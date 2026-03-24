# Phase 19: Self-Tuning Control - Research

**Researched:** 2026-03-24
**Domain:** Adaptive control parameter tuning with safety gates
**Confidence:** HIGH

## Summary

Phase 19 implements automatic tuning of three control parameters -- dead-bands, ramp rates, and min-SoC profiles -- based on real operational data. The phase is purely algorithmic: no new dependencies, no ML models (sklearn), no external services. It builds on the existing coordinator parameter infrastructure (setters, clamp ranges, HA command handlers) and the Phase 17 MAPE tracking for activation gating.

The key architectural challenge is injecting state transition counting into the coordinator's 5-second control loop without adding I/O or blocking work. The nightly computation runs in the existing `_nightly_scheduler_loop` after anomaly training. All tuning state persists as JSON in `/config/ems_models/tuning_state.json`, following the established pattern from anomaly_detector and consumption_forecaster.

**Primary recommendation:** Build a single `SelfTuner` class that receives per-cycle transition events from the coordinator, accumulates hourly statistics in memory, and computes parameter adjustments nightly. Shadow mode logs recommendations for 14 days before enabling live application.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- Single `backend/self_tuner.py` with SelfTuner class managing all 3 tuning domains
- Track ControlState changes in the orchestrator's 5s loop, accumulate per-hour transition counters for oscillation rate
- Persist tuning state in /config/ems_models/tuning_state.json (shadow log, current params, history)
- Nightly tuning computation runs in the existing nightly scheduler loop, after anomaly training
- SelfTuner has a `mode` field: "shadow" or "live". Shadow logs recommended params vs actuals but never applies. Auto-promotes to live after 14 consecutive days of shadow logging
- 10% per-night bound: each parameter stores current + base value, nightly adjustment capped at abs(new - current) <= 0.10 * base
- Absolute safe minimums use existing coordinator clamp ranges: dead_band 50W, ramp_rate 100W, min_soc 10%
- Automatic revert: if oscillation rate increases >20% after a parameter change, revert to previous value on next nightly run
- Dead-band tuning driven by oscillation rate (state transitions/hour)
- Ramp rate tuning driven by grid import spikes
- Min-SoC profile tuning driven by consumption patterns using forecaster predictions
- Tuning status exposed via /api/ml/status with a `self_tuning` section

### Claude's Discretion
- Specific oscillation rate thresholds for dead-band adjustment direction
- Grid import spike detection algorithm (threshold, window size)
- Min-SoC profile granularity (hourly vs 4-hour blocks)
- Internal data structures for transition tracking and shadow logging

### Deferred Ideas (OUT OF SCOPE)
None

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TUNE-01 | Oscillation detector counts state transitions per hour from coordinator decisions | Coordinator `_build_state` sets `CoordinatorState` each cycle; inject `record_transition()` call after state build, comparing previous vs current `pool_status` |
| TUNE-02 | Dead-band auto-tuning based on oscillation rate | Coordinator has `_huawei_deadband_w`/`_victron_deadband_w` with clamp range (50, 1000)/(50, 500). SelfTuner adjusts via setter |
| TUNE-03 | Ramp rate auto-tuning based on grid import spikes | Coordinator has `_huawei_ramp_w_per_cycle`/`_victron_ramp_w_per_cycle` with clamp range (100, 2000). Grid power available from ControllerSnapshot |
| TUNE-04 | Min-SoC profile auto-tuning based on consumption patterns | Existing `MinSocWindow` dataclass and `_get_effective_min_soc()` with profile support. ConsumptionForecaster `predict_hourly()` provides consumption forecast |
| TUNE-05 | Shadow mode for 14 days before live application | JSON persistence pattern from anomaly_detector; mode field in tuning_state.json |
| TUNE-06 | Bounded changes: max 10% per night with absolute safe bounds | Coordinator `_NUMBER_RANGES` defines clamp ranges; SelfTuner enforces 10% cap on top |
| TUNE-07 | Automatic rollback if oscillation rate increases | SelfTuner stores previous values in tuning_state.json; nightly run compares oscillation rates |
| TUNE-08 | Activation gate: MAPE < 25% and 60+ days of data | `ConsumptionForecaster.get_ml_status()` returns `mape.current` and `days_of_history` |

</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib | 3.12+ | All computation (no new deps) | Project constraint: no new core dependencies for v1.3 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| json (stdlib) | - | Tuning state persistence | Save/load tuning_state.json |
| dataclasses (stdlib) | - | Data models for tuning parameters | TuningState, ParameterHistory |
| logging (stdlib) | - | Observability | All tuning decisions logged |

No new dependencies required. This phase is pure Python algorithmic code using existing project infrastructure.

## Architecture Patterns

### Recommended Project Structure
```
backend/
├── self_tuner.py          # SelfTuner class (all 3 tuning domains)
├── coordinator.py         # Modified: inject record_transition() call
├── main.py                # Modified: construct SelfTuner, wire into nightly loop
├── api.py                 # Modified: extend /api/ml/status with self_tuning section
tests/
├── test_self_tuner.py     # Unit tests for all tuning logic
```

### Pattern 1: Per-Cycle Event Recording (Hot Path)
**What:** The coordinator calls `self_tuner.record_transition(prev_status, new_status, grid_power_w)` at the end of each 5s cycle. This must be zero-allocation, zero-I/O -- pure in-memory counter updates.
**When to use:** Every control cycle, after `_build_state()`.
**Example:**
```python
# In coordinator._loop(), after _run_cycle():
if self._self_tuner is not None:
    state = self._state
    if state is not None:
        self._self_tuner.record_cycle(
            pool_status=state.pool_status,
            grid_power_w=getattr(self._last_v_snap, 'grid_power_w', 0.0) or 0.0,
        )
```

Key insight: The coordinator's `CoordinatorState` has a `pool_status` field (from `PoolStatus` enum) that reflects the current state. Comparing previous vs current pool_status each cycle counts transitions. The coordinator already tracks `_prev_h_role` and `_prev_v_role` -- this follows the same pattern.

### Pattern 2: Nightly Batch Computation
**What:** Once per night (in `_nightly_scheduler_loop`), call `self_tuner.nightly_tune(forecaster)`. This reads accumulated hourly stats, computes recommendations, and either logs (shadow) or applies (live) parameter changes.
**When to use:** After anomaly training, before schedule computation.
**Example:**
```python
# In _nightly_scheduler_loop:
if self_tuner is not None:
    try:
        await self_tuner.nightly_tune(consumption_forecaster)
        logger.info("nightly-scheduler: self-tuner completed")
    except Exception as exc:
        logger.warning("nightly-scheduler: self-tuner failed: %s", exc)
```

### Pattern 3: Coordinator Parameter Injection
**What:** In live mode, SelfTuner directly sets coordinator parameters via a setter method. This follows the existing `set_anomaly_detector()` / `set_export_advisor()` injection pattern.
**When to use:** After nightly computation in live mode.
**Example:**
```python
# SelfTuner holds a reference to coordinator
def _apply_params(self, coordinator) -> None:
    if self._mode != "live":
        return
    coordinator._huawei_deadband_w = self._params.huawei_deadband_w
    coordinator._victron_deadband_w = self._params.victron_deadband_w
    coordinator._huawei_ramp_w_per_cycle = self._params.ramp_rate_w
    coordinator._victron_ramp_w_per_cycle = self._params.ramp_rate_w
```

### Pattern 4: JSON State Persistence
**What:** Tuning state saved to `/config/ems_models/tuning_state.json`. Follows anomaly_detector's pattern of JSON read/write with fire-and-forget error handling.
**When to use:** After each nightly computation.

### Anti-Patterns to Avoid
- **I/O in the 5s loop:** Never write to disk or make network calls in `record_cycle()`. Accumulate in memory only.
- **sklearn in the hot path:** This phase has no sklearn usage at all -- it is pure arithmetic.
- **Unbounded parameter changes:** Always enforce both the 10% per-night cap AND the absolute clamp ranges from coordinator's `_NUMBER_RANGES`.
- **Shared mutable state without GIL awareness:** The coordinator runs in the asyncio event loop (single-threaded). The nightly tuner also runs in the event loop (no executor needed -- it is pure computation, not blocking I/O). No thread safety issues.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Parameter clamp ranges | Custom min/max constants | Coordinator's `_NUMBER_RANGES` dict | Already defined: deadband_huawei (50, 1000), deadband_victron (50, 500), ramp_rate (100, 2000), min_soc (10, 100) |
| MAPE retrieval | Custom MAPE computation | `ConsumptionForecaster.get_ml_status()["mape"]["current"]` | Already computed daily and persisted |
| Training data length | Custom day counting | `ConsumptionForecaster.get_ml_status()["days_of_history"]` | Already tracked |
| Min-SoC profile structure | Custom time window format | `MinSocWindow(start_hour, end_hour, min_soc_pct)` from config.py | Already used by coordinator's `_get_effective_min_soc()` |
| Supervisor persistence | Custom file persistence | Coordinator's `_persist_to_supervisor()` pattern | Already handles HA Add-on options persistence |

## Common Pitfalls

### Pitfall 1: Oscillation Rate Threshold Too Aggressive
**What goes wrong:** Setting dead-band adjustment thresholds too tight causes the tuner to oscillate between increasing and decreasing the dead-band every night.
**Why it happens:** Oscillation rate naturally varies day-to-day (weekday vs weekend load patterns).
**How to avoid:** Use a 7-day rolling average of oscillation rate for comparison, not single-day values. Only adjust when the trend is consistent across multiple days.
**Warning signs:** Dead-band value ping-pongs between two values on consecutive nights.

### Pitfall 2: Grid Import Spike False Positives
**What goes wrong:** EV charging or heat pump defrost cycles trigger "grid import spikes" that increase ramp rate unnecessarily.
**Why it happens:** Large loads are normal events, not control deficiencies.
**How to avoid:** Only count spikes that coincide with a state transition (the ramp rate was the bottleneck). A spike during steady-state discharge is a load event, not a ramp issue.
**Warning signs:** Ramp rate climbs to maximum (2000 W/cycle) within a few weeks.

### Pitfall 3: Shadow Mode Day Counter Reset
**What goes wrong:** Shadow mode counter resets to 0 on EMS restart, delaying live activation indefinitely.
**Why it happens:** Counter stored only in memory.
**How to avoid:** Persist shadow start date and shadow day count in tuning_state.json. On restart, resume from persisted state.
**Warning signs:** `self_tuning.shadow_days` in API response never reaches 14.

### Pitfall 4: Min-SoC Profile Conflicts with User Override
**What goes wrong:** SelfTuner sets a min-SoC profile, then user overrides via HA command. Next nightly run overwrites user's choice.
**Why it happens:** No distinction between user-set and tuner-set parameters.
**How to avoid:** Track whether each parameter was last set by the tuner or by HA command. If HA command set it since last nightly run, skip that parameter's tuning.
**Warning signs:** User complains that HA slider resets every night.

### Pitfall 5: 10% Bound Calculated Wrong
**What goes wrong:** 10% of current value (not base value) causes the bound to shrink as parameter decreases, eventually preventing any further decrease.
**Why it happens:** Using current instead of base (initial/default) value for percentage calculation.
**How to avoid:** Store the base value (the value when tuning started) separately. Calculate 10% of base, not current.
**Warning signs:** Parameter converges asymptotically and never reaches the optimal value.

## Code Examples

### SelfTuner Class Structure
```python
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TuningParams:
    """Current tuned parameter values."""
    huawei_deadband_w: int = 300
    victron_deadband_w: int = 150
    ramp_rate_w: int = 2000
    huawei_min_soc_profile: list[dict] | None = None
    victron_min_soc_profile: list[dict] | None = None


@dataclass
class TuningState:
    """Persisted tuning state."""
    mode: str = "shadow"  # "shadow" or "live"
    shadow_start_date: str | None = None
    shadow_days: int = 0
    current_params: dict = field(default_factory=dict)
    base_params: dict = field(default_factory=dict)
    previous_params: dict = field(default_factory=dict)
    last_oscillation_rate: float | None = None
    shadow_log: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)


class SelfTuner:
    """Adaptive parameter tuner for dead-bands, ramp rates, and min-SoC profiles."""

    def __init__(self, state_path: str = "/config/ems_models/tuning_state.json") -> None:
        self._state_path = Path(state_path)
        self._state = self._load_state()

        # Per-cycle counters (in-memory only, reset each hour)
        self._prev_pool_status: str | None = None
        self._hourly_transitions: int = 0
        self._hourly_grid_spikes: int = 0
        self._hour_start: float = time.monotonic()
        self._hourly_stats: list[dict] = []  # accumulated hourly summaries

    def record_cycle(self, pool_status: str, grid_power_w: float) -> None:
        """Called every 5s from coordinator. Zero I/O, pure counter updates."""
        # Count transitions
        if self._prev_pool_status is not None and pool_status != self._prev_pool_status:
            self._hourly_transitions += 1
        self._prev_pool_status = pool_status

        # Detect grid import spikes (>500W import during transition)
        if grid_power_w > 500 and self._prev_pool_status != pool_status:
            self._hourly_grid_spikes += 1

        # Roll over hourly
        elapsed = time.monotonic() - self._hour_start
        if elapsed >= 3600:
            self._hourly_stats.append({
                "transitions": self._hourly_transitions,
                "grid_spikes": self._hourly_grid_spikes,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            })
            self._hourly_transitions = 0
            self._hourly_grid_spikes = 0
            self._hour_start = time.monotonic()
            # Keep max 7 days of hourly stats (168 entries)
            if len(self._hourly_stats) > 168:
                self._hourly_stats = self._hourly_stats[-168:]
```

### Activation Gate Check
```python
def _check_activation_gate(self, forecaster) -> bool:
    """Return True if self-tuning preconditions are met."""
    if forecaster is None:
        logger.info("self-tuner: gate check failed — no forecaster")
        return False
    status = forecaster.get_ml_status()
    mape = status.get("mape", {}).get("current")
    days = status.get("days_of_history", 0)

    if mape is None or mape >= 25.0:
        logger.info(
            "self-tuner: gate check failed — MAPE=%.1f (need <25%%)",
            mape or -1,
        )
        return False
    if days < 60:
        logger.info(
            "self-tuner: gate check failed — days_of_history=%d (need >=60)",
            days,
        )
        return False
    return True
```

### 10% Bounded Adjustment
```python
# Clamp ranges from coordinator._NUMBER_RANGES
_CLAMP_RANGES = {
    "huawei_deadband_w": (50, 1000),
    "victron_deadband_w": (50, 500),
    "ramp_rate_w": (100, 2000),
}

def _bounded_adjust(
    self,
    param_name: str,
    current: float,
    base: float,
    recommended: float,
) -> float:
    """Apply 10% per-night bound and absolute clamp range."""
    max_delta = 0.10 * base
    delta = recommended - current
    if abs(delta) > max_delta:
        delta = max_delta if delta > 0 else -max_delta
    new_val = current + delta
    lo, hi = _CLAMP_RANGES[param_name]
    return max(lo, min(hi, new_val))
```

### Automatic Rollback
```python
def _check_rollback(self, current_osc_rate: float) -> bool:
    """Revert parameters if oscillation rate increased >20% after change."""
    prev_rate = self._state.last_oscillation_rate
    if prev_rate is None or prev_rate == 0:
        return False
    increase_pct = (current_osc_rate - prev_rate) / prev_rate * 100
    if increase_pct > 20:
        logger.warning(
            "self-tuner: rollback — oscillation rate increased %.1f%% "
            "(%.2f → %.2f transitions/hour)",
            increase_pct, prev_rate, current_osc_rate,
        )
        return True
    return False
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Fixed dead-bands (hardcoded 300W/150W) | Adaptive dead-bands based on oscillation rate | Phase 19 | Reduces unnecessary state transitions |
| Fixed ramp rate (2000W/1000W per cycle) | Adaptive ramp rate based on grid spike frequency | Phase 19 | Reduces grid import peaks during transitions |
| Static min-SoC (10%/15%) | Profile-based min-SoC already exists; Phase 19 auto-tunes the profile | v1.2 (profiles), v1.3 (auto-tune) | Better reserves before peak consumption |

## Open Questions

1. **Oscillation Rate Thresholds (Claude's Discretion)**
   - What we know: Transitions/hour is the signal. Default dead-bands are 300W/150W.
   - What's unclear: What oscillation rate is "too high" vs "acceptable"? Likely 4-8 transitions/hour is normal, >12 is problematic.
   - Recommendation: Start with 6 transitions/hour as target. If above, increase dead-band. If below 2, decrease. Use 7-day rolling average.

2. **Grid Import Spike Algorithm (Claude's Discretion)**
   - What we know: Grid power is available from ControllerSnapshot. Ramp rate limits how fast the battery responds.
   - What's unclear: What constitutes a "spike" -- magnitude, duration, or both?
   - Recommendation: A spike is grid_power_w > 500W coinciding with a state transition within the last 30 seconds. Count spikes per day. If >3 spikes/day, increase ramp rate; if 0 for 7 days, decrease.

3. **Min-SoC Profile Granularity (Claude's Discretion)**
   - What we know: `MinSocWindow` supports arbitrary start_hour/end_hour. Consumption forecaster provides hourly predictions.
   - What's unclear: How many windows to define -- 24 hourly, 6x 4-hour, or fewer?
   - Recommendation: Use 4-hour blocks (6 windows: 0-4, 4-8, 8-12, 12-16, 16-20, 20-24). Hourly is too noisy; 4-hour smooths out variance while capturing morning/evening peaks.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + anyio |
| Config file | pyproject.toml (asyncio_mode = "auto") |
| Quick run command | `python -m pytest tests/test_self_tuner.py -q` |
| Full suite command | `python -m pytest tests/ -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TUNE-01 | Counts state transitions per hour | unit | `python -m pytest tests/test_self_tuner.py::test_transition_counting -x` | Wave 0 |
| TUNE-02 | Dead-band adjusts based on oscillation rate | unit | `python -m pytest tests/test_self_tuner.py::test_deadband_tuning -x` | Wave 0 |
| TUNE-03 | Ramp rate adjusts based on grid spikes | unit | `python -m pytest tests/test_self_tuner.py::test_ramp_rate_tuning -x` | Wave 0 |
| TUNE-04 | Min-SoC profile adjusts based on consumption | unit | `python -m pytest tests/test_self_tuner.py::test_min_soc_profile_tuning -x` | Wave 0 |
| TUNE-05 | Shadow mode logs for 14 days | unit | `python -m pytest tests/test_self_tuner.py::test_shadow_mode -x` | Wave 0 |
| TUNE-06 | Changes bounded to 10% with safe minimums | unit | `python -m pytest tests/test_self_tuner.py::test_bounded_adjustment -x` | Wave 0 |
| TUNE-07 | Automatic rollback on oscillation increase | unit | `python -m pytest tests/test_self_tuner.py::test_automatic_rollback -x` | Wave 0 |
| TUNE-08 | Activation gate: MAPE <25%, 60+ days | unit | `python -m pytest tests/test_self_tuner.py::test_activation_gate -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_self_tuner.py -q`
- **Per wave merge:** `python -m pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_self_tuner.py` -- covers TUNE-01 through TUNE-08
- [ ] No new framework install needed -- pytest + anyio already configured

## Sources

### Primary (HIGH confidence)
- `backend/coordinator.py` -- direct code inspection of deadband, ramp rate, min-SoC parameters and clamp ranges
- `backend/consumption_forecaster.py` -- direct code inspection of get_ml_status(), MAPE tracking, predict_hourly()
- `backend/anomaly_detector.py` -- pattern reference for nightly training and JSON persistence
- `backend/main.py` -- direct code inspection of _nightly_scheduler_loop wiring
- `backend/config.py` -- direct code inspection of SystemConfig, MinSocWindow, OrchestratorConfig
- `backend/unified_model.py` -- direct code inspection of ControlState enum

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, all patterns established in prior phases
- Architecture: HIGH -- follows exact patterns from Phase 17 (forecaster) and Phase 18 (anomaly detector)
- Pitfalls: MEDIUM -- oscillation rate thresholds and spike detection are heuristic; may need tuning in practice

**Research date:** 2026-03-24
**Valid until:** 2026-04-24 (stable domain, no external dependency changes expected)
