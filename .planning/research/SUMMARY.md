# Project Research Summary

**Project:** EMS v1.2 — Home Assistant Best Practice Alignment
**Domain:** HA Add-on MQTT integration — entity model, controllability, ingress, config simplification
**Researched:** 2026-03-23
**Confidence:** HIGH

## Executive Summary

EMS v1.2 is a polish-and-alignment milestone, not a feature milestone. The goal is to make the existing dual-battery EMS behave as a first-class Home Assistant citizen: proper MQTT discovery payloads, bidirectional entity control, Ingress dashboard access, and wizard removal. Mature integrations like EMS-ESP32, Zigbee2MQTT, and Tasmota define the pattern — and that pattern is well-documented in official HA developer docs. Zero new dependencies are required; every change is configuration or code-level against libraries already in the project.

The recommended approach is a strictly ordered four-phase delivery. The first priority is wizard removal and config migration, because it simplifies the startup code before anything else changes and avoids a critical data-loss risk for existing users. MQTT discovery cleanup follows immediately, targeting the "janky integration" perception with low-risk additions (availability, origin, entity_category, binary sensors). Controllable entities (number, select, button platforms) come third because they require the new MQTT subscribe infrastructure that the discovery overhaul prepares. HA Ingress support is the most isolated change and slots in as Phase 4, dependent only on Phase 1's routing cleanup.

The two non-obvious risks that must be managed carefully are: (1) `unique_id` preservation — changing even one `unique_id` value silently destroys every user dashboard, automation, and history reference pointing at that entity; and (2) the paho-mqtt background thread crash on reconnect when `subscribe()` is called from `_on_connect` — a known upstream bug that requires defensive wrapping and a periodic health check. Both risks have clear prevention strategies. The build order respects all inter-phase dependencies identified across the four research files.

## Key Findings

### Recommended Stack

No version bumps and no new dependencies. The current stack already provides everything needed: paho-mqtt 2.1 handles both publish and subscribe (the `evcc_mqtt_driver.py` in the codebase is the exact reference implementation to copy), FastAPI handles the Ingress path prefix via the ASGI `root_path` mechanism, uvicorn serves behind the Supervisor proxy with `--proxy-headers`, and a single Vite `base: './'` config change fixes all static asset paths.

**Core technologies and their required changes:**
- **paho-mqtt 2.1**: Add `on_message` callback and `subscribe()` in `_on_connect` — code change only, no version bump
- **FastAPI**: Add thin ASGI `IngressMiddleware` (~40 lines) reading `X-Ingress-Path` and setting `scope["root_path"]` — no new library
- **uvicorn[standard]**: Already supports `--proxy-headers` — no change needed
- **Vite 8.0.1**: Set `base: './'` in `vite.config.ts` for relative asset paths — single config change

### Expected Features

**Must have (table stakes) — fixes "janky integration" perception:**
- Origin metadata on all MQTT discovery payloads — HA logs "unknown origin" without it; required since HA 2023.x
- Availability topics with LWT — entities show stale values forever instead of "unavailable" without it
- `expire_after: 120` on sensor entities — safety net if EMS crashes silently
- `has_entity_name: True` with short names — entity IDs are ugly and device grouping breaks without it
- `entity_category` tagging (diagnostic/config/none) — flat entity list is unusable without separation
- `device_class` and `state_class` audit — required for HA long-term statistics and energy dashboard
- Binary sensors for `huawei_online`, `victron_online`, `grid_charge_active`, `export_active` — boolean values are wrong on sensor platform
- `configuration_url` in device info — points users to the EMS dashboard from the HA device page
- Add-on `translations/en.yaml` — config options show raw key names (`huawei_deadband_w`) without it
- Wizard removal — Add-on options page becomes sole config surface

**Should have (differentiators) — elevate from "works" to "excellent HA citizen":**
- Number entities for tunable parameters (`min_soc`, `deadband`, `ramp_rate`) — HA automations can adjust seasonally
- Select entity for control mode (AUTO/HOLD/GRID_CHARGE/DISCHARGE_LOCKED) — users pick mode from HA UI
- Button entities for force actions (`force_grid_charge`, `reset_to_auto`) — one-tap control without opening the dashboard
- HA Ingress support — dashboard accessible from HA sidebar without separate port/URL
- Two HA devices (EMS Huawei + EMS Victron + EMS System) instead of one — matches physical reality

**Defer to v2+:**
- Custom HA integration (Python component) — MQTT discovery provides 90% of the value at 5% of the complexity
- MQTT device triggers — wrong semantic model for a continuous-state EMS
- Climate entity — wrong platform for battery management
- Diagnostic uptime/cycle-duration sensors — polish, not blocking
- 50+ granular entities (one per register) — entity sprawl harms usability

### Architecture Approach

All changes are modifications to existing components plus one new 40-line file (`backend/ingress.py`) and three file deletions (`setup_config.py`, `setup_api.py`, `SetupWizard.tsx`). The MQTT publish path is unchanged; a new subscribe path is added alongside it. The `evcc_mqtt_driver.py` paho threading pattern (subscribe in `_on_connect`, cross thread boundary via `call_soon_threadsafe`) is the exact reference to replicate in `ha_mqtt_client.py`. The entity registry refactors from a flat `_ENTITIES` tuple to a typed `EntityDefinition` dataclass list that dispatches discovery by platform.

**Major components and change type:**
1. `ha_mqtt_client.py` — MODIFY (major): add subscribe path, availability publishing, entity registry refactor to `EntityDefinition` dataclass
2. `coordinator.py` — MODIFY (minor): add `_handle_ha_command()` callback wired via `set_ha_mqtt_client()`
3. `backend/ingress.py` — NEW: ASGI middleware reading `X-Ingress-Path` and setting `scope["root_path"]`
4. `backend/main.py` — MODIFY: remove wizard wiring, add ingress middleware to app
5. `frontend/src/App.tsx` — MODIFY: remove setup route, fix WebSocket URL construction
6. `frontend/vite.config.ts` — MODIFY: set `base: './'`
7. `backend/setup_api.py`, `setup_config.py`, `frontend/src/pages/SetupWizard.tsx` — DELETE

### Critical Pitfalls

1. **`unique_id` change destroys user dashboards** — Never rename existing `unique_id` values (`ems_huawei_soc` etc.). New entities follow the same `ems_{entity_id}` pattern. Use `default_entity_id` (not deprecated `object_id`, removed in HA 2026.4) for entity ID control. Define migration plan before writing any discovery code.

2. **Platform migration leaves ghost entities** — Moving `huawei_online` from `sensor` to `binary_sensor` requires publishing an empty retained payload to the old `homeassistant/sensor/ems/huawei_online/config` topic before publishing the new `homeassistant/binary_sensor/...` discovery. Implement a one-time migration function that runs on first v1.2 startup.

3. **Ingress breaks SPA routing, WebSocket, and API calls** — The frontend uses absolute paths today. Fix: Vite `base: './'`, WebSocket URL constructed from `window.location` using `new URL('./api/ws/state', window.location.href)`, API calls use relative paths. Must be tested with both direct port access and Ingress URL simultaneously.

4. **paho subscribe threading — silent background thread crash** — Known upstream bug (`paho#894`): calling `subscribe()` in `_on_connect` during reconnect can crash paho's background thread while `_connected` stays `True`. Prevention: wrap subscribe in try/except for `BrokenPipeError`/`OSError`, add periodic health check (no successful publish in N cycles → force reconnect). Consider split publish/subscribe paho clients as the safest pattern.

5. **Config migration data loss** — Existing users configured via the wizard have settings in `ems_config.json` that are absent from `options.json`. On v1.2 upgrade these silently revert to defaults and the add-on enters degraded mode. Prevention: idempotent one-time migration script that reads `ems_config.json` and writes values to Supervisor options via `POST /addons/self/options` (read-merge-write, not replace). Must execute before main application startup.

## Implications for Roadmap

Based on combined research, the suggested phase structure follows the dependency graph from ARCHITECTURE.md exactly.

### Phase 1: Wizard Removal and Config Migration

**Rationale:** Config migration (Pitfall 6) has the highest recovery cost of any pitfall (rated HIGH — user must manually re-enter all settings). It must run before any other change to avoid data loss on upgrade. Removing the wizard also simplifies `main.py` lifespan from three config layers to two, making subsequent phases cleaner. No other phase depends on the wizard being present; Phases 3 and 4 (Ingress) depend on it being gone.
**Delivers:** Simplified startup, `ems_config.json` eliminated, `setup_api.py` / `setup_config.py` / `SetupWizard.tsx` deleted, one-time idempotent migration script, frontend routing cleaned up.
**Addresses:** Wizard anti-feature (FEATURES.md), config migration pitfall (PITFALLS.md P6).
**Avoids:** Users losing all configured settings on upgrade; setup wizard rendering under Ingress.

### Phase 2: MQTT Discovery Overhaul

**Rationale:** All table-stakes items (origin, availability, expire_after, has_entity_name, entity_category, binary sensors, translations) require no MQTT subscribe infrastructure and have no inter-dependencies. Grouping them delivers the "it no longer feels janky" result in a single shippable phase. The `EntityDefinition` registry refactor is the foundation Phase 3 depends on — the `command_topic` field lives there.
**Delivers:** ~30 discovery entities across sensor/binary_sensor platforms, availability topic with LWT, origin metadata, `expire_after: 120`, proper entity naming with `has_entity_name`, `entity_category` tagging, `translations/en.yaml`, platform migration cleanup (empty retained payloads to old sensor topics before new binary_sensor publication).
**Addresses:** All 10 table-stakes features in FEATURES.md. Pitfalls P1 (unique_id preservation), P2 (ghost entities from platform migration).
**Uses:** paho-mqtt publish path only — no subscribe infrastructure yet. Entity registry pattern (ARCHITECTURE.md Pattern 3).

### Phase 3: Controllable Entities (Number, Select, Button)

**Rationale:** Depends on Phase 2's `EntityDefinition` registry (command_topic field). Adds the MQTT subscribe infrastructure — the most architecturally significant addition in this milestone. Number/select/button entities all share the same subscribe path, so implementing them together is efficient. The paho threading pitfall (P4) must be solved here.
**Delivers:** MQTT subscribe loop in `ha_mqtt_client.py`, `_handle_ha_command()` in coordinator, number entities for min_soc/deadband/ramp_rate (with hardware limit validation), select entity for control mode, button entities for force actions, state echo after command processing.
**Addresses:** All three differentiator controllable entity features (FEATURES.md). Pitfalls P4 (paho threading), P5 (Number min/max vs hardware limits), P7 (silent service call failure).
**Uses:** paho subscribe pattern from existing `evcc_mqtt_driver.py` (ARCHITECTURE.md Pattern 1). QoS 1 for command subscriptions.

### Phase 4: HA Ingress Support

**Rationale:** Independent of MQTT work (no shared code paths with Phases 2-3). Depends on Phase 1 (wizard routes removed, frontend routing simplified). Can be developed in parallel with Phase 3 if capacity allows. Isolated failure domain — if Ingress breaks, direct port 8000 access is unaffected; no user data is at risk.
**Delivers:** `ingress: true` / `ingress_port` / `panel_icon` / `panel_title` in `config.yaml`, `backend/ingress.py` (IngressMiddleware, ~40 lines), Vite `base: './'`, dynamic WebSocket URL construction from `window.location`, auth bypass for Ingress requests (detect `X-Ingress-Path` header or Supervisor IP `172.30.32.2`).
**Addresses:** Ingress differentiator (FEATURES.md). Pitfall P3 (broken SPA routing, WebSocket, and API calls under Ingress).
**Uses:** ASGI middleware pattern (ARCHITECTURE.md Pattern 2). FastAPI `root_path` mechanism.

### Phase Ordering Rationale

- **Phase 1 must be first** because config migration data loss (P6) has the highest recovery cost and affects every user upgrading from v1.1. Wizard deletion also unblocks Ingress routing.
- **Phase 2 before Phase 3** because Phase 3 needs the `EntityDefinition` registry with `command_topic` field that Phase 2 introduces. Subscribe topics are cleaner to add when all entity definitions are already typed.
- **Phase 4 after Phase 1, independent of Phases 2-3** — can be parallelized or deferred. No MQTT code paths are shared with the Ingress changes.
- **Each phase is independently shippable** — the add-on remains fully functional and safe at every phase boundary.

### Research Flags

Phases with well-documented patterns (skip research-phase):
- **Phase 1 (Wizard Removal):** Straightforward deletion plus migration. Supervisor API options write pattern is simple read-merge-write. No architectural unknowns.
- **Phase 2 (MQTT Discovery Overhaul):** All entity platforms and discovery fields are comprehensively documented in official HA MQTT docs. Zigbee2MQTT and EMS-ESP32 serve as real-world reference implementations.
- **Phase 3 (Controllable Entities):** paho subscribe pattern is already implemented in `evcc_mqtt_driver.py`. The threading pitfall (P4) has a documented prevention strategy. Copy-and-adapt work.

Phases likely needing deeper research or exploratory testing during planning:
- **Phase 4 (HA Ingress):** Ingress documentation is fragmented across community posts and Supervisor source code. The `X-Ingress-Path` behavior, WebSocket proxying specifics, and the auth header trust model are MEDIUM-confidence findings. Plan for exploratory testing with a real HA install before committing to the full implementation. If WS proxying does not work as expected, the fallback is the existing HTTP polling path already in the frontend.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Zero new dependencies. All changes use existing libraries at existing versions. HA MQTT docs are authoritative. |
| Features | HIGH | Table-stakes features verified against official HA MQTT platform docs for each entity type. Differentiator features follow patterns from Zigbee2MQTT and EMS-ESP32. |
| Architecture | MEDIUM-HIGH | MQTT changes and wizard removal are HIGH confidence (based on codebase analysis + official docs). Ingress proxy internals are MEDIUM — community sources and Supervisor source, not official add-on developer docs. |
| Pitfalls | HIGH | Critical pitfalls sourced from official HA docs, a known upstream paho bug (GitHub #894), and direct codebase analysis. Recovery costs reflect real user impact. |

**Overall confidence:** HIGH

### Gaps to Address

- **Ingress WebSocket proxying under wss://**: The exact behavior of the Supervisor proxy when upgrading a WebSocket connection is documented in community posts but not in official developer docs. Validate with a real WS connection through Ingress before implementing the URL fix. The HTTP polling fallback in `useEmsState.ts` is the safety net if WS proxying proves unreliable.
- **Supervisor API options write format**: `POST /addons/self/options` replaces ALL options (not a partial patch). The migration script must read current options, merge wizard values, then write the full merged object back. Verify the exact JSON schema expected by the Supervisor API before implementing.
- **Two HA devices vs one**: FEATURES.md lists restructuring into Huawei/Victron/System devices as a differentiator. This changes `device` identifiers in discovery payloads and could interact with Pitfall 1 (unique_id). Explicitly scope this as in-Phase-2 or deferred before writing any discovery code — do not discover mid-implementation that it conflicts with entity ID preservation.

## Sources

### Primary (HIGH confidence)
- [HA MQTT Integration docs](https://www.home-assistant.io/integrations/mqtt/) — discovery payload structure, availability, origin, entity_category, migrate_discovery
- [HA MQTT Sensor / Binary Sensor / Number / Select / Switch / Button platform docs](https://www.home-assistant.io/integrations/) — all entity platform fields verified
- [HA Add-on Configuration docs](https://developers.home-assistant.io/docs/apps/configuration/) — ingress_port, ingress_entry, translations format
- [HA Add-on Presentation / Ingress docs](https://developers.home-assistant.io/docs/apps/presentation/) — ingress config, X-Ingress-Path, port and IP restriction
- [FastAPI Behind a Proxy docs](https://fastapi.tiangolo.com/advanced/behind-a-proxy/) — root_path mechanism
- [paho-mqtt PyPI / migrations docs](https://eclipse.dev/paho/files/paho.mqtt.python/html/migrations.html) — CallbackAPIVersion.VERSION2, on_message signature
- Existing `evcc_mqtt_driver.py` in codebase — paho subscribe/threading reference implementation (HIGH — code-verified)
- [object_id deprecation HA Core #153612](https://github.com/home-assistant/core/issues/153612) — deprecated 2025.10, removed 2026.4

### Secondary (MEDIUM confidence)
- [HA Supervisor Proxy and Ingress (DeepWiki)](https://deepwiki.com/home-assistant/supervisor/6.3-proxy-and-ingress) — Ingress proxy internals
- [HA Community: X-Ingress-Path header usage](https://community.home-assistant.io/t/how-to-use-x-ingress-path-in-an-add-on/276905) — header format and add-on behavior
- [HA Community: Ingress static asset issues](https://community.home-assistant.io/t/trouble-with-static-assets-in-custom-addon-with-ingress/712298) — base path, relative URLs
- [HA Community: expire_after vs availability_topic](https://community.home-assistant.io/t/mqtt-discovery-msg-availability-vs-expire-after/788468) — combined usage guidance
- [EMS-ESP32 HA Integration docs](https://docs.emsesp.org/Home-Assistant/) — real-world reference implementation for MQTT discovery best practices
- [Zigbee2MQTT HA Integration](https://www.zigbee2mqtt.io/guide/usage/integrations/home_assistant.html) — naming convention reference

### Tertiary (MEDIUM-LOW confidence)
- [paho-mqtt thread crash on reconnect GitHub #894](https://github.com/eclipse-paho/paho.mqtt.python/issues/894) — BrokenPipeError in on_connect subscribe; needs validation against current paho 2.1 behavior specifically
- [HA Community: Ingress with WebSocket support](https://community.home-assistant.io/t/ingress-with-support-for-websocket/588542) — WS proxying under Ingress; verify in real HA environment before relying on it

---
*Research completed: 2026-03-23*
*Ready for roadmap: yes*
