# Phase 19: Self-Tuning Control - Context

**Gathered:** 2026-03-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Control parameters (dead-bands, ramp rates, min-SoC profiles) automatically adjust based on real usage data — with strict safety gates ensuring tuning only activates when the system has proven forecast accuracy and sufficient historical data. Shadow mode runs for 14+ days before live adjustments. Each parameter domain tunes independently based on its own signal.

</domain>

<decisions>
## Implementation Decisions

### Self-Tuning Architecture
- Single `backend/self_tuner.py` with SelfTuner class managing all 3 tuning domains
- Track ControlState changes in the orchestrator's 5s loop, accumulate per-hour transition counters for oscillation rate
- Persist tuning state in /config/ems_models/tuning_state.json (shadow log, current params, history)
- Nightly tuning computation runs in the existing nightly scheduler loop, after anomaly training

### Safety Gates & Shadow Mode
- SelfTuner has a `mode` field: "shadow" or "live". Shadow logs recommended params vs actuals but never applies. Auto-promotes to live after 14 consecutive days of shadow logging
- 10% per-night bound: each parameter stores current + base value, nightly adjustment capped at abs(new - current) <= 0.10 * base
- Absolute safe minimums use existing coordinator clamp ranges: dead_band 50W, ramp_rate 100W, min_soc 10%
- Automatic revert: if oscillation rate increases >20% after a parameter change, revert to previous value on next nightly run

### Parameter Tuning Signals
- Dead-band tuning driven by oscillation rate (state transitions/hour). High oscillation → increase dead-band, low → decrease
- Ramp rate tuning driven by grid import spikes. Frequent large spikes → increase ramp rate, stable → decrease
- Min-SoC profile tuning driven by consumption patterns. Higher min-SoC before peak consumption hours, using forecaster predictions
- Tuning status exposed via /api/ml/status with a `self_tuning` section (mode, days in shadow, current/recommended params, last adjustment)

### Claude's Discretion
- Specific oscillation rate thresholds for dead-band adjustment direction
- Grid import spike detection algorithm (threshold, window size)
- Min-SoC profile granularity (hourly vs 4-hour blocks)
- Internal data structures for transition tracking and shadow logging

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/coordinator.py` — existing dead-band/ramp-rate/min-SoC parameters with clamp ranges and HA command handlers
- `backend/unified_model.py` — `ControlState` enum for state transition tracking
- `backend/orchestrator.py` — 5s control loop for transition counting
- `backend/consumption_forecaster.py` — MAPE tracking (Phase 17) for safety gate, predict_hourly() for min-SoC profile
- `backend/anomaly_detector.py` — oscillation-related detection patterns (Phase 18)
- `backend/model_store.py` — ModelStore for any ML model persistence

### Established Patterns
- Nightly scheduler loop for periodic computation (forecaster retrain, anomaly training)
- JSON persistence in /config/ems_models/
- Fire-and-forget for optional integrations
- Dataclass config with from_env() classmethods

### Integration Points
- `backend/orchestrator.py` — count ControlState transitions per cycle
- `backend/coordinator.py` — apply tuned parameters via existing setters
- `backend/main.py` — construct SelfTuner in lifespan, wire into nightly scheduler
- `backend/api.py` — extend /api/ml/status with self_tuning section

</code_context>

<specifics>
## Specific Ideas

- Coordinator already has `_huawei_deadband_w`, `_victron_deadband_w`, `_sys_config.ramp_rate_w`, `_sys_config.huawei_min_soc_pct`, `_sys_config.victron_min_soc_pct` — SelfTuner adjusts these
- Coordinator's clamp ranges (deadband_huawei: 50-1000, deadband_victron: 50-500, ramp_rate: 100-2000, min_soc: 10-100) serve as safety floor
- MAPE threshold of 25% for activation gate should read from ConsumptionForecaster's get_ml_status()

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>
