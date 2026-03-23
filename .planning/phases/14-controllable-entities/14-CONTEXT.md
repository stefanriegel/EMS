# Phase 14: Controllable Entities - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Add MQTT subscribe infrastructure to the HA MQTT client, then implement number, select, and button entities that allow users to control EMS parameters and modes from HA UI and automations. Each entity has a command_topic (subscribe) and state_topic (publish). Commands update in-memory config immediately and persist to Supervisor options for restart survival. State echo after every command.

</domain>

<decisions>
## Implementation Decisions

### MQTT Subscribe Architecture
- Copy evcc_mqtt_driver.py pattern: subscribe in _on_connect, _on_message callback, cross thread boundary via call_soon_threadsafe() to async handler
- Topic-based dispatch map in ha_mqtt_client: command_topic → handler function. Handler calls coordinator method via callback registered at init
- Wrap subscribe() in try/except for BrokenPipeError/OSError in _on_connect. Add periodic health check (no successful publish in N cycles → force reconnect)
- QoS 1 for command topic subscriptions (at-least-once, commands are idempotent)

### Controllable Entity Design
- Number entity values applied immediately — update orchestrator config in-memory, effective next control cycle (5s). Persist to Supervisor options via POST /addons/self/options (read-merge-write)
- Select entity mode options: AUTO, HOLD, GRID_CHARGE, DISCHARGE_LOCKED — matches existing ControlState enum values
- Button auto-timeout: Force Grid Charge = 60 minutes auto-timeout then resets to AUTO. Reset to Auto = immediate, no timeout. Log timeout via decision entry
- State echo: publish updated state immediately after command processing (don't wait for next cycle)

### Config Persistence & Coordinator Integration
- Write to Supervisor options via POST /addons/self/options (read-merge-write) for number entity persistence across restarts
- Callback pattern: ha_mqtt_client receives command_handler Callable at init, coordinator registers via set_command_handler(). Handler dispatches to update_config() or set_mode_override()
- Mode override persists until explicitly changed or auto-timeout — HOLD stays until user sends AUTO, GRID_CHARGE via button has 60min timeout, Reset to Auto clears all overrides
- Error response for offline hardware: accept command, log warning, publish state echo with current values. Never reject a valid command

### Claude's Discretion
- Internal structure of the command dispatch map (dict vs match/case)
- Whether to add Supervisor client helper for options write or use httpx directly
- How to wire the 60-minute timeout (asyncio.call_later, background task, or cycle counter)
- Exact entity_id naming for new entities (follow Phase 13 EntityDefinition pattern)
- Whether number entity min/max values need to come from actual hardware config or hardcoded defaults are fine

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- backend/ha_mqtt_client.py — Phase 13 EntityDefinition dataclass, three-device discovery, LWT availability, state publishing
- backend/evcc_mqtt_driver.py — paho subscribe pattern with call_soon_threadsafe (exact pattern to copy)
- backend/coordinator.py — Coordinator class with set_ha_mqtt_client(), _run_cycle(), config update paths
- backend/config.py — SystemConfig, OrchestratorConfig dataclasses with from_env()
- backend/supervisor_client.py — Supervisor API discovery (may need extension for options write)

### Established Patterns
- EntityDefinition(frozen=True) dataclass for entity registration
- SENSOR_ENTITIES and BINARY_SENSOR_ENTITIES lists — add NUMBER_ENTITIES, SELECT_ENTITIES, BUTTON_ENTITIES
- _discovery_payload() builds per-platform payloads from EntityDefinition
- Coordinator exposes config via SystemConfig/OrchestratorConfig — number entities write to these
- ControlState enum: IDLE, DISCHARGE, CHARGE, HOLD, GRID_CHARGE, DISCHARGE_LOCKED

### Integration Points
- ha_mqtt_client.py: add subscribe path alongside existing publish path
- coordinator.py: add set_command_handler() and handle_ha_command() methods
- main.py lifespan: wire command handler callback between coordinator and MQTT client
- config.py: update_config() method or equivalent for runtime config changes

</code_context>

<specifics>
## Specific Ideas

Number entities (5):
- min_soc_huawei: 10-100%, step 5, mode slider, entity_category config, device EMS Huawei
- min_soc_victron: 10-100%, step 5, mode slider, entity_category config, device EMS Victron
- deadband_huawei: 50-1000W, step 50, mode box, entity_category config, device EMS Huawei
- deadband_victron: 50-500W, step 50, mode box, entity_category config, device EMS Victron
- ramp_rate: 100-2000W, step 100, mode box, entity_category config, device EMS System

Select entity (1):
- control_mode: options [AUTO, HOLD, GRID_CHARGE, DISCHARGE_LOCKED], entity_category config, device EMS System

Button entities (2):
- force_grid_charge: entity_category config, device EMS System, 60min auto-timeout
- reset_to_auto: entity_category config, device EMS System, device_class restart

</specifics>

<deferred>
## Deferred Ideas

- Supervisor options persistence may fail if SUPERVISOR_TOKEN not available (local dev). Fall back to in-memory only with warning log
- Additional number entities for charge window times (defer to v1.3)
- Switch entity for enable/disable auto-scheduling (defer to v1.3)

</deferred>
