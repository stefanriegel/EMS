---
phase: 05
slug: dashboard
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 05 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Playwright 1.58.2 (E2E), TypeScript compiler (type-check) |
| **Config file** | `frontend/playwright.config.ts`, `frontend/tsconfig.json` |
| **Quick run command** | `cd frontend && npx tsc --noEmit` |
| **Full suite command** | `cd frontend && npx tsc --noEmit && npx playwright test` |
| **Estimated runtime** | ~15 seconds (tsc) + ~30 seconds (Playwright) |

---

## Sampling Rate

- **After every task commit:** Run `cd frontend && npx tsc --noEmit`
- **After every plan wave:** Run `cd frontend && npx tsc --noEmit && npx playwright test`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 45 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | UI-01, UI-04 | type-check + E2E | `npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| 05-01-02 | 01 | 1 | UI-03 | type-check + E2E | `npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| 05-02-01 | 02 | 2 | UI-02 | type-check + E2E | `npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| 05-02-02 | 02 | 2 | UI-05 | type-check + E2E | `npx tsc --noEmit` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `frontend/e2e/dashboard.spec.ts` — E2E tests for dual-battery display, decision log, energy flow
- [ ] Existing `frontend/tsconfig.json` — type-checking covers all new types and components

*Playwright test infrastructure already exists. Wave 0 adds phase-specific test files.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| SVG flow animation direction | UI-03 | CSS animation visual correctness | Open dashboard, verify flow paths animate in correct direction when charging/discharging |
| Responsive layout on mobile | UI-01 | Visual breakpoint verification | Resize browser to <768px, verify single-column card layout |
| SoC arc visual accuracy | UI-03 | Visual proportionality of arc fill | Compare SoC arc fill level to numeric SoC percentage displayed |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 45s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
