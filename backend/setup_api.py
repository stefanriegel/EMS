"""Setup wizard API endpoints.

Provides three endpoints under the ``/api/setup`` prefix:

- ``GET  /api/setup/status``          — machine-readable setup state
- ``POST /api/setup/probe/{device}``  — point-in-time device reachability check
- ``POST /api/setup/complete``        — write wizard config to disk

These endpoints are intentionally **independent of the orchestrator and all
hardware drivers** so they are served even when the app starts in degraded
setup-only mode (i.e. before HUAWEI_HOST / VICTRON_HOST are configured).

Probe design
------------
Each probe is a short-lived connectivity test — no persistent state is
created.  Blocking operations (raw TCP, pymodbus) are wrapped in
``asyncio.to_thread()`` to avoid stalling the event loop.

The Victron Modbus probe uses ``pymodbus.client.ModbusTcpClient`` to
attempt a real register read (system SoC register 843).  If TCP connects
but the register read fails, a partial success with a warning is returned.

Observability
-------------
- ``INFO "Setup complete — config written to {path}"``  on POST /api/setup/complete
- Probe returns ``{"ok": false, "error": "<exception text>"}`` on any failure —
  the specific error text locates the failure (socket refused, MQTT timeout,
  HTTP 4xx, HA sensor not found).
"""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Literal

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.ha_rest_client import HomeAssistantClient
from backend.setup_config import EmsSetupConfig, load_setup_config, save_setup_config

logger = logging.getLogger(__name__)

setup_router = APIRouter(prefix="/api/setup")


# ---------------------------------------------------------------------------
# GET /api/setup/status
# ---------------------------------------------------------------------------


@setup_router.get("/status")
async def get_setup_status(request: Request) -> dict:
    """Return the current setup state.

    Response shape::

        {"setup_complete": bool, "config_path": str, "config_exists": bool}

    ``setup_complete`` is ``True`` only when the config file exists **and**
    contains non-empty ``huawei_host`` and ``victron_host`` values.
    """
    path: str = request.app.state.setup_config_path
    cfg = load_setup_config(path)
    config_exists = cfg is not None
    setup_complete = config_exists and bool(cfg.huawei_host) and bool(cfg.victron_host)  # type: ignore[union-attr]
    return {"setup_complete": setup_complete, "config_path": path, "config_exists": config_exists}


# ---------------------------------------------------------------------------
# POST /api/setup/probe/{device} — request body
# ---------------------------------------------------------------------------


class ProbeRequest(BaseModel):
    """Generic probe request body — all fields are optional.

    The endpoint inspects ``device`` from the path and reads the relevant
    subset of fields.  This avoids defining four separate endpoint functions
    while keeping Pydantic validation on each field that is used.
    """

    # Modbus
    host: str = ""
    port: int = 502
    unit_id: int = 100

    # HA REST (overrides port for HA which uses 8123 by default)
    url: str = ""
    token: str = ""
    entity_id: str = ""


# ---------------------------------------------------------------------------
# Private probe helpers (sync — called via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _probe_modbus(host: str, port: int) -> bool:
    """Open a raw TCP connection to *host*:*port* and immediately close it.

    Raises on failure so the caller can catch and return ``{"ok": false}``.
    """
    sock = socket.create_connection((host, port), timeout=5)
    sock.close()
    return True


def _probe_victron_modbus(host: str, port: int, unit_id: int) -> dict:
    """Modbus TCP register read probe for Victron Venus OS.

    Attempts to read the system SoC register (843) at the given unit ID.
    If the register read fails but TCP connected, returns a partial success
    with a warning. If TCP connection itself fails, raises.

    Returns
    -------
    dict
        ``{"ok": True}`` on full success.
        ``{"ok": True, "warning": "..."}`` on TCP-only success (register read failed).
    """
    from pymodbus.client import ModbusTcpClient

    client = ModbusTcpClient(host, port=port, timeout=5)
    if not client.connect():
        raise ConnectionError(f"TCP connection to {host}:{port} refused")
    try:
        result = client.read_holding_registers(843, count=1, slave=unit_id)
        if result.isError():
            return {
                "ok": True,
                "warning": "TCP connected, Modbus register read failed. Check unit IDs.",
            }
        return {"ok": True}
    finally:
        client.close()


# ---------------------------------------------------------------------------
# POST /api/setup/probe/{device}
# ---------------------------------------------------------------------------


@setup_router.post("/probe/{device}")
async def probe_device(
    device: Literal["modbus", "victron_modbus", "evcc", "ha_rest"],
    body: ProbeRequest,
) -> dict:
    """Run a point-in-time connectivity probe for *device*.

    Response shape::

        {"ok": true}
        {"ok": false, "error": "<exception string>"}

    All exceptions are caught at this level — the endpoint always returns 200
    with the ``ok``/``error`` shape rather than raising an HTTP error.
    """
    try:
        if device == "modbus":
            await asyncio.to_thread(_probe_modbus, body.host, body.port)

        elif device == "victron_modbus":
            result = await asyncio.to_thread(
                _probe_victron_modbus, body.host, body.port, body.unit_id
            )
            return result

        elif device == "evcc":
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"http://{body.host}:{body.port}/api/state")
                resp.raise_for_status()

        elif device == "ha_rest":
            ha_client = HomeAssistantClient(body.url, body.token, body.entity_id)
            val = await ha_client.get_sensor_value(body.entity_id)
            if val is None:
                return {"ok": False, "error": "HA REST sensor returned None (check URL, token, entity_id)"}

        return {"ok": True}

    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# POST /api/setup/complete
# ---------------------------------------------------------------------------


class SetupCompleteRequest(BaseModel):
    """Wizard payload — mirrors all :class:`~backend.setup_config.EmsSetupConfig` fields."""

    # --- Huawei Modbus ---
    huawei_host: str = ""
    huawei_port: int = 502

    # --- Victron Modbus TCP ---
    victron_host: str = ""
    victron_port: int = 502
    victron_system_unit_id: int = 100
    victron_battery_unit_id: int = 225
    victron_vebus_unit_id: int = 227

    # --- EVCC HTTP ---
    evcc_host: str = "192.168.0.10"
    evcc_port: int = 7070

    # --- EVCC MQTT ---
    evcc_mqtt_host: str = "192.168.0.10"
    evcc_mqtt_port: int = 1883

    # --- Home Assistant REST ---
    ha_url: str = ""
    ha_token: str = ""
    ha_heat_pump_entity_id: str = ""

    # --- Octopus Go tariff ---
    octopus_off_peak_start_min: int = 30
    octopus_off_peak_end_min: int = 330
    octopus_off_peak_rate_eur_kwh: float = 0.08
    octopus_peak_rate_eur_kwh: float = 0.28

    # --- Modul3 grid-fee tariff ---
    modul3_surplus_start_min: int = 0
    modul3_surplus_end_min: int = 0
    modul3_deficit_start_min: int = 0
    modul3_deficit_end_min: int = 0
    modul3_surplus_rate_eur_kwh: float = 0.0
    modul3_deficit_rate_eur_kwh: float = 0.0

    # --- Feed-in tariff ---
    feed_in_rate_eur_kwh: float = 0.074

    # --- Seasonal strategy ---
    winter_months: str = "11,12,1,2"
    winter_min_soc_boost_pct: int = 10

    # --- SoC limits ---
    huawei_min_soc_pct: float = 10.0
    huawei_max_soc_pct: float = 95.0
    victron_min_soc_pct: float = 15.0
    victron_max_soc_pct: float = 95.0


@setup_router.post("/complete")
async def complete_setup(request: Request, body: SetupCompleteRequest) -> dict:
    """Persist the wizard config to disk.

    Constructs an :class:`~backend.setup_config.EmsSetupConfig` from the
    request body and calls :func:`~backend.setup_config.save_setup_config`
    atomically.

    Response shape::

        {"ok": true}
    """
    cfg = EmsSetupConfig(**body.model_dump())
    path: str = request.app.state.setup_config_path
    save_setup_config(cfg, path)
    logger.info("Setup complete — config written to %s", path)
    return {"ok": True}
