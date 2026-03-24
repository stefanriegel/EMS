# Phase 2: Independent Controllers & Coordinator - Research

**Researched:** 2026-03-22
**Domain:** Battery control system architecture, async state machines, coordinator pattern
**Confidence:** HIGH

## Summary

Phase 2 replaces the monolithic `Orchestrator` (1053 lines) with three components: a `HuaweiController`, a `VictronController`, and a `Coordinator` that allocates demand between them. The existing orchestrator already contains all the logic needed -- the task is decomposition, not invention. The `_compute_setpoints()` method (lines 563-715) splits into coordinator-level P_target computation and role assignment, while `_apply_setpoints()` (lines 723-833), `_apply_grid_charge_setpoints()` (lines 835-867), and `_cleanup_grid_charge()` (lines 869-890) move into per-controller execution. The `_transition_state()` debounce machine (lines 944-981) stays in the coordinator.

The existing driver contracts from Phase 1 are stable: HuaweiDriver exposes `read_master()`, `read_battery()`, `write_max_discharge_power()`, `write_ac_charging()`, `write_max_charge_power()` (all async). VictronDriver exposes `read_system_state()` and `write_ac_power_setpoint(phase, watts)` (both async). Controllers wrap these directly -- no abstraction layer needed between controller and driver.

**Primary recommendation:** Decompose the orchestrator into three files (`huawei_controller.py`, `victron_controller.py`, `coordinator.py`) plus a new model file (`controller_model.py`). Keep the 5s control loop in the coordinator. Each controller produces a typed snapshot per cycle; the coordinator consumes snapshots, computes allocation, and sends commands back to controllers. The API layer switches from `orchestrator.get_state()` to `coordinator.get_state()`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Secondary battery activates when SoC gap narrows below 5% -- above 5% gap, only PRIMARY discharges; below 5%, both discharge proportionally (converging toward equal drawdown)
- **D-02:** Role swap uses hysteresis band -- SECONDARY must be >3% SoC higher than current PRIMARY to trigger a swap. Prevents flapping when SoCs are close.
- **D-03:** During PV surplus (CHARGING), fill the smaller battery (Huawei 30 kWh) first, then the larger (Victron 64 kWh) -- faster to reach usable capacity across the pool
- **D-04:** When one battery reaches 95% SoC (full), it enters HOLDING and all surplus routes to the other battery (current overflow routing behavior preserved)
- **D-05:** Coordinator computes P_target from grid meter readings, then allocates watts to each controller -- controllers never read the grid meter directly
- **D-06:** Coordinator pre-applies hysteresis before sending targets to controllers -- controllers write what they're told without additional smoothing. Per-system dead-bands (Huawei ~300-500W, Victron ~100-200W per CTRL-03) are enforced by the coordinator, not the controllers.
- **D-07:** EVCC hold signal (batteryMode=hold) is passed by coordinator to each controller -- each controller independently transitions to DISCHARGE_LOCKED. Coordinator distributes the signal, controllers own the response.
- **D-08:** Coordinator owns the scheduler -- detects active charge slots, computes per-battery charge targets, and tells each controller "charge at X watts from grid". Controllers execute the charge (sign flip, rate limiting, target SoC check).
- **D-09:** Safe state entry after 3 consecutive failed reads (3 cycles x 5s = 15s) -- tolerates brief network glitches without killing output
- **D-10:** When one battery goes offline, coordinator instantly allocates full P_target to the surviving system on the next cycle -- accepts possible power spike, prioritizes continuity over smoothness
- **D-11:** When a failed battery comes back online, immediate re-entry at its SoC-proportional share -- coordinator recalculates split instantly, no probation period
- **D-12:** Coordinator actively triggers reconnect after timeout -- calls `driver.connect()` periodically (not just passive monitoring). Driver's internal `_with_reconnect()` handles per-read reconnection; coordinator handles session-level reconnection.
- **D-13:** One state machine per controller + coordinator owns role assignment. Controllers have simpler states (DISCHARGE, CHARGE, HOLD, GRID_CHARGE). Coordinator decides who is PRIMARY_DISCHARGE vs SECONDARY_DISCHARGE and tells them.
- **D-14:** New enums replace existing `ControlState` -- `BatteryRole` per controller (PRIMARY_DISCHARGE, SECONDARY_DISCHARGE, CHARGING, HOLDING, GRID_CHARGE) and `PoolStatus` for API health (NORMAL, DEGRADED, OFFLINE)
- **D-15:** API returns both controller states separately -- frontend decides how to display. New API contract with per-system visibility (aligns with UI-01, UI-04 in Phase 5).
- **D-16:** Coordinator debounces all state transitions -- controllers propose states freely, coordinator applies debounce (2 cycles) before confirming role assignments. Prevents rapid role flapping.
- **D-17:** Each controller checks its driver's `timestamp` field -- data older than `2 * loop_interval_s` (10s) is treated as stale, triggering the 3-consecutive-failure counter from D-09

### Claude's Discretion
- Internal class structure (single file vs module per controller)
- Test fixture organization for controller and coordinator unit tests
- Exact method signatures for controller-to-coordinator communication
- Whether coordinator uses an event loop or direct method calls per cycle
- Config dataclass structure for per-controller settings

### Deferred Ideas (OUT OF SCOPE)
- Per-system InfluxDB metrics (separate measurements for Huawei and Victron) -- Phase 4 (INT-07)
- Decision transparency logging (structured WHY for each dispatch) -- Phase 4 (INT-04)
- Dashboard per-system visibility -- Phase 5 (UI-01, UI-04)
- Tariff-aware charge rate optimization (stagger charging in short tariff windows) -- Phase 3 (OPT-03)
- Time-of-day min-SoC profiles -- Phase 3 (OPT-05)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CTRL-01 | Each battery system has a dedicated controller with its own state machine, hysteresis, and debounce | Controllers own state (DISCHARGE, CHARGE, HOLD, GRID_CHARGE); coordinator owns debounce per D-16 and hysteresis per D-06 |
| CTRL-02 | Coordinator allocates demand across controllers without directly writing to hardware | Coordinator sends `(command, watts)` to controllers; controllers call driver methods |
| CTRL-03 | Per-system hysteresis dead-band: Huawei ~300-500W, Victron ~100-200W (configurable) | Coordinator applies dead-bands before sending to controllers per D-06 |
| CTRL-04 | Each controller enters safe state independently on communication loss (zero-power, no cross-system impact) | 3-consecutive-failure counter per D-09; safe state = zero-power write to driver |
| CTRL-05 | Total household power remains stable when coordinator reassigns load between systems | D-10 accepts spike for continuity; coordinator recomputes split each cycle from grid meter |
| CTRL-06 | Dynamic role assignment (PRIMARY_DISCHARGE, SECONDARY_DISCHARGE, CHARGING, HOLDING, GRID_CHARGE) based on SoC, tariff, PV | BatteryRole enum per D-14; SoC-based priority per D-01/D-02/D-08 |
| CTRL-07 | Anti-oscillation ramps: soft-start/soft-stop with configurable ramp rate per system | Ramp logic in coordinator allocation; existing hysteresis pattern from orchestrator lines 739-798 |
| CTRL-08 | SoC-based discharge priority: higher-SoC system discharges first | D-01 and D-02 define exact threshold and hysteresis band |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib `asyncio` | 3.12+ | Async control loop, task management | Already used in orchestrator; no additional deps |
| Python stdlib `enum` | 3.12+ | BatteryRole and PoolStatus enums | Existing pattern: `ControlState(str, Enum)` |
| Python stdlib `dataclasses` | 3.12+ | Controller state snapshots, config | Existing pattern throughout codebase |
| Python stdlib `logging` | 3.12+ | Module-level loggers | Existing pattern: `logger = logging.getLogger(__name__)` |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | 8+ | Controller and coordinator unit tests | All tests |
| pytest-anyio | installed | Async test support | Async controller/coordinator tests |
| pytest-mock | installed | Mock drivers in tests | Isolate controllers from real hardware |

No new dependencies required. This phase is pure refactoring of existing logic into a new architecture using only stdlib.

## Architecture Patterns

### Recommended Project Structure
```
backend/
    coordinator.py          # Coordinator class (P_target, role assignment, allocation, debounce)
    huawei_controller.py    # HuaweiController (wraps HuaweiDriver, executes setpoints)
    victron_controller.py   # VictronController (wraps VictronDriver, executes setpoints)
    controller_model.py     # BatteryRole, PoolStatus enums + ControllerState/CoordinatorState dataclasses
    orchestrator.py         # RETAINED but deprecated -- replaced by coordinator in main.py lifespan
    unified_model.py        # RETAINED for backward compat -- CoordinatorState replaces UnifiedPoolState
tests/
    test_huawei_controller.py
    test_victron_controller.py
    test_coordinator.py
    test_controller_model.py
```

### Pattern 1: Controller as Driver Wrapper with State Snapshot
**What:** Each controller wraps exactly one driver. It reads the driver, checks staleness, manages failure counting, and produces a typed `ControllerSnapshot` per cycle. It also accepts commands from the coordinator and executes them against the driver.
**When to use:** Every control cycle.
**Example:**
```python
@dataclass
class ControllerSnapshot:
    """Per-cycle state snapshot produced by a controller."""
    soc_pct: float
    power_w: float               # current power (positive=charge, negative=discharge)
    available: bool
    role: BatteryRole
    consecutive_failures: int
    timestamp: float             # time.monotonic()
    # Huawei-specific (None for Victron)
    max_charge_power_w: int | None
    max_discharge_power_w: int | None
    charge_headroom_w: float
    # Victron-specific (None for Huawei)
    grid_power_w: float | None
    grid_l1_power_w: float | None
    grid_l2_power_w: float | None
    grid_l3_power_w: float | None


@dataclass
class ControllerCommand:
    """Command from coordinator to controller."""
    role: BatteryRole
    target_watts: float          # positive = charge, negative = discharge
    evcc_hold: bool = False      # True when batteryMode=hold
```

### Pattern 2: Coordinator Direct-Call Per Cycle
**What:** Coordinator runs the 5s async loop. Each cycle: (1) ask each controller to poll, (2) collect snapshots, (3) compute P_target and allocation, (4) send commands to controllers, (5) build coordinator state for API.
**When to use:** This is the main control loop pattern.
**Example:**
```python
class Coordinator:
    async def _run_cycle(self) -> None:
        # 1. Poll
        h_snap = await self._huawei_ctrl.poll()
        v_snap = await self._victron_ctrl.poll()

        # 2. Compute P_target from grid meter
        p_target = self._compute_p_target(h_snap, v_snap)

        # 3. Assign roles and allocate
        h_cmd, v_cmd = self._allocate(p_target, h_snap, v_snap)

        # 4. Apply hysteresis, then send
        h_cmd, v_cmd = self._apply_hysteresis(h_cmd, v_cmd)
        await self._huawei_ctrl.execute(h_cmd)
        await self._victron_ctrl.execute(v_cmd)

        # 5. Build state
        self._current_state = self._build_state(h_snap, v_snap, h_cmd, v_cmd)
```

### Pattern 3: Failure Counter with Stale Detection (D-09, D-17)
**What:** Each controller maintains `_consecutive_failures: int`. On each poll: check driver timestamp staleness (> 2 * loop_interval_s), increment on failure, reset on success. At 3 failures, controller enters safe state (writes zero to driver) and reports `available=False`.
**When to use:** Every controller poll.
**Example:**
```python
async def poll(self) -> ControllerSnapshot:
    try:
        data = await self._driver.read_battery()  # or read_system_state()
        # Stale check (D-17)
        if time.monotonic() - data.timestamp > 2 * self._loop_interval_s:
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0
            self._last_data = data
    except Exception:
        self._consecutive_failures += 1

    if self._consecutive_failures >= 3:
        await self._apply_safe_state()
        return self._make_snapshot(available=False)

    return self._make_snapshot(available=True)
```

### Pattern 4: SoC-Based Role Assignment (D-01, D-02, D-08)
**What:** Coordinator assigns PRIMARY_DISCHARGE to the higher-SoC system. SECONDARY activates only when gap < 5%. Swap requires >3% hysteresis to prevent flapping.
**When to use:** Every allocation cycle during discharge.
**Example:**
```python
def _assign_discharge_roles(
    self, h_soc: float, v_soc: float
) -> tuple[BatteryRole, BatteryRole]:
    gap = abs(h_soc - v_soc)
    higher = "huawei" if h_soc >= v_soc else "victron"

    # Swap hysteresis: only swap if new candidate is >3% higher than current PRIMARY
    if self._current_primary == "huawei" and higher == "victron":
        if v_soc - h_soc <= 3.0:
            higher = "huawei"  # no swap, insufficient margin
    elif self._current_primary == "victron" and higher == "huawei":
        if h_soc - v_soc <= 3.0:
            higher = "victron"

    self._current_primary = higher

    if higher == "huawei":
        h_role = BatteryRole.PRIMARY_DISCHARGE
        v_role = BatteryRole.SECONDARY_DISCHARGE if gap < 5.0 else BatteryRole.HOLDING
    else:
        v_role = BatteryRole.PRIMARY_DISCHARGE
        h_role = BatteryRole.SECONDARY_DISCHARGE if gap < 5.0 else BatteryRole.HOLDING

    return h_role, v_role
```

### Anti-Patterns to Avoid
- **Controllers reading grid meter directly:** Violates D-05. Only the coordinator reads grid power (from Victron snapshot's `grid_power_w` or Huawei master fallback).
- **Controllers applying hysteresis:** Violates D-06. Controllers write exactly what the coordinator tells them.
- **Coordinator calling driver methods directly:** Violates CTRL-02. Always go through controller.execute().
- **Shared mutable state between controllers:** Each controller owns its driver and failure counter. No cross-references.
- **Proportional setpoint splitting:** Explicitly out of scope (REQUIREMENTS.md). The old SoC-weighted capacity split is replaced by role-based allocation (PRIMARY gets demand, SECONDARY gets remainder when gap < 5%).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async control loop timing | Custom timer/scheduler | `asyncio.sleep()` with monotonic clock | Already proven in orchestrator._run() |
| State machine debounce | Ad-hoc counter in each controller | Single debounce implementation in coordinator | D-16: coordinator owns debounce; duplicating it creates divergence |
| Thread-safe config updates | Locks/mutexes | GIL-protected single-reference assignment | Existing pattern (orchestrator.sys_config setter), works for single-writer |
| Sentinel/offline data | Special None-handling | Zero-valued dataclass factories (`_huawei_sentinel()`, `_victron_sentinel()`) | Already implemented, handles all nullable fields correctly |

**Key insight:** This phase is decomposition of existing working code, not creation of new algorithms. The orchestrator already handles all the edge cases (overflow routing, phase imbalance, ESS mode guard, grid charge sign flip). The risk is in incorrectly splitting logic, not in missing logic.

## Common Pitfalls

### Pitfall 1: Breaking the Sign Convention During Decomposition
**What goes wrong:** Huawei uses positive=discharge for setpoints. Victron uses negative=discharge (export) for per-phase setpoints. Grid charge uses positive=import for Victron. Mixing these up during refactoring causes batteries to charge when they should discharge or vice versa.
**Why it happens:** The orchestrator handles sign conversion inline (lines 778-781, 856). When splitting into controllers, the conversion boundary shifts.
**How to avoid:** Define the coordinator-to-controller interface as: positive=charge, negative=discharge (matching the canonical sign convention DRV-06). Each controller converts to its driver's convention internally.
**Warning signs:** Unexpected grid import during discharge mode; batteries charging from grid outside scheduled windows.

### Pitfall 2: Losing the Victron ESS Mode Guard
**What goes wrong:** Victron setpoint writes silently fail when ESS mode is 0 or 1 (modes that don't honor AcPowerSetpoint). The orchestrator guards this at line 756-762.
**Why it happens:** Easy to forget this guard during split because it's a Victron-specific check buried in `_apply_setpoints()`.
**How to avoid:** Move the ESS mode guard into `VictronController.execute()`. Log a warning and skip the write, same as current behavior.
**Warning signs:** Victron appears to accept setpoints but doesn't change power output.

### Pitfall 3: GRID_CHARGE State Cleanup Not Firing
**What goes wrong:** When exiting a grid charge slot, Huawei AC charging must be disabled and Victron setpoints zeroed. The orchestrator tracks `_prev_control_state` to detect slot exit (lines 752-753).
**Why it happens:** With separate controllers, the "previous state" tracking must live in the coordinator. If controllers track their own previous role, they may miss the cleanup trigger.
**How to avoid:** Coordinator detects role transitions and sends explicit cleanup commands to controllers when transitioning out of GRID_CHARGE.
**Warning signs:** Huawei continues charging from grid after the cheap tariff window ends.

### Pitfall 4: API Contract Change Breaking Frontend
**What goes wrong:** The frontend consumes `UnifiedPoolState` via WebSocket and `/api/state`. Changing the state shape without backward compatibility breaks the dashboard.
**Why it happens:** D-15 requires per-system visibility in the API, but the frontend update is deferred to Phase 5.
**How to avoid:** The new `CoordinatorState` (or whatever replaces `UnifiedPoolState`) should include all existing fields at the top level PLUS new per-system fields. This way the existing frontend keeps working. Mark old fields as deprecated for Phase 5 removal.
**Warning signs:** Dashboard shows stale/missing data after deploying Phase 2.

### Pitfall 5: Debounce Interaction with Role Assignment
**What goes wrong:** Coordinator debounces role transitions (D-16: 2 cycles). But if SoC changes rapidly (e.g., PV cloud transient), debounce may delay a necessary role swap, causing one battery to over-discharge.
**Why it happens:** The existing orchestrator debounces state transitions, but the new role assignment adds a second layer (PRIMARY/SECONDARY) that also needs debounce.
**How to avoid:** Debounce the `BatteryRole` assignment for each controller independently within the coordinator. Use the same 2-cycle mechanism from `_transition_state()`. Safe-state transitions (HOLD due to comms loss) bypass debounce -- they must be instant.
**Warning signs:** Rapid role flapping in logs; SoC divergence when PV is intermittent.

### Pitfall 6: Huawei Master Data Missing from Controller Snapshot
**What goes wrong:** P_target computation uses `victron.grid_power_w` as primary source and Huawei master `active_power_w` as fallback (orchestrator lines 606-624). If HuaweiController doesn't include master data in its snapshot, the fallback is lost.
**Why it happens:** The controller might only expose battery data, not master inverter data.
**How to avoid:** HuaweiController.poll() must read both `read_master()` and `read_battery()` and include both in its snapshot (or expose master data separately).
**Warning signs:** P_target is always 0 when Victron grid meter is unavailable.

## Code Examples

### New Enum Definitions (D-14)
```python
# backend/controller_model.py
from __future__ import annotations
from enum import Enum

class BatteryRole(str, Enum):
    """Per-controller role assigned by the coordinator."""
    PRIMARY_DISCHARGE = "PRIMARY_DISCHARGE"
    SECONDARY_DISCHARGE = "SECONDARY_DISCHARGE"
    CHARGING = "CHARGING"
    HOLDING = "HOLDING"
    GRID_CHARGE = "GRID_CHARGE"

class PoolStatus(str, Enum):
    """Coordinator-level pool health for API consumers."""
    NORMAL = "NORMAL"       # Both systems online
    DEGRADED = "DEGRADED"   # One system offline
    OFFLINE = "OFFLINE"     # Both systems offline
```

### Coordinator State for API (Backward Compatible)
```python
@dataclass
class CoordinatorState:
    """Full pool state snapshot for API consumers.

    Includes all UnifiedPoolState fields (backward compat) plus per-system
    role and status fields (D-15).
    """
    # --- Backward-compatible fields (existing API contract) ---
    combined_soc_pct: float
    huawei_soc_pct: float
    victron_soc_pct: float
    huawei_available: bool
    victron_available: bool
    control_state: str              # PoolStatus for new consumers; mapped to old ControlState for compat
    huawei_discharge_setpoint_w: int
    victron_discharge_setpoint_w: int
    combined_power_w: float
    huawei_charge_headroom_w: int
    victron_charge_headroom_w: float
    timestamp: float
    grid_charge_slot_active: bool = False
    evcc_battery_mode: str = "normal"

    # --- New per-system fields (D-15) ---
    huawei_role: str = "HOLDING"
    victron_role: str = "HOLDING"
    pool_status: str = "NORMAL"
```

### Controller Safe State (D-09)
```python
async def _apply_safe_state(self) -> None:
    """Write zero-power to driver on communication loss."""
    try:
        await self._driver.write_max_discharge_power(0)
        logger.info("%s: safe state applied (0 W)", self._name)
    except Exception as exc:
        logger.warning("%s: safe state write failed: %s", self._name, exc)
```

### Charge Routing During PV Surplus (D-03)
```python
def _allocate_charge(
    self, surplus_w: float, h_snap: ControllerSnapshot, v_snap: ControllerSnapshot
) -> tuple[ControllerCommand, ControllerCommand]:
    """Fill smaller battery (Huawei 30kWh) first, then larger (Victron 64kWh)."""
    h_headroom = h_snap.charge_headroom_w
    v_headroom = v_snap.charge_headroom_w

    if h_snap.soc_pct < self._sys.huawei_max_soc_pct and h_headroom > 0:
        h_charge = min(surplus_w, h_headroom)
        v_charge = min(surplus_w - h_charge, v_headroom) if v_snap.available else 0.0
    else:
        h_charge = 0.0
        v_charge = min(surplus_w, v_headroom)

    return (
        ControllerCommand(role=BatteryRole.CHARGING, target_watts=h_charge),
        ControllerCommand(role=BatteryRole.CHARGING, target_watts=v_charge),
    )
```

### Wiring in main.py Lifespan
```python
# In lifespan() -- replaces Orchestrator construction
huawei_ctrl = HuaweiController(huawei_driver, sys_config, loop_interval_s=5.0)
victron_ctrl = VictronController(victron_driver, sys_config, loop_interval_s=5.0)
coordinator = Coordinator(
    huawei_ctrl=huawei_ctrl,
    victron_ctrl=victron_ctrl,
    sys_config=sys_config,
    orch_config=orch_config,
    writer=writer,
    tariff_engine=tariff_engine,
)
coordinator.set_scheduler(scheduler)
coordinator.set_evcc_monitor(evcc_mqtt)
coordinator.set_notifier(notifier)
await coordinator.start()
app.state.orchestrator = coordinator  # backward compat: API uses same attribute name
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Unified `Orchestrator` with SoC-weighted proportional split | Per-battery controllers with coordinator role assignment | Phase 2 (this phase) | Eliminates oscillation between asymmetric systems |
| Single `ControlState` enum (IDLE/DISCHARGE/CHARGE/HOLD/GRID_CHARGE/DISCHARGE_LOCKED) | `BatteryRole` per controller + `PoolStatus` for health | Phase 2 (this phase) | Enables per-system visibility in API |
| Victron read was sync (MQTT driver) | Victron read is async (Modbus TCP driver from Phase 1) | Phase 1 | Both drivers are now async; simplifies controller pattern |

## Open Questions

1. **Backward compatibility of `/api/state` response**
   - What we know: D-15 says API returns both controller states separately. Phase 5 updates the frontend.
   - What's unclear: Whether to maintain exact `UnifiedPoolState` JSON shape during Phase 2-4, or introduce the new shape immediately.
   - Recommendation: Maintain backward compat by including all existing fields in `CoordinatorState`. Add new fields alongside. Frontend continues working unchanged until Phase 5.

2. **WebSocket broadcast state shape**
   - What we know: `ws_manager.py` broadcasts the orchestrator state to connected clients.
   - What's unclear: Whether the WS payload should match the new `CoordinatorState` immediately.
   - Recommendation: Yes, broadcast `CoordinatorState` (which is a superset of `UnifiedPoolState`). Existing frontend ignores unknown fields.

3. **InfluxDB metrics writer compatibility**
   - What we know: `influx_writer.py` writes `UnifiedPoolState` fields. Per-system metrics are deferred to Phase 4 (INT-07).
   - What's unclear: Whether `write_system_state()` needs updating in Phase 2.
   - Recommendation: Pass the backward-compatible `CoordinatorState` to `write_system_state()`. It has all the fields `UnifiedPoolState` had. No InfluxDB schema changes needed in Phase 2.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-anyio (asyncio_mode = "auto") |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `python -m pytest tests/test_coordinator.py tests/test_huawei_controller.py tests/test_victron_controller.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CTRL-01 | Each controller has own state machine | unit | `python -m pytest tests/test_huawei_controller.py tests/test_victron_controller.py -x -q -k "state_machine or role"` | Wave 0 |
| CTRL-02 | Coordinator never writes to hardware | unit | `python -m pytest tests/test_coordinator.py -x -q -k "no_direct_write or allocate"` | Wave 0 |
| CTRL-03 | Per-system hysteresis dead-band | unit | `python -m pytest tests/test_coordinator.py -x -q -k "hysteresis or dead_band"` | Wave 0 |
| CTRL-04 | Safe state on communication loss | unit | `python -m pytest tests/test_huawei_controller.py tests/test_victron_controller.py -x -q -k "safe_state or failure"` | Wave 0 |
| CTRL-05 | Stable power on reassignment | unit | `python -m pytest tests/test_coordinator.py -x -q -k "reassign or stable"` | Wave 0 |
| CTRL-06 | Dynamic role assignment | unit | `python -m pytest tests/test_coordinator.py -x -q -k "role_assign"` | Wave 0 |
| CTRL-07 | Anti-oscillation ramps | unit | `python -m pytest tests/test_coordinator.py -x -q -k "ramp"` | Wave 0 |
| CTRL-08 | SoC-based discharge priority | unit | `python -m pytest tests/test_coordinator.py -x -q -k "soc_priority or primary"` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_coordinator.py tests/test_huawei_controller.py tests/test_victron_controller.py tests/test_controller_model.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before verification

### Wave 0 Gaps
- [ ] `tests/test_huawei_controller.py` -- covers CTRL-01, CTRL-04 for Huawei
- [ ] `tests/test_victron_controller.py` -- covers CTRL-01, CTRL-04 for Victron
- [ ] `tests/test_coordinator.py` -- covers CTRL-02, CTRL-03, CTRL-05, CTRL-06, CTRL-07, CTRL-08
- [ ] `tests/test_controller_model.py` -- covers enum and dataclass validation
- [ ] Existing `tests/test_orchestrator.py` must still pass (backward compat until removal)

## Sources

### Primary (HIGH confidence)
- `backend/orchestrator.py` (1053 lines) -- complete read, all logic paths analyzed
- `backend/unified_model.py` (210 lines) -- current state model and ControlState enum
- `backend/config.py` (655 lines) -- OrchestratorConfig, SystemConfig structure
- `backend/drivers/protocol.py` -- LifecycleDriver and BatteryDriver protocols
- `backend/drivers/huawei_driver.py` -- write method signatures verified
- `backend/drivers/victron_driver.py` -- read_system_state() and write_ac_power_setpoint() verified async
- `backend/api.py` -- current API endpoints consuming orchestrator state
- `backend/main.py` -- lifespan wiring pattern
- `tests/test_orchestrator.py` -- existing test patterns and mock fixtures

### Secondary (MEDIUM confidence)
- CONTEXT.md decisions D-01 through D-17 -- detailed user decisions constraining architecture

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, pure stdlib refactoring
- Architecture: HIGH -- decomposing existing 1053-line orchestrator with clear decision constraints
- Pitfalls: HIGH -- all pitfalls observed directly in existing code paths
- API compatibility: MEDIUM -- backward compat strategy is sound but needs validation against frontend consumption

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable domain, no external dependency changes)
