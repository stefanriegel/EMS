"""EMS FastAPI application — lifespan wiring and app factory (S03).

Usage
-----
Start the development server::

    HUAWEI_HOST=192.168.0.10 VICTRON_HOST=192.168.0.10 \\
    uvicorn backend.main:app --host 0.0.0.0 --port 8000

The module-level ``app`` instance is the production entry point for uvicorn.
Tests should call :func:`create_app` directly and inject a mock orchestrator
via ``app.dependency_overrides`` — never import the singleton ``app`` in tests.

Lifespan
--------
On startup:

1. Both driver configs are read from environment variables
   (``HUAWEI_HOST``, ``VICTRON_HOST`` — see :mod:`backend.config`).
2. Both drivers are instantiated and connected.
3. An :class:`~backend.orchestrator.Orchestrator` is constructed and started.
4. The orchestrator is stored on ``app.state.orchestrator`` for the
   :func:`~backend.api.get_orchestrator` dependency.

On shutdown (SIGINT / SIGTERM / uvicorn graceful stop):

1. The orchestrator is stopped (safe setpoints written).
2. Both drivers are disconnected.

Logging
-------
Set ``LOG_LEVEL`` environment variable to control verbosity::

    LOG_LEVEL=DEBUG uvicorn backend.main:app --port 8000

Default level is ``INFO``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from backend.api import api_router
from backend.auth import AdminConfig, AuthMiddleware, auth_router, ensure_jwt_secret
from backend.config import HuaweiConfig, InfluxConfig, OrchestratorConfig, SystemConfig, TariffConfig, VictronConfig, EvccConfig, SchedulerConfig, EvccMqttConfig, HaMqttConfig, TelegramConfig, HaRestConfig, HaStatisticsConfig, MultiEntityHaConfig, LiveTariffConfig
from backend.supervisor_client import SupervisorClient
from backend.setup_config import load_setup_config, EMS_CONFIG_PATH
from backend.setup_api import setup_router
from backend.ha_rest_client import HomeAssistantClient, MultiEntityHaClient
from backend.drivers.huawei_driver import HuaweiDriver
from backend.drivers.victron_driver import VictronDriver
from backend.evcc_client import EvccClient
from backend.evcc_mqtt_driver import EvccMqttDriver
from backend.ha_mqtt_client import HomeAssistantMqttClient
from backend.influx_reader import InfluxMetricsReader
from backend.influx_writer import InfluxMetricsWriter
from backend.notifier import TelegramNotifier
from backend.orchestrator import Orchestrator
from backend.scheduler import Scheduler
from backend.tariff import CompositeTariffEngine
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    """Configure root logger from ``LOG_LEVEL`` environment variable.

    Defaults to ``INFO``.  Called once during :func:`create_app`.
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


# ---------------------------------------------------------------------------
# Nightly scheduler loop
# ---------------------------------------------------------------------------


async def _nightly_scheduler_loop(
    scheduler,
    writer,
    run_hour: int,
    *,
    consumption_forecaster=None,
    app=None,
) -> None:
    """Asyncio task that calls ``scheduler.compute_schedule()`` once per night.

    Sleeps until ``run_hour:00:00`` local time, then fires ``compute_schedule``
    and repeats every 24 hours.  Exceptions in ``compute_schedule`` are caught
    and logged as WARNING so a transient EVCC failure does not kill the loop.

    Parameters
    ----------
    scheduler:
        A :class:`~backend.scheduler.Scheduler` instance.
    writer:
        Optional :class:`~backend.influx_writer.InfluxMetricsWriter` passed
        through to ``compute_schedule``.
    run_hour:
        Local clock hour (0–23) at which to run the scheduler each night.
    consumption_forecaster:
        Optional :class:`~backend.consumption_forecaster.ConsumptionForecaster`
        that is retrained each night.
    app:
        Optional :class:`~fastapi.FastAPI` instance for setting
        ``app.state.forecast_comparison``.
    """
    from datetime import datetime as _dt, timedelta as _td

    now = _dt.now()
    target = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        # Next occurrence is tomorrow
        target = target + _td(days=1)
    initial_sleep = (target - now).total_seconds()
    logger.info(
        "nightly-scheduler: first run in %.0f s at %s (run_hour=%d)",
        initial_sleep,
        target.isoformat(),
        run_hour,
    )
    await asyncio.sleep(initial_sleep)

    while True:
        logger.info("nightly-scheduler: compute_schedule running — run_hour=%d", run_hour)
        try:
            # Retrain ML models if stale
            if consumption_forecaster is not None:
                try:
                    await consumption_forecaster.retrain_if_stale()
                    logger.info("nightly-scheduler: consumption forecaster retrained")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("nightly-scheduler: retrain failed: %s", exc)

            await scheduler.compute_schedule(writer)
            logger.info("nightly-scheduler: compute_schedule complete")

            # Compute forecast comparison for yesterday
            if consumption_forecaster is not None and app is not None:
                try:
                    metrics_reader = getattr(app.state, "metrics_reader", None)
                    if metrics_reader is not None:
                        actual = await metrics_reader.query_consumption_history(days=2)
                        if actual is not None and hasattr(actual, "today_expected_kwh"):
                            comparison = consumption_forecaster.get_forecast_comparison(
                                actual.today_expected_kwh
                            )
                            app.state.forecast_comparison = comparison
                except Exception as exc:  # noqa: BLE001
                    logger.warning("nightly-scheduler: forecast comparison failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("nightly-scheduler: compute_schedule failed: %s", exc)
        await asyncio.sleep(86400)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context manager.

    Connects both drivers, starts the orchestrator control loop, and stores
    the orchestrator on ``app.state.orchestrator`` for the DI layer.

    On shutdown (after the ``yield``), the orchestrator is stopped gracefully
    and both drivers are disconnected.

    If ``HUAWEI_HOST`` / ``VICTRON_HOST`` are absent (and no wizard config is
    present on disk), the lifespan starts in **degraded setup-only mode**:
    ``app.state.orchestrator`` is set to ``None`` and only the setup endpoints
    are served.  Existing ``GET /api/state`` callers will receive 503 via the
    ``get_orchestrator()`` dependency — which is the correct signal that the
    orchestrator is not running.
    """
    # --- Load wizard-persisted config and inject into env (setdefault = env vars win) ---
    config_path = os.environ.get("EMS_CONFIG_PATH", EMS_CONFIG_PATH)
    app.state.setup_config_path = config_path

    # Ensure a persistent JWT secret exists before any auth config is read.
    # Uses the same directory as the wizard config file so it lives on the
    # same persistent volume (HA config volume / Docker bind mount).
    config_dir = os.path.dirname(config_path)
    ensure_jwt_secret(config_dir)

    setup_cfg = load_setup_config(config_path)
    if setup_cfg is not None:
        os.environ.setdefault("HUAWEI_HOST", setup_cfg.huawei_host)
        os.environ.setdefault("HUAWEI_PORT", str(setup_cfg.huawei_port))
        os.environ.setdefault("VICTRON_HOST", setup_cfg.victron_host)
        os.environ.setdefault("VICTRON_PORT", str(setup_cfg.victron_port))
        os.environ.setdefault("EVCC_HOST", setup_cfg.evcc_host)
        os.environ.setdefault("EVCC_PORT", str(setup_cfg.evcc_port))
        os.environ.setdefault("EVCC_MQTT_HOST", setup_cfg.evcc_mqtt_host)
        os.environ.setdefault("EVCC_MQTT_PORT", str(setup_cfg.evcc_mqtt_port))
        os.environ.setdefault("HA_URL", setup_cfg.ha_url)
        os.environ.setdefault("HA_TOKEN", setup_cfg.ha_token)
        os.environ.setdefault("HA_HEAT_PUMP_ENTITY_ID", setup_cfg.ha_heat_pump_entity_id)

    # --- Supervisor service discovery (HA add-on mode only, no-op otherwise) ---
    # Resolves MQTT broker credentials and EVCC add-on location automatically
    # when running inside Home Assistant OS.  Results are injected into env vars
    # via setdefault so explicit env vars (run.sh options, Docker Compose .env)
    # always take precedence.
    supervisor = SupervisorClient.from_env()
    if supervisor is not None:
        logger.info("Supervisor: detected — resolving services automatically")

        # HA Core API via Supervisor proxy (no user token needed)
        ha_proxy = supervisor.get_ha_proxy_config()
        os.environ.setdefault("HA_URL", ha_proxy.base_url)
        os.environ.setdefault("HA_TOKEN", ha_proxy.token)
        logger.info("Supervisor: HA REST API → %s", ha_proxy.base_url)

        # MQTT broker (Mosquitto add-on)
        mqtt_info = await supervisor.get_mqtt_service()
        if mqtt_info is not None:
            os.environ.setdefault("HA_MQTT_HOST", mqtt_info.host)
            os.environ.setdefault("HA_MQTT_PORT", str(mqtt_info.port))
            os.environ.setdefault("HA_MQTT_USERNAME", mqtt_info.username or "")
            os.environ.setdefault("HA_MQTT_PASSWORD", mqtt_info.password or "")
            # EVCC MQTT broker is the same Mosquitto instance
            os.environ.setdefault("EVCC_MQTT_HOST", mqtt_info.host)
            os.environ.setdefault("EVCC_MQTT_PORT", str(mqtt_info.port))
            os.environ.setdefault("EVCC_MQTT_USERNAME", mqtt_info.username or "")
            os.environ.setdefault("EVCC_MQTT_PASSWORD", mqtt_info.password or "")

        # EVCC add-on (optional — skipped gracefully if not installed)
        evcc_info = await supervisor.get_evcc_info()
        if evcc_info is not None:
            os.environ.setdefault("EVCC_HOST", evcc_info.api_host)
            os.environ.setdefault("EVCC_PORT", str(evcc_info.api_port))

        # InfluxDB add-on (optional — not all HAOS installations have it)
        influx_info = await supervisor.get_influxdb_service()
        if influx_info is not None and influx_info.url:
            os.environ.setdefault("INFLUXDB_URL", influx_info.url)
            if influx_info.token:
                os.environ.setdefault("INFLUXDB_TOKEN", influx_info.token)
            logger.info(
                "Supervisor: using InfluxDB URL from Supervisor service discovery — url=%s",
                influx_info.url,
            )
    else:
        logger.debug("Supervisor: not detected — using env vars / wizard config only")

    try:
        huawei_cfg = HuaweiConfig.from_env()
        victron_cfg = VictronConfig.from_env()
        sys_cfg = SystemConfig()
        orch_cfg = OrchestratorConfig()

        logger.info(
            "EMS starting up — Huawei=%s:%d  Victron=%s:%d",
            huawei_cfg.host,
            huawei_cfg.port,
            victron_cfg.host,
            victron_cfg.port,
        )

        huawei = HuaweiDriver(
            host=huawei_cfg.host,
            port=huawei_cfg.port,
            master_slave_id=huawei_cfg.master_slave_id,
            slave_slave_id=huawei_cfg.slave_slave_id,
            timeout_s=huawei_cfg.timeout_s,
        )
        victron = VictronDriver(
            host=victron_cfg.host,
            port=victron_cfg.port,
            timeout_s=victron_cfg.timeout_s,
            discovery_timeout_s=victron_cfg.discovery_timeout_s,
        )

        await huawei.connect()
        logger.info("Huawei driver connected")
        try:
            await victron.connect()
            logger.info("Victron driver connected")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Victron driver failed to connect — running without Victron: %s", exc)

        # --- Instantiate EVCC client and scheduler (needs sys_cfg / orch_cfg) ---
        evcc_cfg = EvccConfig.from_env()
        sched_cfg = SchedulerConfig.from_env()

        # --- Instantiate tariff engine ---
        tariff_cfg = TariffConfig.from_env()
        tariff_engine = CompositeTariffEngine(
            octopus=tariff_cfg.octopus, modul3=tariff_cfg.modul3
        )
        app.state.tariff_engine = tariff_engine
        logger.info(
            "Tariff engine initialised — Octopus tz=%s Modul3 tz=%s windows=%d",
            tariff_cfg.octopus.timezone,
            tariff_cfg.modul3.timezone,
            len(tariff_cfg.modul3.windows),
        )

        # --- Instantiate InfluxDB client and metrics writer ---
        influx_cfg = InfluxConfig.from_env()
        influx_client = InfluxDBClientAsync(
            url=influx_cfg.url, token=influx_cfg.token, org=influx_cfg.org
        )
        metrics_writer = InfluxMetricsWriter(influx_client, influx_cfg.bucket)
        metrics_reader = InfluxMetricsReader(influx_client, influx_cfg.org, influx_cfg.bucket)
        logger.info(
            "InfluxDB client connected — url=%s org=%s", influx_cfg.url, influx_cfg.org
        )

        # --- ML Consumption Forecaster (optional — requires HA SQLite DB) ---
        consumption_forecaster = None
        ha_stats_cfg = HaStatisticsConfig.from_env()
        if ha_stats_cfg is not None and os.path.isfile(ha_stats_cfg.db_path):
            try:
                from backend.ha_statistics_reader import HaStatisticsReader  # noqa: PLC0415
                from backend.consumption_forecaster import ConsumptionForecaster  # noqa: PLC0415

                ha_stats_reader = HaStatisticsReader(ha_stats_cfg.db_path)
                consumption_forecaster = ConsumptionForecaster(ha_stats_reader, ha_stats_cfg)
                await consumption_forecaster.train()
                logger.info(
                    "ConsumptionForecaster trained — db_path=%s min_days=%d",
                    ha_stats_cfg.db_path, ha_stats_cfg.min_training_days,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("ConsumptionForecaster failed to initialize: %s", exc)
                consumption_forecaster = None
        else:
            if ha_stats_cfg is not None:
                logger.info(
                    "ConsumptionForecaster disabled — DB not found at %s",
                    ha_stats_cfg.db_path,
                )
            else:
                logger.info("ConsumptionForecaster disabled — HA_DB_PATH not configured")

        # Use ML forecaster as the consumption reader for the scheduler if available
        effective_consumption_reader = consumption_forecaster if consumption_forecaster is not None else metrics_reader

        evcc_client = EvccClient(evcc_cfg)
        scheduler = Scheduler(evcc_client, effective_consumption_reader, tariff_engine, sys_cfg, orch_cfg)
        app.state.scheduler = scheduler
        logger.info(
            "Scheduler wired — run_hour=%d charge_window=%d–%d min",
            sched_cfg.run_hour,
            sched_cfg.grid_charge_start_min,
            sched_cfg.grid_charge_end_min,
        )

        # --- Start nightly scheduler loop ---
        sched_task = asyncio.create_task(
            _nightly_scheduler_loop(
                scheduler,
                metrics_writer,
                sched_cfg.run_hour,
                consumption_forecaster=consumption_forecaster,
                app=app,
            ),
            name="nightly-scheduler",
        )
        app.state.sched_task = sched_task

        # --- Start orchestrator ---
        orchestrator = Orchestrator(
            huawei, victron, sys_cfg, orch_cfg,
            writer=metrics_writer,
            tariff_engine=tariff_engine,
        )
        await orchestrator.start()
        logger.info("Orchestrator control loop started")
        orchestrator.set_scheduler(scheduler)
        logger.info("Orchestrator: scheduler wired for GRID_CHARGE slot detection")

        app.state.orchestrator = orchestrator
        app.state.metrics_reader = metrics_reader

        # --- EVCC MQTT driver (optional — skipped if host is not configured) ---
        evcc_mqtt_cfg = EvccMqttConfig.from_env()
        try:
            evcc_driver = EvccMqttDriver(host=evcc_mqtt_cfg.host, port=evcc_mqtt_cfg.port)
            await evcc_driver.connect()
            orchestrator.set_evcc_monitor(evcc_driver)
            app.state.evcc_driver = evcc_driver
            logger.info(
                "EVCC MQTT driver connected — host=%s:%d", evcc_mqtt_cfg.host, evcc_mqtt_cfg.port
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("EVCC MQTT driver failed to connect — running without EVCC monitoring: %s", exc)
            app.state.evcc_driver = None

        # --- HA MQTT client (optional — skipped if host is not configured) ---
        ha_mqtt_cfg = HaMqttConfig.from_env()
        try:
            ha_client = HomeAssistantMqttClient(
                host=ha_mqtt_cfg.host,
                port=ha_mqtt_cfg.port,
                username=ha_mqtt_cfg.username,
                password=ha_mqtt_cfg.password,
            )
            await ha_client.connect()
            app.state.ha_mqtt_client = ha_client
            logger.info(
                "HA MQTT client connecting — host=%s:%d", ha_mqtt_cfg.host, ha_mqtt_cfg.port
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("HA MQTT client failed to connect — running without HA MQTT: %s", exc)
            app.state.ha_mqtt_client = None

        # --- Telegram notifier ---
        telegram_cfg = TelegramConfig.from_env()
        notifier: TelegramNotifier | None = None
        if telegram_cfg.token and telegram_cfg.chat_id:
            notifier = TelegramNotifier(
                token=telegram_cfg.token,
                chat_id=telegram_cfg.chat_id,
            )
            logger.info("Telegram notifier configured")
        else:
            logger.info("Telegram notifier disabled — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        app.state.notifier = notifier
        if notifier is not None:
            orchestrator.set_notifier(notifier)
            logger.info("Orchestrator: Telegram notifier wired")

        # --- HA REST client (multi-entity or single-entity fallback) ---
        ha_rest_cfg = HaRestConfig.from_env()
        if ha_rest_cfg.url and ha_rest_cfg.token:
            multi_ha_cfg = MultiEntityHaConfig.from_env()
            entity_map = multi_ha_cfg.entity_map

            # Add Octopus entity to the map if live tariff is configured
            live_tariff_cfg = LiveTariffConfig.from_env()
            if live_tariff_cfg.octopus_entity_id:
                octopus_field = "octopus_electricity_price"
                from backend.ha_rest_client import _float_converter  # noqa: PLC0415
                entity_map[octopus_field] = (live_tariff_cfg.octopus_entity_id, _float_converter)

            ha_rest_client = MultiEntityHaClient(
                ha_rest_cfg.url,
                ha_rest_cfg.token,
                entity_map,
            )
            await ha_rest_client.start()
            app.state.ha_rest_client = ha_rest_client
            logger.info(
                "HA REST multi-entity client configured — %d entities",
                len(entity_map),
            )

            # --- LiveOctopusTariff conditional wrap ---
            if live_tariff_cfg.octopus_entity_id:
                from backend.live_tariff import LiveOctopusTariff  # noqa: PLC0415
                tariff_engine = LiveOctopusTariff(
                    ha_client=ha_rest_client,
                    octopus_entity_field=octopus_field,
                    fallback=tariff_engine,
                )
                app.state.tariff_engine = tariff_engine
                logger.info(
                    "Live Octopus tariff configured — entity=%s field=%s",
                    live_tariff_cfg.octopus_entity_id,
                    octopus_field,
                )
        else:
            app.state.ha_rest_client = None
            logger.info("HA REST client not configured — HA_URL / HA_TOKEN not set")

        # forecast_comparison is updated nightly; initialize to None so the WS
        # handler can safely read it via getattr before the first nightly run.
        app.state.forecast_comparison = None

    except KeyError as exc:
        logger.warning(
            "Orchestrator not started — missing required env var %s "
            "(setup-only mode; open /setup to configure)",
            exc,
        )
        app.state.orchestrator = None
        app.state.scheduler = None
        app.state.sched_task = None
        app.state.metrics_reader = None
        app.state.evcc_driver = None
        app.state.ha_mqtt_client = None
        app.state.notifier = None
        app.state.ha_rest_client = None
        app.state.forecast_comparison = None

    yield  # application is running

    # --- Shutdown ---
    if app.state.orchestrator is not None:
        logger.info("EMS shutting down — stopping orchestrator")
        await app.state.orchestrator.stop()
    if getattr(app.state, "sched_task", None) is not None:
        app.state.sched_task.cancel()
        await asyncio.gather(app.state.sched_task, return_exceptions=True)
        logger.info("nightly-scheduler: task cancelled")
    if app.state.evcc_driver is not None:
        await app.state.evcc_driver.close()
    if app.state.ha_mqtt_client is not None:
        await app.state.ha_mqtt_client.disconnect()
    if app.state.ha_rest_client is not None:
        await app.state.ha_rest_client.stop()
    if app.state.orchestrator is not None:
        # influx_client and drivers are only created in the non-degraded path
        await influx_client.close()
        logger.info("Disconnecting Victron driver")
        await victron.close()
        logger.info("Disconnecting Huawei driver")
        await huawei.close()
    logger.info("EMS shutdown complete")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Separated from the module-level ``app`` singleton so tests can call this
    function directly and inject mock orchestrators via
    ``app.dependency_overrides`` without importing the production lifespan.

    Returns
    -------
    FastAPI
        Configured application instance with lifespan and API router attached.
    """
    _configure_logging()
    app = FastAPI(
        title="EMS",
        description="Energy Management System — unified 94 kWh battery pool API",
        version="0.1.0",
        lifespan=lifespan,
    )
    # Ensure JWT secret is generated/loaded before middleware reads AdminConfig.
    # Uses the configured config dir; falls back to the directory of the default path.
    config_path = os.environ.get("EMS_CONFIG_PATH", EMS_CONFIG_PATH)
    ensure_jwt_secret(os.path.dirname(config_path))
    app.add_middleware(AuthMiddleware, admin_cfg=AdminConfig.from_env())
    app.include_router(api_router)
    app.include_router(setup_router)
    app.include_router(auth_router)

    # Mount the React SPA build artifacts.  The os.path.exists guard is
    # mandatory: without it, uvicorn raises RuntimeError at startup in CI or
    # dev environments where `npm run build` hasn't been run yet.  The mount
    # MUST come after include_router so /api/* and /ws/* routes take
    # precedence over the catch-all SPA fallback.
    if os.path.exists("frontend/dist"):
        app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="static")
        logger.info("StaticFiles mounted — serving React SPA from frontend/dist")
    else:
        logger.warning("frontend/dist not found — React SPA not mounted (run `cd frontend && npm run build`)")

    return app


# ---------------------------------------------------------------------------
# Production entry point
# ---------------------------------------------------------------------------

app = create_app()
