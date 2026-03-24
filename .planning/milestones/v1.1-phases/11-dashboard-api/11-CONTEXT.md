# Phase 11: Dashboard & API - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Users can see export activity, multi-day solar forecasts, and multi-day charge plans in the dashboard. Three new visual elements: export indicator in energy flow, solar forecast visualization, and multi-day charge schedule with per-day breakdown.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion. Key constraints:
- Follow existing dashboard patterns (React 19, wouter, dark theme, CSS custom properties)
- Use existing WebSocket/polling patterns for data delivery
- REST endpoints for forecast and schedule data (extend existing /api/optimization/schedule)
- Export indicator in the existing EnergyFlowCard SVG (add export path/arrow when exporting)
- Forecast visualization: simple bar chart or timeline showing daily solar kWh for 3 days
- Schedule view: extend existing OptimizationCard with per-day breakdown (DayPlan containers)
- Follow Phase 5 patterns: native HTML details/summary, REST polling with AbortController

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- frontend/src/components/EnergyFlowCard.tsx — 5-node SVG energy flow with animated paths
- frontend/src/components/OptimizationCard.tsx — tariff timeline with charge slot visualization
- frontend/src/hooks/useEmsSocket.ts — WebSocket state subscription
- frontend/src/hooks/useEmsState.ts — REST polling fallback
- backend/api.py — /api/optimization/schedule endpoint
- backend/ws_manager.py — WebSocket state broadcasting

### Established Patterns
- Dark theme with CSS custom properties (--bg-primary, --accent-green, etc.)
- REST polling hooks with AbortController for non-critical data
- Responsive card-based layout in App.tsx grid

### Integration Points
- EnergyFlowCard — add export flow path when state shows EXPORTING
- OptimizationCard — extend with multi-day view using DayPlan data
- New ForecastCard or extend existing card with solar forecast bars
- /api/optimization/schedule — extend to include DayPlan multi-day data
- /api/optimization/forecast — new endpoint for solar forecast data

</code_context>

<specifics>
## Specific Ideas

No specific UI requirements beyond ROADMAP success criteria. Follow existing dashboard aesthetic.

</specifics>

<deferred>
## Deferred Ideas

None

</deferred>
