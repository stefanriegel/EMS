# Phase 11: Dashboard & API - Research

**Researched:** 2026-03-23
**Domain:** Frontend React components + FastAPI endpoint extensions
**Confidence:** HIGH

## Summary

Phase 11 adds three visual features to the existing EMS dashboard: an export indicator in the energy flow diagram, a multi-day solar forecast visualization, and a multi-day charge schedule view with per-day breakdown. All three requirements (DSH-01, DSH-02, DSH-03) build on existing infrastructure -- the backend data is already computed and stored in memory (WeatherScheduler.active_day_plans, battery roles with EXPORTING state, SolarForecastMultiDay), it just needs to be exposed via API endpoints and rendered in the frontend.

The frontend follows established patterns: React 19 functional components, CSS custom properties for the dark theme, WebSocket for live data (pool/devices), and REST polling with AbortController for non-critical data (decisions, forecast, schedule). No new libraries are needed. The backend needs two new API endpoints and one WebSocket payload extension.

**Primary recommendation:** Extend the existing API and WebSocket to expose DayPlan and solar forecast data, then build three small frontend additions following the exact same patterns as existing cards.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
None explicitly locked -- all implementation choices are at Claude's discretion.

### Claude's Discretion
All implementation choices are at Claude's discretion. Key constraints:
- Follow existing dashboard patterns (React 19, wouter, dark theme, CSS custom properties)
- Use existing WebSocket/polling patterns for data delivery
- REST endpoints for forecast and schedule data (extend existing /api/optimization/schedule)
- Export indicator in the existing EnergyFlowCard SVG (add export path/arrow when exporting)
- Forecast visualization: simple bar chart or timeline showing daily solar kWh for 3 days
- Schedule view: extend existing OptimizationCard with per-day breakdown (DayPlan containers)
- Follow Phase 5 patterns: native HTML details/summary, REST polling with AbortController

### Deferred Ideas (OUT OF SCOPE)
None
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DSH-01 | Energy flow visualization shows export indicator when PV surplus goes to grid | EnergyFlowCard already has `homeToGridActive` logic and the Home-to-Grid flow path activates when `gridPower < -FLOW_THRESHOLD`. Pool state exposes `huawei_role`/`victron_role` with EXPORTING value. Add visual export label/indicator on the Grid node when export is active. |
| DSH-02 | Multi-day solar forecast visualization showing expected solar production for next 2-3 days | WeatherScheduler stores `active_day_plans` with per-day `solar_forecast_kwh`. Need new REST endpoint `/api/optimization/forecast` to expose this data, plus a new ForecastCard component with simple bar chart. |
| DSH-03 | Charge schedule view shows multi-day plan with per-day breakdown | WeatherScheduler stores `active_day_plans` with full DayPlan data. Extend `/api/optimization/schedule` response to include `day_plans` array, then extend OptimizationCard to show per-day breakdown. |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Stack:** Python 3.12+, FastAPI, React 19, TypeScript 5.9, Vite 8, wouter
- **Naming:** snake_case.py, PascalCase.tsx, camelCase.ts for hooks
- **Testing:** pytest with asyncio_mode="auto", Playwright for E2E
- **CSS:** Dark theme with CSS custom properties, no Tailwind or CSS-in-JS
- **Patterns:** dataclass configs, dependency injection via FastAPI Depends, WebSocket + REST polling fallback
- **No barrel files:** Direct imports only

## Standard Stack

No new dependencies needed. This phase uses only existing project infrastructure.

### Core (already installed)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| React | 19.2.4 | Component framework | Already in use |
| FastAPI | 0.x | REST API framework | Already in use |
| TypeScript | 5.9.3 | Type safety | Already in use |

## Architecture Patterns

### Backend: API Extension Pattern

Two approaches for exposing new data, both already used in the codebase:

**Pattern A: Extend existing endpoint response.** The `/api/optimization/schedule` endpoint returns `_schedule_to_dict(scheduler.active_schedule)`. Extend this to also include `day_plans` when available on the scheduler (WeatherScheduler has `active_day_plans`).

**Pattern B: New endpoint.** Add `/api/optimization/forecast` for solar forecast data. Follow the existing `get_scheduler` dependency pattern.

Both patterns are needed: extend the schedule endpoint for DSH-03, add a new forecast endpoint for DSH-02.

### Backend: Data Flow

```
WeatherScheduler
  ├── active_schedule (ChargeSchedule)     → /api/optimization/schedule (existing)
  ├── active_day_plans (list[DayPlan])      → /api/optimization/schedule (extend)
  └── _last_solar_daily_kwh (list[float])   → /api/optimization/forecast (new)
```

The WeatherScheduler is already wired as `app.state.scheduler` in main.py. All data is accessible through the existing `get_scheduler` dependency.

### Frontend: Card Pattern

Every dashboard card follows the same structure:

```typescript
// Source: Existing codebase patterns (EnergyFlowCard.tsx, OptimizationCard.tsx)
interface Props {
  data: SomePayload | null;
}

export function SomeCard({ data }: Props) {
  return (
    <section className="card some-card">
      <h2 className="card-title">Title</h2>
      {!data ? (
        <p className="unavailable">No data available</p>
      ) : (
        <>{/* render content */}</>
      )}
    </section>
  );
}
```

### Frontend: REST Polling Hook Pattern

For non-WebSocket data (forecast and day plans are not time-critical), follow the existing `useDecisions` pattern:

```typescript
// Source: frontend/src/hooks/useDecisions.ts pattern
function useForecast(intervalMs: number) {
  const [data, setData] = useState<ForecastPayload | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const poll = () => {
      fetch("/api/optimization/forecast", { signal: controller.signal })
        .then(r => r.json())
        .then(setData)
        .catch(() => {});
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => { controller.abort(); clearInterval(id); };
  }, [intervalMs]);
  return data;
}
```

### DSH-01: Export Indicator in EnergyFlowCard

The EnergyFlowCard already detects export via `homeToGridActive` (line 74: `gridPower < -FLOW_THRESHOLD`). The Grid-to-Home path already changes color when exporting (line 151: `homeToGridActive ? "var(--color-home)" : undefined`). What's missing is a visual "Export" label/indicator to make the export state obvious.

**Implementation approach:**
1. Add an "EXPORT" text label near the Grid node when `homeToGridActive` is true
2. Optionally show the export power value (already available as `gridPower`)
3. The flow path animation already shows the correct direction

**Data availability:** `gridPower` comes from `devices.victron.grid_power_w` (negative = exporting). The `pool.huawei_role` and `pool.victron_role` values can include "EXPORTING" for additional semantic clarity, but the gridPower threshold is sufficient and already working.

### DSH-02: Solar Forecast Bar Chart

**Data source:** `WeatherScheduler.active_day_plans[].solar_forecast_kwh` provides per-day solar production in kWh for 3 days. Also `WeatherScheduler._last_solar_daily_kwh` as a direct float list.

**Visualization:** Simple horizontal or vertical bar chart showing 3 bars (Today, Tomorrow, Day After) with kWh values. No charting library needed -- use CSS flexbox bars with width proportional to max value.

**New API endpoint:** `GET /api/optimization/forecast` returns:
```json
{
  "days": [
    { "date": "2026-03-23", "solar_kwh": 12.5, "consumption_kwh": 18.2, "net_kwh": -5.7, "confidence": 1.0 },
    { "date": "2026-03-24", "solar_kwh": 8.3, "consumption_kwh": 17.5, "net_kwh": -9.2, "confidence": 0.8 },
    { "date": "2026-03-25", "solar_kwh": 15.1, "consumption_kwh": 16.8, "net_kwh": -1.7, "confidence": 0.6 }
  ],
  "source": "open_meteo"
}
```

### DSH-03: Multi-Day Charge Schedule

**Data source:** `WeatherScheduler.active_day_plans` contains the full `DayPlan` dataclass list.

**Visualization:** Extend the OptimizationCard to show per-day breakdown below the existing single-night view. Each day shows solar forecast, consumption forecast, net balance, and charge target. Day 0 shows actual charge slots, Days 1-2 show advisory info.

**API change:** Extend the `/api/optimization/schedule` response to include a `day_plans` array alongside the existing `slots`, `reasoning`, `computed_at`, `stale` fields.

### Recommended File Changes

```
Backend:
  backend/api.py              — add /api/optimization/forecast endpoint
                                 extend _schedule_to_dict to include day_plans

Frontend:
  frontend/src/types.ts       — add ForecastPayload, DayPlanPayload types
  frontend/src/components/EnergyFlowCard.tsx — add export indicator label
  frontend/src/components/ForecastCard.tsx   — new: solar forecast bar chart
  frontend/src/components/OptimizationCard.tsx — extend with day plan breakdown
  frontend/src/hooks/useForecast.ts          — new: REST polling for forecast
  frontend/src/App.tsx        — add ForecastCard to dashboard grid
  frontend/src/index.css      — add styles for forecast bars and day plan rows
```

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Bar chart | SVG charting library | CSS flexbox bars with percentage widths | 3 static bars don't justify a dependency; CSS bars match existing dashboard aesthetic |
| Date formatting | Custom date parser | `toLocaleDateString()` with options | Built-in browser API, locale-aware |
| Data polling | Custom WebSocket extension | REST polling with AbortController | Forecast data changes hourly at most; WebSocket overhead not justified; matches existing useDecisions pattern |

## Common Pitfalls

### Pitfall 1: Serializing DayPlan date fields
**What goes wrong:** `dataclasses.asdict()` converts `datetime.date` to a raw object, not an ISO string.
**Why it happens:** Unlike `datetime`, `date` objects don't have `.isoformat()` called automatically by FastAPI's JSON encoder.
**How to avoid:** Manually convert `date` fields to ISO strings in the serialization helper, same as `_schedule_to_dict` does for `computed_at`.
**Warning signs:** Frontend receives `[object Object]` instead of "2026-03-23".

### Pitfall 2: WeatherScheduler vs Scheduler type at runtime
**What goes wrong:** `get_scheduler()` returns `Scheduler | None`, but `active_day_plans` only exists on `WeatherScheduler`.
**Why it happens:** `app.state.scheduler` is actually a `WeatherScheduler` at runtime (since Phase 10), but the type annotation says `Scheduler`.
**How to avoid:** Use `getattr(scheduler, 'active_day_plans', None)` for safe access, or add a type check.
**Warning signs:** `AttributeError` when accessing `active_day_plans` on a plain `Scheduler`.

### Pitfall 3: Empty forecast data on fresh start
**What goes wrong:** `active_day_plans` is `None` until the first nightly schedule run or intra-day replan.
**Why it happens:** WeatherScheduler initializes `active_day_plans = None`.
**How to avoid:** Frontend must handle null/empty forecast gracefully (show "No forecast available").
**Warning signs:** Dashboard shows broken layout or errors before first schedule computation.

### Pitfall 4: Export indicator flicker
**What goes wrong:** Export indicator flickers on/off rapidly with small grid power fluctuations around 0 W.
**Why it happens:** `FLOW_THRESHOLD` is 20 W, and grid power can oscillate near zero.
**How to avoid:** The existing `FLOW_THRESHOLD` constant already handles this. Reuse the same `homeToGridActive` boolean for the export label.
**Warning signs:** Rapid visual toggling of export badge.

## Code Examples

### Backend: New forecast endpoint
```python
# Source: Follows existing /api/optimization/schedule pattern in backend/api.py
@api_router.get("/optimization/forecast")
async def get_optimization_forecast(
    scheduler: Scheduler | None = Depends(get_scheduler),
) -> dict[str, Any]:
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not available")
    day_plans = getattr(scheduler, "active_day_plans", None)
    if day_plans is None:
        raise HTTPException(status_code=503, detail="No forecast available")
    return {
        "days": [
            {
                "date": dp.date.isoformat(),
                "day_index": dp.day_index,
                "solar_kwh": round(dp.solar_forecast_kwh, 1),
                "consumption_kwh": round(dp.consumption_forecast_kwh, 1),
                "net_kwh": round(dp.net_energy_kwh, 1),
                "confidence": dp.confidence,
                "charge_target_kwh": round(dp.charge_target_kwh, 1),
                "advisory": dp.advisory,
            }
            for dp in day_plans
        ],
    }
```

### Frontend: Export indicator in SVG
```typescript
// Source: Extension of existing EnergyFlowCard.tsx pattern
{/* Add near the Grid node, after the grid value text */}
{homeToGridActive && (
  <text
    x={GRID.cx}
    y={GRID.cy + NODE_R + 32}
    className="node-label"
    style={{ fill: "var(--accent-green)", fontSize: 11, fontWeight: 600 }}
  >
    EXPORT
  </text>
)}
```

### Frontend: CSS bar chart for forecast
```css
/* Source: Follows existing card/metric patterns in index.css */
.forecast-bars {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 0.75rem;
}

.forecast-bar-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.forecast-bar-label {
  min-width: 70px;
  font-size: 0.78rem;
  color: var(--text-secondary);
}

.forecast-bar-track {
  flex: 1;
  height: 18px;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 4px;
  overflow: hidden;
}

.forecast-bar-fill {
  height: 100%;
  background: var(--color-pv);
  border-radius: 4px;
  transition: width 0.4s ease;
}

.forecast-bar-value {
  min-width: 55px;
  text-align: right;
  font-family: var(--font-mono);
  font-size: 0.78rem;
  color: var(--text-primary);
}
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 with asyncio_mode="auto" |
| Config file | pyproject.toml |
| Quick run command | `uv run pytest tests/test_api.py -x -q` |
| Full suite command | `uv run pytest tests/ -x -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DSH-01 | Export indicator shows when grid power is negative | unit (frontend) | Manual visual verification | N/A (SVG visual) |
| DSH-02 | /api/optimization/forecast returns day plan solar data | unit | `uv run pytest tests/test_api.py -x -q -k forecast` | Wave 0 |
| DSH-03 | /api/optimization/schedule includes day_plans array | unit | `uv run pytest tests/test_api.py -x -q -k schedule` | Extend existing |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_api.py -x -q`
- **Per wave merge:** `uv run pytest tests/ -x -q`
- **Phase gate:** Full suite green before verification

### Wave 0 Gaps
- [ ] `tests/test_api.py` -- add test for `/api/optimization/forecast` endpoint (new)
- [ ] `tests/test_api.py` -- add test for day_plans in `/api/optimization/schedule` response (extend)

## Sources

### Primary (HIGH confidence)
- Codebase analysis: `backend/api.py`, `backend/weather_scheduler.py`, `backend/schedule_models.py`
- Codebase analysis: `frontend/src/components/EnergyFlowCard.tsx`, `frontend/src/components/OptimizationCard.tsx`
- Codebase analysis: `frontend/src/types.ts`, `frontend/src/App.tsx`, `frontend/src/index.css`

### Secondary (MEDIUM confidence)
- None needed -- all implementation uses existing patterns

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - No new dependencies, all existing
- Architecture: HIGH - Direct extensions of existing patterns with clear data paths
- Pitfalls: HIGH - Based on concrete codebase analysis (serialization, type access, null states)

**Research date:** 2026-03-23
**Valid until:** 2026-04-23 (stable -- no external dependencies changing)
