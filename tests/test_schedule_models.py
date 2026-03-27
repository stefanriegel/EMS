"""Tests for backend/schedule_models.py — EvoptResult, ChargeSlot, ChargeSchedule,
HourlyConsumptionForecast, and related dataclasses."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bat(title: str, charge: list[float], discharge: list[float]):
    from backend.schedule_models import EvoptBatteryTimeseries
    n = max(len(charge), len(discharge))
    charge = (charge + [0.0] * n)[:n]
    discharge = (discharge + [0.0] * n)[:n]
    ts = [datetime(2025, 1, 1, tzinfo=timezone.utc)] * n
    return EvoptBatteryTimeseries(
        title=title,
        charging_power_w=charge,
        discharging_power_w=discharge,
        soc_fraction=[0.5] * n,
        slot_timestamps_utc=ts,
    )


# ---------------------------------------------------------------------------
# EvoptResult.get_huawei_target_soc_pct
# ---------------------------------------------------------------------------

class TestEvoptResultHuawei:
    def test_returns_initial_soc_when_no_emma_batteries(self):
        from backend.schedule_models import EvoptResult
        result = EvoptResult(
            status="Optimal",
            objective_value=0.0,
            batteries=[_make_bat("Victron", [1000.0] * 4, [0.0] * 4)],
        )
        assert result.get_huawei_target_soc_pct(30.0, initial_soc_pct=50.0) == 50.0

    def test_computes_target_from_net_charge(self):
        from backend.schedule_models import EvoptResult
        # 4 slots of 1000 W charging at 15-min resolution = 4 * 1000 * 0.25 = 1000 Wh
        # on a 30 kWh battery from 0% SoC → delta = 1000/30000 * 100 ≈ 3.33%
        bat = _make_bat("Emma Akku 1", [1000.0] * 4, [0.0] * 4)
        result = EvoptResult(status="Optimal", objective_value=0.0, batteries=[bat])
        target = result.get_huawei_target_soc_pct(30.0, initial_soc_pct=0.0)
        # 1000 Wh / 30000 Wh * 100 = 3.333...% but clamped to min 10.0
        assert target == 10.0

    def test_clamps_to_95(self):
        from backend.schedule_models import EvoptResult
        # 96 slots of 10000 W charging = enormous charge → must clamp to 95
        bat = _make_bat("Emma Akku 1", [10000.0] * 96, [0.0] * 96)
        result = EvoptResult(status="Optimal", objective_value=0.0, batteries=[bat])
        target = result.get_huawei_target_soc_pct(30.0, initial_soc_pct=0.0)
        assert target == 95.0


# ---------------------------------------------------------------------------
# EvoptResult.get_victron_target_soc_pct
# ---------------------------------------------------------------------------

class TestEvoptResultVictron:
    def test_returns_initial_soc_when_no_victron_battery(self):
        from backend.schedule_models import EvoptResult
        result = EvoptResult(
            status="Optimal",
            objective_value=0.0,
            batteries=[_make_bat("Emma Akku 1", [1000.0] * 4, [0.0] * 4)],
        )
        assert result.get_victron_target_soc_pct(64.0, initial_soc_pct=40.0) == 40.0

    def test_computes_target_from_net_charge(self):
        from backend.schedule_models import EvoptResult
        # 96 slots of 640 W charging on 64 kWh = 96*640*0.25=15360 Wh
        # delta = 15360/64000 * 100 = 24%
        bat = _make_bat("Victron", [640.0] * 96, [0.0] * 96)
        result = EvoptResult(status="Optimal", objective_value=0.0, batteries=[bat])
        target = result.get_victron_target_soc_pct(64.0, initial_soc_pct=50.0)
        assert 70.0 <= target <= 75.0  # 50+24=74%

    def test_clamps_below_10(self):
        from backend.schedule_models import EvoptResult
        # heavy discharge from 0% → clamps to 10
        bat = _make_bat("Victron", [0.0] * 96, [10000.0] * 96)
        result = EvoptResult(status="Optimal", objective_value=0.0, batteries=[bat])
        target = result.get_victron_target_soc_pct(64.0, initial_soc_pct=0.0)
        assert target == 10.0


# ---------------------------------------------------------------------------
# ChargeSlot defaults
# ---------------------------------------------------------------------------

class TestChargeSlot:
    def test_all_required_fields_constructible(self):
        from backend.schedule_models import ChargeSlot
        now = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
        slot = ChargeSlot(
            battery="huawei",
            target_soc_pct=80.0,
            start_utc=now,
            end_utc=now,
            grid_charge_power_w=3000,
        )
        assert slot.battery == "huawei"
        assert slot.grid_charge_power_w == 3000


# ---------------------------------------------------------------------------
# ChargeSchedule defaults
# ---------------------------------------------------------------------------

class TestChargeSchedule:
    def test_stale_defaults_to_false(self):
        from backend.schedule_models import ChargeSchedule, OptimizationReasoning
        now = datetime(2025, 6, 1, tzinfo=timezone.utc)
        reasoning = OptimizationReasoning(
            text="test",
            tomorrow_solar_kwh=10.0,
            expected_consumption_kwh=8.0,
            charge_energy_kwh=2.0,
            cost_estimate_eur=0.5,
        )
        schedule = ChargeSchedule(slots=[], reasoning=reasoning, computed_at=now)
        assert schedule.stale is False


# ---------------------------------------------------------------------------
# HourlyConsumptionForecast — watts_by_weekday / default fields
# ---------------------------------------------------------------------------

class TestHourlyConsumptionForecast:
    def test_all_required_fields_constructible(self):
        from backend.schedule_models import HourlyConsumptionForecast
        fcst = HourlyConsumptionForecast(
            hourly_kwh=[0.5] * 24,
            total_kwh=12.0,
            horizon_hours=24,
            source="ml",
            fallback_used=False,
        )
        assert len(fcst.hourly_kwh) == 24
        assert fcst.source == "ml"
        assert fcst.fallback_used is False
