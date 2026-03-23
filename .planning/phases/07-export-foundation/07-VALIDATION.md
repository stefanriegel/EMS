---
phase: 07
slug: export-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-23
---

# Phase 07 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8+ with pytest-anyio |
| **Config file** | `pyproject.toml` ([tool.pytest.ini_options]) |
| **Quick run command** | `python -m pytest tests/test_export_advisor.py -x` |
| **Full suite command** | `python -m pytest tests/ -x` |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_export_advisor.py -x`
- **Per wave merge:** `python -m pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

---

## Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command |
|--------|----------|-----------|-------------------|
| SCO-01 | ExportAdvisor never returns advice that would cause battery-to-grid discharge | unit | `python -m pytest tests/test_export_advisor.py::test_never_discharge_battery_to_grid -x` |
| SCO-01 | EXPORT only when batteries are full (combined SoC >= threshold) | unit | `python -m pytest tests/test_export_advisor.py::test_export_only_when_batteries_full -x` |
| SCO-02 | feed_in_rate_eur_kwh field exists in SystemConfig with default 0.074 | unit | `python -m pytest tests/test_export_advisor.py::test_feed_in_rate_config_default -x` |
| SCO-02 | feed_in_rate validation rejects negative values | unit | `python -m pytest tests/test_export_advisor.py::test_feed_in_rate_validation -x` |
| SCO-04 | Decision logged on STORE->EXPORT transition | unit | `python -m pytest tests/test_export_advisor.py::test_decision_logged_on_transition -x` |
| SCO-04 | No decision logged when state unchanged | unit | `python -m pytest tests/test_export_advisor.py::test_no_decision_on_same_state -x` |
| SCO-04 | Reasoning includes feed-in rate, import rate, forecast demand, SoC | unit | `python -m pytest tests/test_export_advisor.py::test_reasoning_content -x` |

---

## Wave 0 Gaps

- [ ] `tests/test_export_advisor.py` — covers SCO-01, SCO-02, SCO-04
- [ ] No new framework install needed — pytest already configured
