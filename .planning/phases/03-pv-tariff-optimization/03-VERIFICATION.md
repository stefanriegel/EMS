---
phase: 03-pv-tariff-optimization
verified: 2026-03-22T10:15:00Z
status: passed
score: 4/4 must-haves verified
---

# Phase 3: PV & Tariff Optimization Verification Report

**Phase Goal:** The system makes intelligent charge/discharge decisions based on PV surplus, tariff windows, solar forecasts, and time-of-day profiles
**Verified:** 2026-03-22T10:15:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (from ROADMAP.md Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | PV surplus is distributed across both batteries weighted by SoC headroom and charge rate limits (not split 50/50) | VERIFIED | `_allocate_charge` uses `h_headroom_soc = max(0.0, self._full_soc_pct - h_snap.soc_pct)` proportional split with overflow routing; confirmed by `TestPvSurplusHeadroomWeighting` (5 tests) |
| 2 | During cheap tariff windows, each battery charges independently at its own rate with the faster charger starting first | VERIFIED | `_compute_grid_charge_commands` stagers by `slot.battery` (Huawei at 5000W first, Victron at 3000W after); scheduler sets Huawei slot first; `TestGridChargeStaggering` verifies the stagger and redirect logic |
| 3 | Grid charge is skipped when solar forecast covers expected demand (predictive pre-charging) | VERIFIED | Scheduler formula fallback has three-branch logic: full skip when `solar_kwh >= consumption * 1.2` (D-10), partial reduction with `0.8` discount (D-11), full charge safety fallback (D-12); `TestPredictivePreCharging` (7 tests) |
| 4 | Min-SoC floors change by time-of-day per configurable profiles | VERIFIED | `_get_effective_min_soc` evaluates `MinSocWindow` list with wrapping support; `_run_cycle` uses profile-aware check replacing static values; `TestMinSocProfiles` (6 tests) |

**Score:** 4/4 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/config.py` | `MinSocWindow` dataclass and `SystemConfig` profile fields | VERIFIED | Lines 122-134: `class MinSocWindow` with `start_hour`, `end_hour`, `min_soc_pct`. Lines 172-176: `huawei_min_soc_profile` and `victron_min_soc_profile` optional fields |
| `backend/coordinator.py` | Headroom-weighted `_allocate_charge` and `_get_effective_min_soc` | VERIFIED | Lines 572-612: `_allocate_charge` with `h_headroom_soc` proportional split and overflow routing. Lines 618-646: `_get_effective_min_soc` with wrapping window support. Lines 376-382: `_run_cycle` uses profile-aware check |
| `backend/controller_model.py` | `CoordinatorState` with effective min-SoC fields | VERIFIED | Lines 178-182: `huawei_effective_min_soc_pct: float = 10.0` and `victron_effective_min_soc_pct: float = 15.0`; `_build_state` populates both via `_get_effective_min_soc` |
| `backend/scheduler.py` | Solar-aware target reduction in formula fallback | VERIFIED | Lines 203-226: three-branch logic (`solar >= 1.2x` skip, `solar > 0` partial with `0.8` discount, else full charge); charge_energy_kwh uses solar-aware `net_charge_kwh` in formula fallback path |
| `tests/test_coordinator.py` | `TestPvSurplusHeadroomWeighting`, `TestMinSocProfiles`, `TestGridChargeStaggering` | VERIFIED | All three classes present with 5, 6, and 3 test methods respectively (14 total) |
| `tests/test_scheduler.py` | `TestPredictivePreCharging` with 6+ test methods | VERIFIED | Class present at line 577 with 7 test methods covering D-10, D-11, D-12, D-18 |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `backend/coordinator.py` | `backend/config.py` | `MinSocWindow` import and `SystemConfig` profile fields | WIRED | Line 24: `from backend.config import MinSocWindow, OrchestratorConfig, SystemConfig`; used in `_get_effective_min_soc` at lines 627-630 |
| `backend/coordinator.py` | `backend/controller_model.py` | `ControllerSnapshot.charge_headroom_w` for rate limiting | WIRED | Lines 599-600: `h_max = h_snap.charge_headroom_w`, `v_max = v_snap.charge_headroom_w`; used to clamp charge allocation |
| `backend/coordinator.py` | `_run_cycle` min-SoC check | `_get_effective_min_soc("huawei", ...)` call | WIRED | Lines 377-382: `_tz`, `now_local`, `h_min_soc`, `v_min_soc` from profile-aware method; replaces old static references |
| `backend/coordinator.py` | `_build_state` | effective min-SoC fields in `CoordinatorState` | WIRED | Lines 860-864: `_get_effective_min_soc` called for both systems, results assigned to `CoordinatorState` fields |
| `backend/scheduler.py` | `backend/schedule_models.py` | `evcc_state.solar` and `ConsumptionForecast.today_expected_kwh` | WIRED | Lines 203-226: `evcc_state.solar is not None` guard, `solar_kwh >= consumption.today_expected_kwh * 1.2` comparison |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| OPT-01 | 03-01-PLAN.md | PV surplus distributed by SoC headroom and charge rate limits | SATISFIED | `_allocate_charge` uses proportional headroom split with overflow routing; 5 test cases in `TestPvSurplusHeadroomWeighting` |
| OPT-02 | 03-01-PLAN.md | Tariff-aware grid charging targets each battery independently | SATISFIED | `_compute_grid_charge_commands` and scheduler produce separate slots per battery (5000W Huawei, 3000W Victron) |
| OPT-03 | 03-01-PLAN.md | Charge rate optimization: stagger charging (faster charger first) | SATISFIED | Scheduler places Huawei slot first (D010 — LUNA-first); `_compute_grid_charge_commands` redirects power to Victron when Huawei target met |
| OPT-04 | 03-02-PLAN.md | Predictive pre-charging: skip grid charge when solar covers demand | SATISFIED | Three-branch formula in `scheduler.py` lines 202-226; 7 tests in `TestPredictivePreCharging` |
| OPT-05 | 03-01-PLAN.md | Configurable min-SoC per time-of-day profiles | SATISFIED | `MinSocWindow` dataclass in `config.py`; `_get_effective_min_soc` with wrapping support in `coordinator.py`; 6 tests in `TestMinSocProfiles` |

No orphaned requirements — all 5 OPT requirements declared in plans map 1:1 to REQUIREMENTS.md Phase 3 entries, all marked "Complete".

### Anti-Patterns Found

No blockers found. Scanned `backend/config.py`, `backend/coordinator.py`, `backend/controller_model.py`, `backend/scheduler.py`, `tests/test_coordinator.py`, `tests/test_scheduler.py`.

Notable observations (informational only):

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `tests/test_coordinator.py` line 894 | `test_both_charge_simultaneously` asserts only Huawei command, not Victron — the test name is misleading but the implementation is correct (sequential stagger, not parallel) | Info | No functional impact; test passes and intent matches design (D010: LUNA first) |
| `tests/test_coordinator.py` class `TestPvSurplusRouting` | Class docstring says "superseded by TestPvSurplusHeadroomWeighting" but tests still exist and test headroom-weighted behavior under new class name | Info | No impact; both test classes verify the same (new) behavior |

### Human Verification Required

None. All phase-3 behaviors are verifiable programmatically via the test suite. The one manual check noted in VALIDATION.md (real hardware charge rate limits) is explicitly out of scope for automated verification and is an integration concern for Phase 4+.

### Test Suite Results

```
193 passed, 1 skipped, 1 warning in 0.36s
```

All coordinator and scheduler tests pass. Full suite executed with `uv run python -m pytest tests/test_coordinator.py tests/test_scheduler.py -x -q`.

### Gaps Summary

No gaps. All four observable truths from the ROADMAP.md success criteria are verified:

1. Headroom-weighted PV surplus allocation is implemented and tested end-to-end
2. Tariff-window staggering places Huawei (5 kW) before Victron (3 kW) with redirect on target-met
3. Predictive pre-charging skips/reduces grid charge based on solar forecast coverage
4. Time-of-day min-SoC profiles with wrapping window support are wired into the control loop

All five OPT requirements are satisfied with substantive implementations and comprehensive test coverage (14 coordinator tests, 7 scheduler predictive pre-charging tests).

---

_Verified: 2026-03-22T10:15:00Z_
_Verifier: Claude (gsd-verifier)_
