---
phase: 17-consumption-forecaster-upgrade
verified: 2026-03-24T00:00:00Z
status: passed
score: 10/10 must-haves verified
re_verification: false
---

# Phase 17: Consumption Forecaster Upgrade — Verification Report

**Phase Goal:** The consumption forecaster produces meaningfully better predictions using real weather, historical patterns, and proper validation — and the system knows how accurate those predictions are
**Verified:** 2026-03-24
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | Predictions reflect actual outdoor temperature instead of hardcoded 10 C | VERIFIED | `_get_temperature_forecast()` calls `weather_client.get_temperature_forecast(hours=N)` in both `predict_hourly()` and `query_consumption_history()`. Fallback is `self._last_outdoor_temp` (learned from training data), not hardcoded 10.0. The constant 10.0 only initialises the fallback before any training has occurred. |
| 2 | Predictions account for day-of-week patterns (weekday vs weekend consumption differs) | VERIFIED | `_build_features()` produces an 8-column row where column 5 is `is_weekend = 1.0 if ts.weekday() >= 5 else 0.0`. Spot-check confirmed correct values for Wednesday (0.0) and Saturday (1.0). |
| 3 | Predictions use recent consumption history (24h-ago and 1-week-ago lag values) | VERIFIED | `_build_lag_features()` computes `lag_24h` (ts − 24h) and `lag_168h` (ts − 168h) from `consumption_map`. Both appear as columns 6 and 7 in every feature row during training. |
| 4 | Missing data does not crash the system — NaN lag values are handled gracefully | VERIFIED | When `consumption_map is None`, `_build_features()` fills lag columns with `float("nan")`. HistGBR handles NaN natively. Future-prediction rows also use `float("nan")` for lags (correct: no historical future data). Spot-check: `math.isnan(row[6]) == True`. |
| 5 | Recent training data influences the model more than old data (recency weighting) | VERIFIED | `_compute_recency_weights()` returns exponential decay with 30-day half-life. Spot-check: newest weight = 1.0, weight at t−30d = 0.5 (within 0.05 tolerance). `sample_weight=weights` passed to both `cross_val_score` and `model.fit()` for all three models. |
| 6 | Model quality is validated via time-series cross-validation before deployment | VERIFIED | `TimeSeriesSplit(n_splits=5)` used with `cross_val_score` in all three model closures (`_cv_and_fit_hp`, `_cv_and_fit_dhw`, `_cv_and_fit_base`). CV scores logged at INFO level. Final fit follows CV inside same `anyio.to_thread.run_sync` closure. |
| 7 | After each nightly retrain, MAPE is computed comparing yesterday's predictions vs actual hourly consumption | VERIFIED | `retrain_if_stale()` reads `self._last_hourly_predictions` and fetches 2 days of actual data before retraining. Filters yesterday's 24 hours, calls `_compute_daily_mape()`, logs result. Wrapped in try/except so retrain always proceeds. |
| 8 | MAPE history is stored in /config/ems_models/mape_history.json with last 30 days | VERIFIED | `_save_mape_history()` appends `{"date": str, "mape": float}` and trims to last 30 entries. Path derived from `Path(model_store._dir) / "mape_history.json"`. `mape_path.parent.mkdir(parents=True, exist_ok=True)` ensures directory exists. |
| 9 | GET /api/ml/status returns model names, last training time, sample count, MAPE history, current MAPE, model versions | VERIFIED | `get_ml_status()` returns `{"models": {...}, "mape": {"current": ..., "history": [...], "days_tracked": N}, "days_of_history": N, "min_training_days": 14}`. Endpoint at `/api/ml/status` returns 200 with this payload, 503 when forecaster is None. |
| 10 | ConsumptionForecaster is accessible from the API via app.state | VERIFIED | `app.state.consumption_forecaster = consumption_forecaster` on line 415 of `main.py`. Also set to `None` in degraded path (line 614). `get_forecaster()` DI function uses `getattr(request.app.state, "consumption_forecaster", None)`. |

**Score:** 10/10 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/weather_client.py` | `get_temperature_forecast()` method on `OpenMeteoClient` | VERIFIED | Method exists at line 134, async, returns `list[float] | None`, uses `temperature_2m` from Open-Meteo hourly API, handles HTTP/parse errors with WARNING log and `None` return. |
| `backend/consumption_forecaster.py` | Upgraded forecaster with HistGBR, FeaturePipeline integration, lag features, recency weighting, CV | VERIFIED | 1215 lines. Contains `HistGradientBoostingRegressor`, `FEATURE_NAMES` (8 entries), `_build_lag_features`, `_compute_recency_weights`, `TimeSeriesSplit`, `_compute_daily_mape`, `_save_mape_history`, `get_ml_status()`. FeaturePipeline path wired with reader fallback. |
| `backend/api.py` | `/api/ml/status` endpoint | VERIFIED | `get_forecaster` DI at line 454, `@api_router.get("/ml/status")` at line 466. Returns 503 when forecaster is None. |
| `backend/main.py` | Forecaster wired to `app.state` for API access | VERIFIED | `app.state.consumption_forecaster = consumption_forecaster` (line 415, normal path). `app.state.consumption_forecaster = None` (line 614, degraded path). |
| `tests/test_consumption_forecaster.py` | Tests for all FCST requirements | VERIFIED | All 11 planned test functions exist and pass. Additional MAPE tests (7 functions) also present and passing. |
| `tests/test_api.py` | API tests for `/api/ml/status` | VERIFIED | `test_get_ml_status_returns_200` (line 1458) and `test_get_ml_status_returns_503_when_not_ready` (line 1500) both pass. |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `consumption_forecaster.py` | `weather_client.py` | `get_temperature_forecast()` call in `_get_temperature_forecast()` | WIRED | `self._weather_client.get_temperature_forecast(hours=hours)` called at line 1195. Used by both `predict_hourly()` and `query_consumption_history()`. |
| `consumption_forecaster.py` | `feature_pipeline.py` | `self._feature_pipeline.extract()` call in `train()` | WIRED | `await self._feature_pipeline.extract(force_refresh=True, days=90)` at line 533. Conditional on `self._feature_pipeline is not None`. |
| `consumption_forecaster.py` | `sklearn.ensemble.HistGradientBoostingRegressor` | import and instantiation in `train()` | WIRED | Imported inside `train()` to handle ImportError gracefully. Instantiated three times (hp, dhw, base) with identical hyperparameters. |
| `consumption_forecaster.py` | `sklearn.model_selection.TimeSeriesSplit` | `cross_val_score` in all three model closures | WIRED | `TimeSeriesSplit(n_splits=5)` used as `cv=` argument. `cross_val_score` with `params={"sample_weight": weights}` (sklearn 1.8+ API). |
| `api.py` | `consumption_forecaster.py` | `Depends(get_forecaster)` calling `get_ml_status()` | WIRED | `get_forecaster` returns `getattr(request.app.state, "consumption_forecaster", None)`. Endpoint calls `forecaster.get_ml_status()`. |
| `consumption_forecaster.py` | `/config/ems_models/mape_history.json` | `_save_mape_history` file write | WIRED | `mape_path.write_text(json.dumps(history, indent=2))` with `mape_path.parent.mkdir(parents=True, exist_ok=True)`. |
| `main.py` | `app.state.consumption_forecaster` | attribute assignment in lifespan | WIRED | Set in both normal path (line 415) and degraded path (line 614). |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `predict_hourly()` | `temps` (temperature array) | `self._weather_client.get_temperature_forecast()` → Open-Meteo API | Yes — HTTP call to `api.open-meteo.com` returning `temperature_2m`. Falls back to `self._last_outdoor_temp` learned from training. | FLOWING |
| `train()` → feature matrix | `temp_data`, `hp_data`, `dhw_data` | `FeaturePipeline.extract()` (when wired) or `reader.read_entity_hourly()` (fallback) | Yes — reads from HA statistics SQLite / InfluxDB | FLOWING |
| `retrain_if_stale()` | `actual_yesterday` | `self._reader.read_entity_hourly(heat_pump_entity, days=2)` | Yes — reads real HA statistics for past 2 days | FLOWING |
| `get_ml_status()` | `mape_history` | `_load_mape_history(self._mape_path)` → JSON file | Yes — reads persisted JSON array | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| FEATURE_NAMES has exactly 8 entries | `from backend.consumption_forecaster import FEATURE_NAMES; assert len(FEATURE_NAMES) == 8` | `['outdoor_temp_c', 'ewm_temp_3d', 'day_of_week', 'hour_of_day', 'month', 'is_weekend', 'lag_24h', 'lag_168h']` | PASS |
| Feature row has 8 columns, is_weekend correct, lag is NaN for missing data | `_build_features` spot-check | row length=8, Wednesday is_weekend=0.0, Saturday is_weekend=1.0, lag NaN=True | PASS |
| Recency weights: newest~1.0, 30-day-old~0.5 | `_compute_recency_weights` with 30-day range | newest=1.0, t−30d=0.5 | PASS |
| MAPE computation: correct value, None on <12 pairs, near-zero filter | `_compute_daily_mape` spot-check | 24 valid pairs → 9.1%, 5 pairs → None, near-zero filtered correctly | PASS |
| Full test suite for forecaster + API | `python -m pytest tests/test_consumption_forecaster.py tests/test_api.py -q` | 156 passed, 1 warning (irrelevant config warning) | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| FCST-01 | 17-01-PLAN | Weather features — outdoor temp from HA + Open-Meteo forecast temps as model inputs | SATISFIED | `get_temperature_forecast()` on `OpenMeteoClient`; `_get_temperature_forecast()` on forecaster; 8-feature matrix includes `outdoor_temp_c` and `ewm_temp_3d`. |
| FCST-02 | 17-01-PLAN | Lagged consumption features — 24h and 168h (1 week) ago as predictors | SATISFIED | `_build_lag_features()` computes both lags; columns `lag_24h` and `lag_168h` in feature matrix. |
| FCST-03 | 17-01-PLAN | Calendar features — day-of-week encoding, optional holiday detection | SATISFIED | `day_of_week`, `hour_of_day`, `month`, and `is_weekend` all in feature matrix. |
| FCST-04 | 17-01-PLAN | Migrate to HistGradientBoostingRegressor with native NaN handling and early stopping | SATISFIED | All 3 models use `HistGradientBoostingRegressor(early_stopping=True, n_iter_no_change=10, validation_fraction=0.1)`. NaN lag values confirmed handled natively. |
| FCST-05 | 17-02-PLAN | MAPE tracking — compute and log forecast accuracy after each day, expose via API | SATISFIED | `_compute_daily_mape`, `_save_mape_history`, `_load_mape_history` all implemented. MAPE computed in `retrain_if_stale()`. `/api/ml/status` exposes history and current MAPE. |
| FCST-06 | 17-01-PLAN | Recency-weighted training — recent data weighted higher than old data | SATISFIED | `_compute_recency_weights(half_life_days=30)` produces exponential decay weights passed as `sample_weight` to `.fit()` and `cross_val_score`. |
| FCST-07 | 17-01-PLAN | Time-series cross-validation — expanding window CV instead of random split | SATISFIED | `TimeSeriesSplit(n_splits=5)` used with `cross_val_score(scoring="neg_mean_absolute_percentage_error")` for all three models. Scores logged at INFO level. |

No orphaned requirements. All 7 FCST requirements appear in plan frontmatter and are mapped to Phase 17 in REQUIREMENTS.md.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `backend/consumption_forecaster.py` | 614–615 | `def _train_hp() -> None: pass  # placeholder` — dead function defined but never called | Info | Dead code. The real training closure `_cv_and_fit_hp` immediately follows and is what gets called (line 642). No functional impact. |
| `backend/consumption_forecaster.py` | 63, 744, 746 | Base load trained on constant `_BASE_LOAD_W = 300.0` (placeholder target values) | Info | Acknowledged pre-existing limitation — comment notes "replaced in S02 when a real consumption entity is available in HA statistics." Not a phase 17 regression. Training still exercises HistGBR + CV + recency weighting correctly. |

No blockers. Both findings are informational.

---

### Human Verification Required

None. All observable behaviors for phase 17 goals can be verified programmatically.

---

### Gaps Summary

No gaps. All 10 observable truths verified, all 6 artifacts pass levels 1–4, all 7 key links wired, all 7 requirements satisfied, test suite passes (156/156).

The one noteworthy finding is the dead `_train_hp` function (defined but never called) on line 614. This is leftover scaffolding from the implementation — the real closure is `_cv_and_fit_hp` on line 617. This should be cleaned up in a future pass but does not affect correctness.

---

_Verified: 2026-03-24_
_Verifier: Claude (gsd-verifier)_
