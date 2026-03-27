"""Huawei working mode lifecycle manager.

Manages the Huawei inverter's storage working mode transitions:
  - Startup: switch to THIRD_PARTY_DISPATCH (mode 6) via EMMA reg 40000,
    OR fall back to TOU mode 5 via the inverter if no EMMA driver is set.
  - Shutdown: restore self-consumption mode (idempotent)
  - Health check: when EMMA is managing mode 6, skip inverter-level reversion
    detection (EMMA firmware owns mode 6 — the inverter register may show a
    different value and that is expected).

State machine states::

    IDLE -> SWITCHING -> ACTIVE
    ACTIVE -> RESTORING -> IDLE        (shutdown)
    IDLE -> CLAMPING -> SWITCHING -> ACTIVE  (TOU fallback path, no EMMA)
    ACTIVE -> CLAMPING -> SWITCHING -> ACTIVE  (TOU health check re-apply)
    Any    -> FAILED                   (unrecoverable error)
"""
from __future__ import annotations

import enum
import logging
import time
from typing import TYPE_CHECKING

import anyio

from backend.config import ModeManagerConfig
from backend.drivers.huawei_driver import HuaweiDriver, StorageWorkingModesC

if TYPE_CHECKING:
    from backend.drivers.emma_driver import EmmaDriver

logger = logging.getLogger(__name__)

_TOU_MODE_VALUE = 5    # StorageWorkingModesC.TIME_OF_USE_LUNA2000 (fallback)
_EMMA_MODE = 6         # THIRD_PARTY_DISPATCH via EMMA reg 40000
_SELF_CONS_MODE = 2    # MAXIMISE_SELF_CONSUMPTION


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
        self._emma_driver: "EmmaDriver | None" = None
        self._state = ModeState.IDLE
        self._last_health_check: float = 0.0
        self._last_reapply: float = 0.0

    def set_emma_driver(self, driver: "EmmaDriver | None") -> None:
        """Attach the EMMA driver for mode 6 (THIRD_PARTY_DISPATCH) control.

        When set, ``activate()`` writes mode 6 via EMMA register 40000 instead
        of TOU mode 5 via the inverter register.  ``check_health()`` skips
        inverter-level reversion detection because EMMA firmware owns mode 6.
        """
        self._emma_driver = driver

    @property
    def state(self) -> ModeState:
        """Current mode manager state."""
        return self._state

    @property
    def is_active(self) -> bool:
        """Whether the mode manager is in the ACTIVE state."""
        return self._state == ModeState.ACTIVE

    @property
    def is_transitioning(self) -> bool:
        """Whether the mode manager is in a transitioning state.

        When ``True``, the controller should skip power writes to avoid
        conflicting with the mode transition sequence.
        """
        return self._state in (ModeState.CLAMPING, ModeState.SWITCHING, ModeState.RESTORING)

    async def activate(self, current_working_mode: int | None = None) -> None:
        """Switch to the appropriate control mode for EMS dispatch.

        If an EMMA driver is set, writes THIRD_PARTY_DISPATCH (mode 6) via
        EMMA register 40000 — no inverter clamping needed.

        If no EMMA driver is set, falls back to TOU mode 5 via the inverter
        register with the original clamp-then-switch sequence.

        Parameters
        ----------
        current_working_mode:
            The current working mode register value from ``read_battery()``.
            Used only by the TOU fallback path for crash recovery detection.
        """
        if self._emma_driver is not None:
            await self._activate_via_emma()
        else:
            await self._activate_via_tou(current_working_mode)

    async def _activate_via_emma(self) -> None:
        """Set THIRD_PARTY_DISPATCH (mode 6) via EMMA register 40000."""
        assert self._emma_driver is not None
        try:
            self._state = ModeState.SWITCHING
            logger.info(
                "Mode transition: setting THIRD_PARTY_DISPATCH (mode 6) via EMMA reg 40000"
            )
            await self._emma_driver.write_ess_mode(_EMMA_MODE)
            await anyio.sleep(self._config.settle_delay_s)
            self._state = ModeState.ACTIVE
            self._last_health_check = time.monotonic()
            logger.info("Mode transition complete: THIRD_PARTY_DISPATCH active via EMMA")
        except Exception:
            self._state = ModeState.FAILED
            logger.exception("EMMA mode 6 activation failed")
            raise

    async def _activate_via_tou(self, current_working_mode: int | None) -> None:
        """Set TOU mode 5 via the inverter register (fallback, no EMMA)."""
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
        """Restore a safe operating mode on shutdown.

        When EMMA driver is set: writes self-consumption mode 2 via EMMA reg 40000.
        Fallback: writes MAXIMISE_SELF_CONSUMPTION via the inverter register.

        Idempotent: logs WARNING and swallows exceptions if the driver
        is unresponsive (e.g. connection already closed).
        """
        try:
            self._state = ModeState.RESTORING
            if self._emma_driver is not None:
                logger.info(
                    "Restoring via EMMA: setting mode %d (max self-consumption)", _SELF_CONS_MODE
                )
                await self._emma_driver.write_ess_mode(_SELF_CONS_MODE)
            else:
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
        """Verify the active control mode and re-apply if reverted.

        When an EMMA driver is set (mode 6 path): reversion detection is
        skipped — EMMA firmware owns mode 6 and the Huawei inverter register
        value is expected to differ.

        When no EMMA driver is set (TOU fallback): checks inverter register
        and re-applies TOU mode 5 if reverted.

        Skips if:
          - Not in ACTIVE state
          - Health check interval has not elapsed
          - (TOU path only) Re-apply cooldown is active

        Parameters
        ----------
        current_working_mode:
            The current working mode register value from ``read_battery()``.
            Relevant only for the TOU fallback path.
        """
        if self._state != ModeState.ACTIVE:
            return

        now = time.monotonic()

        # Respect health check interval
        if (now - self._last_health_check) < self._config.health_check_interval_s:
            return

        self._last_health_check = now

        # EMMA path: mode 6 is managed by EMMA firmware — no inverter reversion check
        if self._emma_driver is not None:
            return

        # TOU fallback path: check inverter register and re-apply if reverted
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
