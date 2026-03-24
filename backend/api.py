"""EMS FastAPI router — exposes the orchestrator state and config via HTTP (S03),
and the composite tariff engine via two read-only endpoints (S04),
and the charge schedule via one read-only endpoint (S05).

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

``GET  /api/optimization/schedule``
    Returns the active :class:`~backend.schedule_models.ChargeSchedule` as a
    JSON-safe dict.  Returns **503** if the scheduler is not available or has
    no active schedule yet.

Dependency injection
--------------------
All endpoints depend on :func:`get_orchestrator` (reads from
``request.app.state.orchestrator``), :func:`get_tariff_engine` (reads from
``request.app.state.tariff_engine``), or :func:`get_scheduler` (reads from
``request.app.state.scheduler``).  Tests override these via
``app.dependency_overrides``.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from datetime import datetime as dt_type
from datetime import date as date_type
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend.config import SystemConfig
from backend.influx_reader import InfluxMetricsReader
from backend.coordinator import Coordinator
from backend.schedule_models import ChargeSchedule
from backend.scheduler import Scheduler
from backend.tariff import CompositeTariffEngine
from backend.ws_manager import manager

logger = logging.getLogger(__name__)

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
    feed_in_rate_eur_kwh: float = Field(
        default=0.074, ge=0.0,
        description="Fixed feed-in tariff rate in EUR/kWh.",
    )
    winter_months: list[int] = Field(
        default=[11, 12, 1, 2],
        description="Months considered winter for seasonal strategy (1=Jan, 12=Dec).",
    )
    winter_min_soc_boost_pct: int = Field(
        default=10, ge=0, le=50,
        description="Additional min-SoC percentage added during winter months.",
    )


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


def get_orchestrator(request: Request) -> Coordinator:
    """FastAPI dependency that returns the running :class:`Coordinator`.

    Reads ``request.app.state.orchestrator`` (backward-compat attribute name).
    Tests override this via::

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
    orchestrator: Coordinator = Depends(get_orchestrator),
) -> dict[str, Any]:
    """Return the current unified pool state snapshot.

    Raises
    ------
    HTTPException(503)
        If the orchestrator has not yet completed its first poll cycle.
    """
    state = orchestrator.get_state()
    if state is None:
        raise HTTPException(status_code=503, detail="Coordinator not yet ready")
    return _state_to_dict(state)


@api_router.get("/health")
async def get_health(
    request: Request,
    orchestrator: Coordinator | None = Depends(get_orchestrator),
) -> dict[str, Any]:
    """Return a structured health report.

    Accepts ``None`` from :func:`get_orchestrator` so it works in degraded
    mode (orchestrator not started) — returns status "offline" with safe
    defaults rather than crashing.

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
    state = orchestrator.get_state() if orchestrator is not None else None

    # HA multi-entity client status
    ha_client = getattr(request.app.state, "ha_rest_client", None)
    ha_entities_count = 0
    ha_entities_available = False
    if ha_client is not None and hasattr(ha_client, "get_all_values"):
        all_values = ha_client.get_all_values()
        ha_entities_count = len(all_values)
        ha_entities_available = any(v is not None for v in all_values.values())

    return {
        "status": _health_status(state),
        "huawei_available": state.huawei_available if state else False,
        "victron_available": state.victron_available if state else False,
        "control_state": str(state.control_state) if state else "IDLE",
        "last_error": orchestrator.get_last_error() if orchestrator is not None else None,
        "uptime_s": time.monotonic() - _start_time,
        "huawei_working_mode": orchestrator.get_working_mode() if orchestrator is not None else None,
        "ha_entities_count": ha_entities_count,
        "ha_entities_available": ha_entities_available,
        "integrations": orchestrator.get_integration_health() if orchestrator is not None else {},
        "cross_charge": orchestrator.get_cross_charge_status() if orchestrator is not None else None,
    }


@api_router.get("/decisions")
async def get_decisions_endpoint(
    limit: int = 20,
    orchestrator: Coordinator | None = Depends(get_orchestrator),
) -> list[dict[str, Any]]:
    """Return the last N coordinator dispatch decisions, newest first.

    Query parameters
    ----------------
    limit
        Maximum number of entries to return. Default 20, max 100 (per D-13).

    Returns 503 if the coordinator is not running (setup-only mode).
    """
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Coordinator not running")
    clamped = min(max(limit, 1), 100)
    return orchestrator.get_decisions(limit=clamped)


@api_router.get("/config")
async def get_config(
    orchestrator: Coordinator = Depends(get_orchestrator),
) -> dict[str, Any]:
    """Return the current system configuration."""
    return _config_to_dict(orchestrator.sys_config)


@api_router.post("/config")
async def post_config(
    body: SystemConfigRequest,
    orchestrator: Coordinator = Depends(get_orchestrator),
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
        feed_in_rate_eur_kwh=body.feed_in_rate_eur_kwh,
        winter_months=body.winter_months,
        winter_min_soc_boost_pct=body.winter_min_soc_boost_pct,
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


# ---------------------------------------------------------------------------
# Metrics reader dependency
# ---------------------------------------------------------------------------


def get_metrics_reader(request: Request) -> InfluxMetricsReader | None:
    """FastAPI dependency that returns the running :class:`InfluxMetricsReader`.

    Reads ``request.app.state.metrics_reader`` via :func:`getattr` so it
    gracefully returns ``None`` if the attribute was never set (e.g. in tests
    that don't wire up the reader).  Tests override this via::

        app.dependency_overrides[get_metrics_reader] = lambda: mock_reader
    """
    return getattr(request.app.state, "metrics_reader", None)


# ---------------------------------------------------------------------------
# ML forecaster dependency
# ---------------------------------------------------------------------------


def get_forecaster(request: Request):
    """FastAPI dependency that returns the running :class:`ConsumptionForecaster`.

    Reads ``request.app.state.consumption_forecaster`` via :func:`getattr` so
    it gracefully returns ``None`` if the attribute was never set (e.g. in
    tests that don't wire up the forecaster).  Tests override this via::

        app.dependency_overrides[get_forecaster] = lambda: mock_forecaster
    """
    return getattr(request.app.state, "consumption_forecaster", None)


# ---------------------------------------------------------------------------
# Anomaly detector dependency
# ---------------------------------------------------------------------------


def get_anomaly_detector(request: Request):
    """FastAPI dependency that returns the running :class:`AnomalyDetector`.

    Reads ``request.app.state.anomaly_detector`` via :func:`getattr` so
    it gracefully returns ``None`` if the attribute was never set.
    """
    return getattr(request.app.state, "anomaly_detector", None)


def get_self_tuner(request: Request):
    """FastAPI dependency that returns the running :class:`SelfTuner`.

    Reads ``request.app.state.self_tuner`` via :func:`getattr` so
    it gracefully returns ``None`` if the attribute was never set.
    """
    return getattr(request.app.state, "self_tuner", None)


@api_router.get("/ml/status")
async def get_ml_status(
    forecaster=Depends(get_forecaster),
    anomaly_detector=Depends(get_anomaly_detector),
    self_tuner=Depends(get_self_tuner),
) -> dict[str, Any]:
    """Return ML model status, training info, MAPE history, and battery health."""
    if forecaster is None:
        raise HTTPException(status_code=503, detail="ML forecaster not available")
    result = forecaster.get_ml_status()
    if anomaly_detector is not None:
        result["battery_health"] = anomaly_detector.get_battery_health()
    if self_tuner is not None:
        result["self_tuning"] = self_tuner.get_tuning_status()
    return result


@api_router.get("/anomaly/events")
async def get_anomaly_events(
    limit: int = 100,
    anomaly_detector=Depends(get_anomaly_detector),
) -> list[dict[str, Any]]:
    """Return recent anomaly events."""
    if anomaly_detector is None:
        raise HTTPException(
            status_code=503, detail="Anomaly detector not available"
        )
    return anomaly_detector.get_events(limit=limit)


# ---------------------------------------------------------------------------
# Scheduler dependency
# ---------------------------------------------------------------------------


def get_scheduler(request: Request) -> Scheduler | None:
    """FastAPI dependency that returns the running :class:`~backend.scheduler.Scheduler`.

    Reads ``request.app.state.scheduler`` via :func:`getattr` so it gracefully
    returns ``None`` if the attribute was never set (e.g. tests that don't wire
    up the scheduler).  Tests override this via::

        app.dependency_overrides[get_scheduler] = lambda: mock_scheduler
    """
    return getattr(request.app.state, "scheduler", None)


# ---------------------------------------------------------------------------
# Schedule serialisation helper
# ---------------------------------------------------------------------------


def _schedule_to_dict(schedule: ChargeSchedule) -> dict[str, Any]:
    """Convert a :class:`~backend.schedule_models.ChargeSchedule` to a JSON-safe dict.

    :func:`dataclasses.asdict` produces raw :class:`datetime` objects for
    ``computed_at`` and each slot's ``start_utc``/``end_utc``; those are
    replaced with ISO 8601 strings so the result passes through
    :func:`json.dumps` without ``TypeError``.
    """
    raw = dataclasses.asdict(schedule)
    raw["computed_at"] = schedule.computed_at.isoformat()
    for slot_raw, slot_obj in zip(raw["slots"], schedule.slots):
        slot_raw["start_utc"] = slot_obj.start_utc.isoformat()
        slot_raw["end_utc"] = slot_obj.end_utc.isoformat()
    return raw


# ---------------------------------------------------------------------------
# EVopt-compatible /api/v1/plan endpoint
# ---------------------------------------------------------------------------


def _schedule_to_evopt(schedule: "ChargeSchedule") -> dict[str, Any]:
    """Convert a :class:`ChargeSchedule` to the EVopt JSON format.

    The EVopt format uses 96 time slots (15-minute intervals over 24 hours)
    with per-battery timeseries arrays.  Batteries are identified by title:
    ``"Emma Akku 1"`` for Huawei and ``"Victron"`` for Victron.
    """
    from datetime import timedelta

    computed_at = schedule.computed_at
    # Generate 96 15-minute timestamps starting from midnight of the schedule day
    base = computed_at.replace(hour=0, minute=0, second=0, microsecond=0)
    if computed_at.hour >= 12:
        # Schedule is for next day
        base = base + timedelta(days=1)
    timestamps = [
        (base + timedelta(minutes=15 * i)).isoformat()
        for i in range(96)
    ]

    # Build per-battery timeseries
    battery_map: dict[str, dict[str, list[float]]] = {
        "Emma Akku 1": {
            "charging_power": [0.0] * 96,
            "discharging_power": [0.0] * 96,
            "state_of_charge": [0.0] * 96,
        },
        "Victron": {
            "charging_power": [0.0] * 96,
            "discharging_power": [0.0] * 96,
            "state_of_charge": [0.0] * 96,
        },
    }

    # Map each slot to its battery title
    for slot in schedule.slots:
        title = "Emma Akku 1" if slot.battery == "huawei" else "Victron"
        if title not in battery_map:
            continue
        # Fill the timeseries for this slot's window
        for i in range(96):
            slot_time = base + timedelta(minutes=15 * i)
            if slot.start_utc <= slot_time < slot.end_utc:
                # Use max in case multiple slots overlap for same battery
                current = battery_map[title]["charging_power"][i]
                battery_map[title]["charging_power"][i] = max(
                    current, float(slot.grid_charge_power_w)
                )

    batteries = [
        {"title": title, **series}
        for title, series in battery_map.items()
    ]

    return {
        "res": {
            "status": schedule.reasoning.evopt_status,
            "objective_value": schedule.reasoning.cost_estimate_eur,
            "batteries": batteries,
            "details": {
                "timestamp": timestamps,
            },
        }
    }


@api_router.get("/v1/plan")
async def get_evopt_plan(
    scheduler: Scheduler | None = Depends(get_scheduler),
) -> dict[str, Any]:
    """Return the active charge schedule in EVopt-compatible JSON format.

    This endpoint implements the EVOPT_URI contract (R038): EVCC (or any EVopt
    consumer) can ``GET /api/v1/plan`` and receive a response that passes
    through ``_parse_state({"evopt": response_json})`` without error.

    Returns
    -------
    dict
        EVopt-format JSON with ``res.status``, ``res.objective_value``,
        ``res.batteries`` (timeseries), and ``res.details.timestamp``.

    Raises
    ------
    HTTPException(503)
        If the scheduler is absent or has no active schedule.
    """
    if scheduler is None:
        raise HTTPException(
            status_code=503,
            detail={"status": "Unavailable"},
        )
    if scheduler.active_schedule is None:
        raise HTTPException(
            status_code=503,
            detail={"status": "Unavailable"},
        )
    return _schedule_to_evopt(scheduler.active_schedule)


# ---------------------------------------------------------------------------
# Metrics routes
# ---------------------------------------------------------------------------


@api_router.get("/metrics/range")
async def get_metrics_range(
    measurement: str,
    start: str,
    stop: str,
    reader: InfluxMetricsReader | None = Depends(get_metrics_reader),
) -> list[dict]:
    """Return time-series records for *measurement* over *[start, stop)*.

    Query parameters
    ----------------
    measurement
        InfluxDB measurement name, e.g. ``ems_system``.
    start
        Flux-compatible start, e.g. ``-1h`` or an RFC3339 string.
    stop
        Flux-compatible stop, e.g. ``now()`` or an RFC3339 string.

    Returns
    -------
    list[dict]
        Flat list of ``{"time", "field", "value"}`` dicts.

    Raises
    ------
    HTTPException(503)
        If the metrics reader is not available (lifespan failed to construct
        it or the app is running without InfluxDB).
    """
    if reader is None:
        raise HTTPException(status_code=503, detail="Metrics reader not available")
    return await reader.query_range(measurement, start, stop)


@api_router.get("/metrics/latest")
async def get_metrics_latest(
    measurement: str,
    reader: InfluxMetricsReader | None = Depends(get_metrics_reader),
) -> dict | None:
    """Return the most-recent record for *measurement*.

    Query parameters
    ----------------
    measurement
        InfluxDB measurement name, e.g. ``ems_system``.

    Returns
    -------
    dict | None
        Single ``{"time", "field", "value"}`` dict, or ``null`` if no data.

    Raises
    ------
    HTTPException(503)
        If the metrics reader is not available.
    """
    if reader is None:
        raise HTTPException(status_code=503, detail="Metrics reader not available")
    return await reader.query_latest(measurement)


# ---------------------------------------------------------------------------
# Optimization schedule route
# ---------------------------------------------------------------------------


@api_router.get("/optimization/forecast")
async def get_optimization_forecast(
    scheduler: Scheduler | None = Depends(get_scheduler),
) -> dict[str, Any]:
    """Return the multi-day solar/consumption forecast from WeatherScheduler.

    Returns
    -------
    dict
        ``{"days": [{"date", "day_index", "solar_kwh", "consumption_kwh",
        "net_kwh", "confidence", "charge_target_kwh", "advisory"}, ...]}``

    Raises
    ------
    HTTPException(503)
        If the scheduler is absent or has no active day plans.
    """
    if scheduler is None:
        raise HTTPException(
            status_code=503, detail="Scheduler not available"
        )
    day_plans = getattr(scheduler, "active_day_plans", None)
    if day_plans is None:
        raise HTTPException(
            status_code=503, detail="No active day plans"
        )
    return {
        "days": [
            {
                "date": dp.date.isoformat(),
                "day_index": dp.day_index,
                "solar_kwh": round(dp.solar_forecast_kwh, 1),
                "consumption_kwh": round(
                    dp.consumption_forecast_kwh, 1
                ),
                "net_kwh": round(dp.net_energy_kwh, 1),
                "confidence": dp.confidence,
                "charge_target_kwh": round(dp.charge_target_kwh, 1),
                "advisory": dp.advisory,
            }
            for dp in day_plans
        ]
    }


def _day_plan_to_dict(dp: Any) -> dict[str, Any]:
    """Serialise a :class:`~backend.schedule_models.DayPlan` to a JSON-safe dict."""
    result: dict[str, Any] = {
        "date": dp.date.isoformat(),
        "day_index": dp.day_index,
        "solar_kwh": round(dp.solar_forecast_kwh, 1),
        "consumption_kwh": round(dp.consumption_forecast_kwh, 1),
        "net_kwh": round(dp.net_energy_kwh, 1),
        "confidence": dp.confidence,
        "charge_target_kwh": round(dp.charge_target_kwh, 1),
        "advisory": dp.advisory,
        "slots": [],
    }
    for slot in dp.slots:
        result["slots"].append({
            "battery": slot.battery,
            "target_soc_pct": slot.target_soc_pct,
            "start_utc": slot.start_utc.isoformat(),
            "end_utc": slot.end_utc.isoformat(),
            "grid_charge_power_w": slot.grid_charge_power_w,
        })
    return result


@api_router.get("/optimization/schedule")
async def get_optimization_schedule(
    scheduler: Scheduler | None = Depends(get_scheduler),
) -> dict[str, Any]:
    """Return the active charge schedule produced by the optimiser.

    Returns
    -------
    dict
        JSON-safe representation of :class:`~backend.schedule_models.ChargeSchedule`
        with all ``datetime`` fields serialised to ISO 8601 strings.
        Includes ``stale: true`` when the schedule could not be refreshed
        from EVCC on the last poll cycle.
        Includes ``day_plans`` array when :class:`WeatherScheduler` has
        active day plans.

    Raises
    ------
    HTTPException(503)
        If ``app.state.scheduler`` is absent (scheduler not yet started).
    HTTPException(503)
        If the scheduler has not yet computed its first schedule.
    """
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not available")
    if scheduler.active_schedule is None:
        raise HTTPException(status_code=503, detail="No active schedule")
    result = _schedule_to_dict(scheduler.active_schedule)
    # Attach day_plans when WeatherScheduler provides them
    day_plans = getattr(scheduler, "active_day_plans", None)
    if day_plans is not None:
        result["day_plans"] = [
            _day_plan_to_dict(dp) for dp in day_plans
        ]
    return result


# ---------------------------------------------------------------------------
# Devices route
# ---------------------------------------------------------------------------


@api_router.get("/devices")
async def get_devices(
    orchestrator: Coordinator = Depends(get_orchestrator),
) -> dict[str, Any]:
    """Return a per-device telemetry snapshot.

    Response shape mirrors ``Coordinator.get_device_snapshot()``:

    .. code-block:: json

        {
            "huawei": {
                "available": bool,
                "pack1_soc_pct": float,
                "pack1_power_w": int,
                "pack2_soc_pct": float | null,
                "pack2_power_w": int | null,
                "total_soc_pct": float,
                "total_power_w": int,
                "max_charge_w": int,
                "max_discharge_w": int,
                "master_pv_power_w": int | null,
                "slave_pv_power_w": null
            },
            "victron": {
                "available": bool,
                "soc_pct": float,
                "battery_power_w": float,
                "l1_power_w": float,
                "l2_power_w": float,
                "l3_power_w": float,
                "l1_voltage_v": float,
                "l2_voltage_v": float,
                "l3_voltage_v": float
            }
        }
    """
    snapshot = orchestrator.get_device_snapshot()
    state = orchestrator.get_state()

    # Merge role and health data from coordinator state
    if state is not None:
        snapshot["huawei"]["role"] = getattr(state, "huawei_role", "HOLDING")
        snapshot["huawei"]["setpoint_w"] = state.huawei_discharge_setpoint_w
        snapshot["victron"]["role"] = getattr(state, "victron_role", "HOLDING")
        snapshot["victron"]["setpoint_w"] = state.victron_discharge_setpoint_w
        snapshot["pool_status"] = getattr(state, "pool_status", "NORMAL")
    else:
        snapshot["huawei"]["role"] = "HOLDING"
        snapshot["huawei"]["setpoint_w"] = 0
        snapshot["victron"]["role"] = "HOLDING"
        snapshot["victron"]["setpoint_w"] = 0
        snapshot["pool_status"] = "OFFLINE"

    return snapshot


# ---------------------------------------------------------------------------
# WebSocket real-time push
# ---------------------------------------------------------------------------


def _build_loads_dict(app: Any) -> dict[str, Any] | None:
    """Build the ``loads`` sub-dict from ``app.state.ha_rest_client``.

    Returns ``None`` when the HA REST client is absent or unconfigured
    (``loads: null`` in WS payload = client not running).

    When using :class:`MultiEntityHaClient`, returns all 8 entity fields
    (``heat_pump_power_w``, ``cop``, ``outdoor_temp_c``, etc.) with ``None``
    for entities that haven't polled yet.

    Falls back to the single-entity ``get_cached_value()`` path for backward
    compatibility with the old ``HomeAssistantClient``.
    """
    client = getattr(app.state, "ha_rest_client", None)
    if client is None:
        return None

    # Multi-entity path
    if hasattr(client, "get_all_values"):
        all_values = client.get_all_values()
        available = any(v is not None for v in all_values.values())
        return {**all_values, "available": available}

    # Single-entity fallback
    value = client.get_cached_value()
    return {
        "heat_pump_power_w": value,
        "available": value is not None,
    }


def _build_evcc_dict(app: Any) -> dict[str, Any]:
    """Build the ``evcc`` sub-dict from ``app.state.evcc_driver``.

    Returns a dict with all six EVCC fields.  Falls back to safe defaults
    when ``evcc_driver`` is not yet set on ``app.state`` (e.g. in tests that
    don't wire up the EVCC driver).
    """
    driver = getattr(app.state, "evcc_driver", None)
    if driver is None:
        return {
            "battery_mode": "normal",
            "loadpoint_mode": "off",
            "charge_power_w": 0.0,
            "vehicle_soc_pct": None,
            "charging": False,
            "connected": False,
        }
    lp = driver.evcc_loadpoint_state
    return {
        "battery_mode": driver.evcc_battery_mode,
        "loadpoint_mode": lp.mode,
        "charge_power_w": lp.charge_power_w,
        "vehicle_soc_pct": lp.vehicle_soc_pct,
        "charging": lp.charging,
        "connected": lp.connected,
    }


def _get_ha_mqtt_connected(app: Any) -> bool:
    """Return the HA MQTT connection state from ``app.state.ha_mqtt_client``.

    Falls back to ``False`` when ``ha_mqtt_client`` is not set (e.g. in tests).
    """
    client = getattr(app.state, "ha_mqtt_client", None)
    if client is None:
        return False
    return bool(client._connected)


@api_router.websocket("/ws/state")
async def ws_state(
    ws: WebSocket,
) -> None:
    """Push combined pool + devices + tariff + optimization JSON to the client every 5 s.

    The payload shape::

        {
            "pool": <UnifiedPoolState as dict, or null>,
            "devices": <device snapshot dict>,
            "tariff": {
                "effective_rate_eur_kwh": float | null,
                "octopus_rate_eur_kwh": float | null,
                "modul3_rate_eur_kwh": float | null
            },
            "optimization": <ChargeSchedule as dict, or null>
        }

    The client is removed from the active set on disconnect.  ``WebSocketDisconnect``
    raised inside the loop exits the handler cleanly (manager.disconnect called
    in the finally block).
    """
    # --- Auth check (when ADMIN_PASSWORD_HASH is set) ---
    from backend.auth import AdminConfig, verify_token

    admin_cfg = AdminConfig.from_env()
    if admin_cfg.password_hash:
        token = ws.cookies.get("ems_token", "")
        if not verify_token(token, admin_cfg.jwt_secret):
            await ws.close(code=4401)
            return

    orchestrator: Coordinator = ws.app.state.orchestrator
    tariff_engine = getattr(ws.app.state, "tariff_engine", None)

    await manager.connect(ws)
    try:
        while True:
            # --- Build pool snapshot ---
            pool_state = orchestrator.get_state()
            pool_dict = dataclasses.asdict(pool_state) if pool_state is not None else None

            # --- Build device snapshot ---
            devices_dict = orchestrator.get_device_snapshot()

            # --- Build tariff snapshot ---
            if tariff_engine is not None:
                try:
                    from datetime import datetime, timezone
                    from zoneinfo import ZoneInfo
                    now = datetime.now(tz=timezone.utc)
                    oct_tz = ZoneInfo(tariff_engine._octopus.timezone)
                    m3_tz = ZoneInfo(tariff_engine._modul3.timezone)
                    now_oct = now.astimezone(oct_tz)
                    now_m3 = now.astimezone(m3_tz)
                    oct_min = now_oct.hour * 60 + now_oct.minute
                    m3_min = now_m3.hour * 60 + now_m3.minute
                    oct_rate = tariff_engine._octopus_rate_at(oct_min)
                    m3_rate = tariff_engine._modul3_rate_at(m3_min)
                    tariff_dict: dict[str, Any] = {
                        "effective_rate_eur_kwh": round(oct_rate + m3_rate, 6),
                        "octopus_rate_eur_kwh": oct_rate,
                        "modul3_rate_eur_kwh": m3_rate,
                        "source": "live" if type(tariff_engine).__name__ == "LiveOctopusTariff" else "hardcoded",
                    }
                except Exception:
                    tariff_dict = {
                        "effective_rate_eur_kwh": None,
                        "octopus_rate_eur_kwh": None,
                        "modul3_rate_eur_kwh": None,
                        "source": "live" if type(tariff_engine).__name__ == "LiveOctopusTariff" else "hardcoded",
                    }
            else:
                tariff_dict = {
                    "effective_rate_eur_kwh": None,
                    "octopus_rate_eur_kwh": None,
                    "modul3_rate_eur_kwh": None,
                    "source": "hardcoded",
                }

            # --- Build optimization snapshot ---
            ws_scheduler = getattr(ws.app.state, "scheduler", None)
            optimization_dict: dict[str, Any] | None = (
                _schedule_to_dict(ws_scheduler.active_schedule)
                if (ws_scheduler is not None and ws_scheduler.active_schedule is not None)
                else None
            )
            if optimization_dict is not None:
                optimization_dict["forecast_comparison"] = getattr(
                    ws.app.state, "forecast_comparison", None
                )

            payload: dict[str, Any] = {
                "pool": pool_dict,
                "devices": devices_dict,
                "tariff": tariff_dict,
                "optimization": optimization_dict,
                "evcc": _build_evcc_dict(ws.app),
                "ha_mqtt_connected": _get_ha_mqtt_connected(ws.app),
                "loads": _build_loads_dict(ws.app),
            }

            try:
                await ws.send_json(payload)
            except WebSocketDisconnect:
                break
            except Exception:
                logger.warning("ws send failed — client disconnected")
                break

            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws)
