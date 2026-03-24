---
phase: 18-anomaly-detection
plan: 01
subsystem: ml
tags: [anomaly-detection, isolation-forest, sklearn, time-series, ema]

# Dependency graph
requires:
  - phase: 16-ml-infrastructure
    provides: ModelStore for model persistence, anyio.to_thread pattern
provides:
  - AnomalyDetector class with check_cycle(), nightly_train(), get_events(), get_battery_health()
  - AnomalyDetectorConfig dataclass in config.py
  - Three detection domains: comm loss, consumption spikes, battery health drift
  - Tiered alert escalation with per-type cooldowns
affects: [18-02-integration, coordinator, api, orchestrator]

# Tech tracking
tech-stack:
  added: []
  patterns: [check-before-update EMA baselines, dual-tier anomaly detection (nightly ML + per-cycle thresholds)]

key-files:
  created:
    - backend/anomaly_detector.py
    - tests/test_anomaly_detector.py
  modified:
    - backend/config.py

key-decisions:
  - "Check deviation BEFORE updating EMA baseline to prevent anomalous values from contaminating thresholds"
  - "Use per-anomaly-type:system composite keys for escalation and cooldown tracking"
  - "Overridable _now_mono clock function for deterministic time-dependent testing"

patterns-established:
  - "Check-before-update: deviation check runs against pre-update baseline, then baseline is updated with the observation"
  - "Atomic JSON persistence: write to .tmp then rename for crash safety"

requirements-completed: [ANOM-01, ANOM-02, ANOM-03, ANOM-04, ANOM-05, ANOM-06, ANOM-07]

# Metrics
duration: 8min
completed: 2026-03-24
---

# Phase 18 Plan 01: Anomaly Detector Core Summary

**AnomalyDetector with 3 detection domains (comm loss, consumption spikes, battery health), tiered alert escalation, nightly IsolationForest training, and per-cycle float-only threshold checks**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-24T00:04:58Z
- **Completed:** 2026-03-24T00:13:35Z
- **Tasks:** 1 (TDD)
- **Files modified:** 3

## Accomplishments
- Communication loss pattern detection tracking failure windows with configurable gaps
- Consumption spike detection against hourly EMA baselines with cold-start protection
- Battery health drift: SoC curve rate deviations per band and round-trip efficiency tracking
- Tiered alert escalation (warning at 1, alert at 3+ within 24h) with per-type cooldowns
- Nightly IsolationForest training via anyio.to_thread.run_sync with ModelStore persistence
- Per-cycle check_cycle() uses only float comparisons, zero ML library calls
- JSON persistence with atomic write (tmp + rename) for events and baselines
- 17 unit tests covering all detection domains and edge cases

## Task Commits

Each task was committed atomically:

1. **Task 1: AnomalyDetector core module with all detection domains** - `e63e8af` (feat)

**Plan metadata:** pending

## Files Created/Modified
- `backend/anomaly_detector.py` - Core AnomalyDetector class (768 lines) with AnomalyEvent, HourlyBaseline, SocBandBaseline dataclasses, escalation/cooldown trackers, and JSON persistence
- `backend/config.py` - Added AnomalyDetectorConfig dataclass with from_env() classmethod
- `tests/test_anomaly_detector.py` - 17 test functions covering all 3 detection domains, escalation, persistence, nightly training, and no-sklearn verification

## Decisions Made
- Check deviation BEFORE updating EMA baseline to prevent anomalous values from contaminating thresholds (discovered during TDD RED phase)
- Use composite keys (e.g., "comm_loss:huawei") for per-system escalation and cooldown tracking
- Overridable _now_mono clock function enables deterministic testing of time-dependent detection logic
- IsolationForest training requires at least 10 hourly baseline samples to avoid degenerate fits

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Check-before-update for EMA baselines**
- **Found during:** Task 1 (TDD GREEN phase)
- **Issue:** Baseline update ran before deviation check, causing anomalous values to contaminate the threshold calculation (spike raises mean+std, making itself appear normal)
- **Fix:** Reordered to check deviation against pre-update baseline, then update with observation
- **Files modified:** backend/anomaly_detector.py (_check_consumption, _check_soc_rate)
- **Verification:** test_consumption_spike and test_soc_curve_anomaly pass
- **Committed in:** e63e8af

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential correctness fix. Without check-before-update, anomaly detection would miss spikes.

## Issues Encountered
None beyond the deviation above.

## Known Stubs
None - all detection domains are fully implemented and wired.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- AnomalyDetector ready for coordinator integration (Plan 02)
- check_cycle() accepts ControllerSnapshot pairs, returns AnomalyEvent list
- nightly_train() ready for _nightly_scheduler_loop hook
- get_events() and get_battery_health() ready for API endpoints

---
*Phase: 18-anomaly-detection*
*Completed: 2026-03-24*
