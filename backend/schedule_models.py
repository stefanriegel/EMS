"""Shared typed dataclasses for the EMS schedule pipeline (S01–S05).

These types flow from the EVCC/EVopt data pipeline (S01) through the
Scheduler (S03) and down to the control drivers (S02/S04).  No runtime
dependencies beyond stdlib ``dataclasses`` and ``datetime``.

Observability notes
-------------------
- ``EvccState.evopt_status`` carries the raw ``"Optimal"`` / ``"Infeasible"``
  string from EVopt for dashboard display and log filtering.
- ``ChargeSchedule.stale`` is set ``True`` by the Scheduler (S03) when
  ``EvccClient.get_state()`` returns ``None``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

logger = logging.getLogger("ems.evcc")

# ---------------------------------------------------------------------------
# EVCC / EVopt data layer (produced by EvccClient.get_state)
# ---------------------------------------------------------------------------


@dataclass
class EvoptBatteryTimeseries:
    """Per-battery charging/discharging power and SoC timeseries from EVopt.

    All list fields have equal length (``n_slots``).  Slot duration is 15 min.

    Attributes:
        title:               Battery display name, e.g. ``"Emma Akku 1"``.
        charging_power_w:    Scheduled charging power in watts per slot.
        discharging_power_w: Scheduled discharging power in watts per slot.
        soc_fraction:        State-of-charge fraction (0.0–1.0) per slot.
        slot_timestamps_utc: UTC start timestamp for each slot.  Slot 0 is
                             *now*, not midnight — derived from
                             ``evopt.res.details.timestamp[0]``.
    """

    title: str
    charging_power_w: list[float]
    discharging_power_w: list[float]
    soc_fraction: list[float]
    slot_timestamps_utc: list[datetime]


@dataclass
class EvoptResult:
    """Top-level EVopt optimisation result for the current horizon.

    Attributes:
        status:          Solver status string, e.g. ``"Optimal"``.
        objective_value: Solver objective (cost) value.
        batteries:       Per-battery timeseries in the same order as EVCC returns.
    """

    status: str
    objective_value: float
    batteries: list[EvoptBatteryTimeseries]

    # ------------------------------------------------------------------
    # Huawei LUNA2000 target SoC
    # ------------------------------------------------------------------

    def get_huawei_target_soc_pct(
        self,
        huawei_capacity_kwh: float,
        initial_soc_pct: float = 0.0,
    ) -> float:
        """Return the projected Huawei LUNA2000 SoC after the next 24 h.

        Filters batteries whose title is ``"Emma Akku 1"`` or
        ``"Emma Akku 2"`` (runtime name match, never by index), sums the
        net charge energy over the first 96 slots (24 h at 15-min resolution),
        and converts to a SoC percentage clamped to [10.0, 95.0].

        Parameters
        ----------
        huawei_capacity_kwh:
            Usable capacity of both LUNA packs combined, in kWh.
        initial_soc_pct:
            Starting SoC percentage (default 0.0 — useful for delta tests).

        Returns
        -------
        float
            Projected target SoC percentage, clamped to [10.0, 95.0].
            Returns *initial_soc_pct* (clamped) if no Emma packs are found.
        """
        huawei_titles = {"Emma Akku 1", "Emma Akku 2"}
        packs = [b for b in self.batteries if b.title in huawei_titles]

        if not packs:
            logger.warning(
                "get_huawei_target_soc_pct: no Emma Akku batteries found in EVopt result"
            )
            return float(initial_soc_pct)

        net_energy_wh = 0.0
        for bat in packs:
            slots = list(zip(bat.charging_power_w[:96], bat.discharging_power_w[:96]))
            net_energy_wh += sum((c - d) * (15 / 60) for c, d in slots)

        target = initial_soc_pct + (net_energy_wh / (huawei_capacity_kwh * 1000)) * 100
        return float(max(10.0, min(95.0, target)))

    # ------------------------------------------------------------------
    # Victron MPII target SoC
    # ------------------------------------------------------------------

    def get_victron_target_soc_pct(
        self,
        victron_capacity_kwh: float,
        initial_soc_pct: float = 0.0,
    ) -> float:
        """Return the projected Victron MPII SoC after the next 24 h.

        Filters batteries whose title is exactly ``"Victron"``.

        Parameters
        ----------
        victron_capacity_kwh:
            Usable capacity of the Victron battery, in kWh.
        initial_soc_pct:
            Starting SoC percentage (default 0.0).

        Returns
        -------
        float
            Projected target SoC percentage, clamped to [10.0, 95.0].
            Returns *initial_soc_pct* (clamped) if no Victron pack is found.
        """
        packs = [b for b in self.batteries if b.title == "Victron"]

        if not packs:
            logger.warning(
                "get_victron_target_soc_pct: no Victron battery found in EVopt result"
            )
            return float(initial_soc_pct)

        net_energy_wh = 0.0
        for bat in packs:
            slots = list(zip(bat.charging_power_w[:96], bat.discharging_power_w[:96]))
            net_energy_wh += sum((c - d) * (15 / 60) for c, d in slots)

        target = initial_soc_pct + (net_energy_wh / (victron_capacity_kwh * 1000)) * 100
        return float(max(10.0, min(95.0, target)))


@dataclass
class SolarForecast:
    """Solar generation forecast data from EVCC.

    Attributes:
        timeseries_w:         Per-slot power forecast in watts.
        slot_timestamps_utc:  UTC start timestamp for each slot.
        tomorrow_energy_wh:   Total forecasted energy for tomorrow in Wh.
        day_after_energy_wh:  Total forecasted energy for the day after tomorrow in Wh.
    """

    timeseries_w: list[float]
    slot_timestamps_utc: list[datetime]
    tomorrow_energy_wh: float
    day_after_energy_wh: float


@dataclass
class SolarForecastMultiDay:
    """Multi-day solar forecast with hourly resolution and source attribution.

    Attributes:
        hourly_wh:       72 hourly energy values in Wh (3 days x 24 hours).
        daily_energy_wh: Per-day total energy in Wh: [today, tomorrow, day_after].
        source:          Data source identifier: ``"evcc"``, ``"open_meteo"``,
                         or ``"seasonal"``.
        fetched_at:      UTC timestamp when this forecast was obtained.
    """

    hourly_wh: list[float]
    daily_energy_wh: list[float]
    source: str
    fetched_at: datetime


@dataclass
class GridPriceSeries:
    """Grid import and feed-in price timeseries from EVCC.

    Attributes:
        import_eur_kwh:      Import price in €/kWh per slot.
        export_eur_kwh:      Feed-in (export) price in €/kWh per slot.
        slot_timestamps_utc: UTC start timestamp for each slot.
    """

    import_eur_kwh: list[float]
    export_eur_kwh: list[float]
    slot_timestamps_utc: list[datetime]


@dataclass
class EvccState:
    """Parsed snapshot of the EVCC ``/api/state`` endpoint.

    Sub-fields are ``None`` when the corresponding section is absent from the
    EVCC response (e.g. EVopt solver not yet run, forecast unavailable).

    Attributes:
        evopt:        EVopt optimisation result, or ``None``.
        solar:        Solar generation forecast, or ``None``.
        grid_prices:  Grid price timeseries, or ``None``.
        evopt_status: Raw solver status string (``"Optimal"``, ``"Infeasible"``,
                      ``"unknown"``), always present for dashboard display.
    """

    evopt: EvoptResult | None
    solar: SolarForecast | None
    grid_prices: GridPriceSeries | None
    evopt_status: str


# ---------------------------------------------------------------------------
# Schedule pipeline (produced by Scheduler in S03, consumed by S02/S04)
# ---------------------------------------------------------------------------


@dataclass
class ChargeSlot:
    """A single scheduled battery charge window.

    Attributes:
        battery:             Battery identifier, e.g. ``"huawei"`` or ``"victron"``.
        target_soc_pct:      Desired SoC at slot end, as a percentage.
        start_utc:           Slot start in UTC.
        end_utc:             Slot end in UTC.
        grid_charge_power_w: Grid charge power budget in watts.
    """

    battery: str
    target_soc_pct: float
    start_utc: datetime
    end_utc: datetime
    grid_charge_power_w: int


@dataclass
class OptimizationReasoning:
    """Human-readable and structured reasoning for a charge schedule.

    Attributes:
        text:                     Free-text explanation for dashboard display.
        tomorrow_solar_kwh:       Forecasted solar energy for tomorrow in kWh.
        expected_consumption_kwh: Expected household consumption in kWh.
        charge_energy_kwh:        Total energy to charge from grid in kWh.
        cost_estimate_eur:        Estimated cost of the schedule in euros.
        evopt_status:             Raw EVopt solver status (``"Optimal"``,
                                  ``"Heuristic"``, ``"Unavailable"``).
    """

    text: str
    tomorrow_solar_kwh: float
    expected_consumption_kwh: float
    charge_energy_kwh: float
    cost_estimate_eur: float
    evopt_status: str = "Heuristic"


@dataclass
class ChargeSchedule:
    """A complete optimised charge schedule for the battery pool.

    Produced by the Scheduler (S03) and consumed by the control drivers
    (S02/S04).  ``stale`` is set ``True`` when ``EvccClient.get_state()``
    returns ``None`` and the schedule could not be refreshed.

    Attributes:
        slots:       Ordered list of charge windows.
        reasoning:   Explanation and cost estimate for this schedule.
        computed_at: UTC timestamp when this schedule was generated.
        stale:       ``True`` if the schedule could not be refreshed from EVCC.
    """

    slots: list[ChargeSlot]
    reasoning: OptimizationReasoning
    computed_at: datetime
    stale: bool = False


@dataclass
class DayPlan:
    """Per-day charge plan within a multi-day weather-aware schedule.

    Day 0 is actionable (tonight's charge window).
    Days 1-2 are advisory (shown in dashboard, not executed).

    Attributes:
        day_index:                0=today/tonight, 1=tomorrow, 2=day_after.
        date:                     Calendar date for this day.
        solar_forecast_kwh:       Expected solar production in kWh.
        consumption_forecast_kwh: Expected consumption in kWh.
        net_energy_kwh:           Solar minus consumption (positive = surplus).
        confidence:               Confidence weight (1.0, 0.8, or 0.6).
        charge_target_kwh:        Grid charge energy needed for this day in kWh.
        slots:                    Charge slots (populated for Day 0, empty for
                                  advisory days).
        advisory:                 ``True`` for Day 1/2 (not executed).
    """

    day_index: int
    date: date
    solar_forecast_kwh: float
    consumption_forecast_kwh: float
    net_energy_kwh: float
    confidence: float
    charge_target_kwh: float
    slots: list[ChargeSlot]
    advisory: bool


@dataclass
class ConsumptionForecast:
    """Weekday-aware household consumption forecast from InfluxDB history.

    Produced by ``InfluxMetricsReader.query_consumption_history()`` (S02) and
    consumed by the Scheduler (S03) to set charge targets.

    Observability notes
    -------------------
    - ``fallback_used=True`` signals cold-start or InfluxDB failure; callers
      (S03 Scheduler) should treat ``today_expected_kwh`` as a rough estimate.
    - ``days_of_history`` drives the ``WARNING "consumption history: only N
      days of data, using seasonal fallback"`` log when ``< 7``.
    - A returned instance with ``fallback_used=False`` and a populated
      ``kwh_by_weekday`` is the happy-path signal — no additional grep needed.

    Attributes:
        kwh_by_weekday:     Mean daily consumption in kWh per weekday
                            (0=Monday … 6=Sunday). Empty dict when
                            fewer than 7 days of data are available.
        today_expected_kwh: kwh_by_weekday[today.weekday()], or the
                            seasonal fallback constant when data is
                            insufficient.
        days_of_history:    Number of distinct calendar days with data
                            in the query window.
        fallback_used:      True when today_expected_kwh is a seasonal
                            constant (< 7 days of data or query error).
    """

    kwh_by_weekday: dict[int, float]   # 0=Mon … 6=Sun
    today_expected_kwh: float
    days_of_history: int
    fallback_used: bool


@dataclass
class HourlyConsumptionForecast:
    """Hourly household consumption forecast for a configurable horizon.

    Produced by ``ConsumptionForecaster.predict_hourly()`` and consumed by
    the multi-day weather-aware scheduler to plan charge/discharge windows.

    Attributes:
        hourly_kwh:    Per-hour predicted consumption in kWh.
        total_kwh:     Sum of all hourly values.
        horizon_hours: Number of hours in the prediction horizon.
        source:        Origin of the forecast — ``"ml"`` or ``"seasonal"``.
        fallback_used: ``True`` when ML models are unavailable and seasonal
                       hour-of-day weights were used instead.
    """

    hourly_kwh: list[float]
    total_kwh: float
    horizon_hours: int
    source: str
    fallback_used: bool
