"""Tests for new WS payload fields: tariff.source and optimization.forecast_comparison.

These tests use the sync ``starlette.testclient.TestClient`` pattern
(same as ``test_api.py``) so they are plain ``def`` functions compatible
with the existing anyio/pytest-asyncio configuration.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Shared helpers — minimal stubs copied from test_api.py pattern
# ---------------------------------------------------------------------------


def _make_mock_orchestrator():
    """Return a minimal orchestrator stub that satisfies ws_state."""
    orch = MagicMock()
    orch.get_state.return_value = None
    orch.get_device_snapshot.return_value = {"huawei": {}, "victron": {}}
    return orch


def _make_app(*, tariff_engine=None, scheduler=None, forecast_comparison=None):
    """Build a minimal FastAPI app wired for WS tests."""
    from fastapi import FastAPI
    from backend.api import api_router, get_orchestrator

    orch = _make_mock_orchestrator()
    app = FastAPI(title="EMS-ws-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.state.orchestrator = orch
    if tariff_engine is not None:
        app.state.tariff_engine = tariff_engine
    if scheduler is not None:
        app.state.scheduler = scheduler
    if forecast_comparison is not None:
        app.state.forecast_comparison = forecast_comparison
    return app


# ---------------------------------------------------------------------------
# Tests: tariff.source
# ---------------------------------------------------------------------------


def test_ws_tariff_source_is_none_by_default():
    """WS tariff dict contains 'source': 'none' when no tariff engine or EVCC is set."""
    from starlette.testclient import TestClient

    app = _make_app()
    # No tariff_engine or scheduler on app.state → none branch

    with TestClient(app).websocket_connect("/api/ws/state") as ws:
        data = ws.receive_json()

    assert "tariff" in data, f"Missing 'tariff' key: {data}"
    tariff = data["tariff"]
    assert "source" in tariff, f"'source' field missing from tariff dict: {tariff}"
    assert tariff["source"] == "none", (
        f"Expected source='none' with no engine, got {tariff['source']!r}"
    )


def test_ws_tariff_source_is_evcc_when_scheduler_has_prices():
    """WS tariff dict contains 'source': 'evcc' when scheduler has grid prices."""
    from datetime import datetime, timedelta, timezone
    from starlette.testclient import TestClient
    from backend.schedule_models import GridPriceSeries

    # Create a mock scheduler with EVCC grid prices
    mock_scheduler = MagicMock()
    mock_scheduler.active_schedule = None  # prevent dataclasses.asdict crash
    base = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
    mock_scheduler.last_grid_prices = GridPriceSeries(
        import_eur_kwh=[0.389] * 96,
        export_eur_kwh=[0.075] * 96,
        slot_timestamps_utc=[base + timedelta(minutes=15 * i) for i in range(96)],
    )

    app = _make_app(scheduler=mock_scheduler)

    with TestClient(app).websocket_connect("/api/ws/state") as ws:
        data = ws.receive_json()

    tariff = data["tariff"]
    assert "source" in tariff, f"'source' field missing from tariff dict: {tariff}"
    assert tariff["source"] == "evcc", (
        f"Expected source='evcc' with EVCC grid prices, got {tariff['source']!r}"
    )


# ---------------------------------------------------------------------------
# Tests: optimization.forecast_comparison
# ---------------------------------------------------------------------------


def test_ws_optimization_has_forecast_comparison_field():
    """WS optimization dict includes forecast_comparison from app.state when set."""
    from starlette.testclient import TestClient
    from backend.schedule_models import ChargeSchedule, ChargeSlot, OptimizationReasoning
    from datetime import datetime, timezone

    # Build a minimal active schedule so optimization_dict is non-null
    slot = ChargeSlot(
        battery="huawei",
        target_soc_pct=90.0,
        start_utc=datetime(2026, 1, 15, 1, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 1, 15, 5, 30, tzinfo=timezone.utc),
        grid_charge_power_w=3500,
    )
    reasoning = OptimizationReasoning(
        text="Test schedule for WS payload test.",
        tomorrow_solar_kwh=10.0,
        expected_consumption_kwh=8.0,
        charge_energy_kwh=4.0,
        cost_estimate_eur=0.70,
    )
    schedule = ChargeSchedule(
        slots=[slot],
        reasoning=reasoning,
        computed_at=datetime(2026, 1, 14, 22, 0, tzinfo=timezone.utc),
        stale=False,
    )

    class _MockScheduler:
        active_schedule = schedule

    expected_comparison = {"predicted_kwh": 18.2, "actual_kwh": 19.7, "error_pct": 7.6}

    app = _make_app(
        scheduler=_MockScheduler(),
        forecast_comparison=expected_comparison,
    )

    with TestClient(app).websocket_connect("/api/ws/state") as ws:
        data = ws.receive_json()

    assert "optimization" in data, f"Missing 'optimization' key: {data}"
    optimization = data["optimization"]
    assert optimization is not None, "Expected non-null optimization dict with active schedule"
    assert "forecast_comparison" in optimization, (
        f"'forecast_comparison' missing from optimization dict: {optimization}"
    )
    fc = optimization["forecast_comparison"]
    assert fc["predicted_kwh"] == pytest.approx(18.2)
    assert fc["actual_kwh"] == pytest.approx(19.7)
    assert fc["error_pct"] == pytest.approx(7.6)
