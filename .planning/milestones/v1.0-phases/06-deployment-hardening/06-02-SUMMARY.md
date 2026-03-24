---
phase: 06-deployment-hardening
plan: 02
subsystem: setup
tags: [modbus, pymodbus, victron, setup-wizard, modul3]

# Dependency graph
requires:
  - phase: 01-victron-modbus-driver
    provides: VictronConfig with Modbus TCP fields
provides:
  - EmsSetupConfig with Victron Modbus TCP unit IDs and Modul3 tariff fields
  - Modbus TCP probe endpoint using pymodbus register read
  - SetupCompleteRequest mirroring all new config fields
  - VictronConfig.battery_unit_id for forward compatibility
affects: [06-deployment-hardening]

# Tech tracking
tech-stack:
  added: []
  patterns: [pymodbus register-read probe with TCP-only fallback warning]

key-files:
  created: []
  modified:
    - backend/setup_config.py
    - backend/setup_api.py
    - backend/config.py
    - tests/test_setup_config.py
    - tests/test_setup_api.py

key-decisions:
  - "Victron probe reads system SoC register 843 as connectivity test"
  - "TCP-only success returns ok:true with warning (partial probe success)"
  - "Removed paho-mqtt dependency from setup_api entirely"

patterns-established:
  - "Modbus probe pattern: connect + register read with partial success fallback"

requirements-completed: [DEP-02, DEP-03]

# Metrics
duration: 4min
completed: 2026-03-23
---

# Phase 6 Plan 2: Setup Wizard Modbus Migration Summary

**Migrated setup wizard from Victron MQTT to Modbus TCP with unit ID config and pymodbus probe**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-23T10:14:55Z
- **Completed:** 2026-03-23T10:19:00Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- EmsSetupConfig and SetupCompleteRequest now use victron_port=502 with 3 Modbus unit ID fields
- Modul3 grid-fee tariff fields added for future wizard UI support
- victron_modbus probe performs real pymodbus register read with TCP-only fallback
- VictronConfig gains battery_unit_id field for forward compatibility
- All paho-mqtt references removed from setup_api.py

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend EmsSetupConfig and SetupCompleteRequest** - `9b60b45` (feat)
2. **Task 2: Replace victron_mqtt probe with victron_modbus** - `03236d9` (feat)

_Note: TDD tasks with RED/GREEN phases committed together_

## Files Created/Modified
- `backend/setup_config.py` - Added Victron Modbus unit IDs and Modul3 tariff fields, changed port to 502
- `backend/setup_api.py` - Replaced MQTT probe with Modbus TCP register read probe, updated SetupCompleteRequest
- `backend/config.py` - Added battery_unit_id to VictronConfig, updated to Modbus TCP config
- `tests/test_setup_config.py` - Tests for new defaults, round-trip, and VictronConfig.battery_unit_id
- `tests/test_setup_api.py` - Tests for victron_modbus probe success/TCP-only/refused/mqtt-removed

## Decisions Made
- Victron probe reads system SoC register 843 as connectivity test (matches existing VictronDriver usage)
- TCP-only success returns ok:true with warning rather than failure (partial connectivity is useful feedback)
- Removed paho-mqtt import entirely from setup_api (no longer needed after MQTT probe removal)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated VictronConfig from MQTT to Modbus TCP in worktree**
- **Found during:** Task 1
- **Issue:** Worktree config.py still had old MQTT-based VictronConfig (port 1883, no unit IDs)
- **Fix:** Updated VictronConfig to match plan's interface contract (port 502, all unit IDs, battery_unit_id)
- **Files modified:** backend/config.py
- **Verification:** test_victron_config_battery_unit_id passes

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Auto-fix aligned worktree VictronConfig with the target interface. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Setup wizard backend fully migrated to Modbus TCP
- Ready for frontend wizard UI updates in plan 03

---
*Phase: 06-deployment-hardening*
*Completed: 2026-03-23*
