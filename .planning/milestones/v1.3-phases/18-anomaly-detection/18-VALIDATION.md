---
phase: 18
slug: anomaly-detection
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-23
---

# Phase 18 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pyproject.toml |
| **Quick run command** | `python -m pytest tests/test_anomaly_detector.py -q --no-header -x` |
| **Full suite command** | `python -m pytest tests/ -q --no-header` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_anomaly_detector.py -q --no-header -x`
- **After every plan wave:** Run `python -m pytest tests/ -q --no-header`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 18-01-01 | 01 | 1 | ANOM-01, ANOM-02, ANOM-03 | unit | `python -m pytest tests/test_anomaly_detector.py -q` | new (TDD) | pending |
| 18-02-01 | 02 | 1 | ANOM-04, ANOM-05 | unit | `python -m pytest tests/test_anomaly_detector.py -q -k "battery"` | extended | pending |
| 18-03-01 | 03 | 2 | ANOM-06, ANOM-07 | unit | `python -m pytest tests/test_anomaly_detector.py -q -k "isolation or nightly"` | extended | pending |
| 18-04-01 | 04 | 3 | ANOM-08 | integration | `python -m pytest tests/test_api.py -q -k "anomal"` | extended | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_anomaly_detector.py` — new test file for AnomalyDetector class

*Existing pytest infrastructure covers framework requirements.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Telegram notifications sent correctly | ANOM-08 | Requires real Telegram bot | Send test anomaly, verify message received in chat |
| Battery drift detection over weeks | ANOM-04/05 | Requires 14+ days of data | Monitor after 2+ weeks of production usage |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 10s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
