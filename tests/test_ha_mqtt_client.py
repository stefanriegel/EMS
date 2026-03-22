"""Unit tests for HomeAssistantMqttClient.

Tests mock paho.mqtt.client.Client — no live broker required.  All async
tests run under anyio_mode='auto' (pyproject.toml).

Test structure follows the TestGridCharge / TestDischargeLock pattern:
direct attribute manipulation, minimal patching, synchronous helpers where
possible.
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, call, patch

import pytest

from backend.controller_model import CoordinatorState
from backend.ha_mqtt_client import HomeAssistantMqttClient, _ENTITIES
from backend.unified_model import ControlState, UnifiedPoolState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(**kwargs) -> HomeAssistantMqttClient:
    defaults = {"host": "localhost", "port": 1883}
    defaults.update(kwargs)
    return HomeAssistantMqttClient(**defaults)


def _make_state(**overrides) -> UnifiedPoolState:
    """Return a fully-populated UnifiedPoolState with sensible defaults."""
    defaults: dict = {
        "combined_soc_pct": 67.5,
        "huawei_soc_pct": 75.0,
        "victron_soc_pct": 60.0,
        "huawei_available": True,
        "victron_available": True,
        "control_state": ControlState.IDLE,
        "huawei_discharge_setpoint_w": 2000,
        "victron_discharge_setpoint_w": 500,
        "combined_power_w": 0.0,
        "huawei_charge_headroom_w": 3000,
        "victron_charge_headroom_w": 1500.0,
        "timestamp": time.monotonic(),
        "grid_charge_slot_active": False,
        "evcc_battery_mode": "normal",
    }
    defaults.update(overrides)
    return UnifiedPoolState(**defaults)


def _make_connected_client(**kwargs) -> HomeAssistantMqttClient:
    """Return a client with _connected=True (simulates post-CONNACK state)."""
    client = _make_client(**kwargs)
    client._connected = True
    return client


# ---------------------------------------------------------------------------


class TestHaMqttClientInit:
    def test_defaults(self):
        client = _make_client()
        assert client._host == "localhost"
        assert client._port == 1883
        assert client._device_id == "ems"
        assert client._device_name == "Energy Management System"
        assert client._connected is False
        assert client._discovery_sent is False
        assert client._client is not None  # paho client created in __init__

    def test_custom_params(self):
        client = _make_client(
            host="broker.local",
            port=8883,
            username="user",
            password="pass",
            device_id="my_ems",
            device_name="My EMS",
        )
        assert client._host == "broker.local"
        assert client._port == 8883
        assert client._username == "user"
        assert client._password == "pass"
        assert client._device_id == "my_ems"
        assert client._device_name == "My EMS"


class TestHaMqttTopics:
    def test_discovery_topic_format(self):
        client = _make_client(device_id="ems")
        assert client._discovery_topic("huawei_soc") == "homeassistant/sensor/ems/huawei_soc/config"

    def test_state_topic_format(self):
        client = _make_client(device_id="ems")
        assert client._state_topic() == "homeassistant/sensor/ems/state"

    def test_discovery_topic_uses_device_id(self):
        client = _make_client(device_id="my_ems")
        assert "my_ems" in client._discovery_topic("control_state")

    def test_state_topic_uses_device_id(self):
        client = _make_client(device_id="my_ems")
        assert "my_ems" in client._state_topic()


class TestHaMqttDiscoveryPayload:
    def test_sensor_with_unit_and_class(self):
        client = _make_client(device_id="ems", device_name="EMS")
        payload = json.loads(
            client._discovery_payload(
                "huawei_soc", "Huawei Battery SOC", "%", "battery", "measurement", "huawei_soc_pct"
            )
        )
        assert payload["name"] == "Huawei Battery SOC"
        assert payload["unit_of_measurement"] == "%"
        assert payload["device_class"] == "battery"
        assert payload["state_class"] == "measurement"
        assert payload["unique_id"] == "ems_huawei_soc"
        assert "value_json.huawei_soc_pct" in payload["value_template"]
        assert payload["state_topic"] == "homeassistant/sensor/ems/state"
        assert payload["device"]["identifiers"] == ["ems"]
        assert payload["device"]["name"] == "EMS"

    def test_sensor_without_unit_omits_optional_keys(self):
        client = _make_client(device_id="ems")
        payload = json.loads(
            client._discovery_payload(
                "control_state", "EMS Control State", None, None, None, "control_state"
            )
        )
        assert "unit_of_measurement" not in payload
        assert "device_class" not in payload
        assert "state_class" not in payload

    def test_all_entities_defined(self):
        entity_ids = [e[0] for e in _ENTITIES]
        assert "huawei_soc" in entity_ids
        assert "victron_soc" in entity_ids
        assert "control_state" in entity_ids
        assert "evcc_battery_mode" in entity_ids

    def test_entity_count(self):
        assert len(_ENTITIES) == 17

    def test_value_keys_match_unified_pool_state_fields(self):
        """First 7 entity value_keys must be real UnifiedPoolState fields."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(UnifiedPoolState)}
        for entity_id, _, _, _, _, value_key in _ENTITIES[:7]:
            assert value_key in field_names, (
                f"Entity '{entity_id}' value_key '{value_key}' not in UnifiedPoolState"
            )

    def test_new_entity_ids_present(self):
        """All 10 new entity IDs must be present in _ENTITIES."""
        entity_ids = [e[0] for e in _ENTITIES]
        expected_new = [
            "huawei_role", "victron_role", "huawei_power", "victron_power",
            "huawei_online", "victron_online", "pool_status",
            "victron_l1_power", "victron_l2_power", "victron_l3_power",
        ]
        for eid in expected_new:
            assert eid in entity_ids, f"Missing new entity: {eid}"

    def test_existing_7_entities_unchanged(self):
        """First 7 entities must be identical to the original list."""
        original_7 = [
            ("huawei_soc",        "Huawei Battery SOC",        "%",  "battery", "measurement", "huawei_soc_pct"),
            ("victron_soc",       "Victron Battery SOC",        "%",  "battery", "measurement", "victron_soc_pct"),
            ("huawei_setpoint",   "Huawei Discharge Setpoint",  "W",  "power",   "measurement", "huawei_discharge_setpoint_w"),
            ("victron_setpoint",  "Victron AC Setpoint",        "W",  "power",   "measurement", "victron_discharge_setpoint_w"),
            ("combined_power",    "Combined Battery Power",     "W",  "power",   "measurement", "combined_power_w"),
            ("control_state",     "EMS Control State",          None, None,      None,          "control_state"),
            ("evcc_battery_mode", "EVCC Battery Mode",          None, None,      None,          "evcc_battery_mode"),
        ]
        assert _ENTITIES[:7] == original_7

    def test_no_connectivity_device_class(self):
        """No entity should use device_class='connectivity' (avoids binary_sensor pitfall)."""
        for entity_id, _, _, device_class, _, _ in _ENTITIES:
            assert device_class != "connectivity", (
                f"Entity '{entity_id}' uses device_class='connectivity' — must use None"
            )

    def test_new_entity_value_keys_match_coordinator_state(self):
        """New entity value_keys should match CoordinatorState fields or be documented extras."""
        import dataclasses
        coord_fields = {f.name for f in dataclasses.fields(CoordinatorState)}
        # These are populated by coordinator as extra_fields, not direct CoordinatorState fields
        extra_keys = {"huawei_power_w", "victron_power_w", "victron_l1_power_w", "victron_l2_power_w", "victron_l3_power_w"}
        for entity_id, _, _, _, _, value_key in _ENTITIES[7:]:
            assert value_key in coord_fields or value_key in extra_keys, (
                f"New entity '{entity_id}' value_key '{value_key}' not in CoordinatorState or extra_keys"
            )


class TestHaMqttDiscovery:
    def test_ensure_discovery_publishes_all_entities(self):
        """_ensure_discovery() calls paho publish for each entity with retain=True."""
        client = _make_connected_client(device_id="ems")
        mock_paho = MagicMock()
        client._client = mock_paho

        client._ensure_discovery()

        assert mock_paho.publish.call_count == len(_ENTITIES)
        # All discovery publishes must use retain=True
        for c in mock_paho.publish.call_args_list:
            assert c.kwargs.get("retain", c.args[2] if len(c.args) > 2 else None) is True

    def test_ensure_discovery_only_runs_once(self):
        """Second call to _ensure_discovery() is a no-op."""
        client = _make_connected_client()
        mock_paho = MagicMock()
        client._client = mock_paho

        client._ensure_discovery()
        client._ensure_discovery()

        assert mock_paho.publish.call_count == len(_ENTITIES)

    def test_ensure_discovery_sets_flag(self):
        client = _make_connected_client()
        client._client = MagicMock()
        assert client._discovery_sent is False
        client._ensure_discovery()
        assert client._discovery_sent is True


class TestHaMqttPublishState:
    def test_publish_state_sends_valid_json(self):
        """_publish_state() publishes JSON containing UnifiedPoolState fields."""
        client = _make_connected_client(device_id="ems")
        mock_paho = MagicMock()
        client._client = mock_paho

        state = _make_state(huawei_soc_pct=80.0, evcc_battery_mode="hold")
        client._publish_state(state)

        mock_paho.publish.assert_called_once()
        topic, payload = mock_paho.publish.call_args[0]
        assert topic == "homeassistant/sensor/ems/state"
        parsed = json.loads(payload)
        assert parsed["huawei_soc_pct"] == 80.0
        assert parsed["evcc_battery_mode"] == "hold"

    def test_publish_state_includes_control_state_string(self):
        """control_state enum is serialised as its string value."""
        client = _make_connected_client()
        mock_paho = MagicMock()
        client._client = mock_paho

        state = _make_state(control_state=ControlState.DISCHARGE_LOCKED)
        client._publish_state(state)

        _, payload = mock_paho.publish.call_args[0]
        parsed = json.loads(payload)
        assert parsed["control_state"] == "DISCHARGE_LOCKED"


class TestHaMqttPublishAsync:
    async def test_publish_skips_when_not_connected(self):
        """publish() is silent when _connected is False."""
        client = _make_client()  # _connected = False
        mock_paho = MagicMock()
        client._client = mock_paho

        await client.publish(_make_state())

        mock_paho.publish.assert_not_called()

    async def test_publish_sends_discovery_then_state_on_first_call(self):
        """publish() sends len(_ENTITIES) discovery + 1 state message on first call."""
        client = _make_connected_client()
        mock_paho = MagicMock()
        client._client = mock_paho

        await client.publish(_make_state())

        assert mock_paho.publish.call_count == len(_ENTITIES) + 1

    async def test_publish_sends_only_state_on_subsequent_calls(self):
        """publish() sends only 1 state message after discovery is done."""
        client = _make_connected_client()
        mock_paho = MagicMock()
        client._client = mock_paho

        await client.publish(_make_state())
        await client.publish(_make_state())

        # First call: len(_ENTITIES) discovery + 1 state
        # Second call: 1 state only
        assert mock_paho.publish.call_count == len(_ENTITIES) + 2


class TestHaMqttCallbacks:
    def test_on_connect_sets_connected_via_threadsafe(self):
        """_on_connect schedules _set_connected(True) on the event loop."""
        client = _make_client()
        mock_loop = MagicMock()
        client._loop = mock_loop

        client._on_connect(MagicMock(), None, None, 0, None)

        mock_loop.call_soon_threadsafe.assert_called_once_with(client._set_connected, True)

    def test_on_connect_rejected_does_not_set_connected(self):
        """_on_connect with non-zero reason_code does not schedule _set_connected."""
        client = _make_client()
        mock_loop = MagicMock()
        client._loop = mock_loop

        client._on_connect(MagicMock(), None, None, 1, None)

        mock_loop.call_soon_threadsafe.assert_not_called()

    def test_on_disconnect_clears_connected_via_threadsafe(self):
        """_on_disconnect schedules _set_connected(False) on the event loop."""
        client = _make_client()
        mock_loop = MagicMock()
        client._loop = mock_loop

        client._on_disconnect(MagicMock(), None, None, 0, None)

        mock_loop.call_soon_threadsafe.assert_called_once_with(client._set_connected, False)

    def test_set_connected_true(self):
        client = _make_client()
        client._set_connected(True)
        assert client._connected is True

    def test_set_connected_false_resets_discovery(self):
        """_set_connected(False) also resets _discovery_sent so it re-runs on reconnect."""
        client = _make_client()
        client._connected = True
        client._discovery_sent = True
        client._set_connected(False)
        assert client._connected is False
        assert client._discovery_sent is False


def _make_coordinator_state(**overrides) -> CoordinatorState:
    """Return a fully-populated CoordinatorState with sensible defaults."""
    defaults: dict = {
        "combined_soc_pct": 67.5,
        "huawei_soc_pct": 75.0,
        "victron_soc_pct": 60.0,
        "huawei_available": True,
        "victron_available": True,
        "control_state": "DISCHARGE",
        "huawei_discharge_setpoint_w": 2000,
        "victron_discharge_setpoint_w": 500,
        "combined_power_w": -2500.0,
        "huawei_charge_headroom_w": 3000,
        "victron_charge_headroom_w": 1500.0,
        "timestamp": time.monotonic(),
        "huawei_role": "PRIMARY_DISCHARGE",
        "victron_role": "SECONDARY_DISCHARGE",
        "pool_status": "NORMAL",
    }
    defaults.update(overrides)
    return CoordinatorState(**defaults)


class TestHaMqttPublishCoordinatorState:
    async def test_publish_accepts_coordinator_state(self):
        """publish() must accept CoordinatorState without TypeError."""
        client = _make_connected_client()
        mock_paho = MagicMock()
        client._client = mock_paho

        state = _make_coordinator_state()
        await client.publish(state)

        # discovery + state = len(_ENTITIES) + 1
        assert mock_paho.publish.call_count == len(_ENTITIES) + 1

    async def test_coordinator_state_payload_has_new_fields(self):
        """Published JSON must include huawei_role, victron_role, pool_status."""
        client = _make_connected_client()
        mock_paho = MagicMock()
        client._client = mock_paho
        client._discovery_sent = True

        state = _make_coordinator_state(
            huawei_role="CHARGING",
            victron_role="HOLDING",
            pool_status="DEGRADED",
        )
        await client.publish(state)

        _, payload = mock_paho.publish.call_args[0]
        parsed = json.loads(payload)
        assert parsed["huawei_role"] == "CHARGING"
        assert parsed["victron_role"] == "HOLDING"
        assert parsed["pool_status"] == "DEGRADED"

    async def test_extra_fields_merged_into_payload(self):
        """extra_fields parameter must be merged into the published JSON."""
        client = _make_connected_client()
        mock_paho = MagicMock()
        client._client = mock_paho
        client._discovery_sent = True

        state = _make_coordinator_state()
        extra = {
            "victron_l1_power_w": 100.0,
            "victron_l2_power_w": 200.0,
            "victron_l3_power_w": 300.0,
            "huawei_power_w": -3000.0,
            "victron_power_w": -2000.0,
        }
        await client.publish(state, extra_fields=extra)

        _, payload = mock_paho.publish.call_args[0]
        parsed = json.loads(payload)
        assert parsed["victron_l1_power_w"] == 100.0
        assert parsed["huawei_power_w"] == -3000.0

    async def test_extra_fields_none_is_ok(self):
        """publish() with extra_fields=None must work without error."""
        client = _make_connected_client()
        mock_paho = MagicMock()
        client._client = mock_paho
        client._discovery_sent = True

        state = _make_coordinator_state()
        await client.publish(state, extra_fields=None)

        mock_paho.publish.assert_called_once()


class TestHaMqttDisconnect:
    async def test_disconnect_resets_flags(self):
        """disconnect() resets _connected and _discovery_sent."""
        client = _make_connected_client()
        client._discovery_sent = True
        mock_paho = MagicMock()
        client._client = mock_paho

        await client.disconnect()

        assert client._connected is False
        assert client._discovery_sent is False
        mock_paho.loop_stop.assert_called_once()
        mock_paho.disconnect.assert_called_once()
