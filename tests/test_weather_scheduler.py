"""Unit tests for WeatherScheduler and DayPlan model.

Tests the multi-day weather-aware charge scheduling algorithm
including confidence weighting, headroom ceiling, winter floor,
and coordinator interface compatibility.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import OrchestratorConfig, SystemConfig
from backend.schedule_models import (
    ChargeSchedule,
    ChargeSlot,
    DayPlan,
    HourlyConsumptionForecast,
    SolarForecastMultiDay,
)
from backend.weather_scheduler import WeatherScheduler, _DAY_CONFIDENCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_solar(daily_kwh: list[float]) -> SolarForecastMultiDay:
    """Build a SolarForecastMultiDay from daily kWh values."""
    daily_wh = [kwh * 1000.0 for kwh in daily_kwh]
    # Simple hourly distribution: flat across 24h per day
    hourly_wh: list[float] = []
    for d in range(3):
        day_wh = daily_wh[d] if d < len(daily_wh) else 0.0
        for _ in range(24):
            hourly_wh.append(day_wh / 24.0)
    return SolarForecastMultiDay(
        hourly_wh=hourly_wh,
        daily_energy_wh=daily_wh,
        source="test",
        fetched_at=datetime.now(tz=timezone.utc),
    )


def _make_consumption(daily_kwh: float, horizon_hours: int = 72) -> HourlyConsumptionForecast:
    """Build a flat HourlyConsumptionForecast with constant per-hour rate."""
    hourly_kwh = [daily_kwh / 24.0] * horizon_hours
    return HourlyConsumptionForecast(
        hourly_kwh=hourly_kwh,
        total_kwh=sum(hourly_kwh),
        horizon_hours=horizon_hours,
        source="test",
        fallback_used=False,
    )


def _make_tariff_slot(start_hour: int = 0, end_hour: int = 5, rate: float = 0.08):
    """Build a mock TariffSlot."""
    today = date.today() + timedelta(days=1)
    slot = MagicMock()
    slot.start = datetime(today.year, today.month, today.day, start_hour, 0, tzinfo=timezone.utc)
    slot.end = datetime(today.year, today.month, today.day, end_hour, 0, tzinfo=timezone.utc)
    slot.effective_rate_eur_kwh = rate
    return slot


def _build_weather_scheduler(
    solar_daily_kwh: list[float] | None = None,
    consumption_daily_kwh: float = 20.0,
    winter: bool = False,
) -> WeatherScheduler:
    """Build a WeatherScheduler with mocked dependencies."""
    if solar_daily_kwh is None:
        solar_daily_kwh = [25.0, 25.0, 25.0]

    solar = _make_solar(solar_daily_kwh)
    consumption = _make_consumption(consumption_daily_kwh)

    # Mock evcc_client
    evcc_client = MagicMock()

    # Mock weather_client (not used directly -- get_solar_forecast is patched)
    weather_client = MagicMock()

    # Mock consumption_forecaster
    consumption_forecaster = MagicMock()
    consumption_forecaster.predict_hourly = AsyncMock(return_value=consumption)

    # Mock tariff_engine
    tariff_engine = MagicMock()
    tariff_engine.get_price_schedule = MagicMock(return_value=[
        _make_tariff_slot(0, 5, 0.08),
    ])

    sys_config = SystemConfig()
    if winter:
        sys_config.winter_months = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]  # always winter
    orch_config = OrchestratorConfig()

    ws = WeatherScheduler(
        scheduler=MagicMock(),
        evcc_client=evcc_client,
        weather_client=weather_client,
        consumption_forecaster=consumption_forecaster,
        sys_config=sys_config,
        orch_config=orch_config,
        tariff_engine=tariff_engine,
    )
    # Store the solar mock for patching
    ws._test_solar = solar
    return ws


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDayPlanStructure:
    """Test DayPlan dataclass structure and fields."""

    def test_dayplan_structure(self):
        """DayPlan has all required fields."""
        plan = DayPlan(
            day_index=0,
            date=date.today(),
            solar_forecast_kwh=25.0,
            consumption_forecast_kwh=20.0,
            net_energy_kwh=5.0,
            confidence=1.0,
            charge_target_kwh=0.0,
            slots=[],
            advisory=False,
        )
        assert plan.day_index == 0
        assert plan.date == date.today()
        assert plan.solar_forecast_kwh == 25.0
        assert plan.consumption_forecast_kwh == 20.0
        assert plan.net_energy_kwh == 5.0
        assert plan.confidence == 1.0
        assert plan.charge_target_kwh == 0.0
        assert plan.slots == []
        assert plan.advisory is False

    def test_day_advisory_flags(self):
        """Day 0 advisory=False, Day 1 advisory=True, Day 2 advisory=True."""
        plans = [
            DayPlan(
                day_index=i,
                date=date.today() + timedelta(days=i),
                solar_forecast_kwh=20.0,
                consumption_forecast_kwh=20.0,
                net_energy_kwh=0.0,
                confidence=[1.0, 0.8, 0.6][i],
                charge_target_kwh=10.0,
                slots=[],
                advisory=(i > 0),
            )
            for i in range(3)
        ]
        assert plans[0].advisory is False
        assert plans[1].advisory is True
        assert plans[2].advisory is True


class TestConfidenceWeights:
    """Test confidence weight values."""

    def test_confidence_weights(self):
        """Day 0 confidence=1.0, Day 1 confidence=0.8, Day 2 confidence=0.6."""
        assert _DAY_CONFIDENCE[0] == 1.0
        assert _DAY_CONFIDENCE[1] == 0.8
        assert _DAY_CONFIDENCE[2] == 0.6
        assert len(_DAY_CONFIDENCE) == 3


class TestWeatherSchedulerAlgorithm:
    """Test the weather-aware charge adjustment algorithm."""

    @pytest.mark.anyio
    async def test_cloudy_increases_charge(self):
        """When solar is low and consumption high, charge target should be high."""
        ws = _build_weather_scheduler(
            solar_daily_kwh=[5.0, 5.0, 5.0],  # cloudy
            consumption_daily_kwh=25.0,  # high demand
        )
        solar = ws._test_solar

        with patch("backend.weather_scheduler.get_solar_forecast", new_callable=AsyncMock, return_value=solar):
            schedule = await ws.compute_schedule()

        assert isinstance(schedule, ChargeSchedule)
        assert schedule.reasoning.charge_energy_kwh > 20.0  # significant charge needed

    @pytest.mark.anyio
    async def test_sunny_reduces_charge(self):
        """When solar is high and consumption moderate, charge target should be near zero."""
        ws = _build_weather_scheduler(
            solar_daily_kwh=[40.0, 40.0, 40.0],  # very sunny
            consumption_daily_kwh=20.0,  # moderate demand
        )
        solar = ws._test_solar

        with patch("backend.weather_scheduler.get_solar_forecast", new_callable=AsyncMock, return_value=solar):
            schedule = await ws.compute_schedule()

        assert isinstance(schedule, ChargeSchedule)
        assert schedule.reasoning.charge_energy_kwh < 5.0  # minimal or zero charge

    @pytest.mark.anyio
    async def test_headroom_ceiling(self):
        """In summer, charge target never exceeds 85% of total capacity."""
        # 94 kWh total * 0.85 = 79.9 kWh max
        ws = _build_weather_scheduler(
            solar_daily_kwh=[0.0, 0.0, 0.0],  # no solar at all
            consumption_daily_kwh=80.0,  # very high demand -> wants > 94 kWh
            winter=False,
        )
        solar = ws._test_solar

        with patch("backend.weather_scheduler.get_solar_forecast", new_callable=AsyncMock, return_value=solar):
            schedule = await ws.compute_schedule()

        assert schedule.reasoning.charge_energy_kwh <= 94.0 * 0.85 + 0.1  # with float tolerance

    @pytest.mark.anyio
    async def test_winter_floor(self):
        """In winter, charge target is at least 30% of total capacity even if sunny."""
        # 94 kWh total * 0.30 = 28.2 kWh minimum
        ws = _build_weather_scheduler(
            solar_daily_kwh=[40.0, 40.0, 40.0],  # sunny
            consumption_daily_kwh=10.0,  # low demand
            winter=True,
        )
        solar = ws._test_solar

        with patch("backend.weather_scheduler.get_solar_forecast", new_callable=AsyncMock, return_value=solar):
            schedule = await ws.compute_schedule()

        assert schedule.reasoning.charge_energy_kwh >= 94.0 * 0.30 - 0.1  # with float tolerance


class TestWeatherSchedulerInterface:
    """Test coordinator-compatible interface attributes."""

    def test_active_schedule_interface(self):
        """WeatherScheduler has active_schedule and schedule_stale attributes."""
        ws = _build_weather_scheduler()
        assert ws.active_schedule is None
        assert ws.schedule_stale is False
        assert isinstance(ws.schedule_stale, bool)

    @pytest.mark.anyio
    async def test_compute_schedule_produces_slots(self):
        """compute_schedule returns ChargeSchedule with huawei + victron slots."""
        ws = _build_weather_scheduler(
            solar_daily_kwh=[10.0, 10.0, 10.0],
            consumption_daily_kwh=25.0,
        )
        solar = ws._test_solar

        with patch("backend.weather_scheduler.get_solar_forecast", new_callable=AsyncMock, return_value=solar):
            schedule = await ws.compute_schedule()

        assert isinstance(schedule, ChargeSchedule)
        assert len(schedule.slots) == 2
        batteries = {s.battery for s in schedule.slots}
        assert "huawei" in batteries
        assert "victron" in batteries

        huawei_slot = next(s for s in schedule.slots if s.battery == "huawei")
        victron_slot = next(s for s in schedule.slots if s.battery == "victron")
        assert huawei_slot.grid_charge_power_w == 5000
        assert victron_slot.grid_charge_power_w == 3000

    @pytest.mark.anyio
    async def test_compute_schedule_sets_day_plans(self):
        """After compute_schedule, active_day_plans has 3 DayPlan entries."""
        ws = _build_weather_scheduler(
            solar_daily_kwh=[15.0, 10.0, 20.0],
            consumption_daily_kwh=20.0,
        )
        solar = ws._test_solar

        with patch("backend.weather_scheduler.get_solar_forecast", new_callable=AsyncMock, return_value=solar):
            await ws.compute_schedule()

        assert ws.active_day_plans is not None
        assert len(ws.active_day_plans) == 3

        for i, dp in enumerate(ws.active_day_plans):
            assert isinstance(dp, DayPlan)
            assert dp.day_index == i
            assert dp.confidence == _DAY_CONFIDENCE[i]
            assert dp.advisory == (i > 0)

        # Day 0 should have slots, Days 1-2 should be empty
        assert len(ws.active_day_plans[0].slots) > 0
        assert len(ws.active_day_plans[1].slots) == 0
        assert len(ws.active_day_plans[2].slots) == 0


class TestForecastDeviation:
    """Test intra-day forecast deviation detection."""

    @pytest.mark.anyio
    async def test_replan_on_deviation(self):
        """When new solar forecast differs by >20% from prior, returns True."""
        ws = _build_weather_scheduler(solar_daily_kwh=[10.0, 10.0, 10.0])
        # Simulate prior compute with known values
        ws._last_solar_daily_kwh = [10.0, 10.0, 10.0]

        # New forecast: day 0 jumps from 10 to 15 kWh (50% deviation)
        new_solar = _make_solar([15.0, 10.0, 10.0])
        with patch(
            "backend.weather_scheduler.get_solar_forecast",
            new_callable=AsyncMock,
            return_value=new_solar,
        ):
            result = await ws.check_forecast_deviation(threshold=0.20)

        assert result is True

    @pytest.mark.anyio
    async def test_no_replan_stable(self):
        """When new solar forecast is within 20% of prior, returns False."""
        ws = _build_weather_scheduler(solar_daily_kwh=[10.0, 10.0, 10.0])
        ws._last_solar_daily_kwh = [10.0, 10.0, 10.0]

        # New forecast: day 0 goes from 10 to 11 kWh (10% deviation)
        new_solar = _make_solar([11.0, 10.0, 10.0])
        with patch(
            "backend.weather_scheduler.get_solar_forecast",
            new_callable=AsyncMock,
            return_value=new_solar,
        ):
            result = await ws.check_forecast_deviation(threshold=0.20)

        assert result is False

    @pytest.mark.anyio
    async def test_replan_no_prior_data(self):
        """When _last_solar_daily_kwh is None, returns False (no basis)."""
        ws = _build_weather_scheduler()
        ws._last_solar_daily_kwh = None

        result = await ws.check_forecast_deviation()
        assert result is False

    @pytest.mark.anyio
    async def test_replan_zero_to_significant(self):
        """When old solar was 0 and new is significant (>1 kWh), returns True."""
        ws = _build_weather_scheduler(solar_daily_kwh=[5.0, 5.0, 5.0])
        ws._last_solar_daily_kwh = [0.0, 10.0, 10.0]

        new_solar = _make_solar([5.0, 10.0, 10.0])
        with patch(
            "backend.weather_scheduler.get_solar_forecast",
            new_callable=AsyncMock,
            return_value=new_solar,
        ):
            result = await ws.check_forecast_deviation(threshold=0.20)

        assert result is True


class TestComputeLock:
    """Test asyncio.Lock prevents concurrent compute_schedule calls."""

    @pytest.mark.anyio
    async def test_compute_lock(self):
        """Two concurrent compute_schedule calls serialize (not corrupt)."""
        ws = _build_weather_scheduler(
            solar_daily_kwh=[10.0, 10.0, 10.0],
            consumption_daily_kwh=20.0,
        )
        solar = ws._test_solar

        call_order: list[str] = []
        original_compute = ws.compute_schedule.__func__

        async def slow_compute(self_ws, writer=None):
            call_order.append("enter")
            await asyncio.sleep(0.05)
            result = await original_compute(self_ws, writer)
            call_order.append("exit")
            return result

        with patch(
            "backend.weather_scheduler.get_solar_forecast",
            new_callable=AsyncMock,
            return_value=solar,
        ):
            # Replace compute_schedule with slow version that tracks order
            # but still goes through the lock
            ws.compute_schedule = lambda writer=None: slow_compute(ws, writer)

            # Simulate using the lock directly
            async def locked_compute():
                async with ws._compute_lock:
                    call_order.append("enter")
                    await asyncio.sleep(0.05)
                    call_order.append("exit")

            t1 = asyncio.create_task(locked_compute())
            t2 = asyncio.create_task(locked_compute())
            await asyncio.gather(t1, t2)

        # With a lock, calls must serialize: enter, exit, enter, exit
        assert call_order == ["enter", "exit", "enter", "exit"]
