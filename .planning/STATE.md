---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: Advanced Optimization
status: Ready to plan
stopped_at: Completed 08-02-PLAN.md
last_updated: "2026-03-23T14:23:16.468Z"
progress:
  total_phases: 5
  completed_phases: 2
  total_plans: 4
  completed_plans: 4
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-23)

**Core value:** Both battery systems operate independently with zero oscillation to maximize PV self-consumption
**Current focus:** Phase 08 — coordinator-export-integration

## Current Position

Phase: 9
Plan: Not started

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: -
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**

- Last 5 plans: -
- Trend: -

*Updated after each plan completion*

**v1.0 historical velocity (16 plans across 6 phases):**

| Phase | Plans | Avg/Plan |
|-------|-------|----------|
| Phase 01 | 3 | 2.7 min |
| Phase 02 | 3 | 4.7 min |
| Phase 03 | 2 | 3.0 min |
| Phase 04 | 3 | 3.3 min |
| Phase 05 | 2 | 3.0 min |
| Phase 06 | 2 | 7.5 min |
| Phase 07 P01 | 5min | 2 tasks | 12 files |
| Phase 07 P02 | 3min | 1 tasks | 2 files |
| Phase 08 P01 | 3min | 2 tasks | 12 files |
| Phase 08 P02 | 3min | 2 tasks | 3 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: Export is coordinator-level (not per-controller) to avoid oscillation
- [Roadmap]: ExportAdvisor is advisory pattern -- coordinator queries it, doesn't delegate control
- [Roadmap]: WeatherScheduler wraps existing Scheduler, doesn't modify it
- [Roadmap]: DayPlan model extends ChargeSchedule with per-day containers; Day 2/3 advisory only
- [Roadmap]: Fixed feed-in rate 0.074 EUR/kWh -- never discharge battery to grid
- [Phase 07]: ExportAdvisor uses sync advise() with cached forecast updated via async refresh_forecast()
- [Phase 07]: SoC threshold gate at 90% before any economic analysis
- [Phase 07]: Conservative default: STORE when forecaster unavailable or fallback used
- [Phase 07]: Export advisory runs as post-cycle hook in _loop(), not inside _run_cycle() -- avoids 6-path duplication
- [Phase 07]: Advisory-only in Plan 02: logs transitions but does not affect P_target (Phase 8 scope)
- [Phase 08]: Winter months stored as comma-separated string in flat config for HA options compatibility
- [Phase 08]: Export tests use debounce_cycles=1 for single-cycle role verification
- [Phase 08]: Higher-SoC system gets EXPORTING role (ties go to Huawei via >= comparison)

### Pending Todos

None yet.

### Blockers/Concerns

- Victron Venus OS Modbus register addresses need verification against actual firmware (v3.20+)
- Exact Victron unit ID assignments need probing or manual config
- EVCC solar forecast timeseries horizon not verified against live instance (may limit Day 3 advisories)
- ConsumptionForecaster multi-day accuracy unknown -- may need larger confidence discounts

## Session Continuity

Last session: 2026-03-23T14:19:49.607Z
Stopped at: Completed 08-02-PLAN.md
Resume file: None
