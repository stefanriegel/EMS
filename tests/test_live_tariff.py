"""Tests for EvccTariffEngine.

Covers:
  - Live path: get_effective_price returns the correct EVCC slot price
  - Fallback path: when no EVCC prices available, delegates to CompositeTariffEngine
  - update() replaces the cached grid-price series
  - get_price_schedule returns TariffSlot instances covering the requested date
  - get_price_schedule falls back when EVCC data doesn't cover the date
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from backend.config import TariffConfig
from backend.schedule_models import GridPriceSeries
from backend.tariff import CompositeTariffEngine, EvccTariffEngine
from backend.tariff_models import TariffSlot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_fallback() -> CompositeTariffEngine:
    cfg = TariffConfig.from_env()
    return CompositeTariffEngine(octopus=cfg.octopus, modul3=cfg.modul3)


def _make_grid_prices(base_dt: datetime, prices: list[float]) -> GridPriceSeries:
    """Build a GridPriceSeries with 15-min slots starting at base_dt."""
    timestamps = [base_dt + timedelta(minutes=15 * i) for i in range(len(prices))]
    return GridPriceSeries(
        import_eur_kwh=prices,
        export_eur_kwh=[0.075] * len(prices),
        slot_timestamps_utc=timestamps,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_effective_price_no_evcc_data_falls_back():
    """Without EVCC prices, delegates to CompositeTariffEngine."""
    engine = EvccTariffEngine(fallback=_make_fallback())
    dt = datetime(2026, 1, 15, 1, 0, tzinfo=timezone.utc)
    expected = _make_fallback().get_effective_price(dt)
    assert abs(engine.get_effective_price(dt) - expected) < 1e-9


def test_get_effective_price_uses_evcc_slot():
    """After update(), returns the EVCC price for the matching slot."""
    engine = EvccTariffEngine(fallback=_make_fallback())
    base = datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc)  # 18:00 CET
    gp = _make_grid_prices(base, [0.389, 0.389, 0.389, 0.355])
    engine.update(gp)

    # Query at slot 0 start
    assert abs(engine.get_effective_price(base) - 0.389) < 1e-9
    # Query at slot 1 (15 min later)
    assert abs(engine.get_effective_price(base + timedelta(minutes=15)) - 0.389) < 1e-9
    # Query at slot 3 (45 min later)
    assert abs(engine.get_effective_price(base + timedelta(minutes=45)) - 0.355) < 1e-9


def test_update_replaces_cached_prices():
    """A second update() replaces the first series."""
    engine = EvccTariffEngine(fallback=_make_fallback())
    base = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
    engine.update(_make_grid_prices(base, [0.30]))
    assert abs(engine.get_effective_price(base) - 0.30) < 1e-9

    engine.update(_make_grid_prices(base, [0.42]))
    assert abs(engine.get_effective_price(base) - 0.42) < 1e-9


def test_get_price_schedule_falls_back_without_evcc():
    """get_price_schedule uses fallback when no EVCC data."""
    engine = EvccTariffEngine(fallback=_make_fallback())
    target = date(2026, 1, 15)
    slots = engine.get_price_schedule(target)
    expected = _make_fallback().get_price_schedule(target)
    assert len(slots) == len(expected)
    for s, e in zip(slots, expected):
        assert abs(s.effective_rate_eur_kwh - e.effective_rate_eur_kwh) < 1e-9


def test_get_price_schedule_uses_evcc_slots():
    """get_price_schedule returns TariffSlot instances from EVCC prices."""
    engine = EvccTariffEngine(fallback=_make_fallback())
    # Build 96 × 15-min slots covering 2026-01-15 in UTC (good enough for the
    # Europe/London timezone used by the fallback — day boundary is close enough)
    base = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
    prices = [0.389 if 17 <= (base + timedelta(minutes=15 * i)).hour < 21 else 0.355
              for i in range(96)]
    engine.update(_make_grid_prices(base, prices))

    slots = engine.get_price_schedule(date(2026, 1, 15))
    assert len(slots) > 0
    assert all(isinstance(s, TariffSlot) for s in slots)
    # All slots are from EVCC (modul3_rate == 0.0)
    assert all(s.modul3_rate_eur_kwh == 0.0 for s in slots)
    # Slots are contiguous
    for i in range(len(slots) - 1):
        assert slots[i].end == slots[i + 1].start


def test_delegate_attributes_available():
    """_octopus, _modul3, _octopus_rate_at, _modul3_rate_at are accessible."""
    engine = EvccTariffEngine(fallback=_make_fallback())
    assert engine._octopus is not None
    assert engine._modul3 is not None
    assert isinstance(engine._octopus_rate_at(0), float)
    assert isinstance(engine._modul3_rate_at(0), float)
