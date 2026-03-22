---
phase: 03-pv-tariff-optimization
plan: 02
subsystem: scheduler
tags: [solar-forecast, predictive-charging, formula-fallback, energy-optimization]

# Dependency graph
requires:
  - phase: 03-pv-tariff-optimization
    provides: "EVCC client, schedule_models with SolarForecast/EvccState/ConsumptionForecast"
provides:
  - "Solar-aware grid charge target reduction in scheduler formula fallback"
  - "Predictive pre-charging: skip, reduce, or full charge based on solar forecast"
affects: [dashboard, api, orchestrator]

# Tech tracking
tech-stack:
  added: []
  patterns: ["solar-aware formula fallback with 1.2x skip threshold and 0.8x discount"]

key-files:
  created: []
  modified:
    - backend/scheduler.py
    - tests/test_scheduler.py

key-decisions:
  - "Solar reduction only in formula fallback, EVopt path untouched (D-18)"
  - "1.2x threshold for full skip, 0.8x discount for partial coverage"
  - "solar=None (EVCC offline) and solar=0 (rainy day) both produce full charge but via different code paths"

patterns-established:
  - "Predictive pre-charging: three-branch solar-aware logic (skip/reduce/full) in formula fallback"

requirements-completed: [OPT-04]

# Metrics
duration: 2min
completed: 2026-03-22
---

# Phase 03 Plan 02: Predictive Pre-Charging Summary

**Solar-aware grid charge target reduction: skip charge when solar covers 120% of demand, reduce with 0.8x discount for partial coverage, full charge as safety fallback**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-22T09:27:19Z
- **Completed:** 2026-03-22T09:29:30Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Scheduler skips grid charge when solar forecast >= 1.2x expected consumption (D-10)
- Partial solar coverage reduces grid charge target using 0.8x discount factor (D-11)
- Full charge maintained when no solar forecast available or zero solar (D-12)
- EVopt path unchanged -- solar reduction only applies to formula fallback (D-18)
- 7 new tests in TestPredictivePreCharging class covering all scenarios
- Reasoning text reflects solar skip/reduction decisions for dashboard transparency

## Task Commits

Each task was committed atomically:

1. **Task 1: Add solar-aware target reduction to scheduler formula fallback** - `7e958f6` (feat)
2. **Task 2: Add predictive pre-charging tests to test_scheduler.py** - `6a6efb2` (test)

## Files Created/Modified
- `backend/scheduler.py` - Solar-aware three-branch formula fallback (skip/reduce/full), solar-aware reasoning text, EVopt-gated charge_energy_kwh
- `tests/test_scheduler.py` - TestPredictivePreCharging class with 7 test methods covering D-10, D-11, D-12, D-18

## Decisions Made
- Solar reduction only applies in formula fallback path, not EVopt path (D-18 compliance)
- 1.2x threshold chosen for full skip to provide safety margin
- 0.8x discount factor on solar for partial coverage accounts for forecast uncertainty
- Distinct code paths for solar=None vs solar=0: same result (full charge) but different reasoning

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Pre-existing test failure in `test_coordinator.py::TestPvSurplusHeadroomWeighting::test_equal_soc_equal_split` -- unrelated to scheduler changes, out of scope

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Scheduler now makes solar-aware decisions in formula fallback path
- Ready for dashboard integration to display solar skip/reduction reasoning
- All 82 scheduler tests pass

---
*Phase: 03-pv-tariff-optimization*
*Completed: 2026-03-22*
