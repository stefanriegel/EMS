"""Home Assistant MQTT integration for EMS telemetry.

Publishes ``UnifiedPoolState`` snapshots to Home Assistant via standard MQTT
discovery.  Uses ``paho-mqtt`` (already a project dependency) with the same
threading model as ``evcc_mqtt_driver.py``.

Threading model
---------------
paho-mqtt callbacks (``_on_connect``, ``_on_disconnect``) run in paho's
background network thread.  State mutations in those callbacks cross the
thread boundary via ``loop.call_soon_threadsafe()``.  ``publish()`` is called
from the asyncio event loop thread and uses paho's thread-safe ``publish()``
method directly — no extra synchronisation needed.

Availability / discovery model
-------------------------------
``_connected`` becomes ``True`` inside ``_on_connect`` (after CONNACK).
``_discovery_sent`` becomes ``True`` after all discovery payloads have been
published.  Both are directly inspectable for debugging.

LWT (Last Will and Testament) ensures that all entities show as unavailable
in HA when EMS disconnects: ``will_set()`` is called before ``connect()``,
and ``"online"`` is published on successful CONNACK.

Connection lifecycle
--------------------
``connect()`` initiates a non-blocking TCP connection and starts paho's
background network thread.  If the broker is unreachable the exception is
caught and logged at WARNING — startup is never blocked.

``publish()`` silently skips the payload if not connected.

Logging
-------
Module logger: ``backend.ha_mqtt_client``.  Key lines:

* ``INFO  "HA MQTT connected"``            — on successful CONNACK
* ``INFO  "HA MQTT discovery published"``  — after all config payloads sent
* ``WARNING "HA MQTT connect failed: ..."`` — on OSError / refused
* ``WARNING "HA MQTT disconnected unexpectedly"`` — on non-zero disconnect rc
* ``ERROR "HA MQTT publish failed: ..."``  — on publish exception
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import paho.mqtt.client as mqtt

if TYPE_CHECKING:
    from backend.unified_model import UnifiedPoolState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Entity definitions
# ---------------------------------------------------------------------------

_AVAILABILITY_TOPIC = "ems/status"

_EMS_VERSION = "1.2.0"


@dataclass(frozen=True)
class EntityDefinition:
    """Typed definition for a single HA MQTT sensor entity.

    Replaces the previous flat tuple format with named fields for clarity
    and type safety.
    """

    entity_id: str
    """Unique entity identifier, e.g. 'huawei_soc'."""

    name: str | None
    """Short display name (has_entity_name removes device prefix), or None."""

    platform: str
    """HA platform: 'sensor' or 'binary_sensor'."""

    unit: str | None
    """Unit of measurement, e.g. '%', 'W', or None."""

    device_class: str | None
    """HA device class: 'battery', 'power', 'enum', etc."""

    state_class: str | None
    """HA state class: 'measurement', 'total_increasing', or None."""

    entity_category: str | None
    """HA entity category: None, 'diagnostic', or 'config'."""

    value_key: str
    """Field name in CoordinatorState or extra_fields dict."""

    device_group: str
    """Device grouping: 'huawei', 'victron', or 'system'."""


SENSOR_ENTITIES: list[EntityDefinition] = [
    # --- Huawei device (5 entities) ---
    EntityDefinition("huawei_soc", "Battery SoC", "sensor", "%", "battery", "measurement", None, "huawei_soc_pct", "huawei"),
    EntityDefinition("huawei_setpoint", "Discharge Setpoint", "sensor", "W", "power", "measurement", None, "huawei_discharge_setpoint_w", "huawei"),
    EntityDefinition("huawei_power", "Battery Power", "sensor", "W", "power", "measurement", None, "huawei_power_w", "huawei"),
    EntityDefinition("huawei_role", "Battery Role", "sensor", None, "enum", None, "diagnostic", "huawei_role", "huawei"),
    EntityDefinition("huawei_online", "Online", "sensor", None, None, None, "diagnostic", "huawei_available", "huawei"),
    # --- Victron device (8 entities) ---
    EntityDefinition("victron_soc", "Battery SoC", "sensor", "%", "battery", "measurement", None, "victron_soc_pct", "victron"),
    EntityDefinition("victron_setpoint", "AC Setpoint", "sensor", "W", "power", "measurement", None, "victron_discharge_setpoint_w", "victron"),
    EntityDefinition("victron_power", "Battery Power", "sensor", "W", "power", "measurement", None, "victron_power_w", "victron"),
    EntityDefinition("victron_role", "Battery Role", "sensor", None, "enum", None, "diagnostic", "victron_role", "victron"),
    EntityDefinition("victron_online", "Online", "sensor", None, None, None, "diagnostic", "victron_available", "victron"),
    EntityDefinition("victron_l1_power", "L1 Power", "sensor", "W", "power", "measurement", "diagnostic", "victron_l1_power_w", "victron"),
    EntityDefinition("victron_l2_power", "L2 Power", "sensor", "W", "power", "measurement", "diagnostic", "victron_l2_power_w", "victron"),
    EntityDefinition("victron_l3_power", "L3 Power", "sensor", "W", "power", "measurement", "diagnostic", "victron_l3_power_w", "victron"),
    # --- System device (4 entities) ---
    EntityDefinition("combined_power", "Combined Power", "sensor", "W", "power", "measurement", None, "combined_power_w", "system"),
    EntityDefinition("control_state", "Control State", "sensor", None, "enum", None, "diagnostic", "control_state", "system"),
    EntityDefinition("evcc_battery_mode", "EVCC Battery Mode", "sensor", None, "enum", None, "diagnostic", "evcc_battery_mode", "system"),
    EntityDefinition("pool_status", "Pool Status", "sensor", None, "enum", None, "diagnostic", "pool_status", "system"),
]


_DEVICES: dict[str, dict[str, Any]] = {
    "huawei": {
        "identifiers": ["ems_huawei"],
        "name": "EMS Huawei",
        "manufacturer": "Huawei",
    },
    "victron": {
        "identifiers": ["ems_victron"],
        "name": "EMS Victron",
        "manufacturer": "Victron Energy",
    },
    "system": {
        "identifiers": ["ems_system"],
        "name": "EMS System",
        "manufacturer": "EMS",
    },
}


# ---------------------------------------------------------------------------


class HomeAssistantMqttClient:
    """Publishes EMS telemetry to Home Assistant via MQTT discovery.

    Parameters
    ----------
    host:
        Hostname or IP of the MQTT broker.
    port:
        TCP port (default 1883).
    username:
        Optional MQTT username.
    password:
        Optional MQTT password.
    device_id:
        Unique device identifier used in discovery topics and ``unique_id``.
    configuration_url:
        URL for the device configuration page shown in HA device info.
    """

    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        device_id: str = "ems",
        configuration_url: str = "http://homeassistant.local:8000",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._device_id = device_id
        self._configuration_url = configuration_url

        # Inspectable state flags
        self._connected: bool = False
        self._discovery_sent: bool = False

        # Event loop captured at connect() time — used by paho callbacks
        self._loop: asyncio.AbstractEventLoop | None = None

        # paho client (created here so it is inspectable before connect)
        self._client: mqtt.Client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"ems-ha-client-{device_id}",
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        if username:
            self._client.username_pw_set(username, password)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Start the paho background thread and initiate a broker connection.

        Never raises — if the broker is unreachable the exception is caught,
        logged at WARNING, and ``_connected`` stays ``False``.
        """
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        try:
            self._client.will_set(
                _AVAILABILITY_TOPIC, "offline", qos=1, retain=True,
            )
            self._client.connect(self._host, self._port)
        except (ConnectionRefusedError, OSError) as exc:
            logger.warning("HA MQTT connect failed: %s", exc)
            return
        self._client.loop_start()

    async def disconnect(self) -> None:
        """Stop the paho network thread and disconnect from the broker."""
        self._client.publish(
            _AVAILABILITY_TOPIC, "offline", qos=1, retain=True,
        )
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False
        self._discovery_sent = False
        logger.debug("HA MQTT disconnected from %s:%d", self._host, self._port)

    async def publish(
        self,
        state: Any,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        """Publish a telemetry snapshot to Home Assistant.

        Accepts both ``UnifiedPoolState`` and ``CoordinatorState`` (any
        dataclass).  Silently skips if not connected.  On the first successful
        call after connect, discovery payloads are published with
        ``retain=True`` before the state payload.

        Args:
            state: A dataclass snapshot (UnifiedPoolState or CoordinatorState).
            extra_fields: Optional dict merged into the JSON payload (e.g.
                per-phase Victron power from the controller snapshot).
        """
        if not self._connected:
            return
        try:
            self._ensure_discovery()
            self._publish_state(state, extra_fields=extra_fields)
        except Exception as exc:
            logger.error("HA MQTT publish failed: %s", exc)

    # ------------------------------------------------------------------
    # Topic helpers
    # ------------------------------------------------------------------

    def _discovery_topic(self, entity: EntityDefinition) -> str:
        return f"homeassistant/{entity.platform}/{self._device_id}/{entity.entity_id}/config"

    def _state_topic(self) -> str:
        return f"homeassistant/sensor/{self._device_id}/state"

    # ------------------------------------------------------------------
    # Payload helpers
    # ------------------------------------------------------------------

    def _discovery_payload(self, entity: EntityDefinition) -> str:
        """Build a JSON discovery payload for a single entity."""
        device_info = dict(_DEVICES[entity.device_group])
        device_info["configuration_url"] = self._configuration_url

        payload: dict[str, Any] = {
            "name": entity.name,
            "unique_id": f"{self._device_id}_{entity.entity_id}",
            "state_topic": self._state_topic(),
            "value_template": f"{{{{ value_json.{entity.value_key} }}}}",
            "has_entity_name": True,
            "origin": {"name": "EMS", "sw": _EMS_VERSION},
            "availability": [
                {
                    "topic": _AVAILABILITY_TOPIC,
                    "payload_available": "online",
                    "payload_not_available": "offline",
                }
            ],
            "device": device_info,
        }

        if entity.platform == "sensor":
            payload["expire_after"] = 120

        if entity.unit is not None:
            payload["unit_of_measurement"] = entity.unit
        if entity.device_class is not None:
            payload["device_class"] = entity.device_class
        if entity.state_class is not None:
            payload["state_class"] = entity.state_class
        if entity.entity_category is not None:
            payload["entity_category"] = entity.entity_category

        return json.dumps(payload)

    # ------------------------------------------------------------------
    # Internal publish helpers (called from asyncio thread; paho.publish is
    # thread-safe so calling from asyncio is fine)
    # ------------------------------------------------------------------

    def _ensure_discovery(self) -> None:
        """Publish discovery config for all entities if not already done."""
        if self._discovery_sent:
            return
        for entity in SENSOR_ENTITIES:
            topic = self._discovery_topic(entity)
            payload = self._discovery_payload(entity)
            self._client.publish(topic, payload, retain=True)
        self._discovery_sent = True
        logger.info("HA MQTT discovery published")

    def _publish_state(
        self,
        state: Any,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        """Serialize ``state`` to JSON and publish to the state topic.

        Args:
            state: A dataclass snapshot (UnifiedPoolState or CoordinatorState).
            extra_fields: Optional dict merged into the payload for fields not
                present in the dataclass (e.g. per-phase Victron power).
        """
        # control_state is a StrEnum — asdict gives its string value directly
        raw = dataclasses.asdict(state)
        if extra_fields:
            raw.update(extra_fields)
        payload = json.dumps(raw)
        self._client.publish(self._state_topic(), payload)

    # ------------------------------------------------------------------
    # paho callbacks  (run in paho's background thread)
    # ------------------------------------------------------------------

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,  # noqa: ARG002
        connect_flags: Any,  # noqa: ARG002
        reason_code: Any,
        properties: Any,  # noqa: ARG002
    ) -> None:
        if reason_code != 0:
            logger.warning("HA MQTT connect rejected: rc=%s", reason_code)
            return
        # Publish online status
        client.publish(_AVAILABILITY_TOPIC, "online", qos=1, retain=True)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._set_connected, True)
        logger.info("HA MQTT connected")

    def _on_disconnect(
        self,
        client: mqtt.Client,  # noqa: ARG002
        userdata: Any,  # noqa: ARG002
        disconnect_flags: Any,  # noqa: ARG002
        reason_code: Any,
        properties: Any,  # noqa: ARG002
    ) -> None:
        if reason_code != 0:
            logger.warning("HA MQTT disconnected unexpectedly")
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._set_connected, False)

    # ------------------------------------------------------------------
    # State mutators  (called in the asyncio event loop thread)
    # ------------------------------------------------------------------

    def _set_connected(self, value: bool) -> None:
        self._connected = value
        if not value:
            # Reset discovery so it re-runs after reconnect
            self._discovery_sent = False
