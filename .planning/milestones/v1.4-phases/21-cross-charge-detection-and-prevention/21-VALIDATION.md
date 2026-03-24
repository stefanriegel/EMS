---
phase: 21
slug: cross-charge-detection-and-prevention
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-24
---

# Phase 21 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x with anyio (backend), Playwright (frontend) |
| **Config file** | pyproject.toml (asyncio_mode = "auto") |
| **Quick run command** | `python -m pytest tests/test_cross_charge.py tests/test_coordinator.py -q` |
| **Full suite command** | `python -m pytest tests/ -q` |
| **Estimated runtime** | ~15 seconds (backend), ~30 seconds (E2E) |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_cross_charge.py -q`
- **After every plan wave:** Run `python -m pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 21-01-01 | 01 | 1 | XCHG-01,02,03 | unit | `python -m pytest tests/test_cross_charge.py -q` | ❌ W0 | ⬜ pending |
| 21-02-01 | 02 | 2 | XCHG-04,05 | unit | `python -m pytest tests/test_cross_charge.py -q -k alert_or_influx` | ❌ W0 | ⬜ pending |
| 21-03-01 | 03 | 2 | XCHG-06 | E2E | `npx playwright test cross-charge` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_cross_charge.py` — stubs for XCHG-01 through XCHG-05
- [ ] `frontend/tests/cross-charge.spec.ts` — stubs for XCHG-06

*Existing test infrastructure covers framework and fixtures.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Cross-charge badge visibility | XCHG-06 | Visual CSS animation | Inspect EnergyFlowCard with cross_charge_active=true in dev tools |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
