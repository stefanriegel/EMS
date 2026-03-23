---
phase: 17-consumption-forecaster-upgrade
plan: 01
subsystem: ml
tags: [scikit-learn, HistGBR, time-series, cross-validation, feature-engineering, weather-forecast]

# Dependency graph
requires:
  - phase: 16-ml-infra-foundation
    provides: FeaturePipeline for centralised raw data extraction, ModelStore for model persistence
provides:
  - HistGradientBoostingRegressor models with 8-feature matrix
  - Recency-weighted training with 30-day half-life
  - Time-series cross-validation with logged MAPE scores
  - OpenMeteoClient.get_temperature_forecast() for real weather data
  - FeaturePipeline wired into train() for raw data extraction
  - Feature count validation on model load (discards old 5-feature models)
affects: [17-02-PLAN, 18-anomaly-detection, 19-self-tuning]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "HistGBR with native NaN handling for lag features"
    - "Recency weighting via sample_weight exponential decay"
    - "TimeSeriesSplit cross-validation before final fit"
    - "Closure-based CV+fit wrapped in single anyio.to_thread.run_sync call"

key-files:
  created: []
  modified:
    - backend/consumption_forecaster.py
    - backend/weather_client.py
    - tests/test_consumption_forecaster.py

key-decisions:
  - "Used params= instead of fit_params= for cross_val_score (sklearn 1.8+ API)"
  - "Lag features use float('nan') for missing history -- HistGBR handles NaN natively"
  - "Weather forecast padded with last value when shorter than requested hours"
  - "Last outdoor temp from training stored as fallback for predictions without weather client"

patterns-established:
  - "CV+fit in single closure for thread offloading: wrap cross_val_score + model.fit in one function passed to anyio.to_thread.run_sync"
  - "Feature count validation on model load: discard persisted models with wrong feature count"

requirements-completed: [FCST-01, FCST-02, FCST-03, FCST-04, FCST-06, FCST-07]

# Metrics
duration: 8min
completed: 2026-03-23
---

# Phase 17 Plan 01: Consumption Forecaster Upgrade Summary

**HistGBR with 8-feature matrix (temp, calendar, lag), recency weighting, time-series CV, and real weather forecast integration via OpenMeteoClient**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-23T23:18:20Z
- **Completed:** 2026-03-23T23:26:30Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Replaced GradientBoostingRegressor with HistGradientBoostingRegressor across all 3 models (heat pump, DHW, base load)
- Extended feature matrix from 5 to 8 columns: added is_weekend, lag_24h, lag_168h
- Wired FeaturePipeline from Phase 16 into train() for raw data extraction with backward-compat reader fallback
- Added recency weighting (30-day half-life exponential decay) via sample_weight
- Integrated TimeSeriesSplit(n_splits=5) cross-validation with logged neg-MAPE scores
- Added get_temperature_forecast() to OpenMeteoClient for real weather data in predictions
- Added feature count validation on model load (discards old 5-feature models)
- 63 tests passing including 11 new tests covering all FCST requirements

## Task Commits

Each task was committed atomically:

1. **Task 1: Add temperature forecast and upgrade ConsumptionForecaster** - `d09fe7f` (feat)
   - Fix: `5c4d105` - sklearn 1.8+ API: params instead of fit_params
2. **Task 2: Tests for upgraded forecaster** - `77e9c07` (test)

## Files Created/Modified
- `backend/consumption_forecaster.py` - Upgraded forecaster with HistGBR, 8 features, recency weighting, CV, FeaturePipeline wiring
- `backend/weather_client.py` - Added get_temperature_forecast() method to OpenMeteoClient
- `tests/test_consumption_forecaster.py` - 63 tests (52 existing updated + 11 new for FCST requirements)

## Decisions Made
- Used `params=` instead of deprecated `fit_params=` for sklearn 1.8+ cross_val_score API
- HistGBR chosen over standard GBR because it handles NaN natively (no imputation needed for lag features)
- Weather forecast padded with last value when response is shorter than requested hours
- Last outdoor temp from training data stored as fallback when weather client is unavailable

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] sklearn 1.8+ API change: fit_params renamed to params**
- **Found during:** Task 2 (test execution)
- **Issue:** cross_val_score in sklearn 1.8.0 removed fit_params parameter, now uses params
- **Fix:** Replaced fit_params={"sample_weight": weights} with params={"sample_weight": weights}
- **Files modified:** backend/consumption_forecaster.py
- **Verification:** All 63 tests pass
- **Committed in:** 5c4d105

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential fix for sklearn 1.8+ compatibility. No scope creep.

## Issues Encountered
None beyond the sklearn API change noted above.

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all features are fully wired with real data sources.

## Next Phase Readiness
- Upgraded forecaster ready for MAPE evaluation in Plan 02
- FeaturePipeline integration provides clean data extraction path
- Weather forecast integration provides real temperature data for predictions
- 8-feature model ready for anomaly detection in Phase 18

---
*Phase: 17-consumption-forecaster-upgrade*
*Completed: 2026-03-23*
