"""Battery driver package.

Provides hardware drivers for Huawei and Victron battery systems,
plus the LifecycleDriver and BatteryDriver Protocol classes for structural typing.
"""
from backend.drivers.protocol import BatteryDriver, LifecycleDriver

__all__ = ["LifecycleDriver", "BatteryDriver"]
