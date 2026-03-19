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

from backend.api import api_router
from backend.config import HuaweiConfig, OrchestratorConfig, SystemConfig, VictronConfig
from backend.drivers.huawei_driver import HuaweiDriver
from backend.drivers.victron_driver import VictronDriver
from backend.orchestrator import Orchestrator

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
    await victron.connect()
    logger.info("Victron driver connected")

    # --- Start orchestrator ---
    orchestrator = Orchestrator(huawei, victron, sys_cfg, orch_cfg)
    await orchestrator.start()
    logger.info("Orchestrator control loop started")

    # Store on app.state so the DI layer can retrieve it
    app.state.orchestrator = orchestrator

    yield  # application is running

    # --- Shutdown ---
    logger.info("EMS shutting down — stopping orchestrator")
    await orchestrator.stop()
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
    return app


# ---------------------------------------------------------------------------
# Production entry point
# ---------------------------------------------------------------------------

app = create_app()
