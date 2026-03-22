---
phase: 1
slug: victron-modbus-driver
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 1 — Validation Strategy

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

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | DRV-06 | unit | `python -m pytest tests/drivers/test_protocol.py -x -q` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01 | 1 | DRV-02 | unit | `python -m pytest tests/drivers/test_victron_modbus.py -x -q` | ❌ W0 | ⬜ pending |
| 01-01-03 | 01 | 1 | DRV-03 | unit | `python -m pytest tests/drivers/test_victron_modbus.py -x -q` | ❌ W0 | ⬜ pending |
| 01-01-04 | 01 | 1 | DRV-04 | unit | `python -m pytest tests/drivers/test_victron_config.py -x -q` | ❌ W0 | ⬜ pending |
| 01-02-01 | 02 | 2 | DRV-05 | unit | `python -m pytest tests/drivers/test_huawei_driver.py -x -q` | ✅ | ⬜ pending |
| 01-02-02 | 02 | 2 | DRV-01 | integration | `python -m pytest tests/drivers/ -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/drivers/test_victron_modbus.py` — stubs for DRV-01, DRV-02, DRV-03
- [ ] `tests/drivers/test_protocol.py` — stubs for DRV-06 protocol conformance
- [ ] `tests/drivers/test_victron_config.py` — stubs for DRV-04

*Existing `tests/drivers/test_huawei_driver.py` covers DRV-05 baseline.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Modbus TCP read from real Venus OS GX | DRV-02 | Requires live hardware | Connect to GX device, run `python -m pytest tests/drivers/test_victron_modbus.py -k live --live-host=<IP>` |
| ESS setpoint write response within 2s | DRV-03 | Requires live hardware + timing | Write setpoint via driver, measure inverter response time |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
