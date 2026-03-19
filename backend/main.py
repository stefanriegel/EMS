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

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from backend.api import api_router
from backend.config import HuaweiConfig, InfluxConfig, OrchestratorConfig, SystemConfig, TariffConfig, VictronConfig, EvccConfig, SchedulerConfig
from backend.drivers.huawei_driver import HuaweiDriver
from backend.drivers.victron_driver import VictronDriver
from backend.evcc_client import EvccClient
from backend.influx_reader import InfluxMetricsReader
from backend.influx_writer import InfluxMetricsWriter
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
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context manager.

    Connects both drivers, starts the orchestrator control loop, and stores
    the orchestrator on ``app.state.orchestrator`` for the DI layer.

    On shutdown (after the ``yield``), the orchestrator is stopped gracefully
    and both drivers are disconnected.

    Raises
    ------
    KeyError
        If ``HUAWEI_HOST`` or ``VICTRON_HOST`` environment variables are not set.
    """
    # --- Read configuration from environment ---
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

    # --- Connect drivers ---
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

    # --- Instantiate EVCC client and scheduler ---
    evcc_cfg = EvccConfig.from_env()
    sched_cfg = SchedulerConfig.from_env()
    evcc_client = EvccClient(evcc_cfg)
    scheduler = Scheduler(evcc_client, metrics_reader, tariff_engine, sys_cfg, orch_cfg)
    app.state.scheduler = scheduler
    logger.info(
        "Scheduler wired — run_hour=%d charge_window=%d–%d min",
        sched_cfg.run_hour,
        sched_cfg.grid_charge_start_min,
        sched_cfg.grid_charge_end_min,
    )

    # --- Start orchestrator ---
    orchestrator = Orchestrator(
        huawei, victron, sys_cfg, orch_cfg,
        writer=metrics_writer,
        tariff_engine=tariff_engine,
    )
    await orchestrator.start()
    logger.info("Orchestrator control loop started")

    # Store on app.state so the DI layer can retrieve it
    app.state.orchestrator = orchestrator
    app.state.metrics_reader = metrics_reader

    yield  # application is running

    # --- Shutdown ---
    logger.info("EMS shutting down — stopping orchestrator")
    await orchestrator.stop()
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
    app.include_router(api_router)

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
