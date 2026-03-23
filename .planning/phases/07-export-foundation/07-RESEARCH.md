# Phase 7: Export Foundation - Research

**Researched:** 2026-03-23
**Domain:** PV export vs. self-consumption decision engine
**Confidence:** HIGH

## Summary

This phase adds an ExportAdvisor that decides whether surplus PV energy should be exported (earning the fixed feed-in rate) or stored in batteries (avoiding future import costs). The core economics are simple: if the feed-in rate exceeds the future import cost for that kWh, export; otherwise store. The implementation follows established project patterns -- advisory class with DI injection into the Coordinator, fire-and-forget error handling, and decision logging via the existing ring buffer.

The codebase already provides all prerequisite infrastructure: `CompositeTariffEngine` for import pricing, `ConsumptionForecaster` for demand prediction, `DecisionEntry` for decision audit trail, `SystemConfig` for per-system configuration, and the setup wizard/HA Add-on config pipeline. No new dependencies are needed.

**Primary recommendation:** Build `ExportAdvisor` as a stateless per-cycle advisor that compares feed-in revenue against expected import cost for the next 4-6 hours. Inject it into the Coordinator alongside the existing Scheduler using `set_export_advisor()`. Log EXPORT/SELF_CONSUME transitions as `DecisionEntry` records with the same ring buffer pattern.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- New `backend/export_advisor.py` module following advisory pattern (like Scheduler)
- ConsumptionForecaster injected at init via DI (same pattern as Scheduler)
- Forward-looking consumption reserve covers next 4-6 hours (evening peak coverage)
- Per-cycle advisory (every 5s control loop) -- coordinator queries ExportAdvisor each cycle
- `SystemConfig.feed_in_rate_eur_kwh: float = 0.074` -- single field in existing config
- Setup wizard Step 5 (Tariff Settings) -- alongside Octopus/Modul3 rates
- Runtime override via `POST /api/config` (matches existing config update pattern)
- Validation: >= 0, warn if > 0.15 (suspiciously high for German feed-in)
- HA Add-on config.yaml and run.sh extended with feed_in_rate field
- Add `EXPORT` and `SELF_CONSUME` decision types to DecisionEntry
- Log only on state changes (STORE->EXPORT or EXPORT->STORE) -- matches existing "role change" pattern
- Reasoning includes: feed-in rate, current import rate, forecast demand next 4h, battery SoC
- Use existing `/api/decisions` endpoint -- no new routes needed
- Feed-in rate is 7.4 ct/kWh (0.074 EUR/kWh) -- fixed, not time-varying
- Never discharge battery to grid -- export only from direct PV surplus when batteries full
- Winter critical: having too little in battery is worse than exporting surplus
- Summer: full battery is fine, natural PV export acceptable

### Claude's Discretion
- Internal algorithm design for forward-looking reserve calculation
- Test structure and coverage approach
- Error handling when ConsumptionForecaster is unavailable (graceful degradation)

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SCO-01 | System never actively discharges battery to grid -- export only from direct PV surplus when batteries are full | ExportAdvisor returns STORE when batteries not full; Coordinator never issues negative (discharge) commands that target grid export. Existing `feed_in_allowed` flags on SystemConfig already guard against per-system grid export. |
| SCO-02 | Feed-in rate configurable as a single EUR/kWh value (default 0.074) in setup config and HA Add-on options | Add `feed_in_rate_eur_kwh` to SystemConfig, EmsSetupConfig, config.yaml schema, run.sh export, and SetupWizard Step 5. Follows exact pattern of existing tariff config fields. |
| SCO-04 | Self-consumption and export decisions logged with structured reasoning in /api/decisions | Extend DecisionEntry trigger types. Log on state transitions only. Existing `/api/decisions` endpoint and `get_decisions()` method serve them without modification. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib | 3.12+ | All logic is pure Python -- datetime, dataclasses, logging | No external deps needed for the advisor algorithm |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | 8+ | Unit testing ExportAdvisor logic | All test scenarios |
| pytest-anyio | latest | Async test support (if testing async forecaster calls) | When testing with mocked ConsumptionForecaster |
| pytest-mock | latest | Mocking tariff engine and forecaster | All advisor tests |

No new dependencies. The ExportAdvisor consumes only existing project interfaces (`CompositeTariffEngine`, `ConsumptionForecaster`, `SystemConfig`).

## Architecture Patterns

### Recommended Project Structure
```
backend/
  export_advisor.py          # NEW: ExportAdvisor class
  config.py                  # MODIFY: add feed_in_rate_eur_kwh to SystemConfig
  setup_config.py            # MODIFY: add feed_in_rate to EmsSetupConfig
  coordinator.py             # MODIFY: query ExportAdvisor in _run_cycle()
  controller_model.py        # MODIFY: add EXPORT/SELF_CONSUME trigger types to DecisionEntry
  api.py                     # MODIFY: add feed_in_rate to ConfigUpdateBody
  main.py                    # MODIFY: wire ExportAdvisor in lifespan
ha-addon/
  config.yaml                # MODIFY: add feed_in_rate option + schema
  run.sh                     # MODIFY: export FEED_IN_RATE_EUR_KWH env var
frontend/src/pages/
  SetupWizard.tsx             # MODIFY: add feed-in rate field to StepTariff
tests/
  test_export_advisor.py     # NEW: unit tests
```

### Pattern 1: Advisory Class with DI Injection (Scheduler Pattern)
**What:** A stateless advisor class injected into the Coordinator via a setter method, queried each control cycle.
**When to use:** Every cycle in `_run_cycle()`, after grid-charge check and before P_target computation.
**Example:**
```python
# backend/export_advisor.py
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.consumption_forecaster import ConsumptionForecaster
    from backend.tariff import CompositeTariffEngine
    from backend.config import SystemConfig

logger = logging.getLogger("ems.export_advisor")

class ExportDecision(str, Enum):
    STORE = "STORE"
    EXPORT = "EXPORT"

@dataclass
class ExportAdvice:
    decision: ExportDecision
    reasoning: str
    feed_in_rate: float
    import_rate: float
    forecast_demand_kwh: float
    battery_soc_pct: float

class ExportAdvisor:
    def __init__(
        self,
        tariff_engine: CompositeTariffEngine,
        forecaster: ConsumptionForecaster | None,
        sys_config: SystemConfig,
    ) -> None:
        self._tariff = tariff_engine
        self._forecaster = forecaster
        self._sys_config = sys_config
        self._last_decision: ExportDecision = ExportDecision.STORE

    def advise(
        self,
        combined_soc_pct: float,
        huawei_soc_pct: float,
        victron_soc_pct: float,
        now: datetime,
    ) -> ExportAdvice:
        """Per-cycle advisory: STORE or EXPORT."""
        ...
```

### Pattern 2: Decision Logging on State Transition
**What:** Log a DecisionEntry only when the export/self-consume state changes, not every cycle.
**When to use:** In `_check_and_log_decision()` or a new export-specific check in the Coordinator.
**Example:**
```python
# In Coordinator.__init__:
self._prev_export_decision: str = "STORE"

# In _run_cycle(), after getting ExportAdvice:
if advice.decision.value != self._prev_export_decision:
    entry = DecisionEntry(
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        trigger="export_change",  # new trigger type
        huawei_role=h_cmd.role.value,
        victron_role=v_cmd.role.value,
        p_target_w=p_target,
        huawei_allocation_w=h_cmd.target_watts,
        victron_allocation_w=v_cmd.target_watts,
        pool_status=pool_status,
        reasoning=advice.reasoning,
    )
    self._decisions.append(entry)
    self._prev_export_decision = advice.decision.value
```

### Pattern 3: Config Pipeline (Existing Pattern)
**What:** Adding a new config field flows through 6 touchpoints in sequence.
**When to use:** For `feed_in_rate_eur_kwh`.
**Touchpoints in order:**
1. `SystemConfig` dataclass field with default (backend/config.py)
2. `SystemConfig.from_env()` -- read `FEED_IN_RATE_EUR_KWH` env var (but note: SystemConfig doesn't currently have `from_env()` -- it's constructed directly in main.py lifespan)
3. `EmsSetupConfig` field (backend/setup_config.py)
4. `ConfigUpdateBody` Pydantic model (backend/api.py)
5. `ha-addon/config.yaml` options + schema
6. `ha-addon/run.sh` export line
7. `frontend/src/pages/SetupWizard.tsx` Step 5 field

### Anti-Patterns to Avoid
- **Grid discharge for export revenue:** Never command batteries to discharge to grid. Economics don't support it (0.074 feed-in vs 0.10+ import after round-trip losses). The ExportAdvisor's scope is limited to advising on what to do with *surplus* PV, not battery dispatch.
- **Per-cycle logging:** Don't log every cycle's STORE/EXPORT decision -- only transitions. At 5s intervals that would generate 17,280 entries/day.
- **Blocking the control loop:** ConsumptionForecaster.query_consumption_history() is async. If it's slow or fails, the advisor must return a safe default (STORE) immediately. Use fire-and-forget with cached last result.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Import price lookup | Custom rate calculator | `CompositeTariffEngine.get_effective_price(dt)` or `LiveOctopusTariff` | Already handles Octopus+Modul3 composite, DST, timezone conversion |
| Consumption forecast | Simple average estimator | `ConsumptionForecaster.query_consumption_history()` | ML-based with GBR models, seasonal fallback, already trained |
| Decision audit trail | Custom logging/database | Existing `deque[DecisionEntry]` ring buffer + `/api/decisions` | 100-entry buffer, newest-first API, InfluxDB write-through |
| Config persistence | Custom file I/O | `EmsSetupConfig` + `save_setup_config()` + `load_setup_config()` | Atomic writes, JSON serialization, error handling |

## Common Pitfalls

### Pitfall 1: Forecaster Unavailability on Cold Start
**What goes wrong:** ConsumptionForecaster has no trained models for the first 14 days. ExportAdvisor must not crash or produce garbage decisions.
**Why it happens:** `query_consumption_history()` returns a `ConsumptionForecast` with `fallback_used=True` and a seasonal constant (~15 kWh/day).
**How to avoid:** When `forecaster is None` or `fallback_used=True`, default to STORE (conservative). Include "forecaster unavailable, defaulting to STORE" in reasoning.
**Warning signs:** `fallback_used=True` in the ConsumptionForecast return value.

### Pitfall 2: Export-Then-Buyback Trap
**What goes wrong:** System exports at 0.074 EUR/kWh during afternoon, then must import at 0.28+ EUR/kWh during evening peak. Net loss of 0.20+ EUR/kWh.
**Why it happens:** Advisor only looks at *current* import rate (which may be low during off-peak afternoon) instead of *future* import rate when the stored energy would be needed.
**How to avoid:** The forward-looking reserve must consider the *highest* import rate in the next 4-6 hours, not just the current rate. Use `get_price_schedule()` to find peak rates ahead.
**Warning signs:** Exports happening at 14:00-16:00 followed by grid imports at 17:00-20:00.

### Pitfall 3: Winter Battery Depletion
**What goes wrong:** Batteries never reach 100% in winter (short solar days, high heating load). Advisor should never advise EXPORT when batteries aren't full in winter.
**Why it happens:** Solar production barely covers daytime consumption in Dec/Jan.
**How to avoid:** EXPORT decision should require combined SoC >= a high threshold (e.g., 90-95%). When batteries aren't full, always STORE. This naturally handles winter vs. summer without explicit season detection.
**Warning signs:** Combined SoC below 90% but advisor returning EXPORT.

### Pitfall 4: SystemConfig Lacks from_env()
**What goes wrong:** Attempting to add `FEED_IN_RATE_EUR_KWH` to a `SystemConfig.from_env()` method that doesn't exist.
**Why it happens:** Unlike other config classes, `SystemConfig` is constructed directly in `main.py` lifespan with hardcoded defaults or values from EmsSetupConfig.
**How to avoid:** Follow the actual construction path: add the field to `SystemConfig` with default, read it from `EmsSetupConfig` or env var in the lifespan code, pass it when constructing `SystemConfig`.
**Warning signs:** Looking for `SystemConfig.from_env()` in the codebase and not finding it.

### Pitfall 5: DecisionEntry Trigger Field Semantics
**What goes wrong:** Adding new trigger values that don't match the existing pattern, or adding new fields that break JSON serialization.
**Why it happens:** DecisionEntry.trigger is a plain string, not an enum. The existing values are: `role_change`, `hold_signal`, `slot_start`, `slot_end`, `failover`, `allocation_shift`.
**How to avoid:** Add `export_change` (or `self_consume_change`) as a new trigger string value. Don't modify the DecisionEntry dataclass structure -- use the existing `reasoning` field for export-specific context.
**Warning signs:** Adding export-specific fields to DecisionEntry that break existing API consumers.

## Code Examples

### ExportAdvisor Forward-Looking Reserve Algorithm
```python
# Source: Project-specific design based on existing tariff engine API
def _compute_forward_reserve_kwh(
    self, now: datetime, forecast_demand_kwh: float
) -> float:
    """Estimate kWh needed from battery in the next 4-6 hours.

    Uses the tariff schedule to find hours where import is expensive
    (> feed-in rate) and allocates forecast demand proportionally.
    """
    schedule = self._tariff.get_price_schedule(now.date())
    # Find expensive hours in the next 4-6h window
    window_end = now + timedelta(hours=6)
    expensive_hours = 0
    for slot in schedule:
        slot_start = slot.start
        slot_end = slot.end
        if slot_end <= now or slot_start >= window_end:
            continue
        if slot.effective_rate_eur_kwh > self._sys_config.feed_in_rate_eur_kwh:
            overlap_h = ... # compute overlap duration
            expensive_hours += overlap_h

    # Proportional demand: forecast_demand * (expensive_hours / 24)
    hourly_demand = forecast_demand_kwh / 24.0
    reserve_kwh = hourly_demand * expensive_hours
    return reserve_kwh
```

### Config Field Addition (Complete Pipeline)
```python
# 1. backend/config.py - SystemConfig
feed_in_rate_eur_kwh: float = 0.074
"""Fixed feed-in tariff rate in EUR/kWh (default 7.4 ct/kWh)."""

# 2. backend/setup_config.py - EmsSetupConfig
feed_in_rate_eur_kwh: float = 0.074

# 3. backend/api.py - ConfigUpdateBody
feed_in_rate_eur_kwh: float = Field(
    0.074, ge=0.0, description="Feed-in rate EUR/kWh"
)

# 4. ha-addon/config.yaml
# options section:
#   feed_in_rate_eur_kwh: 0.074
# schema section:
#   feed_in_rate_eur_kwh: "float?"

# 5. ha-addon/run.sh
# _feed_in=$(get_option 'feed_in_rate_eur_kwh')
# [ -n "$_feed_in" ] && export FEED_IN_RATE_EUR_KWH="$_feed_in"

# 6. frontend/src/pages/SetupWizard.tsx - StepTariff component
# <Field label="Feed-in Rate (EUR/kWh)" name="feed_in_rate_eur_kwh"
#        value={values.feed_in_rate_eur_kwh} onChange={onChange}
#        placeholder="0.074" />
```

### Coordinator Integration Point
```python
# In Coordinator._run_cycle(), after grid charge check, before P_target:
if self._export_advisor is not None:
    advice = self._export_advisor.advise(
        combined_soc_pct=combined_soc,
        huawei_soc_pct=h_snap.soc_pct,
        victron_soc_pct=v_snap.soc_pct,
        now=datetime.now(tz=ZoneInfo("Europe/Berlin")),
    )
    # Log decision if state changed
    if advice.decision.value != self._prev_export_decision:
        entry = DecisionEntry(...)
        self._decisions.append(entry)
        self._prev_export_decision = advice.decision.value
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| No export awareness | System holds batteries at cap, surplus naturally flows to grid | v1.0 | No visibility into export economics |
| No decision logging for export | Role changes only logged | v1.0 | Cannot audit why system exported vs. stored |

**Key insight for this phase:** The ExportAdvisor is *advisory only* -- it does not directly control battery setpoints. It informs the Coordinator whether the current state (batteries full, PV surplus available) should result in allowing natural PV export or continuing to push energy into batteries. The Coordinator already handles the mechanics of charging batteries from surplus PV.

## Open Questions

1. **How does the Coordinator currently handle "batteries full + PV surplus"?**
   - What we know: The Coordinator computes P_target from grid power, assigns charge commands when surplus exists, and both controllers have charge headroom tracking. When both batteries hit max SoC, the existing code transitions to HOLDING and PV naturally exports.
   - What's unclear: The exact code path when both batteries are at 95% SoC and PV surplus continues. The orchestrator.py line 691-698 shows Victron-specific handling for "charge full" + feed-in checks.
   - Recommendation: The ExportAdvisor's STORE/EXPORT decision should influence whether the Coordinator continues trying to push charge into batteries (lowering max SoC threshold) vs. allowing natural export. This is the key integration design decision.

2. **Should the ExportAdvisor influence the effective max SoC?**
   - What we know: When ExportAdvisor says STORE, the system should aggressively charge batteries to max. When it says EXPORT, the system can stop charging at current SoC and let surplus flow to grid.
   - What's unclear: Whether the advisor should lower the effective max SoC target or just signal the Coordinator to stop issuing charge commands.
   - Recommendation: Keep it simple -- the advisor returns STORE/EXPORT, and the Coordinator uses this to decide whether to continue issuing charge commands when batteries are nearly full (>90% SoC). No max SoC manipulation needed.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-anyio |
| Config file | `pyproject.toml` ([tool.pytest.ini_options]) |
| Quick run command | `python -m pytest tests/test_export_advisor.py -x` |
| Full suite command | `python -m pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SCO-01 | ExportAdvisor never returns advice that would cause battery-to-grid discharge | unit | `python -m pytest tests/test_export_advisor.py::test_never_discharge_battery_to_grid -x` | Wave 0 |
| SCO-01 | EXPORT only when batteries are full (combined SoC >= threshold) | unit | `python -m pytest tests/test_export_advisor.py::test_export_only_when_batteries_full -x` | Wave 0 |
| SCO-02 | feed_in_rate_eur_kwh field exists in SystemConfig with default 0.074 | unit | `python -m pytest tests/test_export_advisor.py::test_feed_in_rate_config_default -x` | Wave 0 |
| SCO-02 | feed_in_rate validation rejects negative values | unit | `python -m pytest tests/test_export_advisor.py::test_feed_in_rate_validation -x` | Wave 0 |
| SCO-04 | Decision logged on STORE->EXPORT transition | unit | `python -m pytest tests/test_export_advisor.py::test_decision_logged_on_transition -x` | Wave 0 |
| SCO-04 | No decision logged when state unchanged | unit | `python -m pytest tests/test_export_advisor.py::test_no_decision_on_same_state -x` | Wave 0 |
| SCO-04 | Reasoning includes feed-in rate, import rate, forecast demand, SoC | unit | `python -m pytest tests/test_export_advisor.py::test_reasoning_content -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_export_advisor.py -x`
- **Per wave merge:** `python -m pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_export_advisor.py` -- covers SCO-01, SCO-02, SCO-04
- [ ] No new framework install needed -- pytest already configured

## Sources

### Primary (HIGH confidence)
- `backend/coordinator.py` -- decision ring buffer pattern, DI injection via setter, `_run_cycle()` integration point
- `backend/config.py` -- SystemConfig structure, existing feed_in_allowed flags
- `backend/controller_model.py` -- DecisionEntry dataclass, trigger string values
- `backend/setup_config.py` -- EmsSetupConfig field pattern
- `backend/consumption_forecaster.py` -- ConsumptionForecast interface, fallback behavior
- `backend/tariff.py` -- CompositeTariffEngine.get_effective_price() and get_price_schedule() APIs
- `backend/api.py` -- `/api/decisions` endpoint, ConfigUpdateBody pattern
- `backend/main.py` -- lifespan wiring pattern for Coordinator
- `ha-addon/config.yaml` -- HA Add-on options and schema structure
- `ha-addon/run.sh` -- env var export pattern

### Secondary (MEDIUM confidence)
- Algorithm design for forward-looking reserve calculation -- based on project economics (feed-in 0.074 vs. import 0.10-0.40 EUR/kWh) and established tariff API

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, all existing project libraries
- Architecture: HIGH -- follows exact patterns from Scheduler/Coordinator codebase
- Pitfalls: HIGH -- identified from direct code inspection of existing control loop
- Algorithm design: MEDIUM -- forward-looking reserve heuristic needs tuning in practice

**Research date:** 2026-03-23
**Valid until:** 2026-04-23 (stable domain, fixed project patterns)
