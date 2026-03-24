"""EMS application configuration.

Environment variables are the only source of truth for runtime secrets and
host addresses.  All values have safe defaults so unit tests run without any
environment set.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _require_env(key: str) -> str:
    """Return the value of *key* from the environment.

    Raises :class:`KeyError` if the variable is absent **or empty**.
    ``run.sh`` exports every option from ``/data/options.json`` — unconfigured
    fields arrive as ``""`` rather than being unset.  Treating empty the same
    as missing lets the lifespan's ``except KeyError`` degraded-mode path work
    correctly.
    """
    value = os.environ.get(key, "")
    if not value:
        raise KeyError(key)
    return value

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
            host=_require_env("HUAWEI_HOST"),
            port=int(os.environ.get("HUAWEI_PORT", "502")),
            master_slave_id=int(os.environ.get("HUAWEI_MASTER_SLAVE_ID", "0")),
            slave_slave_id=int(os.environ.get("HUAWEI_SLAVE_SLAVE_ID", "2")),
        )


@dataclass
class VictronConfig:
    """Connection config for the Victron Multiplus II via Modbus TCP.

    Attributes:
        host: IP or hostname of the Venus OS GX device (Modbus TCP server).
        port: TCP port (default 502 for Modbus TCP).
        timeout_s: Per-operation timeout in seconds (default 5.0).
        vebus_unit_id: Modbus unit ID for the VE.Bus inverter registers
            (default 227).  Venus OS assigns unit IDs dynamically based
            on connected devices.
        system_unit_id: Modbus unit ID for system-level registers such as
            battery SoC and total power (default 100).
        battery_unit_id: Modbus unit ID for battery-level registers such as
            voltage and current (default 225).

    Environment variables:
        ``VICTRON_HOST``              — hostname or IP (required).
        ``VICTRON_PORT``              — TCP port (optional, default 502).
        ``VICTRON_VEBUS_UNIT_ID``     — VE.Bus inverter unit ID (optional, default 227).
        ``VICTRON_SYSTEM_UNIT_ID``    — system-level unit ID (optional, default 100).
        ``VICTRON_BATTERY_UNIT_ID``   — battery-level unit ID (optional, default 225).
    """

    host: str
    port: int = 502
    timeout_s: float = 5.0
    vebus_unit_id: int = 227
    system_unit_id: int = 100
    battery_unit_id: int = 225

    @classmethod
    def from_env(cls) -> "VictronConfig":
        """Construct a :class:`VictronConfig` from environment variables.

        Required:
            ``VICTRON_HOST`` — hostname or IP of the Venus OS GX device.

        Optional (with defaults):
            ``VICTRON_PORT``              — TCP port (default 502).
            ``VICTRON_VEBUS_UNIT_ID``     — VE.Bus unit ID (default 227).
            ``VICTRON_SYSTEM_UNIT_ID``    — system-level unit ID (default 100).
            ``VICTRON_BATTERY_UNIT_ID``   — battery-level unit ID (default 225).

        Raises:
            KeyError: if ``VICTRON_HOST`` is not set.
        """
        return cls(
            host=_require_env("VICTRON_HOST"),
            port=int(os.environ.get("VICTRON_PORT", "502")),
            vebus_unit_id=int(os.environ.get("VICTRON_VEBUS_UNIT_ID", "227")),
            system_unit_id=int(os.environ.get("VICTRON_SYSTEM_UNIT_ID", "100")),
            battery_unit_id=int(os.environ.get("VICTRON_BATTERY_UNIT_ID", "225")),
        )


@dataclass
class MinSocWindow:
    """A time-of-day window with a minimum SoC floor.

    Attributes:
        start_hour: Start hour (0-23, inclusive).
        end_hour:   End hour (0-23, exclusive). Wraps around midnight
                    when start_hour > end_hour (e.g., 22 to 6).
        min_soc_pct: Minimum SoC percentage during this window.
    """

    start_hour: int
    end_hour: int
    min_soc_pct: float


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

    huawei_min_soc_profile: list[MinSocWindow] | None = None
    """Time-of-day min-SoC profile for Huawei. None = use static huawei_min_soc_pct."""

    victron_min_soc_profile: list[MinSocWindow] | None = None
    """Time-of-day min-SoC profile for Victron. None = use static victron_min_soc_pct."""

    feed_in_rate_eur_kwh: float = 0.074
    """Fixed feed-in tariff rate in EUR/kWh (default 7.4 ct/kWh)."""

    winter_months: list[int] = field(default_factory=lambda: [11, 12, 1, 2])
    """Months considered winter for seasonal strategy (1=Jan, 12=Dec)."""

    winter_min_soc_boost_pct: int = 10
    """Additional min-SoC percentage added during winter months."""


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

    InfluxDB is **optional** — the EMS runs fully without it.  The client is
    only instantiated when :attr:`enabled` is ``True``, i.e. when at least
    one of ``INFLUXDB_URL`` or ``INFLUXDB_TOKEN`` is explicitly set in the
    environment.

    Attributes:
        url:     HTTP(S) base URL of the InfluxDB instance (default: localhost).
        token:   Authentication token — never logged.
        org:     InfluxDB organisation name.
        bucket:  Target bucket for EMS measurements.
        enabled: ``True`` when InfluxDB is explicitly configured; ``False``
                 when neither ``INFLUXDB_URL`` nor ``INFLUXDB_TOKEN`` is set.

    Environment variables:
        ``INFLUXDB_URL``    — base URL (default ``http://localhost:8086``).
        ``INFLUXDB_TOKEN``  — auth token (default ``""``, i.e. disabled).
        ``INFLUXDB_ORG``    — organisation (default ``ems``).
        ``INFLUXDB_BUCKET`` — bucket (default ``ems``).
    """

    url: str = "http://localhost:8086"
    token: str = ""
    org: str = "ems"
    bucket: str = "ems"
    enabled: bool = False

    @classmethod
    def from_env(cls) -> "InfluxConfig":
        """Construct an :class:`InfluxConfig` from environment variables.

        InfluxDB is considered **enabled** when either ``INFLUXDB_URL`` or
        ``INFLUXDB_TOKEN`` is explicitly set to a non-empty value.  When
        neither is set :attr:`enabled` is ``False`` and the lifespan will
        skip instantiating the InfluxDB client.

        All connection fields fall back to safe defaults when their
        corresponding variable is absent.
        """
        raw_url = os.environ.get("INFLUXDB_URL", "")
        raw_token = os.environ.get("INFLUXDB_TOKEN", "")
        enabled = bool(raw_url or raw_token)
        return cls(
            url=raw_url or "http://localhost:8086",
            token=raw_token,
            org=os.environ.get("INFLUXDB_ORG", "") or "ems",
            bucket=os.environ.get("INFLUXDB_BUCKET", "") or "ems",
            enabled=enabled,
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
class HaRestConfig:
    """Connection config for the Home Assistant REST API.

    Used by :class:`~backend.ha_rest_client.HomeAssistantClient` to poll
    HA sensor states via the REST API.  All fields default to empty strings
    so unit tests run without any environment variables set.

    When both ``url`` and ``token`` are non-empty the client is active;
    when either is empty the client is not instantiated.

    Attributes:
        url:                  Base URL of the HA instance (e.g. ``http://homeassistant.local:8123``).
        token:                Long-lived access token — never logged.
        heat_pump_entity_id:  HA entity ID for the heat pump power sensor.

    Environment variables:
        ``HA_URL``                   — HA base URL (default empty → disabled).
        ``HA_TOKEN``                 — Long-lived access token (default empty → disabled).
        ``HA_HEAT_PUMP_ENTITY_ID``   — Entity ID for heat pump power (default empty).
    """

    url: str = ""
    token: str = ""
    heat_pump_entity_id: str = ""

    @classmethod
    def from_env(cls) -> "HaRestConfig":
        """Construct a :class:`HaRestConfig` from environment variables.

        All fields fall back to empty strings when absent — **no env vars
        are required**.  An empty ``url`` or ``token`` means the client
        will not be instantiated.
        """
        return cls(
            url=os.environ.get("HA_URL", ""),
            token=os.environ.get("HA_TOKEN", ""),
            heat_pump_entity_id=os.environ.get("HA_HEAT_PUMP_ENTITY_ID", ""),
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


@dataclass
class HaStatisticsConfig:
    """Configuration for the HA SQLite statistics reader and ConsumptionForecaster.

    Parameters
    ----------
    db_path:
        Filesystem path to ``home-assistant_v2.db``.
    min_training_days:
        Minimum number of days of history required before the ML models are
        considered trainable.  Below this threshold the forecaster falls back
        to the seasonal constant.  Default: 14.
    outdoor_temp_entity:
        HA ``statistic_id`` for the outdoor temperature sensor.
    heat_pump_entity:
        HA ``statistic_id`` for the heat pump power sensor.
    dhw_entity:
        HA ``statistic_id`` for the domestic hot water (DHW) power sensor.
        May be ``None`` when no DHW entity is configured.
    """

    db_path: str
    outdoor_temp_entity: str
    heat_pump_entity: str
    dhw_entity: str | None = None
    min_training_days: int = 14

    @classmethod
    def from_env(cls) -> "HaStatisticsConfig | None":
        """Construct from environment variables, or return ``None`` if ``HA_DB_PATH`` is absent."""
        db_path = os.environ.get("HA_DB_PATH", "/config/home-assistant_v2.db")
        if not db_path:
            return None
        return cls(
            db_path=db_path,
            outdoor_temp_entity=os.environ.get(
                "HA_STAT_OUTDOOR_TEMP_ENTITY",
                "sensor.ems_esp_boiler_aussentemperatur",
            ),
            heat_pump_entity=os.environ.get(
                "HA_STAT_HEAT_PUMP_ENTITY",
                "sensor.warmepumpe_total_active_power",
            ),
            dhw_entity=os.environ.get("HA_STAT_DHW_ENTITY") or None,
            min_training_days=int(os.environ.get("HA_ML_MIN_DAYS", "14")),
        )


@dataclass
class MultiEntityHaConfig:
    """Configuration for the multi-entity HA REST client.

    Parameters
    ----------
    entity_map:
        ``dict[field_name, (entity_id, converter)]`` mapping field names
        to HA entity IDs and value converters.
    """

    entity_map: dict

    @classmethod
    def default_entities(cls) -> dict:
        """Return the default 8-entity map covering all roadmap-specified sensors."""
        from backend.ha_rest_client import _float_converter  # noqa: PLC0415
        return {
            "heat_pump_power_w": ("sensor.warmepumpe_total_active_power", _float_converter),
            "cop": ("sensor.ems_esp_boiler_current_coefficient_of_performance", _float_converter),
            "outdoor_temp_c": ("sensor.ems_esp_boiler_aussentemperatur", _float_converter),
            "flow_temp_c": ("sensor.ems_esp_boiler_actual_flow_water_temperature", _float_converter),
            "return_temp_c": ("sensor.ems_esp_boiler_return_temperature", _float_converter),
            "hausverbrauch_w": ("sensor.hausverbrauch", _float_converter),
            "steuerbare_w": ("sensor.steuerbare_verbraucher", _float_converter),
            "base_w": ("sensor.hausverbrauch_abzgl_steuerbare", _float_converter),
        }

    @classmethod
    def from_env(cls) -> "MultiEntityHaConfig":
        """Construct from environment variables with sensible defaults."""
        return cls(entity_map=cls.default_entities())


@dataclass
class LiveTariffConfig:
    """Configuration for the live Octopus tariff from HA entity.

    Parameters
    ----------
    octopus_entity_id:
        HA entity ID to poll for the raw Octopus electricity price.
        Empty string disables the live tariff — falls back to CompositeTariffEngine.
    """

    octopus_entity_id: str = "sensor.octopus_a_7721404e_electricity_price"

    @classmethod
    def from_env(cls) -> "LiveTariffConfig":
        """Construct from environment variables."""
        return cls(
            octopus_entity_id=os.environ.get(
                "HA_OCTOPUS_ENTITY_ID",
                "sensor.octopus_a_7721404e_electricity_price",
            ),
        )


@dataclass
class OpenMeteoConfig:
    """Configuration for the Open-Meteo solar forecast fallback.

    Used by :class:`~backend.weather_client.OpenMeteoClient` to fetch
    global tilted irradiance from the free Open-Meteo forecast API and
    convert it to estimated PV output.

    Attributes:
        latitude:  Site latitude in decimal degrees.
        longitude: Site longitude in decimal degrees.
        tilt:      Panel tilt angle in degrees from horizontal (default 30).
        azimuth:   Panel azimuth in degrees: 0=south, -90=east, 90=west (default 0).
        dc_kwp:    PV system rated DC capacity in kWp (default 10.0).
        derating:  System derating factor for inverter/wiring/soiling losses
                   (default 0.80).
        timeout_s: HTTP request timeout in seconds (default 10.0).

    Environment variables:
        ``OPEN_METEO_LATITUDE``  -- site latitude (required for activation).
        ``OPEN_METEO_LONGITUDE`` -- site longitude (required for activation).
        ``OPEN_METEO_TILT``      -- panel tilt (default 30).
        ``OPEN_METEO_AZIMUTH``   -- panel azimuth (default 0).
        ``OPEN_METEO_DC_KWP``    -- PV capacity in kWp (default 10).
    """

    latitude: float = 0.0
    longitude: float = 0.0
    tilt: float = 30.0
    azimuth: float = 0.0
    dc_kwp: float = 10.0
    derating: float = 0.80
    timeout_s: float = 10.0

    @classmethod
    def from_env(cls) -> "OpenMeteoConfig | None":
        """Construct from environment variables, or ``None`` if not configured.

        Returns ``None`` when ``OPEN_METEO_LATITUDE`` or
        ``OPEN_METEO_LONGITUDE`` is absent or empty -- the weather client
        is entirely optional.
        """
        lat = os.environ.get("OPEN_METEO_LATITUDE", "")
        lon = os.environ.get("OPEN_METEO_LONGITUDE", "")
        if not lat or not lon:
            return None
        return cls(
            latitude=float(lat),
            longitude=float(lon),
            tilt=float(os.environ.get("OPEN_METEO_TILT", "30")),
            azimuth=float(os.environ.get("OPEN_METEO_AZIMUTH", "0")),
            dc_kwp=float(os.environ.get("OPEN_METEO_DC_KWP", "10")),
        )


@dataclass
class ModelStoreConfig:
    """Configuration for ML model persistence.

    Attributes:
        model_dir: Directory for persisted models (default /config/ems_models).
        enabled: Whether model persistence is active.

    Environment variables:
        ``EMS_MODEL_DIR`` -- directory path (default ``/config/ems_models``).
    """

    model_dir: str = "/config/ems_models"
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "ModelStoreConfig":
        """Construct from environment variables."""
        model_dir = os.environ.get("EMS_MODEL_DIR", "/config/ems_models")
        return cls(
            model_dir=model_dir,
            enabled=bool(model_dir),
        )


@dataclass
@dataclass
class HardwareValidationConfig:
    """Configuration for the hardware validation phase.

    Controls dry-run mode and the read-only validation period that must
    elapse before the EMS enables write operations on each battery system.

    Environment variables:
        ``EMS_VALIDATION_PERIOD_HOURS`` -- hours before writes enabled (default 48).
        ``EMS_DRY_RUN``                -- force dry-run mode (default "false").
    """

    validation_period_hours: float = 48.0
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> "HardwareValidationConfig":
        return cls(
            validation_period_hours=float(
                os.environ.get("EMS_VALIDATION_PERIOD_HOURS", "48")
            ),
            dry_run=os.environ.get("EMS_DRY_RUN", "false").lower() == "true",
        )


@dataclass
class AnomalyDetectorConfig:
    """Configuration for the anomaly detection engine.

    Governs thresholds, cooldowns, and persistence paths for the three
    detection domains: communication loss, consumption spikes, and
    battery health drift.

    Environment variables:
        ``EMS_MODEL_DIR``          -- base directory (default ``/config/ems_models``).
        ``EMS_ANOMALY_ENABLED``    -- ``"true"`` / ``"false"`` (default ``"true"``).
    """

    enabled: bool = True
    model_dir: str = "/config/ems_models"
    events_path: str = "/config/ems_models/anomaly_events.json"
    baselines_path: str = "/config/ems_models/anomaly_baselines.json"
    consumption_threshold_sigma: float = 3.0
    soc_rate_threshold_sigma: float = 3.0
    efficiency_threshold_pct: float = 85.0
    minimum_consumption_hours: int = 168
    minimum_battery_days: int = 14
    comm_loss_window_s: float = 3600.0
    comm_loss_min_windows: int = 3
    comm_loss_gap_s: float = 30.0
    warning_cooldown_s: float = 3600.0
    alert_cooldown_s: float = 14400.0
    max_events: int = 500
    max_event_age_days: int = 90
    isolation_forest_contamination: float = 0.05
    isolation_forest_n_estimators: int = 100
    isolation_forest_max_samples: int = 256

    @classmethod
    def from_env(cls) -> "AnomalyDetectorConfig":
        """Construct from environment variables.

        Only ``EMS_MODEL_DIR`` and ``EMS_ANOMALY_ENABLED`` are read;
        all other fields use safe defaults.
        """
        model_dir = os.environ.get("EMS_MODEL_DIR", "/config/ems_models")
        enabled = os.environ.get("EMS_ANOMALY_ENABLED", "true").lower() == "true"
        return cls(
            enabled=enabled,
            model_dir=model_dir,
            events_path=f"{model_dir}/anomaly_events.json",
            baselines_path=f"{model_dir}/anomaly_baselines.json",
        )


