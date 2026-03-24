# Phase 21: Cross-Charge Detection and Prevention - Research

**Researched:** 2026-03-24
**Domain:** Dual-battery coordinator safety guard, real-time power analysis, InfluxDB metrics, Telegram alerting, React dashboard badge
**Confidence:** HIGH

## Summary

Cross-charge detection is pure coordinator logic with zero external dependencies. The CrossChargeDetector reads two ControllerSnapshot power values and one grid power value per cycle, applies a threshold+debounce algorithm, and modifies ControllerCommand roles before execution. All integration points (injection, InfluxDB writes, Telegram alerts, DecisionEntry logging, CoordinatorState extension, API health response) follow established patterns already used by the anomaly detector, self-tuner, and export advisor.

The codebase has 8 existing `set_xxx()` injection methods on Coordinator, a fire-and-forget InfluxDB writer with 6 measurement types, a TelegramNotifier with per-category cooldown, and a frontend WebSocket pipeline that pushes CoordinatorState to React components every 5 seconds. Every integration point for this phase maps directly to an existing pattern -- no new infrastructure is needed.

**Primary recommendation:** Implement CrossChargeDetector as a standalone dataclass-based module (`backend/cross_charge.py`) injected into Coordinator via `set_cross_charge_detector()`, with the guard placed after command computation and before `execute()` calls in `_run_cycle()`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Place cross-charge guard after command computation, before execute() -- intercept pattern matching EVCC hold and mode override
- Near-zero grid threshold: abs(grid_power) < 200W, accounts for measurement noise
- CrossChargeDetector dataclass injected into Coordinator via set_cross_charge_detector() -- matches existing injection pattern (anomaly_detector, self_tuner, export_advisor)
- Use total grid power (sum of L1+L2+L3) for detection -- Victron is 3-phase, cross-charge distributes across phases
- Cumulative waste energy: integrate min(abs(charge_power), abs(discharge_power)) * cycle_duration during episodes
- Telegram alert: first detection per episode only, cooldown reset when cross-charge clears for 5+ minutes
- InfluxDB measurement: `ems_cross_charge` with fields: `waste_wh`, `episode_count`, `active` -- follows existing ems_huawei/ems_victron naming
- REST API: extend existing /api/health with cross_charge section -- no new endpoint
- Badge on EnergyFlowCard near battery nodes -- visible at a glance in main energy flow view
- Active cross-charge: red warning badge with "Cross-Charge" label + pulsing animation
- Inactive: hidden (clean dashboard when normal)
- Historical: count + total waste kWh in existing OptimizationCard
- Detection formula: abs(charge_power) > 100W AND abs(discharge_power) > 100W AND abs(grid_power) < 200W for 2+ consecutive cycles
- Mitigation: force the charging battery to HOLDING
- Episode tracking: start/end timestamps, cumulative waste Wh per episode

### Claude's Discretion
- CrossChargeDetector internal data structures and state machine
- Exact CSS styling for the cross-charge badge
- InfluxDB write frequency (per-cycle or per-episode)
- Test fixture design

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| XCHG-01 | Coordinator detects cross-charging (opposing battery power signs + near-zero grid) within 2 control cycles | CrossChargeDetector.check() reads h_snap.power_w, v_snap.power_w, and grid_power from v_snap; 2-cycle debounce via internal counter; grid threshold 200W locked |
| XCHG-02 | On detection, coordinator forces the charging battery to HOLDING role to stop energy transfer | CrossChargeDetector.mitigate() returns modified ControllerCommand with HOLDING role; coordinator logs DecisionEntry with trigger="cross_charge_prevention" |
| XCHG-03 | Cross-charge detection uses 2-cycle debounce and 100W minimum threshold to avoid false positives | threshold_w=100.0 and min_cycles=2 as constructor params; debounce counter increments per cycle, resets on non-detection |
| XCHG-04 | First detection per episode triggers Telegram alert | Uses existing TelegramNotifier.send_alert() with new ALERT_CROSS_CHARGE category; cooldown_s=300 matches 5-minute episode reset |
| XCHG-05 | Cumulative cross-charge waste energy tracked in InfluxDB | New InfluxMetricsWriter.write_cross_charge_point() writes ems_cross_charge measurement; waste_wh field accumulates per episode |
| XCHG-06 | Dashboard displays cross-charge status indicator | New cross_charge_active field on CoordinatorState flows via WebSocket; EnergyFlowCard shows red badge; OptimizationCard shows cumulative stats |
</phase_requirements>

## Architecture Patterns

### Recommended Module Structure
```
backend/
  cross_charge.py          # NEW: CrossChargeDetector + CrossChargeState
  coordinator.py           # MODIFY: add guard + injection + state field
  controller_model.py      # MODIFY: add cross_charge_active to CoordinatorState
  influx_writer.py         # MODIFY: add write_cross_charge_point()
  notifier.py              # MODIFY: add ALERT_CROSS_CHARGE constant
  api.py                   # MODIFY: extend /api/health response
  main.py                  # MODIFY: wire CrossChargeDetector in lifespan

frontend/src/
  types.ts                 # MODIFY: add cross_charge fields to PoolState
  components/
    EnergyFlowCard.tsx     # MODIFY: add cross-charge badge
    OptimizationCard.tsx   # MODIFY: add waste stats section
```

### Pattern 1: Optional Injection (existing pattern)
**What:** Coordinator receives optional collaborators via `set_xxx()` methods, checks `if self._xxx is not None:` before calling.
**When to use:** Always for new coordinator integrations.
**Example from codebase:**
```python
# coordinator.py __init__:
self._anomaly_detector = None

# coordinator.py injection:
def set_anomaly_detector(self, detector) -> None:
    self._anomaly_detector = detector

# coordinator.py usage in _run_cycle:
if self._anomaly_detector is not None:
    events = self._anomaly_detector.check_cycle(self._last_h_snap, self._last_v_snap)
```

### Pattern 2: Fire-and-Forget InfluxDB Write (existing pattern)
**What:** Each write method catches all exceptions, logs WARNING, never raises.
**Example from codebase:**
```python
async def write_per_system_metrics(self, h_snap, v_snap, h_role, v_role) -> None:
    try:
        point = Point("ems_huawei").tag("role", h_role).field("soc_pct", float(h_snap.soc_pct))...
        await self._write_api.write(bucket=self._bucket, record=point)
    except Exception as exc:
        logger.warning("influx per-system write failed: %s", exc)
```

### Pattern 3: Telegram Alert with Per-Category Cooldown (existing pattern)
**What:** `TelegramNotifier.send_alert(category, message)` suppresses duplicates within cooldown_s (default 300s).
**Example from codebase:**
```python
# Existing categories in notifier.py:
ALERT_COMM_FAILURE = "comm_failure"
ALERT_ANOMALY_COMM = "anomaly_comm_loss"
# Add: ALERT_CROSS_CHARGE = "cross_charge"
```

### Pattern 4: Guard Insertion in _run_cycle (key architectural decision)
**What:** The cross-charge guard must intercept commands in every code path that computes h_cmd/v_cmd and then calls execute(). The _run_cycle method has 7 exit points (EVCC hold, mode override, grid charge, grid charge cleanup, PV surplus/export, idle, discharge). The guard should NOT run during EVCC hold or mode override (those are explicit overrides) but MUST run for the normal computation paths (grid charge, PV surplus, idle, discharge).
**Implementation:** Insert the guard call after commands are computed but before the execute() calls in steps 3-6 of _run_cycle. The cleanest approach is a helper method:
```python
def _apply_cross_charge_guard(
    self, h_snap, v_snap, h_cmd, v_cmd
) -> tuple[ControllerCommand, ControllerCommand]:
    if self._cross_charge_detector is None:
        return h_cmd, v_cmd
    xc_state = self._cross_charge_detector.check(h_snap, v_snap, h_cmd, v_cmd)
    if xc_state.detected:
        h_cmd, v_cmd = self._cross_charge_detector.mitigate(xc_state, h_cmd, v_cmd)
        # Log decision, send alert, write InfluxDB
        ...
    return h_cmd, v_cmd
```

### Pattern 5: CoordinatorState Extension (existing pattern)
**What:** Add new fields to CoordinatorState with backward-compatible defaults.
**Example from codebase:**
```python
@dataclass
class CoordinatorState:
    # ... existing fields ...
    grid_charge_slot_active: bool = False     # added in Phase X
    export_active: bool = False               # added in Phase Y
    # Add:
    cross_charge_active: bool = False
    cross_charge_waste_wh: float = 0.0
    cross_charge_episode_count: int = 0
```

### Anti-Patterns to Avoid
- **Inserting guard only in discharge path:** Cross-charge can occur in ANY path where one battery charges while the other discharges. The guard must cover PV surplus (step 5) and discharge (step 6) paths at minimum.
- **Modifying commands in-place:** ControllerCommand is a dataclass. Create new instances rather than mutating fields, following the existing pattern where each path constructs fresh commands.
- **Blocking on Telegram/InfluxDB:** Both are fire-and-forget. Never await them in a way that could delay the control cycle.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Telegram alerting | Custom HTTP sender | Existing `TelegramNotifier.send_alert()` | Already handles cooldown, error logging, HTML formatting |
| InfluxDB time-series write | Custom InfluxDB client | Existing `InfluxMetricsWriter` + new method | Already handles async write API, fire-and-forget, bucket config |
| Decision audit trail | Custom logging | Existing `DecisionEntry` + `self._decisions` deque | Already consumed by `/api/decisions` and InfluxDB writer |
| WebSocket state push | Custom state broadcast | Existing `CoordinatorState` -> `_build_state()` -> WebSocket | Frontend already consumes CoordinatorState via `useEmsSocket` |

## Common Pitfalls

### Pitfall 1: Grid Power Source Confusion
**What goes wrong:** Using Huawei master_active_power_w instead of Victron grid_power_w for grid measurement. The Huawei inverter reports its own output power, not the grid meter.
**Why it happens:** Both snapshots have power-related fields. The grid meter is on the Victron side (Venus OS measures the grid connection).
**How to avoid:** Always use `v_snap.grid_power_w` (total) or sum of `v_snap.grid_l1_power_w + grid_l2_power_w + grid_l3_power_w`. The CONTEXT.md locks total grid power (sum of L1+L2+L3).
**Warning signs:** Detection triggering when grid import is high (household load being served).

### Pitfall 2: Sign Convention Mismatch
**What goes wrong:** Checking for "opposing signs" using the wrong convention. The coordinator canonical convention is positive=charge, negative=discharge.
**Why it happens:** Different components use different sign conventions. The coordinator documentation states this clearly but it's easy to forget.
**How to avoid:** Cross-charge condition is: one battery has `power_w > 100` (charging) and the other has `power_w < -100` (discharging). Check `h_snap.power_w` and `v_snap.power_w` directly -- they use coordinator canonical signs.
**Warning signs:** Detection never triggers, or triggers on every cycle.

### Pitfall 3: Guard Not Covering All Code Paths
**What goes wrong:** Inserting the guard only before the final execute() at the bottom of _run_cycle, missing the early-return paths for PV surplus, idle, and export.
**Why it happens:** _run_cycle has 7 early-return exit points. Each computes commands and calls execute() independently.
**How to avoid:** Extract a `_apply_cross_charge_guard()` helper and call it in every path that computes commands from the normal dispatch logic (steps 3-6). Skip EVCC hold and mode override paths (steps 2, 2b) since those are explicit overrides.
**Warning signs:** Cross-charge not detected during PV surplus periods (step 5) when one battery is full and the other is charging.

### Pitfall 4: Episode Reset Too Aggressive
**What goes wrong:** Episode counter resets on the first non-detection cycle, causing multiple Telegram alerts for a single oscillating episode.
**Why it happens:** Cross-charge can flicker on/off between cycles due to measurement noise near thresholds.
**How to avoid:** The CONTEXT.md specifies 5-minute cooldown for episode reset. Use a separate timer that tracks when cross-charge last cleared. Only reset the episode state after 5 continuous minutes of no detection.
**Warning signs:** Multiple Telegram alerts within minutes for what should be one episode.

### Pitfall 5: InfluxDB Write per Cycle During Episode
**What goes wrong:** Writing ems_cross_charge on every 5s cycle during an episode generates high write volume for repetitive data.
**Why it happens:** Following the ems_system pattern (written every cycle).
**How to avoid:** Write on episode start, episode end, and periodically during (e.g., every 60s or every 12 cycles) to update cumulative waste_wh. This is in Claude's discretion.
**Warning signs:** InfluxDB bucket size growing rapidly.

### Pitfall 6: Missing Null Guard on Grid Power
**What goes wrong:** `v_snap.grid_power_w` is `Optional[float]` and can be `None` when Victron is offline. Comparing `abs(None) < 200` raises TypeError.
**Why it happens:** Victron may be temporarily unavailable.
**How to avoid:** If `v_snap.grid_power_w is None`, skip detection (cannot determine grid state). Same for L1/L2/L3 if using per-phase sum.
**Warning signs:** TypeError crash in control loop.

## Code Examples

### CrossChargeDetector Module Structure
```python
# backend/cross_charge.py
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from backend.controller_model import (
    BatteryRole,
    ControllerCommand,
    ControllerSnapshot,
)

logger = logging.getLogger(__name__)


@dataclass
class CrossChargeState:
    """Result of a single cross-charge detection check."""
    detected: bool = False
    source_system: str | None = None      # "huawei" or "victron" (discharging)
    sink_system: str | None = None        # the one charging
    source_power_w: float = 0.0
    sink_power_w: float = 0.0
    net_grid_power_w: float = 0.0
    consecutive_cycles: int = 0


@dataclass
class CrossChargeEpisode:
    """Tracks a single cross-charge episode for alerting and metrics."""
    start_time: float = 0.0               # time.monotonic()
    end_time: float | None = None
    cumulative_waste_wh: float = 0.0
    cycle_count: int = 0


class CrossChargeDetector:
    """Detects and mitigates battery-to-battery energy transfer."""

    def __init__(
        self,
        threshold_w: float = 100.0,
        grid_threshold_w: float = 200.0,
        min_cycles: int = 2,
        cycle_duration_s: float = 5.0,
        episode_reset_s: float = 300.0,     # 5 minutes
    ) -> None:
        self._threshold_w = threshold_w
        self._grid_threshold_w = grid_threshold_w
        self._min_cycles = min_cycles
        self._cycle_duration_s = cycle_duration_s
        self._episode_reset_s = episode_reset_s

        # Detection state
        self._consecutive_count: int = 0
        self._last_clear_time: float = time.monotonic()

        # Episode tracking
        self._active_episode: CrossChargeEpisode | None = None
        self._total_episodes: int = 0
        self._total_waste_wh: float = 0.0

    def check(
        self,
        h_snap: ControllerSnapshot,
        v_snap: ControllerSnapshot,
    ) -> CrossChargeState:
        """Check current snapshots for cross-charge condition."""
        # Grid power from Victron (sum of L1+L2+L3 for 3-phase accuracy)
        grid_w = self._get_grid_power(v_snap)
        if grid_w is None:
            self._consecutive_count = 0
            return CrossChargeState()

        h_power = h_snap.power_w
        v_power = v_snap.power_w

        # Cross-charge: one charging (>threshold), other discharging (<-threshold),
        # grid near zero
        cross = (
            abs(h_power) > self._threshold_w
            and abs(v_power) > self._threshold_w
            and ((h_power > 0) != (v_power > 0))  # opposing signs
            and abs(grid_w) < self._grid_threshold_w
        )

        if cross:
            self._consecutive_count += 1
        else:
            self._consecutive_count = 0
            self._last_clear_time = time.monotonic()

        detected = self._consecutive_count >= self._min_cycles

        # Identify source (discharging) and sink (charging)
        if detected:
            if h_power < 0:
                source, sink = "huawei", "victron"
                source_w, sink_w = abs(h_power), abs(v_power)
            else:
                source, sink = "victron", "huawei"
                source_w, sink_w = abs(v_power), abs(h_power)
        else:
            source = sink = None
            source_w = sink_w = 0.0

        return CrossChargeState(
            detected=detected,
            source_system=source,
            sink_system=sink,
            source_power_w=source_w,
            sink_power_w=sink_w,
            net_grid_power_w=grid_w,
            consecutive_cycles=self._consecutive_count,
        )

    def mitigate(
        self,
        state: CrossChargeState,
        h_cmd: ControllerCommand,
        v_cmd: ControllerCommand,
    ) -> tuple[ControllerCommand, ControllerCommand]:
        """Force the charging (sink) battery to HOLDING."""
        if state.sink_system == "huawei":
            h_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        elif state.sink_system == "victron":
            v_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        return h_cmd, v_cmd

    def _get_grid_power(self, v_snap: ControllerSnapshot) -> float | None:
        """Sum L1+L2+L3 grid power, fallback to total."""
        if (
            v_snap.grid_l1_power_w is not None
            and v_snap.grid_l2_power_w is not None
            and v_snap.grid_l3_power_w is not None
        ):
            return v_snap.grid_l1_power_w + v_snap.grid_l2_power_w + v_snap.grid_l3_power_w
        return v_snap.grid_power_w  # may be None
```

### Coordinator Guard Integration Point
```python
# In coordinator.py _run_cycle(), after computing h_cmd/v_cmd in step 5 or 6:
h_cmd, v_cmd = self._apply_cross_charge_guard(h_snap, v_snap, h_cmd, v_cmd)
await self._huawei_ctrl.execute(h_cmd)
await self._victron_ctrl.execute(v_cmd)
```

### InfluxDB Cross-Charge Write
```python
# In influx_writer.py:
async def write_cross_charge_point(
    self, active: bool, waste_wh: float, episode_count: int
) -> None:
    try:
        point = (
            Point("ems_cross_charge")
            .field("active", active)
            .field("waste_wh", float(waste_wh))
            .field("episode_count", int(episode_count))
            .time(datetime.now(tz=timezone.utc))
        )
        await self._write_api.write(bucket=self._bucket, record=point)
    except Exception as exc:
        logger.warning("influx cross-charge write failed: %s", exc)
```

### Frontend Badge (EnergyFlowCard)
```typescript
// In EnergyFlowCard.tsx, near battery nodes:
{pool?.cross_charge_active && (
  <g className="cross-charge-badge">
    <rect x={135} y={170} width={130} height={24} rx={12} fill="#dc2626" className="xc-pulse" />
    <text x={200} y={186} textAnchor="middle" fill="white" fontSize={11} fontWeight={600}>
      Cross-Charge
    </text>
  </g>
)}
```

### CoordinatorState Extension
```python
# In controller_model.py CoordinatorState:
cross_charge_active: bool = False
cross_charge_waste_wh: float = 0.0
cross_charge_episode_count: int = 0
```

### Frontend Types Extension
```typescript
// In types.ts PoolState:
cross_charge_active: boolean;
cross_charge_waste_wh: number;
cross_charge_episode_count: number;
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x with anyio |
| Config file | pyproject.toml (asyncio_mode = "auto") |
| Quick run command | `python -m pytest tests/test_cross_charge.py -q` |
| Full suite command | `python -m pytest tests/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| XCHG-01 | Detection within 2 cycles | unit | `python -m pytest tests/test_cross_charge.py::test_detection_opposing_signs -x` | Wave 0 |
| XCHG-01 | Grid threshold check | unit | `python -m pytest tests/test_cross_charge.py::test_grid_threshold -x` | Wave 0 |
| XCHG-02 | Mitigation forces HOLDING | unit | `python -m pytest tests/test_cross_charge.py::test_mitigation_forces_holding -x` | Wave 0 |
| XCHG-02 | DecisionEntry logged | unit | `python -m pytest tests/test_cross_charge.py::test_decision_entry_logged -x` | Wave 0 |
| XCHG-03 | Debounce requires 2 cycles | unit | `python -m pytest tests/test_cross_charge.py::test_debounce_requires_min_cycles -x` | Wave 0 |
| XCHG-03 | Below threshold ignored | unit | `python -m pytest tests/test_cross_charge.py::test_below_threshold_ignored -x` | Wave 0 |
| XCHG-04 | Telegram alert on first detection | unit | `python -m pytest tests/test_cross_charge.py::test_telegram_first_detection -x` | Wave 0 |
| XCHG-04 | No repeat alert same episode | unit | `python -m pytest tests/test_cross_charge.py::test_no_repeat_alert -x` | Wave 0 |
| XCHG-05 | InfluxDB waste tracking | unit | `python -m pytest tests/test_cross_charge.py::test_influx_waste_write -x` | Wave 0 |
| XCHG-06 | CoordinatorState has fields | unit | `python -m pytest tests/test_cross_charge.py::test_state_fields -x` | Wave 0 |
| XCHG-06 | EnergyFlowCard badge | e2e | `npx playwright test --grep cross-charge` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_cross_charge.py -q`
- **Per wave merge:** `python -m pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_cross_charge.py` -- covers XCHG-01 through XCHG-05 (detector + coordinator integration)
- [ ] `frontend/tests/cross-charge.spec.ts` -- covers XCHG-06 (badge visibility)

## Sources

### Primary (HIGH confidence)
- `backend/coordinator.py` -- examined _run_cycle flow, injection pattern, _write_integrations, _build_state
- `backend/controller_model.py` -- ControllerSnapshot fields (power_w, grid_power_w, grid_l1/l2/l3_power_w), CoordinatorState extension pattern, DecisionEntry structure
- `backend/influx_writer.py` -- InfluxMetricsWriter write method patterns, Point construction, fire-and-forget error handling
- `backend/notifier.py` -- TelegramNotifier.send_alert() with per-category cooldown, ALERT_* constant pattern
- `backend/api.py` -- /api/health endpoint structure, get_integration_health() consumption
- `backend/main.py` -- lifespan wiring pattern for coordinator integrations
- `frontend/src/types.ts` -- PoolState interface, WebSocket payload structure
- `frontend/src/components/EnergyFlowCard.tsx` -- SVG coordinate layout, Props interface
- `frontend/src/components/OptimizationCard.tsx` -- card structure for adding waste stats
- `tests/test_coordinator.py` -- _snap() helper pattern, mock setup for controllers
- `tests/test_anomaly_detector.py` -- test pattern for detector modules
- `.planning/research/ARCHITECTURE.md` -- CrossChargeDetector interface design from v1.4 research

### Secondary (MEDIUM confidence)
- `.planning/research/PITFALLS.md` -- cross-charge physics (15% round-trip loss, DC-AC-DC conversion waste)
- `.planning/research/STACK.md` -- detection algorithm reference implementation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries, all existing infrastructure
- Architecture: HIGH -- every integration point maps 1:1 to established codebase patterns
- Pitfalls: HIGH -- sign conventions, null guards, and episode management are all verifiable from codebase inspection

**Research date:** 2026-03-24
**Valid until:** 2026-04-24 (stable -- no external dependencies to version-drift)
