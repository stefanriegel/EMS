---
phase: 24-vrm-dess-integration
plan: 01
subsystem: integration
tags: [vrm, dess, mqtt, config, models]
dependency_graph:
  requires: []
  provides: [vrm_client, dess_mqtt, dess_models, vrm_config, dess_config]
  affects: [coordinator, main, api]
tech_stack:
  added: []
  patterns: [httpx-async-poll, paho-mqtt-subscription, config-from-env]
key_files:
  created:
    - backend/dess_models.py
    - backend/vrm_client.py
    - backend/dess_mqtt.py
    - tests/test_vrm_client.py
    - tests/test_dess_mqtt.py
  modified:
    - backend/config.py
decisions:
  - VRM attribute IDs (51=SoC, 49=power, 1=grid, 131=PV, 73=consumption) mapped from API response structure
  - DESS MQTT subscriber uses asyncio.get_running_loop() for Python 3.14 compatibility
  - get_active_slot returns None when mode=0 to prevent stale schedule data from affecting coordinator
metrics:
  duration: 6min
  completed: "2026-03-24T14:49:15Z"
  tasks: 2
  files: 6
---

# Phase 24 Plan 01: VRM/DESS Integration Layer Summary

VRM REST client with PAT auth and 5-minute async poll loop, DESS MQTT subscriber parsing Venus OS DynamicEss schedule topics, and shared data models with VrmConfig/DessConfig following from_env() pattern.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | e095013 | VRM client, DESS models, VRM/DESS config dataclasses |
| 2 | 7a83905 | DESS MQTT subscriber with Venus OS schedule parsing |

## What Was Built

### Data Models (backend/dess_models.py)
- `DessScheduleSlot`: soc_pct, start_s, duration_s, strategy, active
- `DessSchedule`: 4 slots, mode (0=off, 1=auto, 4=Node-RED), last_update
- `VrmDiagnostics`: battery_soc_pct, battery_power_w, grid_power_w, pv_power_w, consumption_w, timestamp

### Config Dataclasses (backend/config.py)
- `VrmConfig`: token, site_id, poll_interval_s with from_env() reading VRM_TOKEN, VRM_SITE_ID, VRM_POLL_INTERVAL_S
- `DessConfig`: host (fallback VICTRON_HOST), port, portal_id with from_env() reading DESS_MQTT_HOST, DESS_MQTT_PORT, DESS_PORTAL_ID

### VRM Client (backend/vrm_client.py)
- Async httpx client polling `/v2/installations/{site_id}/diagnostics`
- PAT auth via `X-Authorization: Token {token}` header
- Background asyncio task with configurable poll interval
- 429 rate limit handling, connection error graceful degradation
- 15-minute staleness detection marking available=False

### DESS MQTT Subscriber (backend/dess_mqtt.py)
- paho-mqtt client mirroring EvccMqttDriver pattern exactly
- Subscribes to `N/{portalId}/settings/0/Settings/DynamicEss/#`
- Parses Schedule/{0-3}/{Soc,Start,Duration,Strategy} and Mode topics
- `get_active_slot(now_seconds_from_midnight)` for coordinator consumption
- mode=0 treated as DESS-off (returns None from get_active_slot)

### Tests
- 13 VRM client tests: happy path parsing, 429 handling, connection error, staleness, config from_env
- 13 DESS MQTT tests: message parsing per field, unknown topic handling, connect failure, disconnect, get_active_slot with mode checks

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all modules are fully implemented with real logic.

## Self-Check: PASSED

All 6 files exist. Both commits (e095013, 7a83905) verified in git history.
