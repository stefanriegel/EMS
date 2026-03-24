# Phase 4: Integration & Monitoring - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

All external systems (EVCC, InfluxDB, HA, Telegram) integrate with the dual-battery coordinator architecture and every dispatch decision is traceable. Per-system state is exposed via API and HA MQTT discovery. Each integration degrades gracefully when unavailable.

Requirements: INT-01, INT-02, INT-03, INT-04, INT-05, INT-06, INT-07, INT-08

</domain>

<decisions>
## Implementation Decisions

### EVCC hold propagation to coordinator (INT-01)
- **D-01:** Phase 2 D-07 already decided: coordinator distributes EVCC hold signal to both controllers, each independently transitions to HOLDING. Phase 4 verifies this works end-to-end and adds structured logging.
- **D-02:** Coordinator reads `evcc_battery_mode` from `EvccMqttDriver` each cycle. When "hold" detected, coordinator sets both controllers to HOLDING role with reason "EVCC batteryMode=hold". When signal clears, controllers resume their SoC-based roles on next cycle.
- **D-03:** If EVCC MQTT is unavailable (optional dependency), coordinator treats battery mode as "normal" — never blocks dispatch on missing EVCC.

### Per-system API exposure (INT-02)
- **D-04:** Add `/api/devices` endpoint returning coordinator's `get_device_snapshot()` per system — includes role, SoC, power, setpoint, health, last error, per-phase power (Victron).
- **D-05:** Expand existing `/api/state` with per-system role fields (`huawei_role`, `victron_role`) and pool status (`NORMAL`, `DEGRADED`, `OFFLINE`) — backward compatible additions, no field removals.
- **D-06:** Add `/api/health` endpoint showing which integrations are active: `{ influxdb: true, evcc: false, ha_mqtt: true, telegram: false }`. Returns 200 always — health is informational, not a gate.

### Graceful degradation audit (INT-03)
- **D-07:** Audit all integration init paths in `main.py` lifespan — every external dependency must follow the proven pattern: try-connect, catch Exception, log WARNING, set to None, continue.
- **D-08:** Coordinator must never crash or stall when an optional dependency becomes unavailable mid-run (not just at startup). InfluxDB write failure, EVCC MQTT disconnect, HA REST timeout — all must be caught and logged without affecting the 5s control loop.
- **D-09:** Add integration status tracking in coordinator: `{ service: str, available: bool, last_error: str | None, last_seen: datetime | None }` per integration. Feeds the `/api/health` endpoint (D-06) and HA MQTT health entities (D-28).

### Decision transparency and structured logging (INT-04)
- **D-10:** Coordinator maintains an in-memory ring buffer of the last 100 dispatch decisions. Each entry is a dataclass with: `timestamp`, `trigger` (cycle/hold/slot_start/slot_end/failover), `huawei_role`, `victron_role`, `p_target_w`, `huawei_allocation_w`, `victron_allocation_w`, `reasoning` (human-readable text).
- **D-11:** Only log decisions when something changes — role transition, allocation shift > dead-band, failover event, hold signal change. Don't log every 5s cycle when nothing changed.
- **D-12:** New `ems_decision` InfluxDB measurement persists decisions: fields mirror the ring buffer entry, written on each decision event (not every cycle). Optional — skipped when InfluxDB is unavailable.
- **D-13:** Expose via `/api/decisions?limit=N` endpoint (default N=20, max 100). Returns JSON array of decision entries, newest first.

### Per-phase Victron dispatch (INT-05)
- **D-14:** Per-phase dispatch already exists in the orchestrator (`_apply_setpoints` lines 766-800). Phase 4 ensures this logic is preserved in the coordinator→VictronController path.
- **D-15:** VictronController receives total allocation from coordinator, then internally distributes across L1/L2/L3 based on measured per-phase grid power. Coordinator does NOT compute per-phase splits — that's Victron-specific logic owned by the controller.
- **D-16:** Phase-level dead-band: 20W per phase (existing value). If measured grid phase power is below dead-band, skip that phase's setpoint write to reduce Modbus traffic.
- **D-17:** When per-phase grid power is unavailable (Victron reports None for `grid_lN_power_w`), fall back to equal 3-way split: `total_allocation / 3.0` per phase.

### Per-battery nightly charge targets (INT-06)
- **D-18:** Scheduler already produces per-battery `ChargeSlot` objects with independent `target_soc_pct` and `grid_charge_power_w`. Phase 4 verifies coordinator correctly consumes these and routes to each controller.
- **D-19:** Coordinator detects active charge slots per battery independently — Huawei can be in GRID_CHARGE while Victron is in PRIMARY_DISCHARGE if their slot windows differ.
- **D-20:** Expose per-battery charge targets in `/api/optimization/schedule` response — already partially there, verify both `huawei_target_soc_pct` and `victron_target_soc_pct` are present with reasoning.

### Per-system InfluxDB metrics (INT-07)
- **D-21:** Add two new measurements: `ems_huawei` and `ems_victron`. Fields per measurement: `soc_pct`, `power_w`, `setpoint_w`, `role` (string tag), `available` (bool tag), `charge_headroom_w`.
- **D-22:** Keep existing `ems_system` measurement unchanged for backward compatibility — combined metrics continue flowing for existing Grafana dashboards.
- **D-23:** Victron measurement includes additional per-phase fields: `l1_power_w`, `l2_power_w`, `l3_power_w`, `grid_l1_power_w`, `grid_l2_power_w`, `grid_l3_power_w`.
- **D-24:** Write frequency matches existing pattern: once per control cycle (5s) alongside `ems_system`. All writes fire-and-forget — catch Exception, log WARNING, never block.
- **D-25:** Add `ems_decision` measurement (see D-12) for decision log persistence.

### HA MQTT discovery per-system entities (INT-08)
- **D-26:** Add per-system role sensors: `huawei_role` ("PRIMARY_DISCHARGE", "HOLDING", etc.), `victron_role`. Device class: None (enum-like text sensor).
- **D-27:** Add per-system power sensors: `huawei_power` (W), `victron_power` (W). Complement existing setpoint sensors with actual measured power.
- **D-28:** Add per-system availability binary sensors: `huawei_online`, `victron_online`. Device class: `connectivity`.
- **D-29:** Add pool status sensor: `pool_status` ("NORMAL", "DEGRADED", "OFFLINE"). Provides single-glance system health.
- **D-30:** Add Victron per-phase power sensors: `victron_l1_power`, `victron_l2_power`, `victron_l3_power` (W). Useful for phase imbalance monitoring in HA.
- **D-31:** Keep all 7 existing entities unchanged. Total entity count after phase: ~17-18 sensors. All entities share the same device registration (`ems_dual_battery` or existing device ID).
- **D-32:** Entity state updates piggyback on the existing per-cycle publish call — one MQTT message with JSON payload containing all entity values, unchanged pattern.

### Claude's Discretion
- Decision ring buffer dataclass structure and field naming
- InfluxDB measurement field types (float vs int for power values)
- HA MQTT entity `unique_id` naming scheme for new entities
- Internal method decomposition for integration health tracking
- Test fixture organization for integration mocking
- Whether `/api/health` returns flat object or grouped by category
- Exact `ems_decision` InfluxDB tag vs field split

</decisions>

<specifics>
## Specific Ideas

- The coordinator's `get_device_snapshot()` (controller_model.py) already returns per-system state — API just needs to expose it. No new data computation needed for INT-02.
- Decision logging should feel like a structured audit trail, not verbose debug output. Only log when roles change, allocations shift significantly, or events occur (hold/failover/slot transitions).
- HA MQTT entities should mirror what's useful in HA automations: "trigger automation when Huawei goes offline", "show Victron role on dashboard card", "alert when pool degrades".
- The existing `_ENTITIES` list pattern in `ha_mqtt_client.py` makes adding new entities mechanical — just extend the list and map fields.
- Per-phase Victron dispatch is the only INT-05 item that touches the hot control loop. Keep it lightweight — no per-phase InfluxDB writes inside the 5s loop, batch them.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Coordinator and controllers (Phase 2+3 output — primary modification targets)
- `backend/coordinator.py` — Coordinator class with role assignment, allocation, control loop. Add decision logging, integration health tracking, EVCC hold verification.
- `backend/controller_model.py` — `CoordinatorState`, `ControllerSnapshot`, `BatteryRole`, `PoolStatus` enums and dataclasses. Source for API and HA MQTT entity data.

### API layer
- `backend/api.py` — REST endpoints. Add `/api/devices`, `/api/decisions`, `/api/health`. Expand `/api/state` with role fields.
- `backend/ws_manager.py` — WebSocket state broadcasts. May need to include decision events.

### External integrations (audit + enhance)
- `backend/evcc_mqtt_driver.py` — EVCC MQTT driver (243 lines). `evcc_battery_mode` field for hold signal. Verify coordinator reads this correctly.
- `backend/influx_writer.py` — InfluxDB writer. Add `write_per_system_metrics()` for `ems_huawei`/`ems_victron` and `write_decision()` for `ems_decision`.
- `backend/influx_reader.py` — InfluxDB reader. May need per-system query methods for future dashboard.
- `backend/ha_mqtt_client.py` — HA MQTT discovery. Extend `_ENTITIES` list with per-system role, power, availability, and per-phase sensors.
- `backend/notifier.py` — Telegram notifier. Verify optional-dependency pattern, add decision-triggered alerts (e.g., failover notification).

### Scheduler (verify integration)
- `backend/scheduler.py` — Nightly charge schedule with per-battery `ChargeSlot`. Verify coordinator consumption path.
- `backend/schedule_models.py` — `ChargeSlot`, `ChargeSchedule`, `OptimizationReasoning` dataclasses. Reference for decision log structure.

### Orchestrator (legacy, reference only)
- `backend/orchestrator.py` — Legacy unified orchestrator. Per-phase Victron dispatch logic (lines 766-800) is the reference for INT-05. EVCC hold handling (lines 566-570) is the reference for INT-01.

### Configuration
- `backend/config.py` — All config dataclasses. May need `IntegrationHealthConfig` or similar for health tracking intervals.

### Lifespan wiring
- `backend/main.py` — `lifespan()` function wires all services. Audit all integration init paths for graceful degradation (INT-03).

### Driver models (read-only context)
- `backend/drivers/victron_models.py` — `VictronPhaseData` (L1/L2/L3 power) and `VictronSystemData` (grid per-phase power) for INT-05 and INT-07.
- `backend/drivers/huawei_models.py` — `HuaweiBatteryData` for per-system metrics fields.

### Tests
- `tests/test_coordinator.py` — Coordinator tests to extend with decision logging, EVCC hold, integration health.
- `tests/test_influx_writer.py` — InfluxDB writer tests (if present) to extend with per-system metrics.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **Coordinator `get_device_snapshot()`** — Already returns per-system state as `ControllerSnapshot`. Direct input for `/api/devices` endpoint (D-04).
- **`_ENTITIES` list in `ha_mqtt_client.py`** — Mechanical pattern for adding new HA entities. Extend list, map to UnifiedPoolState fields.
- **`OptimizationReasoning` dataclass** — Reference structure for decision log entries. Similar pattern: text + numeric context fields.
- **Fire-and-forget InfluxDB write pattern** — `influx_writer.py` line 98-99: catch Exception, log WARNING, never raise. Reuse for all new measurements.
- **Graceful degradation pattern in `main.py`** — Try-connect, catch, log, set None. Apply consistently to all integration init paths.
- **Per-phase dispatch logic** — Orchestrator lines 766-800: phase-level grid power balancing for Victron. Move to VictronController.

### Established Patterns
- **Optional dependency injection** — Services injected via `set_*()` methods on orchestrator/coordinator. None means disabled.
- **Dataclass state snapshots** — All state is immutable dataclasses, never mutable dicts. New decision log entries follow same pattern.
- **Thread-safe MQTT callbacks** — `loop.call_soon_threadsafe()` for paho→asyncio bridging. All MQTT integrations use this.
- **JSON-serializable state** — All API responses derive from dataclasses with simple types. New endpoints follow same convention.
- **Per-cycle metrics write** — InfluxDB writer called once per control cycle with current state. New per-system metrics piggyback on same call.

### Integration Points
- **Coordinator → API** — `coordinator.get_state()` and `coordinator.get_device_snapshot()` feed API endpoints. Add `coordinator.get_decisions()` for decision log.
- **Coordinator → InfluxDB** — Currently writes via `influx_writer.write_system_metrics()`. Add `write_per_system_metrics()` and `write_decision()`.
- **Coordinator → HA MQTT** — `ha_mqtt_client.publish_state()` sends entity values. Extend with new entity fields from coordinator state.
- **Coordinator → Notifier** — `notifier.send_message()` for alerts. Add decision-triggered notifications (failover, degradation).
- **main.py lifespan → all integrations** — Central wiring point. Audit for consistent graceful degradation.

</code_context>

<deferred>
## Deferred Ideas

- **Grafana dashboard templates** — Pre-built dashboards for per-system metrics. Deferred to v2 (ECO-02) once metric schema stabilizes.
- **Decision log retention policy** — Auto-purge old InfluxDB decision records. Not needed until data volume becomes an issue.
- **WebSocket decision streaming** — Push decision events to frontend in real-time via WebSocket. Phase 5 (dashboard) will consume the ring buffer via REST; WebSocket streaming is a future enhancement.
- **Integration auto-recovery** — Periodically retry failed integrations (e.g., reconnect InfluxDB after network restore). Currently, integrations that fail at startup stay None until restart. Future enhancement.
- **Per-phase tariff awareness** — Germany doesn't have per-phase tariffs, so per-phase dispatch is load-balancing only. If feed-in tariff per phase ever becomes relevant, revisit (ADV-01).

</deferred>

---

*Phase: 04-integration-monitoring*
*Context gathered: 2026-03-22*
