"""Tests for backend/tariff_models.py — Modul3Window, OctopusGoConfig,
Modul3Config, TariffSlot."""
from __future__ import annotations

from datetime import datetime, timezone


class TestModul3Window:
    def test_field_types_and_values(self):
        from backend.tariff_models import Modul3Window
        w = Modul3Window(start_min=0, end_min=360, rate_eur_kwh=0.05, tier="NT")
        assert w.start_min == 0
        assert w.end_min == 360
        assert w.rate_eur_kwh == 0.05
        assert w.tier == "NT"

    def test_round_trip(self):
        from backend.tariff_models import Modul3Window
        w = Modul3Window(start_min=420, end_min=1200, rate_eur_kwh=0.12, tier="HT")
        assert w.start_min == 420
        assert w.end_min == 1200
        assert w.rate_eur_kwh == 0.12
        assert w.tier == "HT"


class TestOctopusGoConfig:
    def test_default_timezone_is_europe_london(self):
        from backend.tariff_models import OctopusGoConfig
        cfg = OctopusGoConfig(
            off_peak_start_min=30,
            off_peak_end_min=270,
            off_peak_rate_eur_kwh=0.07,
            peak_rate_eur_kwh=0.25,
        )
        assert cfg.timezone == "Europe/London"

    def test_all_required_fields_set(self):
        from backend.tariff_models import OctopusGoConfig
        cfg = OctopusGoConfig(
            off_peak_start_min=0,
            off_peak_end_min=480,
            off_peak_rate_eur_kwh=0.08,
            peak_rate_eur_kwh=0.30,
        )
        assert cfg.off_peak_start_min == 0
        assert cfg.off_peak_end_min == 480
        assert cfg.off_peak_rate_eur_kwh == 0.08
        assert cfg.peak_rate_eur_kwh == 0.30


class TestModul3Config:
    def test_default_timezone_is_europe_berlin(self):
        from backend.tariff_models import Modul3Config
        cfg = Modul3Config(windows=[])
        assert cfg.timezone == "Europe/Berlin"

    def test_stores_windows(self):
        from backend.tariff_models import Modul3Config, Modul3Window
        w = Modul3Window(start_min=0, end_min=360, rate_eur_kwh=0.04, tier="NT")
        cfg = Modul3Config(windows=[w])
        assert len(cfg.windows) == 1
        assert cfg.windows[0].tier == "NT"


class TestTariffSlot:
    def test_all_five_required_fields(self):
        from backend.tariff_models import TariffSlot
        now = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
        slot = TariffSlot(
            start=now,
            end=now,
            octopus_rate_eur_kwh=0.07,
            modul3_rate_eur_kwh=0.05,
            effective_rate_eur_kwh=0.12,
        )
        assert slot.octopus_rate_eur_kwh == 0.07
        assert slot.modul3_rate_eur_kwh == 0.05
        assert slot.effective_rate_eur_kwh == 0.12

    def test_effective_rate_equals_sum(self):
        from backend.tariff_models import TariffSlot
        now = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
        octopus = 0.08
        modul3 = 0.06
        slot = TariffSlot(
            start=now,
            end=now,
            octopus_rate_eur_kwh=octopus,
            modul3_rate_eur_kwh=modul3,
            effective_rate_eur_kwh=octopus + modul3,
        )
        assert slot.effective_rate_eur_kwh == pytest.approx(0.14)


import pytest
