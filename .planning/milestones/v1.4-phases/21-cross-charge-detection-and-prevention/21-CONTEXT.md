# Phase 21: Cross-Charge Detection and Prevention - Context

**Gathered:** 2026-03-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Detect and prevent battery-to-battery energy transfer through the AC bus in real time. The CrossChargeDetector is a pure coordinator logic module with no hardware dependencies — it reads ControllerSnapshot power values and modifies ControllerCommand roles before execution. Includes Telegram alerting, InfluxDB waste tracking, and a dashboard indicator.

</domain>

<decisions>
## Implementation Decisions

### Detection Algorithm
- Place cross-charge guard after command computation, before execute() — intercept pattern matching EVCC hold and mode override
- Near-zero grid threshold: abs(grid_power) < 200W, accounts for measurement noise
- CrossChargeDetector dataclass injected into Coordinator via set_cross_charge_detector() — matches existing injection pattern (anomaly_detector, self_tuner, export_advisor)
- Use total grid power (sum of L1+L2+L3) for detection — Victron is 3-phase, cross-charge distributes across phases

### Alerting & Metrics
- Cumulative waste energy: integrate min(abs(charge_power), abs(discharge_power)) * cycle_duration during episodes
- Telegram alert: first detection per episode only, cooldown reset when cross-charge clears for 5+ minutes
- InfluxDB measurement: `ems_cross_charge` with fields: `waste_wh`, `episode_count`, `active` — follows existing ems_huawei/ems_victron naming
- REST API: extend existing /api/health with cross_charge section — no new endpoint

### Dashboard Indicator
- Badge on EnergyFlowCard near battery nodes — visible at a glance in main energy flow view
- Active cross-charge: red warning badge with "Cross-Charge" label + pulsing animation
- Inactive: hidden (clean dashboard when normal)
- Historical: count + total waste kWh in existing OptimizationCard

### Claude's Discretion
- CrossChargeDetector internal data structures and state machine
- Exact CSS styling for the cross-charge badge
- InfluxDB write frequency (per-cycle or per-episode)
- Test fixture design

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `DecisionEntry` (controller_model.py:192) — log cross-charge events with trigger="cross_charge_prevention"
- `CoordinatorState` (controller_model.py:134) — add cross_charge_active field
- `_write_integrations()` in coordinator.py — handles InfluxDB + Telegram per-cycle writes
- `InfluxWriter.write_point()` — existing async InfluxDB client
- `Notifier.send_alert(category, message)` — existing Telegram alerting
- `EnergyFlowCard.tsx` — main energy visualization, add badge here
- `OptimizationCard.tsx` — existing optimization context, add waste stats

### Established Patterns
- Optional injection: `set_xxx()` methods on Coordinator, None guards in _run_cycle
- Fire-and-forget for InfluxDB/Telegram: try/except, WARNING log, never crash
- Per-cycle state in `_run_cycle()`: poll → decide → guard → execute → build_state → write_integrations
- DecisionEntry trigger values: role_change, hold_signal, slot_start, slot_end, failover, allocation_shift
- Frontend: WebSocket state updates via useEmsSocket hook, conditional rendering with null checks

### Integration Points
- `coordinator.py _run_cycle()` — insert guard between command computation and execute()
- `coordinator.py __init__()` — add set_cross_charge_detector() injection
- `controller_model.py` — add cross_charge_active to CoordinatorState, new trigger value
- `influx_writer.py` — new write_cross_charge_point() method
- `api.py /api/health` — extend health response with cross_charge section
- `frontend/src/types.ts` — add cross_charge fields to state types
- `frontend/src/components/EnergyFlowCard.tsx` — add badge
- `frontend/src/components/OptimizationCard.tsx` — add waste stats

</code_context>

<specifics>
## Specific Ideas

- Detection formula from research: `abs(charge_power) > 100W AND abs(discharge_power) > 100W AND abs(grid_power) < 200W` for 2+ consecutive cycles
- Mitigation: force the charging battery to HOLDING — the simpler and safer approach over trying to rebalance
- Episode tracking: start/end timestamps, cumulative waste Wh per episode

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>
