# Pitfalls Research

**Domain:** ML self-tuning, anomaly detection, and forecasting for dual-battery EMS
**Researched:** 2026-03-23
**Confidence:** HIGH (based on existing codebase analysis, scikit-learn docs, InfluxDB docs, and published research)

## Critical Pitfalls

### Pitfall 1: Cold-Start ML Producing Worse Results Than the Seasonal Fallback

**What goes wrong:**
The existing `ConsumptionForecaster` has a 14-day minimum training threshold, but the v1.3 self-tuning features (dead-band optimization, ramp rate tuning, min-SoC profile learning) need much more data to produce safe recommendations. With 14-30 days of history, an ML model can learn spurious correlations (e.g., a two-week cold spell makes it think consumption is always high) and produce parameter suggestions that are objectively worse than the current hand-tuned defaults. The system ships defaults that already work -- `hysteresis_w=200`, `debounce_cycles=2`, `loop_interval_s=5.0` -- and an undertrained model could recommend values that cause oscillation or missed discharge opportunities.

**Why it happens:**
Developers treat "model trained" as "model ready for production." GradientBoostingRegressor will happily fit 14 days of data and report low training RMSE, but the model has never seen a season change, a holiday week, or a heat pump defrost cycle. The existing codebase already shows this risk: `_BASE_LOAD_W = 300.0` is a constant placeholder, and the neutral temperature fallback (`neutral_temp = 10.0`) means the ML path ignores actual weather conditions even when trained.

**How to avoid:**
- Define explicit graduation criteria: a self-tuned parameter only replaces the default when the model has seen at least 60 days of data spanning at least 2 distinct outdoor temperature regimes (warm/cold).
- Implement a shadow mode: ML-recommended parameters are logged alongside the actual parameters being used. Compare outcomes for at least 14 days before activating.
- Cap parameter adjustment range: a self-tuned hysteresis value must stay within [100W, 500W], ramp rates within [50W/s, 500W/s]. Never allow the ML to recommend values outside physically safe bounds.
- Keep the seasonal fallback as the permanent backstop. The `fallback_used=True` pattern in the existing `ConsumptionForecast` is correct -- extend it to all self-tuned parameters.

**Warning signs:**
- Model training succeeds but `days_of_history < 60`.
- Self-tuned parameters change by more than 30% from defaults in the first month.
- Forecast error percentage (`error_pct` from `get_forecast_comparison()`) exceeds 40% consistently.

**Phase to address:**
First phase of self-tuning implementation. Shadow mode must be built before any parameter is auto-applied.

---

### Pitfall 2: Self-Tuning Destabilizes the 5-Second Control Loop

**What goes wrong:**
The Coordinator runs a hard 5-second control loop (`_cfg.loop_interval_s = 5.0`) with carefully tuned anti-oscillation: 200W hysteresis dead-band, 2-cycle debounce, per-system ramp limiting. If self-tuning adjusts these parameters during operation, it can create resonance between the two independent battery controllers. For example: reducing Huawei dead-band to 100W while Victron stays at 150W can cause the two systems to alternate between CHARGING and DISCHARGING on successive cycles when load hovers near the threshold.

**Why it happens:**
The dual-battery architecture has coupled dynamics even though the controllers are logically independent -- they share the same grid meter reading. A parameter change to one system's control response affects the other system's behavior through the shared P_target calculation. Classical control theory calls this "unmodeled coupling," and ML optimizers that treat each parameter independently will miss it.

**How to avoid:**
- Never adjust control parameters mid-cycle or mid-hour. Apply parameter updates only at well-defined transition points (e.g., start of a new hour, after a HOLD period).
- Test parameter combinations in simulation before applying. Build a lightweight replay simulator that feeds historical grid meter data through the Coordinator with proposed parameters, counting oscillation events (role transitions per hour > threshold = reject).
- Enforce monotonic constraints: if the ML suggests reducing hysteresis for Huawei, it must also verify that the Victron hysteresis provides sufficient damping. The combined dead-band must never drop below 150W total.
- Add an oscillation detector to the coordinator: if role transitions exceed 6 per hour for either system, revert to default parameters and log a CRITICAL alert.

**Warning signs:**
- Role transition count per hour increasing after parameter change.
- Both batteries alternating between CHARGING and DISCHARGING within a 30-second window.
- Grid power oscillating (import/export flip-flop visible in InfluxDB metrics).
- `DecisionEntry` ring buffer showing rapid state changes with contradictory reasoning.

**Phase to address:**
Must be addressed in the self-tuning phase. The oscillation detector should be built first, before any parameter adjustment capability.

---

### Pitfall 3: Blocking the Control Loop with ML Inference

**What goes wrong:**
GradientBoostingRegressor `.predict()` on 100 trees with 5 features takes 0.5-2ms on amd64 but 5-15ms on aarch64 (Raspberry Pi 4 class hardware common in Home Assistant). Training `.fit()` on 90 days of hourly data (2160 samples, 100 trees) takes 2-8 seconds on amd64 and 15-60 seconds on aarch64. If training or batch prediction runs inside the coordinator's `_run_cycle()` or blocks the event loop, the 5-second control interval is violated. A missed control cycle means stale setpoints remain active, which is a safety issue for battery systems.

**Why it happens:**
The existing code imports sklearn lazily (`from sklearn.ensemble import GradientBoostingRegressor` inside `train()`) which is good. But `train()` is an `async def` that calls synchronous sklearn `.fit()` -- this blocks the asyncio event loop for the entire training duration. The current architecture runs training in `_nightly_scheduler_loop` which happens at 04:00, far from peak demand. But v1.3's "self-tuning" implies more frequent model updates, and anomaly detection implies per-cycle inference.

**How to avoid:**
- Run all sklearn `.fit()` calls in `asyncio.get_event_loop().run_in_executor(None, ...)` to offload to a thread pool. The GIL will still block CPU-bound work, but it won't block the event loop's I/O operations (driver polling, WebSocket updates).
- For per-cycle anomaly detection, pre-compute thresholds (e.g., z-score bounds, IQR ranges) outside the control loop and use simple arithmetic checks (< 0.1ms) inside the loop. Never call `.predict()` inside `_run_cycle()`.
- Cap training frequency: retrain at most once per 24 hours (the existing `retrain_if_stale(stale_hours=24)` pattern is correct). Self-tuning parameter updates at most once per hour.
- Set `OMP_NUM_THREADS=2` in the Dockerfile for aarch64 builds. OpenMP default thread detection in containers is broken -- it detects all host CPUs, causing oversubscription and severe slowdown on Raspberry Pi.

**Warning signs:**
- Control loop cycle time exceeding 5 seconds (log the elapsed time of each `_run_cycle()`).
- asyncio event loop blocked warnings in Python logs.
- Driver readings becoming stale (`stale_threshold_s=30.0` breached) during training windows.

**Phase to address:**
Anomaly detection phase (per-cycle checks) and self-tuning phase (training offloading). The `run_in_executor` pattern should be implemented as the very first task.

---

### Pitfall 4: Anomaly Detection False Positives Triggering Unnecessary Alerts and Mode Changes

**What goes wrong:**
Anomaly detection on energy consumption data has notoriously high false positive rates because "normal" household consumption is highly variable. A heat pump defrost cycle, an oven preheating, or an EV starting to charge all look like anomalies to a model trained on quiet periods. If anomaly detection triggers Telegram alerts or -- worse -- forces the coordinator into HOLD mode, the user gets alert fatigue and the system becomes less effective than without ML.

Published research shows that models trained on clean data overfit to small variations, with 40% data subsets actually producing fewer false positives than full-dataset models. Static thresholds produce 20% more false positives than adaptive thresholds.

**Why it happens:**
Energy consumption is quasi-periodic with high variance within cycles. A household's consumption at 18:00 on a Tuesday might range from 1.5 kW to 8 kW depending on cooking, laundry, and heat pump behavior. Simple z-score or IQR-based anomaly detection will flag the high end of normal variation as anomalous.

**How to avoid:**
- Use context-aware baselines: anomaly thresholds must be hour-of-day and day-of-week specific. A 6 kW draw at 18:00 is normal; at 03:00 it warrants investigation.
- Implement a tiered alert system: (1) INFO log only for mild anomalies (1.5-2x expected), (2) Telegram notification for sustained anomalies (> 30 minutes above 2x expected), (3) coordinator action only for hardware-level anomalies (driver communication failure, SoC reading impossible values).
- Never let anomaly detection change battery control parameters or force mode changes. Anomaly detection is observability, not control.
- Require confirmation period: an anomaly must persist for N consecutive cycles before generating any alert. A single-cycle spike is noise.
- Track false positive rate: log every alert, and when the user dismisses or ignores it, count that as a false positive. If FP rate exceeds 30%, widen thresholds automatically.

**Warning signs:**
- More than 3 Telegram alerts per day from anomaly detection.
- User stops responding to anomaly alerts (alert fatigue).
- Anomaly rate exceeding 5% of all observations (indicates threshold is too tight).

**Phase to address:**
Anomaly detection phase. The tiered system and confirmation periods must be in the initial design, not added after complaints.

---

### Pitfall 5: Feature Engineering Leaking Future Data Into Training

**What goes wrong:**
The existing `ConsumptionForecaster` builds features from `[outdoor_temp, ewm_temp_3d, day_of_week, hour_of_day, month]`. When predicting future hours, it uses `neutral_temp = 10.0` as a placeholder because actual future temperatures are unknown. This creates a train/predict distribution mismatch: the model trains on real temperature data but predicts with a constant. If v1.3 adds more features (solar production, grid price, EV charging status), the risk of accidentally including "future" features in training grows. For example, using tomorrow's actual solar production to train a model that predicts tonight's charge target is a classic data leakage bug.

**Why it happens:**
In time-series ML, the boundary between "known at prediction time" and "only known after the fact" is subtle. Features like "yesterday's consumption" are fine. Features like "today's total consumption" are leakage if you're predicting the morning and the feature includes evening data. The existing codebase already has this issue with the temperature placeholder -- the model's training distribution doesn't match its inference distribution.

**How to avoid:**
- Categorize every feature as KNOWN_AT_PREDICTION_TIME or RETROSPECTIVE. Only KNOWN_AT_PREDICTION_TIME features may be used during inference. RETROSPECTIVE features are fine for training evaluation but must be replaced with forecasts or dropped during prediction.
- For weather features, integrate the existing `weather_client.OpenMeteoClient` forecast into the prediction pipeline instead of using `neutral_temp = 10.0`. The solar forecast infrastructure already exists.
- Use time-series cross-validation (expanding window), not random train/test split. The existing code trains on all available data without validation -- add a rolling validation window to detect leakage.
- Build a test that compares model accuracy on training data vs. a held-out future period. If training accuracy is dramatically better than held-out accuracy, leakage is likely.

**Warning signs:**
- Training RMSE is suspiciously low (e.g., < 50W for heat pump prediction).
- Model accuracy degrades sharply when deployed vs. offline evaluation.
- Prediction accuracy is much worse at hour boundaries (morning/evening transitions) than mid-period.

**Phase to address:**
Improved forecasting phase. Feature categorization must happen before any new features are added to the model.

---

### Pitfall 6: Model Drift From Seasonal Changes and New Appliances

**What goes wrong:**
A model trained during winter (high heat pump consumption, low solar) performs poorly in spring (heat pump off, high solar). A model trained before an EV purchase doesn't understand the new 7 kW charging patterns. The existing `retrain_if_stale(stale_hours=24)` retrains daily, but the 90-day training window means winter patterns dominate even in March, and vice versa. The model slowly adapts but lags reality by weeks.

**Why it happens:**
GradientBoostingRegressor has no built-in concept of data recency. All 2160 training samples (90 days * 24 hours) are weighted equally. A one-week heatwave 80 days ago has the same influence as yesterday's data. The existing EWM smoothing (`_compute_ewm`) helps the temperature feature but doesn't address the target variable weighting.

**How to avoid:**
- Implement sample weighting: recent data gets higher weight. Use sklearn's `sample_weight` parameter in `.fit()`, with exponential decay (half-life of 30 days). This is a one-line change but dramatically improves seasonal adaptation.
- Track model error over time (the existing `get_forecast_comparison()` method). If error trends upward for 5 consecutive days, trigger immediate retrain with a shorter lookback window (30 days instead of 90).
- Detect distribution shift: compare the mean and variance of predictions over the last 7 days to the training data distribution. A significant shift (> 2 sigma) triggers a retrain alert.
- For new appliance detection: monitor total daily consumption trend. A step change of > 20% sustained for 7 days should trigger a notification suggesting the user confirm a new appliance, and the model retrains with a shorter window.

**Warning signs:**
- `error_pct` from `get_forecast_comparison()` consistently above 30% for 5+ days.
- Predicted daily consumption diverging from actual by more than 5 kWh.
- Model always predicting high consumption during summer (winter training dominance).

**Phase to address:**
Self-tuning phase, specifically the model lifecycle management component.

---

### Pitfall 7: aarch64 Build and Runtime Failures for scikit-learn + numpy

**What goes wrong:**
The Dockerfile already handles this partially (`apk add gfortran openblas-dev`) but scikit-learn on Alpine aarch64 is fragile. The `scikit-learn>=1.4,<2` constraint means pip must compile from source on aarch64 Alpine (no manylinux wheels for musl). Build times exceed 15 minutes on Raspberry Pi 4. At runtime, OpenMP thread detection inside Docker containers detects all host CPUs instead of the container's cgroup limit, causing thread oversubscription that makes GBR training 3-10x slower than expected.

**Why it happens:**
Home Assistant Add-on base images use Alpine Linux (musl libc), not Debian/Ubuntu (glibc). PyPI wheels for scikit-learn are built for manylinux (glibc), so Alpine must compile from source. The HA Add-on builder uses QEMU for cross-architecture builds (amd64 host building aarch64 image), which makes compilation even slower. OpenMP's CPU detection reads `/proc/cpuinfo` (host CPUs) instead of the cgroup CPU quota.

**How to avoid:**
- Set `OMP_NUM_THREADS=2` and `OPENBLAS_NUM_THREADS=2` in the Dockerfile or `run.sh`. This is the single most impactful fix for runtime performance on constrained hardware.
- Consider switching to `HistGradientBoostingRegressor` which is faster and uses less memory than `GradientBoostingRegressor` (it's the recommended replacement in scikit-learn docs since 1.0).
- Pin numpy to a version with pre-built Alpine aarch64 wheels if available, or ensure openblas-dev is installed before numpy compilation.
- Add a build cache step in CI: build the Python dependencies in a separate Docker layer so they're cached between code changes (the current Dockerfile structure already does this correctly with `COPY pyproject.toml .` before `COPY backend/`).
- Set a training timeout: if `.fit()` takes longer than 120 seconds, abort and keep the previous model. Log a WARNING so the user knows training was skipped.

**Warning signs:**
- Docker build taking > 20 minutes on aarch64.
- `import sklearn` taking > 5 seconds at startup.
- Training taking > 60 seconds for 2160 samples on target hardware.
- High CPU usage (> 100% of allocated cores) during training.

**Phase to address:**
First phase touching ML code. The `OMP_NUM_THREADS` fix and `HistGradientBoostingRegressor` switch should happen before any new ML features are added.

---

### Pitfall 8: Model Versioning and Rollback Failure

**What goes wrong:**
scikit-learn models serialized with `pickle` or `joblib` are version-coupled: a model saved with sklearn 1.4 may not load correctly on sklearn 1.5. The existing codebase doesn't persist models at all (retrains from scratch each startup), which avoids this issue but wastes 15-60 seconds on every restart. If v1.3 adds model persistence for faster cold starts, the system must handle sklearn version upgrades gracefully. A failed model load on startup could crash the entire EMS.

More importantly, if a newly trained model performs worse than the previous one (e.g., after a data quality issue), there's no rollback mechanism. The old model is simply overwritten.

**Why it happens:**
scikit-learn explicitly states that loading models across versions is "entirely unsupported and inadvisable." The Add-on auto-updates, and a sklearn minor version bump could silently break persisted models.

**How to avoid:**
- Don't persist sklearn models to disk. The current approach (retrain from scratch at startup + nightly) is actually safer for an HA Add-on environment. Training 2160 samples takes < 60s even on aarch64 -- acceptable for a startup-time cost.
- If persistence is needed: store model metadata (sklearn version, training date, sample count, validation RMSE) alongside the model. On load, verify the sklearn version matches. If it doesn't, discard the model and retrain.
- Keep the last-known-good model: before overwriting, copy the current model file. If the new model's validation RMSE is worse than the previous model's by more than 20%, keep the old model and log a WARNING.
- Use `skops.io` instead of pickle for serialization if persistence is added -- it's more secure and provides version compatibility metadata.

**Warning signs:**
- Model load failure after Add-on update.
- Validation RMSE of new model is worse than the previous model.
- Model age exceeding 7 days (retrain loop may be silently failing).

**Phase to address:**
Model lifecycle management component of the self-tuning phase.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Training on all data without validation split | Simpler code, uses all available data | Can't detect overfitting or leakage | Only during cold-start (< 30 days of data) |
| Using `neutral_temp = 10.0` for prediction | Avoids weather forecast dependency | Predictions ignore the primary consumption driver (heating) | Never acceptable in v1.3 -- integrate Open-Meteo forecast |
| `_BASE_LOAD_W = 300.0` constant | Placeholder until real entity available | Underestimates base load for many households | Only until a consumption entity is configured |
| Retraining from scratch on every startup | Avoids model persistence version coupling | 15-60s startup delay on aarch64 | Acceptable given the safety tradeoff -- persistence is riskier |
| Single GBR model per load type | Simple architecture | No ensemble diversity, single point of failure | Acceptable for v1.3 scope; ensemble can be added in v2 |
| In-process ML training (no separate worker) | No infrastructure complexity | Blocks event loop during training | Acceptable with `run_in_executor()` -- only unacceptable if training runs in the async path without executor |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| HA SQLite statistics | Querying > 90 days of hourly data causes slow reads on SD card | Limit to 90 days; use `LIMIT` clause; cache results in memory |
| InfluxDB Flux queries for training data | Using `filter()` after `map()` prevents pushdown optimization | Always put `filter()`, `range()`, and `keep()` before any `map()` or `reduce()` operations |
| Open-Meteo weather forecast | Treating forecast as ground truth for feature engineering | Use forecast only for inference features; train on actual weather from HA statistics |
| EVCC state for EV charging detection | Polling EVCC HTTP API inside the control loop | Use the existing `EvccMqttDriver` -- it pushes state updates without polling overhead |
| Telegram notifications for anomalies | Sending one message per anomaly detection cycle | Batch anomalies: one summary per hour maximum, with deduplication |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| sklearn `.fit()` in async without executor | Event loop freezes for 2-60s, missed control cycles, stale WebSocket | Always use `run_in_executor(None, model.fit, X, y)` | Immediately on aarch64; after ~5000 samples on amd64 |
| InfluxDB query for 90 days of 5s-interval metrics | Query takes 30s+, returns millions of rows | Pre-aggregate to hourly in Flux query using `aggregateWindow(every: 1h, fn: mean)` | At ~500K data points (about 30 days of 5s data) |
| OpenMP thread oversubscription in container | Training takes 10x longer than expected, high CPU steal time | Set `OMP_NUM_THREADS=2` and `OPENBLAS_NUM_THREADS=2` | Immediately on any containerized aarch64 deployment |
| Per-cycle anomaly detection with sklearn predict | Each `.predict()` call adds 5-15ms on aarch64 | Pre-compute thresholds; use simple arithmetic in the loop | At scale of 5s cycles on aarch64 (need < 1ms budget for anomaly check) |
| Growing in-memory training data cache | Memory usage grows unbounded as history accumulates | Cap in-memory cache at 90 days; drop oldest data on each retrain | At ~6 months of operation on 1GB RAM devices |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Persisting ML models with pickle | Arbitrary code execution on model load (pickle deserialization vulnerability) | Use `skops.io` or retrain from scratch (current approach is safe) |
| Exposing model parameters via REST API without auth | Attacker could observe consumption patterns (occupancy inference) | Ensure `/api/ml/*` endpoints are behind the existing JWT auth middleware |
| Logging raw consumption data at DEBUG level | Energy consumption patterns leak household activity schedule | Log aggregates only; never log per-minute consumption in production |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Showing ML confidence without context | "72% confidence" means nothing to a homeowner | Show "Based on 45 days of data (learning, expect improvement)" |
| Anomaly alerts with technical details | "Z-score 3.2 on base_load at 03:15 UTC" is useless | "Unusual power draw detected at 4:15 AM (3.2 kW vs typical 0.5 kW) -- check for appliances left on" |
| Showing self-tuned parameter changes | "hysteresis_w changed from 200 to 187" confuses users | Don't show parameter changes at all. Show outcomes: "System response optimized -- 12% fewer mode switches this week" |
| Requiring user action for model management | "Model retrained -- approve new parameters?" blocks automation | Fully automated with guardrails. User sees outcomes, not process. Notify only on problems. |

## "Looks Done But Isn't" Checklist

- [ ] **Consumption forecaster:** Often missing actual weather integration -- verify predictions use Open-Meteo forecast, not `neutral_temp = 10.0`
- [ ] **Self-tuning:** Often missing rollback mechanism -- verify oscillation detector can revert to defaults within 2 control cycles
- [ ] **Anomaly detection:** Often missing confirmation period -- verify single-cycle spikes don't generate alerts
- [ ] **Model training:** Often missing executor offload -- verify `_run_cycle()` never blocks > 100ms during training
- [ ] **aarch64 build:** Often missing OMP thread limits -- verify `OMP_NUM_THREADS` is set in Dockerfile and run.sh
- [ ] **Feature engineering:** Often missing train/predict distribution check -- verify all features used in `.predict()` match what was used in `.fit()` (no placeholder constants replacing real training values)
- [ ] **Model lifecycle:** Often missing error trend monitoring -- verify `get_forecast_comparison()` runs daily and logs are actionable
- [ ] **Dashboard ML section:** Often missing fallback state display -- verify UI shows "Learning (14/60 days)" not just "ML active"

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Self-tuning causes oscillation | LOW | Oscillation detector triggers automatic revert to defaults; no user action needed |
| ML training blocks control loop | MEDIUM | Restart the Add-on; add `run_in_executor()` to training path; revert deployment if needed |
| Anomaly detection alert fatigue | LOW | Widen thresholds by 50%; reduce alert frequency to daily summary; disable Telegram for anomalies |
| Model drift after season change | LOW | Force retrain with 30-day window; wait 7 days for model to re-adapt |
| sklearn version incompatibility | LOW | Delete persisted model file; system retrains from scratch at next startup (< 60s) |
| Feature leakage producing false accuracy | MEDIUM | Audit feature pipeline; implement time-series CV; retrain with corrected features; compare to seasonal baseline |
| aarch64 build failure after sklearn update | HIGH | Pin sklearn version exactly in pyproject.toml; test aarch64 build in CI before merging |
| Control loop missed cycles during training | LOW | Add `run_in_executor()`; set training timeout of 120s; monitor cycle timing |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Cold-start worse than fallback | Forecasting improvement phase | Shadow mode log shows ML params vs defaults for 14+ days before activation |
| Self-tuning destabilizes control loop | Self-tuning phase (first task) | Oscillation detector tested with synthetic rapid-transition scenarios |
| Blocking control loop with ML | Any phase adding ML inference | `_run_cycle()` timing histogram shows p99 < 500ms; no stale data warnings during training |
| Anomaly false positives | Anomaly detection phase | False positive rate < 20% after 30 days; alert count < 3/day |
| Feature leakage | Forecasting improvement phase | Time-series CV gap between train and test accuracy < 15% |
| Model drift | Self-tuning lifecycle phase | Error trend monitoring active; auto-retrain triggers within 5 days of drift |
| aarch64 build/runtime issues | First phase touching ML | CI builds and tests on aarch64; `OMP_NUM_THREADS=2` verified in container |
| Model versioning/rollback | Self-tuning lifecycle phase | Model load failure handled gracefully (retrain from scratch, no crash) |

## Sources

- [scikit-learn model persistence docs](https://scikit-learn.org/stable/model_persistence.html) -- version coupling warnings, skops.io recommendation
- [scikit-learn aarch64 performance issue #15824](https://github.com/scikit-learn/scikit-learn/issues/15824) -- slow tests on ARM, OpenMP oversubscription
- [InfluxDB Flux query optimization](https://docs.influxdata.com/influxdb/v2/query-data/optimize-queries/) -- pushdown functions, filter ordering
- [Anomaly detection in energy consumption (Wiley 2025)](https://onlinelibrary.wiley.com/doi/full/10.4218/etrij.2023-0155) -- false positive rates, adaptive thresholds
- [Transfer learning for energy anomaly detection (ScienceDirect 2025)](https://www.sciencedirect.com/science/article/pii/S037877882500859X) -- 40% data subset reducing false positives
- [Stability-preserving RL-based PID tuning](https://www.oaepublish.com/articles/ces.2021.15) -- stability guarantees lost with unconstrained ML tuning
- [ISA PID tuning common mistakes](https://blog.isa.org/avoid-common-tuning-mistakes-pid) -- oscillation from aggressive integral parameters
- Existing EMS codebase analysis: `backend/consumption_forecaster.py`, `backend/coordinator.py`, `backend/config.py`

---
*Pitfalls research for: ML self-tuning, anomaly detection, and forecasting in dual-battery EMS*
*Researched: 2026-03-23*
