---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: Advanced Optimization
status: Defining requirements
stopped_at: null
last_updated: "2026-03-23T12:00:00.000Z"
progress:
  total_phases: 0
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-23)

**Core value:** Both battery systems operate independently with zero oscillation to maximize PV self-consumption
**Current focus:** Defining v1.1 requirements

## Current Position

Phase: Not started (defining requirements)
Plan: —

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
| Phase 01 P01 | 5min | 2 tasks | 5 files |
| Phase 01 P02 | 2min | 2 tasks | 2 files |
| Phase 01 P03 | 1min | 1 tasks | 1 files |
| Phase 02 P01 | 4min | 1 tasks | 6 files |
| Phase 02 P02 | 6min | 1 tasks | 2 files |
| Phase 02 P03 | 4min | 2 tasks | 3 files |
| Phase 03 P02 | 2min | 2 tasks | 2 files |
| Phase 03 P01 | 4min | 2 tasks | 4 files |
| Phase 04 P01 | 5min | 2 tasks | 5 files |
| Phase 04 P02 | 3min | 1 tasks | 3 files |
| Phase 04 P03 | 2min | 1 tasks | 2 files |
| Phase 05 P01 | 3min | 2 tasks | 4 files |
| Phase 05 P02 | 3min | 3 tasks | 10 files |
| Phase 06 P01 | 3min | 2 tasks | 6 files |
| Phase 06 P03 | 12min | 3 tasks | 3 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Roadmap: Victron Modbus driver is Phase 1 due to highest hardware verification risk
- Roadmap: Phases 3 and 4 both depend on Phase 2 but not on each other (could overlap)
- [Phase 01]: pymodbus 3.12.1 uses slave= parameter not device_id= for unit addressing
- [Phase 01]: VictronDriver consumption_w and pv_on_grid_w set to None (not available via Modbus)
- [Phase 01]: Protocol conformance via hasattr/inspect on class, not on instances (no @runtime_checkable)
- [Phase 02]: HuaweiBatteryData has no timestamp; controller tracks _last_read_time internally for stale detection
- [Phase 02]: Per-phase discharge uses -grid_lN_power_w matching existing orchestrator pattern
- [Phase 02]: Both-below-min-SoC check in _run_cycle not _assign_discharge_roles — keeps role assignment pure
- [Phase 02]: Coordinator config (deadband, ramp, SoC thresholds) as instance attrs, not in OrchestratorConfig dataclass
- [Phase 02]: app.state.orchestrator attribute name preserved for backward compat; Coordinator gains get_device_snapshot/get_last_error/get_working_mode
- [Phase 03]: Solar reduction only in formula fallback, EVopt path untouched (D-18)
- [Phase 03]: 1.2x threshold for full skip, 0.8x discount for partial solar coverage in formula fallback
- [Phase 03]: SoC headroom weighting uses (full_soc - current_soc) proportional split for PV surplus
- [Phase 03]: Min-SoC profiles use first-match with wrapping window support; static fallback when no match
- [Phase 04]: Roles stored as InfluxDB fields (not tags) in ems_decision to avoid high-cardinality tag explosion
- [Phase 04]: HA MQTT availability entities use device_class=None to avoid binary_sensor platform pitfall
- [Phase 04]: extra_fields parameter on HA MQTT publish() for per-phase Victron data not in CoordinatorState
- [Phase 04]: Decision entries only on role changes, allocation shifts >300W, or EVCC hold -- not every cycle
- [Phase 04]: Fire-and-forget integration writes: failures logged at WARNING, never block control loop
- [Phase 04]: Used getattr() with defaults for backward-compatible role field access in /api/devices
- [Phase 05]: Roles always read from pool (not devices) per backend WS contract
- [Phase 05]: Per-battery SoC arcs with separate CSS classes for independent color theming
- [Phase 05]: Native HTML details/summary for expandable sections (no JS state management needed)
- [Phase 05]: REST polling hooks with AbortController for non-critical data (decisions)
- [Phase 06]: Single Dockerfile at repo root replaces both root and ha-addon/ Dockerfiles
- [Phase 06]: Victron port default changed from 1883 (MQTT) to 502 (Modbus TCP)
- [Phase 06]: Coordinator tuning and Modul3 tariff fields use optional schema types (int?, str?)
- [Phase 06]: Used native HTML details/summary for Advanced toggle (consistent with Phase 5 pattern)

### Pending Todos

None yet.

### Blockers/Concerns

- Victron Venus OS Modbus TCP register addresses need verification against actual firmware (v3.20+)
- Exact Victron unit ID assignments need probing or manual config
- Ramp rate and dead-band tuning values are starting estimates, need empirical tuning

## Session Continuity

Last session: 2026-03-23T10:32:51.546Z
Stopped at: Completed 06-03-PLAN.md
Resume file: None
