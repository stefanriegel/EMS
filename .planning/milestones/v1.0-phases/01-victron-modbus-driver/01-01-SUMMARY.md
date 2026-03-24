---
phase: 01-victron-modbus-driver
plan: 01
subsystem: drivers
tags: [modbus-tcp, pymodbus, victron, protocol, venus-os]

# Dependency graph
requires: []
provides:
  - VictronDriver with pymodbus AsyncModbusTcpClient (Modbus TCP replacing MQTT)
  - LifecycleDriver and BatteryDriver Protocol classes for structural typing
  - VictronConfig with Modbus TCP fields (port 502, unit IDs)
  - Complete test suite for driver reads, writes, sign convention, reconnect
affects: [01-02-PLAN, orchestrator, api]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Batched Modbus register reads with _signed16 helper for int16 conversion"
    - "Tiered Protocol classes: LifecycleDriver (both drivers) + BatteryDriver (Victron only)"
    - "_with_reconnect pattern for automatic retry on ModbusException"
    - "Health check register read on connect to verify link"

key-files:
  created:
    - backend/drivers/protocol.py
    - tests/drivers/test_victron_config.py
  modified:
    - backend/drivers/victron_driver.py
    - backend/config.py
    - tests/drivers/test_victron_driver.py

key-decisions:
  - "pymodbus uses slave= parameter (not device_id=) in version 3.12.1"
  - "VE.Bus AC power registers use scale 0.1 (raw * 0.1), not scale 10"
  - "system_state field set to None in Modbus driver (no dedicated system state register read)"
  - "consumption_w and pv_on_grid_w set to None (not available via simple Modbus register reads)"

patterns-established:
  - "Protocol classes: plain typing.Protocol without @runtime_checkable"
  - "Modbus register constants: module-level _SYS_REG_* and _VB_REG_* naming"
  - "Mock infrastructure: _mock_register_response() and side_effect with slave-keyed dict"

requirements-completed: [DRV-01, DRV-02, DRV-03, DRV-04, DRV-06]

# Metrics
duration: 5min
completed: 2026-03-22
---

# Phase 01 Plan 01: Victron Modbus TCP Driver Summary

**Replaced MQTT-based VictronDriver with pymodbus AsyncModbusTcpClient reading system/VE.Bus registers with batched reads, int16 sign handling, and configurable unit IDs**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-22T06:49:58Z
- **Completed:** 2026-03-22T06:55:31Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Created LifecycleDriver and BatteryDriver Protocol classes for structural typing
- Completely rewrote VictronDriver from paho-mqtt to pymodbus Modbus TCP
- Updated VictronConfig with Modbus fields (port 502, vebus_unit_id=227, system_unit_id=100)
- Full test suite: 21 config tests + 41 driver tests = 62 new tests, all passing
- Correct sign convention: positive battery_power_w = charging (matches Victron native)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create tiered Protocol classes and update VictronConfig** - `b22eb08` (feat)
2. **Task 2: Rewrite VictronDriver from MQTT to Modbus TCP** - `85a7240` (feat)

## Files Created/Modified
- `backend/drivers/protocol.py` - LifecycleDriver and BatteryDriver Protocol classes
- `backend/drivers/victron_driver.py` - Complete Modbus TCP driver replacing MQTT implementation
- `backend/config.py` - VictronConfig updated with Modbus TCP fields
- `tests/drivers/test_victron_config.py` - Config and Protocol class tests (21 tests)
- `tests/drivers/test_victron_driver.py` - Driver tests with pymodbus mocks (41 tests)

## Decisions Made
- pymodbus 3.12.1 uses `slave=` parameter (not `device_id=` as research notes suggested)
- VE.Bus AC power registers (23-25) use scale factor 0.1 (raw * 0.1 to get watts)
- system_state field left as None since it requires a separate register path not batched here
- consumption_w and pv_on_grid_w set to None -- not derivable from simple Modbus reads

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed pymodbus parameter name: slave instead of device_id**
- **Found during:** Task 2 (driver tests)
- **Issue:** Plan and research notes referenced `device_id=` parameter, but pymodbus 3.12.1 uses `slave=`
- **Fix:** Changed all driver and test code to use `slave=` parameter
- **Files modified:** backend/drivers/victron_driver.py, tests/drivers/test_victron_driver.py
- **Verification:** All 41 driver tests pass
- **Committed in:** 85a7240 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Necessary correction for pymodbus API compatibility. No scope creep.

## Issues Encountered
None beyond the pymodbus parameter name discrepancy.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- VictronDriver is ready for integration into the orchestrator (Plan 02)
- Protocol classes are available for type hints in orchestrator code
- All 105 driver tests pass (Huawei + Victron + config)

## Self-Check: PASSED

All 5 created/modified files exist on disk. Both task commits (b22eb08, 85a7240) verified in git log.

---
*Phase: 01-victron-modbus-driver*
*Completed: 2026-03-22*
