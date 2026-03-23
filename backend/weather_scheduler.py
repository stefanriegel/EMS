"""Multi-day weather-aware charge scheduler.

Wraps the existing :class:`~backend.scheduler.Scheduler` to extend charge
planning from a single-night horizon to a 3-day outlook.  The algorithm
compares per-day solar supply vs. consumption demand with confidence
discounting and adjusts tonight's grid charge target accordingly.

Observability
-------------
- Logger name: ``ems.weather_scheduler``
- INFO ``"WeatherScheduler: solar=[X,Y,Z] consumption=[A,B,C] tonight=T kWh"``
  on each compute.
- INFO ``"WeatherScheduler: winter_floor applied, charge=X kWh"`` when the
  winter minimum is enforced.

Inspection surfaces
-------------------
- ``WeatherScheduler.active_schedule``   -- the last computed schedule (or None).
- ``WeatherScheduler.schedule_stale``    -- True when forecasts were unreachable.
- ``WeatherScheduler.active_day_plans``  -- list of 3 DayPlan containers.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from statistics import mean

from backend.config import OrchestratorConfig, SystemConfig
from backend.schedule_models import (
    ChargeSchedule,
    ChargeSlot,
    DayPlan,
    OptimizationReasoning,
)
from backend.weather_client import get_solar_forecast

logger = logging.getLogger("ems.weather_scheduler")

# Confidence weights by day horizon (MDS-03).
_DAY_CONFIDENCE = [1.0, 0.8, 0.6]

# Price threshold in EUR/kWh below which a slot is considered "cheap".
_CHEAP_THRESHOLD_EUR_KWH = 0.15


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------


def _compute_adjusted_charge(
    solar_daily_kwh: list[float],
    consumption_daily_kwh: list[float],
    total_capacity_kwh: float,
    is_winter: bool,
) -> tuple[float, list[float]]:
    """Compute weather-adjusted grid charge target for tonight.

    Parameters
    ----------
    solar_daily_kwh:
        Per-day solar forecast in kWh (3 values).
    consumption_daily_kwh:
        Per-day consumption forecast in kWh (3 values).
    total_capacity_kwh:
        Combined battery capacity in kWh.
    is_winter:
        Whether the current month is a winter month.

    Returns
    -------
    tuple[float, list[float]]
        ``(tonight_charge_kwh, deficits)`` where deficits is a 3-element
        list of per-day energy deficits in kWh.
    """
    deficits: list[float] = []
    for d in range(3):
        conf = _DAY_CONFIDENCE[d]
        effective_solar = solar_daily_kwh[d] * conf if d < len(solar_daily_kwh) else 0.0
        demand = consumption_daily_kwh[d] if d < len(consumption_daily_kwh) else 0.0
        deficit = max(0.0, demand - effective_solar)
        deficits.append(deficit)

    # Tonight's charge covers today's deficit plus partial pre-charge
    tonight = deficits[0]
    tonight += deficits[1] * 0.5   # 50% of tomorrow's deficit
    tonight += deficits[2] * 0.2   # 20% of day-after deficit

    # Conservative ceiling: leave headroom for unexpected PV (MDS-07)
    headroom = 0.05 if is_winter else 0.15
    max_charge = total_capacity_kwh * (1.0 - headroom)
    tonight = min(tonight, max_charge)

    # Winter floor: always charge at least 30% of capacity
    if is_winter:
        winter_floor = total_capacity_kwh * 0.30
        tonight = max(tonight, winter_floor)

    return tonight, deficits


# ---------------------------------------------------------------------------
# WeatherScheduler
# ---------------------------------------------------------------------------


class WeatherScheduler:
    """Multi-day weather-aware charge scheduler.

    Wraps the existing Scheduler to adjust charge targets based on
    multi-day solar and consumption forecasts.  Exposes the same
    ``active_schedule`` / ``schedule_stale`` interface so the coordinator
    and API consume it transparently.

    Parameters
    ----------
    scheduler:
        The inner :class:`~backend.scheduler.Scheduler` instance (kept for
        reference but NOT delegated to for schedule computation to avoid
        double-counting solar discounts).
    evcc_client:
        EVCC client for solar forecast cascade.
    weather_client:
        Optional Open-Meteo client for solar forecast fallback.
    consumption_forecaster:
        Forecaster with ``predict_hourly(72)`` method.
    sys_config:
        Per-system SoC limits and winter config.
    orch_config:
        Battery capacity and loop parameters.
    tariff_engine:
        Composite tariff engine for cheapest-window selection.
    """

    def __init__(
        self,
        scheduler,
        evcc_client,
        weather_client,
        consumption_forecaster,
        sys_config: SystemConfig,
        orch_config: OrchestratorConfig,
        tariff_engine,
    ) -> None:
        self._scheduler = scheduler
        self._evcc_client = evcc_client
        self._weather_client = weather_client
        self._consumption_forecaster = consumption_forecaster
        self._sys_config = sys_config
        self._orch_config = orch_config
        self._tariff_engine = tariff_engine

        self.active_schedule: ChargeSchedule | None = None
        self.schedule_stale: bool = False
        self.active_day_plans: list[DayPlan] | None = None

        # Last solar daily values for deviation checks (Plan 02)
        self._last_solar_daily_kwh: list[float] | None = None

    async def compute_schedule(self, writer=None) -> ChargeSchedule:
        """Compute a weather-aware charge schedule for the battery pool.

        1. Fetch multi-day solar forecast (72h).
        2. Fetch multi-day consumption forecast (72h).
        3. Compare supply vs demand per day with confidence weights.
        4. Adjust tonight's grid charge target.
        5. Build ChargeSchedule with slots split between batteries.
        6. Package into DayPlan containers.

        Parameters
        ----------
        writer:
            Optional InfluxDB metrics writer.  When provided the computed
            schedule is written to InfluxDB (fire-and-forget).

        Returns
        -------
        ChargeSchedule
            The newly computed weather-aware schedule.
        """
        # ------------------------------------------------------------------
        # 1. Fetch multi-day solar forecast
        # ------------------------------------------------------------------
        solar = await get_solar_forecast(self._evcc_client, self._weather_client)
        solar_daily_kwh = [wh / 1000.0 for wh in solar.daily_energy_wh]

        # Pad to 3 days if shorter
        while len(solar_daily_kwh) < 3:
            solar_daily_kwh.append(0.0)

        # ------------------------------------------------------------------
        # 2. Fetch multi-day consumption forecast
        # ------------------------------------------------------------------
        if self._consumption_forecaster is not None:
            try:
                consumption = await self._consumption_forecaster.predict_hourly(72)
                # Sum 24h chunks to get daily consumption
                consumption_daily_kwh: list[float] = []
                for d in range(3):
                    start = d * 24
                    end = start + 24
                    chunk = consumption.hourly_kwh[start:end]
                    consumption_daily_kwh.append(sum(chunk))
            except Exception as exc:
                logger.warning("WeatherScheduler: consumption forecast failed: %s", exc)
                consumption_daily_kwh = [20.0, 20.0, 20.0]
        else:
            consumption_daily_kwh = [20.0, 20.0, 20.0]

        # Pad to 3 days if shorter
        while len(consumption_daily_kwh) < 3:
            consumption_daily_kwh.append(20.0)

        # ------------------------------------------------------------------
        # 3. Compute adjusted charge target
        # ------------------------------------------------------------------
        total_capacity_kwh = (
            self._orch_config.huawei_capacity_kwh
            + self._orch_config.victron_capacity_kwh
        )
        is_winter = datetime.now().month in self._sys_config.winter_months

        tonight_charge_kwh, deficits = _compute_adjusted_charge(
            solar_daily_kwh, consumption_daily_kwh, total_capacity_kwh, is_winter
        )

        if is_winter and tonight_charge_kwh >= total_capacity_kwh * 0.30:
            logger.info(
                "WeatherScheduler: winter_floor applied, charge=%.1f kWh",
                tonight_charge_kwh,
            )

        # ------------------------------------------------------------------
        # 4. Split charge between batteries by capacity ratio
        # ------------------------------------------------------------------
        if total_capacity_kwh > 0:
            huawei_ratio = self._orch_config.huawei_capacity_kwh / total_capacity_kwh
            victron_ratio = self._orch_config.victron_capacity_kwh / total_capacity_kwh
        else:
            huawei_ratio = 0.5
            victron_ratio = 0.5

        huawei_charge_kwh = tonight_charge_kwh * huawei_ratio
        victron_charge_kwh = tonight_charge_kwh * victron_ratio

        # Compute target SoC percentages
        if self._orch_config.huawei_capacity_kwh > 0:
            huawei_target_soc = (
                huawei_charge_kwh / self._orch_config.huawei_capacity_kwh
            ) * 100.0
        else:
            huawei_target_soc = 0.0

        if self._orch_config.victron_capacity_kwh > 0:
            victron_target_soc = (
                victron_charge_kwh / self._orch_config.victron_capacity_kwh
            ) * 100.0
        else:
            victron_target_soc = 0.0

        # Clamp to SystemConfig limits
        huawei_target_soc = max(
            self._sys_config.huawei_min_soc_pct,
            min(self._sys_config.huawei_max_soc_pct, huawei_target_soc),
        )
        victron_target_soc = max(
            self._sys_config.victron_min_soc_pct,
            min(self._sys_config.victron_max_soc_pct, victron_target_soc),
        )

        # ------------------------------------------------------------------
        # 5. Select cheapest tariff window for tomorrow
        # ------------------------------------------------------------------
        tomorrow = date.today() + timedelta(days=1)
        all_slots = self._tariff_engine.get_price_schedule(tomorrow)

        cheap_slots = [
            s for s in all_slots if s.effective_rate_eur_kwh <= _CHEAP_THRESHOLD_EUR_KWH
        ]
        if not cheap_slots:
            cheap_slots = [min(all_slots, key=lambda s: s.effective_rate_eur_kwh)]

        cheap_slots = sorted(cheap_slots, key=lambda s: s.start)
        window_start = cheap_slots[0].start
        window_end = cheap_slots[-1].end

        # ------------------------------------------------------------------
        # 6. Build ChargeSchedule with slots
        # ------------------------------------------------------------------
        huawei_slot = ChargeSlot(
            battery="huawei",
            target_soc_pct=huawei_target_soc,
            start_utc=window_start,
            end_utc=window_end,
            grid_charge_power_w=5000,
        )
        victron_slot = ChargeSlot(
            battery="victron",
            target_soc_pct=victron_target_soc,
            start_utc=window_start,
            end_utc=window_end,
            grid_charge_power_w=3000,
        )

        # Cost estimate
        avg_price = mean(s.effective_rate_eur_kwh for s in cheap_slots)
        cost_estimate_eur = tonight_charge_kwh * avg_price

        # Reasoning text
        reasoning_text = (
            f"Weather-aware schedule: solar [{solar_daily_kwh[0]:.1f}, "
            f"{solar_daily_kwh[1]:.1f}, {solar_daily_kwh[2]:.1f}] kWh/day, "
            f"consumption [{consumption_daily_kwh[0]:.1f}, "
            f"{consumption_daily_kwh[1]:.1f}, {consumption_daily_kwh[2]:.1f}] kWh/day, "
            f"charging {tonight_charge_kwh:.1f} kWh from grid"
        )

        reasoning = OptimizationReasoning(
            text=reasoning_text,
            tomorrow_solar_kwh=solar_daily_kwh[1] if len(solar_daily_kwh) > 1 else 0.0,
            expected_consumption_kwh=sum(consumption_daily_kwh) / len(consumption_daily_kwh),
            charge_energy_kwh=tonight_charge_kwh,
            cost_estimate_eur=cost_estimate_eur,
            evopt_status="WeatherScheduler",
        )

        schedule = ChargeSchedule(
            slots=[huawei_slot, victron_slot],
            reasoning=reasoning,
            computed_at=datetime.now(tz=timezone.utc),
            stale=False,
        )

        # ------------------------------------------------------------------
        # 7. Build DayPlan containers
        # ------------------------------------------------------------------
        today = date.today()
        day_plans: list[DayPlan] = []
        for d in range(3):
            day_date = today + timedelta(days=d)
            solar_kwh = solar_daily_kwh[d]
            cons_kwh = consumption_daily_kwh[d]
            net = solar_kwh - cons_kwh
            conf = _DAY_CONFIDENCE[d]
            advisory = d > 0

            # Day 0 gets real slots, others are advisory
            day_slots = [huawei_slot, victron_slot] if d == 0 else []
            day_charge = tonight_charge_kwh if d == 0 else deficits[d]

            day_plans.append(DayPlan(
                day_index=d,
                date=day_date,
                solar_forecast_kwh=solar_kwh,
                consumption_forecast_kwh=cons_kwh,
                net_energy_kwh=net,
                confidence=conf,
                charge_target_kwh=day_charge,
                slots=day_slots,
                advisory=advisory,
            ))

        # ------------------------------------------------------------------
        # 8. Update scheduler state
        # ------------------------------------------------------------------
        self.active_schedule = schedule
        self.schedule_stale = False
        self.active_day_plans = day_plans
        self._last_solar_daily_kwh = solar_daily_kwh

        logger.info(
            "WeatherScheduler: solar=[%.1f, %.1f, %.1f] "
            "consumption=[%.1f, %.1f, %.1f] tonight=%.1f kWh",
            *solar_daily_kwh[:3],
            *consumption_daily_kwh[:3],
            tonight_charge_kwh,
        )

        # ------------------------------------------------------------------
        # 9. Write to InfluxDB (fire-and-forget)
        # ------------------------------------------------------------------
        if writer is not None:
            try:
                await writer.write_charge_schedule(schedule)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "WeatherScheduler: writer.write_charge_schedule failed: %s", exc
                )

        return schedule
