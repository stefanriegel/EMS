"""EMS FastAPI router — exposes the orchestrator state and config via HTTP (S03).

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

Dependency injection
--------------------
All endpoints depend on :func:`get_orchestrator`, which reads the
:class:`~backend.orchestrator.Orchestrator` instance from
``request.app.state.orchestrator``.  Tests override this via
``app.dependency_overrides[get_orchestrator] = lambda: mock``.
"""
from __future__ import annotations

import dataclasses
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.config import SystemConfig
from backend.orchestrator import Orchestrator

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
