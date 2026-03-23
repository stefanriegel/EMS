# Phase 8: Coordinator Export Integration - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Coordinator executes export decisions in real time with seasonal awareness, adding the EXPORTING battery role to the control loop. Export only occurs from direct PV surplus when batteries are full — never active battery discharge to grid.

</domain>

<decisions>
## Implementation Decisions

### EXPORTING Role Design
- Higher-SoC system exports (matches existing PRIMARY_DISCHARGE selection logic)
- EXPORTING added as new BatteryRole enum value alongside existing roles
- P_target offset for non-exporting system — subtract estimated export power from grid measurement so other battery doesn't react (prevents oscillation)
- No artificial export power limit — PV surplus naturally determines export amount via grid meter

### Seasonal Strategy
- Month-based season detection: Nov-Feb = winter, Mar-Oct = summer (configurable)
- Winter: raise min-SoC floor by +10% and increase grid charge targets proportionally
- Summer: allow natural PV export when both batteries above 90% SoC (default behavior)
- Config: `winter_months` (list, default [11,12,1,2]) and `winter_min_soc_boost_pct` (int, default 10) in SystemConfig

### Claude's Discretion
- Exact coordinator integration point for EXPORTING role assignment
- Test structure for seasonal and oscillation prevention
- Coordinator state machine transitions involving EXPORTING

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- ExportAdvisor from Phase 7 (backend/export_advisor.py) — already wired into coordinator
- BatteryRole enum in backend/controller_model.py
- Coordinator._assign_discharge_roles() — role assignment logic
- SystemConfig with feed_in_rate_eur_kwh and feed_in_allowed flags

### Established Patterns
- Role assignment in _assign_discharge_roles() based on SoC comparison
- P_target computation in coordinator considers roles
- Fire-and-forget advisory pattern (ExportAdvisor already follows this)
- Decision logging via DecisionEntry with trigger="export_change"

### Integration Points
- BatteryRole enum — add EXPORTING value
- Coordinator._assign_discharge_roles() — integrate export role assignment
- Coordinator._run_cycle() — P_target offset for non-exporting system
- SystemConfig — add winter_months and winter_min_soc_boost_pct fields

</code_context>

<specifics>
## Specific Ideas

- Winter is critical: having too little in battery is worse than exporting surplus
- Summer: full battery is fine, natural PV export acceptable
- Only one system exports at a time to prevent oscillation
- Export is advisory: ExportAdvisor recommends, coordinator decides whether to act

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>
