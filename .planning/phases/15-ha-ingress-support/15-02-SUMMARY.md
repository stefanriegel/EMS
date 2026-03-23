---
phase: 15-ha-ingress-support
plan: 02
subsystem: ui
tags: [vite, react, ingress, websocket, relative-paths]

requires:
  - phase: 05-dashboard
    provides: React frontend with WebSocket and polling hooks
provides:
  - Relative Vite base path for Ingress-compatible asset loading
  - Dynamic WebSocket URL from window.location with protocol detection
  - All fetch() calls use relative ./api/ paths
affects: []

tech-stack:
  added: []
  patterns: [relative-url-construction, dynamic-ws-protocol-detection]

key-files:
  created: []
  modified:
    - frontend/vite.config.ts
    - frontend/index.html
    - frontend/src/App.tsx
    - frontend/src/pages/Login.tsx
    - frontend/src/hooks/useEmsState.ts
    - frontend/src/hooks/useDecisions.ts
    - frontend/src/hooks/useForecast.ts

key-decisions:
  - "Used new URL('./api/ws/state', location.href) for WS URL -- resolves correctly under any base path"

patterns-established:
  - "Relative fetch: all API calls use ./api/ prefix, never absolute /api/"
  - "Dynamic WS URL: protocol detection (ws:/wss:) via location.protocol check"

requirements-completed: [INGR-03, INGR-04, INGR-06]

duration: 1min
completed: 2026-03-23
---

# Phase 15 Plan 02: Frontend Ingress Paths Summary

**Vite relative base config and dynamic WS/fetch URL construction for HA Ingress compatibility**

## Performance

- **Duration:** 1 min
- **Started:** 2026-03-23T21:30:05Z
- **Completed:** 2026-03-23T21:31:12Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- Vite base set to './' so built assets use relative paths in dist/index.html
- All 6 fetch() call sites converted from absolute /api/ to relative ./api/
- WebSocket URL constructed dynamically via new URL() with wss:/ws: protocol detection
- Frontend works identically under direct access (/) and Ingress (/api/hassio_ingress/{token}/)

## Task Commits

Each task was committed atomically:

1. **Task 1: Configure Vite relative base and fix index.html** - `0e13c17` (feat)
2. **Task 2: Convert all fetch calls and WS URL to relative paths** - `07bc8e5` (feat)

## Files Created/Modified
- `frontend/vite.config.ts` - Added base: './' for relative asset paths
- `frontend/index.html` - Changed favicon and script src to relative paths
- `frontend/src/App.tsx` - Dynamic WS URL via buildWsUrl(), relative auth check fetch
- `frontend/src/pages/Login.tsx` - Relative fetch for /api/auth/login
- `frontend/src/hooks/useEmsState.ts` - Relative fetch for /api/state and /api/devices
- `frontend/src/hooks/useDecisions.ts` - Relative fetch for /api/decisions
- `frontend/src/hooks/useForecast.ts` - Relative fetch for /api/optimization/forecast

## Decisions Made
- Used `new URL("./api/ws/state", location.href)` for WS URL construction -- this resolves the relative path against the current page URL, which includes the Ingress prefix when loaded via HA sidebar
- Changed dev mode script src in index.html to relative for consistency (Vite transforms it anyway)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Frontend fully Ingress-compatible with relative paths
- Combined with Plan 01 (backend Ingress middleware), the full Ingress stack is complete

---
*Phase: 15-ha-ingress-support*
*Completed: 2026-03-23*
