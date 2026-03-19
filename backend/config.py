"""EMS application configuration.

Environment variables are the only source of truth for runtime secrets and
host addresses.  All values have safe defaults so unit tests run without any
environment set.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from backend.tariff_models import Modul3Config, Modul3Window, OctopusGoConfig


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


@dataclass
class InfluxConfig:
    """Connection config for the InfluxDB time-series database.

    All fields have safe defaults so unit tests run without any environment
    variables set.  The token default ``"test-token"`` is intentionally
    non-functional for a real InfluxDB instance — tests mock the client.

    Attributes:
        url:    HTTP(S) base URL of the InfluxDB instance (default: localhost).
        token:  Authentication token — never logged.
        org:    InfluxDB organisation name.
        bucket: Target bucket for EMS measurements.

    Environment variables:
        ``INFLUXDB_URL``    — base URL (default ``http://localhost:8086``).
        ``INFLUXDB_TOKEN``  — auth token (default ``test-token``).
        ``INFLUXDB_ORG``    — organisation (default ``ems``).
        ``INFLUXDB_BUCKET`` — bucket (default ``ems``).
    """

    url: str = "http://localhost:8086"
    token: str = "test-token"
    org: str = "ems"
    bucket: str = "ems"

    @classmethod
    def from_env(cls) -> "InfluxConfig":
        """Construct an :class:`InfluxConfig` from environment variables.

        All fields fall back to safe defaults when the corresponding
        environment variable is absent — **no env vars are required**.
        """
        return cls(
            url=os.environ.get("INFLUXDB_URL", "http://localhost:8086"),
            token=os.environ.get("INFLUXDB_TOKEN", "test-token"),
            org=os.environ.get("INFLUXDB_ORG", "ems"),
            bucket=os.environ.get("INFLUXDB_BUCKET", "ems"),
        )


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


@dataclass
class EvccConfig:
    """Connection config for the EVCC energy-management / EVopt HTTP API.

    All fields have safe defaults so unit tests run without any environment
    variables set.

    Attributes:
        host:      Hostname or IP of the EVCC instance (default ``192.168.0.10``).
        port:      HTTP port (default 7070).
        timeout_s: Per-request timeout in seconds (default 10.0).

    Environment variables:
        ``EVCC_HOST`` — hostname or IP (default ``192.168.0.10``).
        ``EVCC_PORT`` — HTTP port (default ``7070``).
    """

    host: str = "192.168.0.10"
    port: int = 7070
    timeout_s: float = 10.0

    @classmethod
    def from_env(cls) -> "EvccConfig":
        """Construct an :class:`EvccConfig` from environment variables.

        Both fields fall back to safe defaults when the corresponding
        environment variable is absent — **no env vars are required**.
        """
        return cls(
            host=os.environ.get("EVCC_HOST", "192.168.0.10"),
            port=int(os.environ.get("EVCC_PORT", "7070")),
        )


@dataclass
class SchedulerConfig:
    """Timing and charge-window parameters for the daily charge scheduler.

    Attributes:
        run_hour: Hour of day (local time) at which the scheduler runs
            (default 23). Set via ``SCHEDULER_RUN_HOUR``.
        grid_charge_start_min: Start of the allowed grid-charge window in
            minutes from midnight, in the Octopus timezone (Europe/London).
            Defaults to 30 (00:30 — start of the Octopus Go off-peak window).
            Set via ``SCHEDULER_CHARGE_START_MIN``.
        grid_charge_end_min: End of the allowed grid-charge window in minutes
            from midnight, in the Octopus timezone (Europe/London). Defaults
            to 300 (05:00 — end of the Octopus Go off-peak window).
            Set via ``SCHEDULER_CHARGE_END_MIN``.
        max_stale_hours: Number of hours after which a schedule is considered
            too stale to use (default 12). Set via ``SCHEDULER_MAX_STALE_HOURS``.
    """

    run_hour: int = 23
    """Hour of day (0–23) at which the scheduler computes a new schedule."""

    grid_charge_start_min: int = 30
    """Start of the grid-charge window in minutes from midnight (Europe/London).
    Default 30 = 00:30, the start of the Octopus Go off-peak window.
    """

    grid_charge_end_min: int = 300
    """End of the grid-charge window in minutes from midnight (Europe/London).
    Default 300 = 05:00, the end of the Octopus Go off-peak window.
    """

    max_stale_hours: int = 12
    """Hours after which an existing schedule is considered too stale to use."""

    @classmethod
    def from_env(cls) -> "SchedulerConfig":
        """Construct a :class:`SchedulerConfig` from environment variables.

        All fields fall back to safe defaults when the corresponding
        environment variable is absent — **no env vars are required**.

        Optional:
            ``SCHEDULER_RUN_HOUR``         — hour to run (default 23).
            ``SCHEDULER_CHARGE_START_MIN`` — charge window start in min (default 30).
            ``SCHEDULER_CHARGE_END_MIN``   — charge window end in min (default 300).
            ``SCHEDULER_MAX_STALE_HOURS``  — stale threshold in hours (default 12).
        """
        return cls(
            run_hour=int(os.environ.get("SCHEDULER_RUN_HOUR", "23")),
            grid_charge_start_min=int(os.environ.get("SCHEDULER_CHARGE_START_MIN", "30")),
            grid_charge_end_min=int(os.environ.get("SCHEDULER_CHARGE_END_MIN", "300")),
            max_stale_hours=int(os.environ.get("SCHEDULER_MAX_STALE_HOURS", "12")),
        )


@dataclass
class HaMqttConfig:
    """Connection config for the Home Assistant MQTT broker.

    Used by :class:`~backend.ha_mqtt_client.HomeAssistantMqttClient` to
    publish EMS telemetry to Home Assistant via MQTT discovery.

    Attributes:
        host:     Hostname or IP of the MQTT broker (default ``192.168.0.10``).
        port:     TCP port (default ``1883``).
        username: Optional MQTT username.
        password: Optional MQTT password.

    Environment variables:
        ``HA_MQTT_HOST``     — hostname or IP (default ``192.168.0.10``).
        ``HA_MQTT_PORT``     — TCP port (default ``1883``).
        ``HA_MQTT_USERNAME`` — MQTT username (optional).
        ``HA_MQTT_PASSWORD`` — MQTT password (optional).
    """

    host: str = "192.168.0.10"
    port: int = 1883
    username: str | None = None
    password: str | None = None

    @classmethod
    def from_env(cls) -> "HaMqttConfig":
        """Construct a :class:`HaMqttConfig` from environment variables.

        All fields fall back to safe defaults when the corresponding
        environment variable is absent — **no env vars are required**.
        """
        return cls(
            host=os.environ.get("HA_MQTT_HOST", "192.168.0.10"),
            port=int(os.environ.get("HA_MQTT_PORT", "1883")),
            username=os.environ.get("HA_MQTT_USERNAME"),
            password=os.environ.get("HA_MQTT_PASSWORD"),
        )


@dataclass
class TelegramConfig:
    """Config for the Telegram Bot alert notifier.

    Used by :class:`~backend.notifier.TelegramNotifier` to send EMS alerts.
    When both ``token`` and ``chat_id`` are non-empty the notifier is active;
    when either is empty the notifier is disabled.

    Attributes:
        token:   Telegram Bot API token from BotFather.
        chat_id: Target chat or channel ID.

    Environment variables:
        ``TELEGRAM_BOT_TOKEN`` — Bot API token (default empty → disabled).
        ``TELEGRAM_CHAT_ID``   — Chat ID (default empty → disabled).
    """

    token: str = ""
    chat_id: str = ""

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        """Construct a :class:`TelegramConfig` from environment variables.

        Both fields fall back to empty strings when absent — an empty token
        or chat_id means the notifier will not be instantiated.
        """
        return cls(
            token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        )


@dataclass
class TariffConfig:
    """Combined Octopus Go supply tariff and §14a EnWG Modul 3 grid-fee config.

    Both sub-configs are bundled here so a single ``TariffConfig.from_env()``
    call produces everything the :class:`~backend.tariff.CompositeTariffEngine`
    needs.  All defaults are realistic values that allow the test suite and
    development server to run without any environment variables.

    Attributes:
        octopus: Octopus Go supply tariff configuration.
        modul3: §14a EnWG Modul 3 Netzgebühren configuration.
    """

    octopus: OctopusGoConfig
    modul3: Modul3Config

    @classmethod
    def from_env(cls) -> "TariffConfig":
        """Construct a :class:`TariffConfig` from environment variables.

        All fields have safe, realistic defaults — **no environment variables
        are required**.  This is intentional: tariff configuration is stable
        for months at a time and the defaults model a typical UK Octopus Go
        customer using a German DSO with standard §14a Modul 3 windows.

        Default Octopus Go:
            off-peak 00:30–05:30 London, 0.08 €/kWh off-peak, 0.28 €/kWh peak.

        Default Modul 3 windows (Europe/Berlin):
            NT 00:00–06:00 (0.026 €/kWh), ST 06:00–17:00 (0.087 €/kWh),
            HT 17:00–20:00 (0.125 €/kWh), ST 20:00–24:00 (0.087 €/kWh).
        """
        octopus = OctopusGoConfig(
            off_peak_start_min=int(os.environ.get("OCTOPUS_OFF_PEAK_START_MIN", "30")),
            off_peak_end_min=int(os.environ.get("OCTOPUS_OFF_PEAK_END_MIN", "330")),
            off_peak_rate_eur_kwh=float(
                os.environ.get("OCTOPUS_OFF_PEAK_RATE_EUR_KWH", "0.08")
            ),
            peak_rate_eur_kwh=float(
                os.environ.get("OCTOPUS_PEAK_RATE_EUR_KWH", "0.28")
            ),
            timezone=os.environ.get("OCTOPUS_TIMEZONE", "Europe/London"),
        )
        modul3 = Modul3Config(
            windows=[
                Modul3Window(start_min=0, end_min=360, rate_eur_kwh=0.026, tier="NT"),
                Modul3Window(start_min=360, end_min=1020, rate_eur_kwh=0.087, tier="ST"),
                Modul3Window(start_min=1020, end_min=1200, rate_eur_kwh=0.125, tier="HT"),
                Modul3Window(start_min=1200, end_min=1440, rate_eur_kwh=0.087, tier="ST"),
            ],
            timezone=os.environ.get("MODUL3_TIMEZONE", "Europe/Berlin"),
        )
        return cls(octopus=octopus, modul3=modul3)

