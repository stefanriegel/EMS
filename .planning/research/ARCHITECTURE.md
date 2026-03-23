# Architecture Patterns

**Domain:** Home Assistant Best Practice Alignment for existing EMS Add-on
**Researched:** 2026-03-23
**Confidence:** MEDIUM-HIGH (HA MQTT discovery patterns well-documented; Ingress details fragmented across community posts)

## Current Architecture Snapshot

```
                    ┌─────────────────────────────────────┐
                    │          FastAPI (uvicorn)           │
                    │  ┌───────┐  ┌────────┐  ┌────────┐  │
                    │  │api.py │  │ws_mgr  │  │setup_  │  │
                    │  │routes │  │        │  │api.py  │  │
                    │  └───┬───┘  └───┬────┘  └────────┘  │
                    │      │          │                    │
                    │  ┌───┴──────────┴────┐              │
                    │  │   Coordinator     │              │
                    │  │  (5s control loop)│              │
                    │  └──┬──────┬────┬────┘              │
                    │     │      │    │                    │
                    │  ┌──┴──┐ ┌─┴──┐ │                   │
                    │  │Huaw │ │Vict│ │                   │
                    │  │Ctrl │ │Ctrl│ │                   │
                    │  └──┬──┘ └─┬──┘ │                   │
                    │     │      │    │                    │
                    │  ┌──┴──────┴──┐ │  ┌──────────────┐ │
                    │  │  Drivers   │ │  │ha_mqtt_client│ │
                    │  │(Modbus TCP)│ │  │(publish only)│ │
                    │  └────────────┘ │  └──────────────┘ │
                    │                 │                    │
                    │  ┌──────────────┴──────────┐        │
                    │  │ evcc_mqtt_driver         │        │
                    │  │ (subscribe + publish)    │        │
                    │  └─────────────────────────┘        │
                    └─────────────────────────────────────┘
                    │                                      │
           port 8000 (webui)                      Modbus TCP / MQTT
```

**Key observation:** `ha_mqtt_client.py` is publish-only (311 lines). `evcc_mqtt_driver.py` already implements the subscribe-with-paho-threading pattern that `ha_mqtt_client` needs to adopt. This is the reference implementation.

## Recommended Architecture Changes

### Change 1: MQTT Subscribe Loop for Command Topics

**What:** Extend `HomeAssistantMqttClient` to subscribe to command topics for number, select, and button entities.

**Current state:** The HA MQTT client only publishes. It has `_on_connect` and `_on_disconnect` callbacks but no `_on_message` handler and no `subscribe()` calls.

**Reference pattern:** `evcc_mqtt_driver.py` already solves this exact problem:
- Subscribes in `_on_connect` callback (paho thread-safe)
- Receives messages in `_on_message` callback (paho background thread)
- Crosses thread boundary via `loop.call_soon_threadsafe()` to update asyncio-side state
- Coordinator reads state each control cycle

**Integration approach:**

```
┌──────────────────────────────────────────────────────────────┐
│  HomeAssistantMqttClient (extended)                          │
│                                                              │
│  PUBLISH path (existing, unchanged):                         │
│    coordinator._step() → ha_mqtt.publish(state, extra)       │
│    → _ensure_discovery() → _publish_state()                  │
│                                                              │
│  SUBSCRIBE path (new):                                       │
│    _on_connect: subscribe to command topics                   │
│    _on_message: parse command, call_soon_threadsafe           │
│    → _handle_command() in asyncio thread                      │
│    → calls back into coordinator or updates pending_commands  │
│                                                              │
│  Command dispatch options:                                    │
│    A) Callback: ha_mqtt.set_command_handler(fn)               │
│       Coordinator provides fn during set_ha_mqtt_client()     │
│    B) Queue: ha_mqtt._pending_commands deque                  │
│       Coordinator drains queue each cycle                     │
│                                                              │
│  RECOMMENDED: Option A (callback) because:                   │
│    - Matches evcc_mqtt_driver pattern                         │
│    - Commands execute within one control cycle (5s max)       │
│    - No unbounded queue growth concern                        │
│    - Coordinator already calls ha_mqtt each cycle             │
└──────────────────────────────────────────────────────────────┘
```

**Threading model implications:**
- paho's `_on_message` runs in paho's background thread (same as EVCC driver)
- Must NOT call coordinator methods directly from paho thread
- Use `loop.call_soon_threadsafe()` to schedule command handling in asyncio thread
- The callback function provided by coordinator must be lightweight (set a flag/value, not do I/O)

**Command topics to subscribe:**
```
homeassistant/number/ems/{entity_id}/set     → min_soc, deadband, ramp_rate
homeassistant/select/ems/{entity_id}/set     → control_mode
homeassistant/button/ems/{entity_id}/set     → force_grid_charge, clear_schedule
```

**Concrete new code locations:**
- `ha_mqtt_client.py`: Add `_on_message`, `_command_callback`, subscribe logic in `_on_connect`
- `coordinator.py`: Add `_handle_ha_command(entity_id, payload)` method, wire via `set_ha_mqtt_client()`

### Change 2: MQTT Discovery Overhaul

**What:** Upgrade discovery payloads to include availability, expire_after, origin metadata, entity_category, and new entity platforms.

**Current state:** Discovery publishes only `sensor` entities. No availability topic. No expire_after. No origin metadata. Names use verbose format ("Huawei Battery SOC" instead of HA-convention short names).

**New discovery model:**

```python
# Current: all entities share one state topic, one discovery prefix
_ENTITIES: list[tuple[...]] = [...]  # flat list of sensors

# New: entity registry with platform-aware discovery
@dataclass
class EntityDefinition:
    platform: str          # "sensor", "binary_sensor", "number", "select", "button"
    entity_id: str         # "huawei_soc"
    name: str | None       # None = inherit from device_class (HA 2023.8+)
    device_class: str | None
    unit: str | None
    state_class: str | None
    entity_category: str | None  # "config" | "diagnostic" | None
    icon: str | None
    # Platform-specific
    options: list[str] | None    # select only
    min_val: float | None        # number only
    max_val: float | None        # number only
    step: float | None           # number only
    command_topic: str | None    # number/select/button only
    value_key: str               # JSON key in state payload
```

**Discovery payload additions (all platforms):**

```python
{
    # Existing fields...

    # NEW: availability (HIGH confidence — official docs)
    "availability": [
        {
            "topic": f"ems/{device_id}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        }
    ],

    # NEW: expire_after for sensors (HIGH confidence)
    "expire_after": 30,  # 6 cycles at 5s = 30s generous timeout

    # NEW: origin metadata (MEDIUM confidence — recommended but not required)
    "origin": {
        "name": "EMS Energy Management System",
        "sw_version": "1.2.0",
        "support_url": "https://github.com/stefanriegel/EMS",
    },

    # NEW: entity_category for config/diagnostic entities
    "entity_category": "config",  # for tuning knobs (deadband, ramp_rate)
}
```

**Availability topic publishing:**
- Publish `online` to `ems/{device_id}/availability` on connect
- Set MQTT Last Will and Testament (LWT) to publish `offline` on ungraceful disconnect
- Publish `offline` explicitly in `disconnect()` method

**New entity platforms and their discovery topics:**

| Platform | Discovery Topic | Entity Examples |
|----------|----------------|-----------------|
| `sensor` (existing) | `homeassistant/sensor/ems/{id}/config` | SoC, power, setpoints, roles |
| `binary_sensor` (new) | `homeassistant/binary_sensor/ems/{id}/config` | huawei_online, victron_online, grid_charge_active, export_active |
| `number` (new) | `homeassistant/number/ems/{id}/config` | min_soc_huawei, min_soc_victron, deadband_huawei, ramp_rate |
| `select` (new) | `homeassistant/select/ems/{id}/config` | control_mode |
| `button` (new) | `homeassistant/button/ems/{id}/config` | force_grid_charge, clear_schedule |

**Entity naming alignment:**
- Set `has_entity_name: true` in device payload (HA 2023.8+)
- Use short names: "Battery SoC" not "Huawei Battery SOC"
- Use `null` name where device_class provides the name
- Prefix device name provides context: "EMS Huawei Battery SoC"

### Change 3: Ingress Support

**What:** Enable HA sidebar access to the EMS dashboard via Ingress proxy.

**Current state:** Dashboard served on port 8000 via `webui: http://[HOST]:[PORT:8000]`. No Ingress support. Frontend uses absolute paths (`/api/ws/state`, `/api/setup/status`).

**Ingress mechanism (MEDIUM confidence -- community-sourced, not well-documented officially):**

The HA Supervisor proxies requests to the add-on via an internal path like `/api/hassio_ingress/{token}/`. The add-on receives:
- HTTP header: `X-Ingress-Path` containing the base path (e.g., `/api/hassio_ingress/abc123def/`)
- Requests with the path stripped (the add-on sees `/` not the full ingress path)
- WebSocket connections are proxied bidirectionally (confirmed in Supervisor source)
- Authentication is handled by HA (no need for EMS auth under Ingress)

**config.yaml changes:**

```yaml
# Existing
ports:
  "8000/tcp": 8000
webui: http://[HOST]:[PORT:8000]

# Add
ingress: true
ingress_port: 8000
ingress_entry: /
panel_icon: mdi:battery-charging
panel_title: EMS
```

**Backend changes (FastAPI):**

```python
# New middleware: IngressMiddleware
# Location: backend/ingress.py (new file, ~40 lines)

class IngressMiddleware:
    """Set ASGI root_path from X-Ingress-Path header.

    When accessed via HA Ingress, the Supervisor proxy sends
    X-Ingress-Path with the base URL prefix. FastAPI needs this
    as root_path so generated URLs (OpenAPI, redirects) are correct.

    When accessed directly on port 8000, the header is absent
    and root_path stays empty -- no behavior change.
    """
    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            ingress_path = headers.get(b"x-ingress-path", b"").decode()
            if ingress_path:
                scope["root_path"] = ingress_path.rstrip("/")
        await self.app(scope, receive, send)
```

**Frontend changes:**

The critical issue: the WS URL is hardcoded as `ws://${location.host}/api/ws/state`. Under Ingress, the frontend is served from a subpath. The fix:

```typescript
// Current (breaks under Ingress):
const WS_URL = `ws://${location.host}/api/ws/state`;

// Fixed (works both direct and Ingress):
const basePath = document.querySelector('base')?.getAttribute('href')?.replace(/\/$/, '') || '';
const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL = `${wsProto}//${location.host}${basePath}/api/ws/state`;
```

Additionally, Vite must be configured with a dynamic base:

```typescript
// vite.config.ts
export default defineConfig({
  base: './',   // relative paths for all assets
  // ... existing config
});
```

And the FastAPI static file mount must work under both direct and Ingress access. The `<base>` tag approach:
- Backend injects `<base href="X-Ingress-Path">` into index.html when the header is present
- Or: use `./` relative paths everywhere (simpler, recommended)

**Auth interaction under Ingress:**
- Ingress requests are already authenticated by HA
- The `AuthMiddleware` must detect Ingress requests (check for `X-Ingress-Path` header or source IP `172.30.32.2`) and skip JWT validation
- Direct port 8000 access continues to require JWT auth

### Change 4: Wizard Removal

**What:** Remove setup wizard code and simplify config loading. Add-on options page becomes sole config surface.

**Current state:**
- `setup_config.py` (166 lines): `EmsSetupConfig` dataclass, `load_setup_config()`, `save_setup_config()`
- `setup_api.py` (~200 lines): 3 endpoints under `/api/setup/`
- `main.py` lifespan: Loads wizard config, injects into env vars via `setdefault()`
- `App.tsx`: Checks `/api/setup/status` on mount, redirects to `/setup` if incomplete
- `frontend/src/pages/SetupWizard.tsx`: Full wizard UI

**Files to delete:**
- `backend/setup_config.py` -- entire file
- `backend/setup_api.py` -- entire file
- `frontend/src/pages/SetupWizard.tsx` -- entire file

**Files to modify:**

| File | Change |
|------|--------|
| `backend/main.py` | Remove `load_setup_config` import and all env-injection logic from lifespan. Remove `setup_router` include. Remove `setup_config_path` from app.state. |
| `ha-addon/run.sh` | Remove `EMS_CONFIG_PATH` export (line 127). All config comes from options.json to env vars. |
| `frontend/src/App.tsx` | Remove setup status check on mount. Remove `/setup` route. Remove `SetupWizard` import. |
| `backend/auth.py` | `ensure_jwt_secret()` continues to use `/config/` directory -- no wizard dependency. |

**Config loading simplification:**

```
BEFORE:
  run.sh reads options.json → env vars
  lifespan loads ems_config.json → env vars (setdefault)
  Supervisor discovery → env vars (setdefault)
  Config dataclasses read env vars

AFTER:
  run.sh reads options.json → env vars
  Supervisor discovery → env vars (setdefault)
  Config dataclasses read env vars
```

The `ems_config.json` layer is completely eliminated. One fewer config precedence level to reason about.

**Migration concern:** Users who configured via the wizard (v1.0-v1.1) need their settings preserved. Since `run.sh` already reads `options.json` and the wizard's `ems_config.json` is only loaded via `setdefault` (env vars win), the migration path is:
1. Document that users must copy their wizard settings to the Add-on options page
2. On first v1.2 startup, if `ems_config.json` exists, log a WARNING with the values that should be moved to options
3. Do NOT auto-migrate -- the options.json schema is the authoritative source

## Component Boundaries

| Component | Responsibility | Communicates With | Change Type |
|-----------|---------------|-------------------|-------------|
| `ha_mqtt_client.py` | MQTT discovery + state publish + command subscribe | Coordinator (callback), MQTT broker | **MODIFY** (major) |
| `coordinator.py` | Control loop, role assignment, command handling | Controllers, ha_mqtt, evcc_mqtt | **MODIFY** (add command handler) |
| `backend/ingress.py` | ASGI middleware for X-Ingress-Path | FastAPI app (middleware chain) | **NEW** |
| `backend/main.py` | Lifespan wiring, app factory | All services | **MODIFY** (remove wizard, add ingress middleware) |
| `ha-addon/config.yaml` | Add-on manifest | HA Supervisor | **MODIFY** (add ingress fields) |
| `frontend/src/App.tsx` | SPA root, routing | API endpoints | **MODIFY** (remove setup route, fix WS URL) |
| `frontend/vite.config.ts` | Build config | Vite bundler | **MODIFY** (base: './') |
| `backend/setup_api.py` | Setup wizard API | (removed) | **DELETE** |
| `backend/setup_config.py` | Wizard config persistence | (removed) | **DELETE** |
| `frontend/src/pages/SetupWizard.tsx` | Wizard UI | (removed) | **DELETE** |

## Data Flow Changes

### Current MQTT Data Flow (publish only)
```
Coordinator._step()
  → ha_mqtt.publish(state, extra_fields)
    → _ensure_discovery()  [once per connect]
    → _publish_state()     [every 5s cycle]
      → MQTT broker
        → HA MQTT integration
          → HA entity states
```

### New MQTT Data Flow (bidirectional)
```
OUTBOUND (unchanged):
  Coordinator._step()
    → ha_mqtt.publish(state, extra_fields)
      → MQTT broker → HA entities

INBOUND (new):
  HA automation / UI slider / service call
    → HA MQTT integration publishes to command_topic
      → MQTT broker
        → paho _on_message (background thread)
          → loop.call_soon_threadsafe(_handle_command)
            → asyncio thread: _command_callback(entity_id, value)
              → Coordinator._handle_ha_command(entity_id, value)
                → Updates config or triggers action
                → Next _step() cycle reflects the change

AVAILABILITY (new):
  On connect: publish "online" to ems/ems/availability
  LWT: broker publishes "offline" to ems/ems/availability on disconnect
  On graceful disconnect: publish "offline" before disconnect()
```

### Ingress Request Flow (new)
```
User clicks "EMS" in HA sidebar
  → HA frontend requests /api/hassio_ingress/{token}/
    → Supervisor validates session, proxies to add-on port 8000
      → IngressMiddleware reads X-Ingress-Path, sets root_path
        → FastAPI serves index.html (static files)
          → Browser loads React SPA with relative asset paths
            → SPA opens WebSocket: wss://ha-host/api/hassio_ingress/{token}/api/ws/state
              → Supervisor proxies WS bidirectionally to add-on
                → FastAPI WS handler receives it normally

Auth under Ingress:
  AuthMiddleware detects X-Ingress-Path header → skip JWT check
  (HA already authenticated the user via Ingress session)
```

## Patterns to Follow

### Pattern 1: paho Subscribe + Asyncio Callback
**What:** Subscribe to MQTT topics in paho's background thread, dispatch to asyncio event loop via callback.
**When:** Any MQTT subscription that needs to update coordinator state.
**Reference:** `evcc_mqtt_driver.py` lines 148-175 (exact pattern to replicate).

```python
# In _on_connect (paho thread -- safe to call client.subscribe):
def _on_connect(self, client, userdata, connect_flags, reason_code, properties):
    if reason_code != 0:
        return
    # Subscribe to all command topics
    for entity in self._command_entities:
        client.subscribe(entity.command_topic)
    # Existing: set connected flag
    if self._loop is not None:
        self._loop.call_soon_threadsafe(self._set_connected, True)

# In _on_message (paho thread -- cross boundary to asyncio):
def _on_message(self, client, userdata, message):
    if self._loop is not None and self._command_callback is not None:
        entity_id = self._parse_entity_from_topic(message.topic)
        payload = message.payload.decode()
        self._loop.call_soon_threadsafe(
            self._command_callback, entity_id, payload
        )
```

### Pattern 2: ASGI Middleware for Header Injection
**What:** Read proxy headers and modify ASGI scope before FastAPI processes the request.
**When:** Running behind a reverse proxy that adds path prefix headers.

```python
from starlette.types import ASGIApp, Receive, Scope, Send

class IngressMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            ingress_path = headers.get(b"x-ingress-path", b"").decode()
            if ingress_path:
                scope["root_path"] = ingress_path.rstrip("/")
        await self.app(scope, receive, send)
```

### Pattern 3: Entity Registry with Platform Dispatch
**What:** Define all HA entities in a single registry, dispatch discovery payloads by platform type.
**When:** Publishing MQTT discovery for mixed entity platforms.

```python
# Single source of truth for all entity definitions
_ENTITY_REGISTRY: list[EntityDefinition] = [
    EntityDefinition(platform="sensor", entity_id="huawei_soc", ...),
    EntityDefinition(platform="binary_sensor", entity_id="huawei_online", ...),
    EntityDefinition(platform="number", entity_id="min_soc_huawei", ...),
    EntityDefinition(platform="select", entity_id="control_mode", ...),
    EntityDefinition(platform="button", entity_id="force_grid_charge", ...),
]

def _discovery_topic(self, entity: EntityDefinition) -> str:
    return f"homeassistant/{entity.platform}/{self._device_id}/{entity.entity_id}/config"
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Calling Coordinator from paho Thread
**What:** Directly calling `coordinator.set_min_soc()` inside `_on_message`.
**Why bad:** paho callbacks run in a background thread. The coordinator is not thread-safe. Concurrent writes to coordinator state from paho thread + asyncio thread = data races.
**Instead:** Always cross the thread boundary via `loop.call_soon_threadsafe()` with a lightweight callback that sets a flag or enqueues a value.

### Anti-Pattern 2: Hardcoded Absolute Paths in Frontend
**What:** Using `fetch("/api/state")` or `new WebSocket("ws://host/api/ws/state")`.
**Why bad:** Breaks under Ingress where the app is served from a subpath.
**Instead:** Use relative fetch paths (`fetch("api/state")` with base tag) or construct URLs from `window.location` + detected base path.

### Anti-Pattern 3: Separate State Topics per Entity
**What:** Publishing each entity to its own MQTT state topic.
**Why bad:** 17+ entities x 5s = 85+ MQTT publishes per cycle. Adds broker load, complicates discovery payloads, increases code complexity.
**Instead:** Keep the existing single shared state topic with `value_template` extraction. This is the standard pattern used by devices like Zigbee2MQTT.

### Anti-Pattern 4: Blocking Supervisor API Calls in paho Thread
**What:** Making HTTP calls to the Supervisor from within MQTT callbacks.
**Why bad:** paho callbacks must return quickly. HTTP calls can block.
**Instead:** Schedule any I/O work via `call_soon_threadsafe()` to the asyncio event loop.

## Suggested Build Order

Based on dependency analysis, the recommended implementation order:

```
Phase 1: Wizard Removal + Config Simplification
  |-- No dependencies on other changes
  |-- Reduces code complexity before adding new features
  |-- Removes dead code paths from main.py lifespan
  +-- Unblocks: frontend routing cleanup for Ingress

Phase 2: MQTT Discovery Overhaul
  |-- Depends on: nothing (extends existing ha_mqtt_client)
  |-- Adds: availability, expire_after, origin, entity_category
  |-- Adds: binary_sensor entities (online/offline, grid_charge_active)
  |-- Refactors: _ENTITIES list -> EntityDefinition registry
  +-- Unblocks: Phase 3 (command topics need registry)

Phase 3: MQTT Command Subscription (Services + Number/Select/Button)
  |-- Depends on: Phase 2 (entity registry with command_topic)
  |-- Adds: _on_message handler, subscribe in _on_connect
  |-- Adds: command callback from coordinator
  |-- Adds: number entities (min_soc, deadband, ramp_rate)
  |-- Adds: select entities (control_mode)
  |-- Adds: button entities (force_grid_charge)
  +-- Unblocks: HA automations can control EMS

Phase 4: Ingress Support
  |-- Depends on: Phase 1 (wizard routes removed, routing simplified)
  |-- Adds: IngressMiddleware (new file)
  |-- Modifies: config.yaml (ingress fields)
  |-- Modifies: frontend base URL / WS URL construction
  |-- Modifies: AuthMiddleware (skip JWT under Ingress)
  |-- Modifies: vite.config.ts (base: './')
  +-- Unblocks: HA sidebar access

Phase 5: Entity Naming + Translations
  |-- Depends on: Phase 2 (entity registry in place)
  |-- Adds: ha-addon/translations/en.yaml
  |-- Modifies: entity names to HA conventions
  +-- Polish / non-functional
```

## Scalability Considerations

| Concern | Current (v1.1) | After v1.2 |
|---------|----------------|------------|
| MQTT messages/cycle | 17 sensor state publishes | 17 sensors + availability heartbeat + ~30 entities total (still one state topic) |
| MQTT subscriptions | 0 (publish-only) | ~10 command topics (number + select + button) |
| Discovery payloads | 17 retained configs | ~30 retained configs (still one-time on connect) |
| Frontend bundle size | No change expected | Minimal (remove wizard, add base path detection) |
| Config surface | 3 layers (env, wizard, supervisor) | 2 layers (env from options.json, supervisor) |

## Sources

- [HA MQTT Discovery docs](https://www.home-assistant.io/integrations/mqtt/) -- HIGH confidence for discovery format, availability, expire_after, origin
- [HA MQTT Number entity](https://www.home-assistant.io/integrations/number.mqtt/) -- HIGH confidence for number entity config
- [HA MQTT Select entity](https://www.home-assistant.io/integrations/select.mqtt/) -- HIGH confidence for select entity config
- [HA MQTT Button entity](https://www.home-assistant.io/integrations/button.mqtt/) -- HIGH confidence for button entity config
- [HA MQTT Binary Sensor](https://www.home-assistant.io/integrations/binary_sensor.mqtt/) -- HIGH confidence for binary_sensor config
- [FastAPI Behind a Proxy](https://fastapi.tiangolo.com/advanced/behind-a-proxy/) -- HIGH confidence for root_path mechanism
- [HA Supervisor Proxy and Ingress](https://deepwiki.com/home-assistant/supervisor/6.3-proxy-and-ingress) -- MEDIUM confidence for Ingress proxy internals
- [HA Community: Ingress with Python](https://community.home-assistant.io/t/addon-ingress/936226) -- MEDIUM confidence for FastAPI+Ingress gotchas
- [HA Community: X-Ingress-Path](https://community.home-assistant.io/t/how-to-use-x-ingress-path-in-an-add-on/276905) -- MEDIUM confidence for header format
- Existing `evcc_mqtt_driver.py` in codebase -- HIGH confidence for paho threading pattern
