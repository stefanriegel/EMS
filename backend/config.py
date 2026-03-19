"""EMS application configuration.

Environment variables are the only source of truth for runtime secrets and
host addresses.  All values have safe defaults so unit tests run without any
environment set.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class HuaweiConfig:
    """Connection and addressing config for the Huawei Modbus TCP proxy.

    Attributes:
        host: IP or hostname of the Modbus TCP proxy / SUN2000 dongle.
        port: TCP port (default 502).
        master_slave_id: Modbus unit ID of the master inverter (with batteries).
        slave_slave_id: Modbus unit ID of the slave (PV-only) inverter.
        timeout_s: Per-request timeout in seconds.
    """

    host: str
    port: int = 502
    master_slave_id: int = 0
    slave_slave_id: int = 2
    timeout_s: float = 10.0

    @classmethod
    def from_env(cls) -> "HuaweiConfig":
        """Construct a :class:`HuaweiConfig` from environment variables.

        Required:
            ``HUAWEI_HOST`` — hostname or IP address of the Modbus proxy.

        Optional (with defaults):
            ``HUAWEI_PORT``             — TCP port (default 502).
            ``HUAWEI_MASTER_SLAVE_ID``  — unit ID for master inverter (default 0).
            ``HUAWEI_SLAVE_SLAVE_ID``   — unit ID for slave inverter (default 2).

        Raises:
            KeyError: if ``HUAWEI_HOST`` is not set.
        """
        return cls(
            host=os.environ["HUAWEI_HOST"],
            port=int(os.environ.get("HUAWEI_PORT", "502")),
            master_slave_id=int(os.environ.get("HUAWEI_MASTER_SLAVE_ID", "0")),
            slave_slave_id=int(os.environ.get("HUAWEI_SLAVE_SLAVE_ID", "2")),
        )


@dataclass
class VictronConfig:
    """Connection config for the Victron Multiplus II MQTT broker.

    Attributes:
        host: IP or hostname of the Venus OS MQTT broker.
        port: TCP port (default 1883).
        timeout_s: Per-operation timeout in seconds (default 10.0).
        discovery_timeout_s: Maximum time to wait for portalId/instanceId
            discovery via the MQTT keep-alive topic (default 15.0).

    Environment variables:
        ``VICTRON_HOST`` — hostname or IP address of the MQTT broker (required).
        ``VICTRON_PORT`` — TCP port (optional, default 1883).
    """

    host: str
    port: int = 1883
    timeout_s: float = 10.0
    discovery_timeout_s: float = 15.0

    @classmethod
    def from_env(cls) -> "VictronConfig":
        """Construct a :class:`VictronConfig` from environment variables.

        Required:
            ``VICTRON_HOST`` — hostname or IP of the Venus OS MQTT broker.

        Optional (with defaults):
            ``VICTRON_PORT`` — TCP port (default 1883).

        Raises:
            KeyError: if ``VICTRON_HOST`` is not set.
        """
        return cls(
            host=os.environ["VICTRON_HOST"],
            port=int(os.environ.get("VICTRON_PORT", "1883")),
        )
