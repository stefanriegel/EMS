"""EVCC MQTT data models and configuration.

``EvccLoadpointState`` holds the last-known state of EVCC loadpoint 1, updated
by :class:`~backend.evcc_mqtt_driver.EvccMqttDriver` on every incoming MQTT
message.  All fields carry safe defaults so a sentinel value can be constructed
with ``EvccLoadpointState()`` without any arguments.

``EvccMqttConfig`` is the connection configuration for the EVCC MQTT broker.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class EvccLoadpointState:
    """Snapshot of EVCC loadpoint 1 state, maintained by the MQTT driver.

    All fields default to safe zero-values so that callers can treat an
    ``EvccLoadpointState()`` as an "empty / not-yet-received" sentinel without
    risking ``AttributeError`` on any field access.

    Attributes:
        mode:             EVCC charge mode string, e.g. ``"off"``, ``"pv"``,
                          ``"minpv"``, ``"now"`` (default ``"off"``).
        charge_power_w:   Current charging power in watts (default ``0.0``).
        vehicle_soc_pct:  Vehicle SoC percentage, ``None`` when no vehicle is
                          plugged in or SoC is unknown (default ``None``).
        charging:         ``True`` when the vehicle is actively charging
                          (default ``False``).
        connected:        ``True`` when a vehicle is physically connected to the
                          EVSE (default ``False``).
    """

    mode: str = "off"
    charge_power_w: float = 0.0
    vehicle_soc_pct: float | None = None
    charging: bool = False
    connected: bool = False


@dataclass
class EvccMqttConfig:
    """Connection config for the EVCC MQTT broker.

    All fields have safe defaults so unit tests run without any environment
    variables set.

    Attributes:
        host: IP or hostname of the EVCC MQTT broker (default ``192.168.0.10``).
        port: TCP port (default ``1883``).

    Environment variables:
        ``EVCC_MQTT_HOST`` — hostname or IP (default ``192.168.0.10``).
        ``EVCC_MQTT_PORT`` — TCP port (default ``1883``).
    """

    host: str = "192.168.0.10"
    port: int = 1883

    @classmethod
    def from_env(cls) -> "EvccMqttConfig":
        """Construct an :class:`EvccMqttConfig` from environment variables.

        Both fields fall back to safe defaults when the corresponding
        environment variable is absent — **no env vars are required**.
        """
        return cls(
            host=os.environ.get("EVCC_MQTT_HOST", "192.168.0.10"),
            port=int(os.environ.get("EVCC_MQTT_PORT", "1883")),
        )
