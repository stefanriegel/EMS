"""Unit tests for VictronConfig Modbus TCP configuration and driver Protocol classes.

Tests cover:
  - VictronConfig defaults (port=502, vebus_unit_id=227, system_unit_id=100)
  - VictronConfig.from_env() reads custom unit IDs from environment
  - VictronConfig.from_env() raises KeyError when VICTRON_HOST is missing
  - VictronConfig no longer has discovery_timeout_s field
  - LifecycleDriver and BatteryDriver Protocol classes exist with correct methods
"""
from __future__ import annotations

import pytest

from backend.config import VictronConfig


# ---------------------------------------------------------------------------
# VictronConfig — Modbus TCP defaults
# ---------------------------------------------------------------------------

class TestVictronConfigDefaults:
    def test_default_port_is_502(self):
        """Default port is 502 (Modbus TCP), not 1883 (MQTT)."""
        cfg = VictronConfig(host="192.168.0.10")
        assert cfg.port == 502

    def test_default_vebus_unit_id(self):
        """Default vebus_unit_id is 227."""
        cfg = VictronConfig(host="192.168.0.10")
        assert cfg.vebus_unit_id == 227

    def test_default_system_unit_id(self):
        """Default system_unit_id is 100."""
        cfg = VictronConfig(host="192.168.0.10")
        assert cfg.system_unit_id == 100

    def test_default_timeout(self):
        """Default timeout_s is 5.0 for Modbus TCP (local network)."""
        cfg = VictronConfig(host="192.168.0.10")
        assert cfg.timeout_s == pytest.approx(5.0)

    def test_no_discovery_timeout_field(self):
        """discovery_timeout_s must not exist (MQTT-only concept removed)."""
        cfg = VictronConfig(host="192.168.0.10")
        assert not hasattr(cfg, "discovery_timeout_s")


# ---------------------------------------------------------------------------
# VictronConfig.from_env() — environment variable reading
# ---------------------------------------------------------------------------

class TestVictronConfigFromEnv:
    def test_from_env_host_and_default_port(self, monkeypatch):
        """VICTRON_HOST is picked up; port defaults to 502."""
        monkeypatch.setenv("VICTRON_HOST", "192.168.0.10")
        monkeypatch.delenv("VICTRON_PORT", raising=False)
        monkeypatch.delenv("VICTRON_VEBUS_UNIT_ID", raising=False)
        monkeypatch.delenv("VICTRON_SYSTEM_UNIT_ID", raising=False)

        cfg = VictronConfig.from_env()

        assert cfg.host == "192.168.0.10"
        assert cfg.port == 502

    def test_from_env_custom_port(self, monkeypatch):
        """VICTRON_PORT overrides the default port."""
        monkeypatch.setenv("VICTRON_HOST", "10.0.0.5")
        monkeypatch.setenv("VICTRON_PORT", "8502")

        cfg = VictronConfig.from_env()

        assert cfg.port == 8502

    def test_from_env_custom_vebus_unit_id(self, monkeypatch):
        """VICTRON_VEBUS_UNIT_ID overrides the default."""
        monkeypatch.setenv("VICTRON_HOST", "10.0.0.5")
        monkeypatch.setenv("VICTRON_VEBUS_UNIT_ID", "228")

        cfg = VictronConfig.from_env()

        assert cfg.vebus_unit_id == 228

    def test_from_env_custom_system_unit_id(self, monkeypatch):
        """VICTRON_SYSTEM_UNIT_ID overrides the default."""
        monkeypatch.setenv("VICTRON_HOST", "10.0.0.5")
        monkeypatch.setenv("VICTRON_SYSTEM_UNIT_ID", "101")

        cfg = VictronConfig.from_env()

        assert cfg.system_unit_id == 101

    def test_from_env_missing_host_raises(self, monkeypatch):
        """Missing VICTRON_HOST must raise KeyError."""
        monkeypatch.delenv("VICTRON_HOST", raising=False)

        with pytest.raises(KeyError):
            VictronConfig.from_env()

    def test_from_env_empty_host_raises(self, monkeypatch):
        """Empty VICTRON_HOST must raise KeyError."""
        monkeypatch.setenv("VICTRON_HOST", "")

        with pytest.raises(KeyError):
            VictronConfig.from_env()

    def test_from_env_defaults_unit_ids(self, monkeypatch):
        """Without VICTRON_VEBUS_UNIT_ID/SYSTEM_UNIT_ID, defaults apply."""
        monkeypatch.setenv("VICTRON_HOST", "192.168.0.10")
        monkeypatch.delenv("VICTRON_VEBUS_UNIT_ID", raising=False)
        monkeypatch.delenv("VICTRON_SYSTEM_UNIT_ID", raising=False)

        cfg = VictronConfig.from_env()

        assert cfg.vebus_unit_id == 227
        assert cfg.system_unit_id == 100


# ---------------------------------------------------------------------------
# Protocol classes — structural existence checks
# ---------------------------------------------------------------------------

class TestProtocolClasses:
    def test_lifecycle_driver_exists(self):
        """LifecycleDriver Protocol class can be imported."""
        from backend.drivers.protocol import LifecycleDriver
        assert LifecycleDriver is not None

    def test_battery_driver_exists(self):
        """BatteryDriver Protocol class can be imported."""
        from backend.drivers.protocol import BatteryDriver
        assert BatteryDriver is not None

    def test_lifecycle_driver_has_connect(self):
        """LifecycleDriver defines async connect method."""
        from backend.drivers.protocol import LifecycleDriver
        import inspect
        assert hasattr(LifecycleDriver, "connect")
        assert inspect.iscoroutinefunction(LifecycleDriver.connect)

    def test_lifecycle_driver_has_close(self):
        """LifecycleDriver defines async close method."""
        from backend.drivers.protocol import LifecycleDriver
        import inspect
        assert hasattr(LifecycleDriver, "close")
        assert inspect.iscoroutinefunction(LifecycleDriver.close)

    def test_lifecycle_driver_has_aenter(self):
        """LifecycleDriver defines __aenter__."""
        from backend.drivers.protocol import LifecycleDriver
        assert hasattr(LifecycleDriver, "__aenter__")

    def test_lifecycle_driver_has_aexit(self):
        """LifecycleDriver defines __aexit__."""
        from backend.drivers.protocol import LifecycleDriver
        assert hasattr(LifecycleDriver, "__aexit__")

    def test_battery_driver_has_read_state(self):
        """BatteryDriver defines async read_state method."""
        from backend.drivers.protocol import BatteryDriver
        import inspect
        assert hasattr(BatteryDriver, "read_state")
        assert inspect.iscoroutinefunction(BatteryDriver.read_state)

    def test_battery_driver_has_write_setpoint(self):
        """BatteryDriver defines async write_setpoint method."""
        from backend.drivers.protocol import BatteryDriver
        import inspect
        assert hasattr(BatteryDriver, "write_setpoint")
        assert inspect.iscoroutinefunction(BatteryDriver.write_setpoint)

    def test_protocol_not_runtime_checkable(self):
        """Protocol classes must NOT be decorated with @runtime_checkable."""
        from backend.drivers.protocol import LifecycleDriver, BatteryDriver
        from typing import runtime_checkable
        # runtime_checkable adds __protocol_attrs__ or changes isinstance behavior
        # The simplest check: trying isinstance should raise TypeError
        with pytest.raises(TypeError):
            isinstance(object(), LifecycleDriver)
        with pytest.raises(TypeError):
            isinstance(object(), BatteryDriver)
