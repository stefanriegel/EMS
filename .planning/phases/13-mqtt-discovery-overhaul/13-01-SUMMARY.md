---
phase: 13-mqtt-discovery-overhaul
plan: 01
subsystem: mqtt
tags: [mqtt, home-assistant, discovery, paho-mqtt, lwt, availability]

# Dependency graph
requires:
  - phase: 04-api-integration
    provides: "Original HA MQTT client with flat _ENTITIES tuple list"
provides:
  - "EntityDefinition dataclass for typed entity model"
  - "SENSOR_ENTITIES list with 17 entities across three HA devices"
  - "LWT availability via ems/status topic"
  - "Origin metadata, expire_after, has_entity_name, entity_category"
  - "Three-device grouping: EMS Huawei, EMS Victron, EMS System"
affects: [13-02, 13-03, 14-mqtt-controllable-entities]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "EntityDefinition frozen dataclass for typed entity model"
    - "Three-device grouping with _DEVICES dict mapping"
    - "LWT availability via will_set before connect, online on CONNACK, offline on disconnect"
    - "Discovery payloads with origin, availability, expire_after, has_entity_name"

key-files:
  created: []
  modified:
    - backend/ha_mqtt_client.py
    - tests/test_ha_mqtt_client.py

key-decisions:
  - "Replaced device_name constructor param with configuration_url for device info"
  - "Used get_running_loop() instead of get_event_loop() for trio compatibility"
  - "Entity names shortened per has_entity_name: True (e.g. 'Battery SoC' not 'Huawei Battery SOC')"

patterns-established:
  - "EntityDefinition: frozen dataclass with entity_id, name, platform, unit, device_class, state_class, entity_category, value_key, device_group"
  - "Three-device grouping: huawei (5), victron (8), system (4) entities"

requirements-completed: [DISC-01, DISC-02, DISC-03, DISC-04, DISC-05, DISC-06, DISC-07, DISC-10, DISC-12]

# Metrics
duration: 5min
completed: 2026-03-23
---

# Phase 13 Plan 01: Entity Model and Discovery Payloads Summary

**Typed EntityDefinition dataclass with three-device grouping, LWT availability, origin metadata, expire_after, and HA best-practice discovery fields**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-23T17:57:13Z
- **Completed:** 2026-03-23T18:02:16Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments
- Replaced flat _ENTITIES tuple list with typed EntityDefinition frozen dataclass
- Split single HA device into three: EMS Huawei (5 entities), EMS Victron (8 entities), EMS System (4 entities)
- Added LWT availability via ems/status topic with will_set, online publish on connect, offline on disconnect
- Added origin metadata, expire_after: 120, has_entity_name: True, entity_category, device_class audit, configuration_url
- All 17 existing unique_id values preserved unchanged (DISC-12)
- 64 tests pass covering all new discovery payload fields

## Task Commits

Each task was committed atomically:

1. **Task 1: Refactor entity model and discovery payloads** - `f48e360` (test: failing tests) + `87f35d0` (feat: implementation)

_TDD task: RED (failing tests) then GREEN (implementation passing)_

## Files Created/Modified
- `backend/ha_mqtt_client.py` - EntityDefinition dataclass, SENSOR_ENTITIES, three-device discovery, LWT, origin metadata
- `tests/test_ha_mqtt_client.py` - 64 tests for all new discovery payload fields

## Decisions Made
- Replaced `device_name` constructor parameter with `configuration_url` (default: `http://homeassistant.local:8000`)
- Changed `asyncio.get_event_loop()` to `asyncio.get_running_loop()` with fallback for trio test backend compatibility
- Short entity names per has_entity_name: True convention (e.g. "Battery SoC" under device "EMS Huawei")
- Diagnostic entities: online, roles, control_state, evcc_battery_mode, pool_status, L1/L2/L3 power
- Online entities keep device_class=None (migrated to binary_sensor in plan 02)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] asyncio.get_event_loop() fails under trio test backend**
- **Found during:** Task 1 (TDD GREEN phase)
- **Issue:** `asyncio.get_event_loop()` raises RuntimeError when running under trio backend in pytest-anyio
- **Fix:** Changed to `asyncio.get_running_loop()` with try/except fallback to None
- **Files modified:** backend/ha_mqtt_client.py
- **Verification:** All 64 tests pass under both asyncio and trio backends
- **Committed in:** 87f35d0

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Necessary for test compatibility. No scope creep.

## Issues Encountered
None beyond the trio compatibility fix documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- EntityDefinition dataclass ready for plan 02 (binary_sensor platform migration)
- Three-device grouping established for plan 02 entity additions
- LWT availability infrastructure in place

---
*Phase: 13-mqtt-discovery-overhaul*
*Completed: 2026-03-23*
