"""Protocol conformance tests for Huawei and Victron drivers.

Verifies the two-tier protocol hierarchy:

- **LifecycleDriver**: Both HuaweiDriver and VictronDriver satisfy shared
  lifecycle methods (connect, close, async context manager).
- **BatteryDriver**: VictronDriver additionally satisfies the generic
  read/write interface.  HuaweiDriver intentionally does NOT -- it uses
  system-specific methods called directly by the orchestrator.

Tests use structural checks (``hasattr`` / ``inspect``) on the class itself
to avoid instantiating drivers that require real hardware connections.
"""
from __future__ import annotations

import inspect

import pytest

from backend.drivers.huawei_driver import HuaweiDriver
from backend.drivers.protocol import BatteryDriver, LifecycleDriver
from backend.drivers.victron_driver import VictronDriver


class TestLifecycleDriverConformance:
    """Verify both drivers satisfy the LifecycleDriver protocol structurally."""

    @pytest.mark.parametrize("driver_cls", [HuaweiDriver, VictronDriver])
    def test_has_connect(self, driver_cls):
        assert hasattr(driver_cls, "connect")
        assert inspect.iscoroutinefunction(driver_cls.connect)

    @pytest.mark.parametrize("driver_cls", [HuaweiDriver, VictronDriver])
    def test_has_close(self, driver_cls):
        assert hasattr(driver_cls, "close")
        assert inspect.iscoroutinefunction(driver_cls.close)

    @pytest.mark.parametrize("driver_cls", [HuaweiDriver, VictronDriver])
    def test_has_async_context_manager(self, driver_cls):
        assert hasattr(driver_cls, "__aenter__")
        assert hasattr(driver_cls, "__aexit__")


class TestBatteryDriverConformance:
    """Verify VictronDriver satisfies the full BatteryDriver protocol."""

    def test_victron_has_read_system_state(self):
        assert hasattr(VictronDriver, "read_system_state")
        assert inspect.iscoroutinefunction(VictronDriver.read_system_state)

    def test_victron_has_write_ac_power_setpoint(self):
        assert hasattr(VictronDriver, "write_ac_power_setpoint")
        assert inspect.iscoroutinefunction(
            VictronDriver.write_ac_power_setpoint
        )


class TestHuaweiSystemSpecificMethods:
    """Verify HuaweiDriver has its own system-specific methods (not generic BatteryDriver)."""

    def test_huawei_has_read_methods(self):
        """HuaweiDriver has its own read methods (not generic read_state)."""
        assert hasattr(HuaweiDriver, "read_master")
        assert hasattr(HuaweiDriver, "read_battery")
        assert hasattr(HuaweiDriver, "read_slave")

    def test_huawei_has_write_methods(self):
        """HuaweiDriver has its own write methods."""
        assert hasattr(HuaweiDriver, "write_battery_mode")
        assert hasattr(HuaweiDriver, "write_ac_charging")

    def test_huawei_does_not_have_generic_read_state(self):
        """HuaweiDriver intentionally does NOT have generic read_state."""
        assert not hasattr(HuaweiDriver, "read_state")


def test_victron_driver_no_mqtt_dependency():
    """Verify VictronDriver module does not import paho-mqtt."""
    import backend.drivers.victron_driver as mod

    source = inspect.getsource(mod)
    assert "import paho" not in source
    assert "mqtt.Client" not in source
