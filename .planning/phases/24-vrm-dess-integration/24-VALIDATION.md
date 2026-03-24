---
phase: 24
slug: vrm-dess-integration
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-24
---

# Phase 24 — Validation Strategy

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x with anyio |
| **Quick run command** | `python -m pytest tests/test_vrm_client.py tests/test_dess_mqtt.py -q` |
| **Full suite command** | `python -m pytest tests/ -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_vrm_client.py tests/test_dess_mqtt.py -q`
- **After every plan wave:** Run `python -m pytest tests/ -q`
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 24-01-01 | 01 | 1 | DESS-01,02,04 | unit | `python -m pytest tests/test_vrm_client.py tests/test_dess_mqtt.py -q` | ❌ W0 | ⬜ pending |
| 24-02-01 | 02 | 2 | DESS-03,04 | unit | `python -m pytest tests/test_coordinator.py tests/test_dess_mqtt.py -q` | depends W1 | ⬜ pending |

---

## Wave 0 Requirements

- [ ] `tests/test_vrm_client.py` — stubs for DESS-01
- [ ] `tests/test_dess_mqtt.py` — stubs for DESS-02

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| VRM API live response | DESS-01 | Requires VRM credentials + internet | Set EMS_VRM_TOKEN, verify /api/health shows VRM data |
| Venus OS MQTT DESS topics | DESS-02 | Requires Venus OS broker | Connect to Venus MQTT, verify DESS schedule parsing |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity maintained
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
