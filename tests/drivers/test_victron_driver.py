"""Unit tests for Victron data models and configuration.

No live hardware required.  No MQTT client is instantiated.  This module
only tests the dataclass contracts defined in
``backend.drivers.victron_models`` and the env-reading logic in
``backend.config``.
"""
from __future__ import annotations

import pytest

from backend.config import VictronConfig
from backend.drivers.victron_models import VictronPhaseData, VictronSystemData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_phase(**overrides) -> VictronPhaseData:
    """Return a fully-populated VictronPhaseData with sensible defaults."""
    defaults: dict = {
        "power_w": 1000.0,
        "current_a": 4.4,
        "voltage_v": 230.0,
        "setpoint_w": None,
    }
    defaults.update(overrides)
    return VictronPhaseData(**defaults)


def _make_system_data(**overrides) -> VictronSystemData:
    """Return a fully-populated VictronSystemData with sensible defaults."""
    defaults: dict = {
        "battery_soc_pct": 60.0,
        "battery_power_w": 0.0,
        "battery_current_a": 0.0,
        "battery_voltage_v": 48.0,
        "l1": _make_phase(),
        "l2": _make_phase(),
        "l3": _make_phase(),
        "ess_mode": 3,
        "system_state": 9,
        "vebus_state": 9,
        "timestamp": 12345.0,
    }
    defaults.update(overrides)
    return VictronSystemData(**defaults)


# ---------------------------------------------------------------------------
# VictronPhaseData
# ---------------------------------------------------------------------------

class TestVictronPhaseData:
    def test_construction_all_fields(self):
        """All four fields are stored and retrieved after construction."""
        phase = VictronPhaseData(
            power_w=1500.0,
            current_a=6.5,
            voltage_v=231.0,
            setpoint_w=-500.0,
        )
        assert phase.power_w == pytest.approx(1500.0)
        assert phase.current_a == pytest.approx(6.5)
        assert phase.voltage_v == pytest.approx(231.0)
        assert phase.setpoint_w == pytest.approx(-500.0)

    def test_setpoint_w_accepts_none(self):
        """setpoint_w=None is valid (no setpoint written yet)."""
        phase = _make_phase(setpoint_w=None)
        assert phase.setpoint_w is None

    def test_setpoint_w_accepts_zero(self):
        """setpoint_w=0.0 is a valid written setpoint."""
        phase = _make_phase(setpoint_w=0.0)
        assert phase.setpoint_w == pytest.approx(0.0)

    def test_setpoint_w_accepts_positive(self):
        """setpoint_w can be positive (grid import setpoint)."""
        phase = _make_phase(setpoint_w=800.0)
        assert phase.setpoint_w == pytest.approx(800.0)

    def test_setpoint_w_accepts_negative(self):
        """setpoint_w can be negative (grid export / ESS discharge)."""
        phase = _make_phase(setpoint_w=-1200.0)
        assert phase.setpoint_w == pytest.approx(-1200.0)


# ---------------------------------------------------------------------------
# VictronSystemData — construction and field access
# ---------------------------------------------------------------------------

class TestVictronSystemData:
    def test_construction_typical_values(self):
        """All fields are stored correctly after construction with typical values."""
        data = _make_system_data(
            battery_soc_pct=75.0,
            battery_power_w=2500.0,
            battery_current_a=52.0,
            battery_voltage_v=48.2,
            ess_mode=3,
            system_state=9,
            vebus_state=9,
            timestamp=99999.5,
        )
        assert data.battery_soc_pct == pytest.approx(75.0)
        assert data.battery_power_w == pytest.approx(2500.0)
        assert data.battery_current_a == pytest.approx(52.0)
        assert data.battery_voltage_v == pytest.approx(48.2)
        assert data.ess_mode == 3
        assert data.system_state == 9
        assert data.vebus_state == 9
        assert data.timestamp == pytest.approx(99999.5)

    def test_phase_fields_accessible(self):
        """l1, l2, l3 fields are accessible and independent."""
        l1 = _make_phase(power_w=1100.0)
        l2 = _make_phase(power_w=1200.0)
        l3 = _make_phase(power_w=1300.0)
        data = _make_system_data(l1=l1, l2=l2, l3=l3)
        assert data.l1.power_w == pytest.approx(1100.0)
        assert data.l2.power_w == pytest.approx(1200.0)
        assert data.l3.power_w == pytest.approx(1300.0)

    # --- charge_power_w / discharge_power_w properties ---

    def test_charging_positive_battery_power(self):
        """Positive battery_power_w → charge_power_w equals it; discharge_power_w is 0."""
        data = _make_system_data(battery_power_w=3000.0)
        assert data.charge_power_w == pytest.approx(3000.0)
        assert data.discharge_power_w == pytest.approx(0.0)

    def test_discharging_negative_battery_power(self):
        """Negative battery_power_w → discharge_power_w is |value|; charge_power_w is 0."""
        data = _make_system_data(battery_power_w=-2000.0)
        assert data.charge_power_w == pytest.approx(0.0)
        assert data.discharge_power_w == pytest.approx(2000.0)

    def test_idle_zero_battery_power(self):
        """Zero battery_power_w → both charge_power_w and discharge_power_w are 0."""
        data = _make_system_data(battery_power_w=0.0)
        assert data.charge_power_w == pytest.approx(0.0)
        assert data.discharge_power_w == pytest.approx(0.0)

    # --- SoC boundaries ---

    def test_soc_boundary_zero(self):
        """battery_soc_pct=0.0 must be stored and retrieved exactly."""
        data = _make_system_data(battery_soc_pct=0.0)
        assert data.battery_soc_pct == pytest.approx(0.0)

    def test_soc_boundary_full(self):
        """battery_soc_pct=100.0 must be stored and retrieved exactly."""
        data = _make_system_data(battery_soc_pct=100.0)
        assert data.battery_soc_pct == pytest.approx(100.0)

    # --- Optional integer fields ---

    def test_ess_mode_none(self):
        """ess_mode=None is accepted (not yet received from broker)."""
        data = _make_system_data(ess_mode=None)
        assert data.ess_mode is None

    def test_system_state_none(self):
        """system_state=None is accepted."""
        data = _make_system_data(system_state=None)
        assert data.system_state is None

    def test_vebus_state_none(self):
        """vebus_state=None is accepted."""
        data = _make_system_data(vebus_state=None)
        assert data.vebus_state is None

    def test_all_optional_none(self):
        """All three optional int fields can be None simultaneously."""
        data = _make_system_data(ess_mode=None, system_state=None, vebus_state=None)
        assert data.ess_mode is None
        assert data.system_state is None
        assert data.vebus_state is None


# ---------------------------------------------------------------------------
# VictronConfig — environment variable reading
# ---------------------------------------------------------------------------

class TestVictronConfig:
    def test_from_env_host_and_default_port(self, monkeypatch):
        """VICTRON_HOST is picked up; port defaults to 1883 when VICTRON_PORT unset."""
        monkeypatch.setenv("VICTRON_HOST", "192.168.0.10")
        monkeypatch.delenv("VICTRON_PORT", raising=False)

        cfg = VictronConfig.from_env()

        assert cfg.host == "192.168.0.10"
        assert cfg.port == 1883

    def test_from_env_custom_port(self, monkeypatch):
        """VICTRON_PORT overrides the default port."""
        monkeypatch.setenv("VICTRON_HOST", "10.0.0.5")
        monkeypatch.setenv("VICTRON_PORT", "8883")

        cfg = VictronConfig.from_env()

        assert cfg.port == 8883

    def test_from_env_missing_host_raises(self, monkeypatch):
        """Missing VICTRON_HOST must raise KeyError, not silently produce bad state."""
        monkeypatch.delenv("VICTRON_HOST", raising=False)

        with pytest.raises(KeyError):
            VictronConfig.from_env()

    def test_default_values_direct_construction(self):
        """VictronConfig defaults are correct when constructed directly."""
        cfg = VictronConfig(host="localhost")
        assert cfg.port == 1883
        assert cfg.timeout_s == pytest.approx(10.0)
        assert cfg.discovery_timeout_s == pytest.approx(15.0)
