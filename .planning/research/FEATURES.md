# Feature Landscape: Home Assistant Best Practice Alignment

**Domain:** HA Add-on MQTT integration -- entity model, controllability, ingress, translations
**Researched:** 2026-03-23
**Overall confidence:** HIGH (HA MQTT discovery is extensively documented; patterns well-established across mature integrations like EMS-ESP32, Zigbee2MQTT, Tasmota)

## Table Stakes

Features users expect from a well-behaved HA integration. Missing = "why does this add-on feel janky?"

| Feature | Why Expected | Complexity | Dependencies |
|---------|--------------|------------|--------------|
| **Origin metadata in discovery** | HA logs show "unknown origin" without it; users distrust unlabeled entities. Required for device discovery since 2023.x. | Low | Existing `_discovery_payload()` method |
| **Availability topics** | Without availability, entities show stale values forever instead of "unavailable" when EMS goes offline. Every mature integration does this. | Low | Existing MQTT client, add LWT (Last Will and Testament) |
| **`expire_after` on sensor entities** | Safety net -- if MQTT publish stops (crash, network), sensors go "unavailable" after timeout instead of showing last known value indefinitely. | Low | Discovery payload addition only |
| **`has_entity_name: True` with proper naming** | Since HA 2023.8, entity names combine device name + entity name. Without this, entity IDs are ugly and device grouping breaks. Current impl uses full names like "Huawei Battery SOC" which duplicates device context. | Low | Rename all entity `name` fields to short forms (e.g., "Battery SOC") |
| **`entity_category` on diagnostic/config entities** | Separates primary entities (SoC, power) from diagnostic (online status, pool status) and config (min-SoC, dead-bands). Without it, the entity list is a flat mess. | Low | Tag each entity in `_ENTITIES` list |
| **`device_class` + `state_class` on all applicable entities** | Enables HA long-term statistics, energy dashboard integration, and proper unit display. Already partially done but missing on some entities. | Low | Audit existing entities, add missing classes |
| **Binary sensors for connectivity/states** | `huawei_online` and `victron_online` are currently plain sensors publishing True/False as text. They should be `binary_sensor` platform with `device_class: connectivity`. Similarly, grid charge active and export active should be binary sensors with `device_class: running`. | Medium | New discovery topics under `homeassistant/binary_sensor/`, subscribe to existing state data |
| **Retained discovery messages** | Already done (good), but state messages should NOT be retained. Current impl correctly does not retain state. | None | Already correct |
| **Device info with `configuration_url`** | Points users to the EMS dashboard from the HA device page. Trivial but expected. | Low | Add `cu` field to device payload |
| **Add-on translations (`en.yaml`)** | Config options show raw key names (`huawei_deadband_w`) without translations. Users see ugly technical names in the Add-on config UI. | Low | Create `translations/en.yaml` with name/description for each config option |

## Differentiators

Features that elevate the integration from "works" to "excellent HA citizen." Not expected, but valued by power users.

| Feature | Value Proposition | Complexity | Dependencies |
|---------|-------------------|------------|--------------|
| **Number entities for tunable parameters** | Expose `min_soc_pct_huawei`, `min_soc_pct_victron`, `huawei_deadband_w`, `victron_deadband_w`, `ramp_rate_w_per_cycle` as HA number entities. Users can tweak from HA UI, automations can adjust seasonally. Uses `homeassistant/number/` discovery with `command_topic` + `state_topic`, `min`/`max`/`step`, `mode: box` or `slider`. | Medium | MQTT subscribe on command topics, config update path in orchestrator, entity_category: config |
| **Select entity for control mode** | Expose the EMS operating mode (IDLE, DISCHARGE, CHARGE, HOLD, GRID_CHARGE) as a `homeassistant/select/` entity. Users pick mode from HA dashboard or automations. Options list matches the ControlState enum values. | Medium | MQTT subscribe, orchestrator mode override path, state feedback loop |
| **Button entities for force actions** | "Force Grid Charge" and "Force Export" as `homeassistant/button/` entities with `entity_category: config`. One tap triggers the action. Payload on command_topic triggers orchestrator action. | Medium | MQTT subscribe, orchestrator action dispatch, timeout/auto-reset logic |
| **HA Services via MQTT** | Not actual HA services (those require a custom integration, not MQTT). Instead, expose equivalent functionality through the number/select/button entities above. An automation calling `number.set_value` on the min-SoC entity achieves the same as a hypothetical `ems.set_min_soc` service. This is the MQTT-native approach. | N/A | Covered by number/select/button entities |
| **Ingress support** | Dashboard accessible from HA sidebar without separate port/URL. Set `ingress: true` in config.yaml, bind to `ingress_port: 8099`, restrict to `172.30.32.2`, handle `X-Ingress-Path` header for base URL. Keep existing port 8000 for direct access. | Medium-High | FastAPI path prefix handling, Vite base URL config, CORS/auth changes (HA handles auth via ingress) |
| **Two HA devices instead of one** | Currently all 17 entities live under a single "Energy Management System" device. Better: create "EMS Huawei" and "EMS Victron" devices (plus an "EMS System" device for pool-level entities). This matches the physical reality and makes entity grouping intuitive. | Medium | Restructure discovery payloads to use different device identifiers per battery system |
| **Diagnostic sensors** | Uptime, last decision timestamp, control cycle duration, MQTT message count -- tagged with `entity_category: diagnostic`. Useful for debugging without cluttering the main entity list. | Low-Medium | Expose existing metrics as additional sensor entities |

## Anti-Features

Features to explicitly NOT build for this milestone.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Custom HA integration (Python)** | Massive complexity increase. MQTT discovery gives 90% of the value. A custom integration would require maintaining a separate HA component, HACS distribution, version compatibility matrix. Only needed for features MQTT cannot provide (e.g., true HA services, config flow). | Use MQTT discovery for all entity platforms. Number/select/button entities cover the controllability gap. |
| **MQTT device triggers** | Device triggers are for physical button presses and remote events. Not appropriate for an EMS that publishes continuous state. Would confuse the entity model. | Use binary sensors for state changes, button entities for user-initiated actions. |
| **Climate entity** | Some energy systems expose climate entities for heating control. EMS manages batteries, not temperature. Wrong semantic model. | Stick with sensor, binary_sensor, number, select, button platforms. |
| **Retained state messages** | HA docs explicitly say "not recommended to retain sensor state at the MQTT broker." HA manages state persistence internally. Retained state causes stale data on reconnect. | Use `expire_after` instead. Let HA restore state from its own database. |
| **Entity per individual metric** | Publishing 50+ granular entities (every register, every internal variable) creates entity sprawl. Users hate cleaning up unused entities. | Curate a focused set: ~25-30 entities across sensor, binary_sensor, number, select, button. Expose detailed data via REST API and dashboard only. |
| **Setup wizard** | Being removed in v1.2. Add-on options page replaces it entirely. | Redirect `/setup` to Add-on config documentation or show "configure via Add-on options" message. |

## Feature Dependencies

```
Availability topics (LWT) ──> expire_after (complementary, not dependent)

has_entity_name + proper naming ──> entity_category tagging (naming must be right first)

Binary sensor platform ──> Availability topics (binary sensors need availability too)

Number entities ──> MQTT subscribe infrastructure (new: EMS must listen on command topics)
Select entities ──> MQTT subscribe infrastructure
Button entities ──> MQTT subscribe infrastructure

MQTT subscribe infrastructure ──> Orchestrator config update API (numbers write to config)
MQTT subscribe infrastructure ──> Orchestrator mode override (select writes mode)
MQTT subscribe infrastructure ──> Orchestrator action dispatch (buttons trigger actions)

Ingress ──> FastAPI path prefix handling ──> Frontend base URL configuration

Two HA devices ──> Discovery payload restructure (device identifiers change)
Two HA devices ──> has_entity_name (naming must work with new device names)

Add-on translations ──> config.yaml schema (keys must match)
```

## Entity Platform Summary

What entity platforms to use and why, based on HA MQTT discovery best practices.

### sensor (existing, needs cleanup)
- **Current:** 17 entities, all under `homeassistant/sensor/`
- **Keep:** SoC (%), power (W), setpoints (W), per-phase power (W), control state, EVCC mode, roles
- **Changes needed:** Add `has_entity_name`, fix naming, add `entity_category: diagnostic` on pool_status, add `expire_after: 120` (2 control cycles worth of buffer)
- **Device classes:** `battery` for SoC, `power` for all W values, `enum` for state/role/mode strings

### binary_sensor (new)
- **Entities:** `huawei_online`, `victron_online` (move from sensor), `grid_charge_active`, `export_active`
- **Device classes:** `connectivity` for online/offline, `running` for active states
- **Why:** Binary sensors show as proper on/off toggles in HA UI, support `device_class` icons, and integrate with HA's device health tracking
- **Availability:** Use availability topic, not `expire_after` (binary sensors support availability but not expire_after)

### number (new)
- **Entities:** `min_soc_huawei` (10-100%, step 5), `min_soc_victron` (10-100%, step 5), `deadband_huawei` (50-1000W, step 50), `deadband_victron` (50-500W, step 50), `ramp_rate` (100-2000W, step 100)
- **Entity category:** `config`
- **Mode:** `box` for deadband/ramp (technical users), `slider` for min-SoC (visual)
- **Requires:** MQTT subscribe on `command_topic`, state feedback on `state_topic`

### select (new)
- **Entities:** `control_mode` (options: AUTO, HOLD, GRID_CHARGE, DISCHARGE_LOCKED)
- **Entity category:** `config`
- **Why select over sensor:** Users can change the mode from HA UI or automations
- **Requires:** MQTT subscribe, orchestrator mode override

### button (new)
- **Entities:** `force_grid_charge`, `force_export`, `reset_to_auto`
- **Entity category:** `config`
- **Device class:** `restart` for reset_to_auto, none for others
- **Requires:** MQTT subscribe, orchestrator action dispatch with auto-timeout

## MVP Recommendation

### Phase 1: Discovery Cleanup (table stakes, low risk)
1. Origin metadata on all discovery messages
2. Availability topic with LWT
3. `expire_after: 120` on all sensor entities
4. `has_entity_name: True` with shortened entity names
5. `entity_category` tagging (diagnostic on status entities)
6. `device_class` audit and completion
7. `configuration_url` in device info
8. Move `huawei_online`/`victron_online` to `binary_sensor` platform
9. Add `grid_charge_active` and `export_active` binary sensors
10. Add-on `translations/en.yaml`

**Rationale:** All low-complexity, no new MQTT subscribe infrastructure needed. Fixes the "janky integration" perception immediately.

### Phase 2: Controllable Entities (differentiators, medium risk)
1. MQTT subscribe infrastructure (new capability: EMS listens to command topics)
2. Number entities for tunable parameters
3. Select entity for control mode
4. Button entities for force actions
5. Two HA devices (Huawei + Victron + System)

**Rationale:** Requires new MQTT subscribe path which is a meaningful architecture addition. Number/select/button all depend on it. Group together.

### Phase 3: Ingress (differentiator, isolated)
1. `ingress: true` in config.yaml
2. FastAPI path prefix handling for `X-Ingress-Path`
3. Frontend base URL configuration
4. Auth bypass when accessed via ingress (HA handles it)

**Rationale:** Independent of MQTT work. Can be done in parallel or deferred. Medium-high complexity due to path rewriting.

### Defer: Diagnostic sensors, device triggers, additional entity platforms
These add polish but are not needed for v1.2 scope.

## Naming Convention Reference

HA best practice since 2023.8 with `has_entity_name: True`:

| Current Name | New Name | Generated Entity ID | Platform |
|-------------|----------|-------------------|----------|
| "Huawei Battery SOC" | "Battery SOC" | `sensor.ems_huawei_battery_soc` | sensor |
| "Victron Battery SOC" | "Battery SOC" | `sensor.ems_victron_battery_soc` | sensor |
| "Huawei Discharge Setpoint" | "Discharge Setpoint" | `sensor.ems_huawei_discharge_setpoint` | sensor |
| "Combined Battery Power" | "Combined Power" | `sensor.ems_system_combined_power` | sensor |
| "Huawei Online" | null (uses device_class) | `binary_sensor.ems_huawei_connectivity` | binary_sensor |
| "EMS Control State" | "Control State" | `sensor.ems_system_control_state` | sensor |
| (new) "Min SOC" | "Min SOC" | `number.ems_huawei_min_soc` | number |
| (new) "Control Mode" | "Control Mode" | `select.ems_system_control_mode` | select |

**Key rule:** Entity `name` should NOT repeat the device name. With `has_entity_name: True`, HA prepends the device name automatically. So "Battery SOC" under device "EMS Huawei" becomes "EMS Huawei Battery SOC" in the UI.

**Null name trick:** Setting `name: null` makes HA derive the name from `device_class`. For a binary_sensor with `device_class: connectivity`, the name becomes just "Connectivity" -- clean and standard.

## Sources

- [MQTT Integration - Home Assistant](https://www.home-assistant.io/integrations/mqtt/) -- HIGH confidence, official docs
- [MQTT Sensor - Home Assistant](https://www.home-assistant.io/integrations/sensor.mqtt/) -- HIGH confidence
- [MQTT Binary Sensor - Home Assistant](https://www.home-assistant.io/integrations/binary_sensor.mqtt/) -- HIGH confidence
- [MQTT Number - Home Assistant](https://www.home-assistant.io/integrations/number.mqtt/) -- HIGH confidence
- [MQTT Select - Home Assistant](https://www.home-assistant.io/integrations/select.mqtt/) -- HIGH confidence
- [MQTT Button - Home Assistant](https://www.home-assistant.io/integrations/button.mqtt/) -- HIGH confidence
- [MQTT Switch - Home Assistant](https://www.home-assistant.io/integrations/switch.mqtt/) -- HIGH confidence
- [Add-on Configuration - HA Developer Docs](https://developers.home-assistant.io/docs/apps/configuration/) -- HIGH confidence, translations format
- [Add-on Presentation/Ingress - HA Developer Docs](https://developers.home-assistant.io/docs/apps/presentation/) -- HIGH confidence, ingress config
- [Entity Category discussion](https://community.home-assistant.io/t/entity-category-mqtt-diagnostic-and-config/609731) -- MEDIUM confidence, community verified
- [expire_after vs availability discussion](https://community.home-assistant.io/t/mqtt-discovery-msg-availability-vs-expire-after/788468) -- MEDIUM confidence
- [EMS-ESP32 HA Integration](https://docs.emsesp.org/Home-Assistant/) -- MEDIUM confidence, real-world reference implementation
- [Zigbee2MQTT HA Integration](https://www.zigbee2mqtt.io/guide/usage/integrations/home_assistant.html) -- MEDIUM confidence, naming convention reference
