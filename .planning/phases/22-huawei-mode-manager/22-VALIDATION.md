---
phase: 22
slug: huawei-mode-manager
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-24
---

# Phase 22 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x with anyio |
| **Config file** | pyproject.toml (asyncio_mode = "auto") |
| **Quick run command** | `python -m pytest tests/test_huawei_mode_manager.py -q` |
| **Full suite command** | `python -m pytest tests/ -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_huawei_mode_manager.py -q`
- **After every plan wave:** Run `python -m pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 22-01-01 | 01 | 1 | HCTL-01,02,03,04 | unit | `python -m pytest tests/test_huawei_mode_manager.py -q` | ❌ W0 | ⬜ pending |
| 22-02-01 | 02 | 2 | HCTL-01,02 | unit | `python -m pytest tests/test_huawei_mode_manager.py tests/test_huawei_controller.py -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_huawei_mode_manager.py` — stubs for HCTL-01 through HCTL-04

*Existing test infrastructure covers framework and fixtures.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real Huawei TOU mode switch | HCTL-01 | Requires physical hardware | Run EMS on HA, check Huawei working mode via FusionSolar app |
| Crash recovery restoration | HCTL-02 | Requires process kill + restart | Kill EMS process, verify Huawei returns to self-consumption mode on restart |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
