"""Victron MultiPlus-II battery controller.

Wraps ``VictronDriver`` with failure counting, stale detection, safe-state
enforcement, ESS mode guard, and per-phase setpoint distribution.

Sign conventions:
- Coordinator canonical: positive=charge, negative=discharge
- Victron write_ac_power_setpoint: positive=import(charge), negative=export(discharge)
  (same convention — no flip needed)

Safe state: After 3 consecutive poll failures, writes 0 to all 3 phases.
ESS mode guard: Skips setpoint writes when ess_mode < 2 (modes 0/1 do not
honour AcPowerSetpoint).
"""
from __future__ import annotations

import logging
import time

from backend.config import HardwareValidationConfig, SystemConfig
from backend.controller_model import BatteryRole, ControllerCommand, ControllerSnapshot
from backend.drivers.victron_driver import VictronDriver
from backend.drivers.victron_models import VictronSystemData

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_FAILURES = 3


class VictronController:
    """Per-battery controller for the Victron MultiPlus-II system.

    Parameters
    ----------
    driver:
        Connected ``VictronDriver`` instance.
    sys_config:
        System-level SoC limits and feed-in rules.
    loop_interval_s:
        Control loop interval in seconds (for stale detection threshold).
    """

    def __init__(
        self,
        driver: VictronDriver,
        sys_config: SystemConfig,
        loop_interval_s: float = 5.0,
        validation_config: HardwareValidationConfig | None = None,
    ) -> None:
        self._driver = driver
        self._sys_config = sys_config
        self._loop_interval_s = loop_interval_s
        self._validation_config = validation_config
        self._first_read_at: float | None = None

        self._role: BatteryRole = BatteryRole.HOLDING
        self._consecutive_failures: int = 0
        self._last_data: VictronSystemData | None = None

    @property
    def role(self) -> BatteryRole:
        """Current role assigned to this controller."""
        return self._role

    def _in_validation_period(self) -> bool:
        """Check if this controller is still in the read-only validation period."""
        if self._validation_config is None:
            return False
        if self._validation_config.dry_run:
            return True  # forced dry-run always active
        if self._first_read_at is None:
            return True  # haven't read successfully yet
        elapsed_hours = (time.time() - self._first_read_at) / 3600.0
        return elapsed_hours < self._validation_config.validation_period_hours

    def _remaining_validation_hours(self) -> float:
        """Return hours remaining in validation period."""
        if self._validation_config is None or self._first_read_at is None:
            return 0.0
        elapsed = (time.time() - self._first_read_at) / 3600.0
        return max(0.0, self._validation_config.validation_period_hours - elapsed)

    async def poll(self) -> ControllerSnapshot:
        """Read driver state and return a typed snapshot.

        On driver exceptions, increments the failure counter. After
        ``_MAX_CONSECUTIVE_FAILURES`` consecutive failures, enters safe
        state (zero setpoint to all 3 phases) and returns ``available=False``.

        Stale detection: if data timestamp is older than
        ``2 * loop_interval_s`` from ``time.monotonic()``, the failure
        counter is incremented.
        """
        now = time.monotonic()

        try:
            data = await self._driver.read_system_state()
        except Exception as exc:
            logger.warning(
                "Victron poll failed (%s): %s", type(exc).__name__, exc
            )
            self._consecutive_failures += 1
            return await self._handle_failure(now)

        # Stale detection: VictronSystemData has a timestamp field
        stale_threshold = 2 * self._loop_interval_s
        if (now - data.timestamp) > stale_threshold:
            logger.warning(
                "Victron data stale: %.1fs old (threshold %.1fs)",
                now - data.timestamp,
                stale_threshold,
            )
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0

        self._last_data = data

        # Track first successful read for validation period (wall-clock time)
        if self._first_read_at is None:
            self._first_read_at = time.time()
            logger.info(
                "Victron: first successful read — validation period started (%.0fh)",
                self._validation_config.validation_period_hours
                if self._validation_config
                else 0,
            )

        if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            return await self._handle_failure(now)

        # Build snapshot from fresh data
        headroom = max(0.0, data.charge_power_w)

        return ControllerSnapshot(
            soc_pct=data.battery_soc_pct,
            power_w=data.battery_power_w,
            available=True,
            role=self._role,
            consecutive_failures=self._consecutive_failures,
            timestamp=now,
            charge_headroom_w=headroom,
            grid_power_w=data.grid_power_w,
            grid_l1_power_w=data.grid_l1_power_w,
            grid_l2_power_w=data.grid_l2_power_w,
            grid_l3_power_w=data.grid_l3_power_w,
            ess_mode=data.ess_mode,
        )

    async def _handle_failure(self, now: float) -> ControllerSnapshot:
        """Return a degraded snapshot and enter safe state at threshold."""
        available = self._consecutive_failures < _MAX_CONSECUTIVE_FAILURES

        if not available:
            # Safe state: zero setpoint to all 3 phases
            try:
                for phase in (1, 2, 3):
                    await self._driver.write_ac_power_setpoint(phase, 0.0)
                logger.warning(
                    "Victron safe state: wrote 0W to all phases after %d failures",
                    self._consecutive_failures,
                )
            except Exception as exc:
                logger.error(
                    "Victron safe state write failed: %s", exc
                )

        data = self._last_data
        soc = data.battery_soc_pct if data else 0.0
        power = data.battery_power_w if data else 0.0

        return ControllerSnapshot(
            soc_pct=soc,
            power_w=power,
            available=available,
            role=self._role,
            consecutive_failures=self._consecutive_failures,
            timestamp=now,
        )

    async def execute(self, cmd: ControllerCommand) -> None:
        """Translate a coordinator command into Victron driver calls.

        ESS mode guard: only writes setpoints when ess_mode >= 2. Modes
        0 and 1 do not honour AcPowerSetpoint writes.

        For discharge with per-phase grid data available, distributes
        setpoints using ``-grid_lN_power_w`` per phase (matching the
        existing orchestrator pattern). Falls back to equal split when
        per-phase data is unavailable.

        For charging and holding, always uses equal split across 3 phases.
        """
        self._role = cmd.role

        # ESS mode guard
        if self._last_data is None or (
            self._last_data.ess_mode is not None
            and self._last_data.ess_mode < 2
        ):
            if self._last_data is not None:
                logger.warning(
                    "Victron ESS mode %s: skipping setpoint write (need >= 2)",
                    self._last_data.ess_mode,
                )
            else:
                logger.warning(
                    "Victron: no data yet, skipping setpoint write"
                )
            return

        dry_run = self._in_validation_period()
        if dry_run:
            remaining = self._remaining_validation_hours()
            logger.info(
                "Victron: validation period active (%.1fh remaining) — dry_run=True",
                remaining,
            )

        if cmd.role in (
            BatteryRole.PRIMARY_DISCHARGE,
            BatteryRole.SECONDARY_DISCHARGE,
        ):
            await self._write_discharge(cmd.target_watts, dry_run=dry_run)

        elif cmd.role in (BatteryRole.CHARGING, BatteryRole.GRID_CHARGE):
            # Positive watts split equally across 3 phases
            per_phase = cmd.target_watts / 3.0
            for phase in (1, 2, 3):
                await self._driver.write_ac_power_setpoint(
                    phase, per_phase, dry_run=dry_run
                )

        elif cmd.role == BatteryRole.HOLDING:
            for phase in (1, 2, 3):
                await self._driver.write_ac_power_setpoint(
                    phase, 0.0, dry_run=dry_run
                )

        else:
            logger.warning(
                "Unhandled role %s for Victron controller", cmd.role
            )

    async def _write_discharge(
        self, target_watts: float, *, dry_run: bool = False
    ) -> None:
        """Write discharge setpoints, using per-phase grid data when available."""
        data = self._last_data
        l1 = data.grid_l1_power_w if data else None
        l2 = data.grid_l2_power_w if data else None
        l3 = data.grid_l3_power_w if data else None

        if l1 is not None and l2 is not None and l3 is not None:
            # Per-phase distribution using grid readings
            await self._driver.write_ac_power_setpoint(1, -l1, dry_run=dry_run)
            await self._driver.write_ac_power_setpoint(2, -l2, dry_run=dry_run)
            await self._driver.write_ac_power_setpoint(3, -l3, dry_run=dry_run)
        else:
            # Equal split fallback (target_watts is already negative for discharge)
            per_phase = target_watts / 3.0
            for phase in (1, 2, 3):
                await self._driver.write_ac_power_setpoint(
                    phase, per_phase, dry_run=dry_run
                )
