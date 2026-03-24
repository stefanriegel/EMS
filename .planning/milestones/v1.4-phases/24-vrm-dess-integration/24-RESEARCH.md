# Phase 24: VRM/DESS Integration - Research

**Researched:** 2026-03-24
**Domain:** Victron VRM REST API diagnostics + Venus OS MQTT DESS schedule reading + DESS-aware coordinator logic
**Confidence:** MEDIUM

## Summary

Phase 24 adds two new integration clients (VrmClient for REST diagnostics, DessMqttSubscriber for Venus OS MQTT schedule reading) and extends the coordinator to avoid contradicting DESS charge/discharge windows when controlling Huawei. All three components follow established patterns already in the codebase: VrmClient mirrors the httpx-based polling pattern, DessMqttSubscriber mirrors the paho-mqtt EvccMqttDriver pattern, and coordinator DESS awareness follows the same optional-injection model used by every other integration.

No new pip dependencies are required. The existing `httpx` library handles VRM API calls. The existing `paho-mqtt` library handles Venus OS MQTT subscriptions. The DESS subscriber connects to the same Venus OS MQTT broker that the EVCC driver already uses (same host, same port, same paho pattern).

**Primary recommendation:** Build three modules (`vrm_client.py`, `dess_mqtt.py`, coordinator extension) following the EvccMqttDriver and existing injection patterns exactly. VRM polling runs as an asyncio background task (5-minute interval). DESS schedule arrives via paho MQTT subscription and is cached as a dataclass for synchronous coordinator consumption.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
None explicitly locked -- infrastructure phase with all choices at Claude's discretion.

### Claude's Discretion
All implementation choices are at Claude's discretion -- infrastructure phase. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

Key constraints from research:
- VRM API uses Personal Access Token auth (header: X-Authorization: Token <token>)
- VRM client polls diagnostics every 5 minutes (configurable), never blocks control loop
- DESS schedule reading via Venus OS MQTT broker (same infrastructure as EVCC MQTT)
- DESS schedule D-Bus paths: N/{portalId}/settings/0/Settings/DynamicEss/Schedule/[0-3]/{Soc,Start,Duration,Strategy}
- Coordinator DESS awareness: avoid issuing Huawei discharge during DESS Victron charge windows
- All VRM/DESS components must be optional -- None checks, graceful degradation
- VRM client uses existing httpx library (already installed)
- Follow existing MQTT subscription pattern from evcc_mqtt_driver.py

### Deferred Ideas (OUT OF SCOPE)
None -- infrastructure phase.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DESS-01 | VRM client reads battery/system diagnostics via REST API with Personal Access Token auth | VRM API base URL, auth header format, diagnostics endpoint, httpx async pattern documented below |
| DESS-02 | EMS reads DESS planned charge/discharge schedule from Venus OS MQTT broker | Venus OS MQTT topic format, DESS D-Bus paths, paho-mqtt EvccMqttDriver pattern documented below |
| DESS-03 | Coordinator avoids issuing Huawei discharge during DESS Victron charge windows (and vice versa) | DESS schedule slot data model, coordinator injection pattern, discharge gating logic documented below |
| DESS-04 | VRM/DESS integration degrades gracefully when VRM credentials missing or Venus MQTT unavailable | Existing graceful degradation patterns documented (EvccMqttDriver, InfluxDB, Telegram) |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Graceful degradation**: every external dep must be optional -- `None` checks, never crash
- **No cloud for core operation**: VRM diagnostics are supplementary, not required for core control
- **Python conventions**: `snake_case` files, `PascalCase` dataclasses, `from __future__ import annotations`, type hints, `logger = logging.getLogger(__name__)`
- **Config pattern**: dataclass with `@classmethod from_env()` reading `os.environ`
- **Error handling**: explicit exceptions (never bare `except:`), fire-and-forget for optional integrations
- **Tests**: `tests/test_*.py` with `pytest` + `anyio`

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| httpx | (installed) | VRM REST API async client | Already in project deps, async-native, used for HA REST client pattern |
| paho-mqtt | (installed) | Venus OS MQTT subscription for DESS schedule | Already used by EvccMqttDriver and HaMqttClient |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| dataclasses (stdlib) | -- | DessScheduleSlot, DessSchedule, VrmDiagnostics models | All data models |
| asyncio (stdlib) | -- | Background polling task for VRM client | VRM 5-min poll loop |

No new pip dependencies needed.

## Architecture Patterns

### Recommended Project Structure
```
backend/
  vrm_client.py        # VRM REST API async client (DESS-01)
  dess_mqtt.py          # Venus OS MQTT DESS schedule subscriber (DESS-02)
  dess_models.py        # DessScheduleSlot, DessSchedule, VrmDiagnostics dataclasses
  coordinator.py        # Extended: DESS-aware discharge gating (DESS-03)
  config.py             # Extended: VrmConfig, DessConfig dataclasses
  controller_model.py   # Extended: CoordinatorState DESS fields
  main.py               # Extended: wire VRM + DESS in lifespan
  api.py                # Extended: /api/health DESS section
tests/
  test_vrm_client.py    # VRM client unit tests
  test_dess_mqtt.py     # DESS MQTT subscriber unit tests
  test_coordinator_dess.py  # Coordinator DESS-aware logic tests
```

### Pattern 1: VRM REST Client (mirrors existing httpx patterns)
**What:** Async httpx client that polls VRM diagnostics endpoint on a timer.
**When to use:** For reading VRM battery/system diagnostics.
**Example:**
```python
# Source: VRM API docs + existing httpx patterns in ha_rest_client.py
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_VRM_BASE_URL = "https://vrmapi.victronenergy.com"


@dataclass
class VrmDiagnostics:
    """Cached VRM diagnostics snapshot."""
    battery_soc_pct: float | None = None
    battery_power_w: float | None = None
    grid_power_w: float | None = None
    pv_power_w: float | None = None
    consumption_w: float | None = None
    timestamp: float = 0.0


class VrmClient:
    def __init__(self, token: str, site_id: int, poll_interval_s: float = 300.0) -> None:
        self._token = token
        self._site_id = site_id
        self._poll_interval_s = poll_interval_s
        self._client: httpx.AsyncClient | None = None
        self._diagnostics = VrmDiagnostics()
        self._available = False
        self._task: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        return self._available

    @property
    def diagnostics(self) -> VrmDiagnostics:
        return self._diagnostics

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=_VRM_BASE_URL,
            headers={"X-Authorization": f"Token {self._token}"},
            timeout=30.0,
        )
        self._task = asyncio.create_task(self._poll_loop(), name="vrm-poll")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self._client is not None:
            await self._client.aclose()

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._fetch_diagnostics()
            except Exception as exc:  # noqa: BLE001
                logger.warning("VRM poll failed: %s", exc)
                self._available = False
            await asyncio.sleep(self._poll_interval_s)

    async def _fetch_diagnostics(self) -> None:
        resp = await self._client.get(
            f"/v2/installations/{self._site_id}/diagnostics",
            params={"count": 100},
        )
        if resp.status_code == 429:
            logger.warning("VRM rate limited — backing off")
            self._available = False
            return
        resp.raise_for_status()
        data = resp.json()
        self._diagnostics = self._parse_diagnostics(data)
        self._available = True

    def _parse_diagnostics(self, data: dict) -> VrmDiagnostics:
        # VRM diagnostics returns {records: [{idDataAttribute: ..., rawValue: ...}]}
        # Parse known attribute IDs into typed fields
        ...
```

### Pattern 2: DESS MQTT Subscriber (mirrors EvccMqttDriver exactly)
**What:** paho-mqtt subscriber that connects to Venus OS MQTT broker and reads DESS schedule slots.
**When to use:** For reading DESS planned charge/discharge windows.
**Example:**
```python
# Source: EvccMqttDriver pattern + Venus OS MQTT topic docs
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


@dataclass
class DessScheduleSlot:
    """A single DESS schedule slot (0-3)."""
    soc_pct: float = 0.0
    start_s: int = 0          # Seconds from midnight
    duration_s: int = 0
    strategy: int = 0         # 0=optimize, 1=charge, 2=sell, etc.
    active: bool = False


@dataclass
class DessSchedule:
    """Current DESS schedule (up to 4 slots)."""
    slots: list[DessScheduleSlot] = field(default_factory=lambda: [
        DessScheduleSlot() for _ in range(4)
    ])
    mode: int = 0             # 0=off, 1=auto(VRM), 4=Node-RED
    last_update: float = 0.0


class DessMqttSubscriber:
    """Venus OS MQTT subscriber for DESS schedule data.

    Threading model matches EvccMqttDriver: paho callbacks run in paho's
    background thread, state mutations cross to asyncio via
    loop.call_soon_threadsafe().
    """

    def __init__(self, host: str, port: int = 1883, portal_id: str = "") -> None:
        self.host = host
        self.port = port
        self._portal_id = portal_id
        self.schedule = DessSchedule()
        self.dess_available: bool = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="ems-dess-subscriber",
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    async def connect(self) -> None:
        self._loop = asyncio.get_event_loop()
        try:
            self._client.connect(self.host, self.port)
        except (ConnectionRefusedError, OSError) as exc:
            logger.warning("DESS MQTT connect failed: %s", exc)
            self.dess_available = False
            return
        self._client.loop_start()

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties) -> None:
        if reason_code != 0:
            return
        # Subscribe to DESS schedule topics
        topic = f"N/{self._portal_id}/settings/0/Settings/DynamicEss/#"
        client.subscribe(topic)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._set_available, True)
        logger.info("DESS MQTT connected to %s:%d", self.host, self.port)

    def _on_message(self, client, userdata, message) -> None:
        # Parse: N/{portalId}/settings/0/Settings/DynamicEss/Schedule/{slot}/{field}
        # or:    N/{portalId}/settings/0/Settings/DynamicEss/Mode
        ...
```

### Pattern 3: Coordinator DESS-Aware Discharge Gating
**What:** Before issuing a Huawei discharge command, check if DESS has Victron in a charge window. If so, gate the discharge to avoid cross-charging.
**When to use:** Every coordinator control cycle when DESS subscriber is available.
**Example:**
```python
# Inside coordinator._run_cycle(), after computing commands but before execute:
def _apply_dess_guard(
    self,
    h_cmd: ControllerCommand,
    v_cmd: ControllerCommand,
) -> tuple[ControllerCommand, ControllerCommand]:
    """Avoid contradicting DESS charge/discharge windows."""
    if self._dess_subscriber is None or not self._dess_subscriber.dess_available:
        return h_cmd, v_cmd

    schedule = self._dess_subscriber.schedule
    active_slot = self._get_active_dess_slot(schedule)
    if active_slot is None:
        return h_cmd, v_cmd

    # If DESS is charging Victron, don't discharge Huawei
    # (would cause cross-charge via AC bus)
    if active_slot.strategy == 1:  # charge strategy
        if h_cmd.target_watts < 0:  # discharge command
            h_cmd = dataclasses.replace(h_cmd, role=BatteryRole.HOLDING, target_watts=0)
            self._log_decision(
                trigger="dess_coordination",
                reasoning=f"DESS charging Victron (slot SoC={active_slot.soc_pct}%) — "
                          "suppressed Huawei discharge to prevent cross-charge",
            )
    return h_cmd, v_cmd
```

### Anti-Patterns to Avoid
- **Writing to VRM API:** Violates local-only constraint; read diagnostics only.
- **Writing DESS schedule via MQTT (W/ prefix):** Creates dual-controller oscillation. EMS reads DESS, never writes.
- **Blocking the control loop on VRM HTTP calls:** VRM polling must run in a background asyncio task with cached results, never inline in the 5s cycle.
- **Sharing paho client with EVCC driver:** Each MQTT subscriber must have its own paho client instance with a unique client_id to avoid interference.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP client with retry | Custom urllib wrapper | httpx with built-in timeout | httpx handles connection pooling, async, timeouts |
| MQTT subscription | Raw socket listener | paho-mqtt Client | Proven MQTT 3.1.1/5.0 client, same as EvccMqttDriver |
| JSON time parsing | Custom seconds-from-midnight | Standard int parsing from MQTT payload | DESS schedule uses simple integer seconds |
| Rate limit handling | Complex retry state machine | Simple exponential backoff with max 3 retries | VRM rate limits are generous at 5-min polling |

## Common Pitfalls

### Pitfall 1: VRM API Rate Limiting
**What goes wrong:** Polling too frequently (e.g., every 30s) triggers HTTP 429 responses.
**Why it happens:** VRM API has undocumented rate limits; community reports ~300 req/hour threshold.
**How to avoid:** Poll every 5 minutes (12 req/hour). Handle 429 with exponential backoff. Cache last-good diagnostics.
**Warning signs:** Consecutive 429 responses in logs.

### Pitfall 2: Venus OS MQTT Portal ID Mismatch
**What goes wrong:** Subscribing to `N/wrong-id/settings/...` receives zero messages silently.
**Why it happens:** Portal ID (e.g., `e0ff50a097c0`) is specific to the Venus OS installation and not obvious from environment.
**How to avoid:** Allow portal_id from config. Alternatively, subscribe with wildcard `N/+/settings/0/Settings/DynamicEss/#` and extract portal_id from first message. Log clearly when no messages received within 60s.
**Warning signs:** `dess_available` stays False after connect.

### Pitfall 3: DESS Schedule Slot Time Interpretation
**What goes wrong:** Misinterpreting `Start` field as UTC when it is actually seconds from midnight in local time (Venus OS timezone).
**Why it happens:** No timezone field in the MQTT payload; time is relative to GX device local clock.
**How to avoid:** Document that Start is seconds-from-midnight in the GX device's configured timezone. Use `zoneinfo.ZoneInfo("Europe/Berlin")` (same as the rest of EMS) for comparison.
**Warning signs:** DESS guard triggers at wrong times of day.

### Pitfall 4: Stale VRM Diagnostics After Network Outage
**What goes wrong:** Using cached diagnostics that are hours old after a network hiccup.
**Why it happens:** VRM poll failure leaves last-good data in cache indefinitely.
**How to avoid:** Add `timestamp` to VrmDiagnostics. In any consumer, check age. After 15 minutes of stale data, mark `available = False`.
**Warning signs:** VRM `available` is True but diagnostics timestamp is old.

### Pitfall 5: paho Client ID Collision
**What goes wrong:** Two EMS instances (or EMS + another client) use the same MQTT client_id, causing one to be forcibly disconnected.
**Why it happens:** MQTT broker (FlashMQ on Venus OS v3.20+) only allows one client per ID.
**How to avoid:** Use unique client_id: `"ems-dess-subscriber"` (distinct from `"ems-evcc-driver"` and `"ems-ha-mqtt"`).
**Warning signs:** Frequent reconnect/disconnect cycles in MQTT logs.

### Pitfall 6: DESS Mode 0 (Off) Misinterpreted as Active
**What goes wrong:** EMS applies DESS coordination logic when DESS is disabled on VRM.
**Why it happens:** Mode=0 means DESS is off, but schedule slots may still contain stale data.
**How to avoid:** Check `DessSchedule.mode` first. Only apply DESS guard when `mode >= 1`. Treat mode=0 as "no DESS, skip all guards."
**Warning signs:** Unexpected HOLDING decisions when DESS is known to be off.

## Code Examples

### VrmConfig Dataclass
```python
# Source: existing config.py patterns (EvccMqttConfig, TelegramConfig)
@dataclass
class VrmConfig:
    """VRM REST API configuration.

    VRM diagnostics are optional. When both token and site_id are empty,
    the VRM client is not instantiated.

    Environment variables:
        ``VRM_TOKEN``        -- Personal Access Token (default empty -> disabled).
        ``VRM_SITE_ID``      -- VRM installation site ID (default empty -> disabled).
        ``VRM_POLL_INTERVAL_S`` -- Poll interval in seconds (default 300).
    """
    token: str = ""
    site_id: str = ""
    poll_interval_s: float = 300.0

    @classmethod
    def from_env(cls) -> "VrmConfig":
        return cls(
            token=os.environ.get("VRM_TOKEN", ""),
            site_id=os.environ.get("VRM_SITE_ID", ""),
            poll_interval_s=float(os.environ.get("VRM_POLL_INTERVAL_S", "300")),
        )
```

### DessConfig Dataclass
```python
@dataclass
class DessConfig:
    """Venus OS MQTT DESS schedule configuration.

    Uses the same MQTT broker as EVCC (Venus OS / HA Mosquitto).
    When portal_id is empty, DESS subscription is disabled.

    Environment variables:
        ``DESS_MQTT_HOST``   -- Venus OS MQTT host (default from VICTRON_HOST).
        ``DESS_MQTT_PORT``   -- Venus OS MQTT port (default 1883).
        ``DESS_PORTAL_ID``   -- Venus OS portal ID (default empty -> disabled).
    """
    host: str = ""
    port: int = 1883
    portal_id: str = ""

    @classmethod
    def from_env(cls) -> "DessConfig":
        return cls(
            host=os.environ.get("DESS_MQTT_HOST", os.environ.get("VICTRON_HOST", "")),
            port=int(os.environ.get("DESS_MQTT_PORT", "1883")),
            portal_id=os.environ.get("DESS_PORTAL_ID", ""),
        )
```

### CoordinatorState Extension
```python
# Add to existing CoordinatorState in controller_model.py:
    dess_mode: int = 0
    """DESS mode: 0=off, 1=auto(VRM), 4=Node-RED. 0 means no DESS coordination."""

    dess_active_slot: int | None = None
    """Index (0-3) of the currently active DESS schedule slot, or None."""

    dess_available: bool = False
    """True when DESS MQTT subscriber is connected and receiving data."""

    vrm_available: bool = False
    """True when VRM REST client is connected and polling."""
```

### Lifespan Wiring
```python
# In main.py lifespan, after coordinator.set_cross_charge_detector():
from backend.config import VrmConfig, DessConfig

# --- VRM client (optional) ---
vrm_cfg = VrmConfig.from_env()
if vrm_cfg.token and vrm_cfg.site_id:
    from backend.vrm_client import VrmClient
    vrm_client = VrmClient(
        token=vrm_cfg.token,
        site_id=int(vrm_cfg.site_id),
        poll_interval_s=vrm_cfg.poll_interval_s,
    )
    await vrm_client.start()
    coordinator.set_vrm_client(vrm_client)
    app.state.vrm_client = vrm_client
    logger.info("VRM client started -- site_id=%s poll=%ds", vrm_cfg.site_id, vrm_cfg.poll_interval_s)
else:
    app.state.vrm_client = None
    logger.info("VRM client disabled -- VRM_TOKEN / VRM_SITE_ID not set")

# --- DESS MQTT subscriber (optional) ---
dess_cfg = DessConfig.from_env()
if dess_cfg.host and dess_cfg.portal_id:
    from backend.dess_mqtt import DessMqttSubscriber
    dess_sub = DessMqttSubscriber(
        host=dess_cfg.host,
        port=dess_cfg.port,
        portal_id=dess_cfg.portal_id,
    )
    await dess_sub.connect()
    coordinator.set_dess_subscriber(dess_sub)
    app.state.dess_subscriber = dess_sub
    logger.info("DESS MQTT subscriber connected -- host=%s portal=%s", dess_cfg.host, dess_cfg.portal_id)
else:
    app.state.dess_subscriber = None
    logger.info("DESS subscriber disabled -- DESS_PORTAL_ID not set")
```

### Health Endpoint Extension
```python
# In api.py get_health(), add to result dict:
    "dess": {
        "available": getattr(request.app.state, "dess_subscriber", None) is not None
                     and request.app.state.dess_subscriber.dess_available,
        "mode": (request.app.state.dess_subscriber.schedule.mode
                 if getattr(request.app.state, "dess_subscriber", None) else 0),
        "active_slot": None,  # populated from coordinator state
    },
    "vrm": {
        "available": getattr(request.app.state, "vrm_client", None) is not None
                     and request.app.state.vrm_client.available,
    },
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Mosquitto MQTT on Venus OS | FlashMQ MQTT broker | Venus OS v3.20 (2024) | Same MQTT protocol, faster; paho client unaffected |
| VRM API user/pass login | Personal Access Token | 2023 | Simpler auth, no login dance; just `X-Authorization: Token <pat>` header |
| DESS via VRM cloud API | DESS via Venus OS MQTT (local) | Always available | Avoids cloud dependency, lower latency, authoritative schedule source |

## Open Questions

1. **VRM Diagnostics Response Schema**
   - What we know: Endpoint returns `{records: [{idDataAttribute, rawValue, ...}]}`. Attribute IDs map to specific measurements.
   - What's unclear: Exact `idDataAttribute` values for battery SoC, power, grid power. Schema is not fully documented publicly.
   - Recommendation: Fetch one diagnostics response on first poll, log the raw JSON at DEBUG level, then map known attribute IDs. Build the parser incrementally. VRM diagnostics are supplementary (not blocking for core operation).

2. **DESS Schedule Slot Strategy Values**
   - What we know: Strategy field exists with values 0, 1, 2. Community sources suggest 0=optimize, 1=charge, 2=sell.
   - What's unclear: Full enum of strategy values and edge cases (e.g., what does strategy=0 mean for the coordinator guard?).
   - Recommendation: Initially gate Huawei discharge only when strategy clearly indicates Victron charging (strategy=1). Log unknown strategy values at WARNING. Expand handling as field data confirms behavior.

3. **Venus OS MQTT Authentication**
   - What we know: EVCC MQTT driver connects without auth to the Venus OS broker. Venus OS v3.20+ uses FlashMQ.
   - What's unclear: Whether the Venus OS MQTT broker requires auth for the DESS settings topics (it may differ from the telemetry topics).
   - Recommendation: Try unauthenticated first (matching EVCC pattern). If rejected, add optional username/password to DessConfig (same pattern as HaMqttConfig).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + anyio |
| Config file | pyproject.toml (`asyncio_mode = "auto"`) |
| Quick run command | `python -m pytest tests/test_vrm_client.py tests/test_dess_mqtt.py tests/test_coordinator_dess.py -q` |
| Full suite command | `python -m pytest tests/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DESS-01 | VRM client polls diagnostics with PAT auth, caches result | unit | `python -m pytest tests/test_vrm_client.py -x` | Wave 0 |
| DESS-02 | DESS subscriber reads schedule from Venus OS MQTT | unit | `python -m pytest tests/test_dess_mqtt.py -x` | Wave 0 |
| DESS-03 | Coordinator gates Huawei discharge during DESS charge windows | unit | `python -m pytest tests/test_coordinator_dess.py -x` | Wave 0 |
| DESS-04 | Graceful degradation when VRM/DESS unavailable | unit | `python -m pytest tests/test_vrm_client.py tests/test_dess_mqtt.py tests/test_coordinator_dess.py -k "graceful or unavail or missing" -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_vrm_client.py tests/test_dess_mqtt.py tests/test_coordinator_dess.py -q`
- **Per wave merge:** `python -m pytest tests/ -q`
- **Phase gate:** Full suite green before verification

### Wave 0 Gaps
- [ ] `tests/test_vrm_client.py` -- covers DESS-01, DESS-04 (VRM portion)
- [ ] `tests/test_dess_mqtt.py` -- covers DESS-02, DESS-04 (DESS portion)
- [ ] `tests/test_coordinator_dess.py` -- covers DESS-03, DESS-04 (coordinator portion)

## Sources

### Primary (HIGH confidence)
- Existing codebase: `evcc_mqtt_driver.py` (MQTT subscription pattern), `config.py` (dataclass config pattern), `coordinator.py` (injection and guard patterns), `main.py` (lifespan wiring), `controller_model.py` (CoordinatorState extension)
- [VRM API v2 docs](https://vrm-api-docs.victronenergy.com/) -- base URL `https://vrmapi.victronenergy.com`, diagnostics at `/v2/installations/{idSite}/diagnostics`, auth via `X-Authorization: Token <pat>`
- [VRM API Python client](https://github.com/victronenergy/vrm-api-python-client/blob/master/vrmapi/vrm.py) -- confirmed base URL, header format, diagnostics endpoint parameters

### Secondary (MEDIUM confidence)
- [Dynamic ESS GitHub](https://github.com/victronenergy/dynamic-ess) -- DESS D-Bus paths `Settings/DynamicEss/Schedule/[0-3]/{Soc,Start,Duration,Strategy}`, Mode values
- [Venus OS dbus-mqtt](https://github.com/victronenergy/dbus-mqtt) -- N/ prefix for read topics, `N/{portalId}/settings/0/Settings/...` format (archived, replaced by dbus-flashmq)
- [Venus OS MQTT Topics (DeepWiki)](https://deepwiki.com/victronenergy/venus-html5-app/5.1-mqtt-topics) -- topic format `N/{portalId}/{serviceType}/{deviceInstance}/{path}`, JSON payload `{"value": ...}`

### Tertiary (LOW confidence -- needs field validation)
- DESS schedule slot strategy enum values (0/1/2) -- inferred from community sources, not officially documented
- VRM diagnostics `idDataAttribute` mapping -- needs discovery from live API response
- Venus OS MQTT auth requirements for settings topics -- assumed unauthenticated based on EVCC pattern

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- zero new dependencies, all patterns proven in codebase
- Architecture: HIGH -- exact replication of EvccMqttDriver and existing injection patterns
- VRM API specifics: MEDIUM -- auth and endpoint confirmed, response schema needs discovery
- DESS MQTT topics: MEDIUM -- D-Bus paths from GitHub, exact MQTT payloads need field validation
- Pitfalls: HIGH -- based on codebase analysis and community reports

**Research date:** 2026-03-24
**Valid until:** 2026-04-24 (stable domain, VRM API is mature)
