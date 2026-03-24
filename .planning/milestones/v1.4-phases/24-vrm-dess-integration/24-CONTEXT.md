# Phase 24: VRM/DESS Integration - Context

**Gathered:** 2026-03-24
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

EMS reads DESS schedule and VRM diagnostics to coordinate with Victron's autonomous operation. Adds VrmClient for REST API diagnostics, Venus OS MQTT subscription for DESS schedule data, and DESS-aware coordinator logic that avoids contradicting DESS charge/discharge windows when controlling Huawei. All integrations degrade gracefully when credentials are missing or connections fail.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — infrastructure phase. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

Key constraints from research:
- VRM API uses Personal Access Token auth (header: X-Authorization: Token <token>)
- VRM client polls diagnostics every 5 minutes (configurable), never blocks control loop
- DESS schedule reading via Venus OS MQTT broker (same infrastructure as EVCC MQTT)
- DESS schedule D-Bus paths: N/{portalId}/settings/0/Settings/DynamicEss/Schedule/[0-3]/{Soc,Start,Duration,Strategy}
- Coordinator DESS awareness: avoid issuing Huawei discharge during DESS Victron charge windows
- All VRM/DESS components must be optional — None checks, graceful degradation
- VRM client uses existing httpx library (already installed)
- Follow existing MQTT subscription pattern from evcc_mqtt_driver.py

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `evcc_mqtt_driver.py` — existing Venus OS MQTT subscription pattern
- `ha_mqtt_client.py` — MQTT connection management pattern
- `httpx` — async HTTP client already in dependencies
- Config dataclass pattern with from_env()
- Optional injection pattern: set_xxx() methods, None guards
- /api/health extension pattern (extended in Phase 21 and 23)

### Established Patterns
- Fire-and-forget for integrations (try/except, WARNING log)
- Async background tasks via asyncio.create_task() in lifespan
- Cached results for coordinator synchronous consumption (matching evcc_battery_mode pattern)
- DecisionEntry trigger values for decision transparency

### Integration Points
- backend/main.py — VRM client and DESS subscriber wired in lifespan
- backend/coordinator.py — DESS-aware discharge gating, CoordinatorState extension
- backend/config.py — VrmConfig, DessConfig dataclasses
- backend/api.py — /api/health DESS section
- backend/controller_model.py — DESS fields on CoordinatorState

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. Refer to ROADMAP phase description and success criteria.

</specifics>

<deferred>
## Deferred Ideas

None — infrastructure phase.

</deferred>
