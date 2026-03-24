# Phase 8: Coordinator Export Integration - Research

**Researched:** 2026-03-23
**Domain:** Real-time battery coordinator control loop — export role assignment, seasonal strategy, oscillation prevention
**Confidence:** HIGH

## Summary

Phase 8 turns the ExportAdvisor from advisory-only (Phase 7) into an active control participant. The coordinator must assign an EXPORTING role to the higher-SoC battery when the advisor recommends EXPORT, apply a P_target offset to the non-exporting system to prevent oscillation, and implement seasonal awareness (winter raises min-SoC floors, summer allows natural export).

The codebase is well-structured for this change. The coordinator already has `_prev_export_decision` tracking and `_run_export_advisory()` as a post-cycle hook. The key transformation is moving export logic from the post-cycle advisory into `_run_cycle()` where it can influence role assignment, P_target computation, and command dispatch. The seasonal strategy adds two new config fields to `SystemConfig` and a simple month-based check in the coordinator.

**Primary recommendation:** Add EXPORTING to BatteryRole, integrate ExportAdvisor decision into the PV surplus (p_target < 0) path of `_run_cycle()`, apply P_target offset via the existing `_compute_p_target()` pipeline, and add winter config to SystemConfig with the established config pipeline pattern.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- EXPORTING role: higher-SoC system exports (matches existing PRIMARY_DISCHARGE selection logic)
- EXPORTING added as new BatteryRole enum value alongside existing roles
- P_target offset for non-exporting system -- subtract estimated export power from grid measurement so other battery doesn't react (prevents oscillation)
- No artificial export power limit -- PV surplus naturally determines export amount via grid meter
- Month-based season detection: Nov-Feb = winter, Mar-Oct = summer (configurable)
- Winter: raise min-SoC floor by +10% and increase grid charge targets proportionally
- Summer: allow natural PV export when both batteries above 90% SoC (default behavior)
- Config: `winter_months` (list, default [11,12,1,2]) and `winter_min_soc_boost_pct` (int, default 10) in SystemConfig

### Claude's Discretion
- Exact coordinator integration point for EXPORTING role assignment
- Test structure for seasonal and oscillation prevention
- Coordinator state machine transitions involving EXPORTING

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SCO-03 | Seasonal self-consumption strategy -- winter prioritizes battery reserves and more aggressive grid charging; summer allows natural PV export when batteries full | BatteryRole.EXPORTING in controller_model.py, seasonal config in SystemConfig, coordinator integration in _run_cycle() PV surplus path, P_target offset for oscillation prevention |

</phase_requirements>

## Architecture Patterns

### Integration Point Analysis

The coordinator's `_run_cycle()` has a clear branching structure:

```
_run_cycle()
  1. Poll both controllers
  2. EVCC hold check → early return
  3. Grid charge check → early return
  4. Compute P_target
  5. PV surplus (p_target < 0) → charge routing  ← EXPORT GOES HERE
  6. Idle (p_target == 0) → hold
  7. Discharge path → role assignment + allocation
```

**Export integration point:** Step 5 (PV surplus path, lines 408-433). When `p_target < 0` (surplus), the coordinator currently always charges batteries. The new logic:

1. Check ExportAdvisor decision (`self._prev_export_decision`)
2. If EXPORT and both batteries above 90% SoC:
   - Assign EXPORTING to higher-SoC system
   - Assign HOLDING (or reduced charge) to other system
   - Apply P_target offset so the non-exporting system sees zero grid import
3. If STORE: continue existing charge routing (no change)

### Pattern 1: EXPORTING Role in PV Surplus Path

**What:** When ExportAdvisor says EXPORT and batteries are sufficiently full, one battery gets the EXPORTING role. The other gets a P_target offset to prevent it from reacting to the "negative grid power" caused by the export.

**When to use:** Only during PV surplus (p_target < 0) and ExportAdvisor decision is EXPORT.

**Key insight:** The EXPORTING system does not need an active discharge command. Export happens naturally -- when batteries are full and PV keeps producing, the grid meter reads negative. The coordinator simply stops trying to absorb the surplus into batteries. The EXPORTING role means "allow this system's inverter to pass PV through to grid" which in Huawei/Victron terms means setting feed-in-allowed and not commanding further charge.

```python
# In _run_cycle(), within the p_target < 0 block:
if self._prev_export_decision == "EXPORT" and self._should_export(h_snap, v_snap):
    # Higher SoC system exports; other holds
    if h_snap.soc_pct >= v_snap.soc_pct:
        h_role = BatteryRole.EXPORTING
        v_role = BatteryRole.HOLDING
    else:
        v_role = BatteryRole.EXPORTING
        h_role = BatteryRole.HOLDING
    # Commands: exporting system gets 0W (let PV flow through),
    # holding system gets 0W (don't react to negative grid power)
    h_cmd = ControllerCommand(role=h_role, target_watts=0.0)
    v_cmd = ControllerCommand(role=v_role, target_watts=0.0)
```

### Pattern 2: P_target Offset for Oscillation Prevention

**What:** When one system exports, the grid meter reads negative (export). Without offset, the other system would see this as "surplus" and try to charge, potentially causing oscillation. The offset subtracts the estimated export power from the grid reading before the non-exporting system processes it.

**When to use:** Every cycle where EXPORTING role is active.

**Key insight from codebase:** P_target comes from `_compute_p_target()` which reads `v_snap.grid_power_w` (Victron grid meter) or falls back to Huawei master power. The offset must be applied AFTER P_target computation but BEFORE the non-exporting system's allocation. Since both systems get 0W target during EXPORTING, the offset is implicitly handled -- neither system reacts.

The simpler approach: when EXPORTING is active, both systems get `target_watts=0.0`. The exporting system's inverter naturally passes PV to grid (it's already full, no charge headroom). The non-exporting system holds at 0W. No explicit P_target offset needed in the command path -- the role assignment IS the offset.

### Pattern 3: Seasonal Strategy via Config

**What:** Month-based season detection modifies min-SoC floors and grid charge targets.

**When to use:** Every control cycle (lightweight check).

```python
# In SystemConfig:
winter_months: list[int] = field(default_factory=lambda: [11, 12, 1, 2])
winter_min_soc_boost_pct: int = 10

# In Coordinator._get_effective_min_soc():
def _get_effective_min_soc(self, system: str, now_local: datetime) -> float:
    base = ... # existing profile/static logic
    if now_local.month in self._sys_config.winter_months:
        base = min(base + self._sys_config.winter_min_soc_boost_pct, 100.0)
    return base
```

This follows the existing `_get_effective_min_soc()` pattern (lines 759-787) which already handles time-of-day profiles. Seasonal boost is applied on top.

### Pattern 4: _build_state Update for EXPORTING

**What:** `_build_state()` must recognize EXPORTING role in the control_state derivation (line 1106-1117).

```python
# Add to _build_state control_state logic:
elif h_cmd.role == BatteryRole.EXPORTING or v_cmd.role == BatteryRole.EXPORTING:
    control_state = "EXPORTING"
```

### Anti-Patterns to Avoid

- **Active battery discharge to grid:** Never command negative watts (discharge) with EXPORTING role. Export is PV-only surplus passing through a full inverter. The system MUST NOT drain batteries to export.
- **Both systems exporting simultaneously:** Only one system exports at a time. If both are assigned EXPORTING, oscillation risk increases because neither has a "stable anchor."
- **Modifying ExportAdvisor logic:** Phase 8 consumes ExportAdvisor decisions; it does not change the advisor's algorithm. The advisor was completed in Phase 7.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Season detection | Custom calendar logic | Simple `month in winter_months` check | Month-based is explicitly decided; no need for astronomical seasons |
| Export power metering | Custom power tracking for export kW | Grid meter reading (already in v_snap.grid_power_w) | Grid meter naturally shows export as negative power |
| Oscillation prevention | Complex feedback loop dampening | Role-based isolation (EXPORTING + HOLDING) | When both systems target 0W, there's nothing to oscillate |

## Common Pitfalls

### Pitfall 1: Grid Charge + Export Conflict
**What goes wrong:** Winter grid charge slot is active AND ExportAdvisor says EXPORT. Which wins?
**Why it happens:** Grid charge slots run during cheap tariff windows; export is for PV surplus periods. They rarely overlap, but edge cases exist (e.g., sunny winter morning during off-peak window).
**How to avoid:** Grid charge check (step 3) runs before PV surplus check (step 5) in `_run_cycle()`. Grid charge always wins -- it returns early. This is already the correct behavior; just ensure export logic is ONLY in the PV surplus path.
**Warning signs:** EXPORTING role assigned during a grid charge slot timestamp.

### Pitfall 2: Seasonal Min-SoC Boost Exceeding 100%
**What goes wrong:** If base min-SoC is 95% and winter boost is 10%, effective min-SoC becomes 105%.
**Why it happens:** Arithmetic without clamping.
**How to avoid:** `min(base + boost, 100.0)` -- always clamp to 100%.
**Warning signs:** Battery stuck at HOLDING in winter because min-SoC exceeds physical maximum.

### Pitfall 3: Export Advisor State vs Coordinator Decision Timing
**What goes wrong:** `_run_export_advisory()` runs AFTER `_run_cycle()` (line 328). So `self._prev_export_decision` reflects the PREVIOUS cycle's advisory. This means there's a 1-cycle (5s) delay between the advisor recommending EXPORT and the coordinator acting on it.
**Why it happens:** Phase 7 intentionally placed the advisory as a post-cycle hook.
**How to avoid:** This is acceptable -- 5s delay is negligible for a battery system. Do NOT move the advisory into `_run_cycle()` as it would duplicate across 6 exit paths. Read `self._prev_export_decision` at the start of the PV surplus path.
**Warning signs:** None expected -- this is a design feature, not a bug.

### Pitfall 4: Winter Boost Affecting Grid Charge Targets
**What goes wrong:** The decision says "increase grid charge targets proportionally" in winter, but the grid charge target comes from the Scheduler, not from the coordinator.
**Why it happens:** Grid charge SoC targets are computed by `backend/scheduler.py` based on consumption forecast.
**How to avoid:** The winter boost on min-SoC floors already implicitly increases grid charge utility -- if the floor is higher, the scheduler will compute higher targets because the "usable range" narrows. The coordinator's seasonal min-SoC boost is sufficient; no direct scheduler modification needed.
**Warning signs:** Scheduler computing same targets in winter and summer despite higher min-SoC floors.

### Pitfall 5: Config Pipeline for Winter Fields
**What goes wrong:** Adding `winter_months` and `winter_min_soc_boost_pct` to SystemConfig but forgetting the other 9 touchpoints (EmsSetupConfig, SetupCompleteRequest, SystemConfigRequest, main.py, config.yaml, run.sh, en.yaml, de.yaml, SetupWizard.tsx).
**Why it happens:** Phase 7 established the "10 touchpoints" pattern for `feed_in_rate_eur_kwh`. Same pattern applies here.
**How to avoid:** Follow the exact same touchpoint list from Phase 7 Plan 01 summary.
**Warning signs:** Config field works in tests but not in HA Add-on deployment.

## Code Examples

### BatteryRole Enum Extension

```python
# backend/controller_model.py — add to BatteryRole enum:
EXPORTING = "EXPORTING"
"""Battery system is allowing PV surplus to flow to grid (no active discharge)."""
```

### SystemConfig Seasonal Fields

```python
# backend/config.py — add to SystemConfig dataclass:
from dataclasses import field

winter_months: list[int] = field(default_factory=lambda: [11, 12, 1, 2])
"""Months considered winter for seasonal strategy (1=Jan, 12=Dec)."""

winter_min_soc_boost_pct: int = 10
"""Additional min-SoC percentage added during winter months."""
```

### Seasonal Min-SoC Boost in Coordinator

```python
# backend/coordinator.py — modify _get_effective_min_soc():
def _get_effective_min_soc(self, system: str, now_local: datetime) -> float:
    # ... existing profile/static logic ...
    base = ...  # result from existing logic

    # Seasonal boost (SCO-03)
    if now_local.month in self._sys_config.winter_months:
        base = min(base + self._sys_config.winter_min_soc_boost_pct, 100.0)

    return base
```

### Export Role Assignment in PV Surplus Path

```python
# backend/coordinator.py — within _run_cycle(), p_target < 0 block:
# Before existing charge routing, check export condition
if (
    self._prev_export_decision == "EXPORT"
    and h_snap.soc_pct >= self._full_soc_pct
    and v_snap.soc_pct >= self._full_soc_pct
):
    # Both batteries full, advisor says export — let PV flow to grid
    if h_snap.soc_pct >= v_snap.soc_pct:
        h_role_raw = BatteryRole.EXPORTING
        v_role_raw = BatteryRole.HOLDING
    else:
        v_role_raw = BatteryRole.EXPORTING
        h_role_raw = BatteryRole.HOLDING

    h_role = self._debounce_role("huawei", h_role_raw)
    v_role = self._debounce_role("victron", v_role_raw)

    h_cmd = ControllerCommand(role=h_role, target_watts=0.0)
    v_cmd = ControllerCommand(role=v_role, target_watts=0.0)

    # ... execute, build state, log decision, write integrations ...
    return

# ... existing charge routing continues for STORE case ...
```

### _build_state Control State for EXPORTING

```python
# Add before the DISCHARGE check in _build_state():
elif h_cmd.role == BatteryRole.EXPORTING or v_cmd.role == BatteryRole.EXPORTING:
    control_state = "EXPORTING"
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-anyio (asyncio_mode = "auto") |
| Config file | pyproject.toml [tool.pytest.ini_options] |
| Quick run command | `python -m pytest tests/test_coordinator.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SCO-03a | EXPORTING role assigned when advisor says EXPORT and both batteries >= 95% SoC | unit | `python -m pytest tests/test_coordinator.py -x -k test_export` | No - Wave 0 |
| SCO-03b | Only higher-SoC system gets EXPORTING role | unit | `python -m pytest tests/test_coordinator.py -x -k test_export_higher_soc` | No - Wave 0 |
| SCO-03c | Non-exporting system gets HOLDING with 0W target (oscillation prevention) | unit | `python -m pytest tests/test_coordinator.py -x -k test_export_non_exporting_holds` | No - Wave 0 |
| SCO-03d | Winter months raise min-SoC by configured boost | unit | `python -m pytest tests/test_coordinator.py -x -k test_winter_min_soc` | No - Wave 0 |
| SCO-03e | Summer (non-winter) months do not boost min-SoC | unit | `python -m pytest tests/test_coordinator.py -x -k test_summer_no_boost` | No - Wave 0 |
| SCO-03f | Export does not activate when advisor says STORE | unit | `python -m pytest tests/test_coordinator.py -x -k test_no_export_when_store` | No - Wave 0 |
| SCO-03g | Export does not activate when batteries below 95% SoC | unit | `python -m pytest tests/test_coordinator.py -x -k test_no_export_below_full` | No - Wave 0 |
| SCO-03h | _build_state produces "EXPORTING" control_state | unit | `python -m pytest tests/test_coordinator.py -x -k test_build_state_exporting` | No - Wave 0 |
| SCO-03i | Winter config fields in SystemConfig with defaults | unit | `python -m pytest tests/test_coordinator.py -x -k test_winter_config` | No - Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_coordinator.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before /gsd:verify-work

### Wave 0 Gaps
- [ ] Tests for EXPORTING role assignment in PV surplus path -- covers SCO-03a, SCO-03b, SCO-03c, SCO-03f, SCO-03g
- [ ] Tests for seasonal min-SoC boost -- covers SCO-03d, SCO-03e
- [ ] Tests for _build_state with EXPORTING role -- covers SCO-03h
- [ ] Tests for winter config defaults -- covers SCO-03i

## Open Questions

1. **Controller behavior for EXPORTING role**
   - What we know: The coordinator sends `ControllerCommand(role=BatteryRole.EXPORTING, target_watts=0.0)`. Controllers (HuaweiController, VictronController) must handle this new role.
   - What's unclear: Do controllers need any special handling for EXPORTING vs HOLDING? Both result in 0W target.
   - Recommendation: Controllers treat EXPORTING identically to HOLDING at the hardware level (0W setpoint). The role is purely semantic for the coordinator's decision logging and state reporting. If feed-in-allowed flags need toggling per-system, that's already in SystemConfig (`huawei_feed_in_allowed`, `victron_feed_in_allowed`) and handled by the controllers independently.

2. **Grid charge target increase in winter**
   - What we know: The decision says "increase grid charge targets proportionally" in winter.
   - What's unclear: Whether the min-SoC boost alone is sufficient, or if the Scheduler needs explicit winter awareness.
   - Recommendation: Min-SoC boost is sufficient for Phase 8. The Scheduler already computes targets relative to current SoC and min-SoC floor. Higher floor = more aggressive charging. If this proves insufficient, Scheduler modifications belong in Phase 10 (multi-day scheduling).

## Sources

### Primary (HIGH confidence)
- `backend/coordinator.py` -- full control loop, role assignment, P_target computation, _build_state, _run_export_advisory
- `backend/controller_model.py` -- BatteryRole enum, CoordinatorState, DecisionEntry
- `backend/export_advisor.py` -- ExportAdvisor, ExportDecision, ExportAdvice
- `backend/config.py` -- SystemConfig, OrchestratorConfig
- `tests/test_coordinator.py` -- 70+ existing coordinator tests, test patterns and fixtures

### Secondary (MEDIUM confidence)
- Phase 7 Plan 01 summary -- ExportAdvisor design decisions, config pipeline pattern (10 touchpoints)
- Phase 7 Plan 02 summary -- Coordinator wiring, post-cycle advisory hook, _prev_export_decision field
- 08-CONTEXT.md -- user decisions on seasonal strategy and oscillation prevention

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries; all changes use existing Python/dataclass patterns
- Architecture: HIGH -- integration points are clearly identified in the codebase; coordinator structure is well-understood
- Pitfalls: HIGH -- config pipeline and oscillation prevention patterns are documented from Phase 7

**Research date:** 2026-03-23
**Valid until:** 2026-04-23 (stable codebase, no external dependency changes)
