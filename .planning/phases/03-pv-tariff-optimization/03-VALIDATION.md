---
phase: 03
slug: pv-tariff-optimization
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 03 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x with pytest-anyio (async) |
| **Config file** | `pyproject.toml` ([tool.pytest.ini_options]) |
| **Quick run command** | `python -m pytest tests/ -x -q --timeout=10` |
| **Full suite command** | `python -m pytest tests/ -v --timeout=30` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ -x -q --timeout=10`
- **After every plan wave:** Run `python -m pytest tests/ -v --timeout=30`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | OPT-01 | unit | `python -m pytest tests/test_coordinator.py -k surplus -v` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | OPT-05 | unit | `python -m pytest tests/test_coordinator.py -k min_soc -v` | ❌ W0 | ⬜ pending |
| 03-02-01 | 02 | 2 | OPT-02, OPT-03 | unit | `python -m pytest tests/test_scheduler.py -k tariff -v` | ❌ W0 | ⬜ pending |
| 03-02-02 | 02 | 2 | OPT-04 | unit | `python -m pytest tests/test_scheduler.py -k predictive -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_coordinator.py` — extend with PV surplus and min-SoC profile test stubs
- [ ] `tests/test_scheduler.py` — extend with predictive pre-charging and tariff optimization stubs

*Existing test infrastructure (pytest, conftest) already in place.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real hardware charge rate limits | OPT-01 | Hardware-dependent max_charge_power_w varies by SoC/temperature | Verify during integration test with real Huawei/Victron hardware |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
