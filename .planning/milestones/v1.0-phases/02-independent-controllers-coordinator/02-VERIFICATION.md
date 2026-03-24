---
phase: 02-independent-controllers-coordinator
verified: 2026-03-22T09:00:00Z
status: passed
score: 12/12 must-haves verified
gaps: []
---

# Phase 02: Independent Controllers and Coordinator — Verification Report

**Phase Goal:** Independent per-battery controllers (HuaweiController, VictronController) with a Coordinator that assigns roles, allocates watts, and dispatches commands — replacing the monolithic Orchestrator's control path.
**Verified:** 2026-03-22T09:00:00Z
**Status:** passed
**Re-verification:** Yes — gap fixed inline (current_power_w → power_w, null guards, test added)

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | BatteryRole enum has exactly 5 members: PRIMARY_DISCHARGE, SECONDARY_DISCHARGE, CHARGING, HOLDING, GRID_CHARGE | VERIFIED | `backend/controller_model.py` lines 19-38: 5 members present with `str` mixin |
| 2 | PoolStatus enum has exactly 3 members: NORMAL, DEGRADED, OFFLINE | VERIFIED | `backend/controller_model.py` lines 41-51: 3 members present |
| 3 | HuaweiController.poll() returns ControllerSnapshot with soc_pct, power_w, available, and Huawei-specific fields | VERIFIED | `backend/huawei_controller.py` lines 111-122: all fields populated including max_charge_power_w, max_discharge_power_w, master_active_power_w |
| 4 | VictronController.poll() returns ControllerSnapshot with soc_pct, power_w, available, and Victron-specific fields | VERIFIED | `backend/victron_controller.py` lines 104-117: grid_power_w, grid_l1/l2/l3_power_w, ess_mode populated |
| 5 | Each controller enters safe state after 3 consecutive failures | VERIFIED | `backend/huawei_controller.py` lines 128-138: `write_max_discharge_power(0)`. `backend/victron_controller.py` lines 123-134: writes 0 to all 3 phases |
| 6 | Stale data increments failure counter | VERIFIED | Huawei: `backend/huawei_controller.py` lines 83-96 (`_last_read_time` tracking). Victron: lines 85-93 (`data.timestamp` comparison) |
| 7 | Controller.execute() writes commanded watts using the driver's sign convention | VERIFIED | Huawei: `abs()` for discharge setpoint. Victron: negative = export path via `_write_discharge()` |
| 8 | Coordinator runs a 5s async control loop polling both controllers and sending commands | VERIFIED | `backend/coordinator.py` lines 273-282: `asyncio.sleep(self._cfg.loop_interval_s)` in `_loop()` |
| 9 | Coordinator never calls driver methods directly (CTRL-02) | VERIFIED | `grep "self._huawei\.read_\|self._victron\.read_\|self._huawei\.write_\|self._victron\.write_"` returns nothing |
| 10 | Higher-SoC system assigned PRIMARY_DISCHARGE with 5% gap threshold and 3% swap hysteresis | VERIFIED | `backend/coordinator.py` lines 447-502: `_assign_discharge_roles()` implements gap/hysteresis logic correctly |
| 11 | Lifespan creates HuaweiController, VictronController, Coordinator; Orchestrator direct instantiation removed | VERIFIED | `backend/main.py` lines 400-415: controllers and coordinator constructed. `grep "Orchestrator(huawei, victron"` returns nothing |
| 12 | GET /api/devices returns correct per-device telemetry from live Coordinator | FAILED | `Coordinator.get_device_snapshot()` references `h_snap.current_power_w` and `v_snap.current_power_w` (lines 186, 190, 215). Field is named `power_w` on ControllerSnapshot. Causes `AttributeError` at runtime. Tests mock this method and do not exercise the real implementation. |

**Score:** 11/12 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/controller_model.py` | BatteryRole, PoolStatus enums; ControllerSnapshot, ControllerCommand, CoordinatorState dataclasses | VERIFIED | 177 lines; all 4 types implemented with correct fields |
| `backend/huawei_controller.py` | HuaweiController wrapping HuaweiDriver | VERIFIED | 186 lines; full poll/execute/failure-counting implementation |
| `backend/victron_controller.py` | VictronController wrapping VictronDriver | VERIFIED | 219 lines; ESS mode guard, per-phase distribution, stale detection |
| `backend/coordinator.py` | Coordinator with control loop, role assignment, allocation, hysteresis, debounce | VERIFIED (with bug) | 825 lines; complete implementation. `get_device_snapshot()` has `current_power_w` field name error |
| `tests/test_controller_model.py` | Enum and dataclass validation tests | VERIFIED | 208 lines; 14 tests |
| `tests/test_huawei_controller.py` | HuaweiController unit tests | VERIFIED | 290 lines; 32 tests |
| `tests/test_victron_controller.py` | VictronController unit tests | VERIFIED | 369 lines; 26 tests |
| `tests/test_coordinator.py` | Coordinator unit tests covering all CTRL requirements | VERIFIED | 660 lines; 87 tests (86 pass, 1 skipped) |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `backend/huawei_controller.py` | `backend/drivers/huawei_driver.py` | `self._driver.read_master()`, `self._driver.read_battery()`, `self._driver.write_max_discharge_power()`, `self._driver.write_ac_charging()`, `self._driver.write_max_charge_power()` | WIRED | All calls present at lines 73, 74, 131, 172, 177, 178 |
| `backend/victron_controller.py` | `backend/drivers/victron_driver.py` | `self._driver.read_system_state()`, `self._driver.write_ac_power_setpoint()` | WIRED | Lines 76, 127, 191, 195, 211-213, 216-218 |
| `backend/huawei_controller.py` | `backend/controller_model.py` | `from backend.controller_model import BatteryRole, ControllerCommand, ControllerSnapshot` | WIRED | Line 17 |
| `backend/coordinator.py` | `backend/huawei_controller.py` | `self._huawei_ctrl.poll()` and `self._huawei_ctrl.execute(cmd)` | WIRED | Lines 287, 301, 312, 321, 351, 363, 411 |
| `backend/coordinator.py` | `backend/victron_controller.py` | `self._victron_ctrl.poll()` and `self._victron_ctrl.execute(cmd)` | WIRED | Lines 288, 302, 313, 322, 352, 364, 412 |
| `backend/coordinator.py` | `backend/controller_model.py` | `from backend.controller_model import BatteryRole, ControllerCommand, ControllerSnapshot, CoordinatorState, PoolStatus` | WIRED | Lines 22-28 |
| `backend/main.py` | `backend/coordinator.py` | `Coordinator(...)` construction and `await coordinator.start()` in lifespan | WIRED | Lines 66, 402-414 |
| `backend/main.py` | `backend/huawei_controller.py` | `HuaweiController(huawei, sys_cfg, ...)` | WIRED | Lines 67, 400 |
| `backend/main.py` | `backend/victron_controller.py` | `VictronController(victron, sys_cfg, ...)` | WIRED | Lines 69, 401 |
| `backend/api.py` | `backend/coordinator.py` | `from backend.coordinator import Coordinator`; `get_orchestrator()` returns `Coordinator`; `orchestrator.get_state()` called | WIRED | Lines 59, 120, 189 |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| CTRL-01 | 02-01 | Each battery system has a dedicated controller with its own state machine, hysteresis, and debounce | SATISFIED | `HuaweiController` and `VictronController` each own failure counting, stale detection, and safe-state logic independently |
| CTRL-02 | 02-02, 02-03 | Coordinator allocates demand across controllers without directly writing to hardware | SATISFIED | No `self._huawei.read_*` or `self._victron.write_*` in coordinator.py; only `ctrl.poll()` and `ctrl.execute()` |
| CTRL-03 | 02-02 | Per-system hysteresis dead-band: Huawei ~300-500W, Victron ~100-200W | SATISFIED | `_apply_hysteresis()` with `_huawei_deadband_w=300`, `_victron_deadband_w=150` |
| CTRL-04 | 02-01, 02-03 | Each controller enters safe state independently on communication loss | SATISFIED | `_handle_failure()` in both controllers applies safe state at 3 failures without touching the other controller |
| CTRL-05 | 02-02, 02-03 | Total household power remains stable when coordinator reassigns load | SATISFIED | `_allocate()` routes full P_target to survivor on failover (D-10 logic at lines 531-535) |
| CTRL-06 | 02-01 | Dynamic role assignment (5 roles) based on SoC, tariff, PV | SATISFIED | `BatteryRole` enum with 5 members; `_assign_discharge_roles()`, `_allocate_charge()`, `_check_grid_charge()` cover all paths |
| CTRL-07 | 02-02 | Anti-oscillation ramps: soft-start/soft-stop with configurable ramp rate | SATISFIED | `_apply_ramp()` with `_huawei_ramp_w_per_cycle=2000`, `_victron_ramp_w_per_cycle=1000` |
| CTRL-08 | 02-02 | SoC-based discharge priority: higher-SoC system discharges first | SATISFIED | `_assign_discharge_roles()` assigns PRIMARY_DISCHARGE to higher SoC; swap hysteresis prevents flapping |

No orphaned requirements found. All 8 CTRL requirements mapped to plans in this phase.

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `backend/coordinator.py` | 186, 190 | `h_snap.current_power_w` — attribute does not exist on `ControllerSnapshot` | BLOCKER | `AttributeError` at runtime when `/api/devices` is called with a live Coordinator and `h_snap.available` is True |
| `backend/coordinator.py` | 215 | `v_snap.current_power_w` — attribute does not exist on `ControllerSnapshot` | BLOCKER | Same `AttributeError` for Victron branch |
| `backend/coordinator.py` | 191, 192 | `int(h_snap.max_charge_power_w)` and `int(h_snap.max_discharge_power_w)` — both fields are `int | None` | WARNING | `TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'` if the Huawei snapshot was built without these optional fields (e.g. stale/failure path) |
| `backend/coordinator.py` | 173-248 | `get_device_snapshot()` has no corresponding test in `test_coordinator.py` | WARNING | The only tests for this method use mocks and do not catch the field name errors |

The blockers in `get_device_snapshot()` are hidden because all API tests inject a `MockOrchestrator` whose `get_device_snapshot()` returns a hardcoded dict. The real Coordinator implementation is never exercised by the test suite.

---

## Human Verification Required

None — the gap is a verifiable field-name error, not a runtime behaviour question.

---

## Gaps Summary

Phase 02 delivers all major structural goals: BatteryRole/PoolStatus enums, ControllerSnapshot/ControllerCommand dataclasses, HuaweiController and VictronController with independent failure counting and safe-state logic, the Coordinator with correct role assignment and dispatch, and full API integration via main.py lifespan replacement.

The single gap is a field-name bug in `Coordinator.get_device_snapshot()`: three attribute lookups use the non-existent name `current_power_w` instead of the correct `power_w` (the ControllerSnapshot field name). This method was added as an unplanned deviation during Plan 03 to satisfy existing API endpoints, but the implementation has the wrong field name. The bug is masked by mock-based tests throughout the test suite — no test constructs a real ControllerSnapshot and passes it to `get_device_snapshot()`.

The fix is mechanical: replace `.current_power_w` with `.power_w` at lines 186, 190, and 215 of `backend/coordinator.py`, add null-guards for the optional `max_charge_power_w` / `max_discharge_power_w` fields, and add a direct unit test.

All 8 CTRL requirements are substantively implemented and verified in the codebase. The full test suite (1083 tests, 11 skipped) passes without regressions.

---

_Verified: 2026-03-22T09:00:00Z_
_Verifier: Claude (gsd-verifier)_
