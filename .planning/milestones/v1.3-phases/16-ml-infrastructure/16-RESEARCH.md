# Phase 16: ML Infrastructure - Research

**Researched:** 2026-03-23
**Domain:** ML model persistence, feature extraction pipeline, non-blocking training infrastructure
**Confidence:** HIGH

## Summary

Phase 16 builds the foundational infrastructure that all subsequent ML phases (17-19) depend on. The existing codebase already has a working `ConsumptionForecaster` using scikit-learn `GradientBoostingRegressor`, but it lacks model persistence (retrained from scratch every startup), runs `.fit()` synchronously on the async event loop, and has no centralized feature extraction. This phase creates three new modules (`ModelStore`, `FeaturePipeline`, and a training executor wrapper) and sets the `OMP_NUM_THREADS=2` environment variable in the Docker image.

The critical insight is that **zero new dependencies are needed**. `joblib` ships bundled with scikit-learn (already in `pyproject.toml` as `scikit-learn>=1.4,<2`), JSON metadata sidecars use the stdlib `json` module, and `asyncio.get_event_loop().run_in_executor()` is stdlib. The existing `anyio.to_thread.run_sync` pattern used in `HaStatisticsReader` provides a proven template for offloading blocking work.

**Primary recommendation:** Create `backend/model_store.py` for joblib persistence with sklearn version tracking, `backend/feature_pipeline.py` for cached feature extraction from InfluxDB + HA statistics, and wrap all `.fit()` calls with `run_in_executor`. Set `OMP_NUM_THREADS=2` in both `Dockerfile` and `ha-addon/run.sh`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
None -- auto-generated infrastructure phase with all decisions at Claude's discretion.

### Claude's Discretion
All implementation choices are at Claude's discretion -- pure infrastructure phase. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

### Deferred Ideas (OUT OF SCOPE)
None.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INFRA-01 | ModelStore persists trained models with joblib, tracks sklearn version, discards on version mismatch | joblib (bundled with sklearn) for persistence; `sklearn.__version__` comparison in JSON sidecar; `InconsistentVersionWarning` from sklearn docs confirms cross-version loading is unsupported |
| INFRA-02 | FeaturePipeline extracts training features from InfluxDB and HA statistics in a single cached read | Existing `HaStatisticsReader.read_entity_hourly()` and `InfluxMetricsReader.query_range()` provide data sources; new module caches results in memory for nightly batch |
| INFRA-03 | All sklearn .fit() calls wrapped in run_in_executor to avoid blocking the event loop | `asyncio.get_event_loop().run_in_executor(None, ...)` or `anyio.to_thread.run_sync()` (already used in `ha_statistics_reader.py`); ProcessPoolExecutor avoided due to serialization overhead |
| INFRA-04 | OMP_NUM_THREADS=2 set in Dockerfile/run.sh for aarch64 thread safety | ENV directive in Dockerfile + export in run.sh; also set OPENBLAS_NUM_THREADS=2 for numpy |
| INFRA-05 | Model directory at /config/ems_models/ with JSON metadata sidecars for each model | `/config/` is the HA Add-on persistent volume; JSON sidecar stores sklearn version, training timestamp, sample count, feature names |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Graceful degradation**: ModelStore and FeaturePipeline must be optional -- system runs without them (falls back to in-memory retrain)
- **Safety**: Never crash on model load failure -- discard and retrain
- **No cloud**: All model storage is local to `/config/ems_models/`
- **HA Add-on**: Must work on aarch64/amd64, Docker container
- **Python conventions**: `snake_case` files, `PascalCase` classes, `from __future__ import annotations`, type hints on all signatures, `logger = logging.getLogger(__name__)`, 4-space indent, 88-char lines
- **Config pattern**: Dataclass with `@classmethod from_env()` reading `os.environ`
- **Error handling**: Explicit exceptions, fire-and-forget for optional integrations, WARNING log + swallow
- **Test required**: `tests/test_*.py` with `pytest` + `anyio`

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| scikit-learn | >=1.4,<2 (existing) | ML models, training | Already in pyproject.toml; provides GBR, HistGBR, IsolationForest |
| joblib | bundled with sklearn | Model serialization to disk | Standard sklearn persistence method; handles numpy arrays efficiently |
| numpy | >=1.25,<3 (existing) | Feature arrays, numerical ops | Already in pyproject.toml |
| json (stdlib) | N/A | Metadata sidecar files | No dependency needed for version tracking |
| asyncio (stdlib) | N/A | run_in_executor for non-blocking training | No dependency needed |
| anyio | existing (dev dep) | to_thread.run_sync alternative | Already used in ha_statistics_reader.py |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| joblib | pickle | joblib is optimized for numpy arrays, more efficient for sklearn models |
| joblib | skops.io | More secure but adds a new dependency; joblib is sufficient for trusted local models |
| ThreadPoolExecutor | ProcessPoolExecutor | Process pool avoids GIL but adds serialization overhead; sklearn releases GIL during training anyway |
| anyio.to_thread | asyncio.to_thread | anyio already used in project; provides trio compatibility for tests |

**Installation:** No new packages needed. Everything is already in `pyproject.toml`.

## Architecture Patterns

### Recommended Project Structure
```
backend/
  model_store.py        # NEW — joblib persistence + version metadata
  feature_pipeline.py   # NEW — cached feature extraction from InfluxDB + HA stats
  consumption_forecaster.py  # MODIFIED — use ModelStore + FeaturePipeline + executor
  config.py             # MODIFIED — add ModelStoreConfig dataclass
  main.py               # MODIFIED — wire ModelStore, set model dir
  ha_statistics_reader.py  # UNCHANGED — already async via anyio
  influx_reader.py      # UNCHANGED
tests/
  test_model_store.py   # NEW
  test_feature_pipeline.py  # NEW
```

### Pattern 1: ModelStore with JSON Sidecar
**What:** Each persisted model gets two files: `{name}.joblib` (the model) and `{name}.meta.json` (version metadata).
**When to use:** Any sklearn model that should survive restarts.
**Example:**
```python
# Source: scikit-learn model persistence docs + project conventions
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import sklearn

logger = logging.getLogger(__name__)

@dataclass
class ModelMetadata:
    """Version and training metadata for a persisted model."""
    sklearn_version: str
    numpy_version: str
    trained_at: str  # ISO 8601
    sample_count: int
    feature_names: list[str]

class ModelStore:
    def __init__(self, model_dir: str) -> None:
        self._dir = Path(model_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, model: Any, metadata: ModelMetadata) -> None:
        model_path = self._dir / f"{name}.joblib"
        meta_path = self._dir / f"{name}.meta.json"
        joblib.dump(model, model_path)
        meta_path.write_text(json.dumps(vars(metadata), indent=2))
        logger.info("ModelStore: saved %s (sklearn=%s, samples=%d)",
                     name, metadata.sklearn_version, metadata.sample_count)

    def load(self, name: str) -> tuple[Any, ModelMetadata] | None:
        model_path = self._dir / f"{name}.joblib"
        meta_path = self._dir / f"{name}.meta.json"
        if not model_path.exists() or not meta_path.exists():
            return None
        try:
            raw = json.loads(meta_path.read_text())
            meta = ModelMetadata(**raw)
            if meta.sklearn_version != sklearn.__version__:
                logger.warning(
                    "ModelStore: discarding %s — sklearn version mismatch "
                    "(saved=%s, current=%s)",
                    name, meta.sklearn_version, sklearn.__version__,
                )
                self._remove(name)
                return None
            model = joblib.load(model_path)
            logger.info("ModelStore: loaded %s (trained_at=%s)", name, meta.trained_at)
            return model, meta
        except Exception as exc:
            logger.warning("ModelStore: failed to load %s: %s", name, exc)
            self._remove(name)
            return None

    def _remove(self, name: str) -> None:
        for suffix in (".joblib", ".meta.json"):
            path = self._dir / f"{name}{suffix}"
            path.unlink(missing_ok=True)
```

### Pattern 2: Non-Blocking Training with run_in_executor
**What:** Wrap synchronous sklearn `.fit()` calls in an executor to avoid blocking the async event loop.
**When to use:** Every `.fit()` call in the codebase.
**Example:**
```python
# Source: Python asyncio docs + existing anyio pattern in ha_statistics_reader.py
import asyncio
from functools import partial

async def train_in_background(model, X, y) -> None:
    """Run model.fit() in a thread pool executor."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, partial(model.fit, X, y))
```

**Alternative using anyio (matches existing codebase pattern):**
```python
import anyio.to_thread

async def train_in_background(model, X, y) -> None:
    await anyio.to_thread.run_sync(partial(model.fit, X, y))
```

### Pattern 3: FeaturePipeline with Cached Read
**What:** Single entry point that reads both HA statistics and InfluxDB, caches results for the nightly batch.
**When to use:** Before training any model in the nightly scheduler loop.
**Example:**
```python
class FeaturePipeline:
    """Cached feature extraction from HA statistics and InfluxDB."""

    def __init__(
        self,
        ha_reader: HaStatisticsReader | None,
        influx_reader: InfluxMetricsReader | None,
        config: HaStatisticsConfig,
    ) -> None:
        self._ha_reader = ha_reader
        self._influx_reader = influx_reader
        self._config = config
        self._cache: dict[str, list[tuple[datetime, float]]] | None = None
        self._cache_timestamp: datetime | None = None

    async def extract(self, *, force_refresh: bool = False) -> FeatureSet | None:
        """Extract features, caching for the current nightly batch."""
        if self._cache is not None and not force_refresh:
            age = (datetime.now(tz=timezone.utc) - self._cache_timestamp).total_seconds()
            if age < 3600:  # 1-hour cache validity
                return self._build_features_from_cache()
        # Read from both sources
        # ... (graceful degradation if either source unavailable)
```

### Anti-Patterns to Avoid
- **Calling model.fit() directly in an async method:** Blocks the event loop for 15-60s on aarch64, causing missed control cycles. Always use executor.
- **Loading a joblib model without version check:** sklearn models are NOT portable across versions. Always check the sidecar metadata first.
- **Caching model predictions across runs:** Products/consumption patterns change. Cache training data within a nightly batch, but never cache model outputs across batches.
- **Using ProcessPoolExecutor for training:** Serialization overhead of sending sklearn models and numpy arrays between processes is significant. ThreadPoolExecutor is sufficient because sklearn releases the GIL during C-level training operations.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Model serialization | Custom pickle wrapper | `joblib.dump` / `joblib.load` | Optimized for numpy arrays, handles large arrays with memory mapping |
| Version compatibility check | Parse sklearn source code | Compare `sklearn.__version__` string in JSON sidecar | Simple string comparison; sklearn officially says cross-version loading is unsupported |
| Non-blocking training | Thread management code | `asyncio.get_running_loop().run_in_executor(None, ...)` | stdlib handles thread pool lifecycle |
| Directory creation | Manual os.makedirs with error handling | `pathlib.Path.mkdir(parents=True, exist_ok=True)` | Idempotent, handles race conditions |

**Key insight:** The entire infrastructure layer uses stdlib + bundled sklearn utilities. No new dependencies, no complex integration. The hardest part is correctly wiring everything into the existing lifespan and nightly scheduler loop.

## Common Pitfalls

### Pitfall 1: Blocking the 5s Control Loop with Training
**What goes wrong:** `model.fit()` takes 15-60s on aarch64. If called from an async method without an executor, the entire event loop freezes -- no control commands, no WebSocket updates, no health checks.
**Why it happens:** The current `ConsumptionForecaster.train()` is an async method but calls `model.fit()` synchronously (line 276 in consumption_forecaster.py).
**How to avoid:** Wrap every `.fit()` call in `loop.run_in_executor(None, ...)`. The existing `HaStatisticsReader` uses `anyio.to_thread.run_sync()` for the same purpose -- follow that pattern.
**Warning signs:** WebSocket disconnections during nightly scheduler run, stale driver readings, missed control cycles in logs.

### Pitfall 2: sklearn Version Mismatch Crash on Add-on Update
**What goes wrong:** User updates HA Add-on, which ships a new sklearn version. `joblib.load()` of the old model either raises `InconsistentVersionWarning` and produces wrong predictions, or crashes with an unpickling error.
**Why it happens:** sklearn models are pickled Python objects. Internal class structure changes between versions.
**How to avoid:** Store `sklearn.__version__` in the JSON sidecar. On load, compare versions. If mismatched, delete the old model and retrain. Log a WARNING so the user knows why startup took longer.
**Warning signs:** `InconsistentVersionWarning` in logs, unexpected prediction values after an update.

### Pitfall 3: OpenMP Thread Oversubscription on aarch64
**What goes wrong:** sklearn/numpy spawn threads equal to CPU core count. In a Docker container on a Raspberry Pi 4 (4 cores), this creates 4 threads competing for limited resources, causing 3-10x slower training than expected.
**Why it happens:** OpenMP/OpenBLAS default to `nproc` threads. Containers see host CPU count, not cgroup limits.
**How to avoid:** Set `ENV OMP_NUM_THREADS=2` and `ENV OPENBLAS_NUM_THREADS=2` in the Dockerfile. Also set in `run.sh` as a fallback.
**Warning signs:** Training taking >120s on aarch64, high CPU usage during training with low throughput.

### Pitfall 4: Model Directory Not on Persistent Volume
**What goes wrong:** Models saved to `/app/` inside the container are lost on container restart. User must wait for full retrain every restart.
**Why it happens:** Only `/config/` (mapped to HA config volume) survives container restarts.
**How to avoid:** Use `/config/ems_models/` as the model directory. This path is on the HA persistent volume.
**Warning signs:** Models disappearing after Add-on restart, repeated "ModelStore: no saved model found" logs.

### Pitfall 5: FeaturePipeline Blocking When InfluxDB Is Unavailable
**What goes wrong:** FeaturePipeline tries to read InfluxDB, which hangs or throws, blocking the nightly scheduler.
**Why it happens:** InfluxDB is optional but the pipeline doesn't gracefully handle its absence.
**How to avoid:** Follow the existing graceful degradation pattern: `if influx_reader is None: return features_from_ha_only`. Use fire-and-forget with WARNING log. The FeaturePipeline must produce usable features from HA statistics alone (InfluxDB is a bonus source).
**Warning signs:** Nightly scheduler log showing "feature extraction failed" or no log at all (hung).

## Code Examples

### ModelStore Config Dataclass
```python
# Source: existing config.py pattern
@dataclass
class ModelStoreConfig:
    """Configuration for ML model persistence.

    Attributes:
        model_dir: Directory for persisted models (default /config/ems_models/).
        enabled: Whether model persistence is active.
    """
    model_dir: str = "/config/ems_models"
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "ModelStoreConfig":
        model_dir = os.environ.get("EMS_MODEL_DIR", "/config/ems_models")
        return cls(
            model_dir=model_dir,
            enabled=bool(model_dir),
        )
```

### Wiring ModelStore into Lifespan
```python
# Source: existing main.py lifespan pattern
# In lifespan(), after consumption_forecaster initialization:
model_store_cfg = ModelStoreConfig.from_env()
model_store: ModelStore | None = None
if model_store_cfg.enabled:
    try:
        from backend.model_store import ModelStore
        model_store = ModelStore(model_store_cfg.model_dir)
        logger.info("ModelStore configured — dir=%s", model_store_cfg.model_dir)
    except Exception as exc:
        logger.warning("ModelStore failed to initialize: %s", exc)
        model_store = None
```

### Modifying ConsumptionForecaster.train() for Executor
```python
# Source: existing ha_statistics_reader.py anyio pattern
async def train(self) -> None:
    # ... existing data fetching code (already async) ...

    # Offload CPU-bound training to thread pool
    import anyio.to_thread
    from functools import partial

    hp_model = GradientBoostingRegressor(n_estimators=100, max_depth=3, random_state=42)
    await anyio.to_thread.run_sync(partial(hp_model.fit, X, y_hp))
    self._heat_pump_model = hp_model

    # Persist if model_store is available
    if self._model_store is not None:
        self._model_store.save("heat_pump", hp_model, ModelMetadata(
            sklearn_version=sklearn.__version__,
            numpy_version=numpy.__version__,
            trained_at=datetime.now(tz=timezone.utc).isoformat(),
            sample_count=len(y_hp),
            feature_names=["outdoor_temp_c", "ewm_temp_3d", "day_of_week", "hour_of_day", "month"],
        ))
```

### OMP_NUM_THREADS in Dockerfile
```dockerfile
# Source: scikit-learn GitHub issue #15824
# Add after the FROM line, before RUN apk:
ENV OMP_NUM_THREADS=2
ENV OPENBLAS_NUM_THREADS=2
```

### OMP_NUM_THREADS in run.sh
```bash
# Add near the top of run.sh, after set -e:
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-2}"
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with anyio |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `python -m pytest tests/test_model_store.py tests/test_feature_pipeline.py -q` |
| Full suite command | `python -m pytest tests/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INFRA-01 | ModelStore save/load with version check, discard on mismatch | unit | `python -m pytest tests/test_model_store.py -x` | Wave 0 |
| INFRA-02 | FeaturePipeline extracts + caches features | unit | `python -m pytest tests/test_feature_pipeline.py -x` | Wave 0 |
| INFRA-03 | .fit() runs in executor, does not block event loop | unit | `python -m pytest tests/test_consumption_forecaster.py -x` | Existing (needs new test case) |
| INFRA-04 | OMP_NUM_THREADS set in Docker image | smoke | `grep OMP_NUM_THREADS Dockerfile ha-addon/run.sh` | Manual verification |
| INFRA-05 | Models saved to /config/ems_models/ with JSON sidecars | unit | `python -m pytest tests/test_model_store.py -x` | Wave 0 (same as INFRA-01) |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_model_store.py tests/test_feature_pipeline.py tests/test_consumption_forecaster.py -q`
- **Per wave merge:** `python -m pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_model_store.py` -- covers INFRA-01, INFRA-05
- [ ] `tests/test_feature_pipeline.py` -- covers INFRA-02
- [ ] New test case in `tests/test_consumption_forecaster.py` for executor offload verification -- covers INFRA-03

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `pickle.dump` for sklearn models | `joblib.dump` (optimized for numpy) | sklearn 0.21+ | Better memory mapping, faster for large arrays |
| `asyncio.run_in_executor` | `anyio.to_thread.run_sync` | anyio 3.0+ | Framework-agnostic, cleaner API, already used in project |
| No version tracking | `InconsistentVersionWarning` in sklearn | sklearn 1.3+ | Official detection of version mismatches |
| `skops.io` for secure persistence | Still experimental | sklearn 1.3+ | More secure but adds dependency; joblib sufficient for trusted local models |

**Deprecated/outdated:**
- `sklearn.externals.joblib`: Removed in sklearn 0.23. Import `joblib` directly.
- `pickle` for sklearn models: Works but lacks numpy optimizations. Use `joblib`.

## Open Questions

1. **Training timeout value for aarch64**
   - What we know: Research estimates 15-60s for GBR training with 90 days of hourly data on RPi4.
   - What's unclear: Actual training time on the target hardware with real data volume.
   - Recommendation: Start with 120s timeout. Log actual training duration. Adjust in Phase 17 based on measurements.

2. **FeaturePipeline cache invalidation strategy**
   - What we know: Cache is only used within a single nightly batch run.
   - What's unclear: Whether multiple models (consumption, anomaly, tuner) will call FeaturePipeline within the same nightly loop iteration.
   - Recommendation: Use a 1-hour TTL cache. All nightly models should extract features once at the start of the nightly loop, then train using the cached data.

## Sources

### Primary (HIGH confidence)
- [scikit-learn 1.8.0 model persistence docs](https://scikit-learn.org/stable/model_persistence.html) -- joblib usage, version mismatch warnings, cross-version loading unsupported
- [Python asyncio documentation](https://docs.python.org/3/library/asyncio-dev.html) -- run_in_executor best practices
- Existing codebase: `backend/ha_statistics_reader.py` (anyio.to_thread pattern), `backend/consumption_forecaster.py` (current training code), `backend/main.py` (lifespan wiring pattern), `backend/config.py` (dataclass config pattern)

### Secondary (MEDIUM confidence)
- [scikit-learn GitHub issue #15824](https://github.com/scikit-learn/scikit-learn/issues/15824) -- aarch64 OpenMP oversubscription
- Project research: `.planning/research/SUMMARY.md` -- ML self-tuning research synthesis

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- zero new dependencies, all patterns verified in existing codebase
- Architecture: HIGH -- direct extension of existing patterns (config dataclass, optional integration, setter injection)
- Pitfalls: HIGH -- identified from direct codebase analysis (synchronous .fit() on line 276, missing /config/ path, no version tracking)

**Research date:** 2026-03-23
**Valid until:** 2026-04-23 (stable domain, no fast-moving dependencies)
