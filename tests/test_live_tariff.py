"""Tests for EvccTariffEngine.

Covers:
  - Live path: get_effective_price returns the correct EVCC slot price
  - No-data path: returns None when no EVCC prices available
  - update() replaces the cached grid-price series
  - get_price_schedule returns TariffSlot instances covering the requested date
  - get_price_schedule returns empty list when no data covers the date
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from backend.schedule_models import GridPriceSeries
from backend.tariff import EvccTariffEngine
from backend.tariff_models import TariffSlot


def _make_grid_prices(base_dt: datetime, prices: list[float]) -> GridPriceSeries:
    """Build a GridPriceSeries with 15-min slots starting at base_dt."""
    timestamps = [base_dt + timedelta(minutes=15 * i) for i in range(len(prices))]
    return GridPriceSeries(
        import_eur_kwh=prices,
        export_eur_kwh=[0.075] * len(prices),
        slot_timestamps_utc=timestamps,
    )


def test_no_evcc_data_returns_none():
    """Without EVCC prices, get_effective_price returns None."""
    engine = EvccTariffEngine()
    dt = datetime(2026, 1, 15, 1, 0, tzinfo=timezone.utc)
    assert engine.get_effective_price(dt) is None


def test_get_effective_price_uses_evcc_slot():
    """After update(), returns the EVCC price for the matching slot."""
    engine = EvccTariffEngine()
    base = datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc)
    gp = _make_grid_prices(base, [0.389, 0.389, 0.389, 0.355])
    engine.update(gp)

    assert abs(engine.get_effective_price(base) - 0.389) < 1e-9
    assert abs(engine.get_effective_price(base + timedelta(minutes=45)) - 0.355) < 1e-9


def test_update_replaces_cached_prices():
    """A second update() replaces the first series."""
    engine = EvccTariffEngine()
    base = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
    engine.update(_make_grid_prices(base, [0.30]))
    assert abs(engine.get_effective_price(base) - 0.30) < 1e-9

    engine.update(_make_grid_prices(base, [0.42]))
    assert abs(engine.get_effective_price(base) - 0.42) < 1e-9


def test_get_price_schedule_empty_without_evcc():
    """get_price_schedule returns empty list when no EVCC data."""
    engine = EvccTariffEngine()
    assert engine.get_price_schedule(date(2026, 1, 15)) == []


def test_get_price_schedule_uses_evcc_slots():
    """get_price_schedule returns TariffSlot instances from EVCC prices."""
    engine = EvccTariffEngine()
    base = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
    prices = [0.389 if 17 <= (base + timedelta(minutes=15 * i)).hour < 21 else 0.355
              for i in range(96)]
    engine.update(_make_grid_prices(base, prices))

    slots = engine.get_price_schedule(date(2026, 1, 15))
    assert len(slots) > 0
    assert all(isinstance(s, TariffSlot) for s in slots)
    assert all(s.modul3_rate_eur_kwh == 0.0 for s in slots)
    for i in range(len(slots) - 1):
        assert slots[i].end == slots[i + 1].start
