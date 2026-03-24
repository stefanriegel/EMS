# Phase 17: Consumption Forecaster Upgrade - Research

**Researched:** 2026-03-24
**Domain:** ML consumption forecasting with weather features, lag features, MAPE tracking
**Confidence:** HIGH

## Summary

This phase upgrades the existing `ConsumptionForecaster` in `backend/consumption_forecaster.py` from `GradientBoostingRegressor` with a hardcoded neutral_temp placeholder to `HistGradientBoostingRegressor` with real weather features, lag consumption features, MAPE tracking, and time-series cross-validation. All changes build on the Phase 16 ML infrastructure (ModelStore, FeaturePipeline) and use only existing dependencies (scikit-learn 1.8.0, numpy).

The existing codebase has clear extension points: `_build_features()` for feature matrix construction, `retrain_if_stale()` for the nightly hook, `get_forecast_comparison()` for accuracy tracking, and the API router pattern in `backend/api.py` for the new `/api/ml/status` endpoint. The Open-Meteo weather client already exists but currently only fetches solar irradiance -- it needs a temperature forecast method added.

**Primary recommendation:** Modify `consumption_forecaster.py` in-place, replacing GBR with HistGBR, extending `_build_features()` with weather/lag features, adding MAPE computation to `retrain_if_stale()`, and exposing status via a new `/api/ml/status` endpoint. No new files needed except the MAPE history JSON file at runtime.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Keep separate heat_pump/base/dhw models -- existing architecture works, each has different data availability patterns
- Use HistGradientBoostingRegressor (per success criterion #4) -- handles NaN natively, no imputation needed
- Lag features with incomplete history use NaN -- let HistGradientBoosting handle missing values natively
- Wire FeaturePipeline from Phase 16 into the forecaster -- replace inline feature extraction, avoid duplicate code
- Store daily MAPE values in /config/ems_models/mape_history.json -- survives restarts, no DB dependency
- Compare previous day's hourly predictions vs actual hourly consumption from HA statistics
- /api/ml/status returns: model names, last training time, sample count, MAPE history (last 30 days), current MAPE, model versions
- Run daily MAPE computation during nightly retrain cycle (retrain_if_stale) -- natural hook, yesterday's data is complete
- TimeSeriesSplit with 5 folds, recency weighting via sample_weight (exponential decay, half-life 30 days)
- CV scores logged only -- use all data for final model (single estimator type, nothing to select)
- ModelStore version check handles GradientBoosting to HistGradientBoosting transition automatically (INFRA-02 from Phase 16)
- Keep existing min_training_days default (14 days) -- HistGradientBoosting handles sparse data better than previous estimator

### Claude's Discretion
- Internal implementation details of feature column ordering and naming
- Exact exponential decay formula for recency weighting
- MAPE history JSON schema details
- Error handling specifics for weather API failures during feature extraction

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| FCST-01 | Weather features integrated -- outdoor temp from HA + Open-Meteo forecast temps as model inputs | Open-Meteo API provides `temperature_2m` hourly; existing `OpenMeteoClient` can be extended; HA outdoor temp already read by `_build_features()` |
| FCST-02 | Lagged consumption features -- 24h and 168h (1 week) ago as predictors | HistGBR handles NaN natively (verified); lag features use NaN when history is incomplete |
| FCST-03 | Calendar features -- day-of-week encoding, optional holiday detection | Existing `_build_features()` already has `day_of_week`, `hour_of_day`, `month`; add is_weekend encoding |
| FCST-04 | Migrate to HistGradientBoostingRegressor with native NaN handling and early stopping | sklearn 1.8.0 installed; HistGBR verified with NaN handling and sample_weight support |
| FCST-05 | MAPE tracking -- compute and log forecast accuracy after each day, expose via API | `retrain_if_stale()` is the nightly hook; `get_forecast_comparison()` already computes error_pct; extend to hourly MAPE |
| FCST-06 | Recency-weighted training -- recent data weighted higher than old data | HistGBR `.fit()` accepts `sample_weight` parameter (verified); exponential decay with half-life 30 days |
| FCST-07 | Time-series cross-validation -- expanding window CV instead of random split | `sklearn.model_selection.TimeSeriesSplit` with `n_splits=5` produces expanding windows (verified) |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Graceful degradation**: every external dep must be optional -- `None` checks, never crash
- **No cloud dependencies for core**: Open-Meteo weather is enhancement only, not required
- **Python conventions**: `snake_case` files/functions, `PascalCase` dataclasses, `from __future__ import annotations`, type hints, 4-space indent, 88-char lines
- **Test conventions**: `tests/test_*.py` with `pytest` + `anyio`, `@pytest.mark.anyio`
- **Error handling**: explicit exceptions, fire-and-forget for optional integrations
- **Imports**: stdlib, third-party, local (blank-line separated), absolute imports
- **Config pattern**: dataclass with `@classmethod from_env()`
- **Run `caliber refresh` before committing**

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| scikit-learn | 1.8.0 (installed) | HistGradientBoostingRegressor, TimeSeriesSplit, mean_absolute_percentage_error | Already a dependency; no new packages needed |
| numpy | (bundled) | Feature matrix construction, sample_weight array, NaN values | Already a dependency |
| joblib | (bundled with sklearn) | Model persistence via ModelStore | Already in use from Phase 16 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | (installed) | Open-Meteo temperature forecast HTTP calls | Extending existing OpenMeteoClient |
| anyio | (installed) | `to_thread.run_sync` for non-blocking `.fit()` calls | All sklearn training calls |

No new dependencies required.

## Architecture Patterns

### Modified Files
```
backend/
├── consumption_forecaster.py  # Major: HistGBR, features, MAPE, CV
├── weather_client.py          # Minor: add get_temperature_forecast()
├── api.py                     # Minor: add /api/ml/status endpoint
├── main.py                    # Minor: wire forecaster to app.state for API
tests/
├── test_consumption_forecaster.py  # Major: new tests for all FCST requirements
├── test_api.py                     # Minor: test /api/ml/status
```

### Pattern 1: HistGradientBoostingRegressor Migration
**What:** Replace `GradientBoostingRegressor` with `HistGradientBoostingRegressor`
**When to use:** In `train()` method for all three models (heat_pump, dhw, base_load)
**Example:**
```python
# Source: sklearn 1.8.0 verified locally
from sklearn.ensemble import HistGradientBoostingRegressor

model = HistGradientBoostingRegressor(
    max_iter=100,
    max_depth=3,
    random_state=42,
    early_stopping=True,
    n_iter_no_change=10,
    validation_fraction=0.1,
)
# NaN in X is handled natively -- no imputation needed
await anyio.to_thread.run_sync(partial(model.fit, X, y, sample_weight=weights))
```

### Pattern 2: Lag Feature Construction with NaN
**What:** Add 24h-ago and 168h-ago consumption as features, using NaN for missing history
**When to use:** In `_build_features()` when constructing the feature matrix
**Example:**
```python
import numpy as np

def _build_lag_features(
    timestamps: list[datetime],
    consumption_map: dict[datetime, float],
) -> tuple[list[float], list[float]]:
    """Build lag-24h and lag-168h features, NaN when unavailable."""
    lag_24h = []
    lag_168h = []
    for ts in timestamps:
        ts_24h = ts - timedelta(hours=24)
        ts_168h = ts - timedelta(hours=168)
        lag_24h.append(consumption_map.get(ts_24h, float("nan")))
        lag_168h.append(consumption_map.get(ts_168h, float("nan")))
    return lag_24h, lag_168h
```

### Pattern 3: Recency-Weighted Sample Weights
**What:** Exponential decay weights so recent data matters more
**When to use:** Passed to `model.fit(X, y, sample_weight=weights)`
**Example:**
```python
import numpy as np

def _compute_recency_weights(
    timestamps: list[datetime],
    half_life_days: float = 30.0,
) -> np.ndarray:
    """Exponential decay weights with half-life in days."""
    if not timestamps:
        return np.array([])
    latest = max(timestamps)
    ages_hours = np.array([
        (latest - ts).total_seconds() / 3600.0 for ts in timestamps
    ])
    # ln(2) / half_life_hours gives decay rate
    decay_rate = np.log(2) / (half_life_days * 24.0)
    weights = np.exp(-decay_rate * ages_hours)
    return weights
```

### Pattern 4: MAPE Computation and Storage
**What:** Compare yesterday's hourly predictions vs actual, store daily MAPE
**When to use:** Inside `retrain_if_stale()` before retraining
**Example:**
```python
import json
from pathlib import Path

def _compute_daily_mape(
    predicted_hourly: list[float],
    actual_hourly: list[float],
) -> float | None:
    """MAPE as percentage. Returns None if insufficient data."""
    pairs = [
        (p, a) for p, a in zip(predicted_hourly, actual_hourly)
        if a > 0.001  # skip near-zero actuals
    ]
    if len(pairs) < 12:  # need at least 12 hours
        return None
    mape = sum(abs(p - a) / a for p, a in pairs) / len(pairs) * 100.0
    return round(mape, 1)

def _save_mape_history(
    mape_path: Path,
    date_str: str,
    mape_value: float,
    max_days: int = 30,
) -> None:
    """Append daily MAPE to JSON history, keeping last max_days entries."""
    history: list[dict] = []
    if mape_path.exists():
        try:
            history = json.loads(mape_path.read_text())
        except (json.JSONDecodeError, OSError):
            history = []
    history.append({"date": date_str, "mape": mape_value})
    history = history[-max_days:]  # keep last 30
    mape_path.write_text(json.dumps(history, indent=2))
```

### Pattern 5: TimeSeriesSplit Cross-Validation
**What:** Expanding-window CV with logged scores
**When to use:** During training, after building feature matrix
**Example:**
```python
from sklearn.model_selection import TimeSeriesSplit, cross_val_score

tscv = TimeSeriesSplit(n_splits=5)
scores = cross_val_score(
    model, X, y,
    cv=tscv,
    scoring="neg_mean_absolute_percentage_error",
    fit_params={"sample_weight": weights},
)
logger.info(
    "CV scores (neg_MAPE): mean=%.1f%% std=%.1f%%",
    -scores.mean(), scores.std(),
)
# Then train on ALL data for the final model
model.fit(X, y, sample_weight=weights)
```

### Pattern 6: /api/ml/status Endpoint
**What:** New GET endpoint returning ML model status and MAPE history
**When to use:** Following the add-api-endpoint skill pattern
**Example:**
```python
# In backend/api.py
def get_forecaster(request: Request) -> ConsumptionForecaster | None:
    return getattr(request.app.state, "consumption_forecaster", None)

@api_router.get("/ml/status")
async def get_ml_status(
    forecaster: ConsumptionForecaster | None = Depends(get_forecaster),
) -> dict[str, Any]:
    """Return ML model status, training info, and MAPE history."""
    if forecaster is None:
        raise HTTPException(status_code=503, detail="ML forecaster not available")
    return forecaster.get_ml_status()
```

### Pattern 7: Open-Meteo Temperature Forecast
**What:** Extend OpenMeteoClient to fetch hourly temperature forecasts
**When to use:** During prediction (not training -- training uses historical HA data)
**Example:**
```python
# Add to OpenMeteoClient in weather_client.py
async def get_temperature_forecast(self, hours: int = 72) -> list[float] | None:
    """Fetch hourly temperature forecast from Open-Meteo.

    Returns list of temperature_2m values in Celsius, or None on error.
    """
    params = {
        "latitude": self._config.latitude,
        "longitude": self._config.longitude,
        "hourly": "temperature_2m",
        "forecast_days": (hours + 23) // 24,
        "timezone": "UTC",
    }
    try:
        async with httpx.AsyncClient(timeout=self._config.timeout_s) as client:
            resp = await client.get(self._base_url, params=params)
            resp.raise_for_status()
            data = resp.json()
        temps = data["hourly"]["temperature_2m"][:hours]
        return temps
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        logger.warning("open-meteo get_temperature_forecast failed: %s", exc)
        return None
```

### Anti-Patterns to Avoid
- **Random train/test split on time-series data:** Causes data leakage (future data in training set). Use TimeSeriesSplit only.
- **Imputing NaN before passing to HistGBR:** Defeats the purpose of using HistGBR. Let it handle NaN natively.
- **Running cross_val_score in the asyncio event loop:** Must wrap in `anyio.to_thread.run_sync`.
- **Storing hourly predictions for MAPE without timestamps:** Need to match prediction hours to actual hours precisely.
- **Using sklearn `mean_absolute_percentage_error` on hourly data with zeros:** Near-zero actual values cause infinite MAPE. Filter out hours with actual < threshold.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Time-series CV | Custom fold logic | `sklearn.model_selection.TimeSeriesSplit` | Handles expanding windows, avoids off-by-one in fold boundaries |
| MAPE metric | Custom percentage error | `sklearn.metrics.mean_absolute_percentage_error` for validation; custom for hourly with zero-guard | sklearn's version is well-tested but needs zero-guard for production |
| NaN handling | Custom imputation pipeline | HistGBR native NaN support | HistGBR's missing-value bins are more robust than any imputation strategy |
| Model persistence | Custom pickle/JSON | ModelStore from Phase 16 | Already handles version checking and metadata sidecars |

## Common Pitfalls

### Pitfall 1: Feature Matrix Shape Mismatch After Upgrade
**What goes wrong:** Adding lag features changes the feature count from 5 to 7+. Persisted models trained with the old 5-feature format will crash on `.predict()` with the new 7-feature input.
**Why it happens:** ModelStore loads old models without checking feature_names against current expectations.
**How to avoid:** ModelStore already handles this -- sklearn version mismatch triggers discard. Additionally, the feature_names field in ModelMetadata provides an explicit check. On load, compare `metadata.feature_names` length against current feature count; discard if mismatched.
**Warning signs:** `ValueError: X has N features, but model is expecting M features` on first prediction after upgrade.

### Pitfall 2: Lag Features All-NaN in Early Training
**What goes wrong:** With only 14 days of data and a 168h (7-day) lag, the first 7 days of training samples have NaN for the 168h lag. HistGBR handles this fine, but if more than ~70% of a feature column is NaN, the feature provides minimal signal and the model may overfit to the non-NaN portion.
**Why it happens:** Early days of training data have no history to lag from.
**How to avoid:** This is acceptable -- HistGBR's native NaN handling routes NaN values through a separate decision tree path. With 14+ days, the 168h lag has useful data for at least 50% of samples. The 24h lag only loses 1 day (7% of 14 days).
**Warning signs:** Feature importance of lag features near zero in early models. Expected to improve as history accumulates.

### Pitfall 3: MAPE Explosion on Low-Consumption Hours
**What goes wrong:** MAPE formula divides by actual value. Nighttime hours with 0.05 kWh actual and 0.10 kWh predicted give 100% error, making the daily MAPE meaninglessly high.
**How to avoid:** Filter out hours where actual consumption is below a minimum threshold (e.g., 0.1 kWh) when computing MAPE. Require at least 12 valid hours for a daily MAPE to be considered meaningful.
**Warning signs:** MAPE consistently above 100% despite reasonable daytime predictions.

### Pitfall 4: Open-Meteo Temperature Forecast Unavailable During Prediction
**What goes wrong:** The prediction path needs future temperatures for the next 72 hours. If Open-Meteo is down, the system currently uses `neutral_temp = 10.0`. With real weather features trained in, predictions using 10.0 for all hours will be worse than the old model.
**How to avoid:** When weather forecast is unavailable, use the last known outdoor temperature from HA statistics as a fallback (better than 10.0). For training, always use actual HA temperatures (never forecast), so the model learns from real data regardless of weather API availability.
**Warning signs:** Prediction accuracy drops significantly on days when Open-Meteo was unavailable.

### Pitfall 5: Cross-Validation Blocking the Event Loop
**What goes wrong:** `cross_val_score` with 5 folds trains 5 models. On aarch64 this could take 30+ seconds total, blocking the event loop.
**How to avoid:** Wrap the entire CV + final fit in a single `anyio.to_thread.run_sync` call so the event loop remains responsive. The CV scoring is informational only (logged, not used for model selection), so it can be a single blocking thread operation.
**Warning signs:** asyncio event loop blocked warnings during nightly retrain.

## Code Examples

### Complete Feature Matrix Construction (Upgraded)
```python
# Features: [outdoor_temp_c, ewm_temp_3d, day_of_week, hour_of_day, month,
#            is_weekend, lag_24h, lag_168h]
def _build_features_v2(
    timestamps: list[datetime],
    outdoor_temps: list[float],
    ewm_temps: list[float],
    consumption_map: dict[datetime, float],
) -> list[list[float]]:
    rows: list[list[float]] = []
    for ts, ot, ewm in zip(timestamps, outdoor_temps, ewm_temps):
        ts_24h = ts - timedelta(hours=24)
        ts_168h = ts - timedelta(hours=168)
        rows.append([
            ot,                                    # outdoor_temp_c
            ewm,                                   # ewm_temp_3d
            float(ts.weekday()),                   # day_of_week
            float(ts.hour),                        # hour_of_day
            float(ts.month),                       # month
            float(ts.weekday() >= 5),              # is_weekend
            consumption_map.get(ts_24h, float("nan")),   # lag_24h
            consumption_map.get(ts_168h, float("nan")),  # lag_168h
        ])
    return rows
```

### MAPE History JSON Schema
```json
[
  {"date": "2026-03-22", "mape": 12.3},
  {"date": "2026-03-23", "mape": 15.7}
]
```

### /api/ml/status Response Schema
```json
{
  "models": {
    "heat_pump": {
      "trained": true,
      "last_trained_at": "2026-03-23T02:00:00Z",
      "sample_count": 2160,
      "feature_names": ["outdoor_temp_c", "ewm_temp_3d", "day_of_week", "hour_of_day", "month", "is_weekend", "lag_24h", "lag_168h"],
      "sklearn_version": "1.8.0"
    },
    "dhw": {"trained": false, "last_trained_at": null, "sample_count": 0},
    "base_load": {"trained": true, "...": "..."}
  },
  "mape": {
    "current": 15.7,
    "history": [
      {"date": "2026-03-22", "mape": 12.3},
      {"date": "2026-03-23", "mape": 15.7}
    ],
    "days_tracked": 2
  },
  "days_of_history": 87,
  "min_training_days": 14
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `GradientBoostingRegressor` | `HistGradientBoostingRegressor` | sklearn 1.0 (2021) | Native NaN handling, 2-10x faster training, native `early_stopping` |
| `neutral_temp = 10.0` placeholder | Real outdoor temp from HA + Open-Meteo forecast | This phase | Predictions actually respond to weather conditions |
| Random train/test split | `TimeSeriesSplit` | Best practice | Prevents future-data leakage in time-series models |
| No accuracy tracking | Daily MAPE with 30-day rolling history | This phase | System knows how well it predicts; gates Phase 19 self-tuning |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-anyio |
| Config file | `pyproject.toml` [tool.pytest.ini_options] with `anyio_mode = "auto"` |
| Quick run command | `python -m pytest tests/test_consumption_forecaster.py -q` |
| Full suite command | `python -m pytest tests/ -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FCST-01 | Weather features used in training and prediction | unit | `python -m pytest tests/test_consumption_forecaster.py::test_train_uses_weather_features -x` | Wave 0 |
| FCST-02 | Lag features (24h, 168h) included in feature matrix | unit | `python -m pytest tests/test_consumption_forecaster.py::test_lag_features_in_matrix -x` | Wave 0 |
| FCST-03 | Calendar features (is_weekend) in feature matrix | unit | `python -m pytest tests/test_consumption_forecaster.py::test_calendar_features -x` | Wave 0 |
| FCST-04 | HistGBR used, NaN in features does not crash | unit | `python -m pytest tests/test_consumption_forecaster.py::test_histgbr_handles_nan -x` | Wave 0 |
| FCST-05 | MAPE computed and stored, /api/ml/status returns it | unit + API | `python -m pytest tests/test_consumption_forecaster.py::test_mape_computation tests/test_api.py::test_get_ml_status -x` | Wave 0 |
| FCST-06 | sample_weight with recency decay passed to fit() | unit | `python -m pytest tests/test_consumption_forecaster.py::test_recency_weighting -x` | Wave 0 |
| FCST-07 | TimeSeriesSplit used instead of random split | unit | `python -m pytest tests/test_consumption_forecaster.py::test_time_series_cv -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_consumption_forecaster.py -q`
- **Per wave merge:** `python -m pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] New test functions in `tests/test_consumption_forecaster.py` for FCST-01 through FCST-07
- [ ] New test function in `tests/test_api.py` for `/api/ml/status` endpoint
- [ ] Existing tests must be updated to account for HistGBR and new feature count

## Sources

### Primary (HIGH confidence)
- **sklearn 1.8.0 (installed locally)** -- verified HistGradientBoostingRegressor API: `__init__` parameters, `.fit()` accepts `sample_weight`, NaN handling confirmed with test
- **sklearn 1.8.0 (installed locally)** -- verified TimeSeriesSplit: `n_splits=5` produces expanding windows as expected
- **Existing codebase** -- `backend/consumption_forecaster.py`, `backend/weather_client.py`, `backend/model_store.py`, `backend/feature_pipeline.py`, `backend/api.py`, `backend/config.py`

### Secondary (MEDIUM confidence)
- **Open-Meteo API** -- `temperature_2m` parameter confirmed available as hourly forecast variable; same API endpoint as existing solar forecast
- **Project research** -- `.planning/research/STACK.md` and `.planning/research/PITFALLS.md` from v1.3 research phase

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already installed and verified locally
- Architecture: HIGH -- modifying existing files with clear extension points
- Pitfalls: HIGH -- based on direct codebase analysis and sklearn documentation

**Research date:** 2026-03-24
**Valid until:** 2026-04-24 (stable -- sklearn and project architecture unlikely to change)
