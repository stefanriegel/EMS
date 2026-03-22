# Phase 2: Independent Controllers & Coordinator - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Each battery system operates through its own controller with the coordinator allocating demand — no oscillation, no cross-system coupling. Replace the unified Orchestrator with per-battery controllers (simpler states: DISCHARGE, CHARGE, HOLD, GRID_CHARGE) and a coordinator that owns role assignment (PRIMARY vs SECONDARY), P_target computation, hysteresis, and scheduling.

Requirements: CTRL-01, CTRL-02, CTRL-03, CTRL-04, CTRL-05, CTRL-06, CTRL-07, CTRL-08

</domain>

<decisions>
## Implementation Decisions

### Role transition behavior
- **D-01:** Secondary battery activates when SoC gap narrows below 5% — above 5% gap, only PRIMARY discharges; below 5%, both discharge proportionally (converging toward equal drawdown)
- **D-02:** Role swap uses hysteresis band — SECONDARY must be >3% SoC higher than current PRIMARY to trigger a swap. Prevents flapping when SoCs are close.
- **D-03:** During PV surplus (CHARGING), fill the smaller battery (Huawei 30 kWh) first, then the larger (Victron 64 kWh) — faster to reach usable capacity across the pool
- **D-04:** When one battery reaches 95% SoC (full), it enters HOLDING and all surplus routes to the other battery (current overflow routing behavior preserved)

### Coordinator↔Controller boundary
- **D-05:** Coordinator computes P_target from grid meter readings, then allocates watts to each controller — controllers never read the grid meter directly
- **D-06:** Coordinator pre-applies hysteresis before sending targets to controllers — controllers write what they're told without additional smoothing. Per-system dead-bands (Huawei ~300-500W, Victron ~100-200W per CTRL-03) are enforced by the coordinator, not the controllers.
- **D-07:** EVCC hold signal (batteryMode=hold) is passed by coordinator to each controller — each controller independently transitions to DISCHARGE_LOCKED. Coordinator distributes the signal, controllers own the response.
- **D-08:** Coordinator owns the scheduler — detects active charge slots, computes per-battery charge targets, and tells each controller "charge at X watts from grid". Controllers execute the charge (sign flip, rate limiting, target SoC check).

### Failure isolation
- **D-09:** Safe state entry after 3 consecutive failed reads (3 cycles × 5s = 15s) — tolerates brief network glitches without killing output
- **D-10:** When one battery goes offline, coordinator instantly allocates full P_target to the surviving system on the next cycle — accepts possible power spike, prioritizes continuity over smoothness
- **D-11:** When a failed battery comes back online, immediate re-entry at its SoC-proportional share — coordinator recalculates split instantly, no probation period
- **D-12:** Coordinator actively triggers reconnect after timeout — calls `driver.connect()` periodically (not just passive monitoring). Driver's internal `_with_reconnect()` handles per-read reconnection; coordinator handles session-level reconnection.

### State machine design
- **D-13:** One state machine per controller + coordinator owns role assignment. Controllers have simpler states (DISCHARGE, CHARGE, HOLD, GRID_CHARGE). Coordinator decides who is PRIMARY_DISCHARGE vs SECONDARY_DISCHARGE and tells them.
- **D-14:** New enums replace existing `ControlState` — `BatteryRole` per controller (PRIMARY_DISCHARGE, SECONDARY_DISCHARGE, CHARGING, HOLDING, GRID_CHARGE) and `PoolStatus` for API health (NORMAL, DEGRADED, OFFLINE)
- **D-15:** API returns both controller states separately — frontend decides how to display. New API contract with per-system visibility (aligns with UI-01, UI-04 in Phase 5).
- **D-16:** Coordinator debounces all state transitions — controllers propose states freely, coordinator applies debounce (2 cycles) before confirming role assignments. Prevents rapid role flapping.

### Stale data detection (deferred from Phase 1 D-18)
- **D-17:** Each controller checks its driver's `timestamp` field — data older than `2 * loop_interval_s` (10s) is treated as stale, triggering the 3-consecutive-failure counter from D-09

### Claude's Discretion
- Internal class structure (single file vs module per controller)
- Test fixture organization for controller and coordinator unit tests
- Exact method signatures for controller↔coordinator communication
- Whether coordinator uses an event loop or direct method calls per cycle
- Config dataclass structure for per-controller settings

</decisions>

<specifics>
## Specific Ideas

- The current orchestrator's `_compute_setpoints()` (lines 563-715) contains most of the logic that will split between coordinator (P_target, role assignment, overflow routing) and controllers (setpoint execution)
- Keep the 5s control loop interval — coordinator polls both controllers each cycle
- Huawei controller calls system-specific methods (`read_master`, `read_battery`, `write_max_discharge_power`) — NOT the generic BatteryDriver protocol
- Victron controller calls `read_system_state()` and `write_ac_power_setpoint(phase, watts)` — can use the generic BatteryDriver protocol
- The coordinator should never directly call driver methods — always through the controller (CTRL-02: "coordinator never directly writes to hardware")
- Preserve the existing `_make_state()` / `get_state()` API pattern — coordinator builds state from controller snapshots

</specifics>

<canonical_refs>
## Canonical References

### Current implementation (to be refactored)
- `backend/orchestrator.py` — Current unified orchestrator (1053 lines). Lines 563-715: `_compute_setpoints()` contains role logic. Lines 723-833: hysteresis/ramp. Lines 944-981: debounce state machine. Lines 835-890: GRID_CHARGE slot logic.
- `backend/unified_model.py` — `UnifiedPoolState` and `ControlState` enum (210 lines). Will be replaced by per-controller state + coordinator pool state.
- `backend/config.py` — `OrchestratorConfig` (loop_interval, hysteresis, debounce, stale_threshold, max_offline) and `SystemConfig` (min/max SoC per system)

### Driver contracts (from Phase 1)
- `backend/drivers/protocol.py` — LifecycleDriver + BatteryDriver Protocol classes
- `backend/drivers/huawei_driver.py` — System-specific methods: `read_master()`, `read_battery()`, `write_max_discharge_power()`, `write_ac_charging()`, `write_max_charge_power()`
- `backend/drivers/victron_driver.py` — Modbus TCP driver: `read_system_state()`, `write_ac_power_setpoint(phase, watts)`

### Integration points
- `backend/main.py` — `lifespan()` wires Orchestrator with drivers, scheduler, EVCC, notifier. Must be updated to wire coordinator + controllers.
- `backend/api.py` — `/state`, `/health`, `/api/devices` endpoints consume `orchestrator.get_state()`. Must be updated for new per-controller state shape.
- `backend/evcc_mqtt_driver.py` — `evcc_battery_mode` field consumed by orchestrator. Coordinator will read this and distribute to controllers.
- `backend/scheduler.py` — Produces `ChargeSchedule` with per-battery `ChargeSlot` objects. Coordinator will consume these.

### Tests
- `tests/test_orchestrator.py` — Existing orchestrator tests (if any) to refactor for controller/coordinator split
- `tests/drivers/test_huawei_driver.py` — Huawei driver test patterns and fixtures
- `tests/drivers/test_victron_driver.py` — Victron driver test patterns and fixtures

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `Orchestrator._compute_setpoints()` lines 606-624 — P_target computation from grid meter / Huawei master fallback → extract to coordinator
- `Orchestrator._compute_setpoints()` lines 645-672 — SoC-weighted capacity split with partial availability → extract to coordinator allocation logic
- `Orchestrator._apply_setpoints()` lines 739-798 — Hysteresis dead-band patterns (200W Huawei, 20W per-phase Victron) → move to coordinator
- `Orchestrator._transition_state()` lines 944-981 — Debounce state machine (pending_state, pending_cycles, commit after N) → move to coordinator
- `Orchestrator._apply_grid_charge_setpoints()` lines 835-867 — Grid charge sign flip and per-battery routing → extract to coordinator
- `Orchestrator._cleanup_grid_charge()` lines 869-890 — Slot exit cleanup → per-controller responsibility
- Sentinel value factories (`_huawei_sentinel`, `_victron_sentinel`) lines 56-96 — reuse for controller offline state
- `_active_charge_slot()` lines 257-284 — Slot detection logic → move to coordinator

### Established Patterns
- Async context manager for drivers — controllers will wrap their driver
- Dataclass-based state snapshots — controllers will produce per-system snapshots
- Thread-safe config updates via GIL — coordinator will forward config changes to controllers
- Dependency injection in lifespan — coordinator receives controllers, controllers receive drivers

### Integration Points
- `api.py` calls `orchestrator.get_state()` → will change to `coordinator.get_state()` returning per-controller states
- `ws_manager.py` broadcasts state updates → new state shape
- `influx_writer.py` writes `UnifiedPoolState` metrics → needs per-system metrics (Phase 4, but data contract set here)
- `main.py` lifespan wires everything → new wiring: drivers → controllers → coordinator → API

</code_context>

<deferred>
## Deferred Ideas

- Per-system InfluxDB metrics (separate measurements for Huawei and Victron) — Phase 4 (INT-07)
- Decision transparency logging (structured WHY for each dispatch) — Phase 4 (INT-04)
- Dashboard per-system visibility — Phase 5 (UI-01, UI-04)
- Tariff-aware charge rate optimization (stagger charging in short windows) — Phase 3 (OPT-03)
- Time-of-day min-SoC profiles — Phase 3 (OPT-05)

</deferred>

---

*Phase: 02-independent-controllers-coordinator*
*Context gathered: 2026-03-22*
