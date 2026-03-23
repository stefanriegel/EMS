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
from backend.ha_mqtt_client import (
    HomeAssistantMqttClient,
    EntityDefinition,
    SENSOR_ENTITIES,
)
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
        )
        assert client._host == "broker.local"
        assert client._port == 8883
        assert client._username == "user"
        assert client._password == "pass"
        assert client._device_id == "my_ems"


class TestEntityDefinition:
    """Tests for the EntityDefinition dataclass."""

    def test_entity_definition_fields(self):
        """EntityDefinition has all required fields."""
        e = EntityDefinition(
            entity_id="test",
            name="Test",
            platform="sensor",
            unit="%",
            device_class="battery",
            state_class="measurement",
            entity_category=None,
            value_key="test_pct",
            device_group="system",
        )
        assert e.entity_id == "test"
        assert e.name == "Test"
        assert e.platform == "sensor"
        assert e.unit == "%"
        assert e.device_class == "battery"
        assert e.state_class == "measurement"
        assert e.entity_category is None
        assert e.value_key == "test_pct"
        assert e.device_group == "system"

    def test_entity_definition_is_frozen(self):
        """EntityDefinition instances are immutable."""
        e = EntityDefinition(
            entity_id="test", name="Test", platform="sensor",
            unit=None, device_class=None, state_class=None,
            entity_category=None, value_key="test", device_group="system",
        )
        with pytest.raises(AttributeError):
            e.entity_id = "changed"  # type: ignore[misc]

    def test_sensor_entities_count(self):
        """SENSOR_ENTITIES contains all 17 existing sensor entities."""
        assert len(SENSOR_ENTITIES) == 17

    def test_sensor_entities_are_entity_definitions(self):
        """All entries in SENSOR_ENTITIES are EntityDefinition instances."""
        for e in SENSOR_ENTITIES:
            assert isinstance(e, EntityDefinition)

    def test_all_sensor_entities_have_sensor_platform(self):
        """All entities in SENSOR_ENTITIES use platform='sensor'."""
        for e in SENSOR_ENTITIES:
            assert e.platform == "sensor", f"{e.entity_id} has platform={e.platform}"


class TestUniqueIdPreservation:
    """All existing unique_id values must be preserved (DISC-12)."""

    def test_existing_unique_ids_unchanged(self):
        """All 17 entities produce the same unique_id as before: ems_{entity_id}."""
        expected_ids = [
            "huawei_soc", "victron_soc", "huawei_setpoint", "victron_setpoint",
            "combined_power", "control_state", "evcc_battery_mode",
            "huawei_role", "victron_role", "huawei_power", "victron_power",
            "huawei_online", "victron_online", "pool_status",
            "victron_l1_power", "victron_l2_power", "victron_l3_power",
        ]
        entity_ids = [e.entity_id for e in SENSOR_ENTITIES]
        for eid in expected_ids:
            assert eid in entity_ids, f"Missing entity_id: {eid}"

    def test_unique_id_format_in_discovery(self):
        """unique_id in discovery payload is ems_{entity_id}."""
        client = _make_connected_client(device_id="ems")
        mock_paho = MagicMock()
        client._client = mock_paho

        client._ensure_discovery()

        # Check a few known unique_ids
        payloads = []
        for c in mock_paho.publish.call_args_list:
            topic = c.args[0] if c.args else c.kwargs.get("topic")
            if "config" in topic:
                payload = json.loads(c.args[1] if len(c.args) > 1 else c.kwargs["payload"])
                payloads.append(payload)

        unique_ids = {p["unique_id"] for p in payloads}
        assert "ems_huawei_soc" in unique_ids
        assert "ems_victron_soc" in unique_ids
        assert "ems_control_state" in unique_ids
        assert "ems_pool_status" in unique_ids


class TestDiscoveryOriginMetadata:
    """Discovery payloads must include origin metadata (DISC-01)."""

    def test_origin_metadata_present(self):
        """Every discovery payload has 'origin' with name and sw."""
        client = _make_connected_client(device_id="ems")
        mock_paho = MagicMock()
        client._client = mock_paho

        client._ensure_discovery()

        for c in mock_paho.publish.call_args_list:
            topic = c.args[0]
            if "config" in topic:
                payload = json.loads(c.args[1])
                assert "origin" in payload, f"Missing origin in {topic}"
                assert payload["origin"]["name"] == "EMS"
                assert payload["origin"]["sw"] == "1.2.0"


class TestDiscoveryAvailability:
    """LWT availability via ems/status topic (DISC-02)."""

    def test_availability_in_discovery_payload(self):
        """Every discovery payload has availability list with ems/status topic."""
        client = _make_connected_client(device_id="ems")
        mock_paho = MagicMock()
        client._client = mock_paho

        client._ensure_discovery()

        for c in mock_paho.publish.call_args_list:
            topic = c.args[0]
            if "config" in topic:
                payload = json.loads(c.args[1])
                assert "availability" in payload, f"Missing availability in {topic}"
                avail = payload["availability"]
                assert len(avail) == 1
                assert avail[0]["topic"] == "ems/status"
                assert avail[0]["payload_available"] == "online"
                assert avail[0]["payload_not_available"] == "offline"

    async def test_lwt_will_set_before_connect(self):
        """will_set() is called with ems/status, 'offline', retain=True before connect()."""
        client = _make_client()
        mock_paho = MagicMock()
        client._client = mock_paho

        await client.connect()

        mock_paho.will_set.assert_called_once_with(
            "ems/status", "offline", qos=1, retain=True,
        )
        # will_set must be called before connect
        will_set_order = None
        connect_order = None
        for i, c in enumerate(mock_paho.method_calls):
            if c[0] == "will_set" and will_set_order is None:
                will_set_order = i
            if c[0] == "connect" and connect_order is None:
                connect_order = i
        assert will_set_order is not None
        assert connect_order is not None
        assert will_set_order < connect_order, "will_set must be called before connect"

    def test_on_connect_publishes_online(self):
        """_on_connect publishes 'online' to ems/status with retain=True."""
        client = _make_client()
        mock_paho = MagicMock()
        client._client = mock_paho
        client._loop = MagicMock()

        client._on_connect(mock_paho, None, None, 0, None)

        mock_paho.publish.assert_called_with("ems/status", "online", qos=1, retain=True)

    async def test_disconnect_publishes_offline(self):
        """disconnect() publishes 'offline' to ems/status before disconnecting."""
        client = _make_connected_client()
        mock_paho = MagicMock()
        client._client = mock_paho

        await client.disconnect()

        # Check offline was published before disconnect
        offline_order = None
        disconnect_order = None
        for i, c in enumerate(mock_paho.method_calls):
            if c[0] == "publish" and offline_order is None:
                args = c[1]
                if len(args) >= 2 and args[0] == "ems/status" and args[1] == "offline":
                    offline_order = i
            if c[0] == "disconnect" and disconnect_order is None:
                disconnect_order = i
        assert offline_order is not None, "offline not published"
        assert disconnect_order is not None, "disconnect not called"
        assert offline_order < disconnect_order, "offline must be published before disconnect"


class TestDiscoveryExpireAfter:
    """All sensor entities have expire_after: 120 (DISC-03)."""

    def test_expire_after_on_all_sensors(self):
        """Every sensor entity discovery payload has expire_after: 120."""
        client = _make_connected_client(device_id="ems")
        mock_paho = MagicMock()
        client._client = mock_paho

        client._ensure_discovery()

        for c in mock_paho.publish.call_args_list:
            topic = c.args[0]
            if "config" in topic:
                payload = json.loads(c.args[1])
                assert payload.get("expire_after") == 120, (
                    f"Missing expire_after in {topic}"
                )


class TestDiscoveryHasEntityName:
    """Entity naming uses has_entity_name: True (DISC-04)."""

    def test_has_entity_name_true(self):
        """Every discovery payload includes has_entity_name: True."""
        client = _make_connected_client(device_id="ems")
        mock_paho = MagicMock()
        client._client = mock_paho

        client._ensure_discovery()

        for c in mock_paho.publish.call_args_list:
            topic = c.args[0]
            if "config" in topic:
                payload = json.loads(c.args[1])
                assert payload.get("has_entity_name") is True, (
                    f"Missing has_entity_name in {topic}"
                )

    def test_short_names_no_device_prefix(self):
        """Entity names are short without device name prefix."""
        names = {e.entity_id: e.name for e in SENSOR_ENTITIES}
        # These should NOT have device prefix like "Huawei Battery SOC"
        assert names["huawei_soc"] == "Battery SoC"
        assert names["victron_soc"] == "Battery SoC"
        assert names["huawei_setpoint"] == "Discharge Setpoint"
        assert names["victron_setpoint"] == "AC Setpoint"
        assert names["combined_power"] == "Combined Power"
        assert names["control_state"] == "Control State"
        assert names["evcc_battery_mode"] == "EVCC Battery Mode"
        assert names["huawei_role"] == "Battery Role"
        assert names["victron_role"] == "Battery Role"
        assert names["huawei_power"] == "Battery Power"
        assert names["victron_power"] == "Battery Power"
        assert names["huawei_online"] == "Online"
        assert names["victron_online"] == "Online"
        assert names["pool_status"] == "Pool Status"
        assert names["victron_l1_power"] == "L1 Power"
        assert names["victron_l2_power"] == "L2 Power"
        assert names["victron_l3_power"] == "L3 Power"


class TestDiscoveryEntityCategory:
    """Diagnostic entities tagged with entity_category: diagnostic (DISC-05)."""

    def test_diagnostic_entities(self):
        """Diagnostic entities have entity_category 'diagnostic'."""
        diagnostic_ids = {
            "huawei_online", "victron_online", "pool_status",
            "control_state", "evcc_battery_mode",
            "huawei_role", "victron_role",
            "victron_l1_power", "victron_l2_power", "victron_l3_power",
        }
        for e in SENSOR_ENTITIES:
            if e.entity_id in diagnostic_ids:
                assert e.entity_category == "diagnostic", (
                    f"{e.entity_id} should be diagnostic"
                )

    def test_primary_entities_no_category(self):
        """Primary entities (SoC, power, setpoints) have entity_category None."""
        primary_ids = {
            "huawei_soc", "victron_soc",
            "huawei_setpoint", "victron_setpoint",
            "combined_power", "huawei_power", "victron_power",
        }
        for e in SENSOR_ENTITIES:
            if e.entity_id in primary_ids:
                assert e.entity_category is None, (
                    f"{e.entity_id} should not have entity_category"
                )

    def test_entity_category_in_discovery_payload(self):
        """Discovery payloads include entity_category for diagnostic entities."""
        client = _make_connected_client(device_id="ems")
        mock_paho = MagicMock()
        client._client = mock_paho

        client._ensure_discovery()

        diagnostic_ids = {
            "huawei_online", "victron_online", "pool_status",
            "control_state", "evcc_battery_mode",
            "huawei_role", "victron_role",
            "victron_l1_power", "victron_l2_power", "victron_l3_power",
        }
        for c in mock_paho.publish.call_args_list:
            topic = c.args[0]
            if "config" in topic:
                payload = json.loads(c.args[1])
                # Extract entity_id from topic
                parts = topic.split("/")
                entity_id = parts[-2]  # homeassistant/sensor/{device_id}/{entity_id}/config
                if entity_id in diagnostic_ids:
                    assert payload.get("entity_category") == "diagnostic", (
                        f"{entity_id} missing entity_category in payload"
                    )


class TestDiscoveryDeviceClass:
    """Device class and state class audit (DISC-06)."""

    def test_soc_entities_battery_class(self):
        """SoC entities have device_class='battery', state_class='measurement'."""
        for e in SENSOR_ENTITIES:
            if e.entity_id in ("huawei_soc", "victron_soc"):
                assert e.device_class == "battery"
                assert e.state_class == "measurement"

    def test_power_entities_power_class(self):
        """Power entities have device_class='power', state_class='measurement'."""
        power_ids = {
            "huawei_setpoint", "victron_setpoint", "combined_power",
            "huawei_power", "victron_power",
            "victron_l1_power", "victron_l2_power", "victron_l3_power",
        }
        for e in SENSOR_ENTITIES:
            if e.entity_id in power_ids:
                assert e.device_class == "power", f"{e.entity_id} device_class"
                assert e.state_class == "measurement", f"{e.entity_id} state_class"

    def test_enum_entities(self):
        """Enum-like entities have device_class='enum', state_class=None."""
        enum_ids = {"control_state", "evcc_battery_mode", "huawei_role",
                    "victron_role", "pool_status"}
        for e in SENSOR_ENTITIES:
            if e.entity_id in enum_ids:
                assert e.device_class == "enum", f"{e.entity_id} device_class"
                assert e.state_class is None, f"{e.entity_id} state_class"

    def test_online_entities_no_device_class(self):
        """Online entities keep device_class=None for now (migrated in plan 02)."""
        for e in SENSOR_ENTITIES:
            if e.entity_id in ("huawei_online", "victron_online"):
                assert e.device_class is None, f"{e.entity_id} should have no device_class"


class TestDiscoveryConfigurationUrl:
    """Device info includes configuration_url (DISC-07)."""

    def test_configuration_url_in_device(self):
        """Discovery payload device info includes configuration_url."""
        client = _make_connected_client(device_id="ems")
        mock_paho = MagicMock()
        client._client = mock_paho

        client._ensure_discovery()

        for c in mock_paho.publish.call_args_list:
            topic = c.args[0]
            if "config" in topic:
                payload = json.loads(c.args[1])
                assert "configuration_url" in payload["device"], (
                    f"Missing configuration_url in {topic}"
                )


class TestThreeDeviceGrouping:
    """Three distinct devices: ems_huawei, ems_victron, ems_system (DISC-10)."""

    def test_three_device_identifiers(self):
        """All entities are grouped under three devices."""
        groups = {e.device_group for e in SENSOR_ENTITIES}
        assert groups == {"huawei", "victron", "system"}

    def test_huawei_entities(self):
        """Huawei device has the correct entities."""
        huawei_ids = {e.entity_id for e in SENSOR_ENTITIES if e.device_group == "huawei"}
        assert huawei_ids == {
            "huawei_soc", "huawei_setpoint", "huawei_power",
            "huawei_role", "huawei_online",
        }

    def test_victron_entities(self):
        """Victron device has the correct entities."""
        victron_ids = {e.entity_id for e in SENSOR_ENTITIES if e.device_group == "victron"}
        assert victron_ids == {
            "victron_soc", "victron_setpoint", "victron_power",
            "victron_role", "victron_online",
            "victron_l1_power", "victron_l2_power", "victron_l3_power",
        }

    def test_system_entities(self):
        """System device has the correct entities."""
        system_ids = {e.entity_id for e in SENSOR_ENTITIES if e.device_group == "system"}
        assert system_ids == {
            "combined_power", "control_state", "evcc_battery_mode", "pool_status",
        }

    def test_device_identifiers_in_discovery(self):
        """Discovery payloads use correct device identifiers per group."""
        client = _make_connected_client(device_id="ems")
        mock_paho = MagicMock()
        client._client = mock_paho

        client._ensure_discovery()

        device_identifiers = set()
        for c in mock_paho.publish.call_args_list:
            topic = c.args[0]
            if "config" in topic:
                payload = json.loads(c.args[1])
                ids = tuple(payload["device"]["identifiers"])
                device_identifiers.add(ids)

        assert ("ems_huawei",) in device_identifiers
        assert ("ems_victron",) in device_identifiers
        assert ("ems_system",) in device_identifiers

    def test_device_names_in_discovery(self):
        """Discovery payloads have correct device names."""
        client = _make_connected_client(device_id="ems")
        mock_paho = MagicMock()
        client._client = mock_paho

        client._ensure_discovery()

        device_names = set()
        for c in mock_paho.publish.call_args_list:
            topic = c.args[0]
            if "config" in topic:
                payload = json.loads(c.args[1])
                device_names.add(payload["device"]["name"])

        assert "EMS Huawei" in device_names
        assert "EMS Victron" in device_names
        assert "EMS System" in device_names


class TestHaMqttTopics:
    def test_discovery_topic_format(self):
        client = _make_client(device_id="ems")
        entity = EntityDefinition(
            entity_id="huawei_soc", name="Battery SoC", platform="sensor",
            unit="%", device_class="battery", state_class="measurement",
            entity_category=None, value_key="huawei_soc_pct", device_group="huawei",
        )
        assert client._discovery_topic(entity) == "homeassistant/sensor/ems/huawei_soc/config"

    def test_state_topic_format(self):
        client = _make_client(device_id="ems")
        assert client._state_topic() == "homeassistant/sensor/ems/state"

    def test_discovery_topic_uses_device_id(self):
        client = _make_client(device_id="my_ems")
        entity = EntityDefinition(
            entity_id="control_state", name="Control State", platform="sensor",
            unit=None, device_class="enum", state_class=None,
            entity_category="diagnostic", value_key="control_state", device_group="system",
        )
        assert "my_ems" in client._discovery_topic(entity)

    def test_state_topic_uses_device_id(self):
        client = _make_client(device_id="my_ems")
        assert "my_ems" in client._state_topic()


class TestHaMqttDiscovery:
    def test_ensure_discovery_publishes_all_entities(self):
        """_ensure_discovery() calls paho publish for each entity with retain=True."""
        client = _make_connected_client(device_id="ems")
        mock_paho = MagicMock()
        client._client = mock_paho

        client._ensure_discovery()

        assert mock_paho.publish.call_count == len(SENSOR_ENTITIES)
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

        assert mock_paho.publish.call_count == len(SENSOR_ENTITIES)

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
        """publish() sends len(SENSOR_ENTITIES) discovery + 1 state message on first call."""
        client = _make_connected_client()
        mock_paho = MagicMock()
        client._client = mock_paho

        await client.publish(_make_state())

        assert mock_paho.publish.call_count == len(SENSOR_ENTITIES) + 1

    async def test_publish_sends_only_state_on_subsequent_calls(self):
        """publish() sends only 1 state message after discovery is done."""
        client = _make_connected_client()
        mock_paho = MagicMock()
        client._client = mock_paho

        await client.publish(_make_state())
        await client.publish(_make_state())

        # First call: len(SENSOR_ENTITIES) discovery + 1 state
        # Second call: 1 state only
        assert mock_paho.publish.call_count == len(SENSOR_ENTITIES) + 2


class TestHaMqttCallbacks:
    def test_on_connect_sets_connected_via_threadsafe(self):
        """_on_connect schedules _set_connected(True) on the event loop."""
        client = _make_client()
        mock_loop = MagicMock()
        client._loop = mock_loop

        mock_paho = MagicMock()
        client._client = mock_paho
        client._on_connect(mock_paho, None, None, 0, None)

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

        # discovery + state = len(SENSOR_ENTITIES) + 1
        assert mock_paho.publish.call_count == len(SENSOR_ENTITIES) + 1

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


class TestValueKeysMatchDataclasses:
    """Value keys match CoordinatorState fields or documented extras."""

    def test_value_keys_valid(self):
        """All value_keys exist in CoordinatorState or extra_keys set."""
        import dataclasses as dc
        coord_fields = {f.name for f in dc.fields(CoordinatorState)}
        extra_keys = {
            "huawei_power_w", "victron_power_w",
            "victron_l1_power_w", "victron_l2_power_w", "victron_l3_power_w",
        }
        for e in SENSOR_ENTITIES:
            assert e.value_key in coord_fields or e.value_key in extra_keys, (
                f"Entity '{e.entity_id}' value_key '{e.value_key}' not found"
            )
