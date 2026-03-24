# Phase 10: Multi-Day Scheduling - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning
**Mode:** Auto-generated (algorithmic phase — clear success criteria from ROADMAP)

<domain>
## Phase Boundary

Nightly charge scheduling uses multi-day weather and consumption outlook to set smarter grid charge targets. DayPlan model extends ChargeSchedule with per-day containers. WeatherScheduler wraps existing Scheduler without modifying it. Confidence discounting by day horizon. Intra-day re-planning on forecast deviation. Conservative charge ceiling for forecast uncertainty.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — algorithmic phase with clear success criteria. Key design notes from research and prior context:
- WeatherScheduler wraps existing Scheduler (decorator pattern — don't modify Scheduler)
- DayPlan model extends ChargeSchedule with per-day containers and day index; Day 2/3 are advisory only
- Confidence weights: Day 1 = 1.0, Day 2 = 0.8, Day 3 = 0.6
- Intra-day re-planning: re-run approximately every 6h when forecast deviates significantly
- Conservative charge ceiling: leave headroom proportional to forecast uncertainty
- Don't over-charge: forecast can be wrong, leave room for PV to fill batteries
- Winter critical: too little in battery is worse than too much grid charge
- Summer: long cloudy stretch → okay to charge on cheap slots, but don't max out

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- backend/scheduler.py — existing nightly Scheduler (DO NOT modify)
- backend/weather_client.py — OpenMeteoClient, get_solar_forecast(), SolarForecastMultiDay
- backend/consumption_forecaster.py — predict_hourly(72), HourlyConsumptionForecast
- backend/schedule_models.py — ChargeSchedule, ChargeSlot, OptimizationReasoning

### Integration Points
- main.py lifespan — WeatherScheduler wraps or augments Scheduler
- _nightly_scheduler_loop — trigger point for nightly re-computation
- Intra-day trigger — new periodic loop or extension of existing

</code_context>

<specifics>
## Specific Ideas

- User said: "dont over charge, as sometime forcast can be not 100% true"
- User said: "in summer, when its long cloudy and we havent enought power in battery, its okay to charge on cheap time slots"
- Fixed feed-in rate 0.074 EUR/kWh — never profitable to discharge battery to grid

</specifics>

<deferred>
## Deferred Ideas

None

</deferred>
