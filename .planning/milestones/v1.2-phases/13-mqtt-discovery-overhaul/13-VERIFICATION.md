---
phase: 13-mqtt-discovery-overhaul
verified: 2026-03-23T18:30:00Z
status: passed
score: 13/13 must-haves verified
re_verification: false
---

# Phase 13: MQTT Discovery Overhaul Verification Report

**Phase Goal:** All HA entities follow best practices — availability, origin metadata, proper naming, correct platforms, and entity categories
**Verified:** 2026-03-23T18:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | All discovery payloads include origin metadata with name EMS and sw version | VERIFIED | `ha_mqtt_client.py:339` — `"origin": {"name": "EMS", "sw": _EMS_VERSION}` where `_EMS_VERSION = "1.2.0"` |
| 2  | When EMS disconnects, all entities show unavailable via LWT on ems/status | VERIFIED | `will_set` at line 269, "online" on CONNACK at line 433, "offline" on disconnect at line 281 |
| 3  | All sensor entities have expire_after: 120 as stale-data safety net | VERIFIED | `ha_mqtt_client.py:351` — conditional on `entity.platform == "sensor"` |
| 4  | Entity names use has_entity_name: True with short names | VERIFIED | `ha_mqtt_client.py:338` — `"has_entity_name": True` in every discovery payload |
| 5  | Diagnostic entities are tagged with entity_category: diagnostic | VERIFIED | roles, control_state, evcc_battery_mode, pool_status, online entities, L1/L2/L3 power all have `entity_category="diagnostic"` |
| 6  | All applicable entities have correct device_class and state_class | VERIFIED | SoC=battery/measurement, power=power/measurement, enum entities=enum/None; confirmed by TestDiscoveryDeviceClass (4 tests passing) |
| 7  | Device info includes configuration_url pointing to EMS dashboard | VERIFIED | `ha_mqtt_client.py:331` — `device_info["configuration_url"] = self._configuration_url` injected from constructor |
| 8  | Three HA devices exist: EMS Huawei, EMS Victron, EMS System | VERIFIED | `_DEVICES` dict at lines 178-194 with identifiers ems_huawei, ems_victron, ems_system |
| 9  | All existing unique_id values preserved | VERIFIED | `ha_mqtt_client.py:335` — `f"{self._device_id}_{entity.entity_id}"` preserves all IDs |
| 10 | huawei_online and victron_online appear as binary_sensor with device_class connectivity | VERIFIED | `BINARY_SENSOR_ENTITIES` lines 126-146; huawei_online and victron_online absent from SENSOR_ENTITIES (confirmed by test_huawei_online_not_in_sensors) |
| 11 | grid_charge_active and export_active appear as binary_sensor with device_class running | VERIFIED | `BINARY_SENSOR_ENTITIES` lines 148-170; device_class="running" for both |
| 12 | Old sensor discovery topics for migrated entities are cleaned up | VERIFIED | `_cleanup_old_sensor_topics()` at lines 388-396; `_MIGRATED_TO_BINARY = ["huawei_online", "victron_online"]`; publishes empty retained payload to old topics |
| 13 | Every option in ha-addon/config.yaml has corresponding entry in translations/en.yaml | VERIFIED | Verification script output: "Config keys: 40 / Translation keys: 40 / All keys have translations / All entries have name and description" |

**Score:** 13/13 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/ha_mqtt_client.py` | EntityDefinition dataclass, three-device discovery, LWT, origin metadata | VERIFIED | 460 lines; EntityDefinition frozen dataclass, SENSOR_ENTITIES (15), BINARY_SENSOR_ENTITIES (4), _DEVICES, _AVAILABILITY_TOPIC, will_set, _cleanup_old_sensor_topics |
| `tests/test_ha_mqtt_client.py` | Tests for all new discovery payload fields | VERIFIED | 86 tests, 0 failures; classes covering origin, availability, expire_after, has_entity_name, entity_category, device_class, configuration_url, LWT, binary sensors, migration |
| `backend/controller_model.py` | export_active field on CoordinatorState | VERIFIED | Line 170: `export_active: bool = False` with docstring |
| `ha-addon/translations/en.yaml` | Human-readable names and descriptions for all config options | VERIFIED | 40 entries covering all config.yaml option and schema keys; every entry has name and description |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `ha_mqtt_client.py` | MQTT broker | `will_set()` before connect, "online" in `_on_connect` | VERIFIED | Lines 269-271 (will_set), line 433 (online publish in _on_connect), line 281 (offline in disconnect) |
| `ha_mqtt_client.py` | `controller_model.py` | `value_key` matches CoordinatorState fields | VERIFIED | `test_value_keys_valid` passes; extra_fields (huawei_power_w, victron_power_w, victron_l[1-3]_power_w) provided by coordinator at call site |
| `ha_mqtt_client.py` | MQTT broker | Empty retained payload to old sensor topics, new binary_sensor discovery | VERIFIED | `_cleanup_old_sensor_topics()` publishes to `homeassistant/sensor/ems/huawei_online/config` and `homeassistant/sensor/ems/victron_online/config`; binary_sensor topics use `homeassistant/binary_sensor/` prefix |
| `ha_mqtt_client.py` | `controller_model.py` | value_key references export_active and grid_charge_slot_active | VERIFIED | BINARY_SENSOR_ENTITIES lines 163/167: `value_key="grid_charge_slot_active"` and `value_key="export_active"` both present in CoordinatorState |
| `backend/coordinator.py` | `ha_mqtt_client.py` | `publish(self._state, extra_fields=extra)` | VERIFIED | Line 1106: publish called with state plus extra dict containing huawei_power_w, victron_power_w, victron_l[1-3]_power_w |
| `ha-addon/translations/en.yaml` | `ha-addon/config.yaml` | Key names match option keys | VERIFIED | All 40 config.yaml keys (options + schema) have matching translation entries |

---

### Data-Flow Trace (Level 4)

Not applicable — `ha_mqtt_client.py` is a publisher/driver, not a rendering component. It reads from `CoordinatorState` dataclass and writes to the MQTT broker. The `test_value_keys_valid` test (line 850) verifies every `value_key` exists in either CoordinatorState fields or the documented extra_fields dict.

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 86 tests pass | `uv run python -m pytest tests/test_ha_mqtt_client.py -x -v` | 86 passed, 0 failures | PASS |
| SENSOR_ENTITIES count is 15 | Count EntityDefinition instances in SENSOR_ENTITIES block | 15 | PASS |
| BINARY_SENSOR_ENTITIES count is 4 | Count EntityDefinition instances | 4 | PASS |
| Translations cover all 40 config.yaml keys | Verification script diff | "All keys have translations" | PASS |
| export_active wired in coordinator | `grep export_active coordinator.py` | `export_active=(control_state == "EXPORTING")` at line 1195 | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| DISC-01 | 13-01 | All discovery payloads include `origin` metadata | SATISFIED | `ha_mqtt_client.py:339` — origin dict with name/sw in every payload |
| DISC-02 | 13-01 | Availability topic with LWT — entities show unavailable on EMS offline | SATISFIED | will_set, "online" on CONNACK, "offline" on disconnect |
| DISC-03 | 13-01 | `expire_after: 120` on all sensor entities | SATISFIED | Line 351, conditional on platform == "sensor" |
| DISC-04 | 13-01 | `has_entity_name: True` with shortened entity names | SATISFIED | Line 338; names like "Battery SoC", "Discharge Setpoint" |
| DISC-05 | 13-01 | `entity_category` tagging — diagnostic for status/online entities | SATISFIED | Roles, control_state, evcc_battery_mode, pool_status, L-power entities all have entity_category="diagnostic" |
| DISC-06 | 13-01 | `device_class` and `state_class` audit | SATISFIED | SoC=battery, power=power, control_state/roles/mode=enum; 4 test classes confirm |
| DISC-07 | 13-01 | `configuration_url` in device info | SATISFIED | Lines 331 — injected into every device dict from constructor param |
| DISC-08 | 13-02 | `huawei_online` and `victron_online` moved to binary_sensor with device_class connectivity | SATISFIED | BINARY_SENSOR_ENTITIES with device_class="connectivity"; removed from SENSOR_ENTITIES |
| DISC-09 | 13-02 | `grid_charge_active` and `export_active` as binary_sensor with device_class running | SATISFIED | BINARY_SENSOR_ENTITIES lines 148-170, device_class="running" |
| DISC-10 | 13-01 | Three HA devices — EMS Huawei, EMS Victron, EMS System | SATISFIED | _DEVICES dict; entities grouped by device_group field |
| DISC-11 | 13-02 | Platform migration cleanup — empty retained payloads to old sensor topics | SATISFIED | `_cleanup_old_sensor_topics()` with `_migration_done` flag |
| DISC-12 | 13-01 | Existing `unique_id` values preserved | SATISFIED | Format `f"{device_id}_{entity_id}"` unchanged; test_unique_id_preservation passes |
| DISC-13 | 13-03 | Add-on `translations/en.yaml` with human-readable names and descriptions | SATISFIED | 40 translation entries, 100% coverage, all have name+description |

All 13 requirements satisfied. No orphaned requirements detected.

---

### Anti-Patterns Found

None. No TODO, FIXME, placeholder comments, empty return values, or stub implementations found in any modified file.

---

### Human Verification Required

None. All behavioral checks are automated and passed.

---

### Gaps Summary

No gaps. All 13/13 truths verified, all artifacts substantive and wired, all requirements satisfied, all tests passing.

---

_Verified: 2026-03-23T18:30:00Z_
_Verifier: Claude (gsd-verifier)_
