# Phase 5: Dashboard - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning
**Mode:** Auto (Claude recommended defaults)

<domain>
## Phase Boundary

Rewrite the React dashboard to show per-system battery state (SoC, power, role, health), a decision log with human-readable reasoning, per-system power flow visualization, and tariff schedule with per-battery charge targets. The dashboard consumes existing API endpoints built in Phase 4.

Requirements: UI-01, UI-02, UI-03, UI-04, UI-05

</domain>

<decisions>
## Implementation Decisions

### Per-system battery display (UI-01, UI-04)
- **D-01:** Replace the single PoolOverview card with a dual-battery layout: two side-by-side battery cards (Huawei left, Victron right), each showing SoC bar, power, role badge, and availability dot. Combined pool SoC remains as a header summary above the two cards.
- **D-02:** Role badges use the same color scheme as existing control_state badges: PRIMARY_DISCHARGE=amber, SECONDARY_DISCHARGE=amber-lighter, CHARGING=green, HOLDING=blue, GRID_CHARGE=cyan. Role text is the human-readable short form (e.g., "Primary", "Secondary", "Charging", "Holding", "Grid Charge").
- **D-03:** Pool status indicator (NORMAL/DEGRADED/OFFLINE) shown in the header next to combined SoC. NORMAL=green dot, DEGRADED=amber dot, OFFLINE=red dot.
- **D-04:** DeviceDetail card retained but restructured: Huawei and Victron sections each show role, setpoint, and measured power prominently at top, with hardware details (per-pack SoC, per-phase voltage) collapsed by default.

### Decision log view (UI-02)
- **D-05:** New DecisionLog card showing the last 20 decisions in a compact vertical timeline. Each entry shows: timestamp (relative, e.g., "2m ago"), trigger badge (role_change/allocation_shift/failover/hold), and one-line reasoning text.
- **D-06:** Expandable detail: tap a decision entry to reveal full fields (huawei_role, victron_role, p_target_w, huawei_allocation_w, victron_allocation_w).
- **D-07:** Fetch from `GET /api/decisions?limit=20` on mount and every 30s. Not WebSocket-driven (REST polling is sufficient for a log that only changes on events, not every 5s cycle).
- **D-08:** Empty state: "No dispatch decisions yet" with subtle icon.

### Energy flow visualization (UI-03)
- **D-09:** Evolve the existing EnergyFlowCard SVG to show two battery nodes instead of one. Layout: PV at top, Huawei battery bottom-left, Victron battery bottom-right, Home center-right, Grid at bottom-center.
- **D-10:** Each battery node shows its own SoC arc and power label. Flow paths animate independently per system (PV->Huawei, PV->Victron, Huawei->Home, Victron->Home, Grid->Huawei, Grid->Victron).
- **D-11:** Direction indicators: animated dash-offset on active paths (existing pattern). Color-coded per source: PV paths green, battery paths amber, grid paths blue.
- **D-12:** When a battery is offline (available=false), its node renders greyed out with a small offline indicator.

### Tariff schedule with per-battery targets (UI-05)
- **D-13:** Extend the existing OptimizationCard (not replace it). Add a visual timeline bar showing the next 24h with charge windows highlighted per battery. Huawei slots in one color band, Victron slots in another.
- **D-14:** Each slot on the timeline shows: time window, target SoC %, charge power. Existing reasoning text and cost estimate remain at top.
- **D-15:** Tariff rate overlay on the timeline: background shading indicates cheap (green-tint) vs expensive (neutral) windows so charge slots visually align with tariff dips.

### Dashboard layout and responsiveness
- **D-16:** Keep CSS-only styling (no Tailwind, no CSS-in-JS). Extend existing `index.css` with new card styles following established `.card` pattern.
- **D-17:** Dashboard grid: 2-column on desktop (>768px), 1-column on mobile. Card order: Energy Flow, Battery Status (dual), Decision Log, Tariff+Schedule, Loads, EVCC, Device Detail.
- **D-18:** Dark theme default (existing). CSS custom properties for all colors — the current `--color-pv`, `--color-battery`, `--color-home`, `--color-grid` pattern extended with `--color-huawei`, `--color-victron` for per-system distinction.

### Data flow
- **D-19:** Existing WebSocket hook (`useEmsSocket`) remains the primary data source for pool, devices, tariff, evcc, loads. The `/api/devices` response now includes `role` and `setpoint_w` per device (added in Phase 4) — consume these directly.
- **D-20:** Decision log uses a separate `useDecisions()` hook with REST polling (not WebSocket). This keeps the WS payload lean and the decision log independent.
- **D-21:** No new backend changes needed. All data is available from existing Phase 4 endpoints.

### Claude's Discretion
- SVG node positioning and spacing for the 5-node energy flow diagram
- Exact animation timing and easing for flow paths
- Decision timeline visual treatment (borders, spacing, typography)
- Tariff timeline bar dimensions and time axis formatting
- Mobile breakpoint card reordering details
- SoC bar color gradient (single color vs gradient based on level)
- Loading skeleton design while WS connects

</decisions>

<specifics>
## Specific Ideas

- The dual-battery cards should feel like a natural evolution of the existing PoolOverview — same visual language, just expanded to show both systems independently.
- Decision log should be scannable at a glance — the user wants to see "why is my Victron holding?" and find the answer in 2 seconds, not dig through verbose logs.
- Energy flow diagram is the hero card — it should be visually prominent and immediately convey "where is power going right now?" for both systems.
- The timeline bar for optimization slots is inspired by calendar day views — horizontal time axis, colored blocks for events.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Frontend (primary modification targets)
- `frontend/src/App.tsx` — Root component, DashboardLayout, card grid composition. Restructure grid for new card layout.
- `frontend/src/components/PoolOverview.tsx` — Replace with dual-battery BatteryStatus component.
- `frontend/src/components/EnergyFlowCard.tsx` — Evolve SVG from 4-node to 5-node (split battery into Huawei+Victron).
- `frontend/src/components/DeviceDetail.tsx` — Restructure with role/setpoint prominence and collapsible hardware details.
- `frontend/src/components/OptimizationCard.tsx` — Extend with per-battery timeline bar.
- `frontend/src/components/TariffCard.tsx` — May merge into OptimizationCard or remain as current rate display.
- `frontend/src/types.ts` — Add DecisionEntry type, update DevicesPayload with role/setpoint fields, add PoolStatus type.
- `frontend/src/hooks/useEmsSocket.ts` — Existing WS hook, no changes needed.
- `frontend/src/hooks/useEmsState.ts` — Existing polling fallback, no changes needed.
- `frontend/src/index.css` — Extend with new card styles, dual-battery layout, decision log, timeline bar.

### Backend API (read-only context — no changes)
- `backend/api.py` — `/api/state`, `/api/devices` (with role/setpoint), `/api/decisions`, `/api/health`. All endpoints already built in Phase 4.
- `backend/controller_model.py` — `BatteryRole`, `PoolStatus`, `ControllerSnapshot` enums/dataclasses. Source of truth for role values the frontend renders.
- `backend/ws_manager.py` — WebSocket payload shape. `WsPayload` includes pool, devices, tariff, optimization, evcc, loads.

### Phase 4 context (decisions affecting this phase)
- `.planning/phases/04-integration-monitoring/04-CONTEXT.md` — D-04 through D-06 define the API response shapes this dashboard consumes. D-10 through D-13 define decision log structure.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`useEmsSocket` hook** — WebSocket with exponential backoff reconnect. No changes needed; all new data comes through existing WS payload or separate REST calls.
- **`useEmsState` hook** — Polling fallback for `/api/state` and `/api/devices`. Already handles the device snapshot with role data.
- **Card component pattern** — All existing cards follow `<section className="card {name}-card">` with `card-title`, `card-subtitle`, metric rows. New cards should match.
- **`stateColors` mapping** — PoolOverview has color mapping for control states. Extend for BatteryRole values.
- **`formatPower()` helper** — EnergyFlowCard has a reusable power formatter (W/kW). Extract to shared utility.
- **SVG flow path pattern** — CSS `flow-path` / `flow-path--active` classes with stroke-dashoffset animation. Reuse for additional paths in 5-node diagram.
- **CSS custom properties** — `--color-pv`, `--color-battery`, `--color-home`, `--color-grid`, `--text-muted`. Established pattern for theming.

### Established Patterns
- **Null-safe rendering** — Every component handles `null` props gracefully with "N/A" or "—" fallback. New components must follow.
- **WebSocket-first, polling-fallback** — App.tsx `DashboardLayout` uses WS data when available, falls back to REST polling. Decision log breaks this pattern intentionally (REST-only) since it's event-driven not real-time.
- **No external UI libraries** — Pure React + vanilla CSS. No component library, no Tailwind. Keep it this way.
- **Availability dots** — Green/red dots with title tooltip for online/offline status. Reuse in battery cards.

### Integration Points
- **WsPayload.devices** — Already contains role and setpoint per device (Phase 4 addition). Frontend types.ts needs updating to include these fields.
- **`GET /api/decisions?limit=N`** — Returns `Array<{ timestamp, trigger, huawei_role, victron_role, p_target_w, huawei_allocation_w, victron_allocation_w, reasoning }>`. New hook consumes this.
- **`GET /api/health`** — Returns integration status map. Could feed a small health indicator in footer or header.

</code_context>

<deferred>
## Deferred Ideas

- **WebSocket-pushed decision events** — Real-time decision log updates via WS instead of REST polling. Enhancement after basic dashboard works.
- **Historical charts** — SoC/power time-series charts using InfluxDB data. Requires new API endpoints with time-range queries. Separate phase or v2 feature.
- **Dark/light theme toggle** — Currently dark-only. User preference toggle is a polish item, not core dashboard functionality.
- **Responsive HA panel embedding** — Embedding the dashboard as an HA iframe panel with proper sizing. Part of Phase 6 (Deployment).
- **Per-phase power visualization** — Showing L1/L2/L3 Victron power in the energy flow diagram. Data is available but adds complexity to the SVG layout. Future enhancement.

</deferred>

---

*Phase: 05-dashboard*
*Context gathered: 2026-03-22*
