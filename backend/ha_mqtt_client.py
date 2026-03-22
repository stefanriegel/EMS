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
from typing import TYPE_CHECKING, Any

import paho.mqtt.client as mqtt

if TYPE_CHECKING:
    from backend.unified_model import UnifiedPoolState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Entity definitions
# (entity_id, friendly_name, unit, device_class, state_class, value_key)
# value_key must match a field name in UnifiedPoolState
# ---------------------------------------------------------------------------

_ENTITIES: list[tuple[str, str, str | None, str | None, str | None, str]] = [
    # --- Existing 7 entities (unchanged) ---
    ("huawei_soc",        "Huawei Battery SOC",        "%",  "battery", "measurement", "huawei_soc_pct"),
    ("victron_soc",       "Victron Battery SOC",        "%",  "battery", "measurement", "victron_soc_pct"),
    ("huawei_setpoint",   "Huawei Discharge Setpoint",  "W",  "power",   "measurement", "huawei_discharge_setpoint_w"),
    ("victron_setpoint",  "Victron AC Setpoint",        "W",  "power",   "measurement", "victron_discharge_setpoint_w"),
    ("combined_power",    "Combined Battery Power",     "W",  "power",   "measurement", "combined_power_w"),
    ("control_state",     "EMS Control State",          None, None,      None,          "control_state"),
    ("evcc_battery_mode", "EVCC Battery Mode",          None, None,      None,          "evcc_battery_mode"),
    # --- New per-system entities (D-26 through D-31) ---
    ("huawei_role",       "Huawei Battery Role",        None, None,      None,          "huawei_role"),
    ("victron_role",      "Victron Battery Role",       None, None,      None,          "victron_role"),
    ("huawei_power",      "Huawei Battery Power",       "W",  "power",   "measurement", "huawei_power_w"),
    ("victron_power",     "Victron Battery Power",      "W",  "power",   "measurement", "victron_power_w"),
    ("huawei_online",     "Huawei Online",              None, None,      None,          "huawei_available"),
    ("victron_online",    "Victron Online",             None, None,      None,          "victron_available"),
    ("pool_status",       "EMS Pool Status",            None, None,      None,          "pool_status"),
    # Per-phase power populated by coordinator before publish
    ("victron_l1_power",  "Victron L1 Power",           "W",  "power",   "measurement", "victron_l1_power_w"),
    ("victron_l2_power",  "Victron L2 Power",           "W",  "power",   "measurement", "victron_l2_power_w"),
    ("victron_l3_power",  "Victron L3 Power",           "W",  "power",   "measurement", "victron_l3_power_w"),
]


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
    device_name:
        Human-readable device name shown in the HA device registry.
    """

    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        device_id: str = "ems",
        device_name: str = "Energy Management System",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._device_id = device_id
        self._device_name = device_name

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
        self._loop = asyncio.get_event_loop()
        try:
            self._client.connect(self._host, self._port)
        except (ConnectionRefusedError, OSError) as exc:
            logger.warning("HA MQTT connect failed: %s", exc)
            return
        self._client.loop_start()

    async def disconnect(self) -> None:
        """Stop the paho network thread and disconnect from the broker."""
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

    def _discovery_topic(self, entity_id: str) -> str:
        return f"homeassistant/sensor/{self._device_id}/{entity_id}/config"

    def _state_topic(self) -> str:
        return f"homeassistant/sensor/{self._device_id}/state"

    # ------------------------------------------------------------------
    # Payload helpers
    # ------------------------------------------------------------------

    def _discovery_payload(
        self,
        entity_id: str,
        name: str,
        unit: str | None,
        device_class: str | None,
        state_class: str | None,
        value_key: str,
    ) -> str:
        payload: dict[str, Any] = {
            "name": name,
            "unique_id": f"{self._device_id}_{entity_id}",
            "state_topic": self._state_topic(),
            "value_template": f"{{{{ value_json.{value_key} }}}}",
            "device": {
                "identifiers": [self._device_id],
                "name": self._device_name,
                "manufacturer": "EMS",
            },
        }
        if unit is not None:
            payload["unit_of_measurement"] = unit
        if device_class is not None:
            payload["device_class"] = device_class
        if state_class is not None:
            payload["state_class"] = state_class
        return json.dumps(payload)

    # ------------------------------------------------------------------
    # Internal publish helpers (called from asyncio thread; paho.publish is
    # thread-safe so calling from asyncio is fine)
    # ------------------------------------------------------------------

    def _ensure_discovery(self) -> None:
        """Publish discovery config for all entities if not already done."""
        if self._discovery_sent:
            return
        for entity_id, name, unit, device_class, state_class, value_key in _ENTITIES:
            topic = self._discovery_topic(entity_id)
            payload = self._discovery_payload(
                entity_id, name, unit, device_class, state_class, value_key
            )
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
        client: mqtt.Client,  # noqa: ARG002
        userdata: Any,  # noqa: ARG002
        connect_flags: Any,  # noqa: ARG002
        reason_code: Any,
        properties: Any,  # noqa: ARG002
    ) -> None:
        if reason_code != 0:
            logger.warning("HA MQTT connect rejected: rc=%s", reason_code)
            return
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
