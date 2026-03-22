---
phase: 2
slug: independent-controllers-coordinator
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-22
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8+ with pytest-anyio (asyncio_mode = "auto") + pytest-mock |
| **Config file** | `pyproject.toml` (asyncio_mode = "auto") |
| **Quick run command** | `python -m pytest tests/test_coordinator.py tests/test_huawei_controller.py tests/test_victron_controller.py tests/test_controller_model.py -x -q` |
| **Full suite command** | `python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~8 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_coordinator.py tests/test_huawei_controller.py tests/test_victron_controller.py tests/test_controller_model.py -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 8 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | Status |
|---------|------|------|-------------|-----------|-------------------|--------|
| 02-01-01 | 01 | 1 | CTRL-01, CTRL-06 | unit | `python -m pytest tests/test_controller_model.py tests/test_huawei_controller.py tests/test_victron_controller.py -x -q` | TDD (self-creates tests) |
| 02-01-02 | 01 | 1 | CTRL-01, CTRL-04 | unit | `python -m pytest tests/test_huawei_controller.py tests/test_victron_controller.py -x -q` | TDD (self-creates tests) |
| 02-02-01 | 02 | 2 | CTRL-02, CTRL-05, CTRL-06, CTRL-08 | unit | `python -m pytest tests/test_coordinator.py -x -q` | TDD (self-creates tests) |
| 02-02-02 | 02 | 2 | CTRL-03, CTRL-07 | unit | `python -m pytest tests/test_coordinator.py -x -q -k "hysteresis or ramp"` | TDD (self-creates tests) |
| 02-03-01 | 03 | 3 | CTRL-02, CTRL-04 | integration | `python -m pytest tests/ -x -q` | TDD (self-creates tests) |

*Status: pending · TDD (self-creates tests) · green · red · flaky*

---

## Wave 0 Requirements

Wave 0 is satisfied by TDD tasks. Each TDD task creates its own test file in the RED phase before writing implementation:

- `tests/test_controller_model.py` — created by Plan 02-01 (TDD RED phase)
- `tests/test_huawei_controller.py` — created by Plan 02-01 (TDD RED phase)
- `tests/test_victron_controller.py` — created by Plan 02-01 (TDD RED phase)
- `tests/test_coordinator.py` — created by Plan 02-02 (TDD RED phase)

*Existing `tests/test_orchestrator.py` must still pass until orchestrator is fully replaced.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Total household power stable on role reassignment | CTRL-05 | Requires real hardware with load | Monitor grid meter during coordinator role swap, verify no spikes >100W |
| Safe state within 15s on communication loss | CTRL-04 | Requires real hardware disconnect | Disconnect Victron Modbus cable, verify zero-power within 15s |
| Smooth ramp on setpoint change | CTRL-07 | Requires real hardware timing | Apply large setpoint change, observe ramp on inverter display |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or TDD self-creation
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covered by TDD task self-creation (no separate stubs needed)
- [x] No watch-mode flags
- [x] Feedback latency < 8s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** ready
