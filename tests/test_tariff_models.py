"""Unit tests for :mod:`backend.tariff_models`.

Covers the TariffSlot dataclass — the only remaining tariff model after
the Octopus/Modul3 removal.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.tariff_models import TariffSlot


class TestTariffSlot:
    def test_construction(self):
        slot = TariffSlot(
            start=datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 1, 15, 0, 15, tzinfo=timezone.utc),
            octopus_rate_eur_kwh=0.389,
            modul3_rate_eur_kwh=0.0,
            effective_rate_eur_kwh=0.389,
        )
        assert slot.effective_rate_eur_kwh == 0.389
        assert slot.modul3_rate_eur_kwh == 0.0

    def test_fields_are_accessible(self):
        slot = TariffSlot(
            start=datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc),
            end=datetime(2026, 1, 15, 17, 15, tzinfo=timezone.utc),
            octopus_rate_eur_kwh=0.355,
            modul3_rate_eur_kwh=0.0,
            effective_rate_eur_kwh=0.355,
        )
        assert slot.start.hour == 17
        assert slot.end.hour == 17
        assert slot.end.minute == 15
