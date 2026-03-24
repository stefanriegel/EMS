---
phase: 19
slug: self-tuning-control
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-24
---

# Phase 19 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pyproject.toml |
| **Quick run command** | `python -m pytest tests/test_self_tuner.py -q --no-header -x` |
| **Full suite command** | `python -m pytest tests/ -q --no-header` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_self_tuner.py -q --no-header -x`
- **After every plan wave:** Run `python -m pytest tests/ -q --no-header`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 19-01-01 | 01 | 1 | TUNE-01..08 | unit | `python -m pytest tests/test_self_tuner.py -q` | new (TDD) | pending |
| 19-02-01 | 02 | 2 | TUNE-01, TUNE-08 | integration | `python -m pytest tests/test_self_tuner.py -q -k "integration"` | extended | pending |
| 19-02-02 | 02 | 2 | TUNE-08 | integration | `python -m pytest tests/test_api.py -q -k "tuning"` | extended | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_self_tuner.py` — new test file for SelfTuner class

*Existing pytest infrastructure covers framework requirements.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Shadow mode auto-promotion after 14 days | TUNE-03 | Requires 14+ days production runtime | Monitor tuning_state.json mode field over 2+ weeks |
| Real oscillation rate calibration | TUNE-01 | Requires real battery switching patterns | Observe transition_rate_per_hour with production loads |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 10s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
