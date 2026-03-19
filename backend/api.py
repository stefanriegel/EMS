"""EMS FastAPI router — exposes the orchestrator state and config via HTTP (S03),
and the composite tariff engine via two read-only endpoints (S04).

Endpoints
---------
``GET  /api/state``
    Returns the current :class:`~backend.unified_model.UnifiedPoolState` as JSON.
    Returns **503** if the orchestrator has not yet completed its first poll cycle.

``GET  /api/health``
    Returns a structured health report: driver availability, control state,
    last error, and uptime.

``GET  /api/config``
    Returns the current :class:`~backend.config.SystemConfig` as JSON.

``POST /api/config``
    Accepts a JSON body matching :class:`SystemConfigRequest`, updates the
    orchestrator's running config, and returns the updated config.  Returns
    **422** on Pydantic validation failure (FastAPI default).

``GET  /api/tariff/price?dt=ISO8601``
    Returns the composite electricity price at a given instant.  Returns **400**
    if ``dt`` cannot be parsed as an ISO 8601 datetime.

``GET  /api/tariff/schedule?date=YYYY-MM-DD``
    Returns the full day's tariff slot list.  Returns **400** if ``date``
    cannot be parsed.

Dependency injection
--------------------
All endpoints depend on :func:`get_orchestrator` (reads from
``request.app.state.orchestrator``) or :func:`get_tariff_engine` (reads from
``request.app.state.tariff_engine``).  Tests override these via
``app.dependency_overrides``.
"""
from __future__ import annotations

import dataclasses
import time
from datetime import datetime as dt_type
from datetime import date as date_type
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.config import SystemConfig
from backend.orchestrator import Orchestrator
from backend.tariff import CompositeTariffEngine

api_router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# Startup timestamp — recorded when the module is first imported (proxy for
# when the app was launched).  Used by /api/health uptime_s.
# ---------------------------------------------------------------------------
_start_time: float = time.monotonic()


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class SystemConfigRequest(BaseModel):
    """Pydantic model for POST /api/config request body.

    Field constraints mirror :class:`~backend.config.SystemConfig` semantics.
    FastAPI validates incoming JSON against this model and returns 422 on
    any violation.
    """

    huawei_min_soc_pct: float = Field(
        default=10.0, ge=0.0, le=100.0,
        description="Minimum allowed SoC for Huawei LUNA2000 (%).",
    )
    huawei_max_soc_pct: float = Field(
        default=95.0, ge=0.0, le=100.0,
        description="Maximum allowed SoC for Huawei LUNA2000 (%).",
    )
    victron_min_soc_pct: float = Field(
        default=15.0, ge=0.0, le=100.0,
        description="Minimum allowed SoC for Victron MPII battery (%).",
    )
    victron_max_soc_pct: float = Field(
        default=95.0, ge=0.0, le=100.0,
        description="Maximum allowed SoC for Victron MPII battery (%).",
    )
    huawei_feed_in_allowed: bool = Field(
        default=False,
        description="Whether the Huawei system may export to the grid.",
    )
    victron_feed_in_allowed: bool = Field(
        default=False,
        description="Whether the Victron system may export to the grid.",
    )


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


def get_orchestrator(request: Request) -> Orchestrator:
    """FastAPI dependency that returns the running :class:`Orchestrator`.

    Reads ``request.app.state.orchestrator``.  Tests override this via::

        app.dependency_overrides[get_orchestrator] = lambda: mock_orchestrator
    """
    return request.app.state.orchestrator  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state_to_dict(state: Any) -> dict[str, Any]:
    """Convert a :class:`~backend.unified_model.UnifiedPoolState` to a plain dict.

    Uses :func:`dataclasses.asdict` so all fields are included automatically.
    ``ControlState`` (a ``str`` mixin enum) serialises to its string value.
    """
    return dataclasses.asdict(state)


def _config_to_dict(cfg: SystemConfig) -> dict[str, Any]:
    """Convert a :class:`~backend.config.SystemConfig` to a plain dict."""
    return dataclasses.asdict(cfg)


def _health_status(state: Any) -> str:
    """Derive health status string from :class:`~backend.unified_model.UnifiedPoolState`.

    Returns
    -------
    str
        ``"ok"`` — both drivers available.
        ``"degraded"`` — one driver available.
        ``"offline"`` — no drivers available.
    """
    if state is None:
        return "offline"
    both = state.huawei_available and state.victron_available
    any_ = state.huawei_available or state.victron_available
    if both:
        return "ok"
    if any_:
        return "degraded"
    return "offline"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@api_router.get("/state")
async def get_state(
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    """Return the current unified pool state snapshot.

    Raises
    ------
    HTTPException(503)
        If the orchestrator has not yet completed its first poll cycle.
    """
    state = orchestrator.get_state()
    if state is None:
        raise HTTPException(status_code=503, detail="Orchestrator not yet ready")
    return _state_to_dict(state)


@api_router.get("/health")
async def get_health(
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    """Return a structured health report.

    Response shape::

        {
            "status": "ok" | "degraded" | "offline",
            "huawei_available": bool,
            "victron_available": bool,
            "control_state": str,
            "last_error": str | null,
            "uptime_s": float
        }
    """
    state = orchestrator.get_state()
    return {
        "status": _health_status(state),
        "huawei_available": state.huawei_available if state else False,
        "victron_available": state.victron_available if state else False,
        "control_state": str(state.control_state) if state else "IDLE",
        "last_error": orchestrator.get_last_error(),
        "uptime_s": time.monotonic() - _start_time,
    }


@api_router.get("/config")
async def get_config(
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    """Return the current system configuration."""
    return _config_to_dict(orchestrator.sys_config)


@api_router.post("/config")
async def post_config(
    body: SystemConfigRequest,
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    """Update the system configuration at runtime.

    Accepts a JSON body matching :class:`SystemConfigRequest`.  Any field
    omitted from the request body uses its default value.  Returns **422** on
    validation failure (FastAPI/Pydantic default behaviour).

    The new config takes effect on the orchestrator's next control cycle.
    """
    new_cfg = SystemConfig(
        huawei_min_soc_pct=body.huawei_min_soc_pct,
        huawei_max_soc_pct=body.huawei_max_soc_pct,
        victron_min_soc_pct=body.victron_min_soc_pct,
        victron_max_soc_pct=body.victron_max_soc_pct,
        huawei_feed_in_allowed=body.huawei_feed_in_allowed,
        victron_feed_in_allowed=body.victron_feed_in_allowed,
    )
    orchestrator.sys_config = new_cfg
    return _config_to_dict(new_cfg)


# ---------------------------------------------------------------------------
# Tariff engine dependency
# ---------------------------------------------------------------------------


def get_tariff_engine(request: Request) -> CompositeTariffEngine:
    """FastAPI dependency that returns the running :class:`CompositeTariffEngine`.

    Reads ``request.app.state.tariff_engine``.  Tests override this via::

        app.dependency_overrides[get_tariff_engine] = lambda: engine
    """
    return request.app.state.tariff_engine  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Tariff routes
# ---------------------------------------------------------------------------


@api_router.get("/tariff/price")
async def get_tariff_price(
    dt: str,
    engine: CompositeTariffEngine = Depends(get_tariff_engine),
) -> dict[str, Any]:
    """Return the composite electricity price at a given instant.

    Query parameters
    ----------------
    dt
        ISO 8601 datetime string, e.g. ``2026-01-15T02:00:00`` or
        ``2026-01-15T02:00:00+00:00``.  Naive strings are interpreted as
        wall-clock time in the Octopus Go timezone.

    Returns
    -------
    dict
        ``dt``, ``effective_rate_eur_kwh``, ``octopus_rate_eur_kwh``,
        ``modul3_rate_eur_kwh``.

    Raises
    ------
    HTTPException(400)
        If ``dt`` cannot be parsed as an ISO 8601 datetime.
    """
    try:
        parsed_dt = dt_type.fromisoformat(dt)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid datetime '{dt}': {exc}",
        ) from exc

    from zoneinfo import ZoneInfo

    oct_tz = ZoneInfo(engine._octopus.timezone)
    m3_tz = ZoneInfo(engine._modul3.timezone)

    if parsed_dt.tzinfo is None:
        dt_oct = parsed_dt.replace(tzinfo=oct_tz)
    else:
        dt_oct = parsed_dt.astimezone(oct_tz)
    dt_m3 = dt_oct.astimezone(m3_tz)

    oct_minute = dt_oct.hour * 60 + dt_oct.minute
    m3_minute = dt_m3.hour * 60 + dt_m3.minute

    octopus_rate = engine._octopus_rate_at(oct_minute)
    modul3_rate = engine._modul3_rate_at(m3_minute)

    return {
        "dt": dt,
        "effective_rate_eur_kwh": round(octopus_rate + modul3_rate, 6),
        "octopus_rate_eur_kwh": octopus_rate,
        "modul3_rate_eur_kwh": modul3_rate,
    }


@api_router.get("/tariff/schedule")
async def get_tariff_schedule(
    date: str,
    engine: CompositeTariffEngine = Depends(get_tariff_engine),
) -> list[dict[str, Any]]:
    """Return the full composite tariff schedule for a given date.

    Query parameters
    ----------------
    date
        ISO 8601 date string, e.g. ``2026-01-15``.

    Returns
    -------
    list[dict]
        Ordered list of slot dicts with keys ``start``, ``end``,
        ``octopus_rate_eur_kwh``, ``modul3_rate_eur_kwh``,
        ``effective_rate_eur_kwh``.  Slots are contiguous and cover
        00:00–24:00 in the Octopus timezone.

    Raises
    ------
    HTTPException(400)
        If ``date`` cannot be parsed as ``YYYY-MM-DD``.
    """
    try:
        parsed_date = date_type.fromisoformat(date)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date '{date}': {exc}",
        ) from exc

    slots = engine.get_price_schedule(parsed_date)
    return [
        {
            "start": slot.start.isoformat(),
            "end": slot.end.isoformat(),
            "octopus_rate_eur_kwh": slot.octopus_rate_eur_kwh,
            "modul3_rate_eur_kwh": slot.modul3_rate_eur_kwh,
            "effective_rate_eur_kwh": slot.effective_rate_eur_kwh,
        }
        for slot in slots
    ]
