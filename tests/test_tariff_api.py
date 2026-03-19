"""Async API tests for the tariff endpoints (S04 T02).

Uses the same ``httpx.AsyncClient`` + ``ASGITransport`` pattern as
``tests/test_api.py``.  The engine is injected via
``app.dependency_overrides[get_tariff_engine]`` — no lifespan required.

Canonical engine config matches the T01 test fixtures exactly:
  - OctopusGoConfig: off_peak 00:30–05:30 London, 0.08 €/kWh off-peak, 0.28 peak
  - Modul3Config: NT 0–360, ST 360–1020, HT 1020–1200, ST 1200–1440 (Berlin)

Timezone context for 2026-01-15 (winter, no DST):
  London UTC+0 / Berlin UTC+1
  → 02:00 London = 03:00 Berlin → off-peak + NT → 0.08 + 0.026 = 0.106 €/kWh
  → 18:00 London = 19:00 Berlin → peak + HT → 0.28 + 0.125 = 0.405 €/kWh
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from backend.api import api_router, get_tariff_engine
from backend.tariff import CompositeTariffEngine
from backend.tariff_models import Modul3Config, Modul3Window, OctopusGoConfig

# ---------------------------------------------------------------------------
# Canonical test engine
# ---------------------------------------------------------------------------

_OCTOPUS = OctopusGoConfig(
    off_peak_start_min=30,
    off_peak_end_min=330,
    off_peak_rate_eur_kwh=0.08,
    peak_rate_eur_kwh=0.28,
    timezone="Europe/London",
)
_MODUL3 = Modul3Config(
    windows=[
        Modul3Window(start_min=0, end_min=360, rate_eur_kwh=0.026, tier="NT"),
        Modul3Window(start_min=360, end_min=1020, rate_eur_kwh=0.087, tier="ST"),
        Modul3Window(start_min=1020, end_min=1200, rate_eur_kwh=0.125, tier="HT"),
        Modul3Window(start_min=1200, end_min=1440, rate_eur_kwh=0.087, tier="ST"),
    ],
    timezone="Europe/Berlin",
)
_ENGINE = CompositeTariffEngine(octopus=_OCTOPUS, modul3=_MODUL3)


# ---------------------------------------------------------------------------
# Test app factory
# ---------------------------------------------------------------------------


def _build_tariff_test_app(engine: CompositeTariffEngine = _ENGINE) -> Any:
    """Build a minimal FastAPI app with the tariff engine injected via DI."""
    app = FastAPI(title="EMS-tariff-test")
    app.include_router(api_router)
    app.dependency_overrides[get_tariff_engine] = lambda: engine
    return app


@pytest.fixture()
def tariff_app() -> Any:
    """Test app wired to the canonical tariff engine."""
    return _build_tariff_test_app()


# ---------------------------------------------------------------------------
# GET /api/tariff/price
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_price_off_peak(tariff_app: Any) -> None:
    """02:00 London → off-peak + NT → effective ≈ 0.106 €/kWh."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=tariff_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/tariff/price", params={"dt": "2026-01-15T02:00:00"})

    assert resp.status_code == 200
    data = resp.json()
    assert abs(data["effective_rate_eur_kwh"] - (0.08 + 0.026)) < 1e-6
    assert abs(data["octopus_rate_eur_kwh"] - 0.08) < 1e-9
    assert abs(data["modul3_rate_eur_kwh"] - 0.026) < 1e-9
    assert data["dt"] == "2026-01-15T02:00:00"


@pytest.mark.anyio
async def test_price_peak_evening(tariff_app: Any) -> None:
    """18:00 London → peak + HT → effective ≈ 0.405 €/kWh."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=tariff_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/tariff/price", params={"dt": "2026-01-15T18:00:00"})

    assert resp.status_code == 200
    data = resp.json()
    assert abs(data["effective_rate_eur_kwh"] - (0.28 + 0.125)) < 1e-6
    assert abs(data["octopus_rate_eur_kwh"] - 0.28) < 1e-9
    assert abs(data["modul3_rate_eur_kwh"] - 0.125) < 1e-9


@pytest.mark.anyio
async def test_price_invalid_dt(tariff_app: Any) -> None:
    """Non-parseable dt param returns 400, not 422 or 500."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=tariff_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/tariff/price", params={"dt": "not-a-date"})

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/tariff/schedule
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_schedule_full_day(tariff_app: Any) -> None:
    """Schedule for 2026-01-15 returns a non-empty list with valid structure."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=tariff_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/tariff/schedule", params={"date": "2026-01-15"})

    assert resp.status_code == 200
    slots = resp.json()
    assert isinstance(slots, list)
    assert len(slots) > 0

    # First slot starts on 2026-01-15
    assert "2026-01-15" in slots[0]["start"]

    # All slots: effective == octopus + modul3
    for slot in slots:
        expected = slot["octopus_rate_eur_kwh"] + slot["modul3_rate_eur_kwh"]
        assert abs(slot["effective_rate_eur_kwh"] - expected) < 1e-9, (
            f"Slot {slot['start']}–{slot['end']}: "
            f"effective={slot['effective_rate_eur_kwh']} != {expected}"
        )

    # Contiguity: each slot's end == next slot's start
    for i in range(1, len(slots)):
        assert slots[i]["start"] == slots[i - 1]["end"], (
            f"Gap between slot {i-1} and {i}: "
            f"{slots[i-1]['end']} vs {slots[i]['start']}"
        )


@pytest.mark.anyio
async def test_schedule_invalid_date(tariff_app: Any) -> None:
    """Non-parseable date param returns 400."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=tariff_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/tariff/schedule", params={"date": "not-a-date"})

    assert resp.status_code == 400
