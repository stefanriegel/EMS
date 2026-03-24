---
phase: 17-consumption-forecaster-upgrade
plan: 02
subsystem: ml
tags: [scikit-learn, mape, model-health, rest-api, consumption-forecast]

# Dependency graph
requires:
  - phase: 17-consumption-forecaster-upgrade
    provides: HistGBR models with 8-feature matrix, ModelStore persistence
provides:
  - Daily MAPE computation comparing hourly predictions vs actual consumption
  - MAPE history persistence in /config/ems_models/mape_history.json (30-day rolling)
  - GET /api/ml/status endpoint for model health visibility
  - get_ml_status() method on ConsumptionForecaster
  - ConsumptionForecaster wired to app.state for API access
affects: [18-anomaly-detection, 19-self-tuning]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "MAPE computation with near-zero filtering (< 0.1 kWh threshold)"
    - "get_forecaster DI dependency for ConsumptionForecaster API access"
    - "Fire-and-forget MAPE computation in retrain_if_stale (non-fatal on error)"

key-files:
  created: []
  modified:
    - backend/consumption_forecaster.py
    - backend/api.py
    - backend/main.py
    - tests/test_consumption_forecaster.py
    - tests/test_api.py

key-decisions:
  - "MAPE filters hours where actual < 0.1 kWh to avoid explosion on near-zero values"
  - "MAPE requires 12+ valid hours (out of 24) to produce a value, returns None otherwise"
  - "MAPE computed in retrain_if_stale before retraining, wrapped in try/except for non-fatal failure"
  - "MAPE history stored as JSON array with 30-day rolling window"

patterns-established:
  - "get_forecaster DI pattern: getattr(request.app.state, 'consumption_forecaster', None)"
  - "MAPE history file lives alongside model artifacts in ModelStore directory"

requirements-completed: [FCST-05]

# Metrics
duration: 8min
completed: 2026-03-23
---

# Phase 17 Plan 02: MAPE Tracking and ML Status API Summary

**Daily MAPE tracking with 30-day rolling history, near-zero filtering, and /api/ml/status endpoint exposing model health, training info, and prediction accuracy**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-23T23:30:22Z
- **Completed:** 2026-03-23T23:38:15Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Added _compute_daily_mape with near-zero filtering (< 0.1 kWh) requiring 12+ valid hours
- Added MAPE history persistence as JSON array with 30-day rolling window
- Integrated MAPE computation into retrain_if_stale (fire-and-forget on error)
- Added get_ml_status() returning model names, training time, sample count, MAPE history, sklearn version, min_training_days
- Added GET /api/ml/status endpoint with 503 when forecaster unavailable
- Wired ConsumptionForecaster to app.state in main.py lifespan (normal + degraded paths)
- 1428 tests passing including 9 new tests for MAPE and ML status

## Task Commits

Each task was committed atomically:

1. **Task 1: MAPE tracking and get_ml_status in ConsumptionForecaster** - `21b86d3` (feat)
2. **Task 2: /api/ml/status endpoint and tests (TDD):**
   - RED: `afc6d25` (test) - failing tests for MAPE helpers and API endpoint
   - GREEN: `8594d15` (feat) - endpoint implementation, all tests pass

## Files Created/Modified
- `backend/consumption_forecaster.py` - Added _compute_daily_mape, _save_mape_history, _load_mape_history, get_ml_status(), MAPE tracking in retrain_if_stale
- `backend/api.py` - Added get_forecaster DI dependency and GET /api/ml/status endpoint
- `backend/main.py` - Wired app.state.consumption_forecaster in lifespan (normal + degraded)
- `tests/test_consumption_forecaster.py` - 7 new tests for MAPE helpers and get_ml_status
- `tests/test_api.py` - 2 new tests for /api/ml/status (200 and 503 responses)

## Decisions Made
- MAPE filters hours where actual < 0.1 kWh to avoid percentage explosion on near-zero values
- 12-hour minimum threshold ensures MAPE is meaningful (at least half a day of valid data)
- MAPE computation is fire-and-forget in retrain_if_stale -- retrain always proceeds even if MAPE fails
- MAPE history stored as simple JSON array alongside model artifacts in ModelStore directory

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all features are fully wired with real data sources.

## Next Phase Readiness
- MAPE tracking ready to gate Phase 19 self-tuning activation (requires MAPE < 25%)
- /api/ml/status endpoint available for dashboard integration
- Model health data available for Phase 18 anomaly detection baseline

---
*Phase: 17-consumption-forecaster-upgrade*
*Completed: 2026-03-23*
