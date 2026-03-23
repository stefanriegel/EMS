---
phase: 13-mqtt-discovery-overhaul
plan: 02
subsystem: mqtt
tags: [mqtt, home-assistant, binary-sensor, discovery, migration, paho-mqtt]

# Dependency graph
requires:
  - phase: 13-mqtt-discovery-overhaul
    plan: 01
    provides: "EntityDefinition dataclass, SENSOR_ENTITIES list, discovery infrastructure"
provides:
  - "BINARY_SENSOR_ENTITIES list with 4 binary sensor entities"
  - "Platform migration cleanup (empty retained payload to old sensor topics)"
  - "export_active field on CoordinatorState"
  - "Binary sensor discovery with payload_on/off, no expire_after"
affects: [13-03, 14-mqtt-controllable-entities]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Binary sensor discovery with payload_on/payload_off for Python bool str()"
    - "One-time migration cleanup via _migration_done flag and empty retained payloads"
    - "_MIGRATED_TO_BINARY list for tracking platform-migrated entities"

key-files:
  created: []
  modified:
    - backend/ha_mqtt_client.py
    - backend/controller_model.py
    - backend/coordinator.py
    - tests/test_ha_mqtt_client.py

key-decisions:
  - "Derived export_active from control_state == EXPORTING rather than ExportAdvisor.should_export() which does not exist"
  - "Binary sensor payload_on/off uses Python str(bool) convention: True/False"

patterns-established:
  - "Binary sensor entities use separate BINARY_SENSOR_ENTITIES list alongside SENSOR_ENTITIES"
  - "Platform migration uses _migration_done flag to run cleanup exactly once per process"

requirements-completed: [DISC-08, DISC-09, DISC-11]

# Metrics
duration: 5min
completed: 2026-03-23
---

# Phase 13 Plan 02: Binary Sensor Entities and Migration Summary

**Binary sensors for connectivity (huawei/victron online) and running states (grid charge, export) with one-time sensor-to-binary_sensor migration cleanup**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-23T18:04:17Z
- **Completed:** 2026-03-23T18:09:18Z
- **Tasks:** 1 (TDD: red + green)
- **Files modified:** 4

## Accomplishments
- Defined 4 binary sensor entities: huawei_online, victron_online (connectivity), grid_charge_active, export_active (running)
- Migrated huawei_online/victron_online from sensor to binary_sensor platform with empty retained cleanup
- Added export_active field to CoordinatorState, wired from control_state == EXPORTING
- Binary sensor discovery payloads include payload_on/off, exclude expire_after, share availability topic

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Failing tests** - `8298ef9` (test)
2. **Task 1 GREEN: Implementation** - `77b82dc` (feat)

## Files Created/Modified
- `backend/ha_mqtt_client.py` - BINARY_SENSOR_ENTITIES list, migration cleanup, updated discovery
- `backend/controller_model.py` - export_active: bool field on CoordinatorState
- `backend/coordinator.py` - Wire export_active from control_state == EXPORTING
- `tests/test_ha_mqtt_client.py` - 86 tests covering binary sensors, migration, export_active

## Decisions Made
- Derived export_active from `control_state == "EXPORTING"` since ExportAdvisor has `advise()` returning ExportAdvice but no simple `should_export()` boolean
- Binary sensor `payload_on`/`payload_off` use Python `str(bool)` convention ("True"/"False") matching how `dataclasses.asdict()` serializes booleans

## Deviations from Plan
None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Binary sensor infrastructure complete, ready for plan 03 (controllable entities)
- All 19 entities (15 sensor + 4 binary_sensor) discovered correctly
- Migration cleanup prevents ghost entities during platform transition

---
*Phase: 13-mqtt-discovery-overhaul*
*Completed: 2026-03-23*
