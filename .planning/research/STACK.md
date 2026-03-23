# Technology Stack

**Project:** EMS v1.2 -- Home Assistant Best Practice Alignment
**Researched:** 2026-03-23

## Recommended Stack Changes

This milestone requires **zero new dependencies**. All changes are configuration/code-level changes to existing libraries. This is the correct outcome -- adding dependencies for HA protocol compliance would be a smell.

### Existing Dependencies (No Version Changes Needed)

| Technology | Current Version | Purpose in v1.2 | Change Needed |
|------------|----------------|------------------|---------------|
| paho-mqtt | >=2.1 (pinned) | MQTT discovery overhaul + command topic subscriptions | **Code change only**: add `on_message` callback and `subscribe()` calls. No version bump. |
| FastAPI | latest | Ingress path handling via `root_path` | **Code change only**: read `X-Ingress-Path` header, pass to `root_path`. |
| uvicorn[standard] | latest | Serve behind HA Ingress reverse proxy | **No change**: already supports `--proxy-headers`. |
| Vite | 8.0.1 (frontend) | Build SPA with dynamic base path for Ingress | **Config change only**: set `base` dynamically or use relative paths. |

### No New Dependencies

| Considered | Why NOT Needed |
|------------|---------------|
| aiomqtt / asyncio-mqtt | paho-mqtt 2.1 already works. The existing `EvccMqttDriver` proves the subscribe pattern works fine with paho's background thread + `call_soon_threadsafe`. Adding asyncio-mqtt would create two MQTT client patterns in the codebase. |
| nginx / reverse proxy | HA Ingress proxies directly to port 8099. FastAPI + uvicorn handle this natively. |
| PyYAML | Translations are YAML files in `ha-addon/translations/` -- edited by hand, not parsed by Python code. HA Supervisor reads them directly. |
| Starlette middleware for path rewriting | FastAPI's built-in `root_path` mechanism handles Ingress path prefixing. A middleware would over-engineer this. |

## Detailed Stack Analysis by Feature

### 1. MQTT Discovery Overhaul

**Library:** paho-mqtt 2.1.0 (current: `>=2.1` in pyproject.toml -- already correct)
**Confidence:** HIGH (verified against official HA MQTT docs + existing codebase pattern)

**What changes in the discovery payload (no library changes):**

```python
# Current minimal discovery payload:
{
    "name": "Huawei Battery SOC",
    "unique_id": "ems_huawei_soc",
    "state_topic": "homeassistant/sensor/ems/state",
    "value_template": "{{ value_json.huawei_soc_pct }}",
    "device": {"identifiers": ["ems"], "name": "Energy Management System", "manufacturer": "EMS"},
}

# v1.2 best-practice payload adds:
{
    # ... existing fields ...
    "availability": [
        {"topic": "ems/availability", "payload_available": "online", "payload_not_available": "offline"},
        {"topic": "homeassistant/status", "payload_available": "online", "payload_not_available": "offline"},
    ],
    "availability_mode": "all",
    "origin": {
        "name": "EMS Energy Management System",
        "sw_version": "1.2.0",
        "support_url": "https://github.com/stefanriegel/EMS",
    },
    "entity_category": "diagnostic",  # for internal metrics, not for primary entities
    "expire_after": 120,  # seconds -- 2x the control loop interval (60s cycle * 2)
    "device": {
        "identifiers": ["ems"],
        "name": "EMS",
        "manufacturer": "Stefan Riegel",
        "model": "EMS v2",
        "sw_version": "1.2.0",
    },
}
```

**Key decisions:**
- `availability_mode: "all"` requires both the EMS availability topic AND the HA birth message to be online. This is the correct mode -- if HA restarts, entities should briefly go unavailable until both sides confirm.
- `expire_after: 120` (2 minutes) -- the control loop runs every 5s, so 120s is generous enough to survive brief stalls without false unavailability. Do NOT use `expire_after` on entities whose state is published with `retain=True` (HA docs explicitly warn about this causing stale-state-on-restart).
- `entity_category: "diagnostic"` for internal tuning parameters (dead-band, ramp rate). Primary battery entities (SoC, power, role) should NOT have entity_category set.
- `origin` is required for device-based discovery and recommended for component discovery. Include it on all entities.

### 2. MQTT Command Topics (Subscribe Capability)

**Library:** paho-mqtt 2.1.0 (no change)
**Confidence:** HIGH (existing `EvccMqttDriver` proves the pattern)

The current `HomeAssistantMqttClient` is publish-only. For number/select/switch/button entities, it must also subscribe to command topics and dispatch incoming values.

**paho-mqtt subscribe pattern already proven in codebase:**

```python
# From evcc_mqtt_driver.py -- same pattern applies:
self._client.on_message = self._on_message
# In _on_connect:
client.subscribe("ems/+/set")  # wildcard for all command topics

# Callback (runs in paho background thread):
def _on_message(self, client, userdata, msg):
    # Bridge to asyncio via call_soon_threadsafe
    if self._loop is not None:
        self._loop.call_soon_threadsafe(self._handle_command, msg.topic, msg.payload)
```

**No API changes in paho-mqtt 2.1 for `on_message`** -- the callback signature is `def on_message(client, userdata, msg)` regardless of `CallbackAPIVersion.VERSION2`. The VERSION2 changes only affect `on_connect` and `on_disconnect` signatures (which the codebase already uses correctly).

**Command topic convention for HA MQTT entities:**

| Entity Type | Discovery Topic | Command Topic | State Topic |
|-------------|----------------|---------------|-------------|
| number | `homeassistant/number/ems/{id}/config` | `ems/number/{id}/set` | `ems/number/{id}/state` |
| select | `homeassistant/select/ems/{id}/config` | `ems/select/{id}/set` | `ems/select/{id}/state` |
| switch | `homeassistant/switch/ems/{id}/config` | `ems/switch/{id}/set` | `ems/switch/{id}/state` |
| button | `homeassistant/button/ems/{id}/config` | `ems/button/{id}/set` | N/A (stateless) |
| binary_sensor | `homeassistant/binary_sensor/ems/{id}/config` | N/A (read-only) | `ems/binary_sensor/{id}/state` |
| sensor | `homeassistant/sensor/ems/{id}/config` | N/A (read-only) | `ems/sensor/{id}/state` |

**Critical: per-entity state topics vs shared state topic.** The current implementation uses a single shared state topic (`homeassistant/sensor/ems/state`) with `value_template` to extract per-entity values. This works for sensors (read-only) but controllable entities (number, select, switch) MUST have their own `command_topic` and SHOULD have their own `state_topic`. Recommendation: keep the shared JSON state topic for sensors, use individual topics for controllable entities.

### 3. New Entity Types for v1.2

**No new libraries needed. Discovery payloads only.**

| HA Entity Type | Use Case | Key Fields |
|---------------|----------|------------|
| `binary_sensor` | Huawei online, Victron online, grid charge active, export active | `device_class: "connectivity"` or `"running"`, `payload_on: "ON"`, `payload_off: "OFF"` |
| `number` | Min SoC (Huawei/Victron), dead-band, ramp rate | `min`, `max`, `step`, `mode: "slider"` or `"box"`, `entity_category: "config"` |
| `select` | Control mode (IDLE/DISCHARGE/CHARGE/HOLD) | `options: [...]`, `entity_category: "config"` |
| `switch` | Force grid charge on/off | `payload_on: "ON"`, `payload_off: "OFF"` |
| `button` | Force schedule recompute | `device_class: "restart"`, `payload_press: "PRESS"` |

**entity_category usage:**
- `"config"` -- for number/select entities that tune runtime behavior (min SoC, dead-bands). These appear under the device's "Configuration" section in HA.
- `"diagnostic"` -- for sensors showing internal state (setpoints, roles). These appear under "Diagnostic".
- Omitted (None) -- for primary user-facing entities (SoC, power, online status). These appear in the main entity list.

### 4. HA Ingress Support

**Library changes:** None. FastAPI + uvicorn handle this natively.
**Confidence:** HIGH (verified against HA developer docs + FastAPI docs)

**config.yaml additions:**

```yaml
ingress: true
ingress_port: 8000   # Match existing uvicorn port
ingress_entry: /
```

**Backend changes (FastAPI):**

FastAPI's `root_path` mechanism handles the Ingress path prefix. The HA Supervisor proxy passes `X-Ingress-Path` as a header. Two approaches:

**Option A (recommended): ASGI middleware to set root_path from header**

```python
class IngressMiddleware:
    """Read X-Ingress-Path header and set ASGI root_path."""
    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            ingress_path = headers.get(b"x-ingress-path", b"").decode()
            if ingress_path:
                scope["root_path"] = ingress_path
        await self.app(scope, receive, send)
```

**Option B: uvicorn `--root-path` flag** -- only works for static path, but Ingress path is dynamic per installation.

Option A is correct because the Ingress path varies per HA installation (it includes the add-on slug in the path).

**Frontend changes (Vite/React):**

The SPA must use relative paths for all assets and API calls. Currently:
- API calls use relative paths like `/api/state` -- these work behind Ingress because the browser resolves them relative to the current origin.
- Static assets: Vite's default `base: '/'` generates absolute paths (`/assets/index-xxx.js`). This breaks behind Ingress.

**Fix:** Set Vite `base: './'` (relative) in production build, or inject the base path at build time. Relative paths (`./assets/...`) work universally.

```typescript
// vite.config.ts
export default defineConfig({
  base: './',  // Use relative paths for all assets
  // ... existing config
})
```

**WebSocket path:** The current WebSocket connects to `/api/ws/state`. Behind Ingress, this path is rewritten by the Supervisor proxy. As long as the frontend uses a relative WebSocket URL (constructed from `window.location`), this works automatically.

**Authentication:** HA Ingress handles authentication before forwarding. The `X-Remote-User-ID`, `X-Remote-User-Name`, and `X-Remote-User-Display-Name` headers are passed to the add-on. The EMS auth middleware should detect Ingress requests (presence of `X-Ingress-Path` header or source IP `172.30.32.2`) and skip JWT auth for those requests. This is a security consideration -- only trust these headers from the Supervisor IP.

**Port change:** The current config uses port 8000 with `host_network: true`. For Ingress, the internal port should match `ingress_port`. Keeping port 8000 and setting `ingress_port: 8000` is the simplest approach. The `ports` mapping can remain for direct access outside Ingress.

### 5. Add-on Translations

**No library changes.** Translations are static YAML files read by HA Supervisor.
**Confidence:** HIGH (existing en.yaml and de.yaml already in place)

The existing translations at `ha-addon/translations/en.yaml` and `de.yaml` already follow the correct format:

```yaml
configuration:
  option_name:
    name: Display Name
    description: Description text
network:
  "8000/TCP": EMS web interface
```

For v1.2, the existing translations need to be updated to include descriptions for any new config options added. The format does not change.

## What NOT to Add

| Do NOT Add | Reason |
|-----------|--------|
| aiomqtt or asyncio-mqtt | Would create a second MQTT client pattern. paho-mqtt's thread-based model is proven and works fine for both publish and subscribe. |
| Any YAML parsing library | Translations are static files, not parsed by the EMS Python code. |
| nginx or any reverse proxy | HA Supervisor handles the Ingress proxy. FastAPI serves directly. |
| Additional auth library | HA Ingress provides authentication. Detect Ingress via header/IP and bypass EMS JWT auth. |
| WebSocket library beyond uvicorn | uvicorn already handles WebSocket. HA Ingress supports WebSocket proxying natively. |
| Path rewriting middleware (beyond the thin ASGI root_path setter) | FastAPI's ASGI scope `root_path` is the standard mechanism. No third-party middleware needed. |

## Integration Points

### paho-mqtt: From Publish-Only to Pub/Sub

The `HomeAssistantMqttClient` class needs these additions (not library changes):

1. **`on_message` callback** -- registered in `__init__`, dispatches to a command handler
2. **`subscribe()` call in `_on_connect`** -- subscribe to `ems/+/set` wildcard after CONNACK
3. **Command dispatch map** -- maps topic patterns to handler functions that update `SystemConfig` / `OrchestratorConfig` or trigger actions (force grid charge, recompute schedule)
4. **State echo** -- after processing a command, publish the new value back to the entity's state topic so HA confirms the change

The threading model is identical to `EvccMqttDriver`: paho callbacks in background thread, `call_soon_threadsafe` to bridge into asyncio for state mutations.

### FastAPI: Ingress Path Handling

1. **Thin ASGI middleware** -- reads `X-Ingress-Path`, sets `scope["root_path"]`
2. **Auth bypass for Ingress** -- detect Supervisor IP `172.30.32.2` or `X-Ingress-Path` header presence, skip JWT validation
3. **No route changes** -- all `/api/*` routes work as-is because root_path is a prefix, not part of the route

### Vite: Relative Base Path

1. **`base: './'`** in vite.config.ts for production builds
2. **WebSocket URL construction** -- use `window.location.host` + relative path, not hardcoded origin
3. **No router changes** -- wouter's default behavior works with base paths

## Versions Summary

| Package | Version | Status | Source |
|---------|---------|--------|--------|
| paho-mqtt | 2.1.0 | Latest stable (Apr 2024), already pinned `>=2.1` | [PyPI](https://pypi.org/project/paho-mqtt/) |
| FastAPI | latest (no pin) | Supports `root_path` for Ingress | [FastAPI docs](https://fastapi.tiangolo.com/advanced/behind-a-proxy/) |
| uvicorn[standard] | latest (no pin) | Supports `--proxy-headers` | Standard |
| Vite | 8.0.1 | Frontend build, `base` config for Ingress | Existing |

## Installation

No changes to `pyproject.toml` or `package.json` dependencies.

```bash
# No new packages to install
# Existing: pip install -e . (or uv pip install -e .)
# Existing: cd frontend && npm install
```

## Sources

- [HA MQTT Integration docs](https://www.home-assistant.io/integrations/mqtt/) -- discovery payload structure, availability, origin
- [HA MQTT Sensor](https://www.home-assistant.io/integrations/sensor.mqtt/) -- entity_category, expire_after behavior
- [HA MQTT Number](https://www.home-assistant.io/integrations/number.mqtt/) -- command_topic, min/max/step/mode
- [HA MQTT Select](https://www.home-assistant.io/integrations/select.mqtt/) -- command_topic, options list
- [HA MQTT Switch](https://www.home-assistant.io/integrations/switch.mqtt/) -- payload_on/off, state vs command topics
- [HA MQTT Button](https://www.home-assistant.io/integrations/button.mqtt/) -- payload_press, stateless action trigger
- [HA MQTT Binary Sensor](https://www.home-assistant.io/integrations/binary_sensor.mqtt/) -- device_class connectivity/running
- [HA Add-on Ingress docs](https://developers.home-assistant.io/docs/apps/presentation/) -- ingress config, X-Ingress-Path, port 8099 default, IP restriction
- [HA Add-on Configuration](https://developers.home-assistant.io/docs/apps/configuration/) -- ingress_port, ingress_entry, ingress_stream, translations format
- [FastAPI Behind a Proxy](https://fastapi.tiangolo.com/advanced/behind-a-proxy/) -- root_path mechanism
- [paho-mqtt PyPI](https://pypi.org/project/paho-mqtt/) -- version 2.1.0 latest
- [paho-mqtt migrations](https://eclipse.dev/paho/files/paho.mqtt.python/html/migrations.html) -- CallbackAPIVersion.VERSION2 signature details
