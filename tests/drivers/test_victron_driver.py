"""Unit tests for Victron data models, configuration, and driver logic.

No live hardware required.  The MQTT client is mocked with
``unittest.mock.MagicMock`` — no real broker connection is made.

Coverage:
  - ``VictronPhaseData`` / ``VictronSystemData`` dataclass contracts (T01)
  - ``VictronConfig.from_env()`` environment-variable reading (T01)
  - ``VictronDriver`` write methods — correct topic + payload + QoS (T02)
  - ``VictronDriver._process_message`` — state updates and discovery events (T02)
  - ``VictronDriver.read_system_state()`` — correct field mapping from state dict (T02)
  - Discovery event signalling from injected MQTT messages (T02)
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


# ===========================================================================
# VictronDriver unit tests (T02)
# All tests are synchronous — write methods and _process_message are sync.
# ===========================================================================

import asyncio
import json
from unittest.mock import MagicMock, call, patch

from backend.drivers.victron_driver import VictronDriver


def _get_or_create_event_loop() -> asyncio.AbstractEventLoop:
    """Return the current event loop; create one if none exists.

    Python 3.10+ raises RuntimeError in asyncio.get_event_loop() when
    called from a non-async context and no loop has been set.  Sync unit
    tests need an event loop only for asyncio.Event objects — we create
    one and set it as current so the driver helpers work.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


def _make_driver(
    portal_id: str = "abc123",
    instance_id: str = "257",
) -> VictronDriver:
    """Return a pre-connected VictronDriver with mocked paho client.

    Sets internal state directly to skip the async connect() flow, so
    write methods and _process_message can be tested synchronously.
    """
    driver = VictronDriver(host="192.168.0.10", port=1883)
    driver._client = MagicMock()
    driver._portal_id = portal_id
    driver._instance_id = instance_id
    driver._loop = _get_or_create_event_loop()
    # Build topic map with the injected IDs
    driver._topic_map = driver._build_topic_map()
    return driver


# ---------------------------------------------------------------------------
# TestVictronDriverWriteMethods
# ---------------------------------------------------------------------------

class TestVictronDriverWriteMethods:
    """write_* methods publish the correct topic, JSON payload, and QoS."""

    def test_write_ac_power_setpoint_phase1(self):
        """L1 AcPowerSetpoint publishes correct topic with given watts at QoS 1."""
        driver = _make_driver()
        driver.write_ac_power_setpoint(1, -500.0)
        driver._client.publish.assert_called_once_with(
            "W/abc123/vebus/257/Hub4/L1/AcPowerSetpoint",
            '{"value": -500.0}',
            qos=1,
        )

    def test_write_ac_power_setpoint_phase2(self):
        """L2 AcPowerSetpoint publishes correct topic."""
        driver = _make_driver()
        driver.write_ac_power_setpoint(2, 300.0)
        driver._client.publish.assert_called_once_with(
            "W/abc123/vebus/257/Hub4/L2/AcPowerSetpoint",
            '{"value": 300.0}',
            qos=1,
        )

    def test_write_ac_power_setpoint_phase3(self):
        """L3 AcPowerSetpoint publishes correct topic."""
        driver = _make_driver()
        driver.write_ac_power_setpoint(3, 0.0)
        driver._client.publish.assert_called_once_with(
            "W/abc123/vebus/257/Hub4/L3/AcPowerSetpoint",
            '{"value": 0.0}',
            qos=1,
        )

    def test_write_ess_mode(self):
        """write_ess_mode(3) publishes to the correct settings path."""
        driver = _make_driver()
        driver.write_ess_mode(3)
        driver._client.publish.assert_called_once_with(
            "W/abc123/settings/0/Settings/CGwacs/Hub4Mode",
            '{"value": 3}',
            qos=1,
        )

    def test_write_disable_charge_true(self):
        """write_disable_charge(True) sends value=1."""
        driver = _make_driver()
        driver.write_disable_charge(True)
        driver._client.publish.assert_called_once_with(
            "W/abc123/vebus/257/Hub4/DisableCharge",
            '{"value": 1}',
            qos=1,
        )

    def test_write_disable_charge_false(self):
        """write_disable_charge(False) sends value=0."""
        driver = _make_driver()
        driver.write_disable_charge(False)
        driver._client.publish.assert_called_once_with(
            "W/abc123/vebus/257/Hub4/DisableCharge",
            '{"value": 0}',
            qos=1,
        )

    def test_write_disable_feed_in_true(self):
        """write_disable_feed_in(True) sends value=1."""
        driver = _make_driver()
        driver.write_disable_feed_in(True)
        driver._client.publish.assert_called_once_with(
            "W/abc123/vebus/257/Hub4/DisableFeedIn",
            '{"value": 1}',
            qos=1,
        )

    def test_write_disable_feed_in_false(self):
        """write_disable_feed_in(False) sends value=0."""
        driver = _make_driver()
        driver.write_disable_feed_in(False)
        driver._client.publish.assert_called_once_with(
            "W/abc123/vebus/257/Hub4/DisableFeedIn",
            '{"value": 0}',
            qos=1,
        )

    def test_write_raises_when_not_connected(self):
        """All write methods raise AssertionError if _client is None."""
        driver = VictronDriver(host="192.168.0.10")
        # _client is None — should raise
        with pytest.raises(AssertionError, match="not connected"):
            driver.write_ac_power_setpoint(1, 0.0)


# ---------------------------------------------------------------------------
# TestVictronDriverMessageCallback
# ---------------------------------------------------------------------------

class TestVictronDriverMessageCallback:
    """_process_message updates self._state correctly."""

    def test_battery_soc_stored(self):
        """Battery SoC message updates battery_soc_pct in state."""
        driver = _make_driver()
        payload = json.dumps({"value": 82.5}).encode()
        driver._process_message("N/abc123/system/0/Dc/Battery/Soc", payload)
        assert driver._state["battery_soc_pct"] == pytest.approx(82.5)

    def test_battery_power_stored(self):
        """Battery power message updates battery_power_w in state."""
        driver = _make_driver()
        payload = json.dumps({"value": -3200.0}).encode()
        driver._process_message("N/abc123/system/0/Dc/Battery/Power", payload)
        assert driver._state["battery_power_w"] == pytest.approx(-3200.0)

    def test_l1_power_stored(self):
        """L1 AC power message updates l1_power_w in state."""
        driver = _make_driver()
        payload = json.dumps({"value": 1100.0}).encode()
        driver._process_message("N/abc123/vebus/257/Ac/Out/L1/P", payload)
        assert driver._state["l1_power_w"] == pytest.approx(1100.0)

    def test_l2_power_stored(self):
        """L2 AC power message updates l2_power_w in state."""
        driver = _make_driver()
        payload = json.dumps({"value": 1200.0}).encode()
        driver._process_message("N/abc123/vebus/257/Ac/Out/L2/P", payload)
        assert driver._state["l2_power_w"] == pytest.approx(1200.0)

    def test_l3_power_stored(self):
        """L3 AC power message updates l3_power_w in state."""
        driver = _make_driver()
        payload = json.dumps({"value": 1300.0}).encode()
        driver._process_message("N/abc123/vebus/257/Ac/Out/L3/P", payload)
        assert driver._state["l3_power_w"] == pytest.approx(1300.0)

    def test_none_value_in_payload_is_skipped(self):
        """A JSON payload with value=null does not update state."""
        driver = _make_driver()
        payload = json.dumps({"value": None}).encode()
        driver._process_message("N/abc123/system/0/Dc/Battery/Soc", payload)
        assert "battery_soc_pct" not in driver._state

    def test_invalid_json_is_silently_ignored(self):
        """Malformed JSON payload does not raise and does not update state."""
        driver = _make_driver()
        driver._process_message("N/abc123/system/0/Dc/Battery/Soc", b"not-json")
        assert "battery_soc_pct" not in driver._state

    def test_ess_mode_stored(self):
        """ESS mode message updates ess_mode in state."""
        driver = _make_driver()
        payload = json.dumps({"value": 3}).encode()
        driver._process_message(
            "N/abc123/settings/0/Settings/CGwacs/Hub4Mode", payload
        )
        assert driver._state["ess_mode"] == 3

    def test_setpoint_readback_stored(self):
        """L1 AcPowerSetpoint readback updates l1_setpoint_w in state."""
        driver = _make_driver()
        payload = json.dumps({"value": -500.0}).encode()
        driver._process_message(
            "N/abc123/vebus/257/Hub4/L1/AcPowerSetpoint", payload
        )
        assert driver._state["l1_setpoint_w"] == pytest.approx(-500.0)


# ---------------------------------------------------------------------------
# TestVictronDriverDiscovery
# ---------------------------------------------------------------------------

class TestVictronDriverDiscovery:
    """_process_message sets discovery IDs and events correctly."""

    def test_portal_id_discovered(self):
        """N/+/system/0/Serial message sets _portal_id and signals _portal_event."""
        driver = VictronDriver(host="192.168.0.10")
        driver._loop = _get_or_create_event_loop()
        payload = json.dumps({"value": "abc123"}).encode()
        driver._process_message("N/abc123/system/0/Serial", payload)
        assert driver._portal_id == "abc123"
        assert driver._portal_event.is_set()

    def test_instance_id_discovered(self):
        """N/{portal}/vebus/+/ProductId message sets _instance_id and signals event."""
        driver = VictronDriver(host="192.168.0.10")
        driver._loop = _get_or_create_event_loop()
        # portal must be known first (portal discovery already done)
        driver._portal_id = "abc123"
        driver._portal_event.set()
        payload = json.dumps({"value": 2634}).encode()
        driver._process_message("N/abc123/vebus/257/ProductId", payload)
        assert driver._instance_id == "257"
        assert driver._instance_event.is_set()

    def test_portal_event_not_set_twice(self):
        """Second Serial message does not overwrite already-discovered portalId."""
        driver = VictronDriver(host="192.168.0.10")
        driver._loop = _get_or_create_event_loop()
        payload_first = json.dumps({"value": "abc123"}).encode()
        payload_second = json.dumps({"value": "different"}).encode()
        driver._process_message("N/abc123/system/0/Serial", payload_first)
        driver._process_message("N/different/system/0/Serial", payload_second)
        # First value is kept (event guards double-set)
        assert driver._portal_id == "abc123"

    def test_instance_event_not_set_twice(self):
        """Second ProductId message does not overwrite already-discovered instanceId."""
        driver = VictronDriver(host="192.168.0.10")
        driver._loop = _get_or_create_event_loop()
        driver._portal_id = "abc123"
        driver._portal_event.set()
        payload_first = json.dumps({"value": 2634}).encode()
        payload_second = json.dumps({"value": 2635}).encode()
        driver._process_message("N/abc123/vebus/257/ProductId", payload_first)
        driver._process_message("N/abc123/vebus/258/ProductId", payload_second)
        assert driver._instance_id == "257"


# ---------------------------------------------------------------------------
# TestVictronDriverReadState
# ---------------------------------------------------------------------------

class TestVictronDriverReadState:
    """read_system_state() maps _state dict → VictronSystemData correctly."""

    def _inject_full_state(self, driver: VictronDriver) -> None:
        driver._state.update(
            {
                "battery_soc_pct": 75.0,
                "battery_power_w": 2500.0,
                "battery_current_a": 52.0,
                "battery_voltage_v": 48.1,
                "l1_power_w": 1100.0,
                "l1_current_a": 4.8,
                "l1_voltage_v": 230.0,
                "l1_setpoint_w": -400.0,
                "l2_power_w": 1200.0,
                "l2_current_a": 5.2,
                "l2_voltage_v": 231.0,
                "l2_setpoint_w": None,
                "l3_power_w": 1300.0,
                "l3_current_a": 5.6,
                "l3_voltage_v": 229.0,
                "l3_setpoint_w": -600.0,
                "ess_mode": 3,
                "system_state": 9,
                "vebus_state": 9,
            }
        )

    def test_battery_fields_mapped(self):
        """Battery fields are read from state and placed correctly."""
        driver = _make_driver()
        self._inject_full_state(driver)
        state = driver.read_system_state()
        assert state.battery_soc_pct == pytest.approx(75.0)
        assert state.battery_power_w == pytest.approx(2500.0)
        assert state.battery_current_a == pytest.approx(52.0)
        assert state.battery_voltage_v == pytest.approx(48.1)

    def test_phase_fields_mapped(self):
        """Per-phase fields are placed in the correct VictronPhaseData objects."""
        driver = _make_driver()
        self._inject_full_state(driver)
        state = driver.read_system_state()
        assert state.l1.power_w == pytest.approx(1100.0)
        assert state.l2.power_w == pytest.approx(1200.0)
        assert state.l3.power_w == pytest.approx(1300.0)
        assert state.l1.setpoint_w == pytest.approx(-400.0)
        assert state.l2.setpoint_w is None
        assert state.l3.setpoint_w == pytest.approx(-600.0)

    def test_optional_int_fields_mapped(self):
        """ess_mode / system_state / vebus_state are read from state."""
        driver = _make_driver()
        self._inject_full_state(driver)
        state = driver.read_system_state()
        assert state.ess_mode == 3
        assert state.system_state == 9
        assert state.vebus_state == 9

    def test_optional_fields_none_when_not_received(self):
        """Fields not yet in _state default to None for optional fields, 0.0 for floats."""
        driver = _make_driver()
        # Empty state
        state = driver.read_system_state()
        assert state.ess_mode is None
        assert state.system_state is None
        assert state.vebus_state is None
        # Float fields default to 0.0
        assert state.battery_soc_pct == pytest.approx(0.0)
        assert state.l1.power_w == pytest.approx(0.0)

    def test_timestamp_is_positive_float(self):
        """timestamp is a positive float (from time.monotonic())."""
        driver = _make_driver()
        state = driver.read_system_state()
        assert isinstance(state.timestamp, float)
        assert state.timestamp > 0.0

    def test_charge_discharge_sign_conventions(self):
        """charge_power_w and discharge_power_w sign conventions are correct."""
        driver = _make_driver()
        driver._state["battery_power_w"] = 3000.0
        state = driver.read_system_state()
        assert state.charge_power_w == pytest.approx(3000.0)
        assert state.discharge_power_w == pytest.approx(0.0)

        driver._state["battery_power_w"] = -2000.0
        state2 = driver.read_system_state()
        assert state2.charge_power_w == pytest.approx(0.0)
        assert state2.discharge_power_w == pytest.approx(2000.0)
