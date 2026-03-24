---
phase: 08-coordinator-export-integration
plan: 01
subsystem: coordinator
tags: [enum, config, seasonal, export, battery-role]

# Dependency graph
requires:
  - phase: 07-export-foundation
    provides: ExportAdvisor advisory pattern and SoC threshold gate
provides:
  - BatteryRole.EXPORTING enum value for grid export mode
  - winter_months and winter_min_soc_boost_pct config fields across all touchpoints
  - Seasonal strategy UI fields in setup wizard
affects: [08-coordinator-export-integration]

# Tech tracking
tech-stack:
  added: []
  patterns: [config-pipeline-10-touchpoint, seasonal-strategy-pattern]

key-files:
  created: []
  modified:
    - backend/controller_model.py
    - backend/config.py
    - backend/setup_config.py
    - backend/setup_api.py
    - backend/api.py
    - backend/main.py
    - ha-addon/config.yaml
    - ha-addon/run.sh
    - ha-addon/translations/en.yaml
    - ha-addon/translations/de.yaml
    - frontend/src/pages/SetupWizard.tsx
    - tests/test_coordinator.py

key-decisions:
  - "Winter months stored as comma-separated string in flat config (EmsSetupConfig, HA add-on) for consistency with HA options pattern"
  - "Winter boost capped at 50% via API validation (ge=0, le=50) to prevent excessive SoC floors"

patterns-established:
  - "Config pipeline pattern: new fields must flow through all 11 touchpoints (SystemConfig, EmsSetupConfig, SetupCompleteRequest, SystemConfigRequest, post_config, main.py, config.yaml, run.sh, en.yaml, de.yaml, SetupWizard.tsx)"

requirements-completed: [SCO-03]

# Metrics
duration: 3min
completed: 2026-03-23
---

# Phase 08 Plan 01: EXPORTING Role and Seasonal Config Summary

**BatteryRole.EXPORTING enum and winter_months/winter_min_soc_boost_pct config fields wired through all 11 config touchpoints with 4 validation tests**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-23T14:10:27Z
- **Completed:** 2026-03-23T14:13:20Z
- **Tasks:** 2
- **Files modified:** 12

## Accomplishments
- Added BatteryRole.EXPORTING enum value for coordinator export integration
- Wired winter_months (default [11,12,1,2]) and winter_min_soc_boost_pct (default 10) through all config touchpoints
- Added seasonal strategy section to setup wizard UI (Step 5: Tariff)
- 4 new tests validating config defaults, custom values, enum existence, and API model

## Task Commits

Each task was committed atomically:

1. **Task 1: Add EXPORTING role and seasonal config fields across all touchpoints** - `30f2167` (feat)
2. **Task 2: Tests for winter config defaults and EXPORTING role** - `1a0f3ed` (test)

## Files Created/Modified
- `backend/controller_model.py` - Added EXPORTING enum value to BatteryRole
- `backend/config.py` - Added winter_months and winter_min_soc_boost_pct to SystemConfig
- `backend/setup_config.py` - Added winter fields to EmsSetupConfig
- `backend/setup_api.py` - Added winter fields to SetupCompleteRequest
- `backend/api.py` - Added winter fields to SystemConfigRequest and post_config handler
- `backend/main.py` - Added WINTER_MONTHS/WINTER_MIN_SOC_BOOST_PCT env var reading and setup_cfg bridging
- `ha-addon/config.yaml` - Added options and schema entries for seasonal config
- `ha-addon/run.sh` - Added get_option/export for seasonal env vars
- `ha-addon/translations/en.yaml` - Added English labels for seasonal config
- `ha-addon/translations/de.yaml` - Added German labels for seasonal config
- `frontend/src/pages/SetupWizard.tsx` - Added FormValues fields, defaults, UI fields, and submit payload
- `tests/test_coordinator.py` - Added TestWinterConfig class with 4 tests

## Decisions Made
- Winter months stored as comma-separated string in flat config (EmsSetupConfig, HA add-on options) for consistency with how HA Supervisor passes list-like options
- Winter boost capped at 50% in API validation to prevent operators from setting unreasonable SoC floors

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- EXPORTING role and seasonal config are ready for Plan 02 coordinator logic
- Winter config fields are persisted and flow through the full pipeline
- All 139 existing tests plus 4 new tests pass

---
*Phase: 08-coordinator-export-integration*
*Completed: 2026-03-23*
