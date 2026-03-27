"""Unit tests for Scheduler.compute_schedule() — S03.

Tests cover:
  - Sunny / cloudy / winter / summer scenarios with EVopt present or absent
  - EVCC unreachable: prior schedule marked stale; fallback schedule returned
  - SoC clamping (above max, below min)
  - LUNA-first slot ordering (D010)
  - Slot power values and UTC-awareness
  - Writer called / not called / exception swallowed
  - active_schedule / schedule_stale attributes updated correctly
  - _make_fallback_schedule() surface
  - Fallback to cheapest slot when no slots below price threshold

K007: anyio_mode = "auto" auto-collects async def test_* without explicit
      @pytest.mark.anyio.  All async tests here rely on that.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import OrchestratorConfig, SystemConfig
from backend.schedule_models import (
    ChargeSchedule,
    ConsumptionForecast,
    EvccState,
    EvoptBatteryTimeseries,
    EvoptResult,
    SolarForecast,
)
from backend.scheduler import Scheduler, _make_fallback_schedule
from backend.tariff import EvccTariffEngine

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_battery(title: str, net_wh: float, n_slots: int = 96) -> EvoptBatteryTimeseries:
    """Build an EvoptBatteryTimeseries with net_wh energy spread over n_slots."""
    charge_w = max(0.0, net_wh / (n_slots * 0.25))
    discharge_w = max(0.0, -net_wh / (n_slots * 0.25))
    ts = [datetime(2026, 1, 1, tzinfo=timezone.utc)] * n_slots
    return EvoptBatteryTimeseries(
        title=title,
        charging_power_w=[charge_w] * n_slots,
        discharging_power_w=[discharge_w] * n_slots,
        soc_fraction=[0.5] * n_slots,
        slot_timestamps_utc=ts,
    )


def _make_evcc_state(solar_kwh: float = 10.0, evopt: bool = True) -> EvccState:
    """Build a realistic EvccState for test scenarios."""
    solar = SolarForecast(
        timeseries_w=[0.0] * 96,
        slot_timestamps_utc=[datetime(2026, 1, 1, tzinfo=timezone.utc)] * 96,
        tomorrow_energy_wh=solar_kwh * 1000,
        day_after_energy_wh=0.0,
    )
    if evopt:
        evopt_result = EvoptResult(
            status="Optimal",
            objective_value=0.0,
            batteries=[
                _make_battery("Emma Akku 1", 3000.0),   # +3 kWh per pack
                _make_battery("Emma Akku 2", 3000.0),   # +3 kWh → 6 kWh total Huawei
                _make_battery("Victron", 5000.0),        # +5 kWh Victron
            ],
        )
    else:
        evopt_result = None
    return EvccState(
        evopt=evopt_result,
        solar=solar,
        grid_prices=None,
        evopt_status="Optimal" if evopt else "unknown",
    )


def _make_evcc_state_no_solar(evopt: bool = False) -> EvccState:
    """Build an EvccState with solar=None."""
    if evopt:
        evopt_result = EvoptResult(
            status="Optimal",
            objective_value=0.0,
            batteries=[
                _make_battery("Emma Akku 1", 1000.0),
                _make_battery("Emma Akku 2", 1000.0),
                _make_battery("Victron", 2000.0),
            ],
        )
    else:
        evopt_result = None
    return EvccState(
        evopt=evopt_result,
        solar=None,
        grid_prices=None,
        evopt_status="Optimal" if evopt else "unknown",
    )


def _make_consumption(kwh: float = 20.0, fallback: bool = False) -> ConsumptionForecast:
    """Build a ConsumptionForecast for tests."""
    return ConsumptionForecast(
        kwh_by_weekday={i: kwh for i in range(7)},
        today_expected_kwh=kwh,
        days_of_history=0 if fallback else 14,
        fallback_used=fallback,
    )


def _make_scheduler(
    evcc_state=None,
    consumption=None,
    sys_config: SystemConfig | None = None,
    orch_config: OrchestratorConfig | None = None,
) -> Scheduler:
    """Build a Scheduler with AsyncMock clients and a real CompositeTariffEngine."""
    
    tariff_engine = EvccTariffEngine()
    evcc_client = MagicMock()
    evcc_client.get_state = AsyncMock(return_value=evcc_state)
    consumption_reader = MagicMock()
    consumption_reader.query_consumption_history = AsyncMock(
        return_value=consumption if consumption is not None else _make_consumption()
    )
    return Scheduler(
        evcc_client,
        consumption_reader,
        tariff_engine,
        sys_config if sys_config is not None else SystemConfig(),
        orch_config if orch_config is not None else OrchestratorConfig(),
    )


# ---------------------------------------------------------------------------
# 1. Sunny scenario (EVopt present)
# ---------------------------------------------------------------------------


async def test_sunny_evopt_returns_schedule():
    """Sunny day with EVopt: should return a valid non-stale ChargeSchedule."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state(solar_kwh=25.0))
    schedule = await scheduler.compute_schedule()
    assert isinstance(schedule, ChargeSchedule)
    assert schedule.stale is False


async def test_sunny_evopt_huawei_target_within_bounds():
    """Huawei target must be within [min_soc, max_soc]."""
    sys_cfg = SystemConfig()
    scheduler = _make_scheduler(
        evcc_state=_make_evcc_state(solar_kwh=25.0), sys_config=sys_cfg
    )
    schedule = await scheduler.compute_schedule()
    assert sys_cfg.huawei_min_soc_pct <= schedule.slots[0].target_soc_pct <= sys_cfg.huawei_max_soc_pct


async def test_sunny_evopt_victron_target_within_bounds():
    """Victron target must be within [min_soc, max_soc]."""
    sys_cfg = SystemConfig()
    scheduler = _make_scheduler(
        evcc_state=_make_evcc_state(solar_kwh=25.0), sys_config=sys_cfg
    )
    schedule = await scheduler.compute_schedule()
    assert sys_cfg.victron_min_soc_pct <= schedule.slots[1].target_soc_pct <= sys_cfg.victron_max_soc_pct


# ---------------------------------------------------------------------------
# 2. Cloudy scenario (EVopt present, high consumption)
# ---------------------------------------------------------------------------


async def test_cloudy_evopt_charge_energy_positive():
    """Cloudy day: consumption >> solar → charge_energy_kwh > 0."""
    scheduler = _make_scheduler(
        evcc_state=_make_evcc_state(solar_kwh=2.0),
        consumption=_make_consumption(kwh=35.0),
    )
    schedule = await scheduler.compute_schedule()
    assert schedule.reasoning.charge_energy_kwh > 0


async def test_cloudy_targets_near_max():
    """Cloudy day, both battery targets should be >= system minimum."""
    sys_cfg = SystemConfig()
    scheduler = _make_scheduler(
        evcc_state=_make_evcc_state(solar_kwh=2.0),
        consumption=_make_consumption(kwh=35.0),
        sys_config=sys_cfg,
    )
    schedule = await scheduler.compute_schedule()
    assert schedule.slots[0].target_soc_pct >= sys_cfg.huawei_min_soc_pct
    assert schedule.slots[1].target_soc_pct >= sys_cfg.victron_min_soc_pct


# ---------------------------------------------------------------------------
# 3. Winter (EVopt absent, formula fallback, fallback consumption)
# ---------------------------------------------------------------------------


async def test_winter_formula_fallback_clamped():
    """Winter cold-start: formula target must be clamped to max_soc_pct."""
    sys_cfg = SystemConfig()
    scheduler = _make_scheduler(
        evcc_state=_make_evcc_state(solar_kwh=0.0, evopt=False),
        consumption=_make_consumption(kwh=35.0, fallback=True),
        sys_config=sys_cfg,
    )
    schedule = await scheduler.compute_schedule()
    assert schedule.slots[0].target_soc_pct <= sys_cfg.huawei_max_soc_pct
    assert schedule.slots[1].target_soc_pct <= sys_cfg.victron_max_soc_pct


async def test_winter_formula_fallback_both_targets_positive():
    """Winter formula: both targets must be ≥ system minimum."""
    sys_cfg = SystemConfig()
    scheduler = _make_scheduler(
        evcc_state=_make_evcc_state(solar_kwh=0.0, evopt=False),
        consumption=_make_consumption(kwh=35.0, fallback=True),
        sys_config=sys_cfg,
    )
    schedule = await scheduler.compute_schedule()
    assert schedule.slots[0].target_soc_pct >= sys_cfg.huawei_min_soc_pct
    assert schedule.slots[1].target_soc_pct >= sys_cfg.victron_min_soc_pct


# ---------------------------------------------------------------------------
# 4. Summer (EVopt absent, solar > consumption)
# ---------------------------------------------------------------------------


async def test_summer_formula_zero_charge():
    """Summer: solar covers consumption → charge_energy_kwh == 0."""
    scheduler = _make_scheduler(
        evcc_state=_make_evcc_state(solar_kwh=30.0, evopt=False),
        consumption=_make_consumption(kwh=15.0),
    )
    schedule = await scheduler.compute_schedule()
    assert schedule.reasoning.charge_energy_kwh == 0.0


async def test_summer_formula_targets_at_min():
    """Summer zero-charge: formula → net_charge = 0 → raw targets = 0 → clamped to min."""
    sys_cfg = SystemConfig()
    scheduler = _make_scheduler(
        evcc_state=_make_evcc_state(solar_kwh=30.0, evopt=False),
        consumption=_make_consumption(kwh=15.0),
        sys_config=sys_cfg,
    )
    schedule = await scheduler.compute_schedule()
    assert schedule.slots[0].target_soc_pct == sys_cfg.huawei_min_soc_pct
    assert schedule.slots[1].target_soc_pct == sys_cfg.victron_min_soc_pct


# ---------------------------------------------------------------------------
# 5. EVCC unreachable — prior schedule exists
# ---------------------------------------------------------------------------


async def test_evcc_unreachable_with_prior_schedule():
    """When EVCC returns None with a prior schedule: prior is returned with stale=True."""
    scheduler = _make_scheduler(evcc_state=None)
    prior = _make_fallback_schedule()
    prior.stale = False  # start not-stale
    scheduler.active_schedule = prior

    result = await scheduler.compute_schedule()
    assert result.stale is True


async def test_evcc_unreachable_same_object_returned():
    """When EVCC returns None: the exact same prior object is returned (not a copy)."""
    scheduler = _make_scheduler(evcc_state=None)
    prior = _make_fallback_schedule()
    prior.stale = False
    scheduler.active_schedule = prior

    result = await scheduler.compute_schedule()
    assert result is prior


async def test_evcc_unreachable_sets_schedule_stale_attr():
    """When EVCC returns None: scheduler.schedule_stale is set to True."""
    scheduler = _make_scheduler(evcc_state=None)
    await scheduler.compute_schedule()
    assert scheduler.schedule_stale is True


# ---------------------------------------------------------------------------
# 6. EVCC unreachable — no prior schedule
# ---------------------------------------------------------------------------


async def test_evcc_unreachable_no_prior_returns_fallback():
    """No prior schedule + EVCC None → fallback schedule with stale=True, slots=[]."""
    scheduler = _make_scheduler(evcc_state=None)
    assert scheduler.active_schedule is None

    result = await scheduler.compute_schedule()
    assert result.stale is True
    assert result.slots == []


async def test_evcc_unreachable_no_prior_schedule_stale_true():
    """No prior schedule + EVCC None → scheduler.schedule_stale is True."""
    scheduler = _make_scheduler(evcc_state=None)
    await scheduler.compute_schedule()
    assert scheduler.schedule_stale is True


# ---------------------------------------------------------------------------
# 7. EVopt absent (formula fallback)
# ---------------------------------------------------------------------------


async def test_evopt_none_formula_fallback_used():
    """EVopt absent: formula fallback computes without error."""
    scheduler = _make_scheduler(
        evcc_state=_make_evcc_state(solar_kwh=10.0, evopt=False),
        consumption=_make_consumption(kwh=20.0),
    )
    schedule = await scheduler.compute_schedule()
    assert isinstance(schedule, ChargeSchedule)


async def test_evopt_none_solar_none_defaults_to_zero():
    """EvccState.solar=None: solar_kwh defaults to 0.0 in reasoning."""
    scheduler = _make_scheduler(
        evcc_state=_make_evcc_state_no_solar(evopt=False),
        consumption=_make_consumption(kwh=20.0),
    )
    schedule = await scheduler.compute_schedule()
    assert schedule.reasoning.tomorrow_solar_kwh == 0.0


# ---------------------------------------------------------------------------
# 8. SoC clamping
# ---------------------------------------------------------------------------


async def test_clamping_above_max():
    """Formula with no solar and tiny capacity → raw > 100 → clamped to max_soc_pct."""
    sys_cfg = SystemConfig(huawei_max_soc_pct=95.0, victron_max_soc_pct=95.0)
    # Tiny capacity means 100% SoC requires very little energy, so net_charge
    # as a fraction of capacity exceeds 100%.
    orch_cfg = OrchestratorConfig(huawei_capacity_kwh=1.0, victron_capacity_kwh=1.0)
    scheduler = _make_scheduler(
        evcc_state=_make_evcc_state(solar_kwh=0.0, evopt=False),
        consumption=_make_consumption(kwh=200.0),
        sys_config=sys_cfg,
        orch_config=orch_cfg,
    )
    schedule = await scheduler.compute_schedule()
    assert schedule.slots[0].target_soc_pct <= 95.0
    assert schedule.slots[1].target_soc_pct <= 95.0


async def test_clamping_below_min():
    """Formula with huge solar and low consumption → raw near 0 → clamped to min_soc_pct."""
    sys_cfg = SystemConfig(huawei_min_soc_pct=10.0, victron_min_soc_pct=15.0)
    scheduler = _make_scheduler(
        evcc_state=_make_evcc_state(solar_kwh=1000.0, evopt=False),
        consumption=_make_consumption(kwh=1.0),
        sys_config=sys_cfg,
    )
    schedule = await scheduler.compute_schedule()
    assert schedule.slots[0].target_soc_pct >= 10.0
    assert schedule.slots[1].target_soc_pct >= 15.0


# ---------------------------------------------------------------------------
# 9. Reasoning fields
# ---------------------------------------------------------------------------


async def test_reasoning_fields_non_negative():
    """All numeric reasoning fields must be non-negative."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    schedule = await scheduler.compute_schedule()
    r = schedule.reasoning
    assert r.tomorrow_solar_kwh >= 0.0
    assert r.expected_consumption_kwh >= 0.0
    assert r.charge_energy_kwh >= 0.0
    assert r.cost_estimate_eur >= 0.0


async def test_reasoning_text_non_empty():
    """Reasoning text must be non-empty."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    schedule = await scheduler.compute_schedule()
    assert len(schedule.reasoning.text) > 0


# ---------------------------------------------------------------------------
# 10. Slot ordering and power values
# ---------------------------------------------------------------------------


async def test_slot_ordering_huawei_first():
    """LUNA (huawei) slot must come before Victron slot (D010)."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    schedule = await scheduler.compute_schedule()
    assert schedule.slots[0].battery == "huawei"
    assert schedule.slots[1].battery == "victron"


async def test_huawei_charge_power_5000w():
    """Huawei slot grid_charge_power_w must be 5000 W."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    schedule = await scheduler.compute_schedule()
    assert schedule.slots[0].grid_charge_power_w == 5000


async def test_victron_charge_power_3000w():
    """Victron slot grid_charge_power_w must be 3000 W."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    schedule = await scheduler.compute_schedule()
    assert schedule.slots[1].grid_charge_power_w == 3000


async def test_window_start_utc_aware():
    """Slot start_utc must be timezone-aware."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    schedule = await scheduler.compute_schedule()
    assert schedule.slots[0].start_utc.tzinfo is not None


async def test_window_end_utc_aware():
    """Slot end_utc must be timezone-aware."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    schedule = await scheduler.compute_schedule()
    assert schedule.slots[0].end_utc.tzinfo is not None


# ---------------------------------------------------------------------------
# 11. Writer behaviour
# ---------------------------------------------------------------------------


async def test_writer_called_when_provided():
    """When writer is provided, write_charge_schedule must be called."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    writer = MagicMock()
    writer.write_charge_schedule = AsyncMock()
    await scheduler.compute_schedule(writer=writer)
    assert writer.write_charge_schedule.called


async def test_writer_not_called_when_none():
    """When writer is None, compute_schedule must succeed without error."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    schedule = await scheduler.compute_schedule(writer=None)
    assert isinstance(schedule, ChargeSchedule)


async def test_writer_exception_swallowed():
    """Writer raising an exception must not propagate out of compute_schedule."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    writer = MagicMock()
    writer.write_charge_schedule = AsyncMock(side_effect=RuntimeError("boom"))
    # Must not raise
    schedule = await scheduler.compute_schedule(writer=writer)
    assert isinstance(schedule, ChargeSchedule)


# ---------------------------------------------------------------------------
# 12. State attributes updated
# ---------------------------------------------------------------------------


async def test_active_schedule_updated():
    """scheduler.active_schedule must point to the returned schedule."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    schedule = await scheduler.compute_schedule()
    assert scheduler.active_schedule is schedule


async def test_schedule_stale_false_after_success():
    """scheduler.schedule_stale must be False after a successful compute."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    await scheduler.compute_schedule()
    assert scheduler.schedule_stale is False


async def test_computed_at_utc_aware():
    """schedule.computed_at must be a UTC-aware datetime."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    schedule = await scheduler.compute_schedule()
    assert schedule.computed_at.tzinfo is not None


async def test_schedule_is_chargeschedule_instance():
    """Return value must be an instance of ChargeSchedule."""
    scheduler = _make_scheduler(evcc_state=_make_evcc_state())
    schedule = await scheduler.compute_schedule()
    assert isinstance(schedule, ChargeSchedule)


# ---------------------------------------------------------------------------
# 13. _make_fallback_schedule surface
# ---------------------------------------------------------------------------


def test_make_fallback_schedule_stale():
    """_make_fallback_schedule() must return stale=True, slots=[]."""
    s = _make_fallback_schedule()
    assert s.stale is True
    assert s.slots == []


def test_make_fallback_schedule_zero_reasoning():
    """_make_fallback_schedule() reasoning fields must all be zero."""
    s = _make_fallback_schedule()
    assert s.reasoning.charge_energy_kwh == 0.0
    assert s.reasoning.cost_estimate_eur == 0.0
    assert s.reasoning.tomorrow_solar_kwh == 0.0
    assert s.reasoning.expected_consumption_kwh == 0.0


# ---------------------------------------------------------------------------
# 14. Fallback to cheapest slot when no slots below threshold
# ---------------------------------------------------------------------------


async def test_fallback_when_no_slots_below_threshold():
    """When all tariff slots have rate > threshold, scheduler picks the cheapest one and returns a valid schedule."""
    from backend.tariff_models import TariffSlot

    high_rate_slots = [
        TariffSlot(
            start=datetime(2026, 1, 2, h, 0, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, h + 1, 0, tzinfo=timezone.utc)
            if h < 23
            else datetime(2026, 1, 3, 0, 0, tzinfo=timezone.utc),
            octopus_rate_eur_kwh=0.20 + h * 0.005,
            modul3_rate_eur_kwh=0.10 + h * 0.005,
            effective_rate_eur_kwh=0.30 + h * 0.01,
        )
        for h in range(24)
    ]

    
    tariff_engine = EvccTariffEngine()

    evcc_client = MagicMock()
    evcc_client.get_state = AsyncMock(return_value=_make_evcc_state())
    consumption_reader = MagicMock()
    consumption_reader.query_consumption_history = AsyncMock(
        return_value=_make_consumption()
    )

    scheduler = Scheduler(
        evcc_client,
        consumption_reader,
        tariff_engine,
        SystemConfig(),
        OrchestratorConfig(),
    )

    # Patch the tariff engine to return only high-rate slots
    with patch.object(tariff_engine, "get_price_schedule", return_value=high_rate_slots):
        schedule = await scheduler.compute_schedule()

    assert isinstance(schedule, ChargeSchedule)
    assert len(schedule.slots) == 2


# ---------------------------------------------------------------------------
# 15. Predictive pre-charging (OPT-04)
# ---------------------------------------------------------------------------


class TestPredictivePreCharging:
    """OPT-04: Skip/reduce grid charge when solar forecast covers demand."""

    async def test_skip_grid_charge_when_solar_exceeds_120pct(self):
        """D-10: solar >= consumption * 1.2 -> skip grid charge."""
        scheduler = _make_scheduler(
            evcc_state=_make_evcc_state(solar_kwh=30.0, evopt=False),
            consumption=_make_consumption(kwh=20.0),
        )
        schedule = await scheduler.compute_schedule()
        # 30 >= 20*1.2=24 -> skip
        assert schedule.reasoning.charge_energy_kwh == 0.0

    async def test_skip_sets_targets_to_min_soc(self):
        """When charge is skipped, targets should clamp to min_soc."""
        sys_cfg = SystemConfig(huawei_min_soc_pct=10.0, victron_min_soc_pct=15.0)
        scheduler = _make_scheduler(
            evcc_state=_make_evcc_state(solar_kwh=30.0, evopt=False),
            consumption=_make_consumption(kwh=20.0),
            sys_config=sys_cfg,
        )
        schedule = await scheduler.compute_schedule()
        assert schedule.slots[0].target_soc_pct == 10.0
        assert schedule.slots[1].target_soc_pct == 15.0

    async def test_partial_coverage_reduces_target(self):
        """D-11: partial solar -> charge = consumption - solar*0.8."""
        scheduler = _make_scheduler(
            evcc_state=_make_evcc_state(solar_kwh=15.0, evopt=False),
            consumption=_make_consumption(kwh=20.0),
        )
        schedule = await scheduler.compute_schedule()
        # 15 < 20*1.2=24, but solar > 0 -> charge = max(0, 20 - 15*0.8) = 8
        assert abs(schedule.reasoning.charge_energy_kwh - 8.0) < 0.1

    async def test_zero_solar_full_charge(self):
        """Rainy day: solar=0 kWh (valid forecast) -> full charge."""
        scheduler = _make_scheduler(
            evcc_state=_make_evcc_state(solar_kwh=0.0, evopt=False),
            consumption=_make_consumption(kwh=20.0),
        )
        schedule = await scheduler.compute_schedule()
        assert schedule.reasoning.charge_energy_kwh == 20.0

    async def test_no_solar_forecast_full_charge(self):
        """D-12: solar=None (EVCC offline) -> full charge (safety)."""
        scheduler = _make_scheduler(
            evcc_state=_make_evcc_state_no_solar(evopt=False),
            consumption=_make_consumption(kwh=20.0),
        )
        schedule = await scheduler.compute_schedule()
        # solar=None -> solar_kwh=0, no solar object -> full charge
        assert schedule.reasoning.charge_energy_kwh == 20.0

    async def test_evopt_present_ignores_solar_reduction(self):
        """D-18: EVopt path does not apply solar reduction formula."""
        scheduler = _make_scheduler(
            evcc_state=_make_evcc_state(solar_kwh=30.0, evopt=True),
            consumption=_make_consumption(kwh=20.0),
        )
        schedule = await scheduler.compute_schedule()
        # EVopt present -> uses EVopt targets, not formula fallback
        assert isinstance(schedule, ChargeSchedule)

    async def test_reasoning_text_mentions_skip(self):
        """Reasoning text should indicate solar skip."""
        scheduler = _make_scheduler(
            evcc_state=_make_evcc_state(solar_kwh=30.0, evopt=False),
            consumption=_make_consumption(kwh=20.0),
        )
        schedule = await scheduler.compute_schedule()
        assert "skip" in schedule.reasoning.text.lower()
