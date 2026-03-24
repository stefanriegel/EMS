# Phase 22: Huawei Mode Manager - Context

**Gathered:** 2026-03-24
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

EMS takes authoritative control of Huawei by managing TOU working mode lifecycle. HuaweiModeManager is a state machine that switches Huawei to TIME_OF_USE_LUNA2000 mode (register 47086) on startup, restores MAXIMISE_SELF_CONSUMPTION on shutdown, periodically verifies mode hasn't reverted, and clamps power to zero during mode transitions with a configurable settle delay.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — pure infrastructure phase. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

Key constraints from research:
- Use existing `HuaweiDriver.write_battery_mode(StorageWorkingModesC.TIME_OF_USE_LUNA2000)` for TOU mode
- Use existing `write_max_charge_power(0)` and `write_max_discharge_power(0)` for power clamping before mode switch
- Mode transitions: clamp → wait 1 cycle (5s) → switch → wait settle (5s) → resume setpoints
- Mode health check: read current working mode periodically, re-apply if reverted
- Shutdown: restore to `MAXIMISE_SELF_CONSUMPTION`, must be idempotent and handle crash recovery
- Safe-state writes must bypass mode manager checks
- Follow existing injection pattern: `set_mode_manager()` on HuaweiController or Coordinator
- Expose current Huawei working mode via HA MQTT entity

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `HuaweiDriver.write_battery_mode()` — existing method for register 47086
- `StorageWorkingModesC` enum — imported from huawei_solar, includes TIME_OF_USE_LUNA2000
- `HuaweiController` — wraps driver, has execute() method for setpoints
- `backend/main.py` lifespan — startup/shutdown hooks
- Config pattern: dataclass with `from_env()` classmethod

### Established Patterns
- Optional injection: `set_xxx()` methods, None guards
- Startup/shutdown in FastAPI lifespan context manager
- Periodic checks: nightly scheduler loop in coordinator
- Fire-and-forget for non-critical operations

### Integration Points
- `backend/main.py` — create and wire mode manager at startup, restore on shutdown
- `HuaweiController` — mode health checks during poll() or execute()
- `backend/config.py` — new config fields for settle delay
- Coordinator — mode manager awareness for transition safety

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. Refer to ROADMAP phase description and success criteria.

</specifics>

<deferred>
## Deferred Ideas

None — infrastructure phase.

</deferred>
