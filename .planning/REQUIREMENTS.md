# Requirements: EMS v1.3

**Defined:** 2026-03-23
**Core Value:** Both battery systems operate independently with zero oscillation to maximize PV self-consumption across the combined 94 kWh pool.

## v1.3 Requirements

Requirements for Intelligent Self-Tuning milestone.

### ML Infrastructure

- [ ] **INFRA-01**: ModelStore persists trained models with joblib, tracks sklearn version, discards on version mismatch
- [ ] **INFRA-02**: FeaturePipeline extracts training features from InfluxDB and HA statistics in a single cached read
- [ ] **INFRA-03**: All sklearn .fit() calls wrapped in run_in_executor to avoid blocking the event loop
- [ ] **INFRA-04**: OMP_NUM_THREADS=2 set in Dockerfile/run.sh for aarch64 thread safety
- [ ] **INFRA-05**: Model directory at /config/ems_models/ with JSON metadata sidecars for each model

### Consumption Forecasting

- [ ] **FCST-01**: Weather features integrated — outdoor temp from HA + Open-Meteo forecast temps as model inputs
- [ ] **FCST-02**: Lagged consumption features — 24h and 168h (1 week) ago as predictors
- [ ] **FCST-03**: Calendar features — day-of-week encoding, optional holiday detection
- [ ] **FCST-04**: Migrate to HistGradientBoostingRegressor with native NaN handling and early stopping
- [ ] **FCST-05**: MAPE tracking — compute and log forecast accuracy after each day, expose via API
- [ ] **FCST-06**: Recency-weighted training — recent data weighted higher than old data
- [ ] **FCST-07**: Time-series cross-validation — expanding window CV instead of random split

### Self-Tuning Control

- [ ] **TUNE-01**: Oscillation detector counts state transitions per hour from coordinator decisions
- [ ] **TUNE-02**: Dead-band auto-tuning — adjust Huawei/Victron hysteresis based on oscillation rate
- [ ] **TUNE-03**: Ramp rate auto-tuning — adjust based on grid import spikes during transitions
- [ ] **TUNE-04**: Min-SoC profile auto-tuning — adjust based on consumption patterns and solar forecast accuracy
- [ ] **TUNE-05**: Shadow mode — log recommended vs actual parameters for 14 days before live application
- [ ] **TUNE-06**: Bounded changes — max 10% adjustment per night with absolute safe bounds
- [ ] **TUNE-07**: Automatic rollback — revert to previous parameters if oscillation rate increases after tuning
- [ ] **TUNE-08**: Activation gate — self-tuning only activates when forecast MAPE < 25% and 60+ days of data

### Anomaly Detection

- [ ] **ANOM-01**: Communication loss pattern detection — identify recurring driver timeout patterns
- [ ] **ANOM-02**: Consumption spike detection — flag unusual consumption relative to time-of-day baseline
- [ ] **ANOM-03**: Tiered alerts with confirmation periods — warning after 1 occurrence, alert after 3 within 24h
- [ ] **ANOM-04**: SoC curve anomaly detection — flag when charge/discharge curves deviate from learned profile
- [ ] **ANOM-05**: Efficiency degradation tracking — monitor round-trip efficiency trends over weeks
- [ ] **ANOM-06**: Nightly Isolation Forest training on InfluxDB metrics for multi-dimensional anomaly scoring
- [ ] **ANOM-07**: Per-cycle anomaly check uses pre-computed statistical thresholds only (no sklearn predict in 5s loop)
- [ ] **ANOM-08**: Anomaly events exposed via REST API and Telegram notifications

## Future Requirements

### Deferred to v1.4+

- **ADV-01**: Battery degradation modeling — predict optimal charge/discharge for lifespan maximization
- **ADV-02**: Dynamic tariff price prediction — forecast Octopus Agile prices for smarter charging
- **ADV-03**: Federated learning — aggregate anonymized patterns across multiple EMS installations

## Out of Scope

| Feature | Reason |
|---------|--------|
| Deep learning (LSTM, Transformer) | Overkill for ~2,000 samples/year residential data; trains slower on aarch64 |
| Reinforcement learning | Needs thousands of real-day episodes, makes bad decisions during exploration |
| LightGBM / XGBoost | Docker build complexity on aarch64 Alpine with zero benefit at this scale |
| GPU inference | HA Add-on runs on Raspberry Pi; no GPU available |
| Real-time model retraining | Nightly batch is sufficient; per-cycle retraining wastes CPU |
| Anomaly-triggered control changes | Anomalies are observability, not control — never auto-change parameters |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFRA-01 | Phase 16 | Pending |
| INFRA-02 | Phase 16 | Pending |
| INFRA-03 | Phase 16 | Pending |
| INFRA-04 | Phase 16 | Pending |
| INFRA-05 | Phase 16 | Pending |
| FCST-01 | Phase 17 | Pending |
| FCST-02 | Phase 17 | Pending |
| FCST-03 | Phase 17 | Pending |
| FCST-04 | Phase 17 | Pending |
| FCST-05 | Phase 17 | Pending |
| FCST-06 | Phase 17 | Pending |
| FCST-07 | Phase 17 | Pending |
| ANOM-01 | Phase 18 | Pending |
| ANOM-02 | Phase 18 | Pending |
| ANOM-03 | Phase 18 | Pending |
| ANOM-04 | Phase 18 | Pending |
| ANOM-05 | Phase 18 | Pending |
| ANOM-06 | Phase 18 | Pending |
| ANOM-07 | Phase 18 | Pending |
| ANOM-08 | Phase 18 | Pending |
| TUNE-01 | Phase 19 | Pending |
| TUNE-02 | Phase 19 | Pending |
| TUNE-03 | Phase 19 | Pending |
| TUNE-04 | Phase 19 | Pending |
| TUNE-05 | Phase 19 | Pending |
| TUNE-06 | Phase 19 | Pending |
| TUNE-07 | Phase 19 | Pending |
| TUNE-08 | Phase 19 | Pending |

**Coverage:**
- v1.3 requirements: 28 total
- Mapped to phases: 28
- Unmapped: 0

---
*Requirements defined: 2026-03-23*
*Last updated: 2026-03-23 after roadmap creation*
