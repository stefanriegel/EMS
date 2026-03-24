---
phase: 14-controllable-entities
plan: 02
subsystem: coordinator
tags: [mqtt, home-assistant, command-handler, mode-override, supervisor, state-echo]

requires:
  - phase: 14-controllable-entities
    plan: 01
    provides: NUMBER_ENTITIES, SELECT_ENTITIES, BUTTON_ENTITIES, subscribe infrastructure, set_command_callback API
provides:
  - Coordinator command handler (_handle_ha_command) for all 8 controllable entity types
  - Mode override integration in control loop (HOLD, GRID_CHARGE, DISCHARGE_LOCKED)
  - 60-minute auto-timeout for force_grid_charge button
  - State echo (immediate HA MQTT publish after every command)
  - Supervisor options read-merge-write persistence for number entities
  - MQTT health check per control cycle
affects: [orchestrator, ha-mqtt, config]

tech-stack:
  added: []
  patterns: [command dispatch via dict mapping, fire-and-forget asyncio tasks for persistence, value clamping from entity definitions]

key-files:
  created: []
  modified:
    - backend/coordinator.py
    - backend/supervisor_client.py
    - backend/main.py
    - tests/test_coordinator.py

key-decisions:
  - "Mode override checked after EVCC hold but before grid charge slot detection in control loop"
  - "Number value clamping uses entity min/max ranges from ha_mqtt_client definitions"
  - "Supervisor persistence is fire-and-forget (asyncio.create_task) to never block command handling"
  - "State echo creates an asyncio task for the async publish method"

patterns-established:
  - "Command handler dispatch pattern: dict mapping entity_id to handler methods"
  - "Controllable entity extra_fields merged into HA MQTT state payload each cycle"

requirements-completed: [CTRL-07, CTRL-08, CTRL-09, CTRL-10]

duration: 4min
completed: 2026-03-23
---

# Phase 14 Plan 02: Coordinator Command Handling and Mode Override Summary

**Bidirectional MQTT control flow with 8-entity command handler, mode override in control loop, 60min force-grid-charge timeout, state echo, and Supervisor persistence**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-23T20:58:12Z
- **Completed:** 2026-03-23T21:02:17Z
- **Tasks:** 2 (Task 1 TDD, Task 2 auto)
- **Files modified:** 4

## Accomplishments
- Coordinator handles all 8 HA entity commands (5 number, 1 select, 2 button) with proper value clamping
- Mode override (HOLD/GRID_CHARGE/DISCHARGE_LOCKED) integrated into control loop between EVCC hold and grid charge slot checks
- Force grid charge button schedules 60-minute auto-timeout via asyncio.call_later
- State echo publishes current state immediately after every command for HA feedback
- Supervisor options persistence uses read-merge-write pattern (fire-and-forget)
- MQTT health check runs every control cycle to detect silent paho thread crashes
- 16 new tests (186 total coordinator tests), all passing

## Task Commits

Each task was committed atomically:

1. **Task 1: Coordinator command handler (TDD)**
   - `493bd65` (test: add failing tests for HA command handler)
   - `8578931` (feat: implement HA command handler with mode override and auto-timeout)

2. **Task 2: Wire command callback and health check**
   - `94741ed` (feat: wire command callback, health check, and mode override in control loop)

## Files Created/Modified
- `backend/coordinator.py` - Added _handle_ha_command, mode override in _run_cycle, state echo, controllable extra fields, Supervisor persistence
- `backend/supervisor_client.py` - Added get_addon_options and set_addon_options methods
- `backend/main.py` - Wired set_command_callback and set_supervisor_client in lifespan
- `tests/test_coordinator.py` - 16 new tests for command handling, mode override, timeout, state echo

## Decisions Made
- Mode override checked after EVCC hold but before grid charge slot detection -- EVCC hold takes priority over HA commands, but HA mode override takes priority over scheduled grid charge
- Number value clamping uses entity min/max ranges from ha_mqtt_client definitions rather than duplicating ranges
- Supervisor persistence is fire-and-forget (asyncio.create_task) to never block command handling
- State echo creates an asyncio task for the async publish method, with RuntimeError catch for test environments

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## Known Stubs
None -- all command handlers are fully implemented, mode override is wired into the control loop, and state echo publishes real data.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Full bidirectional MQTT control flow is operational
- HA number sliders, select entities, and buttons all update EMS config within one control cycle
- State echo confirms every command immediately
- Phase 14 (controllable-entities) is complete -- ready for Phase 15 (Ingress)

---
*Phase: 14-controllable-entities*
*Completed: 2026-03-23*
