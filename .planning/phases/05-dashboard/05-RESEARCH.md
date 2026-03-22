# Phase 5: Dashboard - Research

**Researched:** 2026-03-22
**Domain:** React frontend -- dual-battery dashboard, SVG energy flow, decision log, tariff timeline
**Confidence:** HIGH

## Summary

This phase rewrites the existing React dashboard to visualize two independent battery systems with per-system state, a decision audit trail, an expanded energy flow diagram, and a tariff timeline with per-battery charge targets. All backend endpoints are already built (Phase 4) -- this is purely a frontend phase.

The existing codebase uses React 19.2 + TypeScript 5.9 + Vite 8 + wouter with vanilla CSS (no Tailwind, no component library). All current components follow a `.card` pattern with CSS custom properties for theming. The WebSocket hook (`useEmsSocket`) provides real-time data; a polling fallback (`useEmsState`) activates when WS is unavailable. Playwright is used for E2E testing with a preview-mode approach (no backend required for visual tests).

**Primary recommendation:** Evolve existing components in-place following the established patterns. Add one new hook (`useDecisions`) and three new/refactored components (`BatteryStatus`, `DecisionLog`, timeline extension to `OptimizationCard`). The SVG energy flow diagram needs the most work (4-node to 5-node layout with independent paths per battery).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Replace the single PoolOverview card with a dual-battery layout: two side-by-side battery cards (Huawei left, Victron right), each showing SoC bar, power, role badge, and availability dot. Combined pool SoC remains as a header summary above the two cards.
- **D-02:** Role badges use the same color scheme as existing control_state badges: PRIMARY_DISCHARGE=amber, SECONDARY_DISCHARGE=amber-lighter, CHARGING=green, HOLDING=blue, GRID_CHARGE=cyan. Role text is the human-readable short form (e.g., "Primary", "Secondary", "Charging", "Holding", "Grid Charge").
- **D-03:** Pool status indicator (NORMAL/DEGRADED/OFFLINE) shown in the header next to combined SoC. NORMAL=green dot, DEGRADED=amber dot, OFFLINE=red dot.
- **D-04:** DeviceDetail card retained but restructured: Huawei and Victron sections each show role, setpoint, and measured power prominently at top, with hardware details (per-pack SoC, per-phase voltage) collapsed by default.
- **D-05:** New DecisionLog card showing the last 20 decisions in a compact vertical timeline. Each entry shows: timestamp (relative, e.g., "2m ago"), trigger badge (role_change/allocation_shift/failover/hold), and one-line reasoning text.
- **D-06:** Expandable detail: tap a decision entry to reveal full fields (huawei_role, victron_role, p_target_w, huawei_allocation_w, victron_allocation_w).
- **D-07:** Fetch from `GET /api/decisions?limit=20` on mount and every 30s. Not WebSocket-driven (REST polling is sufficient for a log that only changes on events, not every 5s cycle).
- **D-08:** Empty state: "No dispatch decisions yet" with subtle icon.
- **D-09:** Evolve the existing EnergyFlowCard SVG to show two battery nodes instead of one. Layout: PV at top, Huawei battery bottom-left, Victron battery bottom-right, Home center-right, Grid at bottom-center.
- **D-10:** Each battery node shows its own SoC arc and power label. Flow paths animate independently per system (PV->Huawei, PV->Victron, Huawei->Home, Victron->Home, Grid->Huawei, Grid->Victron).
- **D-11:** Direction indicators: animated dash-offset on active paths (existing pattern). Color-coded per source: PV paths green, battery paths amber, grid paths blue.
- **D-12:** When a battery is offline (available=false), its node renders greyed out with a small offline indicator.
- **D-13:** Extend the existing OptimizationCard (not replace it). Add a visual timeline bar showing the next 24h with charge windows highlighted per battery. Huawei slots in one color band, Victron slots in another.
- **D-14:** Each slot on the timeline shows: time window, target SoC %, charge power. Existing reasoning text and cost estimate remain at top.
- **D-15:** Tariff rate overlay on the timeline: background shading indicates cheap (green-tint) vs expensive (neutral) windows so charge slots visually align with tariff dips.
- **D-16:** Keep CSS-only styling (no Tailwind, no CSS-in-JS). Extend existing `index.css` with new card styles following established `.card` pattern.
- **D-17:** Dashboard grid: 2-column on desktop (>768px), 1-column on mobile. Card order: Energy Flow, Battery Status (dual), Decision Log, Tariff+Schedule, Loads, EVCC, Device Detail.
- **D-18:** Dark theme default (existing). CSS custom properties for all colors -- the current `--color-pv`, `--color-battery`, `--color-home`, `--color-grid` pattern extended with `--color-huawei`, `--color-victron` for per-system distinction.
- **D-19:** Existing WebSocket hook (`useEmsSocket`) remains the primary data source for pool, devices, tariff, evcc, loads. The `/api/devices` response now includes `role` and `setpoint_w` per device (added in Phase 4) -- consume these directly.
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

### Deferred Ideas (OUT OF SCOPE)
- WebSocket-pushed decision events
- Historical charts (InfluxDB time-series)
- Dark/light theme toggle
- Responsive HA panel embedding
- Per-phase power visualization in energy flow
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| UI-01 | Reworked dashboard showing per-system state (SoC, power, role, health) | D-01 through D-04: dual battery cards replace PoolOverview. Data from WS pool payload (huawei_role, victron_role, pool_status fields on CoordinatorState) and devices payload. |
| UI-02 | Decision log view: last N coordinator decisions with reasoning | D-05 through D-08: new DecisionLog component + useDecisions hook. Backend `GET /api/decisions?limit=20` returns DecisionEntry array. |
| UI-03 | Per-system power flow visualization | D-09 through D-12: evolve EnergyFlowCard SVG from 4-node to 5-node layout. Per-battery SoC arcs and independent flow paths. |
| UI-04 | Role indicator per battery (PRIMARY/SECONDARY/HOLDING/CHARGING) | D-02: role badges with color mapping. BatteryRole enum values: PRIMARY_DISCHARGE, SECONDARY_DISCHARGE, CHARGING, HOLDING, GRID_CHARGE. |
| UI-05 | Tariff schedule with per-battery charge targets | D-13 through D-15: extend OptimizationCard with 24h timeline bar. ChargeSlotPayload already has `battery` field for per-system distinction. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| react | 19.2.4 | UI framework | Already in project, no change needed |
| react-dom | 19.2.4 | DOM rendering | Already in project |
| typescript | 5.9.3 | Type safety | Already in project |
| vite | 8.0.1 | Build/dev server | Already in project |
| wouter | 3.9.0 | Client-side routing | Already in project, no new routes needed |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| @playwright/test | 1.58.2 | E2E testing | Visual regression tests for new components |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Vanilla CSS | Tailwind | Explicitly rejected in D-16. Project uses CSS custom properties + vanilla CSS throughout |
| D3.js for SVG | Hand-crafted SVG | D3 adds 30KB+ for something achievable with static SVG + CSS animations. Existing EnergyFlowCard is hand-crafted SVG and works well |
| Chart.js for timeline | CSS-only timeline | Timeline bar is a simple horizontal bar with positioned blocks -- no charting library needed |

**Installation:**
```bash
# No new dependencies needed. All libraries already installed.
```

## Architecture Patterns

### Component Mapping (New/Modified)
```
frontend/src/
  components/
    BatteryStatus.tsx     # NEW - replaces PoolOverview.tsx (D-01 through D-03)
    DecisionLog.tsx       # NEW - decision audit trail (D-05 through D-08)
    EnergyFlowCard.tsx    # MODIFIED - 4-node -> 5-node SVG (D-09 through D-12)
    DeviceDetail.tsx      # MODIFIED - role/setpoint prominence + collapse (D-04)
    OptimizationCard.tsx  # MODIFIED - add timeline bar (D-13 through D-15)
    TariffCard.tsx        # UNCHANGED
    EvccCard.tsx          # UNCHANGED
    LoadsCard.tsx         # UNCHANGED
  hooks/
    useEmsSocket.ts       # UNCHANGED
    useEmsState.ts        # UNCHANGED
    useDecisions.ts       # NEW - REST polling for /api/decisions (D-07, D-20)
  types.ts                # MODIFIED - add DecisionEntry, update DevicesPayload
  index.css               # MODIFIED - new card styles, CSS custom properties
  App.tsx                 # MODIFIED - grid order, replace PoolOverview with BatteryStatus
```

### Pattern 1: Data Source Mapping
**What:** Map backend data to frontend components through existing hooks.
**When to use:** All new components.

Key data flow findings from code analysis:

1. **WS payload `pool` object** already includes `huawei_role`, `victron_role`, `pool_status` (from `CoordinatorState` dataclass). These are available via `ws.data.pool`.
2. **WS payload `devices` object** does NOT include `role`/`setpoint_w` -- those are only added by the REST `/api/devices` endpoint. The WS builds `devices` from `get_device_snapshot()` directly.
3. **Implication:** For role badges in BatteryStatus, use `pool.huawei_role` and `pool.victron_role` from the WS pool payload, NOT from devices. For setpoint display in DeviceDetail, either (a) use the pool's `huawei_discharge_setpoint_w`/`victron_discharge_setpoint_w`, or (b) accept that WS devices lack setpoint data and use pool fields instead.

```typescript
// BatteryStatus consumes roles from pool, availability from pool, SoC from pool
// Power from devices (huawei.total_power_w, victron.battery_power_w)
interface BatteryStatusProps {
  pool: PoolState | null;
  devices: DevicesPayload | null;
  connected: boolean;
}
```

### Pattern 2: REST Polling Hook
**What:** Independent polling hook for the decision log endpoint.
**When to use:** Decision log only (D-07, D-20).

```typescript
// useDecisions.ts - follows useEmsState pattern
export function useDecisions(limit = 20, intervalMs = 30_000) {
  const [decisions, setDecisions] = useState<DecisionEntry[]>([]);
  // AbortController cleanup pattern from useEmsState
  // fetch(`/api/decisions?limit=${limit}`) on mount + setInterval
}
```

### Pattern 3: SVG Energy Flow (5-Node)
**What:** Expand the SVG viewBox to accommodate two battery nodes.
**When to use:** EnergyFlowCard refactor.

Current layout (4 nodes): PV(200,60), Battery(80,200), Home(320,200), Grid(200,300)

Recommended 5-node layout (Claude's discretion area):
```
              PV (200, 55)
             /            \
    Huawei (80, 190)    Home (320, 160)
             \            /
    Victron (80, 310)  Grid (280, 310)
```

Alternative symmetric layout:
```
              PV (200, 50)
           /      |      \
  Huawei        Home      Victron
  (60,200)   (200,200)   (340,200)
           \      |      /
             Grid (200, 330)
```

The CONTEXT specifies: "PV at top, Huawei battery bottom-left, Victron battery bottom-right, Home center-right, Grid at bottom-center" (D-09). This maps to:
```
  PV (200, 55)
    |         \
  Huawei       Home
  (80, 200)    (320, 170)
    |              |
  Victron      Grid
  (80, 320)    (280, 320)
```

But two batteries stacked vertically on the left is awkward for flow paths. A more readable layout honoring the spirit of D-09:
```
         PV (200, 55)
        /     |      \
  Huawei    Home    Victron
  (70,210) (200,200) (330,210)
        \     |      /
         Grid (200, 340)
```

This places both batteries flanking Home with Grid below. Flow paths are cleaner. The planner should pick a layout and commit to specific coordinates.

### Pattern 4: Collapsible Section (DeviceDetail)
**What:** HTML `<details>/<summary>` for collapsible hardware details.
**When to use:** DeviceDetail restructure (D-04).

```typescript
<details className="device-collapse">
  <summary className="device-collapse-summary">Hardware Details</summary>
  <div className="device-collapse-content">
    {/* per-pack SoC, per-phase voltage rows */}
  </div>
</details>
```

No JavaScript needed -- native HTML5 disclosure widget with CSS styling.

### Pattern 5: Timeline Bar (OptimizationCard)
**What:** Horizontal 24h timeline with colored slot blocks and tariff shading.
**When to use:** OptimizationCard extension (D-13 through D-15).

The timeline is a positioned `<div>` container representing 24 hours. Each charge slot is an absolutely-positioned colored block. Tariff rate overlay uses background gradients or individual hour-blocks.

```typescript
// Each slot's left/width calculated from time offsets
const slotLeft = ((slotStartHour - timelineStartHour) / 24) * 100;
const slotWidth = (durationHours / 24) * 100;
```

Per-battery distinction: Huawei slots use `--color-huawei`, Victron slots use `--color-victron`. Slots can stack vertically within the timeline if they overlap.

### Anti-Patterns to Avoid
- **Fetching roles from devices in WS mode:** The WS `devices` payload does NOT contain `role`/`setpoint_w`. Always read roles from `pool.huawei_role` / `pool.victron_role`.
- **Adding D3 or chart libraries:** The project explicitly avoids external UI dependencies. Use CSS + SVG + plain HTML.
- **Coupling decision log to WebSocket:** D-20 explicitly separates this. Decision log polling at 30s is independent of the 5s WS cycle.
- **Breaking the null-safe rendering pattern:** Every existing component handles `null` props with "N/A" or fallback. New components must do the same.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Relative timestamps ("2m ago") | Custom date math | Simple helper function with `Date.now() - Date.parse(iso)` | Only 5 cases (s/m/h/d/older). No library needed but do extract as utility |
| Collapsible sections | Custom state + toggle | HTML `<details>/<summary>` | Native, accessible, zero JS, styleable with CSS |
| SVG flow animations | JS requestAnimationFrame | CSS `@keyframes` with `stroke-dashoffset` | Existing pattern works perfectly, no JS animation loop |
| Color theming | Inline style objects | CSS custom properties | Established pattern with `--color-*` tokens |

## Common Pitfalls

### Pitfall 1: WS Devices Payload Missing Roles
**What goes wrong:** Developer reads `devices.huawei.role` from the WS payload and gets `undefined`.
**Why it happens:** The WS endpoint calls `get_device_snapshot()` directly, which does NOT merge role/setpoint. Only the REST `/api/devices` endpoint merges them via `getattr(state, "huawei_role", ...)`.
**How to avoid:** Always read roles from `pool.huawei_role` / `pool.victron_role`. Read per-device power from `devices.huawei.total_power_w` / `devices.victron.battery_power_w`.
**Warning signs:** Role badges showing "undefined" or missing entirely.

### Pitfall 2: PoolState Type Missing New Fields
**What goes wrong:** TypeScript types don't include `huawei_role`, `victron_role`, `pool_status` because `types.ts` mirrors the old `UnifiedPoolState` not the new `CoordinatorState`.
**Why it happens:** Phase 4 added these backend fields but the frontend types were not updated (frontend changes were out of Phase 4 scope).
**How to avoid:** Update `PoolState` interface in `types.ts` to include `huawei_role: string`, `victron_role: string`, `pool_status: string`, `huawei_effective_min_soc_pct: number`, `victron_effective_min_soc_pct: number`.
**Warning signs:** TypeScript compile errors when accessing `pool.huawei_role`.

### Pitfall 3: SVG ViewBox Overflow
**What goes wrong:** Adding two battery nodes causes labels/arcs to clip at SVG edges.
**Why it happens:** Current viewBox is `0 0 400 360` which is tight for 4 nodes. 5 nodes need more space.
**How to avoid:** Increase viewBox height (e.g., `0 0 400 400`) or redistribute node positions within the existing viewBox. Test at mobile viewport (375px wide).
**Warning signs:** Labels cut off, arcs clipping at SVG boundaries.

### Pitfall 4: Timeline Timezone Confusion
**What goes wrong:** Charge slot times shown in UTC instead of local time.
**Why it happens:** `ChargeSlotPayload.start_utc` / `end_utc` are ISO 8601 UTC strings. The existing `localTime()` helper in OptimizationCard already handles this correctly.
**How to avoid:** Reuse the existing `localTime()` helper for timeline positioning. Calculate hour offsets in local time, not UTC.
**Warning signs:** Slots appearing at wrong positions on the timeline (shifted by timezone offset).

### Pitfall 5: Decision Log Empty During Development
**What goes wrong:** DecisionLog always shows empty state because there are no decisions in the backend.
**Why it happens:** Decisions are only recorded on role changes, allocation shifts >300W, or EVCC hold events. In a test/dev environment these may not occur.
**How to avoid:** Build with mock data first, verify empty state rendering separately. The Playwright tests run against preview mode (no backend) so the empty state is what gets tested.
**Warning signs:** Unable to visually verify the populated decision log during development.

### Pitfall 6: Grid Order Mismatch on Mobile
**What goes wrong:** Cards appear in wrong order on mobile because CSS grid auto-flow doesn't respect semantic ordering.
**Why it happens:** Current grid uses `auto-fill, minmax(280px, 1fr)` which doesn't guarantee card order. D-17 specifies explicit ordering.
**How to avoid:** Use `grid-template-columns: 1fr 1fr` on desktop with explicit `grid-column` spans for full-width cards (EnergyFlow). On mobile (1fr), order follows DOM order which follows the JSX render order.
**Warning signs:** Energy flow card not spanning full width, or cards appearing in unexpected order on resize.

## Code Examples

### Type Updates for types.ts

```typescript
// Add to PoolState interface (fields from CoordinatorState)
export interface PoolState {
  // ... existing fields ...
  huawei_role: string;          // BatteryRole enum value
  victron_role: string;         // BatteryRole enum value
  pool_status: string;          // "NORMAL" | "DEGRADED" | "OFFLINE"
  huawei_effective_min_soc_pct: number;
  victron_effective_min_soc_pct: number;
}

// New type for decision log entries
export interface DecisionEntry {
  timestamp: string;           // ISO 8601 UTC
  trigger: string;             // role_change | allocation_shift | failover | hold_signal | slot_start | slot_end
  huawei_role: string;
  victron_role: string;
  p_target_w: number;
  huawei_allocation_w: number;
  victron_allocation_w: number;
  pool_status: string;
  reasoning: string;
}

// Update DevicesPayload for REST fallback (devices via /api/devices include role)
export interface DevicesPayload {
  huawei: HuaweiSnapshot & { role?: string; setpoint_w?: number };
  victron: VictronSnapshot & { role?: string; setpoint_w?: number };
  pool_status?: string;  // only in REST response, not WS
}
```

### Role Color Mapping

```typescript
// Source: D-02 from CONTEXT.md
const roleColors: Record<string, string> = {
  PRIMARY_DISCHARGE: "#f59e0b",     // amber
  SECONDARY_DISCHARGE: "#fbbf24",   // amber-lighter
  CHARGING: "#22c55e",              // green
  HOLDING: "#3b82f6",               // blue
  GRID_CHARGE: "#06b6d4",           // cyan
};

const roleLabels: Record<string, string> = {
  PRIMARY_DISCHARGE: "Primary",
  SECONDARY_DISCHARGE: "Secondary",
  CHARGING: "Charging",
  HOLDING: "Holding",
  GRID_CHARGE: "Grid Charge",
};
```

### CSS Custom Properties to Add

```css
/* Per-system colors (D-18) */
:root {
  --color-huawei: #f59e0b;   /* amber -- matches existing battery color */
  --color-victron: #8b5cf6;  /* purple -- distinct from huawei */
}
```

### Relative Time Helper

```typescript
function relativeTime(isoTimestamp: string): string {
  const diff = Date.now() - new Date(isoTimestamp).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single battery pool view | Per-system dual-battery view | This phase | Shows independent battery state, roles, health |
| 4-node energy flow (single battery) | 5-node energy flow (Huawei + Victron) | This phase | Per-system power flow visualization |
| No decision transparency | Decision audit trail | This phase (Phase 4 built backend) | Users understand WHY dispatch decisions were made |
| Text-only charge schedule | Timeline bar with tariff overlay | This phase | Visual alignment of charge windows with cheap tariff periods |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | Playwright 1.58.2 |
| Config file | `frontend/playwright.config.ts` |
| Quick run command | `cd frontend && npx playwright test --grep "battery-status"` |
| Full suite command | `cd frontend && npx playwright test` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| UI-01 | Dual battery cards visible with SoC, power, role, health | E2E (visual) | `cd frontend && npx playwright test tests/battery-status.spec.ts` | Wave 0 |
| UI-02 | Decision log renders empty state (no backend) | E2E (visual) | `cd frontend && npx playwright test tests/decision-log.spec.ts` | Wave 0 |
| UI-03 | Energy flow SVG has 5 nodes (2 batteries) | E2E (visual) | `cd frontend && npx playwright test tests/energy-flow.spec.ts` | Exists (needs update) |
| UI-04 | Role badges render for each battery | E2E (visual) | `cd frontend && npx playwright test tests/battery-status.spec.ts` | Wave 0 |
| UI-05 | Optimization card includes timeline bar | E2E (visual) | `cd frontend && npx playwright test tests/optimization-card.spec.ts` | Exists (needs update) |

### Sampling Rate
- **Per task commit:** `cd frontend && npx playwright test --grep "{relevant-card}" -x`
- **Per wave merge:** `cd frontend && npx playwright test`
- **Phase gate:** Full suite green + `cd frontend && npm run build` (tsc + vite)

### Wave 0 Gaps
- [ ] `frontend/tests/battery-status.spec.ts` -- covers UI-01, UI-04
- [ ] `frontend/tests/decision-log.spec.ts` -- covers UI-02
- [ ] Update `frontend/tests/energy-flow.spec.ts` -- verify 5-node SVG instead of 4-node
- [ ] Update `frontend/tests/optimization-card.spec.ts` -- verify timeline bar presence

## Open Questions

1. **SVG 5-node layout coordinates**
   - What we know: D-09 specifies "PV at top, Huawei bottom-left, Victron bottom-right, Home center-right, Grid bottom-center"
   - What's unclear: The described arrangement puts both batteries on opposite bottom corners with Home center-right and Grid bottom-center. This is somewhat unusual for energy flow diagrams. The exact coordinates and path curves need experimentation.
   - Recommendation: Start with a symmetric layout (batteries flanking Home, Grid below) that better serves the flow path clarity, then adjust if user feedback differs. The viewBox may need expanding from 400x360 to 400x400+.

2. **Tariff rate data for timeline overlay**
   - What we know: D-15 wants tariff rate shading on the timeline. Current WS payload includes `tariff.effective_rate_eur_kwh` (single current rate).
   - What's unclear: There is no API endpoint returning future tariff rates for the next 24h. The optimization slots have implied timing but no explicit rate-per-hour array.
   - Recommendation: Use the optimization slot positioning as an implicit "cheap window" indicator. Color the charge slot backgrounds with green tint. If a full rate curve is needed, that requires a backend endpoint (deferred). For now, just distinguish "slot = cheap window" visually.

## Sources

### Primary (HIGH confidence)
- Direct code analysis of `frontend/src/` -- all components, hooks, types, CSS
- Direct code analysis of `backend/api.py` -- WS payload construction (lines 850-928), `/api/devices` endpoint, `/api/decisions` endpoint
- Direct code analysis of `backend/controller_model.py` -- `BatteryRole`, `PoolStatus`, `DecisionEntry`, `CoordinatorState` dataclasses
- Direct code analysis of `backend/coordinator.py` -- `get_decisions()`, `get_device_snapshot()`, decision entry recording

### Secondary (MEDIUM confidence)
- `.planning/phases/05-dashboard/05-CONTEXT.md` -- user decisions D-01 through D-21

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new dependencies, everything already in project
- Architecture: HIGH - well-understood React patterns, existing codebase conventions are clear
- Pitfalls: HIGH - identified through direct code analysis (especially WS vs REST data asymmetry)
- SVG layout: MEDIUM - exact coordinates need visual iteration

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable -- no external dependency changes expected)
