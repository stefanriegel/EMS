# Phase 9: Weather & Forecast Data - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase)

<domain>
## Phase Boundary

System has multi-day solar production forecasts and extended consumption predictions available for scheduling decisions. EVCC already provides day_after_energy_wh; Open-Meteo extends to day 3 as fallback.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — infrastructure phase with clear success criteria from ROADMAP. Key considerations:
- EVCC already parsed in backend/evcc_client.py (has tomorrow_energy_wh and day_after_energy_wh)
- Open-Meteo API is free/keyless — use open-meteo-solar-forecast library or raw httpx
- ConsumptionForecaster already has hourly resolution internally — extend to 72h
- Graceful degradation: seasonal averages as last-resort fallback
- All new data sources must be optional (fire-and-forget pattern)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- backend/evcc_client.py — SolarForecast with tomorrow/day_after energy
- backend/consumption_forecaster.py — ML models with hourly predictions
- backend/scheduler.py — nightly scheduling loop
- httpx for async HTTP calls

### Integration Points
- Scheduler uses solar forecast for charge target reduction
- ConsumptionForecaster.predict() returns scalar — needs hourly extension
- New WeatherClient for Open-Meteo fallback

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. Follow existing patterns.

</specifics>

<deferred>
## Deferred Ideas

None

</deferred>
