---
phase: 1
slug: victron-modbus-driver
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-22
---

# Phase 1 -- Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x with pytest-anyio (async) + pytest-mock |
| **Config file** | `pyproject.toml` (asyncio_mode = "auto") |
| **Quick run command** | `python -m pytest tests/drivers/ -x -q` |
| **Full suite command** | `python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/drivers/ -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | Status |
|---------|------|------|-------------|-----------|-------------------|--------|
| 01-01-01 | 01 | 1 | DRV-06, DRV-04 | unit | `python -m pytest tests/drivers/test_victron_config.py -x -q` | TDD (self-creates tests) |
| 01-01-02 | 01 | 1 | DRV-01, DRV-02, DRV-03 | unit | `python -m pytest tests/drivers/test_victron_driver.py -x -q` | TDD (self-creates tests) |
| 01-02-01 | 02 | 2 | DRV-05 | unit | `python -m pytest tests/drivers/test_protocol.py -x -q` | TDD (self-creates tests) |
| 01-02-02 | 02 | 2 | DRV-01 | integration | `python -m pytest tests/drivers/ -x -q` | pending |

*Status: pending -- TDD (self-creates tests) -- green -- red -- flaky*

---

## Wave 0 Requirements

Wave 0 is satisfied by TDD tasks. Each TDD task creates its own test file in the RED phase before writing implementation. No separate Wave 0 stub task is needed:

- `tests/drivers/test_victron_config.py` -- created by Plan 01-01 Task 1 (TDD RED phase)
- `tests/drivers/test_victron_driver.py` -- rewritten by Plan 01-01 Task 2 (TDD RED phase)
- `tests/drivers/test_protocol.py` -- created by Plan 01-02 Task 1 (TDD RED phase)

*Existing `tests/drivers/test_huawei_driver.py` covers DRV-05 baseline.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Modbus TCP read from real Venus OS GX | DRV-02 | Requires live hardware | Connect to GX device, run `python -m pytest tests/drivers/test_victron_driver.py -k live --live-host=<IP>` |
| ESS setpoint write response within 2s | DRV-03 | Requires live hardware + timing | Write setpoint via driver, measure inverter response time |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or TDD self-creation
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covered by TDD task self-creation (no separate stubs needed)
- [x] No watch-mode flags
- [x] Feedback latency < 5s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** ready
