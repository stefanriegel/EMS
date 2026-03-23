# Architecture Patterns

**Domain:** ML self-tuning integration for dual-battery Energy Management System
**Researched:** 2026-03-23

## Recommended Architecture

The ML features integrate into the existing EMS as three loosely-coupled subsystems that share a common feature pipeline and model storage layer. No existing component needs a rewrite -- the ConsumptionForecaster gets an in-place upgrade, and the two new subsystems (self-tuner, anomaly detector) plug into existing injection points.

```
                    HA SQLite DB          InfluxDB (optional)
                         |                      |
                         v                      v
                  HaStatisticsReader    InfluxMetricsReader
                         |                      |
                         +------+-------+-------+
                                |
                         FeaturePipeline (NEW)
                           /    |       \
                          v     v        v
                   ConsumptionForecaster   SelfTuner   AnomalyDetector
                   (UPGRADED)              (NEW)       (NEW)
                          |                   |              |
                          v                   v              v
                   WeatherScheduler     Coordinator     Coordinator
                   (nightly batch)      (nightly)       (per-cycle hook)
```

### Component Boundaries

| Component | Responsibility | Communicates With | New/Modified |
|-----------|---------------|-------------------|--------------|
| **FeaturePipeline** | Extract, align, and cache training features from HA SQLite and InfluxDB | HaStatisticsReader, InfluxMetricsReader | NEW |
| **ConsumptionForecaster** | Predict hourly consumption with weather, day-of-week, seasonal features | FeaturePipeline, WeatherScheduler | MODIFIED (upgrade) |
| **SelfTuner** | Adjust control parameters (dead-bands, ramp rates, min-SoC) from historical performance | FeaturePipeline, Coordinator config, InfluxDB decision logs | NEW |
| **AnomalyDetector** | Flag unusual consumption, hardware drift, driver stalls per cycle | Coordinator snapshots, FeaturePipeline | NEW |
| **ModelStore** | Persist/load trained sklearn models with versioning metadata | All ML components | NEW |
| **Coordinator** | 5s control loop (existing) -- gains anomaly hook and tuned parameters | AnomalyDetector, SelfTuner | MODIFIED (minor) |
| **WeatherScheduler** | Nightly charge scheduling (existing) -- consumes upgraded forecasts | ConsumptionForecaster | UNCHANGED (interface) |
| **main.py lifespan** | Wiring and lifecycle (existing) -- gains ML component init | All new components | MODIFIED |
| **_nightly_scheduler_loop** | Nightly batch (existing) -- gains self-tuner and anomaly model retrain calls | SelfTuner, AnomalyDetector | MODIFIED |

### Data Flow

#### Training Pipeline (Nightly Batch)

Runs alongside the existing `_nightly_scheduler_loop` at the configurable `SCHEDULER_RUN_HOUR` (default 23:00). The existing loop already calls `consumption_forecaster.retrain_if_stale()` -- the new components follow the same pattern.

```
1. _nightly_scheduler_loop fires
2. FeaturePipeline.refresh() -- reads last 90 days from HA SQLite + InfluxDB
3. ConsumptionForecaster.train() -- uses FeaturePipeline cached features
4. SelfTuner.evaluate_and_adjust() -- reads InfluxDB decision logs, computes parameter deltas
5. AnomalyDetector.retrain() -- fits Isolation Forest on recent feature distributions
6. ModelStore.save_all() -- persists all models to disk
7. WeatherScheduler.compute_schedule() -- uses upgraded forecasts
```

#### Inference Pipeline (Per-Cycle, 5s)

The anomaly detector runs in the coordinator's existing `_loop()` method, after `_run_cycle()` and `_run_export_advisory()`. It must be non-blocking (< 50ms).

```
Coordinator._loop():
  1. _run_cycle()              -- existing: poll, decide, execute
  2. _run_export_advisory()    -- existing: export/store decision
  3. _run_anomaly_check()      -- NEW: lightweight per-cycle anomaly scoring
```

The anomaly check receives the two `ControllerSnapshot` objects already produced by `_run_cycle()` -- no additional hardware polling needed.

#### Forecast Inference (On-Demand)

ConsumptionForecaster inference is already called on-demand by WeatherScheduler. The upgraded forecaster returns the same `ConsumptionForecast` / `HourlyConsumptionForecast` dataclasses -- no interface change.

## New Components -- Detailed Design

### 1. FeaturePipeline (`backend/feature_pipeline.py`)

**What:** Centralized feature extraction and alignment layer that all ML components share. Currently, `ConsumptionForecaster._build_features()` does inline feature extraction. This gets extracted into a shared pipeline.

**Why:** Avoids duplicating HA SQLite reads and InfluxDB queries across three ML components. Caches aligned feature matrices in memory for the nightly training batch.

```python
@dataclass
class FeatureSet:
    """Aligned feature matrix with timestamps and labels."""
    timestamps: list[datetime]
    X: list[list[float]]  # [outdoor_temp, ewm_temp, day_of_week, hour, month, ...]
    y_heat_pump: list[float]
    y_dhw: list[float]
    y_base: list[float]
    # NEW features for v1.3:
    y_grid_power: list[float]       # from InfluxDB ems_system
    y_battery_power: list[float]    # from InfluxDB ems_system
    coordinator_decisions: list[dict]  # from InfluxDB ems_decision
    days_of_history: int

class FeaturePipeline:
    def __init__(self, ha_reader, influx_reader, config):
        ...
    async def refresh(self, days: int = 90) -> FeatureSet:
        """Fetch and align all data sources. Cache result."""
        ...
    def get_cached(self) -> FeatureSet | None:
        """Return last refresh result without re-fetching."""
        ...
```

**Integration point:** Injected into ConsumptionForecaster, SelfTuner, and AnomalyDetector at construction time in `main.py` lifespan. InfluxDB data is optional -- when InfluxDB is disabled, only HA SQLite features are available (graceful degradation).

### 2. Upgraded ConsumptionForecaster (`backend/consumption_forecaster.py`)

**What:** Replace the existing ConsumptionForecaster in-place. Same class name, same public interface (`train()`, `query_consumption_history()`, `predict_hourly()`, `retrain_if_stale()`), but with improved internals.

**Changes from current implementation:**

| Aspect | Current | Upgraded |
|--------|---------|---------|
| Features | 5 features: outdoor_temp, ewm_temp, day_of_week, hour, month | 8+ features: add solar_generation, grid_import, cloud_cover (from Open-Meteo) |
| Temperature | Neutral 10C placeholder during inference | Real forecast from Open-Meteo weather_client (already available in lifespan) |
| Base load model | Constant 300W placeholder | Actual base load from HA statistics grid consumption entity |
| Model type | GradientBoostingRegressor | Same (GBR) -- proven, fast, interpretable |
| Feature pipeline | Inline in train() | Delegates to FeaturePipeline |
| Model persistence | None (retrain from scratch every 24h) | ModelStore (load on startup, retrain nightly) |

**Why NOT change the model type:** GradientBoostingRegressor is the right choice for this problem. The data is tabular, the dataset is small (90 days * 24h = 2160 samples), and GBR handles missing features gracefully via tree splits. Neural networks would overfit. The win comes from better features, not a fancier model.

**Interface preservation:** The Scheduler and WeatherScheduler call `query_consumption_history()` and `predict_hourly()` -- both return the same dataclasses (`ConsumptionForecast`, `HourlyConsumptionForecast`). Zero changes needed downstream.

### 3. SelfTuner (`backend/self_tuner.py`)

**What:** Analyzes historical coordinator decisions and battery behavior to recommend parameter adjustments for dead-bands, ramp rates, and min-SoC profiles.

**Integration point:** Runs nightly in `_nightly_scheduler_loop`. Writes adjusted parameters to the Coordinator via an existing-pattern setter method.

```python
@dataclass
class TuningRecommendation:
    """Parameter adjustment recommendation with reasoning."""
    parameter: str          # e.g., "huawei_deadband_w"
    current_value: float
    recommended_value: float
    confidence: float       # 0.0-1.0
    reasoning: str
    applied: bool = False

class SelfTuner:
    def __init__(self, feature_pipeline, model_store, config):
        ...

    async def evaluate_and_adjust(
        self, coordinator: Coordinator
    ) -> list[TuningRecommendation]:
        """Analyze last 7 days of decisions, recommend parameter changes.

        Only applies changes when confidence > threshold (default 0.7).
        All changes are bounded within safe limits defined in config.
        """
        ...
```

**What it tunes (with safe bounds):**

| Parameter | Current Default | Min Bound | Max Bound | Tuning Signal |
|-----------|----------------|-----------|-----------|---------------|
| `huawei_deadband_w` | 300 | 100 | 500 | Oscillation count in decision log |
| `victron_deadband_w` | 150 | 50 | 300 | Oscillation count in decision log |
| `huawei_ramp_w_per_cycle` | 2000 | 500 | 3000 | Overshoot frequency |
| `victron_ramp_w_per_cycle` | 1000 | 300 | 2000 | Overshoot frequency |
| `min_soc_*` profiles | Static per-system | 10% | 50% | Overnight depletion events |

**How it works:** The SelfTuner reads InfluxDB `ems_decision` measurements for the last 7 days. It counts oscillation events (rapid role flips within 30s), overshoot events (setpoint exceeded by >10%), and depletion events (SoC hitting min bound). If oscillations are high, it widens dead-bands. If overshoots are frequent, it reduces ramp rates. If depletions occur, it raises min-SoC. All adjustments are small (max 10% change per night) and bounded within safe limits.

**Coordinator integration:** The Coordinator already has instance attributes for these parameters (`_huawei_deadband_w`, `_victron_deadband_w`, `_huawei_ramp_w_per_cycle`, `_victron_ramp_w_per_cycle`). The SelfTuner calls setter methods (new, simple) on the Coordinator to apply recommendations. Min-SoC changes go through the existing `sys_config` setter.

**Safety:** A `TunerConfig` dataclass defines absolute min/max bounds for each parameter. The SelfTuner never exceeds these bounds regardless of what the analysis suggests. All changes are logged at INFO level with full reasoning. A `revert_to_defaults()` method is exposed via the API and HA MQTT for manual override.

### 4. AnomalyDetector (`backend/anomaly_detector.py`)

**What:** Two-layer anomaly detection -- a lightweight per-cycle scorer and a heavier nightly retrain.

**Layer 1: Per-cycle scoring (runs every 5s in Coordinator._loop)**

Uses simple statistical thresholds computed from the last 24h of observations. No sklearn dependency on the hot path.

```python
@dataclass
class AnomalyScore:
    """Per-cycle anomaly assessment."""
    score: float              # 0.0 = normal, 1.0 = extreme anomaly
    flags: list[str]          # e.g., ["consumption_spike", "soc_drift"]
    requires_alert: bool      # True when score > alert_threshold

class AnomalyDetector:
    def score_cycle(
        self, h_snap: ControllerSnapshot, v_snap: ControllerSnapshot
    ) -> AnomalyScore:
        """Lightweight per-cycle check. Must complete in < 10ms."""
        ...
```

**What it detects per-cycle:**
- **Consumption spike:** Grid import > 3x rolling 1h mean
- **SoC drift:** Battery SoC dropping faster than physical discharge rate allows
- **Driver stall:** Available=True but power/SoC unchanged for >10 consecutive cycles (50s)
- **Communication loss:** Available=False for >3 consecutive cycles (15s) -- existing failure isolation handles safe state, anomaly detector adds alerting

**Layer 2: Nightly retrain (runs in _nightly_scheduler_loop)**

Fits an Isolation Forest on the FeaturePipeline's aligned feature matrix. Produces updated statistical baselines (rolling means, standard deviations) that Layer 1 uses for threshold computation.

```python
    async def retrain(self, feature_set: FeatureSet) -> None:
        """Nightly retrain: update baselines and Isolation Forest model."""
        ...
```

**Integration point in Coordinator:**

```python
# In Coordinator.__init__:
self._anomaly_detector: AnomalyDetector | None = None

def set_anomaly_detector(self, detector: AnomalyDetector) -> None:
    self._anomaly_detector = detector

# In Coordinator._loop():
async def _loop(self) -> None:
    while True:
        try:
            await self._run_cycle()
            await self._run_export_advisory()
            if self._anomaly_detector is not None:
                score = self._anomaly_detector.score_cycle(
                    self._last_h_snap, self._last_v_snap
                )
                if score.requires_alert and self._notifier is not None:
                    await self._notifier.send(
                        f"Anomaly detected: {', '.join(score.flags)} (score={score.score:.2f})"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Coordinator cycle error: %s", exc, exc_info=True)
        await asyncio.sleep(self._cfg.loop_interval_s)
```

**Alert suppression:** To avoid alert storms, the detector enforces a cooldown period (default 15 minutes) between alerts for the same flag type.

### 5. ModelStore (`backend/model_store.py`)

**What:** Persists trained sklearn models and metadata to the HA Add-on filesystem.

**Storage location:** `/config/ems_models/` in HA Add-on context (same persistent volume as `ems_config.json`). Falls back to `./models/` in development.

```python
@dataclass
class ModelMetadata:
    """Metadata stored alongside each persisted model."""
    model_name: str
    sklearn_version: str
    trained_at: datetime
    training_samples: int
    train_rmse: float | None
    feature_names: list[str]

class ModelStore:
    def __init__(self, base_dir: str):
        ...

    def save(self, name: str, model, metadata: ModelMetadata) -> None:
        """Save model + metadata using joblib (pickle protocol 5)."""
        ...

    def load(self, name: str) -> tuple[Any, ModelMetadata] | None:
        """Load model + metadata. Returns None if not found or version mismatch."""
        ...

    def save_all(self, models: dict[str, tuple[Any, ModelMetadata]]) -> None:
        """Atomic save of all models (write to temp, rename)."""
        ...
```

**Persistence format:** `joblib` with `protocol=5` (reduced memory, good numpy array handling). Each model gets two files: `{name}.joblib` and `{name}.meta.json`. The metadata JSON includes the sklearn version -- on load, if versions differ, the model is discarded and retrained from scratch (sklearn models are not forward/backward compatible).

**Why joblib over skops:** skops provides better security but adds a dependency and is slower. Since all models are trained and loaded locally (never from untrusted sources), joblib's security concern is irrelevant here. joblib is already an implicit dependency of scikit-learn.

**Startup behavior:** On application start (in lifespan), ModelStore attempts to load persisted models. If found and version-compatible, ConsumptionForecaster skips the initial `train()` call -- startup is faster. If not found, training runs as before.

## Patterns to Follow

### Pattern 1: Dependency Injection via Setter Methods

**What:** All new ML components are injected into the Coordinator via setter methods, following the established pattern used by ExportAdvisor, HA MQTT, EVCC monitor, and Telegram notifier.

**When:** Always. No ML component should be constructed inside the Coordinator.

**Example:**
```python
# In main.py lifespan, after coordinator construction:
anomaly_detector = AnomalyDetector(feature_pipeline, model_store, anomaly_cfg)
coordinator.set_anomaly_detector(anomaly_detector)

self_tuner = SelfTuner(feature_pipeline, model_store, tuner_cfg)
# SelfTuner is NOT injected into coordinator -- it pushes parameters TO coordinator nightly
```

**Why:** Keeps the Coordinator's constructor stable. Tests mock the ML components. Optional components gracefully degrade to None checks.

### Pattern 2: Graceful Degradation Chain

**What:** Every ML component has a fallback path. If HA SQLite is missing, use InfluxDB. If InfluxDB is missing, use seasonal constants. If sklearn is not installed, skip ML entirely.

**When:** Always. This system runs on constrained HA Add-on hardware where any external dependency may be absent.

**Example:**
```python
# FeaturePipeline graceful degradation:
async def refresh(self, days: int = 90) -> FeatureSet:
    ha_data = await self._try_ha_read(days)   # Primary
    influx_data = await self._try_influx_read(days)  # Optional enrichment

    if ha_data is None and influx_data is None:
        logger.warning("FeaturePipeline: no data sources available")
        return None  # Callers fall back to seasonal constants

    # Merge whatever is available
    return self._merge(ha_data, influx_data)
```

### Pattern 3: Nightly Batch with Per-Cycle Lightweight Hooks

**What:** Heavy computation (training, evaluation) runs once per night in the existing scheduler loop. Per-cycle operations are limited to simple threshold comparisons against pre-computed baselines.

**When:** For any ML feature that needs both training and inference.

**Why:** The 5s control cycle runs on a Raspberry Pi 4 (aarch64). A full sklearn `predict()` call takes 5-10ms on that hardware -- acceptable. A `train()` call takes 2-30s -- must be nightly only.

### Pattern 4: Bounded Parameter Changes

**What:** The SelfTuner never makes large parameter jumps. Each nightly adjustment is capped at a configurable percentage (default 10%) of the current value, and absolute bounds prevent dangerous configurations.

**When:** Always, for the SelfTuner.

**Why:** Unbounded auto-tuning can cause oscillation spirals. A system that slowly converges to good parameters over 7-14 days is far safer than one that makes dramatic changes overnight.

### Pattern 5: Config via Dataclass from_env()

**What:** All new configuration follows the established `@dataclass` with `@classmethod from_env()` pattern.

**When:** For every new config type.

**Example:**
```python
@dataclass
class AnomalyConfig:
    alert_threshold: float = 0.8
    cooldown_minutes: int = 15
    driver_stall_cycles: int = 10

    @classmethod
    def from_env(cls) -> AnomalyConfig:
        return cls(
            alert_threshold=float(os.environ.get("ANOMALY_ALERT_THRESHOLD", "0.8")),
            cooldown_minutes=int(os.environ.get("ANOMALY_COOLDOWN_MINUTES", "15")),
            driver_stall_cycles=int(os.environ.get("ANOMALY_STALL_CYCLES", "10")),
        )
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: ML in the Hot Path

**What:** Running sklearn `.predict()` or `.fit()` inside the 5s control cycle.
**Why bad:** Even a fast GBR predict takes 5-10ms. Three models = 15-30ms. That's 3-6% of the 5s budget on a Pi. More importantly, if the model is corrupted or sklearn throws, it could delay or crash the control cycle.
**Instead:** Per-cycle anomaly detection uses pre-computed thresholds (rolling mean/std), not sklearn inference. Model predictions happen only in the nightly batch or on-demand forecast calls (which are not time-critical).

### Anti-Pattern 2: Shared Mutable State Between ML and Control

**What:** Having the SelfTuner directly modify Coordinator's internal state dictionaries.
**Why bad:** Race conditions. The Coordinator runs in an asyncio task; the SelfTuner runs in the nightly scheduler task.
**Instead:** SelfTuner produces `TuningRecommendation` objects. The nightly loop applies them via Coordinator setter methods (which are thread-safe via the GIL for simple attribute assignments, matching the existing `sys_config` setter pattern).

### Anti-Pattern 3: Retraining on Every Cycle

**What:** Calling `train()` or `fit()` more than once per day.
**Why bad:** HA SQLite reads are expensive (full table scan). InfluxDB queries add network latency. On a Pi, training takes 2-30s.
**Instead:** Train once per night. Use `retrain_if_stale(stale_hours=24)` guard (already exists in ConsumptionForecaster).

### Anti-Pattern 4: Persisting Models Without Version Metadata

**What:** Saving a joblib model without recording the sklearn version.
**Why bad:** scikit-learn models are NOT forward/backward compatible. A model trained with sklearn 1.4 cannot be loaded with sklearn 1.5. HA Add-on updates may change the sklearn version.
**Instead:** ModelStore always saves `ModelMetadata` with `sklearn_version`. On load, version mismatch triggers discard + retrain.

### Anti-Pattern 5: Alerting Without Cooldown

**What:** Sending a Telegram alert every 5 seconds during sustained anomaly.
**Why bad:** Alert fatigue. Telegram rate limits. Battery on degraded state sends 720 alerts/hour.
**Instead:** AnomalyDetector enforces per-flag cooldown (default 15 min). First occurrence alerts immediately; subsequent occurrences within the cooldown window are logged but not alerted.

## Integration Changes to Existing Files

### `backend/main.py` -- Lifespan Additions

```python
# After existing ConsumptionForecaster construction (line ~374-398):

# --- Feature Pipeline (shared by all ML components) ---
feature_pipeline = FeaturePipeline(
    ha_reader=ha_stats_reader,      # may be None
    influx_reader=metrics_reader,   # may be None
    config=ha_stats_cfg,
)

# --- Model Store ---
model_store_dir = os.path.join(config_dir, "ems_models")
model_store = ModelStore(model_store_dir)

# --- Upgrade ConsumptionForecaster to use FeaturePipeline + ModelStore ---
# (ConsumptionForecaster constructor gains optional feature_pipeline + model_store args)
consumption_forecaster = ConsumptionForecaster(
    ha_stats_reader, ha_stats_cfg,
    feature_pipeline=feature_pipeline,
    model_store=model_store,
    weather_client=weather_client,
)

# --- Self-Tuner ---
tuner_cfg = TunerConfig.from_env()
self_tuner = SelfTuner(feature_pipeline, model_store, tuner_cfg)

# --- Anomaly Detector ---
anomaly_cfg = AnomalyConfig.from_env()
anomaly_detector = AnomalyDetector(feature_pipeline, model_store, anomaly_cfg)

# After coordinator construction:
coordinator.set_anomaly_detector(anomaly_detector)
app.state.self_tuner = self_tuner
app.state.anomaly_detector = anomaly_detector
```

### `backend/main.py` -- Nightly Loop Additions

```python
# In _nightly_scheduler_loop, after existing retrain_if_stale block:

# Self-tuner evaluation
if self_tuner is not None:
    try:
        recommendations = await self_tuner.evaluate_and_adjust(coordinator)
        logger.info("nightly-scheduler: self-tuner produced %d recommendations", len(recommendations))
    except Exception as exc:
        logger.warning("nightly-scheduler: self-tuner failed: %s", exc)

# Anomaly detector retrain
if anomaly_detector is not None and feature_pipeline is not None:
    try:
        feature_set = feature_pipeline.get_cached()
        if feature_set is not None:
            await anomaly_detector.retrain(feature_set)
            logger.info("nightly-scheduler: anomaly detector retrained")
    except Exception as exc:
        logger.warning("nightly-scheduler: anomaly detector retrain failed: %s", exc)
```

### `backend/coordinator.py` -- Minimal Changes

1. Add `_anomaly_detector` attribute (None by default)
2. Add `set_anomaly_detector()` setter method
3. Add `_run_anomaly_check()` call after `_run_export_advisory()` in `_loop()`
4. Add setter methods for tunable parameters: `set_deadband()`, `set_ramp_rate()`

Total diff: ~40 lines added to coordinator.py.

### `backend/api.py` -- New Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/ml/status` | GET | Model training status, last trained timestamps, sample counts |
| `/api/ml/anomalies` | GET | Recent anomaly scores and flags (ring buffer, last 100) |
| `/api/ml/tuning` | GET | Current tuning recommendations and applied adjustments |
| `/api/ml/tuning/revert` | POST | Revert all tuned parameters to defaults |
| `/api/ml/forecast-comparison` | GET | Predicted vs actual consumption (already partially exists) |

## Scalability Considerations

| Concern | Current (1 household) | Future (multiple households) |
|---------|----------------------|------------------------------|
| Training data volume | ~2160 samples (90 days) | Same per household -- local training |
| Training time | 2-30s on Pi 4 | Same -- no cross-household learning |
| Model storage | ~50 KB per model (5 models = 250 KB) | Same -- not a concern |
| Per-cycle overhead | < 1ms (threshold comparison only) | Same |
| InfluxDB queries | Single household metrics | Same -- scoped to local instance |

This system is explicitly single-household by design. There is no multi-tenant scaling concern.

## Build Order Recommendation

Based on dependency analysis, the build order should be:

1. **ModelStore + FeaturePipeline** -- Foundation that all other components need
2. **Upgraded ConsumptionForecaster** -- Highest value, improves existing nightly schedule quality immediately
3. **AnomalyDetector** -- Independent of SelfTuner, provides safety monitoring
4. **SelfTuner** -- Depends on stable InfluxDB decision logging (which already exists)
5. **API endpoints + Dashboard integration** -- Observability layer, last because it depends on all components existing

**Rationale:** The forecaster upgrade is the highest-value change (better schedules = real money saved). Anomaly detection is safety-critical and should come before self-tuning. Self-tuning is the most complex and benefits from having the other components stable first.

## Sources

- [scikit-learn Model Persistence Documentation](https://scikit-learn.org/stable/model_persistence.html) -- joblib/pickle best practices, version compatibility warnings
- [Hybrid ML framework for battery anomaly detection (Nature, 2025)](https://www.nature.com/articles/s41598-025-90810-w) -- Isolation Forest for battery health monitoring
- [Continual learning for EMS review (Applied Energy, 2025)](https://www.sciencedirect.com/science/article/pii/S0306261925001886) -- Catastrophic forgetting prevention in evolving energy systems
- Existing codebase: `backend/consumption_forecaster.py`, `backend/coordinator.py`, `backend/main.py`, `backend/weather_scheduler.py`
