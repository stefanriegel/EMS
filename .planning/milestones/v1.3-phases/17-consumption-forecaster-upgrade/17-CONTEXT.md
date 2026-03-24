# Phase 17: Consumption Forecaster Upgrade - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

The consumption forecaster produces meaningfully better predictions using real weather, historical patterns, and proper validation — and the system knows how accurate those predictions are. This phase upgrades the existing ConsumptionForecaster with weather-aware features, lag features, HistGradientBoostingRegressor, MAPE tracking, and a new /api/ml/status endpoint.

</domain>

<decisions>
## Implementation Decisions

### ML Model Architecture
- Keep separate heat_pump/base/dhw models — existing architecture works, each has different data availability patterns
- Use HistGradientBoostingRegressor (per success criterion #4) — handles NaN natively, no imputation needed
- Lag features with incomplete history use NaN — let HistGradientBoosting handle missing values natively
- Wire FeaturePipeline from Phase 16 into the forecaster — replace inline feature extraction, avoid duplicate code

### MAPE Tracking & API
- Store daily MAPE values in /config/ems_models/mape_history.json — survives restarts, no DB dependency
- Compare previous day's hourly predictions vs actual hourly consumption from HA statistics
- /api/ml/status returns: model names, last training time, sample count, MAPE history (last 30 days), current MAPE, model versions
- Run daily MAPE computation during nightly retrain cycle (retrain_if_stale) — natural hook, yesterday's data is complete

### Cross-Validation & Training Strategy
- TimeSeriesSplit with 5 folds, recency weighting via sample_weight (exponential decay, half-life 30 days)
- CV scores logged only — use all data for final model (single estimator type, nothing to select)
- ModelStore version check handles GradientBoosting→HistGradientBoosting transition automatically (INFRA-02 from Phase 16)
- Keep existing min_training_days default (14 days) — HistGradientBoosting handles sparse data better than previous estimator

### Claude's Discretion
- Internal implementation details of feature column ordering and naming
- Exact exponential decay formula for recency weighting
- MAPE history JSON schema details
- Error handling specifics for weather API failures during feature extraction

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/consumption_forecaster.py` — existing forecaster with _build_features(), train(), predict_hourly(), retrain_if_stale()
- `backend/feature_pipeline.py` — Phase 16 FeaturePipeline with cached HA stats + InfluxDB extraction
- `backend/model_store.py` — Phase 16 ModelStore with joblib persistence + version tracking
- `backend/weather_client.py` — OpenMeteoClient with forecast data
- `backend/ha_statistics_reader.py` — read_entity_hourly() for historical consumption

### Established Patterns
- neutral_temp = 10.0 placeholder in query_consumption_history() and predict_hourly() — replace with real weather data
- _build_features() already has outdoor_temp_c, ewm_temp_3d, day_of_week, hour_of_day, month columns
- anyio.to_thread.run_sync() for non-blocking .fit() calls (Phase 16)
- Fire-and-forget for optional integrations

### Integration Points
- `backend/api.py` — add /api/ml/status endpoint
- `backend/main.py` — FeaturePipeline construction and injection
- `backend/config.py` — any new config for MAPE tracking

</code_context>

<specifics>
## Specific Ideas

- The _build_features() function already defines the feature columns — upgrade should extend these with lag features (24h-ago, 1-week-ago)
- get_forecast_comparison() already exists for comparing predictions vs actuals — extend for MAPE calculation
- retrain_if_stale() is the natural hook for nightly MAPE computation

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>
