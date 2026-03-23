---
phase: 17
slug: consumption-forecaster-upgrade
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-23
---

# Phase 17 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pyproject.toml |
| **Quick run command** | `python -m pytest tests/test_consumption_forecaster.py -q --no-header -x` |
| **Full suite command** | `python -m pytest tests/ -q --no-header` |
| **Estimated runtime** | ~8 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_consumption_forecaster.py -q --no-header -x`
- **After every plan wave:** Run `python -m pytest tests/ -q --no-header`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 17-01-01 | 01 | 1 | FCST-01, FCST-02 | unit | `python -m pytest tests/test_consumption_forecaster.py -q -k "weather or lag"` | ✅ (extended) | ⬜ pending |
| 17-02-01 | 02 | 1 | FCST-04, FCST-05 | unit | `python -m pytest tests/test_consumption_forecaster.py -q -k "histgradient or cv"` | ✅ (extended) | ⬜ pending |
| 17-03-01 | 03 | 2 | FCST-03, FCST-06 | unit | `python -m pytest tests/test_consumption_forecaster.py -q -k "mape"` | ✅ (extended) | ⬜ pending |
| 17-03-02 | 03 | 2 | FCST-07 | integration | `python -m pytest tests/test_api.py -q -k "ml_status"` | ✅ (extended) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

*Existing test infrastructure covers all phase requirements. Tests extend existing files.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real weather data improves predictions | FCST-01 | Needs multi-day production data | Monitor MAPE trend over 2+ weeks after deployment |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 10s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
