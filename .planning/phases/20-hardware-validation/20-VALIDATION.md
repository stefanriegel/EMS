---
phase: 20
slug: hardware-validation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-24
---

# Phase 20 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x with anyio |
| **Config file** | pyproject.toml (asyncio_mode = "auto") |
| **Quick run command** | `python -m pytest tests/test_huawei_driver.py tests/test_victron_driver.py -q` |
| **Full suite command** | `python -m pytest tests/ -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_huawei_driver.py tests/test_victron_driver.py tests/test_hardware_validation.py -q`
- **After every plan wave:** Run `python -m pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 20-01-01 | 01 | 1 | HWVAL-03 | unit | `python -m pytest tests/test_huawei_driver.py -q -k dry_run` | ❌ W0 | ⬜ pending |
| 20-01-02 | 01 | 1 | HWVAL-03 | unit | `python -m pytest tests/test_victron_driver.py -q -k dry_run` | ❌ W0 | ⬜ pending |
| 20-02-01 | 02 | 1 | HWVAL-01 | unit | `python -m pytest tests/test_hardware_validation.py -q -k connectivity` | ❌ W0 | ⬜ pending |
| 20-02-02 | 02 | 1 | HWVAL-02 | unit | `python -m pytest tests/test_hardware_validation.py -q -k write_back` | ❌ W0 | ⬜ pending |
| 20-02-03 | 02 | 1 | HWVAL-04 | unit | `python -m pytest tests/test_hardware_validation.py -q -k validation_period` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_hardware_validation.py` — stubs for HWVAL-01, HWVAL-02, HWVAL-04
- [ ] dry_run tests added to existing `tests/test_huawei_driver.py` and `tests/test_victron_driver.py`

*Existing test infrastructure covers framework and fixtures.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real Modbus TCP connectivity | HWVAL-01 | Requires physical hardware | Run `scripts/probe_huawei.py` and `scripts/probe_victron.py` on HA host |
| Write-back on real registers | HWVAL-02 | Requires physical hardware | Enable dry_run=False on test instance, verify logs show read-back match |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
