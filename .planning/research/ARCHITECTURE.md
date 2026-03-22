# Architecture Patterns

**Domain:** Dual-battery energy management system with independent dispatch
**Researched:** 2026-03-22

## Recommended Architecture

### Overview: Coordinator + Per-Battery Controllers

Replace the current monolithic `Orchestrator` (which computes weighted-average SoC and dispatches proportional setpoints to both systems) with a three-tier architecture:

```
                         ┌──────────────────────┐
                         │    FastAPI Lifespan   │
                         │   (wiring + startup)  │
                         └──────────┬─────────────┘
                                    │ owns
                         ┌──────────▼─────────────┐
                         │     Coordinator         │
                         │  (dispatch + stability) │
                         └──┬───────────────────┬──┘
                  instructs │                   │ instructs
              ┌─────────────▼──────┐   ┌────────▼──────────────┐
              │  HuaweiController  │   │  VictronController    │
              │  (state machine,   │   │  (state machine,      │
              │   anti-oscillation,│   │   anti-oscillation,   │
              │   setpoint logic)  │   │   setpoint logic)     │
              └────────┬───────────┘   └────────┬──────────────┘
                       │ reads/writes            │ reads/writes
              ┌────────▼───────────┐   ┌────────▼──────────────┐
              │  HuaweiDriver      │   │  VictronDriver        │
              │  (Modbus TCP)      │   │  (Modbus TCP — new)   │
              └────────────────────┘   └───────────────────────┘
```

**Why this pattern over alternatives:**

- **Coordinator + controllers** beats **agent-based dispatch** (where each battery independently decides based on shared signals) because agent-based systems require consensus protocols and are prone to split-brain when both agents react to the same grid-power signal simultaneously. With a coordinator, there is a single decision point that allocates responsibility before controllers execute.
- **Per-battery state machines** beat the current **single state machine** because each system has fundamentally different characteristics (30 kWh / 5 kW vs 64 kWh / 10 kW), different communication protocols, different failure modes, and different control semantics (Huawei: discharge power limit register vs Victron: AC power setpoint per phase).
- **Coordinator allocates, controllers execute** ensures that total system behavior is stable (no fighting) while each controller handles its own anti-oscillation, ramp rates, and failure isolation independently.

### Component Boundaries

| Component | Responsibility | Communicates With | Owns |
|-----------|---------------|-------------------|------|
| **Coordinator** | Reads global signals (grid power, PV, tariff, EVCC), computes power budget, allocates discharge/charge targets per battery, monitors combined stability | Both controllers (instructs), Scheduler (receives schedules), TariffEngine (reads rates), EvccMonitor (reads battery mode) | Power budget allocation, combined system state snapshot, dispatch strategy |
| **HuaweiController** | Receives target from Coordinator, runs its own state machine, applies hysteresis/ramp/deadband, writes setpoints to HuaweiDriver, reports actual state back | Coordinator (receives targets, reports state), HuaweiDriver (reads/writes) | Per-battery state machine, setpoint history, anti-oscillation state, failure counter |
| **VictronController** | Same as HuaweiController but for Victron; handles 3-phase per-phase setpoint logic internally | Coordinator (receives targets, reports state), VictronDriver (reads/writes) | Per-battery state machine, per-phase setpoint history, anti-oscillation state |
| **HuaweiDriver** | Modbus TCP read/write for SUN2000 + LUNA2000 registers | HuaweiController only | Connection state, register cache |
| **VictronDriver** | Modbus TCP read/write for Venus OS GX device (replacing current MQTT driver) | VictronController only | Connection state, register cache |
| **Scheduler** | Computes nightly charge schedules with per-battery targets | Coordinator (provides schedule), TariffEngine, ConsumptionForecaster | Active schedule, charge slots |
| **MetricsWriter** | Writes per-battery and combined metrics to InfluxDB | Coordinator (receives snapshots) | InfluxDB connection |
| **API Layer** | Exposes per-battery and combined state via REST + WebSocket | Coordinator (reads state) | WebSocket connections |

### Data Flow

**Signal acquisition (every cycle, 5 s):**

```
1. Coordinator calls huawei_ctrl.poll() and victron_ctrl.poll() concurrently
2. Each controller calls its driver to read current state
3. Each controller returns a BatteryReport:
   - soc_pct, power_w, available, max_charge_w, max_discharge_w
   - controller_state (its local state machine)
   - error (if any)
4. Coordinator also reads:
   - Grid power from Victron controller (Venus OS grid meter)
   - PV power from Huawei controller (SUN2000 master)
   - Current tariff rate from TariffEngine
   - EVCC battery mode from EvccMonitor
   - Active charge schedule from Scheduler
```

**Dispatch decision (Coordinator, every cycle):**

```
1. Compute P_target = grid_power_w (positive = importing, need to discharge)
2. Check overrides:
   a. EVCC hold → both targets = 0, state = DISCHARGE_LOCKED
   b. Active charge slot → route charge power to designated battery
   c. Both controllers unavailable → both targets = 0, state = HOLD
3. Compute available headroom per battery:
   - huawei_headroom = max(0, huawei_soc - huawei_min_soc) * huawei_capacity
   - victron_headroom = max(0, victron_soc - victron_min_soc) * victron_capacity
4. Apply dispatch strategy (see "Dispatch Strategies" below)
5. Send target_w to each controller
6. Each controller independently:
   a. Applies anti-oscillation filters (hysteresis, ramp limiter, deadband)
   b. Runs its state machine transition (with debounce)
   c. Writes filtered setpoint to driver
   d. Reports back actual applied setpoint
7. Coordinator builds CombinedSystemState from both reports
8. Coordinator publishes to MetricsWriter and WebSocket
```

**Per-battery controller internal flow:**

```
receive_target(target_w: float) →
  ├─ ramp_limiter(target_w) → ramped_w
  │    (limits Δw per cycle to max_ramp_rate)
  ├─ deadband_filter(ramped_w) → filtered_w
  │    (suppresses write if |ramped_w - last_written| < deadband_w)
  ├─ state_machine_transition(filtered_w)
  │    (debounced: N consecutive cycles in same proposed state)
  ├─ write_to_driver(filtered_w)
  │    (only if not suppressed by deadband)
  └─ return ControllerReport(actual_w, state, available)
```

## Anti-Oscillation Algorithm Specifics

The current system has oscillation problems because both batteries react to the same grid power signal proportionally. When Huawei discharges, it changes the grid power reading, which the next cycle uses to compute a different Victron target, causing both systems to chase each other.

### Root Cause Analysis

1. **Shared feedback loop:** Both batteries see the combined effect of each other's actions via the grid meter
2. **No temporal separation:** Both setpoints are computed and applied simultaneously
3. **Proportional splitting amplifies noise:** Small SoC differences cause disproportionate target swings

### Anti-Oscillation Strategy: Layered Filtering

Each layer addresses a different oscillation frequency:

#### Layer 1: Coordinator-Level Role Assignment (prevents inter-battery oscillation)

Instead of proportional splitting, assign **roles** each cycle:

| Role | Description | Assigned When |
|------|-------------|---------------|
| **PRIMARY** | Covers base load up to its max, first to discharge | Higher SoC headroom OR designated base-load system |
| **SECONDARY** | Covers overflow above primary's max | Primary insufficient for P_target |
| **IDLE** | Not discharging (but available as backup) | P_target fully covered by primary |
| **CHARGING** | Accepting surplus PV or grid charge | PV surplus or scheduled charge slot |

**Role assignment heuristic:**

```python
# Simplified — actual implementation adds configurable thresholds
if victron_headroom_kwh > huawei_headroom_kwh * 1.5:
    primary, secondary = VICTRON, HUAWEI
elif huawei_headroom_kwh > victron_headroom_kwh * 1.5:
    primary, secondary = HUAWEI, VICTRON
else:
    # Similar headroom — use Victron as primary (larger capacity, 3-phase)
    primary, secondary = VICTRON, HUAWEI

primary_target = min(P_target, primary.max_discharge_w)
secondary_target = max(0, P_target - primary_target)
```

**Why roles prevent oscillation:** Only the primary battery reacts to grid meter changes. The secondary has a fixed target (overflow) or is idle. No feedback loop between the two.

**Role stickiness:** Once assigned, a role persists for `role_hold_cycles` (default: 6 cycles = 30 s) before re-evaluation. This prevents rapid role-swapping during load transients.

#### Layer 2: Per-Controller Ramp Limiter (prevents intra-battery oscillation)

Each controller limits how fast its setpoint can change:

```python
max_ramp_w_per_cycle = 500  # Huawei: 500 W/cycle (100 W/s at 5s intervals)
                             # Victron: 800 W/cycle (160 W/s at 5s intervals)

delta = target_w - last_target_w
if abs(delta) > max_ramp_w_per_cycle:
    ramped_w = last_target_w + sign(delta) * max_ramp_w_per_cycle
else:
    ramped_w = target_w
```

**Rationale for asymmetric ramp rates:** Victron (10 kW max, 3-phase) can ramp faster than Huawei (5 kW max, single-phase on battery side). Ramp rate = ~10% of max power per cycle is a standard industrial value.

#### Layer 3: Per-Controller Dead-Band (prevents micro-oscillation at steady state)

Suppress writes when the setpoint change is below a threshold:

| System | Dead-band | Rationale |
|--------|-----------|-----------|
| Huawei | 200 W combined | Existing value, works well — Modbus write overhead is significant |
| Victron | 30 W per-phase | Slightly wider than current 20 W to reduce write frequency; Modbus TCP writes are cheaper than MQTT but still should be minimized |

Dead-band applies **after** ramping, so a large target change still ramps through smoothly.

#### Layer 4: Per-Controller State Machine Debounce (prevents mode-flapping)

Each controller runs its own debounce independently:

```
States: IDLE, DISCHARGE, CHARGE, HOLD, GRID_CHARGE
Debounce: 2 consecutive cycles proposing the same new state (10 s at 5 s intervals)
```

This is unchanged from v1 but now runs per-controller instead of globally.

### Anti-Oscillation Parameter Summary

| Parameter | Huawei | Victron | Scope |
|-----------|--------|---------|-------|
| `deadband_w` | 200 | 30 (per-phase) | Controller |
| `max_ramp_w_per_cycle` | 500 | 800 | Controller |
| `debounce_cycles` | 2 | 2 | Controller |
| `role_hold_cycles` | 6 | 6 | Coordinator |
| `role_hysteresis_factor` | 1.5x | 1.5x | Coordinator |
| `min_discharge_w` | 100 | 150 | Controller (below this, snap to 0) |

### Soft-Start / Soft-Stop

When transitioning from IDLE to DISCHARGE (or CHARGE):

```
Soft-start: ramp from 0 to target over 3 cycles (15 s)
  Cycle 1: 33% of target
  Cycle 2: 66% of target
  Cycle 3: 100% of target

Soft-stop: ramp from current to 0 over 2 cycles (10 s)
  Cycle 1: 50% of current
  Cycle 2: 0
```

**Why:** Sudden load changes cause grid meter spikes that feed back into P_target, creating an overshoot-undershoot cycle. Soft transitions let the grid meter stabilize between steps.

## Patterns to Follow

### Pattern 1: Controller Protocol (Abstract Base)

Each battery controller implements a common interface so the Coordinator does not care about hardware specifics:

```python
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

class ControllerState(str, Enum):
    IDLE = "IDLE"
    DISCHARGE = "DISCHARGE"
    CHARGE = "CHARGE"
    HOLD = "HOLD"
    GRID_CHARGE = "GRID_CHARGE"

@dataclass
class BatteryReport:
    """Snapshot returned by a controller after each poll cycle."""
    soc_pct: float
    power_w: float              # actual measured power (positive = charge)
    available: bool
    max_charge_w: float
    max_discharge_w: float
    state: ControllerState
    applied_setpoint_w: float   # what was actually written after filtering
    error: str | None

class BatteryController(Protocol):
    """Interface that each per-battery controller implements."""

    async def poll(self) -> BatteryReport:
        """Read driver state, return report."""
        ...

    async def set_target(self, target_w: float, mode: ControllerState) -> None:
        """Receive dispatch target from Coordinator. Controller applies
        ramp/deadband/debounce internally before writing to driver."""
        ...

    async def safe_shutdown(self) -> None:
        """Write zero setpoints and enter HOLD."""
        ...

    @property
    def name(self) -> str: ...

    @property
    def capacity_kwh(self) -> float: ...
```

### Pattern 2: Coordinator Owns the Loop, Controllers Own the Writes

The Coordinator runs the async control loop (currently in `Orchestrator._run`). Controllers do not have their own loops — they are called synchronously within the Coordinator's cycle:

```python
class Coordinator:
    async def _run_cycle(self):
        # 1. Poll both controllers concurrently
        huawei_report, victron_report = await asyncio.gather(
            self._huawei_ctrl.poll(),
            self._victron_ctrl.poll(),
        )

        # 2. Compute dispatch (Coordinator logic)
        targets = self._compute_dispatch(huawei_report, victron_report)

        # 3. Instruct controllers (they apply their own filtering)
        await asyncio.gather(
            self._huawei_ctrl.set_target(targets.huawei_w, targets.huawei_mode),
            self._victron_ctrl.set_target(targets.victron_w, targets.victron_mode),
        )

        # 4. Build combined state and publish
        state = self._build_combined_state(huawei_report, victron_report)
        await self._publish(state)
```

**Why controllers don't have their own loops:** Independent loops introduce timing drift and race conditions on shared resources (grid meter). A single loop with concurrent I/O ensures deterministic ordering.

### Pattern 3: Immutable Dispatch Instructions

The Coordinator sends immutable instruction dataclasses, not mutable shared state:

```python
@dataclass(frozen=True)
class DispatchTarget:
    """Immutable instruction from Coordinator to Controller."""
    target_w: float
    mode: ControllerState
    reason: str
    timestamp: float
```

This prevents any possibility of the Coordinator mutating a target after the Controller has started processing it.

### Pattern 4: Per-Battery Metrics Tags

All InfluxDB writes include a `battery` tag (`huawei` or `victron`) so dashboards and queries can filter by system:

```python
# Combined measurement
write_point("ems_state", tags={"scope": "combined"}, fields={...})

# Per-battery measurements
write_point("ems_battery", tags={"battery": "huawei"}, fields={
    "soc_pct": report.soc_pct,
    "power_w": report.power_w,
    "setpoint_w": report.applied_setpoint_w,
    "state": report.state.value,
})
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Weighted-Average SoC for Dispatch

**What:** The current v1 approach: `(huawei_soc * 30 + victron_soc * 64) / 94` used to determine combined pool state.

**Why bad:** Masks individual battery states. When Huawei is at 10% and Victron at 80%, the combined SoC is 57% — suggesting healthy state while Huawei is near empty. Dispatch decisions based on this average will try to discharge Huawei further.

**Instead:** Each controller reports its own SoC. The Coordinator makes decisions based on per-battery headroom, never on an averaged value. A combined SoC may still be displayed in the UI for convenience but must never be used for dispatch logic.

### Anti-Pattern 2: Shared Mutable State Between Controllers

**What:** Both controllers reading/writing the same state object (like the current `UnifiedPoolState`).

**Why bad:** Race conditions even in async code (if one controller's await yields, the other can see partial state). More importantly, it couples the controllers — a bug in one corrupts the other's view.

**Instead:** Each controller owns its state. The Coordinator receives immutable reports and builds a combined view.

### Anti-Pattern 3: Proportional Splitting Without Role Assignment

**What:** Splitting P_target proportionally by SoC headroom and sending both setpoints simultaneously.

**Why bad:** Both batteries react to the same signal, creating a feedback loop through the grid meter. When Huawei discharges 2 kW, grid power drops 2 kW, and the next cycle gives Victron a lower target, which then causes grid power to rise, giving Huawei a higher target, etc.

**Instead:** Role-based dispatch where only the PRIMARY battery actively tracks grid power. The SECONDARY receives a stable, bounded target.

### Anti-Pattern 4: Agent-Based Autonomous Dispatch

**What:** Each battery runs its own control loop, reads grid power independently, and decides its own setpoint.

**Why bad:** Classic split-brain problem. Both agents see the same grid import and both try to discharge to cover it. Result: 2x the needed discharge, causing grid export, which both agents then try to reduce by cutting discharge, causing grid import again. Oscillation is guaranteed.

**Instead:** Single Coordinator makes dispatch decisions. Controllers only execute filtered setpoints.

## Dispatch Strategies

The Coordinator supports pluggable dispatch strategies. Start with one, add more later:

### Strategy 1: Priority Cascade (recommended for MVP)

```
1. If P_target > 0 (import from grid, need to discharge):
   a. Assign larger-headroom battery as PRIMARY
   b. PRIMARY covers up to min(P_target, primary_max_discharge)
   c. SECONDARY covers overflow: max(0, P_target - primary_actual)
   d. If P_target < min_discharge_w: both IDLE (snap-to-zero)

2. If P_target < 0 (exporting to grid, PV surplus):
   a. Assign lower-SoC battery as PRIMARY for charging
   b. PRIMARY absorbs up to min(|P_target|, primary_max_charge)
   c. SECONDARY absorbs overflow

3. Role re-evaluation every role_hold_cycles with hysteresis
```

### Strategy 2: Time-of-Use Optimization (future)

```
During cheap tariff: charge both batteries at max rate
During expensive tariff: discharge both batteries, Victron first (larger)
During shoulder tariff: discharge only Victron, hold Huawei as reserve
```

### Strategy 3: Forecast-Driven (future)

```
Use consumption forecast + PV forecast to pre-position batteries:
- Evening peak expected: ensure both batteries > 80% before peak
- Overnight: charge cheapest battery first during lowest tariff
- Morning: hold — PV will cover load soon
```

## Integration with FastAPI Lifespan

The Coordinator replaces the current Orchestrator in the lifespan wiring:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Create drivers (unchanged)
    huawei_driver = HuaweiDriver(huawei_config)
    victron_driver = VictronDriver(victron_config)  # NEW: Modbus TCP

    # 2. Connect drivers
    await huawei_driver.connect()
    await victron_driver.connect()

    # 3. Create controllers (NEW)
    huawei_ctrl = HuaweiController(
        driver=huawei_driver,
        config=HuaweiControllerConfig.from_env(),
    )
    victron_ctrl = VictronController(
        driver=victron_driver,
        config=VictronControllerConfig.from_env(),
    )

    # 4. Create Coordinator (replaces Orchestrator)
    coordinator = Coordinator(
        huawei=huawei_ctrl,
        victron=victron_ctrl,
        sys_config=sys_config,
        dispatch_config=DispatchConfig.from_env(),
    )

    # 5. Wire optional dependencies (same pattern as today)
    coordinator.set_scheduler(scheduler)
    coordinator.set_tariff_engine(tariff_engine)
    coordinator.set_evcc_monitor(evcc_mqtt)
    coordinator.set_notifier(notifier)
    coordinator.set_metrics_writer(writer)

    # 6. Start
    await coordinator.start()
    app.state.coordinator = coordinator

    yield

    # 7. Shutdown
    await coordinator.stop()          # writes safe setpoints via controllers
    await huawei_driver.close()
    await victron_driver.close()
```

**Key difference from v1:** The Coordinator does not directly hold driver references. It only talks to controllers. This enforces the boundary — the Coordinator cannot accidentally bypass a controller's anti-oscillation filters.

## Suggested Build Order

Based on component dependencies:

```
Phase 1: Foundation (no oscillation risk — building blocks)
├── 1a. BatteryController protocol + BatteryReport dataclass
├── 1b. VictronDriver (Modbus TCP — replacing MQTT driver)
├── 1c. HuaweiController wrapping existing HuaweiDriver
└── 1d. VictronController wrapping new VictronDriver

Phase 2: Coordinator Core (replaces Orchestrator)
├── 2a. Coordinator with single-loop dispatch
├── 2b. Priority Cascade dispatch strategy
├── 2c. Role assignment with stickiness + hysteresis
└── 2d. Anti-oscillation: ramp limiter + deadband per controller

Phase 3: Integration (wiring into existing system)
├── 3a. FastAPI lifespan rewiring (Coordinator replaces Orchestrator)
├── 3b. API endpoints for per-battery state
├── 3c. Per-battery InfluxDB metrics
├── 3d. WebSocket broadcast of per-battery state
└── 3e. Scheduler integration (per-battery charge targets)

Phase 4: Frontend + Polish
├── 4a. Per-battery dashboard cards
├── 4b. Decision transparency (show role assignments, dispatch reasoning)
├── 4c. Soft-start/soft-stop refinement
└── 4d. Advanced dispatch strategies (ToU, forecast-driven)
```

**Dependency rationale:**
- Phase 1 has no dependencies on the existing Orchestrator — can be built in parallel
- Phase 2 depends on the controller protocol from Phase 1
- Phase 3 depends on Phase 2 (Coordinator must exist before it can be wired in)
- Phase 4 depends on Phase 3 (API endpoints must exist before frontend consumes them)

**Critical path:** The VictronDriver rewrite (1b) is the highest-risk item because it involves a protocol change (MQTT to Modbus TCP) with hardware testing required. Start it first.

## Scalability Considerations

| Concern | Current (2 batteries) | Future (3-4 batteries) | Notes |
|---------|----------------------|----------------------|-------|
| Coordinator dispatch | O(n) per cycle, trivial | O(n) still trivial | Role assignment generalizes to N batteries |
| Control loop timing | 5 s cycle handles 2 polls + 2 writes easily | May need staggered polls at 4+ batteries | 5 s is generous for Modbus TCP (~50 ms round-trip) |
| Anti-oscillation | Role-based prevents 2-battery oscillation | Priority cascade generalizes to N tiers | Only 1 PRIMARY at a time, rest are SECONDARY/IDLE |
| Metrics | 2 batteries = ~10 points/cycle | Linear scaling | InfluxDB handles this trivially |

## File Structure (Proposed)

```
backend/
├── coordinator.py              # Coordinator class (replaces orchestrator.py)
├── controllers/
│   ├── __init__.py
│   ├── protocol.py             # BatteryController protocol, BatteryReport, ControllerState
│   ├── base.py                 # BaseController (shared anti-oscillation logic)
│   ├── huawei_controller.py    # HuaweiController(BaseController)
│   └── victron_controller.py   # VictronController(BaseController)
├── dispatch/
│   ├── __init__.py
│   ├── strategy.py             # DispatchStrategy protocol
│   ├── priority_cascade.py     # PriorityCascadeStrategy
│   └── models.py               # DispatchTarget, RoleAssignment
├── drivers/
│   ├── huawei_driver.py        # (unchanged)
│   ├── huawei_models.py        # (unchanged)
│   ├── victron_driver.py       # REWRITTEN: Modbus TCP instead of MQTT
│   └── victron_models.py       # (updated for Modbus TCP register map)
├── anti_oscillation/
│   ├── __init__.py
│   ├── ramp_limiter.py         # RampLimiter class
│   ├── deadband.py             # DeadbandFilter class
│   └── soft_transition.py      # SoftStart / SoftStop logic
└── ...                         # existing files (api.py, config.py, etc.)
```

## Sources

- Analysis of current codebase: `backend/orchestrator.py`, `backend/unified_model.py`, `backend/config.py`, `backend/main.py`
- Control theory: standard industrial PID / cascade controller patterns applied to ESS dispatch
- Anti-oscillation: hysteresis and dead-band are standard power electronics control techniques; ramp limiters are standard in VFD and inverter control
- Coordinator pattern: standard in distributed systems (Raft leader analogy — single decision maker, multiple executors)

**Confidence:** MEDIUM — architecture patterns are well-established in control systems engineering and distributed systems. The specific parameter values (ramp rates, dead-bands, role hold times) are reasonable starting points but will need tuning with the actual hardware. No web sources could be consulted to verify against similar residential dual-battery EMS implementations.

---

*Architecture research: 2026-03-22*
