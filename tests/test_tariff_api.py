"""API tests for the tariff endpoints — EVCC-only engine.

The EvccTariffEngine is injected via dependency_overrides.
Tests cover: price with data, price without data (503), schedule with data,
schedule empty, and invalid input handling.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from backend.api import api_router, get_tariff_engine
from backend.schedule_models import GridPriceSeries
from backend.tariff import EvccTariffEngine


def _make_engine_with_prices() -> EvccTariffEngine:
    """Build an EvccTariffEngine with 96 × 15-min slots for 2026-01-15 UTC."""
    engine = EvccTariffEngine()
    base = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
    prices = [0.355 if i < 68 else 0.389 for i in range(96)]  # HT from 17:00
    timestamps = [base + timedelta(minutes=15 * i) for i in range(96)]
    engine.update(GridPriceSeries(
        import_eur_kwh=prices,
        export_eur_kwh=[0.075] * 96,
        slot_timestamps_utc=timestamps,
    ))
    return engine


def _build_tariff_test_app(engine: EvccTariffEngine | None = None) -> Any:
    app = FastAPI(title="EMS-tariff-test")
    app.include_router(api_router)
    app.dependency_overrides[get_tariff_engine] = lambda: engine or EvccTariffEngine()
    return app


@pytest.fixture()
def tariff_app() -> Any:
    return _build_tariff_test_app(_make_engine_with_prices())


@pytest.fixture()
def empty_tariff_app() -> Any:
    return _build_tariff_test_app(EvccTariffEngine())


# ---------------------------------------------------------------------------
# GET /api/tariff/price
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_price_with_evcc_data(tariff_app: Any) -> None:
    """Price endpoint returns EVCC rate for the matching slot."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=tariff_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/tariff/price", params={"dt": "2026-01-15T02:00:00+00:00"})

    assert resp.status_code == 200
    data = resp.json()
    assert abs(data["effective_rate_eur_kwh"] - 0.355) < 1e-6


@pytest.mark.anyio
async def test_price_returns_503_without_data(empty_tariff_app: Any) -> None:
    """Price endpoint returns 503 when no EVCC data is available."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=empty_tariff_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/tariff/price", params={"dt": "2026-01-15T02:00:00+00:00"})

    assert resp.status_code == 503


@pytest.mark.anyio
async def test_price_invalid_dt(tariff_app: Any) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=tariff_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/tariff/price", params={"dt": "not-a-date"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/tariff/schedule
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_schedule_with_evcc_data(tariff_app: Any) -> None:
    """Schedule returns slots from EVCC prices."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=tariff_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/tariff/schedule", params={"date": "2026-01-15"})

    assert resp.status_code == 200
    slots = resp.json()
    assert len(slots) > 0
    assert "effective_rate_eur_kwh" in slots[0]
    # Contiguity
    for i in range(1, len(slots)):
        assert slots[i]["start"] == slots[i - 1]["end"]


@pytest.mark.anyio
async def test_schedule_empty_without_data(empty_tariff_app: Any) -> None:
    """Schedule returns empty list when no EVCC data."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=empty_tariff_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/tariff/schedule", params={"date": "2026-01-15"})

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_schedule_invalid_date(tariff_app: Any) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=tariff_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/tariff/schedule", params={"date": "not-a-date"})
    assert resp.status_code == 400
