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
