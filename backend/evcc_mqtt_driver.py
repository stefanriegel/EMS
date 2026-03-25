"""Async EVCC MQTT driver.

Subscribes to ``evcc/site/batteryMode`` and ``evcc/loadpoints/1/#``, maintains
``evcc_battery_mode`` and ``evcc_loadpoint_state`` attributes, and bridges
paho callbacks into the asyncio event loop via ``loop.call_soon_threadsafe()``.

Threading model
---------------
paho-mqtt callbacks run in paho's own background thread (started by
``loop_start()``).  The asyncio event loop runs in the main thread.
All state mutations triggered by MQTT callbacks cross the boundary via
``loop.call_soon_threadsafe()``.  Never call asyncio primitives directly
from paho callbacks.

Availability model
------------------
``evcc_available`` starts ``False`` and only becomes ``True`` inside the
``_on_connect`` callback (which paho calls from its thread after a successful
TCP handshake + CONNACK).  If the broker is unreachable, ``connect()`` catches
the exception, logs a WARNING, and returns normally — EMS startup is never
blocked by EVCC unavailability.

Logging
-------
The module logger is ``backend.evcc_mqtt_driver``.  Grep for ``"EVCC MQTT"``
to find all relevant log lines.  Key lines:

* ``INFO  "EVCC MQTT connected to <host>:<port>"``  — on successful CONNACK
* ``WARNING "EVCC MQTT connect failed: <exc>"``      — on OSError / refused
* ``WARNING "EVCC MQTT disconnected unexpectedly"``  — on rc != 0 disconnect
* ``DEBUG "evcc/site/batteryMode → <value>"``        — on each mode update
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import paho.mqtt.client as mqtt

from backend.evcc_models import EvccLoadpointState

logger = logging.getLogger(__name__)

# MQTT topics
_TOPIC_BATTERY_MODE = "evcc/site/batteryMode"
_TOPIC_LOADPOINTS_PREFIX = "evcc/loadpoints/1/"
_TOPIC_LOADPOINTS_WILDCARD = "evcc/loadpoints/1/#"

# Subtopic → EvccLoadpointState field mapping
_SUBTOPIC_FIELD_MAP: dict[str, str] = {
    "mode": "mode",
    "chargePower": "charge_power_w",
    "vehicleSoc": "vehicle_soc_pct",
    "charging": "charging",
    "connected": "connected",
}


class EvccMqttDriver:
    """MQTT client for the EVCC energy management system.

    Maintains ``evcc_battery_mode`` and ``evcc_loadpoint_state`` attributes
    updated live from the EVCC MQTT broker.  All state mutations happen in the
    asyncio event loop thread via ``loop.call_soon_threadsafe()``.

    Parameters
    ----------
    host:
        IP address or hostname of the EVCC MQTT broker.
    port:
        TCP port (default 1883).
    """

    def __init__(
        self, host: str, port: int = 1883, username: str = "", password: str = ""
    ) -> None:
        self.host = host
        self.port = port

        # --- Public state attributes ---
        self.evcc_battery_mode: str = "normal"
        self.evcc_loadpoint_state: EvccLoadpointState = EvccLoadpointState()
        self.evcc_available: bool = False

        # --- Internal ---
        # Event loop captured at connect() time — used by paho callbacks
        self._loop: asyncio.AbstractEventLoop | None = None

        # paho MQTT client (created in __init__ so it's inspectable before connect)
        self._client: mqtt.Client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="ems-evcc-driver",
        )
        if username:
            self._client.username_pw_set(username, password)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Start the paho background thread and request a broker connection.

        Never raises — if the broker is unreachable the exception is caught,
        logged at WARNING, and ``evcc_available`` stays ``False``.  EMS
        startup is never blocked by EVCC unavailability.

        The successful connection path is: paho's ``loop_start()`` initiates
        the TCP connect in the background; ``_on_connect`` is called by paho
        on CONNACK success, at which point ``evcc_available`` becomes ``True``
        and topics are subscribed.
        """
        self._loop = asyncio.get_event_loop()

        try:
            # Non-blocking paho connect — returns immediately; actual TCP
            # handshake happens in the paho background thread started below.
            self._client.connect(self.host, self.port)
        except (ConnectionRefusedError, OSError) as exc:
            logger.warning("EVCC MQTT connect failed: %s", exc)
            self.evcc_available = False
            return

        # Start paho's background network thread
        self._client.loop_start()

    async def close(self) -> None:
        """Stop the paho network thread and disconnect from the broker."""
        self._client.loop_stop()
        self._client.disconnect()
        logger.debug("EVCC MQTT disconnected from %s:%d", self.host, self.port)

    # ------------------------------------------------------------------
    # Async context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "EvccMqttDriver":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

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
        """Called by paho after a successful CONNACK.

        Runs in paho's background thread — must not touch asyncio state
        directly.  Subscription calls via ``client.subscribe()`` are
        thread-safe within paho.
        """
        if reason_code != 0:
            logger.warning(
                "EVCC MQTT connect rejected: rc=%s", reason_code
            )
            return

        # Thread-safe: paho client methods are safe to call from callbacks
        client.subscribe(_TOPIC_BATTERY_MODE)
        client.subscribe(_TOPIC_LOADPOINTS_WILDCARD)

        # Cross the thread boundary to set evcc_available in the asyncio thread
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._set_available, True)

        logger.info("EVCC MQTT connected to %s:%d", self.host, self.port)

    def _on_disconnect(
        self,
        client: mqtt.Client,  # noqa: ARG002
        userdata: Any,  # noqa: ARG002
        disconnect_flags: Any,  # noqa: ARG002
        reason_code: Any,
        properties: Any,  # noqa: ARG002
    ) -> None:
        """Called by paho on disconnect.

        Runs in paho's background thread.
        """
        if reason_code != 0:
            logger.warning("EVCC MQTT disconnected unexpectedly")

        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._set_available, False)

    def _on_message(
        self,
        client: mqtt.Client,  # noqa: ARG002
        userdata: Any,  # noqa: ARG002
        message: mqtt.MQTTMessage,
    ) -> None:
        """Called by paho for every incoming MQTT message.

        Runs in paho's background thread — all state mutations are deferred to
        the asyncio event loop thread via ``call_soon_threadsafe()``.
        """
        assert self._loop is not None
        topic: str = message.topic
        try:
            payload: str = message.payload.decode("utf-8").strip()
        except (UnicodeDecodeError, AttributeError):
            return

        if topic == _TOPIC_BATTERY_MODE:
            logger.debug("evcc/site/batteryMode → %s", payload)
            self._loop.call_soon_threadsafe(self._update_battery_mode, payload)
        elif topic.startswith(_TOPIC_LOADPOINTS_PREFIX):
            subtopic = topic[len(_TOPIC_LOADPOINTS_PREFIX):]
            self._loop.call_soon_threadsafe(self._update_loadpoint, subtopic, payload)

    # ------------------------------------------------------------------
    # State mutators  (run in the asyncio event loop thread)
    # ------------------------------------------------------------------

    def _set_available(self, value: bool) -> None:
        """Set ``evcc_available`` — called in the asyncio thread."""
        self.evcc_available = value

    def _update_battery_mode(self, value: str) -> None:
        """Update ``evcc_battery_mode`` — called in the asyncio thread."""
        self.evcc_battery_mode = value

    def _update_loadpoint(self, subtopic: str, value: str) -> None:
        """Update the relevant field on ``evcc_loadpoint_state``.

        Subtopic → field mapping:
            ``mode``         → ``mode``            (str)
            ``chargePower``  → ``charge_power_w``  (float, watts)
            ``vehicleSoc``   → ``vehicle_soc_pct`` (float | None, percent)
            ``charging``     → ``charging``        (bool)
            ``connected``    → ``connected``        (bool)

        Unknown subtopics are silently ignored — EVCC publishes many
        loadpoint sub-topics we don't need.
        """
        field = _SUBTOPIC_FIELD_MAP.get(subtopic)
        if field is None:
            return

        state = self.evcc_loadpoint_state
        try:
            if field == "mode":
                state.mode = value
            elif field == "charge_power_w":
                state.charge_power_w = float(value)
            elif field == "vehicle_soc_pct":
                state.vehicle_soc_pct = float(value) if value not in ("", "null") else None
            elif field == "charging":
                # EVCC publishes booleans as JSON true/false strings
                state.charging = value.lower() in ("true", "1")
            elif field == "connected":
                state.connected = value.lower() in ("true", "1")
        except (ValueError, TypeError):
            logger.debug(
                "EVCC loadpoint: could not parse subtopic=%s value=%r",
                subtopic,
                value,
            )
