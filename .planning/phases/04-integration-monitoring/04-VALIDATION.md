---
phase: 04
slug: integration-monitoring
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 04 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8+ with pytest-anyio |
| **Config file** | `pyproject.toml` [tool.pytest.ini_options] |
| **Quick run command** | `python -m pytest tests/test_coordinator.py tests/test_influx_writer.py tests/test_ha_mqtt_client.py tests/test_api.py -x -q` |
| **Full suite command** | `python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_coordinator.py tests/test_influx_writer.py tests/test_ha_mqtt_client.py tests/test_api.py -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | INT-04 | unit | `python -m pytest tests/test_coordinator.py -k decision -x` | ❌ W0 | ⬜ pending |
| 04-01-02 | 01 | 1 | INT-01 | unit | `python -m pytest tests/test_coordinator.py -k evcc_hold -x` | ✅ partial | ⬜ pending |
| 04-01-03 | 01 | 1 | INT-03 | unit | `python -m pytest tests/test_coordinator.py -k "degrad or fail" -x` | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 | 1 | INT-07 | unit | `python -m pytest tests/test_influx_writer.py -k per_system -x` | ❌ W0 | ⬜ pending |
| 04-02-02 | 02 | 1 | INT-08 | unit | `python -m pytest tests/test_ha_mqtt_client.py -k "discovery or entities" -x` | ✅ partial | ⬜ pending |
| 04-03-01 | 03 | 2 | INT-02 | unit | `python -m pytest tests/test_api.py -k "devices or state" -x` | ✅ partial | ⬜ pending |
| 04-03-02 | 03 | 2 | INT-04 | unit | `python -m pytest tests/test_api.py -k decisions -x` | ❌ W0 | ⬜ pending |
| 04-03-03 | 03 | 2 | INT-05 | unit | `python -m pytest tests/test_victron_controller.py -k "phase or discharge" -x` | ✅ | ⬜ pending |
| 04-03-04 | 03 | 2 | INT-06 | unit | `python -m pytest tests/test_coordinator.py -k grid_charge -x` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_coordinator.py` — add decision logging tests (INT-04)
- [ ] `tests/test_coordinator.py` — add mid-run integration failure tests (INT-03)
- [ ] `tests/test_influx_writer.py` — add per-system metrics tests (INT-07)
- [ ] `tests/test_ha_mqtt_client.py` — add new entity discovery tests (INT-08)
- [ ] `tests/test_api.py` — add `/api/decisions` and expanded `/api/health` tests (INT-02, INT-04)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| InfluxDB measurements visible in Grafana | INT-07 | Requires running InfluxDB instance | Run EMS with InfluxDB, query `ems_huawei` and `ems_victron` measurements |
| HA entities update in HA dashboard | INT-08 | Requires running HA + MQTT broker | Start HA add-on, verify entities in Developer Tools > States |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
