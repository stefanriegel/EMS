"""Huawei working mode lifecycle manager.

Manages the Huawei inverter's storage working mode transitions:
  - Startup: switch from self-consumption to TOU mode with power clamping
  - Shutdown: restore self-consumption mode (idempotent)
  - Health check: detect and re-apply TOU mode if reverted by inverter

State machine states::

    IDLE -> CLAMPING -> SWITCHING -> ACTIVE
    ACTIVE -> CLAMPING -> SWITCHING -> ACTIVE  (health check re-apply)
    ACTIVE -> RESTORING -> IDLE                (shutdown)
    Any    -> FAILED                           (unrecoverable error)
"""
from __future__ import annotations

import enum
import logging
import time

import anyio

from backend.config import ModeManagerConfig
from backend.drivers.huawei_driver import HuaweiDriver, StorageWorkingModesC

logger = logging.getLogger(__name__)

_TOU_MODE_VALUE = 5  # StorageWorkingModesC.TIME_OF_USE_LUNA2000


class ModeState(enum.Enum):
    """Working mode manager states."""

    IDLE = "idle"
    CLAMPING = "clamping"
    SWITCHING = "switching"
    ACTIVE = "active"
    RESTORING = "restoring"
    FAILED = "failed"


class HuaweiModeManager:
    """State machine for Huawei inverter working mode lifecycle.

    Parameters
    ----------
    driver:
        The Huawei Modbus TCP driver instance.
    config:
        Mode manager configuration (settle delays, health check timing).
    """

    def __init__(self, driver: HuaweiDriver, config: ModeManagerConfig) -> None:
        self._driver = driver
        self._config = config
        self._state = ModeState.IDLE
        self._last_health_check: float = 0.0
        self._last_reapply: float = 0.0

    @property
    def state(self) -> ModeState:
        """Current mode manager state."""
        return self._state

    @property
    def is_active(self) -> bool:
        """Whether the mode manager is in the ACTIVE state (TOU confirmed)."""
        return self._state == ModeState.ACTIVE

    @property
    def is_transitioning(self) -> bool:
        """Whether the mode manager is in a transitioning state.

        When ``True``, the controller should skip power writes to avoid
        conflicting with the mode transition sequence.
        """
        return self._state in (ModeState.CLAMPING, ModeState.SWITCHING, ModeState.RESTORING)

    async def activate(self, current_working_mode: int | None = None) -> None:
        """Switch the inverter to TOU mode for EMS control.

        If *current_working_mode* is already ``TIME_OF_USE_LUNA2000`` (5),
        skips the transition (crash recovery path) and goes straight to
        ACTIVE.

        Parameters
        ----------
        current_working_mode:
            The current working mode register value from ``read_battery()``.
            Pass this at startup to enable crash recovery detection.
        """
        if current_working_mode == _TOU_MODE_VALUE:
            logger.info(
                "Crash recovery: inverter already in TOU mode (value=%d), "
                "skipping transition",
                current_working_mode,
            )
            self._state = ModeState.ACTIVE
            self._last_health_check = time.monotonic()
            return

        try:
            # Phase 1: Clamp power to zero
            self._state = ModeState.CLAMPING
            logger.info("Mode transition: clamping power to zero")
            await self._driver.write_max_charge_power(0)
            await self._driver.write_max_discharge_power(0)

            # Settle after clamping
            await anyio.sleep(self._config.settle_delay_s)

            # Phase 2: Switch to TOU mode
            self._state = ModeState.SWITCHING
            logger.info("Mode transition: switching to TIME_OF_USE_LUNA2000")
            await self._driver.write_battery_mode(
                StorageWorkingModesC.TIME_OF_USE_LUNA2000
            )

            # Settle after mode switch
            await anyio.sleep(self._config.settle_delay_s)

            # Transition complete
            self._state = ModeState.ACTIVE
            self._last_health_check = time.monotonic()
            logger.info("Mode transition complete: now in ACTIVE state")

        except Exception:
            self._state = ModeState.FAILED
            logger.exception("Mode transition failed")
            raise

    async def restore(self) -> None:
        """Restore the inverter to self-consumption mode on shutdown.

        Idempotent: logs WARNING and swallows exceptions if the driver
        is unresponsive (e.g. connection already closed).
        """
        try:
            self._state = ModeState.RESTORING
            logger.info("Restoring to MAXIMISE_SELF_CONSUMPTION")
            await self._driver.write_battery_mode(
                StorageWorkingModesC.MAXIMISE_SELF_CONSUMPTION
            )
            self._state = ModeState.IDLE
            logger.info("Mode restored to self-consumption")
        except Exception:
            logger.warning(
                "Failed to restore self-consumption mode (driver may be closed)",
                exc_info=True,
            )
            self._state = ModeState.IDLE

    async def check_health(self, current_working_mode: int | None) -> None:
        """Verify the inverter is still in TOU mode and re-apply if reverted.

        Skips if:
          - Not in ACTIVE state
          - Health check interval has not elapsed
          - Re-apply cooldown is active (prevents infinite re-apply loop)

        Parameters
        ----------
        current_working_mode:
            The current working mode register value from ``read_battery()``.
        """
        if self._state != ModeState.ACTIVE:
            return

        now = time.monotonic()

        # Respect health check interval
        if (now - self._last_health_check) < self._config.health_check_interval_s:
            return

        self._last_health_check = now

        # Check if mode matches expected
        if current_working_mode == _TOU_MODE_VALUE:
            return

        # Respect re-apply cooldown
        if (now - self._last_reapply) < self._config.reapply_cooldown_s:
            logger.debug(
                "Mode mismatch detected (mode=%s) but cooldown active, skipping",
                current_working_mode,
            )
            return

        # Re-apply: clamp + switch
        logger.warning(
            "Mode reversion detected: expected %d, got %s. Re-applying TOU mode.",
            _TOU_MODE_VALUE,
            current_working_mode,
        )
        try:
            await self._driver.write_max_charge_power(0)
            await self._driver.write_max_discharge_power(0)
            await anyio.sleep(self._config.settle_delay_s)
            await self._driver.write_battery_mode(
                StorageWorkingModesC.TIME_OF_USE_LUNA2000
            )
            await anyio.sleep(self._config.settle_delay_s)
            self._last_reapply = time.monotonic()
            logger.info("TOU mode re-applied successfully")
        except Exception:
            logger.exception("Failed to re-apply TOU mode")
