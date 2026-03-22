---
phase: 02-independent-controllers-coordinator
plan: 02
subsystem: coordinator
tags: [coordinator, role-assignment, hysteresis, ramp-limiting, debounce, dual-battery, tdd]

# Dependency graph
requires:
  - phase: 02-independent-controllers-coordinator
    plan: 01
    provides: HuaweiController, VictronController, ControllerSnapshot, ControllerCommand, CoordinatorState, BatteryRole
provides:
  - Coordinator class with 5s async control loop
  - SoC-based role assignment with swap hysteresis
  - Per-system hysteresis dead-band (300W/150W)
  - Per-system ramp limiting (2000W/1000W per cycle)
  - 2-cycle debounce with safe-state bypass
  - PV surplus routing (Huawei first, overflow to Victron)
  - Grid charge slot handling and cleanup
  - EVCC hold mode propagation
  - Failover routing to surviving system
  - CoordinatorState backward-compatible with UnifiedPoolState
affects: [02-03-PLAN, api, main, orchestrator-replacement]

# Tech tracking
tech-stack:
  added: []
  patterns: [coordinator-pattern, role-based-dispatch, per-system-hysteresis, ramp-limiting, debounce-with-safe-bypass]

key-files:
  created:
    - backend/coordinator.py
  modified:
    - tests/test_coordinator.py

key-decisions:
  - "Both-below-min-SoC check handled in _run_cycle (not in _assign_discharge_roles) — keeps role assignment pure"
  - "Coordinator-specific config (deadband, ramp, SoC thresholds) stored as instance attributes, not added to OrchestratorConfig dataclass"
  - "PV surplus uses charge_headroom_w from controller snapshots (not raw max_charge_power_w) for correct allocation"

patterns-established:
  - "Coordinator pattern: _run_cycle polls both controllers, decides roles/allocation, applies hysteresis+ramp, sends commands via execute()"
  - "Per-system hysteresis: Huawei 300W, Victron 150W dead-band applied before sending commands"
  - "Ramp limiting: max watts change per cycle applied after hysteresis"
  - "Debounce per-controller: pending_role + pending_cycles, safe_state=True bypasses"
  - "Swap hysteresis: current PRIMARY keeps role unless challenger exceeds by 3%"

requirements-completed: [CTRL-02, CTRL-03, CTRL-05, CTRL-06, CTRL-07, CTRL-08]

# Metrics
duration: 6min
completed: 2026-03-22
---

# Phase 02 Plan 02: Coordinator Summary

**Dual-battery coordinator with SoC-based role assignment, per-system hysteresis/ramp limiting, 2-cycle debounce, PV surplus routing, and grid charge handling**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-22T08:25:13Z
- **Completed:** 2026-03-22T08:31:00Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 2 created

## Accomplishments
- Coordinator class implementing complete dual-battery control loop with 5s cycle interval
- SoC-based role assignment: higher SoC gets PRIMARY_DISCHARGE, gap >= 5% means HOLDING for secondary, gap < 5% means SECONDARY_DISCHARGE
- Swap hysteresis prevents flapping: current PRIMARY keeps role unless challenger exceeds by 3%
- Per-system hysteresis dead-band (Huawei 300W, Victron 150W) and ramp limiting (2000W/1000W per cycle)
- PV surplus routing fills Huawei first (D-03), overflows to Victron, respects 95% SoC routing (D-04)
- Grid charge slot detection/cleanup, EVCC hold mode, failover to survivor
- 87 new tests (86 pass, 1 skipped on trio), full suite 1083 green

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Failing coordinator tests** - `e87a5e9` (test)
2. **Task 1 (GREEN): Coordinator implementation** - `97efaba` (feat)

## Files Created/Modified
- `backend/coordinator.py` - Coordinator class: control loop, role assignment, allocation, hysteresis, ramp, debounce, grid charge, state building
- `tests/test_coordinator.py` - 87 tests covering all CTRL requirements and coordinator behaviors

## Decisions Made
- Both-below-min-SoC check is handled in `_run_cycle` rather than `_assign_discharge_roles` to keep role assignment a pure function of SoC values
- Coordinator-specific config (deadband, ramp rate, SoC thresholds) stored as instance attributes rather than modifying OrchestratorConfig dataclass — keeps config backward-compatible during migration
- PV surplus allocation uses `charge_headroom_w` from controller snapshots for accurate remaining capacity

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test both-below-min-SoC expectation mismatch**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** Test expected `_assign_discharge_roles` to handle min SoC check, but the method only handles SoC-based role assignment; min SoC is a caller responsibility in `_run_cycle`
- **Fix:** Rewrote test to verify min SoC handling through `_run_cycle` integration instead
- **Files modified:** tests/test_coordinator.py
- **Verification:** Test passes verifying both controllers receive HOLDING commands

**2. [Rule 3 - Blocking] Trio backend incompatible with asyncio.create_task**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** `test_start_stop_lifecycle` uses `asyncio.create_task` which fails under trio backend (pytest-anyio runs both)
- **Fix:** Added runtime check for asyncio event loop, `pytest.skip()` on trio
- **Files modified:** tests/test_coordinator.py
- **Verification:** Test passes on asyncio, cleanly skipped on trio

---

**Total deviations:** 2 auto-fixed (1 bug, 1 blocking)
**Impact on plan:** Both fixes aligned with plan intent. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Coordinator is ready for API integration (Plan 03)
- `get_state()` returns backward-compatible `CoordinatorState` for existing API consumers
- `set_scheduler()`, `set_evcc_monitor()`, `set_notifier()` maintain same wiring interface as current Orchestrator
- Control loop uses `start()`/`stop()` for lifespan management

## Self-Check: PASSED

---
*Phase: 02-independent-controllers-coordinator*
*Completed: 2026-03-22*
