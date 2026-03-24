"""Huawei LUNA2000 battery controller.

Wraps ``HuaweiDriver`` with failure counting, stale detection, safe-state
enforcement, and sign-convention translation between the coordinator's
canonical convention (positive=charge, negative=discharge) and Huawei's
driver API.

Safe state: After 3 consecutive poll failures, writes
``write_max_discharge_power(0)`` to prevent uncontrolled discharge.
"""
from __future__ import annotations

import logging
import time

from backend.config import HardwareValidationConfig, SystemConfig
from backend.controller_model import BatteryRole, ControllerCommand, ControllerSnapshot
from backend.drivers.huawei_driver import HuaweiDriver
from backend.drivers.huawei_models import HuaweiBatteryData, HuaweiMasterData

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_FAILURES = 3


class HuaweiController:
    """Per-battery controller for the Huawei LUNA2000 system.

    Parameters
    ----------
    driver:
        Connected ``HuaweiDriver`` instance.
    sys_config:
        System-level SoC limits and feed-in rules.
    loop_interval_s:
        Control loop interval in seconds (for stale detection threshold).
    """

    def __init__(
        self,
        driver: HuaweiDriver,
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
        self._last_battery: HuaweiBatteryData | None = None
        self._last_master: HuaweiMasterData | None = None
        self._last_read_time: float = 0.0

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
        state (zero discharge power) and returns ``available=False``.

        Stale detection: if the last successful read is older than
        ``2 * loop_interval_s``, the failure counter is incremented.
        """
        now = time.monotonic()

        try:
            master = await self._driver.read_master()
            battery = await self._driver.read_battery()
        except Exception as exc:
            logger.warning(
                "Huawei poll failed (%s): %s", type(exc).__name__, exc
            )
            self._consecutive_failures += 1
            return await self._handle_failure(now)

        # Stale detection: if too long since last successful read
        stale_threshold = 2 * self._loop_interval_s
        if (
            self._last_read_time > 0
            and (now - self._last_read_time) > stale_threshold
        ):
            logger.warning(
                "Huawei data stale: %.1fs since last read (threshold %.1fs)",
                now - self._last_read_time,
                stale_threshold,
            )
            self._consecutive_failures += 1
            # Still use the fresh data we just got — but count the gap
        else:
            # Successful fresh read: reset failures
            self._consecutive_failures = 0

        self._last_battery = battery
        self._last_master = master
        self._last_read_time = now

        # Track first successful read for validation period (wall-clock time)
        if self._first_read_at is None:
            self._first_read_at = time.time()
            logger.info(
                "Huawei: first successful read — validation period started (%.0fh)",
                self._validation_config.validation_period_hours
                if self._validation_config
                else 0,
            )

        # Check if we crossed the failure threshold despite stale counting
        if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            return await self._handle_failure(now)

        # Build snapshot from fresh data
        charge_power = battery.charge_power_w  # max(0, total_charge_discharge_power_w)
        headroom = max(0, battery.max_charge_power_w - charge_power)

        return ControllerSnapshot(
            soc_pct=battery.total_soc_pct,
            power_w=float(battery.total_charge_discharge_power_w),
            available=True,
            role=self._role,
            consecutive_failures=self._consecutive_failures,
            timestamp=now,
            max_charge_power_w=battery.max_charge_power_w,
            max_discharge_power_w=battery.max_discharge_power_w,
            charge_headroom_w=float(headroom),
            master_active_power_w=float(master.active_power_w),
        )

    async def _handle_failure(self, now: float) -> ControllerSnapshot:
        """Return a degraded snapshot and enter safe state at threshold."""
        available = self._consecutive_failures < _MAX_CONSECUTIVE_FAILURES

        if not available:
            # Safe state: clamp discharge to zero
            try:
                await self._driver.write_max_discharge_power(0)
                logger.warning(
                    "Huawei safe state: wrote discharge=0 after %d failures",
                    self._consecutive_failures,
                )
            except Exception as exc:
                logger.error(
                    "Huawei safe state write failed: %s", exc
                )

        # Return snapshot with last known or zeroed data
        battery = self._last_battery
        soc = battery.total_soc_pct if battery else 0.0
        power = float(battery.total_charge_discharge_power_w) if battery else 0.0

        return ControllerSnapshot(
            soc_pct=soc,
            power_w=power,
            available=available,
            role=self._role,
            consecutive_failures=self._consecutive_failures,
            timestamp=now,
        )

    async def execute(self, cmd: ControllerCommand) -> None:
        """Translate a coordinator command into Huawei driver calls.

        Sign conventions:
        - Coordinator: positive=charge, negative=discharge
        - Huawei write_max_discharge_power: takes positive watts (discharge limit)
        - Huawei write_ac_charging: enable/disable + write_max_charge_power

        Role mapping:
        - PRIMARY_DISCHARGE / SECONDARY_DISCHARGE: write_max_discharge_power(abs(watts))
        - CHARGING / GRID_CHARGE: write_ac_charging(True) + write_max_charge_power(watts)
        - HOLDING: write_max_discharge_power(0)
        """
        self._role = cmd.role
        dry_run = self._in_validation_period()
        if dry_run:
            remaining = self._remaining_validation_hours()
            logger.info(
                "Huawei: validation period active (%.1fh remaining) — dry_run=True",
                remaining,
            )

        if cmd.role in (BatteryRole.PRIMARY_DISCHARGE, BatteryRole.SECONDARY_DISCHARGE):
            # Discharge: coordinator sends negative watts, Huawei wants positive
            await self._driver.write_max_discharge_power(
                int(abs(cmd.target_watts)), dry_run=dry_run
            )

        elif cmd.role in (BatteryRole.CHARGING, BatteryRole.GRID_CHARGE):
            # Charge: coordinator sends positive watts
            watts = int(cmd.target_watts)
            await self._driver.write_ac_charging(True, dry_run=dry_run)
            await self._driver.write_max_charge_power(watts, dry_run=dry_run)

        elif cmd.role == BatteryRole.HOLDING:
            # Hold: zero discharge
            await self._driver.write_max_discharge_power(0, dry_run=dry_run)

        else:
            logger.warning("Unhandled role %s for Huawei controller", cmd.role)
