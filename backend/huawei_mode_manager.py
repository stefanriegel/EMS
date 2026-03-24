"""Huawei working mode lifecycle manager.

Manages the Huawei inverter's storage working mode transitions:
  - Startup: switch from self-consumption to TOU mode
  - Shutdown: restore self-consumption mode
  - Health check: detect and re-apply TOU mode if reverted
"""
from __future__ import annotations

import asyncio
import enum
import logging

from backend.config import ModeManagerConfig
from backend.drivers.huawei_driver import HuaweiDriver, StorageWorkingModesC

logger = logging.getLogger(__name__)


class ModeState(enum.Enum):
    """Working mode manager states."""

    IDLE = "idle"
    CLAMPING = "clamping"
    SWITCHING = "switching"
    ACTIVE = "active"
    RESTORING = "restoring"
    FAILED = "failed"


class HuaweiModeManager:
    """State machine for Huawei inverter working mode lifecycle."""

    def __init__(self, driver: HuaweiDriver, config: ModeManagerConfig) -> None:
        raise NotImplementedError

    @property
    def state(self) -> ModeState:
        raise NotImplementedError

    @property
    def is_active(self) -> bool:
        raise NotImplementedError

    @property
    def is_transitioning(self) -> bool:
        raise NotImplementedError

    async def activate(self, current_working_mode: int | None = None) -> None:
        raise NotImplementedError

    async def restore(self) -> None:
        raise NotImplementedError

    async def check_health(self, current_working_mode: int | None) -> None:
        raise NotImplementedError
