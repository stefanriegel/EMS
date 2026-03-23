---
phase: 14-controllable-entities
verified: 2026-03-23T21:30:00Z
status: passed
score: 9/9 must-haves verified
re_verification: false
---

# Phase 14: Controllable Entities Verification Report

**Phase Goal:** Users can control EMS parameters and modes directly from HA UI, automations, and scripts
**Verified:** 2026-03-23T21:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | ha_mqtt_client.py subscribes to command topics on MQTT connect | VERIFIED | `_on_connect` iterates NUMBER+SELECT+BUTTON entities and calls `client.subscribe(entity.command_topic, qos=1)` (line 590-598) |
| 2 | Incoming MQTT messages are dispatched to a registered callback via call_soon_threadsafe | VERIFIED | `_on_message` calls `self._loop.call_soon_threadsafe(self._command_callback, entity_id, payload_str)` (line 632) |
| 3 | Number, select, and button entity definitions exist with correct discovery payloads | VERIFIED | NUMBER_ENTITIES (5), SELECT_ENTITIES (1), BUTTON_ENTITIES (2) defined at lines 198-261; `_discovery_payload` handles all three platforms with command_topic, min/max/step/mode/options/payload_press |
| 4 | Subscribe calls in _on_connect are wrapped in try/except for BrokenPipeError | VERIFIED | Each `client.subscribe()` call is inside `try/except (BrokenPipeError, OSError)` (lines 593-599) |
| 5 | A health check detects silent paho thread crash and forces reconnect | VERIFIED | `check_health()` method at lines 395-416 checks `_last_publish_time` staleness and calls `self._client.reconnect()` |
| 6 | User can adjust min-SoC via HA number slider and the value takes effect on the next control cycle | VERIFIED | `_cmd_min_soc_huawei` updates `_sys_config.huawei_min_soc_pct`; `_cmd_min_soc_victron` updates `_sys_config.victron_min_soc_pct`; mode_override checked in `_run_cycle` step 2b |
| 7 | User can switch control mode via HA select entity and EMS reflects it | VERIFIED | `_cmd_control_mode` sets `_mode_override`; control loop checks it at lines 564-587 and executes HOLD/GRID_CHARGE/DISCHARGE_LOCKED commands accordingly |
| 8 | User can press Force Grid Charge and EMS enters GRID_CHARGE with 60min auto-timeout | VERIFIED | `_cmd_force_grid_charge` sets `_mode_override = "GRID_CHARGE"` and schedules `loop.call_later(3600, self._clear_mode_override)` (lines 295-310) |
| 9 | After any command, the entity state topic reflects the updated value (state echo) | VERIFIED | `_handle_ha_command` calls `_trigger_state_echo()` after every dispatch; `_trigger_state_echo` fires `loop.create_task(self._ha_mqtt_client.publish(...))` immediately with `_build_controllable_extra_fields()` |

**Score:** 9/9 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/ha_mqtt_client.py` | MQTT subscribe infrastructure, entity definitions, health check | VERIFIED | Contains `_on_message` (line 605), `_on_connect` subscribes (line 589), `NUMBER_ENTITIES` (5), `SELECT_ENTITIES` (1), `BUTTON_ENTITIES` (2), `check_health()` |
| `tests/test_ha_mqtt_client.py` | Tests for subscribe, dispatch, discovery, health check | VERIFIED | Covers `test_on_connect_subscribes_to_command_topics`, `test_on_message_dispatches_via_threadsafe`, `test_on_message_no_callback_is_noop`, `test_subscribe_broken_pipe_does_not_crash`, `test_number_entities_count`, `test_select_entities_count`, `test_button_entities_count`, and health check tests |
| `backend/coordinator.py` | Command handler, mode override, auto-timeout, state echo trigger | VERIFIED | Contains `_handle_ha_command` (line 213), `_mode_override` (line 123), `_mode_timeout_handle` (line 124), `_trigger_state_echo` (line 326), `_clear_mode_override` (line 319), `_build_controllable_extra_fields` (line 340) |
| `backend/main.py` | Wiring of command callback between coordinator and MQTT client | VERIFIED | `ha_client.set_command_callback(coordinator._handle_ha_command)` at line 513; `coordinator.set_supervisor_client(supervisor)` at line 471 |
| `tests/test_coordinator.py` | Tests for command handling, mode override, timeout, state echo | VERIFIED | `TestHaCommandHandler` class at line 1382 with 16 tests covering all 8 entity_ids, clamping, state echo, supervisor persistence, graceful None handling |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `ha_mqtt_client.py _on_connect` | `client.subscribe` | subscribes to command topics on connect | VERIFIED | Pattern `client.subscribe` found at line 594; iterates all controllable entities |
| `ha_mqtt_client.py _on_message` | `_command_callback` | call_soon_threadsafe to cross thread boundary | VERIFIED | `self._loop.call_soon_threadsafe(self._command_callback, entity_id, payload_str)` at line 632 |
| `backend/main.py lifespan` | `ha_mqtt.set_command_callback` | wires coordinator._handle_ha_command as callback | VERIFIED | Line 513: `ha_client.set_command_callback(coordinator._handle_ha_command)` |
| `backend/coordinator.py _handle_ha_command` | `ha_mqtt.publish` | state echo after command processing | VERIFIED | `_trigger_state_echo` calls `self._ha_mqtt_client.publish(self._state, extra_fields=extra)` via loop.create_task |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `coordinator.py _build_controllable_extra_fields` | `control_mode_override`, `huawei_min_soc_pct`, `victron_min_soc_pct`, `huawei_deadband_w`, `victron_deadband_w`, `ramp_rate_w` | `_mode_override`, `_sys_config.*`, `_huawei_deadband_w`, `_victron_deadband_w`, `_huawei_ramp_w_per_cycle` — all updated by command handlers | Yes — live runtime values mutated by command handlers, not static defaults | FLOWING |
| `ha_mqtt_client.py _publish_state` | CoordinatorState + extra_fields | Coordinator passes state via `publish()` call every cycle and after every command | Yes — real dataclass snapshot serialized to JSON | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| NUMBER_ENTITIES has 5 items | `uv run python -c "from backend.ha_mqtt_client import NUMBER_ENTITIES; print(len(NUMBER_ENTITIES))"` | 5 | PASS |
| SELECT_ENTITIES has 1 item with correct options | `uv run python -c "from backend.ha_mqtt_client import SELECT_ENTITIES; print(len(SELECT_ENTITIES), SELECT_ENTITIES[0].options)"` | 1 ['AUTO', 'HOLD', 'GRID_CHARGE', 'DISCHARGE_LOCKED'] | PASS |
| BUTTON_ENTITIES has 2 items | `uv run python -c "from backend.ha_mqtt_client import BUTTON_ENTITIES; print(len(BUTTON_ENTITIES))"` | 2 | PASS |
| All ha_mqtt_client and coordinator tests pass | `uv run pytest tests/test_ha_mqtt_client.py tests/test_coordinator.py -q` | 285 passed, 1 skipped | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| CTRL-01 | 14-01-PLAN | MQTT subscribe infrastructure — EMS listens on command topics for bidirectional control | SATISFIED | `_on_connect` subscribes all 8 command topics; `_on_message` dispatches via `call_soon_threadsafe` |
| CTRL-02 | 14-01-PLAN | Number entity for Huawei min-SoC (10-100%, step 5, slider mode) | SATISFIED | `min_soc_huawei` in `NUMBER_ENTITIES` with `min_val=10, max_val=100, step=5, mode="slider"` |
| CTRL-03 | 14-01-PLAN | Number entity for Victron min-SoC (10-100%, step 5, slider mode) | SATISFIED | `min_soc_victron` in `NUMBER_ENTITIES` with `min_val=10, max_val=100, step=5, mode="slider"` |
| CTRL-04 | 14-01-PLAN | Number entity for Huawei dead-band (50-1000W, step 50, box mode) | SATISFIED | `deadband_huawei` in `NUMBER_ENTITIES` with `min_val=50, max_val=1000, step=50, mode="box"` |
| CTRL-05 | 14-01-PLAN | Number entity for Victron dead-band (50-500W, step 50, box mode) | SATISFIED | `deadband_victron` in `NUMBER_ENTITIES` with `min_val=50, max_val=500, step=50, mode="box"` |
| CTRL-06 | 14-01-PLAN | Number entity for ramp rate (100-2000W, step 100, box mode) | SATISFIED | `ramp_rate` in `NUMBER_ENTITIES` with `min_val=100, max_val=2000, step=100, mode="box"` |
| CTRL-07 | 14-01-PLAN, 14-02-PLAN | Select entity for control mode (AUTO, HOLD, GRID_CHARGE, DISCHARGE_LOCKED) | SATISFIED | `control_mode` in `SELECT_ENTITIES`; `_cmd_control_mode` sets `_mode_override`; control loop enforces it |
| CTRL-08 | 14-01-PLAN, 14-02-PLAN | Button entity for Force Grid Charge with auto-timeout | SATISFIED | `force_grid_charge` in `BUTTON_ENTITIES`; `_cmd_force_grid_charge` sets GRID_CHARGE mode + `loop.call_later(3600, _clear_mode_override)` |
| CTRL-09 | 14-01-PLAN, 14-02-PLAN | Button entity for Reset to Auto | SATISFIED | `reset_to_auto` in `BUTTON_ENTITIES`; `_cmd_reset_to_auto` clears `_mode_override` and cancels timeout handle |
| CTRL-10 | 14-02-PLAN | State echo — after processing a command, publish updated state on state_topic | SATISFIED | `_trigger_state_echo` called after every command dispatch; publishes via `ha_mqtt_client.publish` with `_build_controllable_extra_fields()` |
| CTRL-11 | 14-01-PLAN | Defensive paho threading — wrap subscribe in try/except, periodic health check for silent thread crash | SATISFIED | Each `client.subscribe()` wrapped in `try/except (BrokenPipeError, OSError)`; `check_health()` called per control cycle at coordinator line 1309 |

All 11 requirements satisfied. No orphaned requirements found.

---

### Anti-Patterns Found

None detected. Scan of `backend/ha_mqtt_client.py`, `backend/coordinator.py`, `backend/main.py`:

- No TODO/FIXME/PLACEHOLDER comments in modified code paths
- No empty implementations or stub returns
- `_cmd_*` handlers all mutate real runtime state (not no-ops)
- `_trigger_state_echo` creates a real async task rather than being a stub
- Discovery payloads include real field values from entity definitions

One minor warning: `check_health()` is called synchronously at coordinator line 1309 but the mock in tests returns an `AsyncMock` coroutine, causing "RuntimeWarning: coroutine was never awaited" in 4 integration tests. This does not affect production behavior because `check_health()` is defined as a synchronous method returning `bool` — the mock configuration in those 4 tests is overly strict. Not a code defect; the warning does not indicate a functional gap.

---

### Human Verification Required

#### 1. End-to-End HA UI Slider Command

**Test:** In Home Assistant, navigate to the EMS device and adjust the "Huawei Min SoC" number slider. Wait one control cycle (5 seconds).
**Expected:** The slider reflects the new value. The EMS control loop respects the new min-SoC threshold in its next role assignment calculation.
**Why human:** Requires a live MQTT broker, connected HA instance, and observable battery control behavior.

#### 2. Force Grid Charge Button with 60-Minute Auto-Timeout

**Test:** Press the "Force Grid Charge" button in HA. Observe that EMS logs "Force grid charge activated". Wait for the 60-minute timeout (or mock time to fast-forward).
**Expected:** EMS enters GRID_CHARGE mode immediately. After 60 minutes, mode automatically reverts to AUTO without user intervention.
**Why human:** The 60-minute real-time timeout cannot be verified without running the full service or mocking time at the asyncio loop level.

#### 3. Control Mode Select Persistence After Restart

**Test:** Set control mode to HOLD via the HA select entity. Restart the EMS add-on. Verify the mode override is still HOLD after restart.
**Expected:** Supervisor persistence (read-merge-write to `/addons/self/options`) restores the HOLD override on next startup.
**Why human:** Requires a live HA Supervisor environment with add-on restart capability to verify `supervisor_client.get_addon_options` / `set_addon_options` end-to-end.

---

### Gaps Summary

No gaps. All automated checks passed with full evidence.

The full command flow from HA UI through MQTT to coordinator config changes is wired and verified:

- HA number/select/button entity discovers on MQTT with correct payloads
- User interaction sends command to `homeassistant/{platform}/ems/{entity_id}/set`
- `_on_message` parses entity_id, crosses paho→asyncio boundary via `call_soon_threadsafe`
- `_handle_ha_command` dispatches to one of 8 handlers with value clamping
- Config/runtime state updates take effect on the next control cycle (mode override checked before grid-charge scheduling)
- State echo publishes immediately after every command so HA slider reflects the accepted value
- Health check runs every cycle; 60-min timeout on force_grid_charge auto-resets the override

---

_Verified: 2026-03-23T21:30:00Z_
_Verifier: Claude (gsd-verifier)_
