---
phase: 15-ha-ingress-support
plan: 01
subsystem: infra
tags: [ha-ingress, asgi-middleware, auth-bypass, home-assistant]

# Dependency graph
requires:
  - phase: 12-wizard-removal
    provides: "AuthMiddleware with JWT cookie auth on /api/* routes"
provides:
  - "IngressMiddleware ASGI middleware for root_path from X-Ingress-Path"
  - "Auth bypass for Ingress requests in AuthMiddleware"
  - "HA Supervisor Ingress config fields in config.yaml"
affects: [15-02 frontend ingress path handling]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Raw ASGI middleware for WebSocket-compatible request processing"]

key-files:
  created: [backend/ingress.py]
  modified: [ha-addon/config.yaml, backend/auth.py, backend/main.py]

key-decisions:
  - "Raw ASGI middleware instead of BaseHTTPMiddleware for WebSocket scope support"
  - "ingress_port: 8000 matches uvicorn listen port (Supervisor connects to container internal port)"
  - "IngressMiddleware runs before AuthMiddleware in request chain via Starlette add_middleware ordering"

patterns-established:
  - "Ingress detection via X-Ingress-Path header presence"
  - "Auth bypass for HA-authenticated Ingress requests"

requirements-completed: [INGR-01, INGR-02, INGR-05]

# Metrics
duration: 2min
completed: 2026-03-23
---

# Phase 15 Plan 01: HA Ingress Backend Support Summary

**Raw ASGI IngressMiddleware sets root_path from X-Ingress-Path header, with JWT auth bypass for Supervisor-authenticated Ingress requests**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-23T21:30:05Z
- **Completed:** 2026-03-23T21:31:58Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- HA Supervisor Ingress config fields added (ingress: true, panel_icon, panel_title)
- IngressMiddleware reads X-Ingress-Path header and sets ASGI scope root_path for correct URL generation
- AuthMiddleware bypasses JWT auth when X-Ingress-Path header is present (HA session is trusted)
- Middleware chain order: IngressMiddleware -> AuthMiddleware -> routes

## Task Commits

Each task was committed atomically:

1. **Task 1: Add Ingress fields to config.yaml and create IngressMiddleware** - `556a415` (feat)
2. **Task 2: Add Ingress auth bypass and wire middleware into app** - `f635438` (feat)

## Files Created/Modified
- `backend/ingress.py` - Raw ASGI middleware setting scope root_path from X-Ingress-Path header
- `ha-addon/config.yaml` - Added ingress: true, ingress_port, ingress_entry, panel_icon, panel_title
- `backend/auth.py` - Added Ingress bypass check before JWT enforcement in AuthMiddleware.dispatch()
- `backend/main.py` - Imported and registered IngressMiddleware in middleware chain

## Decisions Made
- Used raw ASGI middleware (not BaseHTTPMiddleware) for IngressMiddleware to support WebSocket scope types
- Set ingress_port to 8000 (same as uvicorn) since Supervisor connects directly to container internal port
- Placed Ingress bypass after auth-disabled check but before path-based exemptions in AuthMiddleware

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Backend is fully Ingress-aware with middleware chain handling both access methods
- Ready for Plan 02: Frontend Ingress path handling (asset URLs, WebSocket connections)

---
*Phase: 15-ha-ingress-support*
*Completed: 2026-03-23*
