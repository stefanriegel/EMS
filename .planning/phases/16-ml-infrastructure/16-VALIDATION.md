---
phase: 16
slug: ml-infrastructure
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-23
---

# Phase 16 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pyproject.toml |
| **Quick run command** | `python -m pytest tests/ -q --no-header -x` |
| **Full suite command** | `python -m pytest tests/ -q --no-header` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ -q --no-header -x`
- **After every plan wave:** Run `python -m pytest tests/ -q --no-header`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 16-01-01 | 01 | 1 | INFRA-01 | unit | `python -m pytest tests/test_model_store.py -q` | ❌ W0 | ⬜ pending |
| 16-01-02 | 01 | 1 | INFRA-02 | unit | `python -m pytest tests/test_model_store.py -q -k version` | ❌ W0 | ⬜ pending |
| 16-02-01 | 02 | 1 | INFRA-03 | unit | `python -m pytest tests/test_feature_pipeline.py -q` | ❌ W0 | ⬜ pending |
| 16-03-01 | 03 | 1 | INFRA-04 | unit | `python -m pytest tests/test_training_executor.py -q` | ❌ W0 | ⬜ pending |
| 16-04-01 | 04 | 2 | INFRA-05 | integration | `grep OMP_NUM_THREADS Dockerfile` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_model_store.py` — stubs for INFRA-01, INFRA-02
- [ ] `tests/test_feature_pipeline.py` — stubs for INFRA-03
- [ ] `tests/test_training_executor.py` — stubs for INFRA-04

*Existing pytest infrastructure covers framework requirements.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| aarch64 training perf | INFRA-05 | Requires ARM hardware | Deploy to HA, run training, verify OMP_NUM_THREADS=2 in process env |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
