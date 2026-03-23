"""Unit tests for the ExportAdvisor module.

Tests cover:
- SoC threshold gating (STORE when < 90%)
- Economic comparison (EXPORT when batteries full and favorable)
- Forecaster degradation (STORE when unavailable or fallback)
- ExportDecision enum and ExportAdvice dataclass contracts
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from backend.export_advisor import ExportAdvice, ExportAdvisor, ExportDecision
from backend.config import SystemConfig
from backend.schedule_models import ConsumptionForecast
from backend.tariff_models import TariffSlot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TZ = ZoneInfo("Europe/Berlin")


def _make_tariff_slot(
    hour: int,
    effective_rate: float,
    target_date: date | None = None,
) -> TariffSlot:
    """Build a minimal TariffSlot for a single hour."""
    d = target_date or date(2026, 3, 23)
    start = datetime.combine(d, time(hour, 0), tzinfo=_TZ)
    end = start + timedelta(hours=1)
    return TariffSlot(
        start=start,
        end=end,
        octopus_rate_eur_kwh=effective_rate * 0.7,
        modul3_rate_eur_kwh=effective_rate * 0.3,
        effective_rate_eur_kwh=effective_rate,
    )


def _make_forecast(
    today_kwh: float = 20.0,
    fallback_used: bool = False,
) -> ConsumptionForecast:
    return ConsumptionForecast(
        kwh_by_weekday={},
        today_expected_kwh=today_kwh,
        days_of_history=30,
        fallback_used=fallback_used,
    )


def _make_advisor(
    feed_in_rate: float = 0.074,
    cached_forecast: ConsumptionForecast | None = None,
    schedule_rates: list[float] | None = None,
    effective_price: float = 0.35,
    forecaster_available: bool = True,
) -> ExportAdvisor:
    """Build an ExportAdvisor with mocked dependencies and pre-set cache."""
    tariff_engine = MagicMock()
    tariff_engine.get_effective_price.return_value = effective_price

    # Build a 24-hour schedule if rates provided, otherwise use a default
    if schedule_rates is None:
        schedule_rates = [0.35] * 24
    slots = [_make_tariff_slot(h, r) for h, r in enumerate(schedule_rates)]
    tariff_engine.get_price_schedule.return_value = slots

    forecaster = MagicMock() if forecaster_available else None

    sys_config = SystemConfig(feed_in_rate_eur_kwh=feed_in_rate)

    advisor = ExportAdvisor(
        tariff_engine=tariff_engine,
        forecaster=forecaster,
        sys_config=sys_config,
    )

    if cached_forecast is not None:
        advisor._cached_forecast = cached_forecast

    return advisor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExportDecisionEnum:
    def test_export_decision_enum_values(self) -> None:
        assert ExportDecision.STORE == "STORE"
        assert ExportDecision.EXPORT == "EXPORT"
        assert len(ExportDecision) == 2


class TestExportAdviceDataclass:
    def test_export_advice_dataclass_fields(self) -> None:
        advice = ExportAdvice(
            decision=ExportDecision.STORE,
            reasoning="test",
            feed_in_rate=0.074,
            import_rate=0.35,
            forecast_demand_kwh=20.0,
            battery_soc_pct=50.0,
        )
        assert advice.decision == ExportDecision.STORE
        assert advice.reasoning == "test"
        assert advice.feed_in_rate == 0.074
        assert advice.import_rate == 0.35
        assert advice.forecast_demand_kwh == 20.0
        assert advice.battery_soc_pct == 50.0


class TestExportAdvisorSocGating:
    def test_store_when_soc_below_threshold(self) -> None:
        advisor = _make_advisor(cached_forecast=_make_forecast())
        now = datetime(2026, 3, 23, 12, 0, tzinfo=_TZ)
        advice = advisor.advise(
            combined_soc_pct=80.0,
            huawei_soc_pct=75.0,
            victron_soc_pct=83.0,
            now=now,
        )
        assert advice.decision == ExportDecision.STORE

    def test_store_when_soc_at_50_pct(self) -> None:
        advisor = _make_advisor(cached_forecast=_make_forecast())
        now = datetime(2026, 3, 23, 12, 0, tzinfo=_TZ)
        advice = advisor.advise(
            combined_soc_pct=50.0,
            huawei_soc_pct=50.0,
            victron_soc_pct=50.0,
            now=now,
        )
        assert advice.decision == ExportDecision.STORE


class TestExportAdvisorForecasterDegradation:
    def test_store_when_forecaster_unavailable(self) -> None:
        advisor = _make_advisor(forecaster_available=False)
        now = datetime(2026, 3, 23, 12, 0, tzinfo=_TZ)
        advice = advisor.advise(
            combined_soc_pct=95.0,
            huawei_soc_pct=95.0,
            victron_soc_pct=95.0,
            now=now,
        )
        assert advice.decision == ExportDecision.STORE
        assert "forecaster unavailable" in advice.reasoning.lower()

    def test_store_when_fallback_used(self) -> None:
        advisor = _make_advisor(
            cached_forecast=_make_forecast(fallback_used=True),
        )
        now = datetime(2026, 3, 23, 12, 0, tzinfo=_TZ)
        advice = advisor.advise(
            combined_soc_pct=95.0,
            huawei_soc_pct=95.0,
            victron_soc_pct=95.0,
            now=now,
        )
        assert advice.decision == ExportDecision.STORE


class TestExportAdvisorEconomicDecision:
    def test_export_when_batteries_full_and_economically_favorable(self) -> None:
        """When SoC >= 95% and forward reserve is small, advise EXPORT."""
        # Low consumption forecast = little forward demand
        advisor = _make_advisor(
            cached_forecast=_make_forecast(today_kwh=5.0),
            # All hours cheap - no expensive import upcoming
            schedule_rates=[0.10] * 24,
            effective_price=0.10,
        )
        now = datetime(2026, 3, 23, 12, 0, tzinfo=_TZ)
        advice = advisor.advise(
            combined_soc_pct=95.0,
            huawei_soc_pct=95.0,
            victron_soc_pct=95.0,
            now=now,
        )
        assert advice.decision == ExportDecision.EXPORT

    def test_store_when_future_import_expensive(self) -> None:
        """When upcoming hours are expensive, store to avoid buyback trap."""
        # Very high consumption + all upcoming hours expensive
        # At 90% SoC: available = 84.6 kWh
        # Forward reserve: 6 expensive hours * (600/24) kWh/h = 150 kWh
        # surplus = 84.6 - 150 = -65.4 => STORE
        rates = [0.50] * 24  # all hours expensive (above feed-in 0.074)
        advisor = _make_advisor(
            cached_forecast=_make_forecast(today_kwh=600.0),
            schedule_rates=rates,
            effective_price=0.50,
        )
        now = datetime(2026, 3, 23, 10, 0, tzinfo=_TZ)
        advice = advisor.advise(
            combined_soc_pct=90.0,
            huawei_soc_pct=90.0,
            victron_soc_pct=90.0,
            now=now,
        )
        assert advice.decision == ExportDecision.STORE


class TestExportAdvisorReasoning:
    def test_reasoning_includes_all_fields(self) -> None:
        advisor = _make_advisor(cached_forecast=_make_forecast())
        now = datetime(2026, 3, 23, 12, 0, tzinfo=_TZ)
        advice = advisor.advise(
            combined_soc_pct=95.0,
            huawei_soc_pct=95.0,
            victron_soc_pct=95.0,
            now=now,
        )
        reasoning_lower = advice.reasoning.lower()
        assert "feed_in_rate" in reasoning_lower or "feed-in" in reasoning_lower
        assert "import" in reasoning_lower
        assert "forecast" in reasoning_lower or "demand" in reasoning_lower
        assert "soc" in reasoning_lower
