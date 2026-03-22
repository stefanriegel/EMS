"""Driver protocol classes for structural typing.

Defines two tiers of driver interface:

- **LifecycleDriver**: Shared lifecycle methods (connect, close, async context
  manager) satisfied by both HuaweiDriver and VictronDriver.

- **BatteryDriver**: Extends LifecycleDriver with generic read/write methods.
  VictronDriver satisfies this via ``read_system_state()`` and
  ``write_ac_power_setpoint()``.  HuaweiDriver intentionally does NOT satisfy
  this -- it uses system-specific methods (``read_master``, ``read_battery``,
  ``write_battery_mode``) called directly by the orchestrator.

These are plain ``typing.Protocol`` classes for type-checker-only structural
subtyping.  They are NOT ``@runtime_checkable`` -- ``isinstance()`` checks
are not needed for two known drivers.
"""
from __future__ import annotations

from typing import Protocol


class LifecycleDriver(Protocol):
    """Shared lifecycle methods satisfied by both HuaweiDriver and VictronDriver."""

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> LifecycleDriver: ...
    async def __aexit__(self, *args) -> None: ...


class BatteryDriver(LifecycleDriver, Protocol):
    """Full battery driver protocol with generic read/write.

    VictronDriver satisfies this via read_system_state() and
    write_ac_power_setpoint().  HuaweiDriver intentionally does NOT
    satisfy this -- it uses system-specific methods (read_master,
    read_battery, write_battery_mode) called directly by the orchestrator.
    """

    async def read_state(self) -> object: ...
    async def write_setpoint(self, watts: float) -> None: ...
