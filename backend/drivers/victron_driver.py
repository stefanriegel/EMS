"""Async Victron Multiplus II 3-phase MQTT driver.

Connects to the Venus OS dbus-flashmq MQTT broker, discovers the ``portalId``
and vebus ``instanceId`` via live subscription, subscribes to all relevant
telemetry topics, and starts a periodic keepalive loop.

Usage::

    async with VictronDriver("192.168.0.10") as driver:
        state = driver.read_system_state()
        driver.write_ac_power_setpoint(1, -500.0)  # L1: 500 W export

Threading model
---------------
paho-mqtt callbacks run in paho's own background thread (started by
``loop_start()``).  The asyncio event loop runs in the main thread.
All state mutations triggered by MQTT callbacks cross the boundary via
``loop.call_soon_threadsafe()``.  Never call asyncio primitives directly
from paho callbacks.

Discovery model
---------------
dbus-flashmq (Venus OS v3.20+) does **not** retain messages.  After
subscribing, clients must send ``R/{portalId}/keepalive`` (empty payload) to
trigger the first data burst.  Discovery is a two-step process:

1. Subscribe ``N/+/system/0/Serial`` → first message yields ``portalId``.
2. Subscribe ``N/{portalId}/vebus/+/ProductId`` → first message yields
   ``instanceId``.

Both events are signalled via ``asyncio.Event`` objects.

Logging
-------
The module logger is ``backend.drivers.victron_driver``.  Set it to DEBUG to
see every MQTT publish and incoming message topic + value.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import time
from contextlib import suppress
from typing import Any

import paho.mqtt.client as mqtt

from backend.drivers.victron_models import VictronPhaseData, VictronSystemData

logger = logging.getLogger(__name__)


class VictronDriver:
    """Async context manager for the Victron Multiplus II 3-phase MQTT interface.

    Parameters
    ----------
    host:
        IP address or hostname of the Venus OS MQTT broker.
    port:
        TCP port (default 1883).
    timeout_s:
        General operation timeout in seconds (default 10.0).
    discovery_timeout_s:
        Maximum seconds to wait for portalId / instanceId discovery
        before raising ``asyncio.TimeoutError`` (default 15.0).
    """

    def __init__(
        self,
        host: str,
        port: int = 1883,
        timeout_s: float = 10.0,
        discovery_timeout_s: float = 15.0,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self.discovery_timeout_s = discovery_timeout_s

        # MQTT client (set during connect)
        self._client: mqtt.Client | None = None

        # Event loop captured at connect() for use by paho callbacks
        self._loop: asyncio.AbstractEventLoop | None = None

        # Discovered identifiers
        self._portal_id: str | None = None
        self._instance_id: str | None = None

        # Asyncio events signalled when discovery completes
        self._portal_event: asyncio.Event = asyncio.Event()
        self._instance_event: asyncio.Event = asyncio.Event()

        # Live telemetry state dict — mutated only in the asyncio thread
        self._state: dict[str, Any] = {}

        # Topic → field key map built after discovery
        self._topic_map: dict[str, str] = {}

        # Keepalive task
        self._keepalive_task: asyncio.Task | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to the broker, discover portalId + instanceId, and subscribe.

        Raises
        ------
        ConnectionError
            If the broker is not reachable at TCP level.
        asyncio.TimeoutError
            If discovery (portalId or instanceId) does not complete within
            ``discovery_timeout_s``.
        """
        # Capture the event loop before spawning background thread
        self._loop = asyncio.get_event_loop()

        # TCP pre-flight — fast failure before handing off to paho
        try:
            s = socket.create_connection((self.host, self.port), timeout=3.0)
            s.close()
        except Exception as exc:
            msg = f"{type(exc).__name__} connecting to {self.host}:{self.port}: {exc}"
            logger.error("Victron TCP pre-flight failed: %s", msg)
            raise ConnectionError(msg) from exc

        # Create paho client (paho 2.x requires explicit CallbackAPIVersion)
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_message = self._on_message

        # Non-blocking connect + background thread
        self._client.connect_async(self.host, self.port)
        self._client.loop_start()

        # --- Step 1: discover portalId ---
        self._client.subscribe("N/+/system/0/Serial")
        try:
            await asyncio.wait_for(
                self._portal_event.wait(),
                timeout=self.discovery_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Victron discovery timeout: portalId not found within %.1fs",
                self.discovery_timeout_s,
            )
            raise

        # Send initial keepalive (empty payload) — triggers the first data burst
        # from dbus-flashmq.  Must happen *before* step 2 to ensure the broker
        # starts emitting the vebus/+/ProductId message we need.
        self._client.publish(f"R/{self._portal_id}/keepalive", b"", qos=0)

        # --- Step 2: discover instanceId ---
        self._client.subscribe(f"N/{self._portal_id}/vebus/+/ProductId")
        try:
            await asyncio.wait_for(
                self._instance_event.wait(),
                timeout=self.discovery_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Victron discovery timeout: instanceId not found within %.1fs",
                self.discovery_timeout_s,
            )
            raise

        logger.info(
            "Victron MQTT connected: portalId=%s instanceId=%s",
            self._portal_id,
            self._instance_id,
        )

        # Build the topic → field key map now that both IDs are known
        self._topic_map = self._build_topic_map()

        # Subscribe to all data topics
        for topic in self._topic_map:
            self._client.subscribe(topic)

        # Activate ESS external control mode (Hub4Mode=3) so setpoints take effect
        self.write_ess_mode(3)
        logger.debug("Victron ESS mode set to 3 (external control)")

        # Start 30s keepalive loop
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def close(self) -> None:
        """Cancel the keepalive task and disconnect from the broker."""
        if self._keepalive_task is not None and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.shield(self._keepalive_task)
            self._keepalive_task = None

        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

        logger.debug(
            "Victron MQTT disconnected from %s:%d", self.host, self.port
        )

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "VictronDriver":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Topic map
    # ------------------------------------------------------------------

    def _build_topic_map(self) -> dict[str, str]:
        """Return the full MQTT topic → state field key mapping.

        Called after both ``_portal_id`` and ``_instance_id`` are known.
        Topic keys include the full ``N/{portalId}/...`` prefix so they can
        be compared directly against ``message.topic``.
        """
        p = self._portal_id
        i = self._instance_id
        return {
            # Battery
            f"N/{p}/system/0/Dc/Battery/Soc": "battery_soc_pct",
            f"N/{p}/system/0/Dc/Battery/Power": "battery_power_w",
            f"N/{p}/system/0/Dc/Battery/Current": "battery_current_a",
            f"N/{p}/system/0/Dc/Battery/Voltage": "battery_voltage_v",
            # Per-phase AC output
            f"N/{p}/vebus/{i}/Ac/Out/L1/P": "l1_power_w",
            f"N/{p}/vebus/{i}/Ac/Out/L1/I": "l1_current_a",
            f"N/{p}/vebus/{i}/Ac/Out/L1/V": "l1_voltage_v",
            f"N/{p}/vebus/{i}/Ac/Out/L2/P": "l2_power_w",
            f"N/{p}/vebus/{i}/Ac/Out/L2/I": "l2_current_a",
            f"N/{p}/vebus/{i}/Ac/Out/L2/V": "l2_voltage_v",
            f"N/{p}/vebus/{i}/Ac/Out/L3/P": "l3_power_w",
            f"N/{p}/vebus/{i}/Ac/Out/L3/I": "l3_current_a",
            f"N/{p}/vebus/{i}/Ac/Out/L3/V": "l3_voltage_v",
            # AcPowerSetpoint readbacks
            f"N/{p}/vebus/{i}/Hub4/L1/AcPowerSetpoint": "l1_setpoint_w",
            f"N/{p}/vebus/{i}/Hub4/L2/AcPowerSetpoint": "l2_setpoint_w",
            f"N/{p}/vebus/{i}/Hub4/L3/AcPowerSetpoint": "l3_setpoint_w",
            # VE.Bus control state
            f"N/{p}/vebus/{i}/State": "vebus_state",
            f"N/{p}/vebus/{i}/Mode": "vebus_mode",
            # System state
            f"N/{p}/system/0/SystemState/State": "system_state",
            # ESS settings
            f"N/{p}/settings/0/Settings/CGwacs/Hub4Mode": "ess_mode",
            f"N/{p}/settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit": "min_soc_limit_pct",
        }

    # ------------------------------------------------------------------
    # MQTT callbacks
    # ------------------------------------------------------------------

    def _on_message(
        self,
        client: mqtt.Client,  # noqa: ARG002
        userdata: Any,  # noqa: ARG002
        message: mqtt.MQTTMessage,
    ) -> None:
        """paho callback — runs in paho's background thread.

        Crosses the asyncio thread boundary via ``call_soon_threadsafe``.
        Never touch asyncio primitives directly here.
        """
        assert self._loop is not None
        self._loop.call_soon_threadsafe(
            self._process_message, message.topic, message.payload
        )

    def _process_message(self, topic: str, payload: bytes) -> None:
        """Process an MQTT message — runs in the asyncio event loop thread.

        Updates ``self._state`` and signals discovery events when the relevant
        topics arrive.
        """
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return

        value = data.get("value")
        if value is None:
            return

        logger.debug("MQTT rx: topic=%s value=%s", topic, value)

        # Discovery: portalId from N/+/system/0/Serial
        segments = topic.split("/")
        if (
            len(segments) == 5
            and segments[0] == "N"
            and segments[2] == "system"
            and segments[3] == "0"
            and segments[4] == "Serial"
            and not self._portal_event.is_set()
        ):
            self._portal_id = segments[1]
            self._portal_event.set()
            logger.debug("Victron portalId discovered: %s", self._portal_id)
            return

        # Discovery: instanceId from N/{portal}/vebus/+/ProductId
        if (
            len(segments) == 5
            and segments[0] == "N"
            and segments[2] == "vebus"
            and segments[4] == "ProductId"
            and not self._instance_event.is_set()
        ):
            self._instance_id = segments[3]
            self._instance_event.set()
            logger.debug("Victron instanceId discovered: %s", self._instance_id)
            return

        # Normal telemetry: update state dict via topic map
        field_key = self._topic_map.get(topic)
        if field_key is not None:
            self._state[field_key] = value

    # ------------------------------------------------------------------
    # Keepalive loop
    # ------------------------------------------------------------------

    async def _keepalive_loop(self) -> None:
        """Send periodic MQTT keepalives to keep dbus-flashmq publishing.

        The initial keepalive (empty payload) is sent in ``connect()`` to
        trigger the first data burst.  Subsequent keepalives use
        ``suppress-republish`` to avoid re-triggering a full burst.
        """
        payload = json.dumps({"keepalive-options": ["suppress-republish"]})
        try:
            while True:
                await asyncio.sleep(30)
                if self._client is not None and self._portal_id is not None:
                    self._client.publish(
                        f"R/{self._portal_id}/keepalive", payload, qos=0
                    )
                    logger.debug(
                        "MQTT keepalive sent to R/%s/keepalive", self._portal_id
                    )
        except asyncio.CancelledError:
            return

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_system_state(self) -> VictronSystemData:
        """Return a snapshot of the current system state.

        All fields not yet received via MQTT are ``None``.  The caller
        (S03 orchestrator) should check ``timestamp`` against
        ``time.monotonic()`` to detect stale data.

        Returns
        -------
        VictronSystemData
            Snapshot of ``self._state`` at call time.
        """
        s = self._state

        def _phase(prefix: str) -> VictronPhaseData:
            return VictronPhaseData(
                power_w=s.get(f"{prefix}_power_w", 0.0),
                current_a=s.get(f"{prefix}_current_a", 0.0),
                voltage_v=s.get(f"{prefix}_voltage_v", 0.0),
                setpoint_w=s.get(f"{prefix}_setpoint_w"),
            )

        return VictronSystemData(
            battery_soc_pct=s.get("battery_soc_pct", 0.0),
            battery_power_w=s.get("battery_power_w", 0.0),
            battery_current_a=s.get("battery_current_a", 0.0),
            battery_voltage_v=s.get("battery_voltage_v", 0.0),
            l1=_phase("l1"),
            l2=_phase("l2"),
            l3=_phase("l3"),
            ess_mode=s.get("ess_mode"),
            system_state=s.get("system_state"),
            vebus_state=s.get("vebus_state"),
            timestamp=time.monotonic(),
        )

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    def write_ac_power_setpoint(self, phase: int, watts: float) -> None:
        """Publish a per-phase AcPowerSetpoint to the VE.Bus ESS Hub4 register.

        Parameters
        ----------
        phase:
            Phase number: 1, 2, or 3.
        watts:
            Setpoint in watts.  Positive = import from grid (charge battery /
            supply loads).  Negative = export to grid (discharge battery).
            Note: negative values require Venus OS ≥ 3.21.

        Raises
        ------
        AssertionError
            If the driver has not been connected yet.
        """
        assert self._client is not None, "Driver not connected — call connect() first"
        topic = (
            f"W/{self._portal_id}/vebus/{self._instance_id}"
            f"/Hub4/L{phase}/AcPowerSetpoint"
        )
        payload = json.dumps({"value": watts})
        logger.debug("MQTT tx: topic=%s payload=%s", topic, payload)
        self._client.publish(topic, payload, qos=1)

    def write_disable_charge(self, disabled: bool) -> None:
        """Enable or disable battery charging.

        Parameters
        ----------
        disabled:
            ``True`` to disable charging; ``False`` to re-enable it.
        """
        assert self._client is not None, "Driver not connected — call connect() first"
        topic = f"W/{self._portal_id}/vebus/{self._instance_id}/Hub4/DisableCharge"
        payload = json.dumps({"value": 1 if disabled else 0})
        logger.debug("MQTT tx: topic=%s payload=%s", topic, payload)
        self._client.publish(topic, payload, qos=1)

    def write_disable_feed_in(self, disabled: bool) -> None:
        """Enable or disable grid feed-in (export).

        Parameters
        ----------
        disabled:
            ``True`` to disable feed-in; ``False`` to re-enable it.
        """
        assert self._client is not None, "Driver not connected — call connect() first"
        topic = f"W/{self._portal_id}/vebus/{self._instance_id}/Hub4/DisableFeedIn"
        payload = json.dumps({"value": 1 if disabled else 0})
        logger.debug("MQTT tx: topic=%s payload=%s", topic, payload)
        self._client.publish(topic, payload, qos=1)

    def write_ess_mode(self, mode: int) -> None:
        """Set the ESS Hub4Mode register.

        Parameters
        ----------
        mode:
            ESS mode integer.  Use ``3`` for external control (required for
            per-phase AcPowerSetpoints to take effect).
        """
        assert self._client is not None, "Driver not connected — call connect() first"
        topic = f"W/{self._portal_id}/settings/0/Settings/CGwacs/Hub4Mode"
        payload = json.dumps({"value": mode})
        logger.debug("MQTT tx: topic=%s payload=%s", topic, payload)
        self._client.publish(topic, payload, qos=1)
