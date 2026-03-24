---
phase: 20-hardware-validation
plan: 01
subsystem: drivers
tags: [modbus, dry-run, hardware-validation, huawei, victron, safety]

requires:
  - phase: 01-dual-drivers
    provides: "HuaweiDriver and VictronDriver with Modbus TCP read/write"
provides:
  - "dry_run flag on all 5 driver write methods"
  - "validate_connectivity() on both drivers"
  - "verify_write_max_charge_power/discharge_power on Huawei"
  - "verify_write_ac_power_setpoint on Victron"
affects: [20-02, hardware-testing, production-deployment]

tech-stack:
  added: []
  patterns: ["dry_run keyword-only flag on write methods", "write-then-read-back verification", "connectivity pre-check before writes"]

key-files:
  created:
    - "tests/test_hardware_validation.py"
  modified:
    - "backend/drivers/huawei_driver.py"
    - "backend/drivers/victron_driver.py"

key-decisions:
  - "dry_run check placed inside _do() inner function (within _with_reconnect wrapper) for consistency"
  - "validate_connectivity calls all read methods sequentially for thorough validation"
  - "verify_write methods call the write method then read back, keeping write+verify atomic"

patterns-established:
  - "dry_run: bool = False as keyword-only param on all hardware write methods"
  - "validate_connectivity() -> bool pattern for pre-flight checks"
  - "verify_write_*() -> bool pattern for write-back confirmation"

requirements-completed: [HWVAL-01, HWVAL-02, HWVAL-03]

duration: 4min
completed: 2026-03-24
---

# Phase 20 Plan 01: Driver Safety Primitives Summary

**dry_run flag on all 5 Modbus write methods, connectivity validation, and write-back verification for both Huawei and Victron drivers**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-24T10:53:51Z
- **Completed:** 2026-03-24T10:58:08Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 3

## Accomplishments
- All 5 driver write methods (4 Huawei, 1 Victron) accept `dry_run: bool = False` keyword-only parameter
- Both drivers have `validate_connectivity() -> bool` performing full read cycles
- Huawei has `verify_write_max_charge_power` and `verify_write_max_discharge_power` write-back verification
- Victron has `verify_write_ac_power_setpoint` write-back verification
- 38 new tests pass, full suite green (1547 tests, 12 skipped)

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Failing tests** - `15ce052` (test)
2. **Task 1 (GREEN): Implementation** - `e1d49f2` (feat)

## Files Created/Modified
- `tests/test_hardware_validation.py` - 38 tests covering dry_run, connectivity validation, write-back verification
- `backend/drivers/huawei_driver.py` - dry_run on 4 write methods, validate_connectivity, verify_write_max_charge/discharge_power
- `backend/drivers/victron_driver.py` - dry_run on write_ac_power_setpoint, validate_connectivity, verify_write_ac_power_setpoint

## Decisions Made
- dry_run check placed inside the `_do()` inner function (within `_with_reconnect` wrapper) so the assert-connected check still runs but the actual hardware write is skipped
- validate_connectivity calls all read methods sequentially rather than a single register probe, providing thorough validation
- verify_write methods call the write method then immediately read back, keeping the write+verify cycle as tight as possible

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed VictronDriver test mock creation**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** VictronDriver.__init__ creates a real AsyncModbusTcpClient which needs a running asyncio event loop; trio backend tests fail with "no running event loop"
- **Fix:** Used object.__new__ to bypass __init__ and manually set driver attributes for test mocking
- **Files modified:** tests/test_hardware_validation.py
- **Verification:** All 38 tests pass on both asyncio and trio backends
- **Committed in:** e1d49f2 (Task 1 GREEN commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Minor test infrastructure fix, no scope creep.

## Issues Encountered
None beyond the VictronDriver mock creation issue documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Safety primitives ready for use in Phase 20 Plan 02 (hardware validation scripts)
- dry_run enables safe testing against real hardware
- validate_connectivity enables pre-flight checks before any write operations
- verify_write enables confirmation that hardware accepted setpoint changes

## Known Stubs
None - all methods are fully implemented with real logic.

---
*Phase: 20-hardware-validation*
*Completed: 2026-03-24*
