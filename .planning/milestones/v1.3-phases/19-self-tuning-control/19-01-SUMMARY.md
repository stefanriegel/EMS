---
phase: 19-self-tuning-control
plan: 01
subsystem: orchestration
tags: [adaptive-tuning, dead-band, ramp-rate, min-soc, shadow-mode, rollback]

requires:
  - phase: 17-enhanced-forecasting
    provides: ConsumptionForecaster with get_ml_status() and predict_hourly() for activation gate and min-SoC profile
  - phase: 18-anomaly-detection
    provides: Nightly training loop pattern and JSON state persistence pattern
provides:
  - SelfTuner class with oscillation counting, dead-band/ramp/min-SoC tuning, shadow mode, bounded changes, rollback, and coordinator parameter injection
affects: [19-02 integration plan, coordinator, main.py wiring]

tech-stack:
  added: []
  patterns: [per-cycle in-memory event recording, nightly batch parameter computation, coordinator field injection via set_coordinator pattern, 14-day shadow-to-live promotion]

key-files:
  created:
    - backend/self_tuner.py
    - tests/test_self_tuner.py
  modified: []

key-decisions:
  - "Oscillation thresholds: >6 trans/hr increases dead-band, <2 decreases -- uses 7-day rolling average"
  - "Grid spikes only counted on state transition coincidence to avoid false positives from EV/heat pump"
  - "Min-SoC profile uses 4-hour blocks (6 windows) -- hourly too noisy, 4-hour captures morning/evening peaks"
  - "10% bound uses base value not current value to prevent asymptotic convergence"
  - "Ramp rate applied symmetrically to both huawei and victron ramp_w_per_cycle fields"

patterns-established:
  - "SelfTuner set_coordinator injection: follows existing set_anomaly_detector/set_export_advisor pattern"
  - "Shadow mode with persisted day counter for restart resilience"
  - "HA override tracking: mark_ha_override() prevents nightly tuning from overwriting user-set values"

requirements-completed: [TUNE-01, TUNE-02, TUNE-03, TUNE-04, TUNE-05, TUNE-06, TUNE-07, TUNE-08]

duration: 4min
completed: 2026-03-24
---

# Phase 19 Plan 01: Self-Tuning Control Summary

**SelfTuner engine with adaptive dead-band/ramp/min-SoC tuning, 14-day shadow mode, bounded 10%-per-night changes, automatic rollback, and coordinator runtime field injection**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-24T07:36:41Z
- **Completed:** 2026-03-24T07:41:06Z
- **Tasks:** 1 (TDD: test + feat)
- **Files modified:** 2

## Accomplishments
- SelfTuner class with all 8 TUNE requirement implementations
- 55 passing unit tests covering oscillation counting, dead-band tuning, ramp rate tuning, min-SoC profile generation, shadow mode, bounded changes, rollback, activation gate, and _apply_params
- Coordinator parameter injection via set_coordinator() / _apply_params() pattern
- HA command override tracking to prevent nightly tuning from overwriting user preferences

## Task Commits

Each task was committed atomically:

1. **Task 1: Create SelfTuner module with all tuning logic** - `26f01db` (test: failing tests) + `a6ce357` (feat: implementation)

## Files Created/Modified
- `backend/self_tuner.py` - SelfTuner class with TuningParams, TuningState dataclasses and all tuning logic
- `tests/test_self_tuner.py` - 55 unit tests covering all 8 TUNE requirements plus _apply_params

## Decisions Made
- Oscillation thresholds set to >6 trans/hr (increase dead-band) and <2 (decrease) using 7-day rolling average per research recommendation
- Grid spikes only counted when coincident with state transition (pitfall #2) to avoid EV/heat pump false positives
- Min-SoC profile uses 6 four-hour blocks; above-average consumption blocks get 20% min-SoC, below-average get 10%
- 10% per-night bound calculated against base value (not current) per pitfall #5 to prevent asymptotic convergence
- Ramp rate written to both huawei_ramp_w_per_cycle and victron_ramp_w_per_cycle (single tuning domain)

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None - all tuning logic is fully implemented with real computation.

## Issues Encountered
- Ramp rate increase test initially failed because default base (2000) equals clamp max -- adjusted test to use lower starting value (1500) to make increase observable

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- SelfTuner class ready for integration into coordinator and main.py lifespan (Plan 19-02)
- set_coordinator() injection point ready for wiring
- nightly_tune() ready to be called from _nightly_scheduler_loop

## Self-Check: PASSED

- backend/self_tuner.py: FOUND
- tests/test_self_tuner.py: FOUND
- 19-01-SUMMARY.md: FOUND
- Commit 26f01db (test): FOUND
- Commit a6ce357 (feat): FOUND
- All 55 tests passing

---
*Phase: 19-self-tuning-control*
*Completed: 2026-03-24*
