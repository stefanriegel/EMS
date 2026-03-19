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


@dataclass
class SystemConfig:
    """Per-system SoC limits and feed-in rules for the unified battery pool.

    Applied by the orchestrator to guard against deep discharge or
    over-charge of either ESS system.  Feed-in flags control whether the
    orchestrator may instruct a system to export energy to the grid.

    All percentage fields are in the range 0.0–100.0.
    """

    huawei_min_soc_pct: float = 10.0
    """Minimum allowed SoC for the Huawei LUNA2000 (default 10 %).
    The orchestrator stops discharging Huawei when this threshold is reached.
    """

    huawei_max_soc_pct: float = 95.0
    """Maximum allowed SoC for the Huawei LUNA2000 (default 95 %).
    The orchestrator stops charging Huawei above this level.
    """

    victron_min_soc_pct: float = 15.0
    """Minimum allowed SoC for the Victron MPII battery (default 15 %).
    Slightly higher than Huawei to reflect a more conservative reserve.
    """

    victron_max_soc_pct: float = 95.0
    """Maximum allowed SoC for the Victron MPII battery (default 95 %)."""

    huawei_feed_in_allowed: bool = False
    """Whether the Huawei system may export to the grid (default False)."""

    victron_feed_in_allowed: bool = False
    """Whether the Victron system may export to the grid (default False)."""


@dataclass
class OrchestratorConfig:
    """Timing, hysteresis, and capacity parameters for the control loop.

    These govern how frequently the orchestrator polls drivers, how it
    debounces state transitions, and the physical limits it may apply.
    """

    loop_interval_s: float = 5.0
    """Control loop interval in seconds (default 5 s)."""

    hysteresis_w: int = 200
    """Dead-band around setpoint transitions in watts (default 200 W).
    Prevents micro-oscillations when load hovers near a threshold.
    """

    debounce_cycles: int = 2
    """Number of consecutive cycles a new state must persist before the
    orchestrator commits the transition (default 2 cycles).
    """

    stale_threshold_s: float = 30.0
    """Age in seconds beyond which a driver reading is considered stale
    (default 30 s).  Stale data triggers a WARNING log.
    """

    max_offline_s: float = 60.0
    """Maximum seconds a driver may be unreachable before the orchestrator
    transitions to HOLD for that system (default 60 s).
    """

    victron_max_discharge_w: float = 10000.0
    """Maximum discharge power the orchestrator will request from the
    Victron system in watts (default 10 000 W / 10 kW).
    """

    victron_max_charge_w: float = 10000.0
    """Maximum charge power the orchestrator will request from the
    Victron system in watts (default 10 000 W / 10 kW).
    """

    huawei_capacity_kwh: float = 30.0
    """Usable capacity of the Huawei LUNA2000 system in kWh (default 30)."""

    victron_capacity_kwh: float = 64.0
    """Usable capacity of the Victron MPII battery in kWh (default 64)."""
