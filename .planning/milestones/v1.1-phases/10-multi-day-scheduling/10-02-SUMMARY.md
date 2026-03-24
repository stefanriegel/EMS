---
phase: 10-multi-day-scheduling
plan: 02
subsystem: scheduler
tags: [weather-scheduling, intraday-replan, forecast-deviation, asyncio-lock]

# Dependency graph
requires:
  - phase: 10-multi-day-scheduling
    plan: 01
    provides: WeatherScheduler class, _last_solar_daily_kwh for deviation checks
provides:
  - check_forecast_deviation method for intra-day solar forecast monitoring
  - asyncio.Lock serializing concurrent compute_schedule calls
  - _intraday_replan_loop running every 6 hours
  - WeatherScheduler wired as app.state.scheduler (coordinator-transparent)
affects: [coordinator, api, dashboard]

# Tech tracking
tech-stack:
  added: []
  patterns: [intraday-replan-loop, forecast-deviation-gating]

key-files:
  created: []
  modified:
    - backend/weather_scheduler.py
    - backend/main.py
    - tests/test_weather_scheduler.py

key-decisions:
  - "check_forecast_deviation fetches fresh solar and compares per-day against stored values"
  - "Threshold of 20% relative deviation on any single day triggers replan"
  - "Zero-to-significant (>1 kWh) treated as deviation even when old value was 0"
  - "compute_schedule body extracted to _compute_schedule_unlocked behind asyncio.Lock"

patterns-established:
  - "Intra-day loop: initial delay then periodic check with deviation gating"
  - "Lock-protected schedule computation: nightly and intra-day share same lock"

requirements-completed: [MDS-04]

# Metrics
duration: 4min
completed: 2026-03-23
---

# Phase 10 Plan 02: Intra-Day Re-Planning Summary

**Forecast deviation detection with 20% threshold gating and 6-hour intra-day replan loop wired into FastAPI lifespan**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-23T14:54:55Z
- **Completed:** 2026-03-23T14:59:03Z
- **Tasks:** 2 (Task 1: TDD RED+GREEN, Task 2: auto)
- **Files modified:** 3

## Accomplishments
- check_forecast_deviation method compares fresh solar forecast against last-computed values with configurable threshold
- asyncio.Lock prevents concurrent nightly and intra-day compute_schedule calls from corrupting state
- _intraday_replan_loop runs every 6 hours, only triggers recompute when solar deviates >20%
- WeatherScheduler replaces raw Scheduler on app.state.scheduler -- coordinator and API consume it transparently
- 5 new tests covering deviation detection, stability, no-prior-data, zero-to-significant, and lock serialization

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Failing tests for deviation/lock** - `2f0bda1` (test)
2. **Task 1 GREEN: check_forecast_deviation + asyncio.Lock** - `20dafcb` (feat)
3. **Task 2: Lifespan wiring + intra-day loop** - `ed47c04` (feat)

## Files Created/Modified
- `backend/weather_scheduler.py` - Added check_forecast_deviation method, asyncio.Lock, _compute_schedule_unlocked extraction
- `backend/main.py` - WeatherScheduler wiring, _intraday_replan_loop, intraday_task lifecycle
- `tests/test_weather_scheduler.py` - 5 new tests for deviation and lock behavior

## Decisions Made
- Extracted compute_schedule body to _compute_schedule_unlocked so the lock wrapper is clean
- Zero-to-significant threshold at 1.0 kWh to avoid noise from near-zero forecasts
- Lock test restricted to asyncio backend (asyncio.Lock incompatible with trio)
- Degraded mode in main.py sets intraday_task to None to prevent shutdown errors

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Added intraday_task=None in degraded mode**
- **Found during:** Task 2
- **Issue:** Shutdown code tried to cancel intraday_task via getattr but degraded path did not initialize it, causing ValueError on test
- **Fix:** Added `app.state.intraday_task = None` in the KeyError except block
- **Files modified:** backend/main.py
- **Committed in:** ed47c04 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential fix for degraded mode correctness. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Multi-day scheduling complete: 3-day horizon with confidence weighting and intra-day adaptation
- WeatherScheduler is the canonical scheduler on app.state -- all consumers use it transparently
- Phase 10 complete -- ready for milestone validation

## Self-Check: PASSED

- backend/weather_scheduler.py: FOUND
- backend/main.py: FOUND
- tests/test_weather_scheduler.py: FOUND
- Commit 2f0bda1: FOUND
- Commit 20dafcb: FOUND
- Commit ed47c04: FOUND

---
*Phase: 10-multi-day-scheduling*
*Completed: 2026-03-23*
