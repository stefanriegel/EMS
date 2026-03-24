---
phase: 6
slug: deployment-hardening
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x (backend), Playwright 1.58.2 (frontend E2E) |
| **Config file** | `pyproject.toml` (pytest section), `frontend/playwright.config.ts` |
| **Quick run command** | `python -m pytest backend/tests/ -x -q --timeout=10` |
| **Full suite command** | `python -m pytest backend/tests/ -q && cd frontend && npx playwright test` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest backend/tests/ -x -q --timeout=10`
- **After every plan wave:** Run `python -m pytest backend/tests/ -q && cd frontend && npx playwright test`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | DEP-01 | unit + integration | `python -m pytest backend/tests/test_config.py -x -q` | ✅ | ⬜ pending |
| TBD | TBD | TBD | DEP-02 | unit | `python -m pytest backend/tests/test_setup_config.py -x -q` | ✅ | ⬜ pending |
| TBD | TBD | TBD | DEP-03 | E2E | `cd frontend && npx playwright test setup` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Multi-arch Docker build (aarch64/amd64) | DEP-01 | Requires cross-platform Docker buildx or real hardware | Run `docker buildx build --platform linux/amd64,linux/arm64 .` and verify both manifest entries |
| HA Add-on install via Supervisor | DEP-01 | Requires running HA Supervisor instance | Add repository URL in HA → Add-ons → Repositories, verify add-on appears and installs |
| Supervisor service discovery detects MQTT | DEP-02 | Requires HA Supervisor services API | Install on HA with Mosquitto add-on, verify auto-detection in setup wizard |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
