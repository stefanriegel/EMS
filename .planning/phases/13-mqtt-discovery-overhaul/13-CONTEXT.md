# Phase 13: MQTT Discovery Overhaul - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Overhaul all HA MQTT discovery payloads to follow best practices: availability topics with LWT, origin metadata, expire_after, has_entity_name with shortened names, entity_category tagging, device_class audit, configuration_url. Move boolean-state entities to binary_sensor platform. Split single HA device into three devices (EMS Huawei, EMS Victron, EMS System). Add translations/en.yaml. Preserve all existing unique_id values.

</domain>

<decisions>
## Implementation Decisions

### Entity Architecture
- Three HA devices: EMS Huawei (huawei_soc, huawei_setpoint, huawei_power, huawei_role, huawei_online), EMS Victron (victron_soc, victron_setpoint, victron_power, victron_role, victron_online, victron_l1/l2/l3_power), EMS System (combined_power, control_state, evcc_battery_mode, pool_status, grid_charge_active, export_active)
- Replace flat _ENTITIES tuple list with EntityDefinition dataclass: entity_id, name, platform (sensor/binary_sensor), unit, device_class, state_class, entity_category, value_key, device_group (huawei/victron/system)
- Preserve all existing unique_id values (ems_{entity_id}) — never change existing unique_ids
- Platform migration for huawei_online/victron_online: publish empty retained payload to old homeassistant/sensor/ems/{entity_id}/config topics, then publish new homeassistant/binary_sensor/... discovery. One-time migration on first v1.2 startup

### Discovery Payload Standards
- LWT availability: availability_topic ems/status, payload_available "online", payload_not_available "offline". Set via will_set() before connect(). Publish "online" in _on_connect
- expire_after: 120 seconds on all sensor entities
- Entity naming with has_entity_name: True — short names without device prefix (e.g. "Battery SoC" under device "EMS Huawei"). Use name: null for binary sensors with device_class (HA auto-derives name from device_class)
- Origin metadata: {"name": "EMS", "sw": "1.2.0"} on all discovery payloads

### Entity Categorization
- Primary entities: SoC (%), power (W), combined_power, roles
- Diagnostic entities: online/offline, pool_status, control_state, evcc_battery_mode, L1/L2/L3 power
- Binary sensor device_class: connectivity for huawei_online/victron_online, running for grid_charge_active/export_active
- configuration_url: dynamically built from add-on network config (http://{host}:8000), falls back to static URL

### Translations
- en.yaml only — cover all config.yaml options with name + description per field

### Claude's Discretion
- Internal refactoring approach for _discovery_payload() and _ensure_discovery() methods
- State topic structure (keep shared JSON or split per device)
- How to detect first v1.2 startup for migration (flag file, version check, etc.)
- Whether grid_charge_active and export_active derive from existing state fields or need new coordinator outputs

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- backend/ha_mqtt_client.py — HomeAssistantMqttClient class, _ENTITIES tuple list, _discovery_payload(), _ensure_discovery(), _publish_state()
- backend/evcc_mqtt_driver.py — paho subscribe pattern with call_soon_threadsafe (reference for Phase 14)
- backend/config.py — HaMqttConfig dataclass with from_env()

### Established Patterns
- paho.mqtt.client with CallbackAPIVersion.VERSION2
- _on_connect / _on_disconnect callbacks crossing thread boundary via loop.call_soon_threadsafe()
- Discovery payloads published with retain=True on first publish after connect
- State published as single JSON object to shared state topic

### Integration Points
- coordinator.py calls ha_mqtt.publish(state, extra_fields) every 5s control cycle
- main.py lifespan creates HomeAssistantMqttClient and wires to coordinator
- ha-addon/config.yaml defines add-on config schema (translations target this)
- CoordinatorState dataclass provides all entity values via dataclasses.asdict()

</code_context>

<specifics>
## Specific Ideas

No specific requirements beyond research findings. Follow HA MQTT discovery docs for payload formats.

</specifics>

<deferred>
## Deferred Ideas

- MQTT subscribe infrastructure (Phase 14 scope)
- Number/select/button entities (Phase 14 scope)
- Diagnostic sensors like uptime, cycle duration (deferred to v2+)

</deferred>
