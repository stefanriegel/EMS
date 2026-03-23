# Roadmap: EMS v1.3 Intelligent Self-Tuning

## Overview

Replace manual tuning with data-driven ML models that learn from real usage patterns. The dependency chain is strict: ML infrastructure first, then an upgraded forecaster that produces trustworthy predictions, then anomaly detection that measures system health, and finally self-tuning control that adjusts parameters only when the forecast and observability layers prove reliable. Each phase delivers independent user value while building the foundation for the next.

## Milestones

- v1.0 Independent Dual-Battery EMS (shipped 2026-03-23)
- v1.1 Advanced Optimization (shipped 2026-03-23)
- v1.2 Home Assistant Best Practice Alignment (shipped 2026-03-23)
- **v1.3 Intelligent Self-Tuning** - Phases 16-19 (in progress)

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 16: ML Infrastructure** - Foundation layer for model persistence, feature extraction, and safe sklearn execution (completed 2026-03-23)
- [ ] **Phase 17: Consumption Forecaster Upgrade** - Weather-aware forecasting with accuracy tracking and proper validation
- [ ] **Phase 18: Anomaly Detection** - Nightly anomaly model training with lightweight per-cycle checks and tiered alerts
- [ ] **Phase 19: Self-Tuning Control** - Data-driven parameter adjustment with shadow mode, bounded changes, and automatic rollback

## Phase Details

### Phase 16: ML Infrastructure
**Goal**: All ML components have a reliable foundation for model persistence, feature extraction, and non-blocking training
**Depends on**: Phase 15 (v1.2 complete)
**Requirements**: INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05
**Success Criteria** (what must be TRUE):
  1. A trained sklearn model can be saved to /config/ems_models/ and restored across EMS restarts with version metadata preserved
  2. When sklearn is upgraded, stale models are automatically discarded and retrained instead of crashing
  3. Feature extraction from InfluxDB and HA statistics completes in a single cached read without blocking the 5s control loop
  4. sklearn .fit() calls run in a background executor and never block the async event loop
  5. OMP_NUM_THREADS=2 is set in the Docker image so training on aarch64 does not oversubscribe CPU threads
**Plans**: 3 plans
Plans:
- [x] 16-01-PLAN.md — ModelStore module with joblib persistence and version-tracked JSON sidecars
- [x] 16-02-PLAN.md — FeaturePipeline with cached extraction from HA statistics and InfluxDB
- [ ] 16-03-PLAN.md — Non-blocking training, ModelStore wiring, and OMP_NUM_THREADS in Docker

### Phase 17: Consumption Forecaster Upgrade
**Goal**: The consumption forecaster produces meaningfully better predictions using real weather, historical patterns, and proper validation -- and the system knows how accurate those predictions are
**Depends on**: Phase 16
**Requirements**: FCST-01, FCST-02, FCST-03, FCST-04, FCST-05, FCST-06, FCST-07
**Success Criteria** (what must be TRUE):
  1. Forecast predictions incorporate outdoor temperature and Open-Meteo weather data as model inputs instead of the hardcoded neutral_temp placeholder
  2. The model uses 24h-ago and 1-week-ago consumption as lag features, plus day-of-week encoding
  3. After each day, the system computes and logs MAPE (mean absolute percentage error) comparing predicted vs actual consumption, visible via /api/ml/status
  4. The model uses HistGradientBoostingRegressor with native NaN handling so missing weather or lag data does not crash training
  5. Training uses expanding-window time-series cross-validation with recency weighting, not random splits
**Plans**: TBD

### Phase 18: Anomaly Detection
**Goal**: The system detects unusual consumption patterns, communication failures, and battery behavior drift -- alerting the user without generating false-positive fatigue
**Depends on**: Phase 17
**Requirements**: ANOM-01, ANOM-02, ANOM-03, ANOM-04, ANOM-05, ANOM-06, ANOM-07, ANOM-08
**Success Criteria** (what must be TRUE):
  1. Recurring driver timeout patterns are detected and surfaced as communication loss anomalies
  2. Unusual consumption spikes relative to time-of-day baselines trigger tiered alerts -- warning after 1 occurrence, alert after 3 within 24h
  3. SoC charge/discharge curve deviations and round-trip efficiency degradation trends are tracked and flagged over weeks
  4. Nightly IsolationForest training runs in a background executor while per-cycle anomaly checks use only pre-computed thresholds (no sklearn predict in the 5s loop)
  5. Anomaly events are queryable via REST API and optionally sent as Telegram notifications
**Plans**: TBD

### Phase 19: Self-Tuning Control
**Goal**: Control parameters (dead-bands, ramp rates, min-SoC profiles) automatically adjust based on real usage data -- with strict safety gates ensuring tuning only activates when the system has proven forecast accuracy and sufficient historical data
**Depends on**: Phase 17 (MAPE tracking), Phase 18 (oscillation detection)
**Requirements**: TUNE-01, TUNE-02, TUNE-03, TUNE-04, TUNE-05, TUNE-06, TUNE-07, TUNE-08
**Success Criteria** (what must be TRUE):
  1. The system counts state transitions per hour and uses this oscillation rate to inform dead-band and ramp rate adjustments
  2. Parameter changes are bounded to max 10% per night with absolute safe minimums, and automatically revert if oscillation rate increases
  3. Self-tuning runs in shadow mode for 14+ days (logging recommended vs actual parameters) before any live parameter changes are applied
  4. Self-tuning only activates when forecast MAPE is below 25% and at least 60 days of training data exist
  5. Dead-band, ramp rate, and min-SoC profile tuning each adjust independently based on their respective signals (oscillation rate, grid import spikes, consumption patterns)
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 16 -> 17 -> 18 -> 19

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 16. ML Infrastructure | 2/3 | Complete    | 2026-03-23 |
| 17. Consumption Forecaster Upgrade | 0/? | Not started | - |
| 18. Anomaly Detection | 0/? | Not started | - |
| 19. Self-Tuning Control | 0/? | Not started | - |
