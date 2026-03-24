---
phase: 02-independent-controllers-coordinator
plan: 03
subsystem: api-integration
tags: [coordinator, lifespan, fastapi, api, websocket, backward-compat]

# Dependency graph
requires:
  - phase: 02-independent-controllers-coordinator
    plan: 01
    provides: HuaweiController, VictronController with poll/execute interface
  - phase: 02-independent-controllers-coordinator
    plan: 02
    provides: Coordinator with start/stop, get_state, set_scheduler/set_evcc_monitor/set_notifier
provides:
  - Coordinator wired into FastAPI lifespan replacing Orchestrator
  - API layer serving CoordinatorState with backward-compatible fields
  - WebSocket broadcasts including huawei_role, victron_role, pool_status
  - Coordinator get_device_snapshot, get_last_error, get_working_mode for API compatibility
affects: [frontend, dashboard, ha-addon, phase-03, phase-04]

# Tech tracking
tech-stack:
  added: []
  patterns: [coordinator-lifespan-wiring, backward-compat-attribute-name]

key-files:
  created: []
  modified:
    - backend/main.py
    - backend/api.py
    - backend/coordinator.py

key-decisions:
  - "app.state.orchestrator attribute name preserved for backward compatibility with all API and test code"
  - "Coordinator gains get_device_snapshot, get_last_error, get_working_mode methods for API parity with Orchestrator"
  - "get_device_snapshot sources data from cached controller snapshots stored during _run_cycle"

patterns-established:
  - "Backward-compat wiring: new coordinator stored under old attribute name, API dependency unchanged"
  - "Controller snapshot caching: Coordinator stores _last_h_snap/_last_v_snap for inter-cycle queries"

requirements-completed: [CTRL-02, CTRL-04, CTRL-05]

# Metrics
duration: 4min
completed: 2026-03-22
---

# Phase 02 Plan 03: API Integration Summary

**Coordinator and per-battery controllers wired into FastAPI lifespan, API layer serving CoordinatorState with full backward compatibility for existing frontend and tests**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T08:33:01Z
- **Completed:** 2026-03-22T08:37:24Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Lifespan creates HuaweiController, VictronController, and Coordinator instead of Orchestrator
- API layer type hints updated from Orchestrator to Coordinator throughout api.py
- Coordinator gains get_device_snapshot, get_last_error, get_working_mode for /api/devices and /api/health endpoints
- All 1083 tests pass (11 skipped), zero regressions from the integration

## Task Commits

Each task was committed atomically:

1. **Task 1: Update main.py lifespan to wire controllers and coordinator** - `8857b3d` (feat)
2. **Task 2: Update API layer for CoordinatorState** - `69b0562` (feat)

## Files Created/Modified
- `backend/main.py` - Lifespan wires HuaweiController, VictronController, Coordinator; updates EVCC/notifier wiring
- `backend/api.py` - Orchestrator type hints replaced with Coordinator; DI dependency unchanged
- `backend/coordinator.py` - Added get_device_snapshot, get_last_error, get_working_mode; cached last snapshots

## Decisions Made
- Preserved `app.state.orchestrator` attribute name so all existing API code, test mocks, and WebSocket handler continue working without changes
- Added `get_device_snapshot()` to Coordinator sourcing from cached `_last_h_snap`/`_last_v_snap` rather than calling controllers directly (avoids async in sync method)
- `get_working_mode()` returns None since Coordinator does not track Huawei working mode (controller handles it internally)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added get_device_snapshot, get_last_error, get_working_mode to Coordinator**
- **Found during:** Task 1
- **Issue:** API layer calls `orchestrator.get_device_snapshot()`, `orchestrator.get_last_error()`, and `orchestrator.get_working_mode()` in /api/devices, /api/health, and WebSocket handler, but Coordinator had none of these methods
- **Fix:** Added all three methods to Coordinator with data sourced from cached controller snapshots; added `_last_h_snap`/`_last_v_snap` instance variables populated during `_run_cycle`
- **Files modified:** backend/coordinator.py
- **Verification:** Full test suite (1083 tests) passes

---

**Total deviations:** 1 auto-fixed (missing critical API interface methods)
**Impact on plan:** Essential for the integration to work. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 02 complete: all three plans delivered
- Coordinator is the live control loop, fully replacing Orchestrator in the application
- API serves CoordinatorState with backward-compatible fields plus new per-system visibility
- Ready for Phase 03 (scheduler rework) and Phase 04 (dashboard rework) which can proceed in parallel

## Self-Check: PASSED

---
*Phase: 02-independent-controllers-coordinator*
*Completed: 2026-03-22*
