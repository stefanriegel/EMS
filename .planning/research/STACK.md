# Stack Research: ML Self-Tuning for EMS v1.3

**Domain:** ML-enhanced energy management (forecasting, self-tuning, anomaly detection)
**Researched:** 2026-03-23
**Confidence:** HIGH (all recommendations use existing dependencies or well-established sklearn modules)

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| scikit-learn | >=1.4,<2 (keep existing) | All ML models: forecasting, anomaly detection, self-tuning | Already a dependency. Contains every algorithm needed: GBR, IsolationForest, HistGradientBoosting. Zero new deps required for core ML. Current stable is 1.8.0 (Dec 2025). |
| numpy | >=1.25,<3 (keep existing) | Feature engineering, array ops, statistical computations | Already a dependency. Used for rolling statistics, z-score calculations, feature matrix construction. |
| joblib | (bundled with sklearn) | Model persistence to disk | Ships with scikit-learn. The standard for sklearn model serialization. No additional install needed. |

### Key Design Decision: NO New ML Dependencies

The entire v1.3 ML feature set can be built with the existing `scikit-learn` and `numpy` dependencies. This is the strongest recommendation in this document.

**Rationale:**
- The EMS runs on aarch64 Alpine inside an HA Add-on with `openblas-dev` already installed for sklearn
- Every new C-extension dependency (LightGBM, XGBoost) adds Docker build time, image size, and aarch64 compilation risk
- The dataset is small: ~2,160 hourly samples per 90-day training window. At this scale, sklearn's `GradientBoostingRegressor` trains in <1 second. LightGBM's histogram binning advantage only kicks in above ~10,000 samples
- scikit-learn 1.4+ has `HistGradientBoostingRegressor` which provides LightGBM-comparable speed natively if the dataset ever grows

### Module-by-Module Recommendations

#### 1. Consumption Forecasting (Upgrade Existing)

| Component | Current | Recommended Change | Why |
|-----------|---------|-------------------|-----|
| Model class | `GradientBoostingRegressor` | Keep, or migrate to `HistGradientBoostingRegressor` | HistGBR handles missing values natively (useful when HA entities have gaps), trains faster. Available in sklearn >=1.0. Both support `warm_start=True`. |
| Features | 5 features (temp, ewm_temp, dow, hour, month) | Add ~5 more: is_weekend, is_holiday_de, solar_forecast_kwh, wind_speed, cloud_cover | Weather features from existing Open-Meteo client. Calendar features from stdlib. No new deps. |
| Feature engineering | Manual `_build_features()` | Keep manual approach | At 5-10 features, sklearn Pipelines add complexity without benefit. Keep the explicit feature matrix. |
| Training data | 90 days HA SQLite | Keep 90 days | Sufficient for seasonal patterns. Longer risks concept drift from household changes. |
| Retraining | Daily via `retrain_if_stale(24)` | Keep daily nightly batch | Correct cadence. More frequent wastes cycles. Less frequent misses seasonal shifts. |

**Migration path for HistGBR:**
```python
# Before (current)
from sklearn.ensemble import GradientBoostingRegressor
model = GradientBoostingRegressor(n_estimators=100, max_depth=3, random_state=42)

# After (v1.3) -- native NaN handling, faster training
from sklearn.ensemble import HistGradientBoostingRegressor
model = HistGradientBoostingRegressor(
    max_iter=100, max_depth=3, random_state=42,
    early_stopping=True, n_iter_no_change=10,  # auto stop if converged
)
```

#### 2. Self-Tuning Control Parameters

| Component | Approach | Library | Why |
|-----------|---------|---------|-----|
| Parameter optimizer | Bayesian-style search over dead-band/ramp configs | `sklearn.gaussian_process.GaussianProcessRegressor` | Lightweight surrogate model. Evaluates few configurations per night. No scipy.optimize needed. |
| Alternative | Grid search with performance scoring | Manual loop + numpy | Even simpler. With only 3-5 parameters to tune (dead_band_huawei, dead_band_victron, ramp_rate, min_soc_floor), exhaustive grid search is feasible nightly. |
| Performance metric | Oscillation count + energy waste from InfluxDB | `numpy` statistical functions | Query InfluxDB for setpoint reversals, idle losses. Score each parameter set. |
| Safety bounds | Hard min/max per parameter | Config dataclass with validation | Never let self-tuning set dead-band below safe minimum. Enforce bounds in code. |

**Recommended approach:** Start with simple grid search over bounded parameter space. Gaussian Process only if the parameter space grows beyond 5 dimensions.

#### 3. Anomaly Detection

| Component | Algorithm | Library | Why |
|-----------|-----------|---------|-----|
| Hardware fault detection | `IsolationForest` | `sklearn.ensemble.IsolationForest` | Tree-based, fast, no feature scaling needed. Works well with the mixed numeric features from Modbus readings. Handles small datasets. |
| Consumption anomaly | Statistical z-score on residuals | `numpy` | Simpler and more interpretable than ML for "actual vs predicted" deviation. Flag when residual > 2.5 sigma. |
| Driver behavior drift | Exponential moving average on error rates | `numpy` | Track Modbus timeout rate, CRC error rate. EMA + threshold is sufficient. No ML model needed. |
| SoC sensor drift | Rolling mean comparison | `numpy` | Compare reported SoC delta vs integrated power delta. Pure math, no ML. |

**Why IsolationForest over LocalOutlierFactor:**
- IsolationForest is faster on the data volumes here (~2K-10K samples)
- Does not require feature scaling (LOF does)
- Better at global anomalies (hardware faults are global, not local density deviations)
- Supports `predict()` on new unseen data (LOF in sklearn is primarily for training data scoring unless `novelty=True`)

#### 4. Model Persistence

| Component | Approach | Library | Why |
|-----------|---------|---------|-----|
| Serialization | `joblib.dump()` / `joblib.load()` | `joblib` (bundled with sklearn) | Standard sklearn persistence. Efficient with numpy arrays. Already available. |
| Versioning | Metadata sidecar JSON | `json` (stdlib) | Store sklearn version, feature names, training date, sample count, RMSE alongside each model file. |
| Storage location | `/config/ems_models/` (HA persistent config dir) | `pathlib` (stdlib) | Survives container restarts. HA Add-on convention for persistent data. |
| Cold-start | Seasonal fallback (existing pattern) | Already implemented | Current `_seasonal_fallback_kwh` and `_seasonal_hourly_fallback` patterns are correct. Extend to anomaly detection (no anomaly flagging until model trained). |
| Model rotation | Keep last 2 versions | `pathlib` + `os` (stdlib) | Rollback if new model performs worse. Simple file rename. |

**Persistence file layout:**
```
/config/ems_models/
  consumption_forecaster.joblib       # Current model
  consumption_forecaster.prev.joblib  # Previous version (rollback)
  consumption_forecaster.meta.json    # {sklearn_version, features, trained_at, rmse, samples}
  anomaly_detector.joblib
  anomaly_detector.meta.json
  tuning_history.json                 # Parameter tuning results over time
```

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `holidays` | >=0.40 | German public holiday detection for calendar features | Only if holiday feature improves forecast accuracy. ~50KB pure Python, no C extensions. aarch64 safe. Optional -- can start without it and add later. |

**Note:** `holidays` is the only potentially new dependency, and it is optional. All other functionality uses existing deps.

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `pytest` (existing) | Test ML models with synthetic data | Already configured. Use `pytest-mock` for mocking InfluxDB/HA responses. |
| `sklearn.model_selection.cross_val_score` | Validate model quality during development | Built into sklearn. Use 5-fold time-series split for honest evaluation. |
| `sklearn.model_selection.TimeSeriesSplit` | Proper CV for time-series data | Never use random k-fold on time-series. TimeSeriesSplit respects temporal ordering. |

## Installation

```bash
# No new core dependencies needed. Existing pyproject.toml is sufficient.
# The only optional addition:
pip install holidays>=0.40  # Only if German holiday features prove valuable
```

**pyproject.toml change (if holidays added):**
```toml
dependencies = [
    # ... existing ...
    "holidays>=0.40",  # German public holiday calendar features (optional but lightweight)
]
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| sklearn `HistGradientBoostingRegressor` | LightGBM 4.6+ | Never for this project. LightGBM adds ~50MB to Docker image, requires `cmake` + `build-base` at build time on aarch64. Performance gain is negligible at <10K samples. Only consider if training dataset grows to >100K rows (unlikely for household EMS). |
| sklearn `IsolationForest` | PyOD (Python Outlier Detection) | Never for this project. PyOD pulls in torch/tensorflow as optional deps and has a heavy dependency tree. IsolationForest covers our use case. |
| sklearn `GaussianProcessRegressor` for tuning | Optuna hyperparameter optimization | Only if parameter space exceeds 10 dimensions. Optuna adds a dependency (sqlite, sqlalchemy). For 3-5 control parameters, manual grid search or GP is sufficient. |
| `joblib` for persistence | `skops` (secure persistence) | Only if model files are loaded from untrusted sources. Our models are self-trained and self-loaded in the same container. Security benefit is irrelevant. skops adds a dependency. |
| `numpy` z-score for consumption anomaly | sklearn `IsolationForest` | Only if simple statistical detection produces too many false positives. Start simple, upgrade if needed. |
| Daily batch retraining | River (online learning) | Never for this project. River is a separate framework with different API. Daily batch retraining on 90 days of data takes <2 seconds. Online learning adds complexity with no benefit at this scale. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| TensorFlow / PyTorch | Massive dependencies (>500MB), GPU-oriented, overkill for tabular data with <10K samples. Would not fit in HA Add-on image budget. | sklearn gradient boosting |
| LightGBM / XGBoost | Adds C++ build complexity on aarch64 Alpine. No performance benefit at this data scale. Marginal accuracy improvement not worth the operational cost. | sklearn `HistGradientBoostingRegressor` (same histogram-binning approach, built into sklearn) |
| pandas | Not needed. Feature matrices are small enough for pure numpy arrays. Pandas adds ~30MB and import-time overhead on every control cycle restart. | numpy arrays + Python lists |
| Prophet / NeuralProphet | Designed for univariate time-series with strong seasonality. Our problem is multivariate (weather, calendar, device state). Also heavy deps (cmdstanpy or PyTorch). | sklearn GBR with engineered features |
| ONNX Runtime | Model serving framework for production ML pipelines. Our models are trained and served in the same Python process. No serialization format benefit. | joblib persistence |
| MLflow / Weights & Biases | Experiment tracking platforms. Massive overkill for a single-model HA Add-on. | Simple JSON metadata sidecar files |
| `dask-ml` / distributed training | The entire dataset fits in ~1MB of RAM. Distributed training adds complexity with zero benefit. | Single-process sklearn `fit()` |

## Stack Patterns by Variant

**If forecast accuracy is poor with GBR (>25% MAPE):**
- Switch to `HistGradientBoostingRegressor` with `early_stopping=True`
- Add more weather features from Open-Meteo (humidity, pressure, wind)
- Increase training window from 90 to 180 days
- All still within existing sklearn

**If anomaly detection has too many false positives:**
- Increase `contamination` threshold in IsolationForest (default 0.05 -> 0.01)
- Add a confirmation window: only alert if anomaly persists for 3+ consecutive readings
- Fall back to pure statistical approach (rolling z-score)

**If self-tuning diverges or causes oscillation:**
- Implement hard safety bounds that cannot be overridden
- Require N days of stable operation before applying new parameters
- Keep a "known-good" parameter snapshot for emergency rollback
- Limit parameter changes to max 10% per tuning cycle

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| scikit-learn >=1.4,<2 | numpy >=1.25,<3 | Already validated in pyproject.toml. sklearn 1.8 supports Python 3.11-3.14. |
| scikit-learn >=1.4 | joblib (bundled) | joblib ships with sklearn. No separate version pin needed. |
| scikit-learn >=1.4 | `HistGradientBoostingRegressor` | Available since sklearn 1.0, stable API since 1.1. Safe to use. |
| scikit-learn >=1.4 | `IsolationForest` | Available since sklearn 0.18, stable API. `warm_start` added in 0.21. |
| holidays >=0.40 | Python 3.12+ | Pure Python, no C extensions, no numpy dependency. |
| Alpine 3.21 + openblas-dev | scikit-learn wheels | Already validated in current Dockerfile. No additional system packages needed. |

## Memory and Performance Budget (aarch64 constraints)

| Operation | Expected Time | Memory | Frequency |
|-----------|--------------|--------|-----------|
| Consumption model training (90 days, ~2160 samples) | <1 second | ~5 MB peak | Nightly |
| Anomaly model training (IsolationForest, ~2160 samples) | <0.5 second | ~3 MB peak | Nightly |
| Parameter tuning grid search (50 configurations scored) | <2 seconds | ~2 MB peak | Nightly |
| Single prediction (all models) | <5 ms | ~1 MB (model in memory) | Per control cycle (~5s) |
| Model persistence (joblib dump) | <100 ms | Negligible | After each retrain |
| Total model files on disk | - | ~2 MB | Persistent |

These numbers are well within HA Add-on resource constraints. The entire ML pipeline adds <10 seconds to the nightly scheduler run and <5ms per control cycle for inference.

## Integration Points with Existing Code

| Existing Component | How ML Integrates | Change Required |
|-------------------|-------------------|-----------------|
| `ConsumptionForecaster` | Upgrade features, add HistGBR option, add persistence | Extend existing class |
| `WeatherScheduler` | Consume improved forecasts, pass weather data to forecaster | Wire weather features into forecaster |
| `Orchestrator` | Consume tuned parameters, feed metrics to anomaly detector | Add anomaly check in control loop |
| `InfluxDB writer/reader` | Source training data for anomaly detection and tuning scoring | Add queries for setpoint reversal count, error rates |
| `HA Statistics Reader` | Source training data (existing) | No change needed |
| `/api/` endpoints | Expose model status, anomaly alerts, tuning history | New REST endpoints |
| `Notifier` (Telegram) | Send anomaly alerts | New notification type |
| `config.py` | Add ML config dataclass (model_dir, retrain_interval, safety_bounds) | New config section |

## Sources

- [scikit-learn 1.8.0 documentation -- Model persistence](https://scikit-learn.org/stable/model_persistence.html) -- joblib best practices, versioning guidance
- [scikit-learn 1.8.0 -- HistGradientBoostingRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html) -- native NaN support, early stopping, warm_start
- [scikit-learn 1.8.0 -- IsolationForest](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html) -- anomaly detection API, contamination parameter
- [scikit-learn 1.8.0 -- Novelty and Outlier Detection](https://scikit-learn.org/stable/modules/outlier_detection.html) -- IsolationForest vs LOF comparison
- [scikit-learn 1.8.0 -- Speeding up gradient boosting](https://inria.github.io/scikit-learn-mooc/python_scripts/ensemble_hist_gradient_boosting.html) -- HistGBR vs GBR speed comparison
- [LightGBM PyPI](https://pypi.org/project/lightgbm/) -- aarch64 wheel availability confirmed for 4.6.0 (Feb 2025), but deemed unnecessary
- [scikit-learn Release Highlights 1.8](https://scikit-learn.org/stable/auto_examples/release_highlights/plot_release_highlights_1_8_0.html) -- Array API support, Python 3.14 compatibility

---
*Stack research for: EMS v1.3 ML Self-Tuning*
*Researched: 2026-03-23*
