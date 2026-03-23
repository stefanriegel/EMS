# Phase 18: Anomaly Detection - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

The system detects unusual consumption patterns, communication failures, and battery behavior drift — alerting the user without generating false-positive fatigue. Covers three detection domains: consumption anomalies (spikes/drops vs time-of-day baselines), communication loss patterns (recurring driver timeouts), and battery health drift (SoC curve deviations, round-trip efficiency degradation). Nightly IsolationForest training with lightweight per-cycle threshold checks.

</domain>

<decisions>
## Implementation Decisions

### Anomaly Detection Architecture
- Store anomaly events in /config/ems_models/anomaly_events.json — consistent with MAPE storage, survives restarts, no DB dependency
- Single `backend/anomaly_detector.py` with AnomalyDetector class managing all 3 detection types
- Pre-computed thresholds stored as simple floats after nightly IsolationForest training — per-cycle checks are comparisons against mean ± N*std, no sklearn calls in 5s loop
- AnomalyDetector called from orchestrator's 5s loop via `check_cycle()` method — receives latest ControllerSnapshot, returns anomaly events if any

### Alert Tiering & Notification
- Three severity tiers: info (logged only), warning (1 occurrence), alert (3+ within 24h) — escalation prevents false-positive fatigue
- Cooldown per anomaly type: 1 hour for warnings, 4 hours for alerts — prevents notification spam
- Telegram notification format: single-line summary with emoji severity indicator (e.g. "⚠️ Consumption spike: 4.2 kWh at 14:00 (baseline: 1.8 kWh)")
- Event retention: last 500 events or 90 days, whichever is smaller

### Battery Health Tracking
- Rolling 7-day baseline of charge/discharge rates per SoC band (0-20%, 20-50%, 50-80%, 80-100%)
- Round-trip efficiency: track energy in (charge kWh) vs energy out (discharge kWh) over 24h windows, flag if below 85%
- Minimum 14 days of data before any battery anomaly alerts
- Battery health metrics exposed via /api/ml/status response as a `battery_health` section

### Claude's Discretion
- IsolationForest hyperparameters (contamination, n_estimators)
- Exact threshold multipliers for anomaly detection (e.g., 2.5σ vs 3σ)
- Internal data structures for tracking event history and cooldowns
- Specific Modbus registers used for battery charge/discharge kWh tracking

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/huawei_controller.py` / `backend/victron_controller.py` — have `_consecutive_failures` counter, `ControllerSnapshot` includes availability data
- `backend/model_store.py` — ModelStore for persisting IsolationForest models
- `backend/notifier.py` — TelegramNotifier for sending alert notifications
- `backend/orchestrator.py` — 5s control loop where check_cycle() will be called
- `backend/api.py` — existing /api/ml/status endpoint to extend with battery_health

### Established Patterns
- `anyio.to_thread.run_sync()` for non-blocking sklearn training (Phase 16)
- Fire-and-forget for optional integrations (Telegram, InfluxDB)
- Dataclass config with `from_env()` classmethods
- JSON file persistence in /config/ems_models/ (ModelStore, MAPE history)

### Integration Points
- `backend/orchestrator.py` — inject AnomalyDetector, call check_cycle() in control loop
- `backend/main.py` — construct AnomalyDetector in lifespan
- `backend/api.py` — extend /api/ml/status with anomaly events and battery health
- `backend/config.py` — AnomalyDetectorConfig dataclass

</code_context>

<specifics>
## Specific Ideas

- Controllers already track `_consecutive_failures` — AnomalyDetector can observe these patterns over time
- `ControllerSnapshot` from `controller_model.py` provides all data needed for per-cycle checks
- Orchestrator's `_run_cycle()` is the natural injection point for check_cycle()

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>
