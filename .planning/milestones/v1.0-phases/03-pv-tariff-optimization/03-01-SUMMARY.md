---
phase: 03-pv-tariff-optimization
plan: 01
subsystem: coordinator
tags: [soc-headroom, pv-surplus, min-soc-profile, time-of-day, charge-allocation]

requires:
  - phase: 02-coordinator-controllers
    provides: Coordinator with _allocate_charge, _run_cycle, role assignment, debounce

provides:
  - SoC-headroom-weighted PV surplus distribution replacing Huawei-first allocation
  - MinSocWindow dataclass for time-of-day min-SoC profiles
  - _get_effective_min_soc method with wrapping window support
  - CoordinatorState effective min-SoC fields for API exposure
  - Comprehensive test coverage for headroom weighting, profiles, and grid charge

affects: [scheduler, api, dashboard]

tech-stack:
  added: [zoneinfo]
  patterns: [time-of-day profile evaluation with wrapping windows]

key-files:
  created: []
  modified:
    - backend/config.py
    - backend/coordinator.py
    - backend/controller_model.py
    - tests/test_coordinator.py

key-decisions:
  - "SoC headroom weighting uses (full_soc - current_soc) proportional split, not capacity-based"
  - "Overflow routing sends excess from rate-limited battery to the other"
  - "Min-SoC profiles evaluate first-match semantics with wrapping window support"
  - "Effective min-SoC exposed in CoordinatorState for frontend/API visibility"

patterns-established:
  - "MinSocWindow dataclass: (start_hour, end_hour, min_soc_pct) with wrapping support"
  - "Profile evaluation: first matching window wins, static fallback when no match"

requirements-completed: [OPT-01, OPT-02, OPT-03, OPT-05]

duration: 4min
completed: 2026-03-22
---

# Phase 3 Plan 1: PV Surplus & Min-SoC Profiles Summary

**SoC-headroom-weighted PV surplus distribution with time-of-day min-SoC profiles and verified parallel grid charge behavior**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T09:27:15Z
- **Completed:** 2026-03-22T09:31:25Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Replaced Huawei-first charge allocation with proportional SoC headroom weighting (OPT-01)
- Added MinSocWindow dataclass and _get_effective_min_soc with wrapping window support (OPT-05)
- Verified parallel grid charge staggering behavior meets OPT-02/OPT-03 requirements
- 14 new tests across 3 test classes, all 1122 tests in full suite passing

## Task Commits

Each task was committed atomically:

1. **Task 1: Add MinSocWindow and rewrite coordinator allocation + min-SoC evaluation** - `a827c02` (test: RED) + `c49cd3d` (feat: GREEN)
2. **Task 2: Comprehensive tests for headroom weighting, min-SoC profiles, grid charge** - included in Task 1 commits (TDD flow combined)

_Note: TDD RED-GREEN commits cover both tasks since test classes were written in the RED phase._

## Files Created/Modified
- `backend/config.py` - Added MinSocWindow dataclass and SystemConfig profile fields
- `backend/coordinator.py` - Headroom-weighted _allocate_charge, _get_effective_min_soc, profile-aware _run_cycle
- `backend/controller_model.py` - CoordinatorState effective min-SoC fields for API
- `tests/test_coordinator.py` - TestPvSurplusHeadroomWeighting (5), TestMinSocProfiles (6), TestGridChargeStaggering (3)

## Decisions Made
- SoC headroom weighting uses `max(0, full_soc - current_soc)` for proportional split -- simpler and more predictable than capacity-weighted
- Overflow routing adds rate-limited excess to the other battery -- ensures all available surplus is used
- Min-SoC profiles use first-match semantics with wrapping window support (start > end means midnight wrap)
- Effective min-SoC fields added to CoordinatorState so the API and frontend can display active thresholds

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Coordinator now has headroom-weighted PV surplus allocation and time-of-day min-SoC profiles
- Ready for Plan 02 (scheduler enhancements with solar-aware target reduction)
- API already exposes effective min-SoC through CoordinatorState

## Self-Check: PASSED

- All 4 modified files exist on disk
- Both commit hashes (a827c02, c49cd3d) found in git log
- Full test suite: 1122 passed, 11 skipped

---
*Phase: 03-pv-tariff-optimization*
*Completed: 2026-03-22*
