---
phase: 12-wizard-removal
plan: 01
subsystem: config
tags: [setup-wizard, lifespan, env-vars, ha-addon]

# Dependency graph
requires: []
provides:
  - "Setup wizard code removed (setup_api.py, setup_config.py deleted)"
  - "Lifespan reads config exclusively from env vars and Supervisor discovery"
  - "JWT secret resolution preserved via EMS_CONFIG_PATH directory"
affects: [13-mqtt-discovery, 15-ingress]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Env-var-only config: all config via HA Add-on options -> run.sh -> env vars"

key-files:
  created: []
  modified:
    - backend/main.py
    - tests/test_auth.py

key-decisions:
  - "Kept EMS_CONFIG_PATH in ha-addon/run.sh for JWT secret directory resolution"
  - "Removed test_setup_status_exempt_when_auth_enabled since /api/setup/* no longer exists"

patterns-established:
  - "No ems_config.json fallback: Add-on options page is the sole config surface"

requirements-completed: [CFG-01, CFG-02]

# Metrics
duration: 3min
completed: 2026-03-23
---

# Phase 12 Plan 01: Wizard Removal Summary

**Deleted setup wizard backend (setup_api.py, setup_config.py) and simplified main.py lifespan to use env vars exclusively**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-23T17:03:03Z
- **Completed:** 2026-03-23T17:06:00Z
- **Tasks:** 2
- **Files modified:** 6 (4 deleted, 2 modified)

## Accomplishments
- Deleted setup_api.py, setup_config.py and their test files (4 files, ~980 lines removed)
- Simplified main.py lifespan: no wizard config loading, env vars are sole config source
- Updated test_auth.py to remove setup_router dependency; all 30 auth tests pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Delete setup wizard backend files and clean main.py lifespan** - `3cf43d3` (feat)
2. **Task 2: Update test_auth.py to remove setup_router dependency** - `ad78097` (test)

## Files Created/Modified
- `backend/setup_api.py` - Deleted (setup wizard API routes)
- `backend/setup_config.py` - Deleted (wizard config persistence layer)
- `tests/test_setup_api.py` - Deleted (setup wizard API tests)
- `tests/test_setup_config.py` - Deleted (setup config tests)
- `backend/main.py` - Removed wizard imports, config loading block, and setup_router
- `tests/test_auth.py` - Removed setup_router import and setup-related test

## Decisions Made
- Kept EMS_CONFIG_PATH export in ha-addon/run.sh since ensure_jwt_secret still uses the directory
- Removed test_setup_status_exempt_when_auth_enabled test since the /api/setup/* routes no longer exist

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed stale test referencing deleted endpoint**
- **Found during:** Task 2
- **Issue:** test_setup_status_exempt_when_auth_enabled tests GET /api/setup/status which no longer exists
- **Fix:** Removed the test entirely; updated module docstring to remove /api/setup/* from exempt paths
- **Files modified:** tests/test_auth.py
- **Verification:** All 30 remaining auth tests pass
- **Committed in:** ad78097

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Test cleanup necessary since tested endpoint was removed. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Backend no longer has any setup wizard code
- Config flows exclusively through env vars (run.sh) and Supervisor discovery
- Ready for MQTT discovery overhaul (Phase 13) and Ingress support (Phase 15)

---
*Phase: 12-wizard-removal*
*Completed: 2026-03-23*
