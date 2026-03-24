---
phase: 20-hardware-validation
plan: 02
subsystem: controllers
tags: [hardware-validation, dry-run, modbus, huawei, victron, safety, config]

requires:
  - phase: 20-hardware-validation
    plan: 01
    provides: "dry_run flag on all driver write methods, validate_connectivity()"
provides:
  - "HardwareValidationConfig dataclass with from_env()"
  - "Validation period gating in HuaweiController.execute()"
  - "Validation period gating in VictronController.execute()"
  - "Startup connectivity validation in main.py lifespan"
  - "first_read_at wall-clock tracking in both controllers"
affects: [hardware-testing, production-deployment]

tech-stack:
  added: []
  patterns: ["validation_config optional parameter on controllers", "_in_validation_period() gating pattern", "safe-state writes bypass validation gate"]

key-files:
  created: []
  modified:
    - "backend/config.py"
    - "backend/huawei_controller.py"
    - "backend/victron_controller.py"
    - "backend/main.py"
    - "tests/test_huawei_controller.py"
    - "tests/test_victron_controller.py"
    - "tests/test_main_lifespan.py"

key-decisions:
  - "HardwareValidationConfig uses wall-clock time.time() not time.monotonic() for validation period tracking"
  - "Safe-state writes in _handle_failure bypass validation gate entirely (no dry_run passed)"
  - "validation_config is optional (None) so existing controller usage without validation still works"

patterns-established:
  - "_in_validation_period() returns bool, used to compute dry_run flag in execute()"
  - "first_read_at tracked in poll() on first successful read, never overwritten"
  - "validate_connectivity() called at startup before coordinator starts"

requirements-completed: [HWVAL-01, HWVAL-04]

duration: 9min
completed: 2026-03-24
---

# Phase 20 Plan 02: Validation Period Gating and Startup Connectivity Summary

**HardwareValidationConfig with 48h read-only validation period gating on both controllers, startup connectivity checks, and safe-state bypass**

## Performance

- **Duration:** 9 min
- **Started:** 2026-03-24T11:00:09Z
- **Completed:** 2026-03-24T11:09:00Z
- **Tasks:** 2 (1 TDD, 1 standard)
- **Files modified:** 7

## Accomplishments
- HardwareValidationConfig dataclass in config.py reads EMS_VALIDATION_PERIOD_HOURS and EMS_DRY_RUN environment variables
- Both controllers gate execute() writes with dry_run flag during 48h validation period
- Safe-state writes in _handle_failure bypass the validation gate completely
- Startup connectivity validation calls validate_connectivity() on both drivers before coordinator starts
- 50 new/updated tests across both controller test files, full suite green (1591 tests)

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Failing tests for validation period** - `3fc68de` (test)
2. **Task 1 (GREEN): Validation period implementation** - `7a3396c` (feat)
3. **Task 2: Startup connectivity validation in main.py** - `f881d02` (feat)

## Files Created/Modified
- `backend/config.py` - HardwareValidationConfig dataclass with from_env()
- `backend/huawei_controller.py` - validation_config parameter, _in_validation_period(), dry_run gating in execute(), first_read_at tracking in poll()
- `backend/victron_controller.py` - Same pattern as Huawei: validation_config, _in_validation_period(), dry_run gating, first_read_at
- `backend/main.py` - Startup validate_connectivity() calls, HardwareValidationConfig wiring into controllers
- `tests/test_huawei_controller.py` - TestHuaweiDryRun and TestHuaweiValidationPeriod test classes
- `tests/test_victron_controller.py` - TestVictronDryRun and TestVictronValidationPeriod test classes
- `tests/test_main_lifespan.py` - Added validate_connectivity mock to driver fixtures

## Decisions Made
- Used wall-clock `time.time()` for validation period tracking (not `time.monotonic()`) because the validation period spans system restarts conceptually
- Safe-state writes in `_handle_failure` do not pass `dry_run` at all, ensuring they always execute on real hardware
- `validation_config` is `None`-optional so controllers without it (e.g., in existing tests) never apply dry_run

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Updated lifespan test mocks for validate_connectivity**
- **Found during:** Task 2 (main.py wiring)
- **Issue:** Existing lifespan tests used MagicMock for drivers without validate_connectivity async method, causing TypeError
- **Fix:** Added `d.validate_connectivity = AsyncMock(return_value=True)` to both _make_mock_huawei() and _make_mock_victron()
- **Files modified:** tests/test_main_lifespan.py
- **Verification:** All 15 lifespan tests pass
- **Committed in:** f881d02 (Task 2 commit)

**2. [Rule 1 - Bug] Updated existing controller test assertions for dry_run parameter**
- **Found during:** Task 1 GREEN phase
- **Issue:** Existing execute() tests asserted exact call args (e.g., `assert_awaited_once_with(5000)`) but now calls include `dry_run=False`
- **Fix:** Updated all existing test assertions to include `dry_run=False` in expected call args
- **Files modified:** tests/test_huawei_controller.py, tests/test_victron_controller.py
- **Verification:** All 100 controller tests pass
- **Committed in:** 7a3396c (Task 1 GREEN commit)

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 bug)
**Impact on plan:** Both fixes necessary for test compatibility. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations documented above.

## User Setup Required
None - no external service configuration required. Set `EMS_VALIDATION_PERIOD_HOURS` and `EMS_DRY_RUN` environment variables to configure (defaults: 48h period, dry_run=false).

## Next Phase Readiness
- Both controllers enforce read-only validation period before allowing writes
- Startup validates Modbus connectivity before any control loop begins
- Safe-state writes remain unconditional for safety
- Ready for hardware testing scripts and production deployment phases

## Known Stubs
None - all methods are fully implemented with real logic.

---
*Phase: 20-hardware-validation*
*Completed: 2026-03-24*
