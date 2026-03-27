"""Unit tests for :mod:`backend.tariff` EvccTariffEngine.

These tests verify the EVCC-only tariff engine. The old CompositeTariffEngine
has been removed — tariff is now fully sourced from EVCC grid prices.

See also: tests/test_live_tariff.py for additional EvccTariffEngine coverage.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from backend.schedule_models import GridPriceSeries
from backend.tariff import EvccTariffEngine
from backend.tariff_models import TariffSlot


def _make_prices(
    base: datetime, prices: list[float]
) -> GridPriceSeries:
    """Build a GridPriceSeries with 15-min slots."""
    return GridPriceSeries(
        import_eur_kwh=prices,
        export_eur_kwh=[0.075] * len(prices),
        slot_timestamps_utc=[base + timedelta(minutes=15 * i) for i in range(len(prices))],
    )


class TestEvccTariffEngine:
    """Core EvccTariffEngine behaviour."""

    def test_no_data_returns_none(self):
        engine = EvccTariffEngine()
        assert engine.get_effective_price(datetime.now(tz=timezone.utc)) is None

    def test_no_data_schedule_empty(self):
        engine = EvccTariffEngine()
        assert engine.get_price_schedule(date.today()) == []

    def test_price_lookup_first_slot(self):
        engine = EvccTariffEngine()
        base = datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
        engine.update(_make_prices(base, [0.30, 0.35, 0.40]))
        assert engine.get_effective_price(base) == pytest.approx(0.30)

    def test_price_lookup_mid_slot(self):
        engine = EvccTariffEngine()
        base = datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
        engine.update(_make_prices(base, [0.30, 0.35, 0.40]))
        assert engine.get_effective_price(base + timedelta(minutes=20)) == pytest.approx(0.35)

    def test_price_lookup_last_slot(self):
        engine = EvccTariffEngine()
        base = datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
        engine.update(_make_prices(base, [0.30, 0.35, 0.40]))
        assert engine.get_effective_price(base + timedelta(minutes=40)) == pytest.approx(0.40)

    def test_update_replaces_prices(self):
        engine = EvccTariffEngine()
        base = datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
        engine.update(_make_prices(base, [0.30]))
        assert engine.get_effective_price(base) == pytest.approx(0.30)
        engine.update(_make_prices(base, [0.50]))
        assert engine.get_effective_price(base) == pytest.approx(0.50)

    def test_schedule_returns_tariff_slots(self):
        engine = EvccTariffEngine()
        base = datetime(2026, 3, 27, 0, 0, tzinfo=timezone.utc)
        engine.update(_make_prices(base, [0.30] * 96))
        slots = engine.get_price_schedule(date(2026, 3, 27))
        assert len(slots) > 0
        assert all(isinstance(s, TariffSlot) for s in slots)

    def test_schedule_slots_contiguous(self):
        engine = EvccTariffEngine()
        base = datetime(2026, 3, 27, 0, 0, tzinfo=timezone.utc)
        engine.update(_make_prices(base, [0.30] * 96))
        slots = engine.get_price_schedule(date(2026, 3, 27))
        for i in range(len(slots) - 1):
            assert slots[i].end == slots[i + 1].start

    def test_schedule_modul3_always_zero(self):
        engine = EvccTariffEngine()
        base = datetime(2026, 3, 27, 0, 0, tzinfo=timezone.utc)
        engine.update(_make_prices(base, [0.389] * 96))
        slots = engine.get_price_schedule(date(2026, 3, 27))
        assert all(s.modul3_rate_eur_kwh == 0.0 for s in slots)

    def test_schedule_empty_for_uncovered_date(self):
        engine = EvccTariffEngine()
        base = datetime(2026, 3, 27, 0, 0, tzinfo=timezone.utc)
        engine.update(_make_prices(base, [0.30] * 96))
        # Ask for a date the prices don't cover
        assert engine.get_price_schedule(date(2026, 4, 1)) == []
