"""Dual-battery coordinator — the brain of EMS v2.

Owns the 5s async control loop, computes P_target from grid meter data,
assigns roles based on SoC, allocates watts with hysteresis and ramp
limiting, debounces role transitions, and handles failure routing.

The coordinator NEVER calls driver methods directly — only
``controller.poll()`` and ``controller.execute()`` (CTRL-02).

Sign convention (coordinator canonical):
- Positive watts = charge (battery absorbs energy)
- Negative watts = discharge (battery supplies energy)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.config import MinSocWindow, OrchestratorConfig, SystemConfig
from backend.controller_model import (
    BatteryRole,
    ControllerCommand,
    ControllerSnapshot,
    CoordinatorState,
    PoolStatus,
)

logger = logging.getLogger(__name__)

# Physical capacity constants (kWh)
_HUAWEI_KWH: float = 30.0
_VICTRON_KWH: float = 64.0
_TOTAL_KWH: float = _HUAWEI_KWH + _VICTRON_KWH  # 94.0


class Coordinator:
    """Dual-battery coordinator with independent role assignment.

    Parameters
    ----------
    huawei_ctrl:
        HuaweiController instance (with poll/execute interface).
    victron_ctrl:
        VictronController instance (with poll/execute interface).
    sys_config:
        System-level SoC limits and feed-in rules.
    orch_config:
        Timing, hysteresis, and capacity parameters.
    writer:
        Optional InfluxDB metrics writer.
    tariff_engine:
        Optional tariff engine for rate lookups.
    """

    def __init__(
        self,
        huawei_ctrl,
        victron_ctrl,
        sys_config: SystemConfig,
        orch_config: OrchestratorConfig,
        writer=None,
        tariff_engine=None,
    ) -> None:
        self._huawei_ctrl = huawei_ctrl
        self._victron_ctrl = victron_ctrl
        self._sys_config = sys_config
        self._cfg = orch_config
        self._writer = writer
        self._tariff_engine = tariff_engine

        # Optional integrations (same interface as Orchestrator)
        self._scheduler = None
        self._evcc_monitor = None
        self._notifier = None

        # EVCC battery mode (updated externally via set_evcc_monitor callback)
        self._evcc_battery_mode: str = "normal"

        # Coordinator-specific config (added to OrchestratorConfig pattern)
        self._huawei_deadband_w: int = 300
        self._victron_deadband_w: int = 150
        self._huawei_ramp_w_per_cycle: int = 2000
        self._victron_ramp_w_per_cycle: int = 1000
        self._soc_gap_threshold_pct: float = 5.0
        self._swap_hysteresis_pct: float = 3.0
        self._full_soc_pct: float = 95.0

        # Runtime state
        self._state: CoordinatorState | None = None
        self._task: asyncio.Task | None = None

        # Last commanded watts (for hysteresis and ramp)
        self._last_huawei_cmd_w: float = 0.0
        self._last_victron_cmd_w: float = 0.0

        # Debounce state per controller
        self._committed_roles: dict[str, BatteryRole] = {
            "huawei": BatteryRole.HOLDING,
            "victron": BatteryRole.HOLDING,
        }
        self._pending_roles: dict[str, BatteryRole] = {
            "huawei": BatteryRole.HOLDING,
            "victron": BatteryRole.HOLDING,
        }
        self._pending_cycles: dict[str, int] = {"huawei": 0, "victron": 0}

        # Role assignment tracking (for swap hysteresis)
        self._current_primary: str | None = None  # "huawei" or "victron"

        # Grid charge tracking
        self._grid_charge_was_active: bool = False

        # Last snapshots (for get_device_snapshot / get_last_error)
        self._last_h_snap: ControllerSnapshot | None = None
        self._last_v_snap: ControllerSnapshot | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_state(self) -> CoordinatorState | None:
        """Return the current CoordinatorState or None before first cycle."""
        return self._state

    @property
    def sys_config(self) -> SystemConfig:
        """Current system configuration."""
        return self._sys_config

    @sys_config.setter
    def sys_config(self, value: SystemConfig) -> None:
        """Update system configuration (thread-safe via GIL)."""
        self._sys_config = value

    def set_scheduler(self, scheduler) -> None:
        """Inject the charge scheduler."""
        self._scheduler = scheduler

    def set_evcc_monitor(self, evcc_mqtt) -> None:
        """Inject the EVCC MQTT monitor."""
        self._evcc_monitor = evcc_mqtt

    def set_notifier(self, notifier) -> None:
        """Inject the Telegram notifier."""
        self._notifier = notifier

    def get_last_error(self) -> str | None:
        """Return the most recent controller error, or None.

        Checks both controllers for failure state and returns the first
        non-empty error.  Maintains the same interface as Orchestrator so
        the API layer's /health endpoint works unchanged.
        """
        h_snap = self._last_h_snap
        v_snap = self._last_v_snap
        if h_snap is not None and not h_snap.available:
            return f"Huawei controller unavailable (failures={h_snap.consecutive_failures})"
        if v_snap is not None and not v_snap.available:
            return f"Victron controller unavailable (failures={v_snap.consecutive_failures})"
        return None

    def get_working_mode(self) -> int | None:
        """Return the Huawei working mode, or None if not available.

        The Coordinator does not track working mode directly (the
        HuaweiController/HuaweiDriver handles mode internally).  Returns
        None for backward compatibility with the API /health endpoint.
        """
        return None

    def get_device_snapshot(self) -> dict:
        """Return a per-device telemetry snapshot for the /api/devices endpoint.

        Sources data from the last controller snapshots.  Returns safe
        defaults when controllers have no data yet.
        """
        h_snap = self._last_h_snap
        v_snap = self._last_v_snap

        if h_snap is not None and h_snap.available:
            huawei_dict: dict = {
                "available": True,
                "pack1_soc_pct": h_snap.soc_pct,
                "pack1_power_w": int(h_snap.power_w),
                "pack2_soc_pct": None,
                "pack2_power_w": None,
                "total_soc_pct": h_snap.soc_pct,
                "total_power_w": int(h_snap.power_w),
                "max_charge_w": int(h_snap.max_charge_power_w or 0),
                "max_discharge_w": int(h_snap.max_discharge_power_w or 0),
                "master_pv_power_w": None,
                "slave_pv_power_w": None,
            }
        else:
            huawei_dict = {
                "available": False,
                "pack1_soc_pct": 0.0,
                "pack1_power_w": 0,
                "pack2_soc_pct": None,
                "pack2_power_w": None,
                "total_soc_pct": 0.0,
                "total_power_w": 0,
                "max_charge_w": 0,
                "max_discharge_w": 0,
                "master_pv_power_w": None,
                "slave_pv_power_w": None,
            }

        if v_snap is not None and v_snap.available:
            victron_dict: dict = {
                "available": True,
                "soc_pct": v_snap.soc_pct,
                "battery_power_w": v_snap.power_w,
                "l1_power_w": 0.0,
                "l2_power_w": 0.0,
                "l3_power_w": 0.0,
                "l1_voltage_v": 0.0,
                "l2_voltage_v": 0.0,
                "l3_voltage_v": 0.0,
                "grid_power_w": 0.0,
                "grid_l1_power_w": 0.0,
                "grid_l2_power_w": 0.0,
                "grid_l3_power_w": 0.0,
                "consumption_w": None,
                "pv_on_grid_w": None,
            }
        else:
            victron_dict = {
                "available": False,
                "soc_pct": 0.0,
                "battery_power_w": 0.0,
                "l1_power_w": 0.0,
                "l2_power_w": 0.0,
                "l3_power_w": 0.0,
                "l1_voltage_v": 0.0,
                "l2_voltage_v": 0.0,
                "l3_voltage_v": 0.0,
                "grid_power_w": 0.0,
                "grid_l1_power_w": 0.0,
                "grid_l2_power_w": 0.0,
                "grid_l3_power_w": 0.0,
                "consumption_w": None,
                "pv_on_grid_w": None,
            }

        return {"huawei": huawei_dict, "victron": victron_dict}

    async def start(self) -> None:
        """Start the control loop as an asyncio background task."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("Coordinator started (interval=%.1fs)", self._cfg.loop_interval_s)

    async def stop(self) -> None:
        """Stop the control loop and wait for cleanup."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("Coordinator stopped")

    # ------------------------------------------------------------------
    # Control loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Main control loop — runs at loop_interval_s."""
        while True:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Coordinator cycle error: %s", exc, exc_info=True)
            await asyncio.sleep(self._cfg.loop_interval_s)

    async def _run_cycle(self) -> None:
        """Single control cycle: poll, decide, execute, build state."""
        # 1. Poll both controllers
        h_snap = await self._huawei_ctrl.poll()
        v_snap = await self._victron_ctrl.poll()
        self._last_h_snap = h_snap
        self._last_v_snap = v_snap

        # 2. Check EVCC hold mode
        evcc_hold = self._evcc_battery_mode == "hold"
        if evcc_hold:
            h_cmd = ControllerCommand(
                role=BatteryRole.HOLDING, target_watts=0.0, evcc_hold=True
            )
            v_cmd = ControllerCommand(
                role=BatteryRole.HOLDING, target_watts=0.0, evcc_hold=True
            )
            await self._huawei_ctrl.execute(h_cmd)
            await self._victron_ctrl.execute(v_cmd)
            self._state = self._build_state(h_snap, v_snap, h_cmd, v_cmd)
            return

        # 3. Check grid charge
        slot = self._check_grid_charge()
        if slot is not None:
            h_cmd, v_cmd = self._compute_grid_charge_commands(
                slot, h_snap, v_snap
            )
            await self._huawei_ctrl.execute(h_cmd)
            await self._victron_ctrl.execute(v_cmd)
            self._grid_charge_was_active = True
            self._state = self._build_state(h_snap, v_snap, h_cmd, v_cmd)
            return

        # Grid charge cleanup on slot exit
        if self._grid_charge_was_active:
            h_cmd, v_cmd = self._compute_grid_charge_cleanup()
            await self._huawei_ctrl.execute(h_cmd)
            await self._victron_ctrl.execute(v_cmd)
            self._grid_charge_was_active = False
            self._state = self._build_state(h_snap, v_snap, h_cmd, v_cmd)
            return

        # 4. Compute P_target
        p_target = self._compute_p_target(h_snap, v_snap)

        # 5. PV surplus → charge routing
        if p_target < 0:
            surplus_w = abs(p_target)
            h_charge_w, v_charge_w = self._allocate_charge(
                surplus_w, h_snap, v_snap
            )
            h_role_raw = BatteryRole.CHARGING if h_charge_w > 0 else BatteryRole.HOLDING
            v_role_raw = BatteryRole.CHARGING if v_charge_w > 0 else BatteryRole.HOLDING

            h_role = self._debounce_role("huawei", h_role_raw)
            v_role = self._debounce_role("victron", v_role_raw)

            h_target = h_charge_w if h_role == BatteryRole.CHARGING else 0.0
            v_target = v_charge_w if v_role == BatteryRole.CHARGING else 0.0

            h_cmd = ControllerCommand(role=h_role, target_watts=h_target)
            v_cmd = ControllerCommand(role=v_role, target_watts=v_target)

            self._last_huawei_cmd_w = h_target
            self._last_victron_cmd_w = v_target

            await self._huawei_ctrl.execute(h_cmd)
            await self._victron_ctrl.execute(v_cmd)
            self._state = self._build_state(h_snap, v_snap, h_cmd, v_cmd)
            return

        # 6. Discharge path
        if p_target == 0.0:
            # Idle — both hold
            h_role = self._debounce_role("huawei", BatteryRole.HOLDING)
            v_role = self._debounce_role("victron", BatteryRole.HOLDING)
            h_cmd = ControllerCommand(role=h_role, target_watts=0.0)
            v_cmd = ControllerCommand(role=v_role, target_watts=0.0)
            await self._huawei_ctrl.execute(h_cmd)
            await self._victron_ctrl.execute(v_cmd)
            self._state = self._build_state(h_snap, v_snap, h_cmd, v_cmd)
            return

        # Assign discharge roles
        h_role_raw, v_role_raw = self._assign_discharge_roles(
            h_snap.soc_pct, v_snap.soc_pct
        )

        # Check if both are below min SoC → HOLDING (profile-aware)
        _tz = ZoneInfo(os.environ.get("MODUL3_TIMEZONE", "Europe/Berlin"))
        now_local = datetime.now(tz=_tz)
        h_min_soc = self._get_effective_min_soc("huawei", now_local)
        v_min_soc = self._get_effective_min_soc("victron", now_local)
        h_below_min = h_snap.soc_pct <= h_min_soc
        v_below_min = v_snap.soc_pct <= v_min_soc
        if h_below_min and v_below_min:
            h_role_raw = BatteryRole.HOLDING
            v_role_raw = BatteryRole.HOLDING

        # Safe-state: if a controller is offline → HOLDING immediately
        h_safe = not h_snap.available
        v_safe = not v_snap.available
        if h_safe:
            h_role_raw = BatteryRole.HOLDING
        if v_safe:
            v_role_raw = BatteryRole.HOLDING

        # Debounce roles (safe-state bypasses debounce)
        h_role = self._debounce_role("huawei", h_role_raw, safe_state=h_safe)
        v_role = self._debounce_role("victron", v_role_raw, safe_state=v_safe)

        # Allocate watts
        h_w, v_w = self._allocate(p_target, h_role, v_role, h_snap, v_snap)

        # Apply hysteresis
        h_w = self._apply_hysteresis(h_w, "huawei")
        v_w = self._apply_hysteresis(v_w, "victron")

        # Apply ramp limiting
        h_w = self._apply_ramp(h_w, "huawei")
        v_w = self._apply_ramp(v_w, "victron")

        # Update last commands
        self._last_huawei_cmd_w = h_w
        self._last_victron_cmd_w = v_w

        # Build and send commands
        h_cmd = ControllerCommand(role=h_role, target_watts=h_w)
        v_cmd = ControllerCommand(role=v_role, target_watts=v_w)

        await self._huawei_ctrl.execute(h_cmd)
        await self._victron_ctrl.execute(v_cmd)
        self._state = self._build_state(h_snap, v_snap, h_cmd, v_cmd)

    # ------------------------------------------------------------------
    # P_target computation
    # ------------------------------------------------------------------

    def _compute_p_target(
        self, h_snap: ControllerSnapshot, v_snap: ControllerSnapshot
    ) -> float:
        """Compute net power target from grid measurements.

        Positive = house importing from grid (need to discharge).
        Negative = surplus (PV exceeds load, can charge).

        Primary source: Victron grid_power_w (Venus OS grid meter).
        Fallback: Huawei master_active_power_w (sign-flipped).
        """
        if v_snap.grid_power_w is not None and v_snap.available:
            logger.debug("P_target source: grid_meter (%.0f W)", v_snap.grid_power_w)
            return float(v_snap.grid_power_w)

        if h_snap.master_active_power_w is not None:
            # Huawei active_power: positive=export → negate for P_target
            p = -float(h_snap.master_active_power_w)
            logger.debug("P_target source: huawei_master (%.0f W)", p)
            return p

        logger.debug("P_target source: none available — holding at 0")
        return 0.0

    # ------------------------------------------------------------------
    # Role assignment (D-01, D-02, CTRL-06, CTRL-08)
    # ------------------------------------------------------------------

    def _assign_discharge_roles(
        self, h_soc: float, v_soc: float
    ) -> tuple[BatteryRole, BatteryRole]:
        """Assign PRIMARY_DISCHARGE and SECONDARY/HOLDING based on SoC.

        Rules:
        - Higher SoC system gets PRIMARY_DISCHARGE
        - Gap >= soc_gap_threshold (5%): other gets HOLDING
        - Gap < soc_gap_threshold (5%): other gets SECONDARY_DISCHARGE
        - Swap hysteresis: current PRIMARY keeps role unless challenger
          exceeds it by swap_hysteresis_pct (3%)
        - Both below min SoC: caller handles → both HOLDING
        """
        gap = abs(h_soc - v_soc)

        # Determine naive winner (higher SoC)
        if h_soc >= v_soc:
            naive_primary = "huawei"
        else:
            naive_primary = "victron"

        # Apply swap hysteresis: current primary keeps role unless
        # challenger exceeds by swap_hysteresis_pct
        if self._current_primary is not None:
            if self._current_primary == "huawei":
                # Victron needs to exceed Huawei by 3% to take over
                if v_soc > h_soc + self._swap_hysteresis_pct:
                    primary = "victron"
                else:
                    primary = "huawei"
            else:
                # Huawei needs to exceed Victron by 3% to take over
                if h_soc > v_soc + self._swap_hysteresis_pct:
                    primary = "huawei"
                else:
                    primary = "victron"
        else:
            primary = naive_primary

        self._current_primary = primary

        # Assign roles
        if primary == "huawei":
            h_role = BatteryRole.PRIMARY_DISCHARGE
            if gap < self._soc_gap_threshold_pct:
                v_role = BatteryRole.SECONDARY_DISCHARGE
            else:
                v_role = BatteryRole.HOLDING
        else:
            v_role = BatteryRole.PRIMARY_DISCHARGE
            if gap < self._soc_gap_threshold_pct:
                h_role = BatteryRole.SECONDARY_DISCHARGE
            else:
                h_role = BatteryRole.HOLDING

        return h_role, v_role

    # ------------------------------------------------------------------
    # Allocation (CTRL-02, CTRL-05)
    # ------------------------------------------------------------------

    def _allocate(
        self,
        p_target: float,
        h_role: BatteryRole,
        v_role: BatteryRole,
        h_snap: ControllerSnapshot,
        v_snap: ControllerSnapshot,
    ) -> tuple[float, float]:
        """Allocate discharge watts to controllers based on roles.

        Returns (h_watts, v_watts) in coordinator convention
        (negative = discharge).

        Failover (D-10): when one system is unavailable, the survivor
        gets the full P_target.
        """
        h_available = h_snap.available
        v_available = v_snap.available

        # Neither available → zero
        if not h_available and not v_available:
            return 0.0, 0.0

        # Only one available → full to survivor (D-10)
        if not h_available:
            return 0.0, -p_target
        if not v_available:
            return -p_target, 0.0

        # Both available — allocate by role
        is_h_primary = h_role == BatteryRole.PRIMARY_DISCHARGE
        is_v_primary = v_role == BatteryRole.PRIMARY_DISCHARGE
        is_h_secondary = h_role == BatteryRole.SECONDARY_DISCHARGE
        is_v_secondary = v_role == BatteryRole.SECONDARY_DISCHARGE

        if is_h_primary and not is_v_secondary:
            # PRIMARY only → full to Huawei
            return -p_target, 0.0
        if is_v_primary and not is_h_secondary:
            # PRIMARY only → full to Victron
            return 0.0, -p_target

        # Both discharging (PRIMARY + SECONDARY): split by capacity ratio
        h_cap = self._cfg.huawei_capacity_kwh
        v_cap = self._cfg.victron_capacity_kwh
        total_cap = h_cap + v_cap
        if total_cap == 0:
            return 0.0, 0.0

        h_ratio = h_cap / total_cap
        v_ratio = v_cap / total_cap
        return -p_target * h_ratio, -p_target * v_ratio

    # ------------------------------------------------------------------
    # PV surplus routing (D-03, D-04)
    # ------------------------------------------------------------------

    def _allocate_charge(
        self,
        surplus_w: float,
        h_snap: ControllerSnapshot,
        v_snap: ControllerSnapshot,
    ) -> tuple[float, float]:
        """Allocate PV surplus weighted by SoC headroom (OPT-01, per D-02).

        headroom = full_soc_pct - current_soc. Battery with more headroom
        gets proportionally more. Charge rate limits respected; overflow
        routes to the other battery (D-03). Battery at full_soc_pct gets
        zero (D-04).

        Returns (h_charge_w, v_charge_w) — both positive (charge).
        """
        h_headroom_soc = max(0.0, self._full_soc_pct - h_snap.soc_pct)
        v_headroom_soc = max(0.0, self._full_soc_pct - v_snap.soc_pct)
        total_headroom = h_headroom_soc + v_headroom_soc

        if total_headroom <= 0.0:
            return 0.0, 0.0

        # Proportional split by SoC headroom
        h_share = surplus_w * (h_headroom_soc / total_headroom)
        v_share = surplus_w * (v_headroom_soc / total_headroom)

        # Clamp to charge rate limits
        h_max = h_snap.charge_headroom_w
        v_max = v_snap.charge_headroom_w

        h_charge = min(h_share, h_max)
        v_charge = min(v_share, v_max)

        # Overflow routing (D-03)
        h_overflow = max(0.0, h_share - h_max)
        v_overflow = max(0.0, v_share - v_max)

        h_charge += min(v_overflow, max(0.0, h_max - h_charge))
        v_charge += min(h_overflow, max(0.0, v_max - v_charge))

        return h_charge, v_charge

    # ------------------------------------------------------------------
    # Min-SoC profiles (OPT-05, D-13, D-15, D-16)
    # ------------------------------------------------------------------

    def _get_effective_min_soc(
        self, system: str, now_local: datetime
    ) -> float:
        """Return effective min-SoC for the given system at the given local time.

        Evaluates profiles from SystemConfig; first matching window wins.
        Falls back to static min_soc if no profiles configured (D-15).
        """
        if system == "huawei":
            profiles = self._sys_config.huawei_min_soc_profile
            static = self._sys_config.huawei_min_soc_pct
        else:
            profiles = self._sys_config.victron_min_soc_profile
            static = self._sys_config.victron_min_soc_pct

        if not profiles:
            return static

        current_hour = now_local.hour
        for window in profiles:
            if window.start_hour <= window.end_hour:
                if window.start_hour <= current_hour < window.end_hour:
                    return window.min_soc_pct
            else:
                # Wrapping window (e.g., 22 to 6)
                if current_hour >= window.start_hour or current_hour < window.end_hour:
                    return window.min_soc_pct

        return static

    # ------------------------------------------------------------------
    # Hysteresis (CTRL-03, D-06)
    # ------------------------------------------------------------------

    def _apply_hysteresis(self, target_w: float, system: str) -> float:
        """Apply dead-band hysteresis — suppress small changes.

        Huawei dead-band: 300W (default).
        Victron dead-band: 150W (default).

        Returns the target unchanged if delta exceeds dead-band,
        or the previous command value if suppressed.
        """
        if system == "huawei":
            deadband = self._huawei_deadband_w
            last = self._last_huawei_cmd_w
        else:
            deadband = self._victron_deadband_w
            last = self._last_victron_cmd_w

        delta = abs(target_w - last)
        if delta < deadband:
            logger.debug(
                "Hysteresis %s: suppressed (delta=%.0f W < deadband=%d W)",
                system, delta, deadband,
            )
            return last
        return target_w

    # ------------------------------------------------------------------
    # Ramp limiting (CTRL-07)
    # ------------------------------------------------------------------

    def _apply_ramp(self, target_w: float, system: str) -> float:
        """Limit setpoint change per cycle to max ramp rate.

        Huawei: 2000 W/cycle (default).
        Victron: 1000 W/cycle (default).
        """
        if system == "huawei":
            max_ramp = self._huawei_ramp_w_per_cycle
            last = self._last_huawei_cmd_w
        else:
            max_ramp = self._victron_ramp_w_per_cycle
            last = self._last_victron_cmd_w

        delta = target_w - last
        if abs(delta) > max_ramp:
            # Clamp to max ramp in the direction of change
            clamped = last + max_ramp * (1 if delta > 0 else -1)
            logger.debug(
                "Ramp %s: limited %.0f → %.0f (max %d W/cycle)",
                system, target_w, clamped, max_ramp,
            )
            return clamped
        return target_w

    # ------------------------------------------------------------------
    # Debounce (D-16)
    # ------------------------------------------------------------------

    def _debounce_role(
        self,
        system: str,
        proposed: BatteryRole,
        safe_state: bool = False,
    ) -> BatteryRole:
        """Debounce role transitions — require 2 consecutive cycles.

        Safe-state transitions (HOLDING due to comms loss) bypass
        debounce and take effect immediately.
        """
        current = self._committed_roles[system]

        # Safe state bypasses debounce
        if safe_state:
            self._committed_roles[system] = proposed
            self._pending_cycles[system] = 0
            self._pending_roles[system] = proposed
            return proposed

        # Already in this role → no transition
        if proposed == current:
            self._pending_roles[system] = proposed
            self._pending_cycles[system] = 0
            return current

        # Same proposal as pending?
        if proposed == self._pending_roles[system]:
            self._pending_cycles[system] += 1
        else:
            self._pending_roles[system] = proposed
            self._pending_cycles[system] = 1

        if self._pending_cycles[system] >= self._cfg.debounce_cycles:
            logger.info(
                "Debounce %s: %s -> %s committed (cycles=%d)",
                system, current, proposed, self._pending_cycles[system],
            )
            self._committed_roles[system] = proposed
            self._pending_cycles[system] = 0
            return proposed

        logger.debug(
            "Debounce %s: %s -> %s pending (%d/%d)",
            system, current, proposed,
            self._pending_cycles[system], self._cfg.debounce_cycles,
        )
        return current

    # ------------------------------------------------------------------
    # Grid charge (D-08)
    # ------------------------------------------------------------------

    def _check_grid_charge(self):
        """Check for an active charge slot from the scheduler.

        Returns the active ChargeSlot or None.
        """
        from datetime import datetime, timezone

        if self._scheduler is None:
            return None
        schedule = self._scheduler.active_schedule
        if schedule is None or schedule.stale:
            return None
        now_utc = datetime.now(tz=timezone.utc)
        for slot in schedule.slots:
            if slot.start_utc <= now_utc < slot.end_utc:
                return slot
        return None

    def _compute_grid_charge_commands(
        self, slot, h_snap: ControllerSnapshot, v_snap: ControllerSnapshot
    ) -> tuple[ControllerCommand, ControllerCommand]:
        """Build GRID_CHARGE commands for both controllers."""
        if slot.battery == "huawei":
            h_target_met = h_snap.soc_pct >= slot.target_soc_pct
            h_watts = 0 if h_target_met else slot.grid_charge_power_w
            v_watts = slot.grid_charge_power_w if h_target_met else 0
            h_cmd = ControllerCommand(
                role=BatteryRole.GRID_CHARGE, target_watts=h_watts
            )
            v_cmd = ControllerCommand(
                role=BatteryRole.GRID_CHARGE if v_watts > 0 else BatteryRole.HOLDING,
                target_watts=v_watts,
            )
        else:
            v_watts = slot.grid_charge_power_w
            h_cmd = ControllerCommand(
                role=BatteryRole.HOLDING, target_watts=0
            )
            v_cmd = ControllerCommand(
                role=BatteryRole.GRID_CHARGE, target_watts=v_watts
            )
        return h_cmd, v_cmd

    def _compute_grid_charge_cleanup(
        self,
    ) -> tuple[ControllerCommand, ControllerCommand]:
        """Build cleanup commands when exiting a grid charge slot."""
        h_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0)
        v_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0)
        return h_cmd, v_cmd

    # ------------------------------------------------------------------
    # State building
    # ------------------------------------------------------------------

    def _build_state(
        self,
        h_snap: ControllerSnapshot,
        v_snap: ControllerSnapshot,
        h_cmd: ControllerCommand,
        v_cmd: ControllerCommand,
    ) -> CoordinatorState:
        """Construct CoordinatorState from snapshots and commands.

        Backward-compatible with UnifiedPoolState fields.
        """
        h_soc = h_snap.soc_pct
        v_soc = v_snap.soc_pct
        combined_soc = (h_soc * _HUAWEI_KWH + v_soc * _VICTRON_KWH) / _TOTAL_KWH

        combined_power = h_snap.power_w + v_snap.power_w

        # Determine control state string (backward-compat)
        if h_cmd.role == BatteryRole.GRID_CHARGE or v_cmd.role == BatteryRole.GRID_CHARGE:
            control_state = "GRID_CHARGE"
        elif h_cmd.evcc_hold:
            control_state = "DISCHARGE_LOCKED"
        elif h_cmd.role in (BatteryRole.PRIMARY_DISCHARGE, BatteryRole.SECONDARY_DISCHARGE):
            control_state = "DISCHARGE"
        elif v_cmd.role in (BatteryRole.PRIMARY_DISCHARGE, BatteryRole.SECONDARY_DISCHARGE):
            control_state = "DISCHARGE"
        elif h_cmd.role == BatteryRole.CHARGING or v_cmd.role == BatteryRole.CHARGING:
            control_state = "CHARGE"
        else:
            control_state = "IDLE"

        # Pool status
        if h_snap.available and v_snap.available:
            pool_status = "NORMAL"
        elif h_snap.available or v_snap.available:
            pool_status = "DEGRADED"
        else:
            pool_status = "OFFLINE"

        # Setpoints: convert to discharge magnitude (positive = discharging)
        h_setpoint = int(abs(h_cmd.target_watts)) if h_cmd.target_watts < 0 else 0
        v_setpoint = int(abs(v_cmd.target_watts)) if v_cmd.target_watts < 0 else 0

        # Effective min-SoC from profiles
        _tz = ZoneInfo(os.environ.get("MODUL3_TIMEZONE", "Europe/Berlin"))
        now_local = datetime.now(tz=_tz)
        h_eff_min = self._get_effective_min_soc("huawei", now_local)
        v_eff_min = self._get_effective_min_soc("victron", now_local)

        return CoordinatorState(
            combined_soc_pct=combined_soc,
            huawei_soc_pct=h_soc,
            victron_soc_pct=v_soc,
            huawei_available=h_snap.available,
            victron_available=v_snap.available,
            control_state=control_state,
            huawei_discharge_setpoint_w=h_setpoint,
            victron_discharge_setpoint_w=v_setpoint,
            combined_power_w=combined_power,
            huawei_charge_headroom_w=int(h_snap.charge_headroom_w),
            victron_charge_headroom_w=v_snap.charge_headroom_w,
            timestamp=time.monotonic(),
            grid_charge_slot_active=(control_state == "GRID_CHARGE"),
            evcc_battery_mode=self._evcc_battery_mode,
            huawei_role=h_cmd.role.value,
            victron_role=v_cmd.role.value,
            pool_status=pool_status,
            huawei_effective_min_soc_pct=h_eff_min,
            victron_effective_min_soc_pct=v_eff_min,
        )
