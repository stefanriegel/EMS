---
phase: 02-independent-controllers-coordinator
plan: 01
subsystem: controllers
tags: [modbus, huawei, victron, dataclass, enum, tdd, battery-controller]

# Dependency graph
requires:
  - phase: 01-victron-modbus-driver
    provides: VictronDriver with read_system_state and write_ac_power_setpoint
provides:
  - BatteryRole and PoolStatus enums for coordinator role assignment
  - ControllerSnapshot dataclass consumed by coordinator on every cycle
  - ControllerCommand dataclass for coordinator-to-controller instructions
  - CoordinatorState backward-compatible superset of UnifiedPoolState
  - HuaweiController wrapping HuaweiDriver with failure counting and safe state
  - VictronController wrapping VictronDriver with ESS mode guard and per-phase setpoints
affects: [02-02-PLAN, 02-03-PLAN, api, orchestrator]

# Tech tracking
tech-stack:
  added: []
  patterns: [per-battery-controller, failure-counting-safe-state, ess-mode-guard, stale-detection]

key-files:
  created:
    - backend/controller_model.py
    - backend/huawei_controller.py
    - backend/victron_controller.py
    - tests/test_controller_model.py
    - tests/test_huawei_controller.py
    - tests/test_victron_controller.py
  modified: []

key-decisions:
  - "HuaweiBatteryData has no timestamp field; controller tracks last-read time internally for stale detection"
  - "write_ac_charging takes only bool (not bool+power_w); charge power set separately via write_max_charge_power"
  - "VictronController uses data.timestamp from VictronSystemData for stale detection (driver sets it)"
  - "Per-phase discharge uses -grid_lN_power_w matching existing orchestrator pattern"

patterns-established:
  - "Controller pattern: poll() returns ControllerSnapshot, execute() takes ControllerCommand"
  - "Failure counting: 3 consecutive failures triggers safe state (zero-power write)"
  - "Stale detection: data older than 2 * loop_interval_s increments failure counter"
  - "ESS mode guard: Victron skips writes when ess_mode < 2"

requirements-completed: [CTRL-01, CTRL-04, CTRL-06]

# Metrics
duration: 4min
completed: 2026-03-22
---

# Phase 02 Plan 01: Controller Model and Battery Controllers Summary

**TDD controller model with BatteryRole/PoolStatus enums, ControllerSnapshot/Command dataclasses, and HuaweiController + VictronController with failure counting, safe state, and ESS mode guard**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T08:18:34Z
- **Completed:** 2026-03-22T08:22:42Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 6 created

## Accomplishments
- Controller model types: BatteryRole (5 members), PoolStatus (3 members), ControllerSnapshot, ControllerCommand, CoordinatorState
- HuaweiController: polls read_master + read_battery, failure counting with safe state at 3 failures, sign-convention-correct execute
- VictronController: polls read_system_state, ESS mode guard (skip write when mode < 2), per-phase setpoint distribution using grid data
- CoordinatorState backward-compatible with UnifiedPoolState plus huawei_role, victron_role, pool_status
- 72 new tests all passing, full suite (997 tests) green

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Failing tests for model and controllers** - `3870d8d` (test)
2. **Task 1 (GREEN): Production controller model and both controllers** - `19eb38b` (feat)

## Files Created/Modified
- `backend/controller_model.py` - BatteryRole, PoolStatus enums; ControllerSnapshot, ControllerCommand, CoordinatorState dataclasses
- `backend/huawei_controller.py` - HuaweiController wrapping HuaweiDriver with poll/execute/failure counting
- `backend/victron_controller.py` - VictronController wrapping VictronDriver with ESS mode guard and per-phase setpoints
- `tests/test_controller_model.py` - 14 tests for enum members, JSON serialization, dataclass construction
- `tests/test_huawei_controller.py` - 32 tests for poll, execute, failure counting, stale detection
- `tests/test_victron_controller.py` - 26 tests for poll, execute, ESS mode guard, per-phase distribution

## Decisions Made
- HuaweiBatteryData has no timestamp field, so HuaweiController tracks `_last_read_time` internally for stale detection rather than relying on data timestamp
- Huawei `write_ac_charging` takes only `bool` (not `bool, power_w` as the plan interface suggested); charge power is set separately via `write_max_charge_power`
- VictronController leverages VictronSystemData.timestamp for stale detection since the driver populates it from `time.monotonic()`
- No base class/mixin for shared failure counting logic -- two controllers with slight duplication is clearer and the plan explicitly allows it

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Huawei write_ac_charging signature mismatch**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** Plan specified `write_ac_charging(True, 3000)` but actual driver signature is `write_ac_charging(enabled: bool)` with no power parameter
- **Fix:** Split into two calls: `write_ac_charging(True)` + `write_max_charge_power(watts)`
- **Files modified:** backend/huawei_controller.py, tests/test_huawei_controller.py
- **Verification:** Tests pass with correct two-call pattern

**2. [Rule 1 - Bug] HuaweiBatteryData has no timestamp field**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** Plan assumed HuaweiBatteryData has a `timestamp` field for stale detection, but it does not
- **Fix:** Controller tracks `_last_read_time` internally using `time.monotonic()` and detects gaps between successful reads
- **Files modified:** backend/huawei_controller.py
- **Verification:** Stale detection test passes

---

**Total deviations:** 2 auto-fixed (2 bugs from plan-vs-actual driver interface mismatch)
**Impact on plan:** Both fixes aligned with the plan's intent. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Controller model types and both controllers are ready for the coordinator (Plan 02)
- ControllerSnapshot provides all fields needed for coordinator decision logic
- ControllerCommand provides the interface for coordinator-to-controller dispatch
- CoordinatorState is backward-compatible with existing UnifiedPoolState consumers

## Self-Check: PASSED

---
*Phase: 02-independent-controllers-coordinator*
*Completed: 2026-03-22*
