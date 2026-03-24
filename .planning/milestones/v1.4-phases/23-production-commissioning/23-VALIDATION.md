---
phase: 23
slug: production-commissioning
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-24
---

# Phase 23 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x with anyio |
| **Config file** | pyproject.toml (asyncio_mode = "auto") |
| **Quick run command** | `python -m pytest tests/test_commissioning.py -q` |
| **Full suite command** | `python -m pytest tests/ -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_commissioning.py -q`
- **After every plan wave:** Run `python -m pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 23-01-01 | 01 | 1 | PROD-01,02 | unit | `python -m pytest tests/test_commissioning.py -q` | ❌ W0 | ⬜ pending |
| 23-02-01 | 02 | 2 | PROD-03 | unit | `python -m pytest tests/test_victron_controller.py -q -k watchdog` | ❌ W0 | ⬜ pending |
| 23-02-02 | 02 | 2 | PROD-01,02 | unit | `python -m pytest tests/test_commissioning.py tests/test_coordinator.py -q` | depends on W1 | ⬜ pending |

---

## Wave 0 Requirements

- [ ] `tests/test_commissioning.py` — stubs for PROD-01, PROD-02

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Staged rollout on real hardware | PROD-01 | Requires live batteries | Deploy to HA, verify READ_ONLY → SINGLE → DUAL progression |
| Shadow mode decision accuracy | PROD-02 | Requires production load patterns | Run shadow mode for 24h, compare logged vs actual decisions |
| Victron watchdog guard timing | PROD-03 | Requires real Venus OS | Kill control loop, verify 0W writes continue every 45s |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
