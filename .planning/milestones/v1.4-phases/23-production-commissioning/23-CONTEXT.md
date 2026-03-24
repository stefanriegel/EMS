# Phase 23: Production Commissioning - Context

**Gathered:** 2026-03-24
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

Both batteries operate under live EMS control with staged rollout and safety guards. Implements a configurable commissioning state machine that progresses through stages: READ_ONLY → SINGLE_BATTERY → DUAL_BATTERY, with documented progression criteria at each gate. Adds shadow mode (log decisions without executing writes) and a Victron 45-second emergency zero-write guard to prevent the 60-second watchdog timeout.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — infrastructure phase. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

Key constraints:
- Staged rollout: READ_ONLY → SINGLE_BATTERY (Victron only, since Huawei mode manager handles its own writes) → DUAL_BATTERY
- Each stage transition requires documented criteria (configurable via env vars)
- Shadow mode: coordinator computes decisions and logs them but does NOT call controller.execute()
- Shadow mode must be configurable via env var (default: True for safety)
- Victron 45s guard: periodic zero-write to keep the 60s watchdog from firing during normal operation
- Guard runs as a background task in the coordinator, independent of the 5s control loop
- All commissioning state exposed via /api/health and HA MQTT
- Commissioning stages persist across restarts (file-based state in /config/ems_commissioning.json)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- Coordinator._run_cycle() — the 5s control loop where shadow mode intercepts before execute()
- HardwareValidationConfig — Phase 20's dry_run and validation_period provide the read-only foundation
- DecisionEntry — log shadow decisions with trigger="shadow_mode"
- Config dataclass pattern with from_env()
- JSON file persistence pattern from ModelStore (Phase 16)

### Established Patterns
- Optional injection: set_xxx() methods, None guards
- Fire-and-forget for integrations
- Background tasks via asyncio.create_task() in lifespan
- /api/health extension pattern (already extended in Phase 21 for cross-charge)

### Integration Points
- backend/main.py — commissioning manager wired in lifespan
- backend/coordinator.py — shadow mode guard before execute(), commissioning state in CoordinatorState
- backend/config.py — CommissioningConfig dataclass
- backend/api.py — /api/health commissioning section
- backend/victron_controller.py — 45s watchdog guard task

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. Refer to ROADMAP phase description and success criteria.

</specifics>

<deferred>
## Deferred Ideas

None — infrastructure phase.

</deferred>
