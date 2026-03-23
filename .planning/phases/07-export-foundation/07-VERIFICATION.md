---
phase: 07-export-foundation
verified: 2026-03-23T14:30:00Z
status: passed
score: 11/11 must-haves verified
---

# Phase 7: Export Foundation Verification Report

**Phase Goal:** System can evaluate whether PV surplus should be exported or stored, based on economic analysis of fixed feed-in rate vs. future import costs
**Verified:** 2026-03-23T14:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Feed-in rate is configurable in setup config and HA Add-on options as a single EUR/kWh value | VERIFIED | `feed_in_rate_eur_kwh=0.074` in `SystemConfig`, `EmsSetupConfig`, `SetupCompleteRequest`, `SystemConfigRequest`, `ha-addon/config.yaml`, `ha-addon/run.sh`, `frontend/src/pages/SetupWizard.tsx` |
| 2 | System never commands a battery to discharge energy to the grid — export occurs only from direct PV surplus when batteries are full | VERIFIED | `ExportAdvisor.advise()` module docstring explicitly states "The advisor only handles surplus PV — it never suggests discharging batteries to grid"; STORE gate at SoC < 90% enforces this |
| 3 | ExportAdvisor produces STORE/EXPORT decisions with structured reasoning that accounts for forward-looking consumption | VERIFIED | `_compute_forward_reserve_kwh()` looks 6 hours ahead at tariff schedule; `_cached_forecast.today_expected_kwh` drives reserve estimate; test `test_store_when_future_import_expensive` confirms no-export-then-buyback |
| 4 | Export and self-consumption decisions appear in /api/decisions with human-readable reasoning | VERIFIED | `_run_export_advisory()` in coordinator appends `DecisionEntry(trigger="export_change", reasoning=advice.reasoning)` to `self._decisions` ring buffer; `/api/decisions` reads this buffer |

**Score:** 4/4 success criteria verified (from ROADMAP.md Phase 7)

### Plan 07-01 Must-Haves

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | ExportAdvisor returns STORE when batteries are not full (combined SoC < 90%) | VERIFIED | `coordinator.py` line 126: `if combined_soc_pct < _SOC_THRESHOLD_PCT:` returns STORE; tests pass |
| 2 | ExportAdvisor returns EXPORT only when combined SoC >= 90% AND feed-in rate > future import savings | VERIFIED | Forward reserve algorithm gates EXPORT on surplus_kwh > 0 after subtracting reserve from available battery kWh |
| 3 | ExportAdvisor returns STORE when forecaster is unavailable (conservative default) | VERIFIED | `if self._forecaster is None:` returns STORE with "forecaster unavailable, defaulting to STORE" reasoning |
| 4 | feed_in_rate_eur_kwh defaults to 0.074 in SystemConfig | VERIFIED | `backend/config.py` line 184: `feed_in_rate_eur_kwh: float = 0.074` |
| 5 | feed_in_rate_eur_kwh is configurable via POST /api/config, setup wizard, and HA Add-on options | VERIFIED | Present in `SystemConfigRequest` + `post_config` handler, `SetupWizard.tsx`, `ha-addon/config.yaml` + `run.sh` |
| 6 | ExportAdvice includes structured reasoning with feed-in rate, import rate, forecast demand, SoC | VERIFIED | Reasoning string includes `feed-in=`, `import=`, `forecast_demand=`, `soc=` in all non-gate paths |

### Plan 07-02 Must-Haves

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 7 | Coordinator queries ExportAdvisor every control cycle and logs STORE/EXPORT transitions | VERIFIED | `_loop()` calls `await self._run_export_advisory()` after every `_run_cycle()` call |
| 8 | ExportAdvisor is wired in main.py lifespan with tariff_engine, consumption_forecaster, and sys_cfg | VERIFIED | `main.py` lines 419-426: `ExportAdvisor(tariff_engine=tariff_engine, forecaster=consumption_forecaster, sys_config=sys_cfg)` followed by `coordinator.set_export_advisor(export_advisor)` |
| 9 | Decision log entries for export_change trigger appear in /api/decisions with structured reasoning | VERIFIED | `DecisionEntry(trigger="export_change", reasoning=advice.reasoning)` appended to `self._decisions` on transition |
| 10 | Export state transitions logged only on change (not every cycle) | VERIFIED | `if advice.decision.value != self._prev_export_decision:` guard before appending entry |
| 11 | ExportAdvisor failure does not crash the control loop | VERIFIED | `_run_export_advisory()` wraps both `refresh_forecast()` and `advise()` in try/except logging WARNING |

**Score:** 11/11 must-haves verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/export_advisor.py` | ExportAdvisor class with advise() method | VERIFIED | 279 lines; exports `ExportDecision`, `ExportAdvice`, `ExportAdvisor`; `advise()` and `async refresh_forecast()` present |
| `tests/test_export_advisor.py` | Unit tests, min 80 lines | VERIFIED | 231 lines; 9 tests, all passing |
| `backend/config.py` | feed_in_rate_eur_kwh field on SystemConfig | VERIFIED | Line 184: `feed_in_rate_eur_kwh: float = 0.074` |
| `backend/coordinator.py` | ExportAdvisor integration in _run_cycle() | VERIFIED | `_export_advisor` field, `set_export_advisor()` DI setter, `_run_export_advisory()` post-cycle hook |
| `backend/main.py` | ExportAdvisor wiring in lifespan | VERIFIED | Lines 419-426: ExportAdvisor constructed and injected |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `backend/export_advisor.py` | `backend/tariff.py` | `get_effective_price()` and `get_price_schedule()` | VERIFIED | Both calls present in `advise()` and `_compute_forward_reserve_kwh()` |
| `backend/export_advisor.py` | `backend/consumption_forecaster.py` | `query_consumption_history()` | VERIFIED | Called in `refresh_forecast()` line 269 |
| `backend/export_advisor.py` | `backend/config.py` | `SystemConfig.feed_in_rate_eur_kwh` | VERIFIED | `self._sys_config.feed_in_rate_eur_kwh` in `advise()` line 122 |
| `backend/coordinator.py` | `backend/export_advisor.py` | `set_export_advisor()` and `advise()` | VERIFIED | DI setter at line 185; `_export_advisor.advise()` at line 536 |
| `backend/main.py` | `backend/export_advisor.py` | `ExportAdvisor(...)` construction | VERIFIED | Lines 419-426 |
| `backend/coordinator.py` | `backend/controller_model.py` | `DecisionEntry` with `trigger='export_change'` | VERIFIED | Line 546 in `_run_export_advisory()` |

### Data-Flow Trace (Level 4)

`ExportAdvisor` renders no UI directly — it produces `ExportAdvice` (a dataclass) consumed by the coordinator decision ring buffer. The data pipeline is:

| Component | Data Variable | Source | Produces Real Data | Status |
|-----------|---------------|--------|--------------------|--------|
| `ExportAdvisor.advise()` | `feed_in_rate` | `SystemConfig.feed_in_rate_eur_kwh` | Yes — real config field, not hardcoded in advisor | FLOWING |
| `ExportAdvisor.advise()` | `import_rate` | `tariff_engine.get_effective_price(now)` | Yes — live tariff lookup | FLOWING |
| `ExportAdvisor._compute_forward_reserve_kwh()` | `schedule` | `tariff_engine.get_price_schedule(now.date())` | Yes — real tariff schedule | FLOWING |
| `ExportAdvisor.refresh_forecast()` | `_cached_forecast` | `forecaster.query_consumption_history()` | Yes — async DB query; None forecaster handled gracefully | FLOWING |
| `Coordinator._run_export_advisory()` | `state.combined_soc_pct` | `_build_state()` from live controller snapshots | Yes — live hardware data | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 9 ExportAdvisor tests pass | `uv run python -m pytest tests/test_export_advisor.py -x -v` | 9 passed, 0 failed | PASS |
| Full test suite — no regressions | `uv run python -m pytest tests/ -x -q` | 1220 passed, 11 skipped | PASS |
| ExportAdvisor module imports cleanly | `uv run python -c "from backend.export_advisor import ExportAdvisor, ExportDecision, ExportAdvice; print('OK')"` | OK | PASS |
| SystemConfig default 0.074 | `uv run python -c "from backend.config import SystemConfig; assert SystemConfig().feed_in_rate_eur_kwh == 0.074"` | Passes | PASS |
| Coordinator imports cleanly | `uv run python -c "from backend.coordinator import Coordinator"` | Passes | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SCO-01 | 07-01-PLAN, 07-02-PLAN | System never actively discharges battery to grid — export only from direct PV surplus when batteries are full | SATISFIED | Module docstring explicitly states advisory-only surplus PV scope; no EXPORT path allows active battery discharge; SoC < 90% gate enforces batteries-not-full constraint |
| SCO-02 | 07-01-PLAN | Feed-in rate configurable as a single EUR/kWh value (default 0.074) in setup config and HA Add-on options | SATISFIED | `feed_in_rate_eur_kwh=0.074` in `SystemConfig`, `EmsSetupConfig`, `SetupCompleteRequest`, `SystemConfigRequest`, `config.yaml`, `run.sh`, `SetupWizard.tsx` — 10 config touchpoints |
| SCO-04 | 07-01-PLAN, 07-02-PLAN | Self-consumption and export decisions logged with structured reasoning in /api/decisions | SATISFIED | `DecisionEntry(trigger="export_change", reasoning=advice.reasoning)` persisted to `self._decisions` ring buffer, served at `/api/decisions` |

No orphaned requirements: all three IDs (SCO-01, SCO-02, SCO-04) claimed in plan frontmatter are mapped to Phase 7 in REQUIREMENTS.md and all three are satisfied.

### Anti-Patterns Found

Scanned `backend/export_advisor.py`, `backend/coordinator.py` (export advisory sections), `tests/test_export_advisor.py`, `backend/config.py` (feed_in field), `backend/main.py` (wiring section).

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| No issues found | — | — | — | — |

Key findings:
- No TODO/FIXME/placeholder comments in any phase-7-modified files
- No `return null` / `return {}` / `return []` stubs
- `_cached_forecast = None` initial state in `ExportAdvisor.__init__()` is correct — it is the documented "no forecast yet" sentinel, handled by Gate 3 in `advise()` which returns STORE conservatively. This is not a stub; it is intentional defensive design.
- `p_target_w=0.0` and `huawei_allocation_w=0.0` in the `export_change` DecisionEntry are documented as advisory-only placeholders for this phase (Phase 8 will wire export into P_target). This is a known, documented design decision from 07-02-SUMMARY.md.

### Human Verification Required

None — all success criteria are verifiable programmatically. The ExportAdvisor is advisory-only in this phase; actual export actuation (Phase 8) is explicitly deferred per the roadmap.

---

## Gaps Summary

No gaps. All 4 ROADMAP success criteria, all 11 plan must-have truths, all 5 required artifacts, all 6 key links, and all 3 requirement IDs are fully verified. The full test suite passes with 1220 tests and zero regressions.

---

_Verified: 2026-03-23T14:30:00Z_
_Verifier: Claude (gsd-verifier)_
