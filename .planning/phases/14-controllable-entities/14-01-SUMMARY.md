---
phase: 14-controllable-entities
plan: 01
subsystem: ha-mqtt
tags: [mqtt, home-assistant, number-entity, select-entity, button-entity, paho, subscribe]

requires:
  - phase: 13-mqtt-discovery-overhaul
    provides: EntityDefinition model, SENSOR_ENTITIES, BINARY_SENSOR_ENTITIES, discovery infrastructure
provides:
  - NUMBER_ENTITIES (5) with min/max/step/mode for runtime-tunable config
  - SELECT_ENTITIES (1) for control mode override
  - BUTTON_ENTITIES (2) for force grid charge and reset to auto
  - MQTT subscribe infrastructure (_on_connect subscribes, _on_message dispatches)
  - Health check for stale publish detection and automatic reconnect
  - set_command_callback API for orchestrator integration
affects: [14-02-controllable-entities, orchestrator, config]

tech-stack:
  added: []
  patterns: [call_soon_threadsafe dispatch for MQTT commands, BrokenPipeError guard on subscribe]

key-files:
  created: []
  modified:
    - backend/ha_mqtt_client.py
    - tests/test_ha_mqtt_client.py

key-decisions:
  - "Extended EntityDefinition with optional fields (default=None) to keep backward compatibility with existing sensor/binary_sensor definitions"
  - "Button discovery payloads omit state_topic and value_template (stateless entities)"
  - "Subscribe BrokenPipeError guard catches OSError too for robustness"

patterns-established:
  - "Controllable entity command_topic format: homeassistant/{platform}/ems/{entity_id}/set"
  - "Entity _on_message parses entity_id from topic segment [-2] and dispatches via call_soon_threadsafe"

requirements-completed: [CTRL-01, CTRL-02, CTRL-03, CTRL-04, CTRL-05, CTRL-06, CTRL-07, CTRL-08, CTRL-09, CTRL-11]

duration: 4min
completed: 2026-03-23
---

# Phase 14 Plan 01: Subscribe Infrastructure and Controllable Entity Definitions Summary

**MQTT subscribe path with 8 controllable entities (5 number, 1 select, 2 button), BrokenPipeError-guarded subscriptions, and stale-publish health check**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-23T20:51:43Z
- **Completed:** 2026-03-23T20:56:00Z
- **Tasks:** 1 (TDD: RED -> GREEN)
- **Files modified:** 2

## Accomplishments
- Extended EntityDefinition with optional controllable fields (command_topic, min/max/step/mode, options, payload_press) while preserving backward compatibility
- Defined NUMBER_ENTITIES (5), SELECT_ENTITIES (1), BUTTON_ENTITIES (2) with correct discovery payloads
- Built MQTT subscribe infrastructure: _on_connect subscribes to command topics, _on_message dispatches via call_soon_threadsafe
- Added health check detecting stale publishes and forcing paho reconnect
- 29 new tests (115 total), all passing

## Task Commits

Each task was committed atomically:

1. **Task 1: Add controllable entity definitions and MQTT subscribe infrastructure**
   - `c7e9c40` (test: failing tests for controllable entities)
   - `cf0be43` (feat: implement controllable entities and subscribe path)

## Files Created/Modified
- `backend/ha_mqtt_client.py` - Extended EntityDefinition, added NUMBER/SELECT/BUTTON_ENTITIES, subscribe infrastructure, health check, _on_message dispatch
- `tests/test_ha_mqtt_client.py` - 29 new tests covering entity definitions, discovery payloads, subscribe, dispatch, health check

## Decisions Made
- Extended EntityDefinition with optional fields (default=None) rather than creating separate dataclasses -- keeps a single entity model across all platforms
- Button discovery payloads omit state_topic and value_template since buttons are stateless in HA
- BrokenPipeError guard also catches OSError for broader resilience against paho thread issues

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated existing test publish counts for new entity totals**
- **Found during:** Task 1 GREEN phase
- **Issue:** Existing tests hardcoded discovery publish counts (15 sensors + 4 binary + 2 migration = 21) which broke when 8 controllable entities were added
- **Fix:** Updated all publish count assertions to dynamically include NUMBER/SELECT/BUTTON entity counts
- **Files modified:** tests/test_ha_mqtt_client.py
- **Verification:** All 115 tests pass
- **Committed in:** cf0be43

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Necessary to keep existing tests passing with the new entity lists. No scope creep.

## Issues Encountered
None

## Known Stubs
None -- all entity definitions are complete with real values, discovery payloads are fully wired, and subscribe infrastructure dispatches to a callback.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Subscribe infrastructure is ready for Plan 02 to wire command dispatch to the orchestrator
- set_command_callback() API is the integration point for Plan 02
- All 8 entity command topics are subscribed on connect

---
*Phase: 14-controllable-entities*
*Completed: 2026-03-23*
