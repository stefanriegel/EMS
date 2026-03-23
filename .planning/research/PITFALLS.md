# Pitfalls Research

**Domain:** HA best practice alignment for existing dual-battery EMS add-on (MQTT discovery overhaul, Ingress, services, controllable entities)
**Researched:** 2026-03-23
**Confidence:** HIGH (based on official HA docs, community issue trackers, and direct codebase analysis)

## Critical Pitfalls

### Pitfall 1: MQTT Entity unique_id Change Destroys User Dashboards and Automations

**What goes wrong:**
The current `unique_id` format is `ems_{entity_id}` (e.g., `ems_huawei_soc`). When overhauling MQTT discovery to add `entity_category`, `availability`, `origin`, and binary sensors, the temptation is to restructure the `unique_id` scheme (e.g., to include the platform prefix, or to rename entities for better HA naming compliance). Any `unique_id` change causes HA to treat the entity as a completely new entity. The old entity becomes "unavailable" forever (retained discovery config still on the broker), and the new entity gets a fresh entity ID. Every HA dashboard card, automation, script, and history reference pointing at the old entity ID breaks silently.

**Why it happens:**
HA's entity registry keys entities by `(platform, unique_id)`. Changing either deletes the entity and creates a new one. Developers rename entities "for clarity" during a refactor, not realizing `unique_id` is the primary key that users' entire HA configuration depends on.

**How to avoid:**
- Never change existing `unique_id` values. The current `ems_huawei_soc`, `ems_victron_soc`, etc. must remain exactly as they are.
- New entities (binary sensors, number entities, select entities) get new `unique_id` values following the existing pattern: `ems_{entity_id}`.
- Use `default_entity_id` (not `object_id`, which is deprecated as of HA 2025.10 and removed in 2026.4) to control the entity ID shown in HA, without touching `unique_id`.
- To move an existing entity from `sensor` to `binary_sensor` platform (e.g., `huawei_online`), use the `migrate_discovery` process: publish `{"migrate_discovery": true}` to the old topic, then publish the new discovery config to the new platform topic.

**Warning signs:**
- After add-on update, entities show as "unavailable" in HA while new entities with `_2` suffix appear.
- User automations stop firing because `sensor.ems_huawei_soc` no longer exists.
- HA logs show "entity not found" warnings referencing old entity IDs.

**Phase to address:**
Must be the very first step of the MQTT discovery overhaul phase. Define a migration plan before writing any discovery code.

---

### Pitfall 2: Migrating sensor Entities to binary_sensor Breaks the Discovery Topic Path

**What goes wrong:**
The current code publishes ALL entities (including `huawei_online`, `victron_online`, `pool_status`) as `sensor` platform via discovery topic `homeassistant/sensor/ems/{entity_id}/config`. These should be `binary_sensor` per HA best practices (they represent on/off states). But changing the discovery topic from `homeassistant/sensor/...` to `homeassistant/binary_sensor/...` without cleaning up the old topic leaves a ghost `sensor` entity alongside the new `binary_sensor` entity. The user sees duplicates: `sensor.ems_huawei_online` (stale, unavailable) and `binary_sensor.ems_huawei_online` (new, working).

**Why it happens:**
MQTT discovery topics are retained on the broker. Publishing to a new topic path does not delete the old one. HA discovers both and creates both entities. The old entity never goes away until someone publishes an empty payload to the old topic.

**How to avoid:**
- For each entity changing platform, publish an empty retained payload to the OLD discovery topic to delete it: `self._client.publish("homeassistant/sensor/ems/huawei_online/config", "", retain=True)`.
- Then publish the new discovery config to the NEW topic: `homeassistant/binary_sensor/ems/huawei_online/config`.
- Use `migrate_discovery` for entities that need to preserve their entity registry settings.
- Implement a one-time migration function that runs on first startup after the version upgrade, cleaning up old topics before publishing new ones.
- Include version tracking in the MQTT client: store the last-published discovery schema version (e.g., in a file on the HA config volume) so migration only runs once.

**Warning signs:**
- Duplicate entities in HA entity registry (one sensor, one binary_sensor with same name).
- Old sensor entities stuck in "unavailable" state indefinitely.
- Entity count in HA device info doubles instead of staying the same.

**Phase to address:**
MQTT discovery overhaul phase. Must implement topic cleanup before new discovery publication.

---

### Pitfall 3: Ingress Path Rewriting Breaks SPA Routing, WebSocket, and API Calls

**What goes wrong:**
HA Ingress proxies add-on traffic through `/api/hassio_ingress/{token}/`. The current SPA uses absolute paths (`/api/state`, `/api/ws/state`, static assets at `/assets/`). When accessed via Ingress, these paths resolve to the HA root, not the add-on. Every API call 404s. Every static asset returns the HA frontend HTML. The WebSocket connection at `/api/ws/state` hits HA's own WebSocket API instead of the EMS one.

**Why it happens:**
The Vite config has no `base` setting (defaults to `/`). The frontend fetches from hardcoded paths like `/api/state`. HA Ingress adds the `X-Ingress-Path` header with the correct prefix, but neither the FastAPI backend nor the React frontend reads or uses it. The SPA's `index.html` references assets at `/assets/index-xxx.js` which bypass the Ingress prefix entirely.

**How to avoid:**
- **Frontend build:** Set Vite's `base` to `./` (relative paths) so all asset references in `index.html` become `./assets/index-xxx.js` instead of `/assets/index-xxx.js`. This makes assets load correctly regardless of the URL prefix.
- **API calls:** The frontend must detect the Ingress base path at runtime. Approach: inject a `<script>` tag in `index.html` that reads the document's `baseURI` or uses a well-known endpoint. Alternatively, use relative URLs (`./api/state` instead of `/api/state`) which work under any prefix.
- **WebSocket:** The WebSocket URL must be constructed from the current page location, not hardcoded. Use `new URL('./api/ws/state', window.location.href)` to derive the full WebSocket URL dynamically. Ensure the protocol is `wss://` when the page is loaded over HTTPS (which Ingress always is).
- **FastAPI:** Add `ingress: true` to `config.yaml`. Read the `X-Ingress-Path` header in a middleware and make it available to responses if needed for URL generation. The FastAPI app must NOT assume it runs at `/` -- use `root_path` from the ASGI scope.
- **Static file mount:** The current `StaticFiles(directory="frontend/dist", html=True)` catch-all must work correctly with Ingress. Since Ingress strips the prefix before forwarding, the backend sees requests at `/` not `/api/hassio_ingress/{token}/`. This means the backend routing should work as-is, but the frontend asset URLs must be relative.

**Warning signs:**
- Dashboard loads as a blank page when accessed through HA sidebar.
- Browser console shows 404 errors for `/assets/index-xxx.js`.
- WebSocket connects to HA's API instead of EMS, producing "invalid message" errors.
- API calls return HA's "unauthorized" response instead of EMS data.

**Phase to address:**
Ingress support phase. Must be tested with both direct access (`http://host:8000`) and Ingress access (`https://ha.local/api/hassio_ingress/...`) simultaneously.

---

### Pitfall 4: Adding MQTT Subscribe to a Publish-Only Client Introduces Threading Hazards

**What goes wrong:**
The current `HomeAssistantMqttClient` is publish-only: it calls `self._client.publish()` from the asyncio thread and handles `_on_connect`/`_on_disconnect` callbacks from paho's thread. Adding subscribe capability (for command_topic on Number/Select entities and service call topics) introduces `_on_message` callbacks that need to safely mutate state and trigger actions in the asyncio thread. A known paho-mqtt bug (reported June 2025) causes `BrokenPipeError` when calling `client.subscribe()` in the `_on_connect` callback during reconnection, which crashes paho's background thread silently. After the crash, `publish()` calls succeed without error but messages are never delivered.

**Why it happens:**
paho-mqtt's threading model has a single network thread that handles both reads and writes. When `subscribe()` is called from `_on_connect()` during a reconnect (not initial connect), the socket may be in a transitional state. The background thread crashes, but there is no callback or exception visible to the asyncio side. The `_connected` flag stays `True` because `_on_disconnect` was never called (the thread died, it did not disconnect).

**How to avoid:**
- Add a `_on_subscribe` callback to detect successful subscription acknowledgment from the broker.
- Wrap `subscribe()` calls in `_on_connect` with a try/except that catches `BrokenPipeError` and `OSError`, logs the error, and sets a flag to retry subscription on the next control cycle.
- Add a periodic health check: if `_connected` is `True` but no state update has been published successfully in the last N cycles, force a reconnect by calling `_client.reconnect()`.
- Use QoS 1 for command_topic subscriptions to ensure delivery guarantees on commands that affect real hardware.
- Consider splitting into two paho clients: one for publish (existing pattern, low risk) and one for subscribe (new pattern, isolated failure domain). This matches the existing codebase pattern where `EvccMqttDriver` is a separate client from `HomeAssistantMqttClient`.

**Warning signs:**
- Number entity changes in HA UI do not reach the EMS backend (no log messages for received commands).
- MQTT entities in HA show "unavailable" after a broker restart, even though the EMS log says "HA MQTT connected".
- Paho thread silently stops: `threading.enumerate()` shows the paho thread is gone while `_connected` is still `True`.

**Phase to address:**
Controllable entities phase (Number/Select entities). Must be tested with broker restart scenarios.

---

### Pitfall 5: Number Entity min/max Values Conflict with Hardware Limits

**What goes wrong:**
MQTT Number entities require `min` and `max` in the discovery payload. If the published min/max does not match the actual hardware limits, two bad things happen: (1) HA allows the user to set a value outside hardware-safe range (e.g., Huawei min SoC set to 0% when the BMS minimum is 5%), and (2) HA rejects valid values that are outside the published range (e.g., user tries to set Victron deadband to 50W but the Number entity has `min: 100`). The HA UI shows a slider that silently clips values, and the user does not realize their change was not applied.

**Why it happens:**
The discovery payload's `min`/`max` are static values published at connect time. Hardware limits may vary per installation (different BMS firmware, different inverter models). The current `SystemConfig` has hardcoded defaults (`min_soc_pct_huawei: 10`, `min_soc_pct_victron: 15`) that are reasonable starting values but not universal. If the Number entity discovery publishes `min: 0, max: 100` for SoC and the hardware actually requires `min: 5, max: 95`, the user can set dangerous values.

**How to avoid:**
- Read actual hardware limits from the drivers at startup. The Huawei driver can read min/max SoC from Modbus registers. The Victron driver can read ESS limits from Venus OS registers. Use these as the source of truth for Number entity min/max.
- If hardware limits cannot be read (driver offline at startup), use conservative defaults and update the Number entity discovery payload when the driver connects. Re-publishing discovery with updated min/max is safe -- HA will update the entity.
- Validate every command received on the command_topic against current hardware limits before applying. If the value is out of range, publish the current valid value back to the state_topic (so HA UI reverts to the correct value) and log a warning.
- Document which Number entities are "soft limits" (tuning parameters like deadband, ramp rate) vs. "hard limits" (SoC floors that affect hardware safety).

**Warning signs:**
- HA UI slider range does not match hardware reality.
- User sets min SoC to 5% but BMS refuses to discharge below 10%, causing the controller to appear stuck.
- Number entity state and command value are perpetually out of sync (HA shows 5, backend shows 10).

**Phase to address:**
Controllable entities phase. Must coordinate with driver initialization.

---

### Pitfall 6: Config Migration from Wizard JSON to Add-on Options Loses User Settings

**What goes wrong:**
The current system has two config sources: the setup wizard's `ems_config.json` (on the HA config volume) and the add-on `options.json` (managed by HA Supervisor). The v1.2 plan removes the wizard and makes `options.json` the sole config surface. Existing users who configured EMS through the wizard have settings in `ems_config.json` that are NOT in `options.json` (because they never used the add-on config page). After upgrading to v1.2, the wizard config is ignored, and all user-specific settings (hardware hosts, EVCC endpoints, tariff rates, SoC limits) revert to defaults.

**Why it happens:**
The current `lifespan()` in `main.py` calls `load_setup_config()` and injects values into `os.environ` via `setdefault()`. The add-on's `run.sh` exports `options.json` values BEFORE the Python process starts. If `options.json` has empty defaults, they get exported as empty strings, and `setdefault()` in the lifespan does not override them (because the env var exists, even if empty). The `_require_env()` function treats empty as "not set" and enters degraded mode. So the user upgrades, the wizard config exists on disk but is never read because `run.sh` already exported empty env vars.

**How to avoid:**
- Implement a one-time migration script that runs BEFORE the main application. On first v1.2 startup, check if `ems_config.json` exists and `options.json` still has default values. If so, write the wizard values into the Supervisor options via the Supervisor API (`POST /addons/self/options`).
- The migration must be idempotent: if it runs twice, it should detect that migration already happened (e.g., check a `_migrated` flag in `ems_config.json`) and skip.
- After migration, rename `ems_config.json` to `ems_config.json.bak` so it is clear the file is no longer authoritative.
- Add a startup log message: "Migrated N settings from wizard config to add-on options" or "No wizard config found, using add-on options directly."
- Keep the `load_setup_config()` fallback for one more version as a safety net: if migration fails, the system still works.

**Warning signs:**
- After v1.2 upgrade, the add-on enters "setup-only mode" even though it was fully configured before.
- User sees the HA add-on config page with all fields at defaults, not their actual values.
- Coordinator fails to start because `HUAWEI_HOST` is empty.

**Phase to address:**
First phase of v1.2 (config migration). Must happen before any other changes.

---

### Pitfall 7: Service Calls Fail Silently When Hardware Is Offline

**What goes wrong:**
HA Services (e.g., `ems.set_discharge_setpoint`, `ems.force_grid_charge`) are expected to succeed or raise an explicit error. If the user calls a service from an automation while one or both battery systems are offline, the service must either: (a) apply the command to the available system only, or (b) return an error that HA can display. The current codebase has no service call infrastructure. Adding services without proper error handling means HA automations silently do nothing when hardware is down, and the user never knows.

**Why it happens:**
MQTT-based service calls arrive as messages on a command_topic. Unlike HTTP APIs (which return status codes), MQTT commands are fire-and-forget. If the backend receives a command but cannot execute it (driver offline, value out of range, conflicting state), there is no built-in way to signal failure back to HA.

**How to avoid:**
- Publish the service execution result to a status/response topic that HA can monitor.
- More practically: update the entity state immediately after processing a command. If the command failed, the state does not change, and the HA entity shows the old value (which signals failure to attentive users).
- Log every service call with its result (applied, rejected, partially applied) at INFO level.
- For critical services like `force_grid_charge`: validate prerequisites (tariff window, SoC limits, hardware availability) before accepting the command. If prerequisites fail, publish a notification via Telegram (already wired) explaining why the command was rejected.
- Add integration health information to the device's `availability` topic. If the coordinator is in HOLD/degraded mode, set the device availability to "offline" so HA marks all entities as unavailable and prevents new service calls from being attempted.

**Warning signs:**
- HA automation log shows "service call completed" but nothing happened on the hardware.
- Number entity UI shows a value the user set, but the actual hardware is doing something different.
- No error feedback in HA UI when a command cannot be executed.

**Phase to address:**
HA Services phase. Must define error handling strategy before implementing any service.

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Hardcoding entity list in `_ENTITIES` tuple | Simple, flat, easy to test | Adding new entity types (binary_sensor, number, select) makes the tuple format inadequate; need a proper entity registry class | Never for v1.2; refactor to a typed entity definition system first |
| Publishing all entities on one shared state_topic | One publish call per cycle, simple | Command responses, binary sensors, and number states need their own topics; shared topic grows into a god-object JSON blob | Acceptable for sensor entities; command entities must have separate topics |
| Using `retain=True` for state payloads | Entity value survives broker restart | `expire_after` does not work correctly with retained state payloads (HA replays the retained value on restart, making expired sensors appear available with stale data) | Never for sensors with `expire_after`; use `retain=True` only for discovery config |
| Single paho client for both pub and sub | Fewer connections to broker | Threading issues on reconnect (Pitfall 4); failure in subscribe path kills publish path | Accept only if broker restarts are rare; split clients is safer |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| MQTT Discovery | Using `object_id` in discovery payload | Use `default_entity_id` instead; `object_id` deprecated in HA 2025.10, removed in 2026.4 |
| MQTT Discovery | Publishing discovery config without `retain=True` | Discovery config MUST be retained; otherwise entities disappear after broker restart |
| MQTT Discovery | Not publishing `availability_topic` with `expire_after` | Use `expire_after` for sensor entities (marks unavailable if no update within N seconds) AND publish a birth/will availability topic for the device |
| MQTT Discovery | Missing `origin` field in discovery payload | Add `origin: {name: "EMS", sw_version: "1.2.0", support_url: "..."}` -- HA uses this for device info and issue reporting |
| HA Ingress | Hardcoded absolute paths in SPA | Use relative paths (`./api/state`) or dynamic base path detection from `window.location` |
| HA Ingress | Assuming WebSocket URL is `ws://host:port/path` | Under Ingress, WebSocket must use `wss://` and the full Ingress path; construct from `window.location` |
| HA Ingress | Not setting `ingress: true` in config.yaml | Without this flag, HA Supervisor does not create the Ingress proxy endpoint |
| HA Ingress | Setting `ingress_port` to the wrong value | Must match the port the FastAPI server listens on inside the container (8000) |
| MQTT Number | Publishing `min: 1, max: 100` (defaults) | Always set meaningful min/max that match hardware limits; validate commands server-side |
| MQTT Number | Using `optimistic: true` without state_topic | HA assumes command succeeded immediately; backend must publish actual state to state_topic for closed-loop feedback |
| Supervisor API | Calling `/addons/self/options` with partial data | The Supervisor API replaces ALL options, not just the ones you send; always read current options, merge changes, then write back |
| translations/en.yaml | Mismatched keys between config.yaml options and translations | Every option in `config.yaml` must have a corresponding entry in translations; missing keys show raw option names in UI |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Publishing full state JSON on every 5-second control cycle via MQTT | Broker message queue grows; HA entity history inflated | Throttle MQTT publish to every 15-30 seconds unless values changed significantly | Not a breaking issue at current scale but wasteful; becomes problematic with 30+ entities |
| Re-publishing discovery config on every reconnect without deduplication | Broker processes identical retained messages; HA re-registers entities | Track `_discovery_version` and only re-publish if the schema changed | Not performance-critical but adds unnecessary broker load on flaky connections |
| Ingress + WebSocket with high-frequency state updates | Each WebSocket message goes through HA's nginx proxy, adding latency | Reduce WebSocket update frequency to 5-10s when accessed via Ingress; keep 1-2s for direct access | Noticeable at >20 concurrent Ingress WebSocket connections (unlikely for single-user EMS) |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Accepting MQTT commands without validation | Malicious MQTT message could set SoC limit to 0% or discharge setpoint to max, potentially damaging hardware | Validate every command against hardware limits before applying; reject and log invalid commands |
| Exposing service topics without authentication | Anyone with MQTT broker access can send commands | HA's MQTT broker (Mosquitto add-on) handles auth; ensure command topics require authenticated clients; document that shared brokers need ACLs |
| Ingress token in browser history | Ingress URLs contain a session token that could be shared inadvertently | This is HA's concern, not the add-on's; but do not log or expose the Ingress token in the EMS UI |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Entity names like "EMS Huawei Battery SOC" (current) | Verbose, does not follow HA naming convention (device name should not repeat in entity name) | Name should be just "Battery SOC" since the device is already "EMS Huawei"; HA concatenates device + entity name |
| Changing entity names in discovery without renaming in entity registry | User sees new name in entity list but old name in dashboards (HA caches entity names) | Use `has_entity_name: true` in discovery so HA correctly composes the display name from device + entity name |
| Number entity slider with 0-100 range for a 50-500W deadband | User cannot set precise values; slider resolution too coarse | Set `mode: "box"` for parameters that need exact values; use `mode: "slider"` only for percentage-based values |
| No entity_category on diagnostic entities | Battery role, pool status, and control state entities clutter the main entity list alongside actionable entities | Set `entity_category: "diagnostic"` for read-only status entities; `entity_category: "config"` for tuning parameters |

## "Looks Done But Isn't" Checklist

- [ ] **MQTT Discovery:** All entities have `availability_topic` or `expire_after` -- verify entities go unavailable when EMS stops
- [ ] **MQTT Discovery:** `origin` field included with `name`, `sw_version`, and `support_url` -- verify in HA device info page
- [ ] **MQTT Discovery:** Old sensor topics cleaned up after platform migration (sensor -> binary_sensor) -- verify no duplicate entities
- [ ] **MQTT Discovery:** `object_id` not used anywhere -- verify no deprecation warnings in HA 2025.10+ logs
- [ ] **Ingress:** Dashboard loads correctly via both direct URL and HA sidebar -- verify with browser DevTools network tab
- [ ] **Ingress:** WebSocket connects via Ingress (wss:// protocol) -- verify real-time updates work in HA sidebar
- [ ] **Ingress:** Static assets load with relative paths -- verify no 404s in browser console
- [ ] **Number Entities:** Command received on command_topic is validated before applying -- verify out-of-range values are rejected
- [ ] **Number Entities:** State published back to state_topic after command processing -- verify HA UI reflects actual state, not commanded state
- [ ] **Config Migration:** Wizard config values migrated to Supervisor options on first v1.2 startup -- verify with a fresh v1.1 -> v1.2 upgrade
- [ ] **Config Migration:** `ems_config.json` backed up and no longer read by application -- verify removal does not break startup
- [ ] **Translations:** Every option in `config.yaml` has an `en.yaml` entry -- verify no raw key names shown in HA add-on config page
- [ ] **Binary Sensors:** Online/offline entities use `binary_sensor` platform, not `sensor` -- verify device_class is `connectivity` or `running`
- [ ] **Services:** Service calls fail gracefully when hardware is offline -- verify from HA Developer Tools > Services

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| unique_id changed (Pitfall 1) | HIGH | Revert unique_id to old values. Users must manually re-add entities to dashboards if entity registry entries were auto-deleted. No automated recovery. |
| Duplicate entities from platform migration (Pitfall 2) | MEDIUM | Publish empty retained payloads to old discovery topics. Users may need to manually delete stale entities from HA entity registry. |
| Ingress broken (Pitfall 3) | LOW | Direct access still works. Fix base path configuration and rebuild frontend. No data loss. |
| paho thread crash (Pitfall 4) | MEDIUM | Restart the add-on. Implement health check + auto-reconnect. No data loss but commands lost during outage. |
| Wrong Number entity limits (Pitfall 5) | LOW-MEDIUM | Re-publish discovery with corrected min/max. Backend validation prevents hardware damage even if HA sends bad values. |
| Config migration failure (Pitfall 6) | HIGH | User must manually re-enter all settings in HA add-on config page. Provide a migration fallback that reads wizard config as backup. |
| Silent service call failure (Pitfall 7) | LOW | Add logging and error feedback. No data loss, just missed commands. |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| unique_id preservation (P1) | MQTT Discovery Overhaul | Diff old vs. new discovery payloads; verify unique_id unchanged for all 17 existing entities |
| Platform migration cleanup (P2) | MQTT Discovery Overhaul | After upgrade, verify HA device shows correct entity count (no duplicates) |
| Ingress path rewriting (P3) | Ingress Support | Test dashboard loads via both `http://host:8000` and HA sidebar Ingress URL |
| paho subscribe threading (P4) | Controllable Entities (Number/Select) | Broker restart test: kill Mosquitto, wait 30s, restart; verify entities recover within 60s |
| Number min/max validation (P5) | Controllable Entities (Number/Select) | Send out-of-range command on MQTT; verify backend rejects and publishes correct state |
| Config migration (P6) | Remove Setup Wizard (first phase) | Fresh v1.1 install with wizard config -> upgrade to v1.2 -> verify all settings preserved |
| Service error handling (P7) | HA Services | Call service with Huawei driver offline; verify HA shows error or entity shows unchanged state |
| Entity naming (UX) | MQTT Discovery Overhaul | Verify HA entity list shows clean names like "EMS Huawei Battery SOC" not "EMS Energy Management System Huawei Battery SOC" |
| `object_id` deprecation | MQTT Discovery Overhaul | Run HA 2025.10+ and check logs for zero deprecation warnings from EMS entities |
| `expire_after` + retained state conflict | MQTT Discovery Overhaul | Stop EMS, wait for `expire_after` timeout, verify entities show "unavailable" not stale value |

## Sources

- [HA MQTT Integration docs](https://www.home-assistant.io/integrations/mqtt/) -- discovery, availability, origin, entity_category, migrate_discovery
- [HA MQTT Number docs](https://www.home-assistant.io/integrations/number.mqtt/) -- command_topic, state_topic, min/max, mode
- [object_id deprecation (HA Core #153612)](https://github.com/home-assistant/core/issues/153612) -- deprecated 2025.10, removed 2026.4
- [object_id vs default_entity_id discussion](https://community.home-assistant.io/t/mqtt-object-id-vs-default-entity-id-warning/937665)
- [Ingress WebSocket support discussion](https://community.home-assistant.io/t/ingress-with-support-for-websocket/588542) -- WebSocket upgrade headers, proxy configuration
- [Ingress static asset issues](https://community.home-assistant.io/t/trouble-with-static-assets-in-custom-addon-with-ingress/712298) -- base path, relative URLs
- [X-Ingress-Path header usage](https://community.home-assistant.io/t/how-to-use-x-ingress-path-in-an-add-on/276905)
- [paho-mqtt thread crash on reconnect (GitHub #894)](https://github.com/eclipse-paho/paho.mqtt.python/issues/894) -- BrokenPipeError in on_connect subscribe
- [HA Supervisor Proxy and Ingress (DeepWiki)](https://deepwiki.com/home-assistant/supervisor/6.3-proxy-and-ingress)
- [expire_after vs availability_topic discussion](https://community.home-assistant.io/t/mqtt-discovery-msg-availability-vs-expire-after/788468)
- Direct codebase analysis: `backend/ha_mqtt_client.py`, `backend/evcc_mqtt_driver.py`, `backend/main.py`, `backend/config.py`, `backend/setup_config.py`, `ha-addon/config.yaml`, `ha-addon/run.sh`, `ha-addon/translations/en.yaml`, `frontend/vite.config.ts`

---
*Pitfalls research for: HA best practice alignment (v1.2 milestone)*
*Researched: 2026-03-23*
