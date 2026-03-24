"""Venus OS MQTT subscriber for DESS schedule data.

Connects to the Venus OS MQTT broker (FlashMQ on v3.20+) and subscribes to
the ``DynamicEss`` settings topics to read the DESS charge/discharge schedule.

Threading model
---------------
Mirrors :class:`~backend.evcc_mqtt_driver.EvccMqttDriver` exactly: paho-mqtt
callbacks run in paho's background thread; state mutations cross to the asyncio
event loop via ``loop.call_soon_threadsafe()``.

Availability model
------------------
``dess_available`` starts ``False`` and becomes ``True`` on successful CONNACK.
It reverts to ``False`` on disconnect or connect failure.

Topic format
------------
``N/{portalId}/settings/0/Settings/DynamicEss/Schedule/{slot_idx}/{field}``
``N/{portalId}/settings/0/Settings/DynamicEss/Mode``

Payload is JSON ``{"value": ...}``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import paho.mqtt.client as mqtt

from backend.dess_models import DessSchedule, DessScheduleSlot

logger = logging.getLogger(__name__)

# DESS schedule topic field names
_SCHEDULE_FIELDS = {"Soc", "Start", "Duration", "Strategy"}


class DessMqttSubscriber:
    """Venus OS MQTT subscriber for DESS schedule data.

    Parameters
    ----------
    host:
        IP or hostname of the Venus OS MQTT broker.
    port:
        TCP port (default 1883).
    portal_id:
        Venus OS portal ID (e.g. ``e0ff50a097c0``).
    """

    def __init__(
        self, host: str, port: int = 1883, portal_id: str = ""
    ) -> None:
        self.host = host
        self.port = port
        self._portal_id = portal_id

        # --- Public state ---
        self.schedule = DessSchedule()
        self.dess_available: bool = False

        # --- Internal ---
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: mqtt.Client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="ems-dess-subscriber",
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Start the paho background thread and connect to the broker.

        Never raises -- if the broker is unreachable the exception is caught,
        logged at WARNING, and ``dess_available`` stays ``False``.
        """
        self._loop = asyncio.get_running_loop()
        try:
            self._client.connect(self.host, self.port)
        except (ConnectionRefusedError, OSError) as exc:
            logger.warning("DESS MQTT connect failed: %s", exc)
            self.dess_available = False
            return
        self._client.loop_start()

    def disconnect(self) -> None:
        """Stop the paho network thread and disconnect from the broker."""
        self._client.loop_stop()
        self._client.disconnect()
        logger.debug(
            "DESS MQTT disconnected from %s:%d", self.host, self.port
        )

    # ------------------------------------------------------------------
    # paho callbacks (run in paho's background thread)
    # ------------------------------------------------------------------

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,  # noqa: ARG002
        connect_flags: Any,  # noqa: ARG002
        reason_code: Any,
        properties: Any,  # noqa: ARG002
    ) -> None:
        """Subscribe to DESS topics on successful CONNACK."""
        if reason_code != 0:
            logger.warning("DESS MQTT connect rejected: rc=%s", reason_code)
            return

        topic = f"N/{self._portal_id}/settings/0/Settings/DynamicEss/#"
        client.subscribe(topic)

        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._set_available, True)

        logger.info("DESS MQTT connected to %s:%d", self.host, self.port)

    def _on_disconnect(
        self,
        client: mqtt.Client,  # noqa: ARG002
        userdata: Any,  # noqa: ARG002
        disconnect_flags: Any,  # noqa: ARG002
        reason_code: Any,
        properties: Any,  # noqa: ARG002
    ) -> None:
        """Mark unavailable on disconnect."""
        if reason_code != 0:
            logger.warning("DESS MQTT disconnected unexpectedly")

        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._set_available, False)

    def _on_message(
        self,
        client: mqtt.Client,  # noqa: ARG002
        userdata: Any,  # noqa: ARG002
        message: Any,
    ) -> None:
        """Parse DESS schedule/mode topics from Venus OS MQTT.

        Topic patterns:
          ``.../DynamicEss/Schedule/{slot_idx}/{field}``
          ``.../DynamicEss/Mode``
        """
        topic: str = message.topic
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            value = payload.get("value")
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            return

        if value is None:
            return

        # Extract the path after DynamicEss/
        prefix = f"N/{self._portal_id}/settings/0/Settings/DynamicEss/"
        if not topic.startswith(prefix):
            return

        suffix = topic[len(prefix):]

        if suffix == "Mode":
            self._update_mode(int(value))
            return

        # Schedule/{slot_idx}/{field}
        parts = suffix.split("/")
        if len(parts) == 3 and parts[0] == "Schedule":
            try:
                slot_idx = int(parts[1])
            except (ValueError, TypeError):
                return
            if 0 <= slot_idx <= 3:
                field_name = parts[2]
                self._update_slot(slot_idx, field_name, value)

    # ------------------------------------------------------------------
    # State mutators (called via call_soon_threadsafe or directly)
    # ------------------------------------------------------------------

    def _set_available(self, value: bool) -> None:
        self.dess_available = value

    def _update_mode(self, value: int) -> None:
        self.schedule.mode = value
        self.schedule.last_update = time.time()

    def _update_slot(self, idx: int, field_name: str, value: Any) -> None:
        slot = self.schedule.slots[idx]
        if field_name == "Soc":
            slot.soc_pct = float(value)
        elif field_name == "Start":
            slot.start_s = int(value)
        elif field_name == "Duration":
            slot.duration_s = int(value)
        elif field_name == "Strategy":
            slot.strategy = int(value)
        else:
            logger.debug("DESS: unknown schedule field %r", field_name)
            return
        self.schedule.last_update = time.time()

    # ------------------------------------------------------------------
    # Active slot helper
    # ------------------------------------------------------------------

    def get_active_slot(
        self, now_seconds_from_midnight: int
    ) -> DessScheduleSlot | None:
        """Return the schedule slot whose time window contains *now*, or None.

        Returns ``None`` when ``schedule.mode == 0`` (DESS off) -- stale
        slot data is not treated as active.
        """
        if self.schedule.mode < 1:
            return None

        for slot in self.schedule.slots:
            end_s = slot.start_s + slot.duration_s
            if slot.duration_s > 0 and slot.start_s <= now_seconds_from_midnight < end_s:
                return slot

        return None
