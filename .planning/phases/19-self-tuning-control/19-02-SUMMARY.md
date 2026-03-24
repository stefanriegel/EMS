---
phase: 19-self-tuning-control
plan: 02
subsystem: orchestration
tags: [adaptive-tuning, coordinator-wiring, nightly-scheduler, api-status]

requires:
  - phase: 19-self-tuning-control
    plan: 01
    provides: SelfTuner class with record_cycle(), nightly_tune(), mark_ha_override(), get_tuning_status(), set_coordinator()
provides:
  - SelfTuner fully integrated into coordinator control loop, nightly scheduler, and REST API
  - Bidirectional coordinator-tuner wiring (record_cycle in, _apply_params out)
  - HA override tracking on all 5 tunable parameters
affects: [dashboard, api-consumers]

tech-stack:
  added: []
  patterns: [bidirectional injection (set_self_tuner + set_coordinator), fire-and-forget per-cycle recording, nightly batch after anomaly training]

key-files:
  created: []
  modified:
    - backend/coordinator.py
    - backend/main.py
    - backend/api.py

key-decisions:
  - "SelfTuner constructed before nightly loop task so it can be passed as parameter"
  - "Bidirectional wiring deferred until coordinator exists: construct early, wire later"
  - "record_cycle() runs outside main try/except in _loop() with its own fire-and-forget handler"

patterns-established:
  - "Bidirectional injection: construct early, pass to async task, wire coordinator reference later"
  - "Five HA command handlers all notify SelfTuner via mark_ha_override with canonical param names"

requirements-completed: [TUNE-01, TUNE-02, TUNE-05]

duration: 4min
completed: 2026-03-24
---

# Phase 19 Plan 02: Self-Tuner Integration Summary

**SelfTuner wired into coordinator 5s loop (record_cycle), nightly scheduler (after anomaly training), REST API (/api/ml/status self_tuning section), and all 5 HA command handlers (mark_ha_override)**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-24T07:43:27Z
- **Completed:** 2026-03-24T07:56:09Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Coordinator calls record_cycle() every 5s with pool_status and grid_power_w for oscillation tracking
- Bidirectional wiring: coordinator feeds data to SelfTuner, SelfTuner pushes computed params back via _apply_params()
- Nightly tuning runs after anomaly training in the scheduler loop
- GET /api/ml/status returns self_tuning section with mode, shadow_days, current params, and activation gate
- All 5 HA command handlers (deadband_huawei, deadband_victron, ramp_rate, min_soc_huawei, min_soc_victron) notify SelfTuner of user overrides

## Task Commits

Each task was committed atomically:

1. **Task 1: Wire SelfTuner into coordinator and HA command handlers** - `f475efb` (feat)
2. **Task 2: Wire SelfTuner into main.py lifespan and nightly loop, extend API** - `b8f6a8b` (feat)

## Files Created/Modified
- `backend/coordinator.py` - set_self_tuner() injection, record_cycle() in _loop(), mark_ha_override() in 5 HA command handlers
- `backend/main.py` - SelfTuner construction, bidirectional coordinator wiring, self_tuner param in nightly loop
- `backend/api.py` - get_self_tuner dependency, self_tuning section in /api/ml/status endpoint

## Decisions Made
- SelfTuner constructed before nightly loop asyncio task creation (avoids UnboundLocalError), coordinator wiring done after coordinator construction
- record_cycle() placed outside the main try/except in _loop() with its own fire-and-forget pattern (matching _run_anomaly_check style)
- API dependency follows existing get_anomaly_detector pattern with getattr fallback

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed UnboundLocalError for self_tuner in nightly loop call**
- **Found during:** Task 2 (main.py wiring)
- **Issue:** Plan specified constructing SelfTuner after coordinator (line ~525) but nightly loop call at line ~495 referenced it before construction
- **Fix:** Moved SelfTuner construction before nightly loop call; deferred coordinator wiring to after coordinator construction
- **Files modified:** backend/main.py
- **Verification:** All 1509 tests pass
- **Committed in:** b8f6a8b (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Construction order adjustment necessary to avoid runtime error. No scope change.

## Known Stubs

None - all integration wiring is complete with real connections.

## Issues Encountered
- Initial test run showed 8 failures due to self_tuner variable referenced before assignment in lifespan -- resolved by reordering construction

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 19 (Self-Tuning Control) is fully complete
- SelfTuner is live in the control loop, receiving data and ready for nightly computation
- Shadow mode will accumulate 14 days before promoting to live parameter application
- Activation gate requires MAPE < 25% and 60+ days of history data

## Self-Check: PASSED

- backend/coordinator.py: FOUND
- backend/main.py: FOUND
- backend/api.py: FOUND
- Commit f475efb (Task 1): FOUND
- Commit b8f6a8b (Task 2): FOUND
- All 1509 tests passing

---
*Phase: 19-self-tuning-control*
*Completed: 2026-03-24*
