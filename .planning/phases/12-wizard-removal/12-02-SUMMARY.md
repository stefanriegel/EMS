---
phase: 12-wizard-removal
plan: 02
subsystem: ui
tags: [react, wouter, routing, setup-wizard]

requires:
  - phase: 05-dashboard
    provides: "React SPA with wouter routing and DashboardLayout"
provides:
  - "Clean App.tsx with only / and /login routes"
  - "SetupWizard.tsx removed from codebase"
affects: [15-ingress]

tech-stack:
  added: []
  patterns: ["Auth-only redirect on mount (fetch /api/state for 401 check)"]

key-files:
  created: []
  modified:
    - frontend/src/App.tsx

key-decisions:
  - "Auth check moved from /api/setup/status to /api/state — simpler, no setup dependency"

patterns-established:
  - "Auth redirect pattern: fetch /api/state on mount, redirect to /login on 401"

requirements-completed: [CFG-03]

duration: 1min
completed: 2026-03-23
---

# Phase 12 Plan 02: Remove Frontend Setup Wizard Summary

**Deleted SetupWizard.tsx (618 LOC) and /setup route; App.tsx now serves only dashboard and login routes with auth-only redirect**

## Performance

- **Duration:** 1 min
- **Started:** 2026-03-23T17:03:08Z
- **Completed:** 2026-03-23T17:04:05Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments
- Deleted SetupWizard.tsx entirely (618 lines of 6-step wizard component)
- Removed /setup route, SetupWizard import, and setup_complete redirect logic from App.tsx
- Replaced setup status check with simple auth check against /api/state
- Navigating to /setup now falls through to / (dashboard) via wouter default routing

## Task Commits

Each task was committed atomically:

1. **Task 1: Delete SetupWizard.tsx and clean App.tsx routing** - `7063f9d` (feat)

## Files Created/Modified
- `frontend/src/pages/SetupWizard.tsx` - Deleted (6-step setup wizard)
- `frontend/src/App.tsx` - Removed setup imports, route, redirect; simplified auth check

## Decisions Made
- Auth check endpoint changed from `/api/setup/status` to `/api/state` -- the setup endpoint is being removed by plan 12-01, so the auth redirect now uses the main state endpoint which returns 401 when unauthenticated

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Frontend is clean of all setup wizard references
- Ready for Phase 15 (Ingress) which will add HA Ingress path handling to App.tsx

---
*Phase: 12-wizard-removal*
*Completed: 2026-03-23*
