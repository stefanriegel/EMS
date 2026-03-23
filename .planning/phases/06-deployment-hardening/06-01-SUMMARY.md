---
phase: 06-deployment-hardening
plan: 01
subsystem: infra
tags: [dockerfile, ha-addon, config, deployment, modbus, victron]

# Dependency graph
requires:
  - phase: 01-victron-modbus-driver
    provides: VictronConfig.from_env() with unit ID env vars
  - phase: 02-coordinator
    provides: Coordinator tuning parameters (deadband, ramp, min SoC)
  - phase: 03-pv-tariff-optimization
    provides: Modul3 tariff window configuration
provides:
  - Consolidated multi-stage Dockerfile (Node.js frontend build + HA base Python runtime)
  - Extended HA Add-on config.yaml with Victron unit IDs, coordinator tuning, and Modul3 tariff fields
  - Updated run.sh bridging all new options.json fields to backend env vars
  - English and German translations for all new config fields
affects: [06-deployment-hardening]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Multi-stage Docker build: Node.js frontend + HA base Python runtime in single Dockerfile"
    - "Nonempty-only export pattern for optional HA Add-on config fields"

key-files:
  created: []
  modified:
    - Dockerfile
    - docker-compose.yml
    - ha-addon/config.yaml
    - ha-addon/run.sh
    - ha-addon/translations/en.yaml
    - ha-addon/translations/de.yaml

key-decisions:
  - "Single Dockerfile at repo root replaces both root and ha-addon/ Dockerfiles"
  - "Victron port default changed from 1883 (MQTT) to 502 (Modbus TCP) throughout"
  - "Coordinator tuning and Modul3 tariff fields use optional schema types (int?, str?)"

patterns-established:
  - "Multi-stage Dockerfile: frontend-build stage + HA base runtime stage"
  - "All new config options use nonempty-only export in run.sh"

requirements-completed: [DEP-01, DEP-02]

# Metrics
duration: 3min
completed: 2026-03-23
---

# Phase 6 Plan 1: Dockerfile Consolidation and Config Extension Summary

**Multi-stage Dockerfile consolidation removing 33 stale files, plus HA Add-on config extension for Victron Modbus unit IDs, coordinator tuning, and Modul3 tariff fields**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-23T10:14:43Z
- **Completed:** 2026-03-23T10:17:43Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Replaced single-stage Dockerfile with multi-stage build (Node.js frontend + HA Alpine Python runtime)
- Removed 33 stale duplicate files from ha-addon/ (backend/, dist/, pyproject.toml, Dockerfile)
- Extended config.yaml with 14 new fields (3 Victron unit IDs, 5 coordinator tuning, 6 Modul3 tariff)
- Updated run.sh to export all new fields using nonempty-only pattern
- Added English and German translations for all new configuration fields

## Task Commits

Each task was committed atomically:

1. **Task 1: Consolidate Dockerfile and delete stale ha-addon duplicates** - `a8805d4` (feat)
2. **Task 2: Extend config.yaml schema, run.sh exports, and translations for v2 fields** - `9492c75` (feat)

## Files Created/Modified
- `Dockerfile` - Consolidated multi-stage build (Node.js frontend + HA base Python)
- `docker-compose.yml` - Explicit build context, added VICTRON_PORT default
- `ha-addon/config.yaml` - Added 14 new fields in options and schema sections
- `ha-addon/run.sh` - Added exports for Victron unit IDs, coordinator tuning, Modul3 tariff
- `ha-addon/translations/en.yaml` - English labels and descriptions for all new fields
- `ha-addon/translations/de.yaml` - German labels and descriptions for all new fields

## Decisions Made
- Single Dockerfile at repo root replaces both root and ha-addon/ Dockerfiles -- simplifies build pipeline
- Victron port default changed from 1883 (MQTT) to 502 (Modbus TCP) to match v2 Modbus architecture
- Coordinator tuning and Modul3 tariff fields use optional schema types (int?, str?) so defaults in backend config.py apply when not set

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Dockerfile ready for HA Add-on builder (BUILD_FROM arg passed by build.yaml)
- ha-addon/ directory is clean: only config.yaml, build.yaml, run.sh, translations/, DOCS.md, CHANGELOG.md, icon.png, logo.png
- All new env vars match backend/config.py from_env() expected names

## Self-Check: PASSED

All 6 files verified present. Both task commits (a8805d4, 9492c75) verified in git log.

---
*Phase: 06-deployment-hardening*
*Completed: 2026-03-23*
