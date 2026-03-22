# Phase 4: Integration & Monitoring - Research

**Researched:** 2026-03-22
**Domain:** External integration wiring, structured logging, HA MQTT discovery, InfluxDB metrics, API endpoints
**Confidence:** HIGH

## Summary

Phase 4 connects the Phase 2/3 coordinator to all external systems (EVCC, InfluxDB, HA MQTT, Telegram) and adds decision transparency. The codebase already has working implementations of every integration client -- the work is wiring them into the coordinator's control loop (they currently run on the legacy orchestrator or are connected but never called), extending InfluxDB measurements, expanding HA MQTT entities, adding new API endpoints, and building the decision ring buffer.

**Critical finding:** The coordinator (`coordinator.py`) has a `_writer` attribute injected at construction but NEVER calls `write_system_state()` or any write method. The HA MQTT client is connected at startup (`main.py` line 441) but `publish()` is never invoked from the coordinator loop. Both integrations worked in the legacy `Orchestrator` but were not migrated to the new `Coordinator`. Phase 4 must wire these into `_run_cycle()`.

**Primary recommendation:** Follow the established per-cycle pattern from the legacy orchestrator: at the end of `_run_cycle()`, call InfluxDB write and HA MQTT publish with fire-and-forget exception handling. Add decision logging as a new concern within the coordinator, triggered by state-change detection.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D-01 through D-03: EVCC hold propagation -- coordinator reads `evcc_battery_mode` each cycle, sets both controllers to HOLDING when "hold", treats missing EVCC as "normal"
- D-04 through D-06: API endpoints -- `/api/devices`, expanded `/api/state`, `/api/health` with integration status
- D-07 through D-09: Graceful degradation audit -- every integration follows try-connect/catch/log/None pattern, mid-run failures caught without blocking 5s loop
- D-10 through D-13: Decision transparency -- 100-entry ring buffer, log only on change, `ems_decision` InfluxDB measurement, `/api/decisions` endpoint
- D-14 through D-17: Per-phase Victron dispatch -- VictronController owns phase distribution, 20W per-phase dead-band, equal split fallback
- D-18 through D-20: Per-battery charge targets -- scheduler already produces per-battery ChargeSlots, coordinator routes them independently
- D-21 through D-25: Per-system InfluxDB metrics -- `ems_huawei`, `ems_victron` measurements, keep `ems_system`, per-phase Victron fields, `ems_decision` measurement
- D-26 through D-32: HA MQTT entities -- per-system role, power, availability, pool status, per-phase Victron power sensors; keep existing 7 entities, total ~17-18

### Claude's Discretion
- Decision ring buffer dataclass structure and field naming
- InfluxDB measurement field types (float vs int for power values)
- HA MQTT entity `unique_id` naming scheme for new entities
- Internal method decomposition for integration health tracking
- Test fixture organization for integration mocking
- Whether `/api/health` returns flat object or grouped by category
- Exact `ems_decision` InfluxDB tag vs field split

### Deferred Ideas (OUT OF SCOPE)
- Grafana dashboard templates (ECO-02)
- Decision log retention policy
- WebSocket decision streaming
- Integration auto-recovery (periodic retry of failed services)
- Per-phase tariff awareness
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INT-01 | EVCC hold signal propagated to both controllers | Coordinator already reads `_evcc_battery_mode` and sends HOLDING commands (lines 296-307). Verify end-to-end with structured logging. |
| INT-02 | Per-system SoC, power, and health exposed via API | `get_device_snapshot()` exists (line 176). Add role fields to `/api/state`. New `/api/devices` endpoint already partially implemented (api.py line 660). |
| INT-03 | All external integrations optional | Lifespan follows try-connect pattern. Gap: coordinator must handle mid-run failures in InfluxDB/HA MQTT writes. |
| INT-04 | Decision transparency: structured log | New feature: ring buffer in coordinator, dataclass entries, `/api/decisions` endpoint, `ems_decision` InfluxDB measurement. |
| INT-05 | Phase-aware Victron dispatch | Already implemented in VictronController._write_discharge() (lines 202-218). Verify dead-band and equal-split fallback. |
| INT-06 | Per-battery nightly charge targets from scheduler | Coordinator._check_grid_charge() and _compute_grid_charge_commands() already handle per-battery slots. Verify routing. |
| INT-07 | Per-system metrics in InfluxDB | New write methods in InfluxMetricsWriter. Critical gap: coordinator never calls writer. |
| INT-08 | HA MQTT discovery publishes per-system entities | Extend `_ENTITIES` list in ha_mqtt_client.py. Critical gap: publish() never called from coordinator. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| influxdb-client[async] | >=1.45 | Per-system metrics persistence | Already in pyproject.toml, async write API used by existing InfluxMetricsWriter |
| paho-mqtt | >=2.1 | HA MQTT discovery and EVCC monitoring | Already in pyproject.toml, CallbackAPIVersion.VERSION2 |
| fastapi | latest | REST API endpoints | Already in pyproject.toml |
| httpx | latest | Telegram notifications | Already in pyproject.toml |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| collections.deque | stdlib | Decision ring buffer (maxlen=100) | Decision transparency (INT-04) |
| dataclasses | stdlib | Decision entry, integration status types | All new data structures |

### Alternatives Considered
None -- all libraries are already in the project. No new dependencies needed for Phase 4.

## Architecture Patterns

### Recommended Project Structure
No new files needed beyond extending existing modules:
```
backend/
├── coordinator.py           # Add: decision logging, integration wiring, health tracking
├── controller_model.py      # Add: DecisionEntry dataclass
├── api.py                   # Add: /api/decisions, expanded /api/health
├── influx_writer.py         # Add: write_per_system_metrics(), write_decision()
├── ha_mqtt_client.py        # Add: ~10 new entities to _ENTITIES list
└── main.py                  # Add: wire HA MQTT publish + InfluxDB write into coordinator
```

### Pattern 1: Fire-and-Forget Integration Calls
**What:** Every integration call (InfluxDB write, HA MQTT publish, Telegram alert) is wrapped in try/except Exception with WARNING log. Never blocks the 5s control loop.
**When to use:** Every per-cycle integration call.
**Example:**
```python
# Source: backend/influx_writer.py lines 81-99 (existing pattern)
try:
    point = Point("ems_huawei").field("soc_pct", float(soc)).time(datetime.now(tz=timezone.utc))
    await self._write_api.write(bucket=self._bucket, record=point)
except Exception as exc:
    logger.warning("influx write failed: %s", exc)
```

### Pattern 2: Optional Dependency Injection via set_*()
**What:** External services are injected into the coordinator via setter methods. None means disabled.
**When to use:** For all optional integrations (InfluxDB writer, HA MQTT client, Telegram notifier, EVCC monitor).
**Example:**
```python
# Source: backend/coordinator.py lines 140-150 (existing pattern)
def set_scheduler(self, scheduler) -> None:
    self._scheduler = scheduler

# In the control loop:
if self._writer is not None:
    await self._writer.write_system_state(state)
```

### Pattern 3: Mechanical Entity List Extension (HA MQTT)
**What:** HA MQTT entities are defined as a flat list of tuples. Adding entities means appending rows.
**When to use:** INT-08 new entity additions.
**Example:**
```python
# Source: backend/ha_mqtt_client.py lines 60-68 (existing pattern)
_ENTITIES: list[tuple[str, str, str | None, str | None, str | None, str]] = [
    ("huawei_soc", "Huawei Battery SOC", "%", "battery", "measurement", "huawei_soc_pct"),
    # ... existing 7 entities ...
    # New entities (INT-08):
    ("huawei_role",    "Huawei Battery Role",    None, None,           None,          "huawei_role"),
    ("victron_role",   "Victron Battery Role",   None, None,           None,          "victron_role"),
    ("huawei_power",   "Huawei Battery Power",   "W",  "power",       "measurement", "huawei_power_w"),
    ("victron_power",  "Victron Battery Power",  "W",  "power",       "measurement", "victron_power_w"),
    ("huawei_online",  "Huawei Online",          None, "connectivity", None,          "huawei_available"),
    ("victron_online", "Victron Online",         None, "connectivity", None,          "victron_available"),
    ("pool_status",    "EMS Pool Status",        None, None,           None,          "pool_status"),
    ("victron_l1_power", "Victron L1 Power",     "W",  "power",       "measurement", "victron_l1_power_w"),
    ("victron_l2_power", "Victron L2 Power",     "W",  "power",       "measurement", "victron_l2_power_w"),
    ("victron_l3_power", "Victron L3 Power",     "W",  "power",       "measurement", "victron_l3_power_w"),
]
```

### Pattern 4: Change-Detection Decision Logging
**What:** Coordinator maintains previous-cycle state and only logs a decision entry when roles change, allocations shift beyond dead-band, or events occur (hold/failover/slot transitions).
**When to use:** INT-04 decision ring buffer.
**Example:**
```python
from collections import deque

@dataclass
class DecisionEntry:
    timestamp: str          # ISO 8601 UTC
    trigger: str            # "role_change" | "hold_signal" | "slot_start" | "slot_end" | "failover" | "allocation_shift"
    huawei_role: str
    victron_role: str
    p_target_w: float
    huawei_allocation_w: float
    victron_allocation_w: float
    reasoning: str          # Human-readable WHY text

# In Coordinator.__init__:
self._decisions: deque[DecisionEntry] = deque(maxlen=100)
self._prev_h_role: str = "HOLDING"
self._prev_v_role: str = "HOLDING"

# At end of _run_cycle, after building state:
if h_cmd.role.value != self._prev_h_role or v_cmd.role.value != self._prev_v_role:
    self._decisions.append(DecisionEntry(...))
    self._prev_h_role = h_cmd.role.value
    self._prev_v_role = v_cmd.role.value
```

### Pattern 5: Integration Health Tracking
**What:** Per-integration status tracking with available/last_error/last_seen fields, feeding `/api/health` and HA MQTT entities.
**When to use:** INT-03 graceful degradation status, D-09.
**Example:**
```python
@dataclass
class IntegrationStatus:
    service: str
    available: bool
    last_error: str | None = None
    last_seen: datetime | None = None
```

### Anti-Patterns to Avoid
- **Blocking the 5s loop with integration calls:** Never await an integration call without a timeout or fire-and-forget wrapper. The control loop MUST complete in under 5s.
- **Publishing HA MQTT discovery on every cycle:** Discovery is sent once on connect (existing `_ensure_discovery()` pattern). State updates are per-cycle, but discovery config is retained.
- **Logging every cycle in the decision buffer:** D-11 explicitly requires change-detection. Logging every 5s cycle fills the buffer with identical entries and makes it useless.
- **Modifying `_ENTITIES` to use binary_sensor platform for availability:** HA MQTT discovery for binary sensors uses a different topic prefix (`homeassistant/binary_sensor/`). The current code hardcodes `homeassistant/sensor/`. Either handle platform-specific topics or keep availability as a text sensor with "True"/"False" values.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Ring buffer with fixed size | Custom list with manual rotation | `collections.deque(maxlen=100)` | Thread-safe, O(1) append, automatic eviction |
| InfluxDB Point construction | String-formatted line protocol | `influxdb_client.Point` class | Handles escaping, type coercion, timestamp formatting |
| HA MQTT discovery payloads | Manual JSON string building | Existing `_discovery_payload()` method | Handles device registration, unique_id, value_template correctly |
| Integration health monitoring | Custom polling/watchdog | Track status in existing fire-and-forget catch blocks | Already catching exceptions; just update a status dict |

## Common Pitfalls

### Pitfall 1: HA MQTT Binary Sensor vs Sensor Platform
**What goes wrong:** Adding `device_class: "connectivity"` to a sensor entity. HA expects binary_sensor for connectivity class.
**Why it happens:** The existing `_discovery_topic()` method hardcodes `homeassistant/sensor/` prefix.
**How to avoid:** Either (a) add platform-aware topic generation for binary_sensor entities, or (b) use a text sensor with string values "on"/"off" instead of binary_sensor. Option (b) is simpler but less idiomatic.
**Warning signs:** Entities appear in HA but show "unknown" state or don't respond to automations.

### Pitfall 2: Coordinator Writer Never Called
**What goes wrong:** Per-system InfluxDB metrics are never written despite the writer being injected.
**Why it happens:** The coordinator has `self._writer` but the legacy orchestrator's `write_system_state()` call was not migrated. The coordinator's `_run_cycle()` ends without any writer call.
**How to avoid:** Add writer calls at the end of `_run_cycle()`, after `_build_state()`. Follow the legacy orchestrator pattern (orchestrator.py lines 398-399).
**Warning signs:** InfluxDB measurements stop appearing after switching to the coordinator.

### Pitfall 3: HA MQTT Publish Never Invoked
**What goes wrong:** HA entities exist in discovery but never update with actual values.
**Why it happens:** `HomeAssistantMqttClient.publish()` is never called from the coordinator loop. The client connects at startup but nothing triggers state publishing.
**How to avoid:** Add `ha_mqtt_client.publish(state)` to the coordinator's per-cycle path, or add a `set_ha_mqtt_client()` method on the coordinator and call publish from `_run_cycle()`.
**Warning signs:** HA entities show "unavailable" or stale values.

### Pitfall 4: CoordinatorState vs UnifiedPoolState Type Mismatch
**What goes wrong:** The HA MQTT client's `publish()` method takes `UnifiedPoolState`, but the coordinator produces `CoordinatorState`. These are different dataclasses.
**Why it happens:** Phase 2 introduced `CoordinatorState` as a superset, but the HA MQTT client was written for the legacy model.
**How to avoid:** Either (a) make `HomeAssistantMqttClient.publish()` accept `CoordinatorState` (it already has all the fields HA needs via `dataclasses.asdict()`), or (b) change the type hint to `Any` and rely on duck typing. Option (a) is cleaner -- change the type hint and adjust `_publish_state()` to serialize `CoordinatorState`.
**Warning signs:** `TypeError` at runtime when publish is first called with a `CoordinatorState`.

### Pitfall 5: InfluxDB Tag Cardinality for Decision Logging
**What goes wrong:** Using roles as InfluxDB tags creates high cardinality, degrading query performance.
**Why it happens:** Tags are indexed; fields are not. Roles have 5 values x 2 systems = 25 combinations per decision.
**How to avoid:** Use `huawei_role` and `victron_role` as fields (strings), not tags. Only use the `trigger` as a tag (6 distinct values). Keep `ems_decision` tag cardinality low.
**Warning signs:** Slow InfluxDB queries on the decision measurement.

### Pitfall 6: Missing Per-Phase Data in HA MQTT State Payload
**What goes wrong:** New HA entities reference `victron_l1_power_w` in value_template, but the state payload from `dataclasses.asdict(state)` doesn't have this field.
**Why it happens:** `CoordinatorState` doesn't include per-phase power fields. They exist in `ControllerSnapshot` but not in the state object published to HA.
**How to avoid:** Either (a) add per-phase fields to `CoordinatorState`, or (b) build a custom HA MQTT state payload that merges coordinator state with Victron controller snapshot data. Option (b) is more flexible.
**Warning signs:** Per-phase entities show "None" or "unknown" in HA.

## Code Examples

### Example 1: Per-System InfluxDB Write Method
```python
# Add to backend/influx_writer.py
async def write_per_system_metrics(
    self,
    h_snap: "ControllerSnapshot",
    v_snap: "ControllerSnapshot",
    h_role: str,
    v_role: str,
) -> None:
    """Write per-system measurements: ems_huawei and ems_victron."""
    now = datetime.now(tz=timezone.utc)
    try:
        h_point = (
            Point("ems_huawei")
            .tag("role", h_role)
            .tag("available", "true" if h_snap.available else "false")
            .field("soc_pct", float(h_snap.soc_pct))
            .field("power_w", float(h_snap.power_w))
            .field("setpoint_w", float(abs(h_snap.power_w)))  # placeholder
            .field("charge_headroom_w", float(h_snap.charge_headroom_w))
            .time(now)
        )
        v_point = (
            Point("ems_victron")
            .tag("role", v_role)
            .tag("available", "true" if v_snap.available else "false")
            .field("soc_pct", float(v_snap.soc_pct))
            .field("power_w", float(v_snap.power_w))
            .field("charge_headroom_w", float(v_snap.charge_headroom_w))
            .field("l1_power_w", float(v_snap.grid_l1_power_w or 0.0))
            .field("l2_power_w", float(v_snap.grid_l2_power_w or 0.0))
            .field("l3_power_w", float(v_snap.grid_l3_power_w or 0.0))
            .field("grid_l1_power_w", float(v_snap.grid_l1_power_w or 0.0))
            .field("grid_l2_power_w", float(v_snap.grid_l2_power_w or 0.0))
            .field("grid_l3_power_w", float(v_snap.grid_l3_power_w or 0.0))
            .time(now)
        )
        await self._write_api.write(bucket=self._bucket, record=[h_point, v_point])
    except Exception as exc:
        logger.warning("influx per-system write failed: %s", exc)
```

### Example 2: Decision Entry Dataclass
```python
# Add to backend/controller_model.py
from collections import deque

@dataclass
class DecisionEntry:
    """Single coordinator dispatch decision for the audit trail."""
    timestamp: str
    trigger: str
    huawei_role: str
    victron_role: str
    p_target_w: float
    huawei_allocation_w: float
    victron_allocation_w: float
    pool_status: str
    reasoning: str
```

### Example 3: Coordinator Integration Wiring in _run_cycle
```python
# At the end of _run_cycle(), after self._state = self._build_state(...)
# Fire-and-forget integration calls:
if self._writer is not None:
    try:
        await self._writer.write_system_state(self._state)
        await self._writer.write_per_system_metrics(h_snap, v_snap, h_cmd.role.value, v_cmd.role.value)
    except Exception as exc:
        logger.warning("influx write failed: %s", exc)

if self._ha_mqtt_client is not None:
    try:
        await self._ha_mqtt_client.publish(self._state)
    except Exception as exc:
        logger.warning("ha mqtt publish failed: %s", exc)
```

### Example 4: HA MQTT Binary Sensor Discovery (Platform-Aware)
```python
# If adding binary_sensor entities, use a different discovery topic:
def _discovery_topic(self, entity_id: str, platform: str = "sensor") -> str:
    return f"homeassistant/{platform}/{self._device_id}/{entity_id}/config"

# And for binary_sensor entities, the payload needs "payload_on"/"payload_off":
# binary_sensor entities use True/False from value_json
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Legacy Orchestrator writes InfluxDB | Coordinator has writer but never calls it | Phase 2 (coordinator migration) | InfluxDB metrics stopped flowing -- Phase 4 must fix |
| Legacy Orchestrator published HA MQTT | HA MQTT client connected but never invoked | Phase 2 (coordinator migration) | HA entities not updating -- Phase 4 must fix |
| Single `ems_system` measurement | Per-system `ems_huawei` + `ems_victron` | Phase 4 new | Enables per-battery Grafana dashboards |
| 7 HA MQTT entities | ~17-18 entities with per-system detail | Phase 4 new | Enables HA automations on per-system events |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-anyio |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `python -m pytest tests/test_coordinator.py tests/test_influx_writer.py tests/test_ha_mqtt_client.py tests/test_api.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INT-01 | EVCC hold reaches both controllers, both respond HOLDING | unit | `python -m pytest tests/test_coordinator.py -k evcc_hold -x` | Partial (basic hold test exists, needs structured logging verification) |
| INT-02 | Per-system state exposed via /api/devices and /api/state | unit | `python -m pytest tests/test_api.py -k "devices or state" -x` | Partial (/api/devices exists, role fields need test) |
| INT-03 | Graceful degradation for all integrations | unit | `python -m pytest tests/test_main_lifespan.py tests/test_coordinator.py -k "degrad or optional or fail" -x` | Partial (lifespan tests exist, mid-run failure tests needed) |
| INT-04 | Decision ring buffer populated on state change | unit | `python -m pytest tests/test_coordinator.py -k decision -x` | Wave 0 |
| INT-05 | Per-phase Victron dispatch with dead-band and fallback | unit | `python -m pytest tests/test_victron_controller.py -k "phase or discharge" -x` | Exists (basic per-phase test) |
| INT-06 | Per-battery charge targets routed correctly | unit | `python -m pytest tests/test_coordinator.py -k grid_charge -x` | Exists (grid charge test) |
| INT-07 | Per-system InfluxDB measurements written | unit | `python -m pytest tests/test_influx_writer.py -k per_system -x` | Wave 0 |
| INT-08 | HA MQTT discovery includes per-system entities | unit | `python -m pytest tests/test_ha_mqtt_client.py -k "discovery or entities" -x` | Partial (discovery test exists, new entities need test) |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_coordinator.py tests/test_influx_writer.py tests/test_ha_mqtt_client.py tests/test_api.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_coordinator.py` -- add decision logging tests (INT-04)
- [ ] `tests/test_influx_writer.py` -- add per-system metrics tests (INT-07)
- [ ] `tests/test_ha_mqtt_client.py` -- add new entity discovery tests (INT-08)
- [ ] `tests/test_api.py` -- add `/api/decisions` and expanded `/api/health` tests (INT-02, INT-04)

## Open Questions

1. **HA MQTT state payload source for new fields**
   - What we know: `_publish_state()` serializes `UnifiedPoolState` via `dataclasses.asdict()`. The coordinator produces `CoordinatorState` instead. CoordinatorState already has `huawei_role`, `victron_role`, `pool_status` fields.
   - What's unclear: Per-phase Victron power is NOT in CoordinatorState -- it's in the Victron ControllerSnapshot. Need to either extend CoordinatorState or build a custom payload.
   - Recommendation: Extend CoordinatorState with per-phase fields from Victron snapshot (simplest), or have the coordinator build a combined dict for HA MQTT.

2. **InfluxDB write_system_state compatibility**
   - What we know: `write_system_state()` takes `UnifiedPoolState` and accesses `.control_state.value`. CoordinatorState has `control_state` as a plain string, not an enum.
   - What's unclear: Will `Point.tag("control_state", state.control_state.value)` fail on a string?
   - Recommendation: Either make `write_system_state` accept CoordinatorState too (use `str(state.control_state)` which works for both enum and string), or add a separate `write_coordinator_state()` method.

## Sources

### Primary (HIGH confidence)
- `backend/coordinator.py` -- Full read, confirmed writer is injected but never called
- `backend/ha_mqtt_client.py` -- Full read, confirmed publish pattern and `_ENTITIES` list
- `backend/influx_writer.py` -- Full read, confirmed fire-and-forget pattern and Point construction
- `backend/api.py` -- Full read, confirmed existing endpoints and dependency injection pattern
- `backend/victron_controller.py` -- Full read, confirmed per-phase dispatch already implemented
- `backend/main.py` -- Full read, confirmed lifespan wiring and graceful degradation pattern
- `backend/controller_model.py` -- Full read, confirmed CoordinatorState fields
- `backend/notifier.py` -- Full read, confirmed async httpx pattern with cooldown

### Secondary (MEDIUM confidence)
- `backend/orchestrator.py` -- Partial read (lines 380-410), confirmed legacy InfluxDB write pattern

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, all libraries already in use
- Architecture: HIGH -- all patterns verified against existing codebase, gaps clearly identified
- Pitfalls: HIGH -- identified through direct code reading, not speculation
- Decision logging: MEDIUM -- ring buffer pattern is straightforward but InfluxDB tag/field split needs validation during implementation

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable domain, no external API changes expected)
