"""EMS Orchestrator — unified SoC-balanced control loop (S03).

``Orchestrator`` runs an async control loop that:
  1. Polls both drivers concurrently (Huawei sequential, Victron sync).
  2. Computes SoC-balanced setpoints using available capacity above min SoC.
  3. Applies setpoints with hysteresis dead-band and debounce state machine.
  4. Handles driver failures gracefully — one failure keeps the other running,
     both failures for > ``max_offline_s`` transitions the pool to HOLD.

Sign conventions:
  * Huawei setpoints: positive watts = discharge power limit.
  * Victron setpoints: ``write_ac_power_setpoint(phase, -watts/3)`` —
    negative = export (discharge); each phase gets an equal third.

Logging::

    import logging
    logging.basicConfig(level=logging.DEBUG)

Module logger: ``backend.orchestrator``.

  * INFO  — every control cycle: state, setpoints, reason, SoC values.
  * WARNING — driver failure, stale data, phase imbalance.
  * DEBUG — hysteresis suppression, debounce pending, raw poll output.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from backend.config import OrchestratorConfig, SystemConfig
from backend.drivers.huawei_models import HuaweiBatteryData
from backend.drivers.victron_models import VictronSystemData
from backend.unified_model import ControlState, UnifiedPoolState

if TYPE_CHECKING:
    from backend.drivers.huawei_driver import HuaweiDriver
    from backend.drivers.victron_driver import VictronDriver
    from backend.influx_writer import InfluxMetricsWriter
    from backend.tariff import CompositeTariffEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel values — used when a driver is offline
# ---------------------------------------------------------------------------

def _huawei_sentinel() -> HuaweiBatteryData:
    """Return a zeroed HuaweiBatteryData representing an offline Huawei system."""
    from backend.drivers.huawei_models import HuaweiBatteryData  # local to avoid circulars
    return HuaweiBatteryData(
        pack1_soc_pct=0.0,
        pack1_charge_discharge_power_w=0,
        pack1_status=None,
        pack2_soc_pct=None,
        pack2_charge_discharge_power_w=None,
        pack2_status=None,
        total_soc_pct=0.0,
        total_charge_discharge_power_w=0,
        max_charge_power_w=0,
        max_discharge_power_w=0,
        working_mode=None,
    )


def _victron_sentinel() -> VictronSystemData:
    """Return a zeroed VictronSystemData representing an offline Victron system."""
    from backend.drivers.victron_models import VictronPhaseData, VictronSystemData
    phase = VictronPhaseData(power_w=0.0, current_a=0.0, voltage_v=0.0, setpoint_w=None)
    return VictronSystemData(
        battery_soc_pct=0.0,
        battery_power_w=0.0,
        battery_current_a=0.0,
        battery_voltage_v=0.0,
        l1=phase,
        l2=phase,
        l3=phase,
        ess_mode=None,
        system_state=None,
        vebus_state=None,
        timestamp=0.0,  # always stale
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Unified SoC-balanced control loop for the 94 kWh battery pool.

    Parameters
    ----------
    huawei:
        Connected :class:`~backend.drivers.huawei_driver.HuaweiDriver`.
        The orchestrator calls ``read_master()``, ``read_battery()``, and
        ``write_max_discharge_power()`` on it.  These calls are sequential
        (the driver is not re-entrant).
    victron:
        Connected :class:`~backend.drivers.victron_driver.VictronDriver`.
        The orchestrator calls ``read_system_state()`` (sync) and
        ``write_ac_power_setpoint()`` (sync) on it.
    sys_config:
        Per-system SoC limits and feed-in rules.
    orch_config:
        Timing, hysteresis, debounce, and capacity parameters.
    """

    def __init__(
        self,
        huawei: "HuaweiDriver",
        victron: "VictronDriver",
        sys_config: SystemConfig,
        orch_config: OrchestratorConfig,
        writer: "InfluxMetricsWriter | None" = None,
        tariff_engine: "CompositeTariffEngine | None" = None,
    ) -> None:
        self._huawei = huawei
        self._victron = victron
        self._sys = sys_config
        self._cfg = orch_config
        self._writer = writer
        self._tariff_engine = tariff_engine

        # --- Driver state ---
        self._last_battery: HuaweiBatteryData = _huawei_sentinel()
        self._last_victron: VictronSystemData = _victron_sentinel()
        self._last_master: "HuaweiMasterData | None" = None  # retained across poll failures
        self._huawei_available: bool = False
        self._victron_available: bool = False

        # Track when each driver was last seen online (monotonic)
        self._huawei_last_seen: float = 0.0
        self._victron_last_seen: float = 0.0

        # Last error strings (surfaced by get_last_error())
        self._huawei_error: str | None = None
        self._victron_error: str | None = None

        # --- Setpoint tracking ---
        self._last_huawei_setpoint: int = 0
        self._last_victron_setpoint: float = 0.0

        # --- State machine ---
        self._control_state: ControlState = ControlState.IDLE
        self._pending_state: ControlState = ControlState.IDLE
        self._pending_cycles: int = 0

        # --- Phase imbalance detection ---
        # Counts consecutive cycles where any phase deviates > 500W from setpoint
        self._phase_imbalance_cycles: int = 0

        # --- Published state snapshot ---
        # Initialised to None — get_state() returns None until the first poll
        # cycle completes.  The API layer returns HTTP 503 while this is None.
        self._current_state: UnifiedPoolState | None = None

        # --- Background task ---
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background control loop task."""
        if self._task is not None and not self._task.done():
            logger.warning("Orchestrator already running — ignoring start()")
            return
        self._task = asyncio.create_task(self._run(), name="orchestrator-loop")
        logger.info("Orchestrator control loop started")

    async def stop(self) -> None:
        """Cancel the control loop and apply safe (zero) setpoints.

        Safe setpoints mean: write 0 W discharge limit to Huawei (BMS will
        stop discharging on next cycle), and write 0 W setpoint to all three
        Victron phases.
        """
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.shield(self._task)
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

        await self._apply_safe_setpoints()
        logger.info("Orchestrator stopped; safe setpoints applied")

    def get_state(self) -> UnifiedPoolState | None:
        """Return the most recent unified pool state snapshot, or None if the
        first poll cycle has not yet completed."""
        return self._current_state

    def get_last_error(self) -> str | None:
        """Return the most recent driver error string, or None if no error."""
        if self._huawei_error:
            return self._huawei_error
        return self._victron_error

    def get_device_snapshot(self) -> dict:
        """Return a per-device telemetry snapshot for the ``/api/devices`` endpoint.

        Returns a plain dict with two top-level keys: ``huawei`` and
        ``victron``.  Fields are sourced exclusively from cached poll
        results already stored on ``self`` — no driver I/O is performed.

        Null handling:
        * ``_last_master is None`` → ``master_pv_power_w`` is ``None``.
        * ``_last_battery.pack2_soc_pct is None`` → pack2 fields are ``None``.
        * ``slave_pv_power_w`` is always ``None`` (slave not polled by orchestrator).
        """
        battery = self._last_battery
        victron = self._last_victron
        master = self._last_master

        huawei_dict: dict = {
            "available": self._huawei_available,
            "pack1_soc_pct": battery.pack1_soc_pct,
            "pack1_power_w": battery.pack1_charge_discharge_power_w,
            "pack2_soc_pct": battery.pack2_soc_pct,
            "pack2_power_w": battery.pack2_charge_discharge_power_w,
            "total_soc_pct": battery.total_soc_pct,
            "total_power_w": battery.total_charge_discharge_power_w,
            "max_charge_w": battery.max_charge_power_w,
            "max_discharge_w": battery.max_discharge_power_w,
            "master_pv_power_w": master.pv_input_power_w if master is not None else None,
            "slave_pv_power_w": None,
        }

        victron_dict: dict = {
            "available": self._victron_available,
            "soc_pct": victron.battery_soc_pct,
            "battery_power_w": victron.battery_power_w,
            "l1_power_w": victron.l1.power_w,
            "l2_power_w": victron.l2.power_w,
            "l3_power_w": victron.l3.power_w,
            "l1_voltage_v": victron.l1.voltage_v,
            "l2_voltage_v": victron.l2.voltage_v,
            "l3_voltage_v": victron.l3.voltage_v,
        }

        return {"huawei": huawei_dict, "victron": victron_dict}

    @property
    def sys_config(self) -> SystemConfig:
        """Return the current system configuration (SoC limits, feed-in rules)."""
        return self._sys

    @sys_config.setter
    def sys_config(self, value: SystemConfig) -> None:
        """Update the system configuration at runtime.

        Takes effect on the next control cycle.  Thread-safe for read/write
        of a single Python reference (GIL-protected assignment).
        """
        self._sys = value

    # ------------------------------------------------------------------
    # Control loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Main async control loop — runs until cancelled."""
        while True:
            cycle_start = time.monotonic()
            try:
                await self._poll()
                huawei_w, victron_w = self._compute_setpoints()
                await self._apply_setpoints(huawei_w, victron_w)
                self._current_state = self._build_unified_state(huawei_w, victron_w)

                if self._writer is not None:
                    await self._writer.write_system_state(self._current_state)
                    if self._tariff_engine is not None:
                        from datetime import datetime, timezone
                        now = datetime.now(tz=timezone.utc)
                        from zoneinfo import ZoneInfo
                        oct_tz = ZoneInfo(self._tariff_engine._octopus.timezone)
                        now_oct = now.astimezone(oct_tz)
                        oct_min = now_oct.hour * 60 + now_oct.minute
                        m3_tz = ZoneInfo(self._tariff_engine._modul3.timezone)
                        now_m3 = now.astimezone(m3_tz)
                        m3_min = now_m3.hour * 60 + now_m3.minute
                        oct_rate = self._tariff_engine._octopus_rate_at(oct_min)
                        m3_rate = self._tariff_engine._modul3_rate_at(m3_min)
                        await self._writer.write_tariff(now, oct_rate + m3_rate, oct_rate, m3_rate)

                logger.info(
                    "cycle state=%s huawei_setpoint_w=%d victron_setpoint_w=%.0f "
                    "huawei_soc=%.1f%% victron_soc=%.1f%% "
                    "huawei_avail=%s victron_avail=%s",
                    self._control_state,
                    self._last_huawei_setpoint,
                    self._last_victron_setpoint,
                    self._last_battery.total_soc_pct,
                    self._last_victron.battery_soc_pct,
                    self._huawei_available,
                    self._victron_available,
                )

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Orchestrator cycle exception — retrying in 5 s")
                await asyncio.sleep(5.0)
                continue

            # Sleep the remainder of the interval; skip (don't stack) if over time
            elapsed = time.monotonic() - cycle_start
            sleep_s = max(0.0, self._cfg.loop_interval_s - elapsed)
            if sleep_s == 0.0 and elapsed > self._cfg.loop_interval_s:
                logger.debug(
                    "cycle took %.2f s (> loop_interval %.2f s) — skipping sleep",
                    elapsed,
                    self._cfg.loop_interval_s,
                )
            await asyncio.sleep(sleep_s)

    # ------------------------------------------------------------------
    # Poll
    # ------------------------------------------------------------------

    async def _poll(self) -> None:
        """Read both drivers; update availability flags and cached readings.

        Huawei reads are sequential (driver is not re-entrant):
          read_master() then read_battery().

        Victron read is synchronous (non-blocking — reads in-memory state).
        """
        now = time.monotonic()

        # --- Huawei (sequential, not concurrent) ---
        try:
            # read_master provides AC power context for P_target; we store it
            # on self so _compute_setpoints can access it.
            self._last_master = await self._huawei.read_master()
            self._last_battery = await self._huawei.read_battery()
            self._huawei_available = True
            self._huawei_last_seen = time.monotonic()
            self._huawei_error = None
            logger.debug(
                "Huawei poll ok: total_soc=%.1f%% total_power=%dW",
                self._last_battery.total_soc_pct,
                self._last_battery.total_charge_discharge_power_w,
            )
        except Exception as exc:
            self._huawei_error = str(exc)
            self._huawei_available = False
            logger.warning(
                "Huawei driver failure (%s): %s", type(exc).__name__, exc
            )

        # --- Victron (synchronous) ---
        try:
            victron_state = self._victron.read_system_state()
            # Stale-data guard
            age_s = now - victron_state.timestamp
            if victron_state.timestamp > 0 and age_s > self._cfg.stale_threshold_s:
                logger.warning(
                    "Victron data stale: age=%.1f s (threshold=%.1f s)",
                    age_s,
                    self._cfg.stale_threshold_s,
                )
                self._victron_available = False
                self._victron_error = f"stale data: age={age_s:.1f}s"
            elif victron_state.timestamp == 0.0:
                # Sentinel timestamp — no data received yet
                logger.warning("Victron data stale: no data received yet (timestamp=0)")
                self._victron_available = False
                self._victron_error = "no data received yet"
            else:
                self._last_victron = victron_state
                self._victron_available = True
                self._victron_last_seen = time.monotonic()
                self._victron_error = None
                logger.debug(
                    "Victron poll ok: soc=%.1f%% battery_power=%.0fW",
                    victron_state.battery_soc_pct,
                    victron_state.battery_power_w,
                )
        except Exception as exc:
            self._victron_error = str(exc)
            self._victron_available = False
            logger.warning(
                "Victron driver failure (%s): %s", type(exc).__name__, exc
            )

    # ------------------------------------------------------------------
    # Setpoint computation
    # ------------------------------------------------------------------

    def _compute_setpoints(self) -> tuple[int, float]:
        """Compute SoC-balanced discharge setpoints for both systems.

        Returns
        -------
        tuple[int, float]
            ``(huawei_discharge_w, victron_discharge_w)`` — positive values
            representing the magnitude of discharge power to request.

        Strategy
        ---------
        1. Determine P_target from net house load vs PV generation.
           ``P_target > 0`` → house needs power (discharge);
           ``P_target < 0`` → surplus PV available (charge).
        2. Split P_target proportionally to available capacity above each
           system's minimum SoC.
        3. Cap each setpoint against the hardware limit.
        4. Handle overflow: if one system is full (charging side), route
           surplus to the other.
        5. If both at min SoC: return (0, 0).
        6. If only one driver available: assign full P_target to that system.
        """
        now = time.monotonic()

        # --- Determine both-offline → HOLD ---
        huawei_offline_s = now - self._huawei_last_seen if self._huawei_last_seen else float("inf")
        victron_offline_s = now - self._victron_last_seen if self._victron_last_seen else float("inf")
        both_offline = (
            not self._huawei_available and not self._victron_available
            and huawei_offline_s > self._cfg.max_offline_s
            and victron_offline_s > self._cfg.max_offline_s
        )
        if both_offline:
            self._transition_state(ControlState.HOLD, "both drivers offline > max_offline_s")
            return (0, 0)

        battery = self._last_battery
        victron = self._last_victron

        # --- P_target: net discharge needed ---
        # positive = house needs energy (discharge), negative = surplus (charge)
        if self._last_master is not None:
            # master.active_power_w: positive = exporting to grid; we want
            # net consumption = -(active_power_w) when it's negative (importing)
            # For simplicity: P_target = discharge power requested from pool.
            # Use master active power as grid import indicator.
            # Positive active_power = export (PV surplus); negative = grid import.
            # We cap P_target to the total max discharge of the pool.
            master_power = self._last_master.active_power_w
            # If master is exporting (positive), P_target < 0 (charge signal)
            # If master is importing (negative), P_target > 0 (discharge signal)
            P_target = float(-master_power)
        else:
            # No master data available at all — can't compute P_target
            P_target = 0.0

        # Clamp to physically achievable
        max_discharge = (
            battery.max_discharge_power_w + self._cfg.victron_max_discharge_w
        )
        P_target = max(0.0, min(P_target, max_discharge))

        # --- SoC-balanced split ---
        huawei_cap = max(
            0.0, battery.total_soc_pct - self._sys.huawei_min_soc_pct
        )
        victron_cap = max(
            0.0, victron.battery_soc_pct - self._sys.victron_min_soc_pct
        )
        total_cap = huawei_cap + victron_cap

        if total_cap == 0.0:
            self._transition_state(ControlState.HOLD, "both systems at min SoC")
            return (0, 0)

        if self._huawei_available and self._victron_available:
            huawei_ratio = huawei_cap / total_cap
            victron_ratio = victron_cap / total_cap
        elif self._huawei_available:
            huawei_ratio = 1.0
            victron_ratio = 0.0
        else:  # only victron available
            huawei_ratio = 0.0
            victron_ratio = 1.0

        raw_huawei_w = P_target * huawei_ratio
        raw_victron_w = P_target * victron_ratio

        # Cap against hardware limits
        huawei_w = int(
            min(
                raw_huawei_w,
                battery.max_discharge_power_w if self._huawei_available else 0,
            )
        )
        victron_w = min(
            raw_victron_w,
            self._cfg.victron_max_discharge_w if self._victron_available else 0.0,
        )

        # Ensure non-negative (we only discharge here)
        huawei_w = max(0, huawei_w)
        victron_w = max(0.0, victron_w)

        # --- Overflow routing (R028) ---
        # Huawei charging at capacity → reduce Victron to allow more Huawei absorption
        if (
            battery.charge_power_w >= battery.max_charge_power_w * 0.95
            and battery.max_charge_power_w > 0
        ):
            logger.debug(
                "Huawei charge full (%.0f W / %.0f W) — reducing Victron setpoint",
                battery.charge_power_w,
                battery.max_charge_power_w,
            )
            # Reduce Victron to give Huawei absorption priority (allow Victron to absorb less)
            victron_w = min(
                victron_w,
                max(0.0, self._cfg.victron_max_charge_w * 0.5),
            )

        # Victron charging at capacity → both full → check feed-in
        if (
            victron.charge_power_w >= self._cfg.victron_max_charge_w * 0.95
            and self._cfg.victron_max_charge_w > 0
        ):
            if not self._sys.victron_feed_in_allowed:
                logger.debug(
                    "Victron charge full and feed-in not allowed — holding setpoints"
                )
                # Hold at zero discharge (don't push more in)
                huawei_w = 0
                victron_w = 0.0

        # --- Determine control state ---
        if huawei_w > 0 or victron_w > 0:
            proposed = ControlState.DISCHARGE
        elif P_target < 0:
            proposed = ControlState.CHARGE
        else:
            proposed = ControlState.IDLE

        if not self._huawei_available and not self._victron_available:
            proposed = ControlState.HOLD

        self._transition_state(proposed, f"P_target={P_target:.0f}W")

        return (huawei_w, victron_w)

    # ------------------------------------------------------------------
    # Apply setpoints
    # ------------------------------------------------------------------

    async def _apply_setpoints(self, huawei_w: int, victron_w: float) -> None:
        """Write computed setpoints to both drivers with hysteresis and debounce.

        Hysteresis: if both setpoints are within ``hysteresis_w`` of the last
        applied values, skip the write entirely (prevents micro-oscillation).

        Debounce is handled in _compute_setpoints via _transition_state;
        by the time _apply_setpoints is called, ``self._control_state`` already
        reflects the committed (debounced) state.

        Victron writes use negative watts (negative = export/discharge):
          ``write_ac_power_setpoint(phase, -victron_w/3)`` for phase in (1, 2, 3).
        """
        huawei_delta = abs(huawei_w - self._last_huawei_setpoint)
        victron_delta = abs(victron_w - self._last_victron_setpoint)

        if (
            huawei_delta < self._cfg.hysteresis_w
            and victron_delta < self._cfg.hysteresis_w
        ):
            logger.debug(
                "Hysteresis: suppressing write (Δhuawei=%d W, Δvictron=%.0f W < %d W)",
                huawei_delta,
                victron_delta,
                self._cfg.hysteresis_w,
            )
            return

        # --- Write Huawei ---
        if self._huawei_available:
            try:
                await self._huawei.write_max_discharge_power(huawei_w)
                self._last_huawei_setpoint = huawei_w
                logger.debug("Huawei discharge limit set to %d W", huawei_w)
            except Exception as exc:
                logger.warning(
                    "Huawei write failed (%s): %s", type(exc).__name__, exc
                )

        # --- Write Victron (3-phase equal split, negative = discharge) ---
        if self._victron_available:
            per_phase_w = -(victron_w / 3.0)
            try:
                for phase in (1, 2, 3):
                    self._victron.write_ac_power_setpoint(phase, per_phase_w)
                self._last_victron_setpoint = victron_w
                logger.debug(
                    "Victron per-phase setpoint %.0f W (total %.0f W discharge)",
                    per_phase_w,
                    victron_w,
                )
            except Exception as exc:
                logger.warning(
                    "Victron write failed (%s): %s", type(exc).__name__, exc
                )

        # --- Phase imbalance check ---
        self._check_phase_imbalance(victron_w)

    def _check_phase_imbalance(self, victron_setpoint_w: float) -> None:
        """Detect and log phase imbalance after writing Victron setpoints.

        Checks each measured phase power against the per-phase setpoint.
        If any phase deviates by > 500 W for > 2 consecutive cycles, logs WARNING.
        """
        per_phase_setpoint = victron_setpoint_w / 3.0  # magnitude
        threshold_w = 500.0

        victron = self._last_victron
        imbalance_detected = False

        for phase_name, phase_data in (
            ("L1", victron.l1),
            ("L2", victron.l2),
            ("L3", victron.l3),
        ):
            # Measured power is positive for import; setpoint is for discharge (export)
            # We compare the magnitude of what was measured vs what was commanded
            measured_magnitude = abs(phase_data.power_w)
            deviation = abs(measured_magnitude - per_phase_setpoint)
            if deviation > threshold_w:
                imbalance_detected = True
                logger.debug(
                    "Phase imbalance candidate: %s measured=%.0f W "
                    "setpoint=%.0f W deviation=%.0f W",
                    phase_name,
                    measured_magnitude,
                    per_phase_setpoint,
                    deviation,
                )

        if imbalance_detected:
            self._phase_imbalance_cycles += 1
            if self._phase_imbalance_cycles > 2:
                logger.warning(
                    "Phase imbalance: measured power deviates >500 W from setpoint "
                    "(%.0f W per-phase) for %d consecutive cycles",
                    per_phase_setpoint,
                    self._phase_imbalance_cycles,
                )
        else:
            self._phase_imbalance_cycles = 0

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _transition_state(self, proposed: ControlState, reason: str) -> None:
        """Apply debounce: commit a state transition only after ``debounce_cycles``
        consecutive polls proposing the same new state.

        If the proposed state equals the current state, the pending counter resets.
        """
        if proposed == self._control_state:
            # Already in this state — no transition needed
            self._pending_state = proposed
            self._pending_cycles = 0
            return

        if proposed == self._pending_state:
            self._pending_cycles += 1
        else:
            # New candidate — start counting
            self._pending_state = proposed
            self._pending_cycles = 1

        if self._pending_cycles >= self._cfg.debounce_cycles:
            logger.info(
                "State transition: %s → %s (reason: %s, debounce_cycles=%d)",
                self._control_state,
                proposed,
                reason,
                self._pending_cycles,
            )
            self._control_state = proposed
            self._pending_cycles = 0
        else:
            logger.debug(
                "Debounce pending: %s → %s cycle %d/%d (reason: %s)",
                self._control_state,
                proposed,
                self._pending_cycles,
                self._cfg.debounce_cycles,
                reason,
            )

    # ------------------------------------------------------------------
    # Unified state builder
    # ------------------------------------------------------------------

    def _build_unified_state(
        self, huawei_w: int, victron_w: float
    ) -> UnifiedPoolState:
        """Construct the current ``UnifiedPoolState`` snapshot.

        Called after each poll + apply cycle so ``get_state()`` always returns
        a fresh snapshot reflecting the most recent driver readings and applied
        setpoints.
        """
        battery = self._last_battery
        victron = self._last_victron

        huawei_soc = battery.total_soc_pct
        victron_soc = victron.battery_soc_pct

        combined_soc = (
            huawei_soc * self._cfg.huawei_capacity_kwh
            + victron_soc * self._cfg.victron_capacity_kwh
        ) / (self._cfg.huawei_capacity_kwh + self._cfg.victron_capacity_kwh)

        huawei_headroom = max(0, battery.max_charge_power_w - battery.charge_power_w)
        victron_headroom = max(0.0, victron.charge_power_w)

        combined_power = battery.total_charge_discharge_power_w + victron.battery_power_w

        return UnifiedPoolState(
            combined_soc_pct=combined_soc,
            huawei_soc_pct=huawei_soc,
            victron_soc_pct=victron_soc,
            huawei_available=self._huawei_available,
            victron_available=self._victron_available,
            control_state=self._control_state,
            huawei_discharge_setpoint_w=self._last_huawei_setpoint,
            victron_discharge_setpoint_w=int(self._last_victron_setpoint),
            combined_power_w=combined_power,
            huawei_charge_headroom_w=huawei_headroom,
            victron_charge_headroom_w=victron_headroom,
            timestamp=time.monotonic(),
        )

    # ------------------------------------------------------------------
    # Safe setpoints (used on stop)
    # ------------------------------------------------------------------

    async def _apply_safe_setpoints(self) -> None:
        """Apply zero setpoints to both systems for safe shutdown.

        Writes Huawei max_discharge_power=0 and Victron per-phase=0.
        Errors are suppressed — this is a best-effort shutdown call.
        """
        try:
            if self._huawei_available:
                await self._huawei.write_max_discharge_power(0)
                logger.debug("Safe shutdown: Huawei discharge limit set to 0 W")
        except Exception as exc:
            logger.warning("Safe shutdown: Huawei write failed: %s", exc)

        try:
            if self._victron_available:
                for phase in (1, 2, 3):
                    self._victron.write_ac_power_setpoint(phase, 0.0)
                logger.debug("Safe shutdown: Victron setpoints zeroed")
        except Exception as exc:
            logger.warning("Safe shutdown: Victron write failed: %s", exc)
