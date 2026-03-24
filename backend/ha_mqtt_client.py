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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
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
    """Typed definition for a single HA MQTT entity.

    Supports sensor, binary_sensor, number, select, and button platforms.
    Optional fields (command_topic, min_val, etc.) default to None so
    existing sensor/binary_sensor definitions remain unchanged.
    """

    entity_id: str
    """Unique entity identifier, e.g. 'huawei_soc'."""

    name: str | None
    """Short display name (has_entity_name removes device prefix), or None."""

    platform: str
    """HA platform: 'sensor', 'binary_sensor', 'number', 'select', 'button'."""

    unit: str | None
    """Unit of measurement, e.g. '%', 'W', or None."""

    device_class: str | None
    """HA device class: 'battery', 'power', 'enum', 'restart', etc."""

    state_class: str | None
    """HA state class: 'measurement', 'total_increasing', or None."""

    entity_category: str | None
    """HA entity category: None, 'diagnostic', or 'config'."""

    value_key: str
    """Field name in CoordinatorState or extra_fields dict."""

    device_group: str
    """Device grouping: 'huawei', 'victron', or 'system'."""

    # --- Controllable entity fields (optional, default None) ---

    command_topic: str | None = None
    """MQTT topic for incoming commands (number/select/button)."""

    min_val: float | None = None
    """Minimum value for number entities."""

    max_val: float | None = None
    """Maximum value for number entities."""

    step: float | None = None
    """Step increment for number entities."""

    mode: str | None = None
    """Input mode for number entities: 'auto', 'slider', or 'box'."""

    options: list[str] | None = field(default=None)
    """Options list for select entities."""

    payload_press: str | None = None
    """Payload sent when a button entity is pressed."""


SENSOR_ENTITIES: list[EntityDefinition] = [
    # --- Huawei device (4 sensor entities) ---
    EntityDefinition("huawei_soc", "Battery SoC", "sensor", "%", "battery", "measurement", None, "huawei_soc_pct", "huawei"),
    EntityDefinition("huawei_setpoint", "Discharge Setpoint", "sensor", "W", "power", "measurement", None, "huawei_discharge_setpoint_w", "huawei"),
    EntityDefinition("huawei_power", "Battery Power", "sensor", "W", "power", "measurement", None, "huawei_power_w", "huawei"),
    EntityDefinition("huawei_role", "Battery Role", "sensor", None, "enum", None, "diagnostic", "huawei_role", "huawei"),
    EntityDefinition("huawei_working_mode", "Working Mode", "sensor", None, "enum", None, "diagnostic", "huawei_working_mode", "huawei"),
    # --- Victron device (7 sensor entities) ---
    EntityDefinition("victron_soc", "Battery SoC", "sensor", "%", "battery", "measurement", None, "victron_soc_pct", "victron"),
    EntityDefinition("victron_setpoint", "AC Setpoint", "sensor", "W", "power", "measurement", None, "victron_discharge_setpoint_w", "victron"),
    EntityDefinition("victron_power", "Battery Power", "sensor", "W", "power", "measurement", None, "victron_power_w", "victron"),
    EntityDefinition("victron_role", "Battery Role", "sensor", None, "enum", None, "diagnostic", "victron_role", "victron"),
    EntityDefinition("victron_l1_power", "L1 Power", "sensor", "W", "power", "measurement", "diagnostic", "victron_l1_power_w", "victron"),
    EntityDefinition("victron_l2_power", "L2 Power", "sensor", "W", "power", "measurement", "diagnostic", "victron_l2_power_w", "victron"),
    EntityDefinition("victron_l3_power", "L3 Power", "sensor", "W", "power", "measurement", "diagnostic", "victron_l3_power_w", "victron"),
    # --- System device (4 sensor entities) ---
    EntityDefinition("combined_power", "Combined Power", "sensor", "W", "power", "measurement", None, "combined_power_w", "system"),
    EntityDefinition("control_state", "Control State", "sensor", None, "enum", None, "diagnostic", "control_state", "system"),
    EntityDefinition("evcc_battery_mode", "EVCC Battery Mode", "sensor", None, "enum", None, "diagnostic", "evcc_battery_mode", "system"),
    EntityDefinition("pool_status", "Pool Status", "sensor", None, "enum", None, "diagnostic", "pool_status", "system"),
]

BINARY_SENSOR_ENTITIES: list[EntityDefinition] = [
    EntityDefinition(
        entity_id="huawei_online",
        name=None,
        platform="binary_sensor",
        unit=None,
        device_class="connectivity",
        state_class=None,
        entity_category="diagnostic",
        value_key="huawei_available",
        device_group="huawei",
    ),
    EntityDefinition(
        entity_id="victron_online",
        name=None,
        platform="binary_sensor",
        unit=None,
        device_class="connectivity",
        state_class=None,
        entity_category="diagnostic",
        value_key="victron_available",
        device_group="victron",
    ),
    EntityDefinition(
        entity_id="grid_charge_active",
        name="Grid Charge Active",
        platform="binary_sensor",
        unit=None,
        device_class="running",
        state_class=None,
        entity_category="diagnostic",
        value_key="grid_charge_slot_active",
        device_group="system",
    ),
    EntityDefinition(
        entity_id="export_active",
        name="Export Active",
        platform="binary_sensor",
        unit=None,
        device_class="running",
        state_class=None,
        entity_category="diagnostic",
        value_key="export_active",
        device_group="system",
    ),
]

NUMBER_ENTITIES: list[EntityDefinition] = [
    EntityDefinition(
        entity_id="min_soc_huawei", name="Min SoC", platform="number",
        unit="%", device_class=None, state_class=None, entity_category="config",
        value_key="huawei_min_soc_pct", device_group="huawei",
        command_topic="homeassistant/number/ems/min_soc_huawei/set",
        min_val=10, max_val=100, step=5, mode="slider",
    ),
    EntityDefinition(
        entity_id="min_soc_victron", name="Min SoC", platform="number",
        unit="%", device_class=None, state_class=None, entity_category="config",
        value_key="victron_min_soc_pct", device_group="victron",
        command_topic="homeassistant/number/ems/min_soc_victron/set",
        min_val=10, max_val=100, step=5, mode="slider",
    ),
    EntityDefinition(
        entity_id="deadband_huawei", name="Deadband", platform="number",
        unit="W", device_class=None, state_class=None, entity_category="config",
        value_key="huawei_deadband_w", device_group="huawei",
        command_topic="homeassistant/number/ems/deadband_huawei/set",
        min_val=50, max_val=1000, step=50, mode="box",
    ),
    EntityDefinition(
        entity_id="deadband_victron", name="Deadband", platform="number",
        unit="W", device_class=None, state_class=None, entity_category="config",
        value_key="victron_deadband_w", device_group="victron",
        command_topic="homeassistant/number/ems/deadband_victron/set",
        min_val=50, max_val=500, step=50, mode="box",
    ),
    EntityDefinition(
        entity_id="ramp_rate", name="Ramp Rate", platform="number",
        unit="W", device_class=None, state_class=None, entity_category="config",
        value_key="ramp_rate_w", device_group="system",
        command_topic="homeassistant/number/ems/ramp_rate/set",
        min_val=100, max_val=2000, step=100, mode="box",
    ),
]

SELECT_ENTITIES: list[EntityDefinition] = [
    EntityDefinition(
        entity_id="control_mode", name="Control Mode", platform="select",
        unit=None, device_class=None, state_class=None, entity_category="config",
        value_key="control_mode_override", device_group="system",
        command_topic="homeassistant/select/ems/control_mode/set",
        options=["AUTO", "HOLD", "GRID_CHARGE", "DISCHARGE_LOCKED"],
    ),
]

BUTTON_ENTITIES: list[EntityDefinition] = [
    EntityDefinition(
        entity_id="force_grid_charge", name="Force Grid Charge", platform="button",
        unit=None, device_class=None, state_class=None, entity_category=None,
        value_key="force_grid_charge", device_group="system",
        command_topic="homeassistant/button/ems/force_grid_charge/set",
        payload_press="PRESS",
    ),
    EntityDefinition(
        entity_id="reset_to_auto", name="Reset to Auto", platform="button",
        unit=None, device_class="restart", state_class=None, entity_category=None,
        value_key="reset_to_auto", device_group="system",
        command_topic="homeassistant/button/ems/reset_to_auto/set",
        payload_press="PRESS",
    ),
]

# Entities that were migrated from sensor to binary_sensor platform.
# Their old sensor discovery topics must be cleared with empty retained payloads
# to prevent ghost entities in Home Assistant.
_MIGRATED_TO_BINARY: list[str] = ["huawei_online", "victron_online"]


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
        self._migration_done: bool = False

        # Event loop captured at connect() time — used by paho callbacks
        self._loop: asyncio.AbstractEventLoop | None = None

        # Command callback for incoming MQTT commands (set by orchestrator)
        self._command_callback: Callable[[str, str], None] | None = None

        # Health check: timestamp of last successful publish
        self._last_publish_time: float = 0.0

        # paho client (created here so it is inspectable before connect)
        self._client: mqtt.Client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"ems-ha-client-{device_id}",
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

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

    def set_command_callback(self, callback: Callable[[str, str], None]) -> None:
        """Register a callback for incoming MQTT commands.

        The callback receives (entity_id, payload_str) and is invoked in
        the asyncio event loop thread via ``call_soon_threadsafe()``.
        """
        self._command_callback = callback

    def check_health(self, max_stale_s: float = 60.0) -> bool:
        """Check MQTT health: connected and publishing recently.

        Returns ``False`` if not connected or if no publish has occurred
        within ``max_stale_s`` seconds.  On stale detection, forces a
        reconnect attempt to recover from silent paho thread crashes.
        """
        if not self._connected:
            return False
        if self._last_publish_time > 0 and (
            time.monotonic() - self._last_publish_time
        ) > max_stale_s:
            logger.warning(
                "HA MQTT health check failed: no publish in %ds, forcing reconnect",
                max_stale_s,
            )
            try:
                self._client.reconnect()
            except Exception:
                pass
            return False
        return True

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

        # Buttons are stateless -- no state_topic or value_template
        if entity.platform != "button":
            payload["state_topic"] = self._state_topic()
            payload["value_template"] = f"{{{{ value_json.{entity.value_key} }}}}"

        if entity.platform == "sensor":
            payload["expire_after"] = 120
        elif entity.platform == "binary_sensor":
            payload["payload_on"] = "True"
            payload["payload_off"] = "False"
        elif entity.platform == "number":
            payload["command_topic"] = entity.command_topic
            if entity.min_val is not None:
                payload["min"] = entity.min_val
            if entity.max_val is not None:
                payload["max"] = entity.max_val
            if entity.step is not None:
                payload["step"] = entity.step
            if entity.mode is not None:
                payload["mode"] = entity.mode
        elif entity.platform == "select":
            payload["command_topic"] = entity.command_topic
            if entity.options is not None:
                payload["options"] = entity.options
        elif entity.platform == "button":
            payload["command_topic"] = entity.command_topic
            if entity.payload_press is not None:
                payload["payload_press"] = entity.payload_press

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
        self._cleanup_old_sensor_topics()
        all_entities = (
            list(SENSOR_ENTITIES)
            + list(BINARY_SENSOR_ENTITIES)
            + list(NUMBER_ENTITIES)
            + list(SELECT_ENTITIES)
            + list(BUTTON_ENTITIES)
        )
        for entity in all_entities:
            topic = self._discovery_topic(entity)
            payload = self._discovery_payload(entity)
            self._client.publish(topic, payload, retain=True)
        self._discovery_sent = True
        logger.info("HA MQTT discovery published")

    def _cleanup_old_sensor_topics(self) -> None:
        """One-time migration: clear old sensor topics for entities moved to binary_sensor."""
        if self._migration_done:
            return
        for entity_id in _MIGRATED_TO_BINARY:
            old_topic = f"homeassistant/sensor/{self._device_id}/{entity_id}/config"
            self._client.publish(old_topic, "", retain=True)
        self._migration_done = True
        logger.info("HA MQTT: cleaned up old sensor topics for binary_sensor migration")

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
        self._last_publish_time = time.monotonic()

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

        # Subscribe to command topics for controllable entities
        all_controllable = list(NUMBER_ENTITIES) + list(SELECT_ENTITIES) + list(BUTTON_ENTITIES)
        for entity in all_controllable:
            if entity.command_topic:
                try:
                    client.subscribe(entity.command_topic, qos=1)
                except (BrokenPipeError, OSError) as exc:
                    logger.warning(
                        "HA MQTT subscribe failed for %s: %s",
                        entity.command_topic, exc,
                    )

        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._set_connected, True)
        logger.info("HA MQTT connected")

    def _on_message(
        self,
        client: mqtt.Client,  # noqa: ARG002
        userdata: Any,  # noqa: ARG002
        msg: Any,
    ) -> None:
        """Dispatch incoming MQTT command to the registered callback.

        Parses entity_id from topic (e.g. ``homeassistant/number/ems/min_soc_huawei/set``
        -> ``min_soc_huawei``), decodes payload as UTF-8, and dispatches via
        ``call_soon_threadsafe`` to cross the paho->asyncio thread boundary.
        """
        # Parse entity_id from topic: .../ems/{entity_id}/set
        parts = msg.topic.split("/")
        if len(parts) >= 5:
            entity_id = parts[-2]  # second-to-last segment
        else:
            logger.debug("HA MQTT: unexpected topic format: %s", msg.topic)
            return

        payload_str = msg.payload.decode("utf-8") if isinstance(msg.payload, bytes) else str(msg.payload)

        if self._command_callback is None:
            logger.debug("HA MQTT: no command callback set, ignoring %s", entity_id)
            return

        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._command_callback, entity_id, payload_str)

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
