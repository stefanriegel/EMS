# Phase 3: PV & Tariff Optimization - Research

**Researched:** 2026-03-22
**Domain:** Battery dispatch optimization (PV surplus allocation, tariff-aware charging, solar-predictive scheduling, time-of-day SoC profiles)
**Confidence:** HIGH

## Summary

Phase 3 is a pure algorithmic enhancement phase. No new libraries, no new infrastructure, no new protocols. All changes modify existing modules: `coordinator.py` (PV surplus weighting, min-SoC profiles), `scheduler.py` (predictive pre-charging), and `config.py` (min-SoC profile dataclass). The codebase is well-structured from Phases 1-2, with clear extension points already in place.

The biggest risk is not technical complexity but **behavioral correctness under edge cases** -- what happens when one battery is full during PV surplus distribution, when solar forecast is partially available, when time-of-day profile boundaries coincide with tariff windows, or when headroom weighting produces zero denominators. Each algorithm is individually simple (O(1) arithmetic), but the combination creates a state space that requires careful testing.

**Primary recommendation:** Implement as targeted modifications to the coordinator's `_allocate_charge()` and `_run_cycle()` methods, the scheduler's `compute_schedule()`, and `SystemConfig`. Extract each optimization concern into its own pure helper function for testability.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** PV surplus detected from negative P_target (grid export) -- coordinator already computes this, no separate detector
- **D-02:** Surplus allocation uses SoC headroom weighting: `headroom = max_soc - current_soc`. Replaces current "Huawei first" logic
- **D-03:** Charge rate limits respected per system; overflow routes to the other battery
- **D-04:** Battery at max_soc (95%) enters HOLDING, all surplus to other battery
- **D-05:** Each battery gets its own ChargeSlot with independent target SoC and charge rate (already exists)
- **D-06:** Both charge in parallel in tariff windows; Huawei (5kW) reaches target first, then time goes to Victron (3kW). No sequential staggering
- **D-07:** Grid charge power budgets are configurable constants (Huawei: 5000W, Victron: 3000W)
- **D-08:** Coordinator detects active charge slots and sets GRID_CHARGE role per controller
- **D-09:** Solar forecast from EVCC (`SolarForecast.tomorrow_energy_wh`) -- already fetched by scheduler
- **D-10:** Skip grid charge when `solar_forecast_kwh >= expected_consumption_kwh * 1.2` (20% margin)
- **D-11:** Partial solar coverage reduces target: `target_kwh = max(0, consumption - solar * 0.8)` (0.8 discount for real-world losses)
- **D-12:** No solar forecast available -> fall back to full grid charge (safety over optimization)
- **D-13:** Min-SoC profiles are `(start_hour, end_hour, min_soc_pct)` tuples per battery. First matching window wins; fallback to static value
- **D-14:** Default profiles: Huawei `[(6,16,30),(16,22,20),(22,6,10)]`, Victron `[(6,16,25),(16,22,15),(22,6,10)]`
- **D-15:** Profiles stored in SystemConfig as optional lists; None/empty = static min_soc (backward compat)
- **D-16:** Coordinator evaluates active profile each cycle (local time), passes effective floor to controllers
- **D-17:** All optimization logic in coordinator, not controllers
- **D-18:** Scheduler remains source for charge schedules; Phase 3 enhances `compute_schedule()` with solar-aware target reduction
- **D-19:** No intra-day schedule recomputation in this phase

### Claude's Discretion
- Internal method decomposition within coordinator for optimization logic
- Config dataclass structure for min-SoC profiles (list of tuples vs dedicated dataclass)
- Test scenario selection and fixture organization
- Whether to extract PV surplus allocation into a helper or keep it inline
- Exact logging format for optimization decisions

### Deferred Ideas (OUT OF SCOPE)
- Intra-day schedule recomputation
- Tariff-aware discharge timing
- Grid export optimization
- Weather-enhanced consumption forecast
- Multi-day scheduling
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| OPT-01 | PV surplus distributed across both batteries based on SoC headroom and charge rate limits | Coordinator `_allocate_charge()` rewrite with headroom weighting formula; existing `charge_headroom_w` field in ControllerSnapshot already available |
| OPT-02 | Tariff-aware grid charging targets each battery independently | Already implemented in Phase 2 via per-battery ChargeSlots; scheduler enhancement for solar-reduced targets |
| OPT-03 | Charge rate optimization: stagger charging in short tariff windows | Both charge in parallel (D-06); Huawei's higher rate means it finishes first, remaining window time goes to Victron. Existing `_compute_grid_charge_commands` needs minor enhancement |
| OPT-04 | Predictive pre-charging: skip grid charge when solar forecast covers demand | Scheduler's `compute_schedule()` already has `solar_kwh` and `consumption.today_expected_kwh`; add comparison logic with D-10/D-11 formulas |
| OPT-05 | Configurable min-SoC per time-of-day profiles | New dataclass in config.py; coordinator evaluates on each cycle via `datetime.now()` with configured timezone |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib `dataclasses` | 3.12+ | Min-SoC profile config | Already used for all config types in the project |
| Python stdlib `datetime` | 3.12+ | Time-of-day profile evaluation, timezone-aware comparison | Already used throughout coordinator and scheduler |
| Python stdlib `zoneinfo` | 3.12+ | Local timezone for min-SoC profile evaluation | Built-in since 3.9; no pytz needed |

### Supporting
No new dependencies required. This phase modifies existing code only.

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Custom headroom formula | Linear programming (scipy.optimize) | Massive overkill for 2-battery O(1) arithmetic; adds 50MB dependency |
| Tuple-based profiles | Pydantic model or NamedTuple | Pydantic not in stack; NamedTuple viable but standard dataclass matches project patterns |

## Architecture Patterns

### Recommended Project Structure
No new files needed. All changes go into existing modules:
```
backend/
  coordinator.py        # Modified: _allocate_charge() headroom weighting,
                        #           _get_effective_min_soc(), _run_cycle() profile eval
  scheduler.py          # Modified: compute_schedule() solar-aware target reduction
  config.py             # Modified: SystemConfig gains min_soc_profiles fields
tests/
  test_coordinator.py   # Extended: new test classes for OPT-01/02/03/05
  test_scheduler.py     # Extended: new test cases for OPT-04
```

### Pattern 1: SoC Headroom Weighting (OPT-01)
**What:** Replace "Huawei first" charge allocation with proportional distribution by SoC headroom.
**When to use:** Every PV surplus allocation in `_allocate_charge()`.
**Example:**
```python
# In coordinator.py — _allocate_charge()
def _allocate_charge(
    self,
    surplus_w: float,
    h_snap: ControllerSnapshot,
    v_snap: ControllerSnapshot,
) -> tuple[float, float]:
    """Allocate PV surplus weighted by SoC headroom (OPT-01).

    headroom = max_soc - current_soc.  Battery with more headroom
    gets a proportionally larger share.  Charge rate limits are
    respected; overflow routes to the other battery (D-03).
    """
    h_headroom_soc = max(0.0, self._full_soc_pct - h_snap.soc_pct)
    v_headroom_soc = max(0.0, self._full_soc_pct - v_snap.soc_pct)
    total_headroom = h_headroom_soc + v_headroom_soc

    if total_headroom == 0.0:
        return 0.0, 0.0

    # Proportional split by SoC headroom
    h_share = surplus_w * (h_headroom_soc / total_headroom)
    v_share = surplus_w * (v_headroom_soc / total_headroom)

    # Clamp to charge rate limits
    h_max = h_snap.charge_headroom_w  # hardware-reported max
    v_max = v_snap.charge_headroom_w

    h_charge = min(h_share, h_max)
    v_charge = min(v_share, v_max)

    # Overflow routing (D-03): if one hits rate limit, redirect to other
    h_overflow = max(0.0, h_share - h_max)
    v_overflow = max(0.0, v_share - v_max)

    h_charge += min(v_overflow, h_max - h_charge)
    v_charge += min(h_overflow, v_max - v_charge)

    return h_charge, v_charge
```

### Pattern 2: Time-of-Day Min-SoC Profile (OPT-05)
**What:** Evaluate a list of time windows against current local time; return the effective min-SoC floor.
**When to use:** On every 5s coordinator cycle, before discharge decisions.
**Example:**
```python
# In coordinator.py — new helper method
def _get_effective_min_soc(
    self,
    system: str,
    now_local: datetime,
) -> float:
    """Return the effective min-SoC for the given system at the given time.

    Evaluates profiles from SystemConfig; first matching window wins.
    Falls back to static min_soc if no profiles configured (D-15).
    """
    profiles = self._get_profiles_for_system(system)
    if not profiles:
        return self._get_static_min_soc(system)

    current_hour = now_local.hour
    for start_hour, end_hour, min_soc_pct in profiles:
        if start_hour <= end_hour:
            # Normal window: e.g., (6, 16, 30)
            if start_hour <= current_hour < end_hour:
                return min_soc_pct
        else:
            # Wrapping window: e.g., (22, 6, 10)
            if current_hour >= start_hour or current_hour < end_hour:
                return min_soc_pct

    return self._get_static_min_soc(system)
```

### Pattern 3: Solar-Aware Target Reduction (OPT-04)
**What:** Reduce or skip grid charge targets when solar forecast covers expected consumption.
**When to use:** In scheduler's `compute_schedule()`, after deriving consumption and solar forecasts.
**Example:**
```python
# In scheduler.py — inside compute_schedule(), after step 3 (solar forecast)
# and step 2 (consumption forecast)

# Predictive pre-charging (D-10, D-11)
if solar_kwh >= consumption.today_expected_kwh * 1.2:
    # Full solar coverage — skip grid charge entirely (D-10)
    charge_energy_kwh = 0.0
elif solar_kwh > 0:
    # Partial coverage — reduce target (D-11)
    charge_energy_kwh = max(
        0.0,
        consumption.today_expected_kwh - solar_kwh * 0.8,
    )
else:
    # No solar forecast (EVCC offline) — full charge (D-12)
    charge_energy_kwh = consumption.today_expected_kwh
```

### Pattern 4: Min-SoC Profile Dataclass
**What:** Dedicated dataclass for profile entries, stored as optional list on SystemConfig.
**Recommendation:** Use a simple dataclass (not raw tuples) for type safety and JSON serialization.
```python
# In config.py
@dataclass
class MinSocWindow:
    """A time-of-day window with a minimum SoC floor.

    Attributes:
        start_hour: Start hour (0-23, inclusive).
        end_hour:   End hour (0-23, exclusive). Wraps around midnight
                    when start_hour > end_hour (e.g., 22 to 6).
        min_soc_pct: Minimum SoC percentage during this window.
    """
    start_hour: int
    end_hour: int
    min_soc_pct: float

# Added to SystemConfig:
@dataclass
class SystemConfig:
    # ... existing fields ...
    huawei_min_soc_profile: list[MinSocWindow] | None = None
    victron_min_soc_profile: list[MinSocWindow] | None = None
```

### Anti-Patterns to Avoid
- **Coupling optimization to controllers:** Controllers are dumb executors. Never put headroom calculation or profile evaluation inside HuaweiController or VictronController. All logic stays in the coordinator.
- **Blocking calls in the 5s loop:** The scheduler runs nightly. Never call `compute_schedule()` or any DB query from `_run_cycle()`. All data is pre-computed and accessed via attributes.
- **Float equality in headroom checks:** Never compare `headroom == 0.0` without epsilon. Use `<= 0.0` or `< 1.0` to handle floating-point edge cases.
- **Hardcoding timezone:** Use `zoneinfo.ZoneInfo` with configurable timezone string (already `MODUL3_TIMEZONE` pattern exists). Never assume Europe/Berlin.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Timezone math | Manual UTC offset calculation | `zoneinfo.ZoneInfo` + `datetime.now(tz=...)` | DST transitions are deceptive edge cases; stdlib handles them correctly |
| Wrapping time windows (22:00-06:00) | Custom modular arithmetic | Pattern in code example above (start > end check) | Well-known pattern; custom implementations frequently miss midnight boundary |
| Schedule serialization | Manual dict construction for API | Existing `ChargeSchedule` / `OptimizationReasoning` dataclasses | Already serialized correctly by the API layer |

**Key insight:** This phase adds no new infrastructure. Every building block already exists in the codebase. The work is purely algorithmic -- modify existing methods, add new helper functions, extend existing tests.

## Common Pitfalls

### Pitfall 1: Division by Zero in Headroom Weighting
**What goes wrong:** Both batteries at 95% SoC (full) produce `total_headroom = 0.0`, causing ZeroDivisionError in the ratio calculation.
**Why it happens:** PV surplus exists (negative P_target from grid meter) but both batteries are already full -- common on sunny afternoons.
**How to avoid:** Guard with `if total_headroom == 0.0: return 0.0, 0.0` before computing ratios. The surplus goes to grid export (or is curtailed by the inverter).
**Warning signs:** Crash in `_allocate_charge()` on sunny days after both batteries reach 95%.

### Pitfall 2: Wrapping Time Windows Cross Midnight
**What goes wrong:** Profile `(22, 6, 10)` fails at midnight because `22 <= 0 < 6` is false in naive comparison.
**Why it happens:** The window crosses midnight but the comparison logic assumes `start < end`.
**How to avoid:** Explicitly handle the wrapping case: `if start > end: match = (hour >= start or hour < end)`. See Pattern 2 above.
**Warning signs:** Min-SoC jumps to fallback value at midnight instead of staying at the overnight floor.

### Pitfall 3: Stale Static Min-SoC Used When Profile Exists But Has Gaps
**What goes wrong:** Profile covers `[(6,16,30), (16,22,20)]` but not 22-6, so at 23:00 the static fallback (e.g., 10%) is used. User expects 24h coverage but left a gap.
**Why it happens:** The CONTEXT says "first matching window wins; fallback to static if no match" (D-13). This is correct behavior but users may not realize gaps exist.
**How to avoid:** Log a WARNING at startup if profiles don't cover 24h. Don't auto-fill -- just warn.
**Warning signs:** Different min-SoC behavior at times not covered by the profile.

### Pitfall 4: Solar Forecast = 0 vs Solar Forecast Unavailable
**What goes wrong:** Treating `solar_kwh = 0.0` (rainy day forecast) the same as "no forecast available" (EVCC offline). Zero is a valid forecast; absent data is not.
**Why it happens:** Both produce a falsy value in Python (`if not solar_kwh`).
**How to avoid:** Check `evcc_state.solar is not None` for availability (D-12), and use the numeric value for comparison (D-10, D-11). A rainy-day forecast of 0 kWh should result in full grid charge, same as no forecast -- but for different reasons.
**Warning signs:** Incorrect reasoning text in the schedule ("skipping grid charge due to solar" when solar is actually zero).

### Pitfall 5: Charge Rate Overflow Creates Negative Values
**What goes wrong:** After clamping `h_charge = min(h_share, h_max)` and routing overflow, the overflow math produces negative values when one battery has zero headroom.
**Why it happens:** `v_overflow = max(0.0, v_share - v_max)` is correct, but adding it to a battery that's also at its max creates inconsistency.
**How to avoid:** Clamp the overflow addition: `h_charge += min(v_overflow, max(0.0, h_max - h_charge))`. The `max(0.0, ...)` prevents going below zero when h_charge already equals h_max.
**Warning signs:** Negative charge values in logs, or total allocated exceeding surplus_w.

### Pitfall 6: Scheduler Target Reduction Applied After EVopt Branch
**What goes wrong:** The solar-aware target reduction (D-10/D-11) is applied in the formula fallback path but the EVopt branch already computes targets independently, creating contradictory schedules.
**Why it happens:** The EVopt optimizer has its own solar awareness, and the scheduler's reduction would double-count it.
**How to avoid:** Apply D-10/D-11 only in the formula fallback branch (`evopt is None`). When EVopt is present, trust its targets -- it already incorporates solar forecasts.
**Warning signs:** Grid charge targets are much lower than EVopt recommends when solar is good.

## Code Examples

### Effective Min-SoC Integration in _run_cycle
```python
# In _run_cycle(), replace static min-SoC references:
from zoneinfo import ZoneInfo

# At the top of _run_cycle or a dedicated helper:
tz = ZoneInfo("Europe/Berlin")  # from config, not hardcoded
now_local = datetime.now(tz=tz)

h_min_soc = self._get_effective_min_soc("huawei", now_local)
v_min_soc = self._get_effective_min_soc("victron", now_local)

# Then in the both-below-min check:
h_below_min = h_snap.soc_pct <= h_min_soc
v_below_min = v_snap.soc_pct <= v_min_soc
```

### Test Fixture Pattern for Headroom Weighting
```python
# In test_coordinator.py
class TestPvSurplusHeadroomWeighting:
    """OPT-01: PV surplus weighted by SoC headroom, not Huawei-first."""

    async def test_equal_soc_equal_split(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate_charge(
            surplus_w=4000.0,
            h_snap=_snap(soc=50.0, charge_headroom_w=5000.0),
            v_snap=_snap(soc=50.0, charge_headroom_w=8000.0),
        )
        # Equal SoC headroom (95-50=45 each) -> 50/50 split
        assert abs(h_w - 2000.0) < 1.0
        assert abs(v_w - 2000.0) < 1.0

    async def test_lower_soc_gets_more(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate_charge(
            surplus_w=3000.0,
            h_snap=_snap(soc=80.0, charge_headroom_w=5000.0),  # headroom=15%
            v_snap=_snap(soc=50.0, charge_headroom_w=8000.0),  # headroom=45%
        )
        # Victron has 3x the headroom -> gets ~75%
        assert v_w > h_w

    async def test_both_full_returns_zero(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate_charge(
            surplus_w=3000.0,
            h_snap=_snap(soc=96.0, charge_headroom_w=0.0),
            v_snap=_snap(soc=96.0, charge_headroom_w=0.0),
        )
        assert h_w == 0.0
        assert v_w == 0.0

    async def test_overflow_routing_when_rate_limited(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate_charge(
            surplus_w=6000.0,
            h_snap=_snap(soc=50.0, charge_headroom_w=2000.0),  # rate limited
            v_snap=_snap(soc=50.0, charge_headroom_w=8000.0),
        )
        # Both have equal headroom, so 3000 each initially.
        # Huawei capped at 2000, overflow 1000 goes to Victron.
        assert h_w == 2000.0
        assert v_w == 4000.0
```

### Solar-Aware Scheduler Test
```python
class TestPredictivePreCharging:
    """OPT-04: Skip/reduce grid charge when solar covers demand."""

    async def test_skip_grid_charge_when_solar_covers_120pct(self):
        """D-10: solar >= consumption * 1.2 -> skip."""
        scheduler = _make_scheduler()
        evcc_state = _make_evcc_state(solar_kwh=30.0, evopt=False)
        # consumption ~ 20 kWh, solar 30 >= 20*1.2=24 -> skip
        schedule = await scheduler.compute_schedule()
        for slot in schedule.slots:
            assert slot.target_soc_pct <= scheduler._sys_config.huawei_min_soc_pct \
                or slot.target_soc_pct <= scheduler._sys_config.victron_min_soc_pct

    async def test_no_solar_data_full_charge(self):
        """D-12: no solar forecast -> full charge (safety)."""
        scheduler = _make_scheduler()
        # evcc_state.solar = None
        schedule = await scheduler.compute_schedule()
        assert schedule.reasoning.charge_energy_kwh > 0
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Huawei-first charge (D-03 Phase 2) | SoC headroom weighting (D-02 Phase 3) | Phase 3 | Better utilization of both batteries; larger battery (Victron 64kWh) gets proportional surplus |
| Static min-SoC in SystemConfig | Time-of-day min-SoC profiles | Phase 3 | Enables conservative daytime reserves, aggressive overnight depletion |
| Fixed grid charge targets | Solar-aware target reduction | Phase 3 | Saves money by reducing unnecessary grid charging on sunny-forecast days |

**Deprecated/outdated:**
- `_allocate_charge()` "Huawei first then overflow" logic: replaced by headroom-weighted proportional split

## Open Questions

1. **Timezone configuration for min-SoC profiles**
   - What we know: `MODUL3_TIMEZONE` exists in config (`Europe/Berlin` default). Profiles need local time evaluation.
   - What's unclear: Should profiles use the same timezone or get their own config field?
   - Recommendation: Reuse `MODUL3_TIMEZONE` for profile evaluation. Both are German grid-time concepts. Add a dedicated env var later if needed.

2. **API exposure of effective min-SoC**
   - What we know: The API currently exposes `SystemConfig` with static min-SoC values.
   - What's unclear: Should `CoordinatorState` include the current effective min-SoC per system?
   - Recommendation: Add `huawei_effective_min_soc_pct` and `victron_effective_min_soc_pct` to `CoordinatorState` for dashboard consumption. Minimal effort, high observability value.

3. **Profile persistence through API config updates**
   - What we know: `POST /api/config` updates `SystemConfig` at runtime.
   - What's unclear: How profiles are serialized for the config endpoint.
   - Recommendation: Phase 4 (INT) handles API exposure. For Phase 3, profiles are set via env vars or constructor defaults. API persistence is deferred.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-anyio (asyncio_mode="auto") |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `python -m pytest tests/test_coordinator.py tests/test_scheduler.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| OPT-01 | PV surplus split by SoC headroom, rate limits, overflow | unit | `python -m pytest tests/test_coordinator.py::TestPvSurplusHeadroomWeighting -x` | Wave 0 |
| OPT-02 | Per-battery grid charge with independent targets | unit | `python -m pytest tests/test_coordinator.py::TestGridCharge -x` | Existing (extend) |
| OPT-03 | Parallel charging in tariff windows, faster finishes first | unit | `python -m pytest tests/test_coordinator.py::TestGridChargeStaggering -x` | Wave 0 |
| OPT-04 | Solar-aware target reduction in scheduler | unit | `python -m pytest tests/test_scheduler.py::TestPredictivePreCharging -x` | Wave 0 |
| OPT-05 | Time-of-day min-SoC profiles with wraparound | unit | `python -m pytest tests/test_coordinator.py::TestMinSocProfiles -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_coordinator.py tests/test_scheduler.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_coordinator.py::TestPvSurplusHeadroomWeighting` -- covers OPT-01 headroom weighting
- [ ] `tests/test_coordinator.py::TestGridChargeStaggering` -- covers OPT-03 parallel charge behavior
- [ ] `tests/test_coordinator.py::TestMinSocProfiles` -- covers OPT-05 time-of-day profiles with wraparound
- [ ] `tests/test_scheduler.py::TestPredictivePreCharging` -- covers OPT-04 solar-aware target reduction

## Sources

### Primary (HIGH confidence)
- Codebase inspection: `backend/coordinator.py` -- current `_allocate_charge()` implementation (Huawei-first), `_run_cycle()` structure, `_compute_grid_charge_commands()` grid charge handling
- Codebase inspection: `backend/scheduler.py` -- current `compute_schedule()` with solar_kwh and consumption forecast paths
- Codebase inspection: `backend/config.py` -- `SystemConfig` dataclass pattern, `OrchestratorConfig` with capacity fields
- Codebase inspection: `backend/schedule_models.py` -- `ChargeSlot`, `ChargeSchedule`, `OptimizationReasoning`, `SolarForecast` dataclasses
- Codebase inspection: `backend/controller_model.py` -- `ControllerSnapshot.charge_headroom_w` field, `BatteryRole` enum
- Codebase inspection: `tests/test_coordinator.py` -- test patterns, `_snap()` helper, `_make_coordinator()` factory

### Secondary (MEDIUM confidence)
- Phase 2 decisions (from STATE.md) -- coordinator owns all optimization logic, controllers are dumb executors
- CONTEXT.md locked decisions D-01 through D-19 -- full implementation specification from user discussion

### Tertiary (LOW confidence)
- None -- this phase is fully specified by existing code + CONTEXT.md decisions

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries; modifications to existing modules only
- Architecture: HIGH -- extension points already exist in coordinator and scheduler; patterns proven in Phase 2
- Pitfalls: HIGH -- identified from direct code analysis of edge cases in existing `_allocate_charge()`, `_compute_grid_charge_commands()`, and `compute_schedule()`

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable -- no external dependencies or fast-moving libraries)
