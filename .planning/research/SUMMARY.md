# Project Research Summary

**Project:** EMS v1.3 — ML Self-Tuning for Dual-Battery Energy Management
**Domain:** ML-enhanced residential energy management (forecasting, self-tuning, anomaly detection)
**Researched:** 2026-03-23
**Confidence:** HIGH (stack), MEDIUM (features), HIGH (architecture), HIGH (pitfalls)

## Executive Summary

EMS v1.3 extends an existing, production-ready dual-battery controller with ML capabilities: improved consumption forecasting, self-tuning control parameters, and anomaly detection. The system already has scikit-learn and numpy as dependencies with a working `ConsumptionForecaster` class — the entire v1.3 ML feature set can be built without adding any new core dependencies. The existing codebase provides all the infrastructure hooks needed (nightly scheduler loop, dependency injection via setters, graceful degradation patterns, InfluxDB metrics, HA statistics reader), making this primarily an enhancement project rather than a greenfield build.

The recommended approach follows a strict three-layer progression: first fix the forecaster's known weaknesses (neutral temperature placeholder, missing lagged features, no accuracy tracking), then build the measurement and safety infrastructure (optimization scorecard, oscillation detector), and only then activate self-tuning parameters. This ordering is non-negotiable: self-tuning activated before the forecast is reliable amplifies forecast errors into dangerous parameter recommendations. The dependency chain is clear — anomaly detection requires a good forecast baseline; self-tuning dead-bands require the oscillation detector; self-tuning min-SoC requires both a trusted 72h forecast and a stable self-tuning foundation.

The highest risks are not technical but operational: running sklearn training synchronously in the async control loop (which causes missed 5s control cycles), self-tuning activating before enough training data exists (60-day minimum for seasonal coverage), and anomaly detection generating alert fatigue (tiered alerts with confirmation periods are mandatory, not optional). Every new ML component must follow the established patterns — `run_in_executor()` for training, setter-based injection, hard safety bounds, and graceful degradation to seasonal constants when data is unavailable.

## Key Findings

### Recommended Stack

The entire v1.3 ML feature set fits within the existing dependency set. scikit-learn `GradientBoostingRegressor` (or `HistGradientBoostingRegressor` for native NaN handling) handles consumption forecasting; `IsolationForest` handles anomaly detection; `joblib` (bundled with sklearn) handles model persistence. The only optional new dependency is `holidays>=0.40` for German public holiday calendar features — pure Python, no C extensions, ~50KB.

**Core technologies:**
- `scikit-learn >=1.4,<2` (existing): All ML models — GBR/HistGBR forecasting, IsolationForest anomaly detection, Gaussian Process or grid search for self-tuning — zero new deps required
- `numpy >=1.25,<3` (existing): Feature engineering, rolling statistics, z-score calculations, all numerical operations
- `joblib` (bundled with sklearn): Model persistence to `/config/ems_models/` with version metadata sidecar JSON
- `holidays >=0.40` (optional new): German public holiday calendar feature for day-of-week encoding — only add if holiday feature improves MAPE
- `OMP_NUM_THREADS=2` / `OPENBLAS_NUM_THREADS=2` (Dockerfile env): Critical runtime fix for aarch64 — without this, training is 3-10x slower due to OpenMP oversubscription in containers

### Expected Features

**Must have (table stakes — P1, Phase 1):**
- Weather-aware consumption forecast — replace `neutral_temp = 10.0` placeholder with actual Open-Meteo hourly forecast; single biggest accuracy win for lowest effort
- Lagged consumption features — add `load_24h_ago`, `load_168h_ago`, `avg_load_last_24h`; research confirms these are the strongest predictors for residential load
- Day-of-week and holiday encoding — proper categorical encoding plus German holiday flag; current raw integer encoding loses cyclical structure
- Forecast accuracy tracking (MAPE) — persist daily predicted vs. actual to InfluxDB; this gates all self-tuning features
- Optimization scorecard — daily self-consumption ratio (SCR), self-sufficiency ratio (SSR), grid import kWh; the "did it work?" metric for all ML features

**Should have (differentiators — P2, Phase 2):**
- Self-tuning dead-bands and ramp rates — count oscillation events from `ems_decision` log; adjust hysteresis within [100W, 500W] bounds nightly; unique vs. EMHASS and SolarAssistant
- Multi-horizon 72h forecast with real weather — pipe Open-Meteo hourly forecasts into `predict_hourly(72)`; enables better WeatherScheduler decisions
- Consumption anomaly detection — flag hours where actual > 2x forecast for 2+ consecutive hours; tiered alerts with 15-minute cooldown

**Defer (P3, Phase 3 or v1.4):**
- Self-tuning min-SoC profiles — requires high forecast confidence (MAPE < 25%), complex safety constraints, high under-reserve risk
- SoC curve anomaly detection — needs weeks of baseline data for calibration; meaningful only after Phase 2 is stable
- Efficiency degradation tracking — meaningful only over months of data; low urgency for v1.3

### Architecture Approach

The ML features integrate as three loosely-coupled subsystems — `ConsumptionForecaster` (upgrade in place), `SelfTuner` (new), `AnomalyDetector` (new) — sharing a common `FeaturePipeline` and `ModelStore`. No existing component needs a rewrite; the Coordinator gains an anomaly hook (~40 lines) and setter methods for tunable parameters, following the established injection pattern used by ExportAdvisor, HA MQTT, and Telegram notifier. Heavy computation (training, self-tuning evaluation) runs exclusively in the nightly `_nightly_scheduler_loop`; per-cycle anomaly checking uses pre-computed statistical thresholds only (no sklearn inference on the hot path).

**Major components:**
1. `FeaturePipeline` (`backend/feature_pipeline.py`, NEW) — centralized feature extraction from HA SQLite + InfluxDB, cached in memory for the nightly batch; gracefully degrades when either source is unavailable
2. `ConsumptionForecaster` (UPGRADED in place) — same public interface, upgraded internals: real weather features, lagged consumption, delegates to FeaturePipeline; interface change is zero — downstream WeatherScheduler and Scheduler are untouched
3. `SelfTuner` (`backend/self_tuner.py`, NEW) — reads 7 days of `ems_decision` InfluxDB data nightly, produces bounded `TuningRecommendation` objects, applies via Coordinator setters; max 10% change per night per parameter
4. `AnomalyDetector` (`backend/anomaly_detector.py`, NEW) — two-layer: per-cycle lightweight threshold checks (< 10ms), nightly IsolationForest retrain; alert cooldown per-flag type (default 15 min)
5. `ModelStore` (`backend/model_store.py`, NEW) — `joblib` persistence to `/config/ems_models/` with `ModelMetadata` sidecar; version mismatch triggers discard and retrain

### Critical Pitfalls

1. **Blocking the async control loop with sklearn training** — always use `asyncio.get_event_loop().run_in_executor(None, model.fit, X, y)` for any `.fit()` call; never call `.train()` synchronously in the async path; set a 120s training timeout with fallback to the previous model

2. **Self-tuning activating before the forecast is trustworthy** — enforce a 60-day minimum data requirement and shadow mode (log ML-recommended vs. actual parameters for 14+ days before applying); MAPE must be below 25% before any parameter auto-adjustment activates

3. **Anomaly detection alert fatigue from high variance household consumption** — use hour-of-day and day-of-week specific baselines, require 2+ consecutive cycles for confirmation, tiered alerts (INFO log only for 1.5-2x, Telegram for sustained 2x, coordinator action only for hardware faults); never allow anomaly detection to change control parameters

4. **Self-tuning destabilizing the 5-second control loop** — build the oscillation detector first (before any parameter adjustment capability); enforce combined dead-band minimum of 150W across both systems; apply parameter changes only at clean transition points (start of hour, after HOLD); auto-revert to defaults if oscillation rate exceeds 6 transitions/hour

5. **aarch64 OpenMP oversubscription** — set `OMP_NUM_THREADS=2` and `OPENBLAS_NUM_THREADS=2` in the Dockerfile; this is the single most impactful fix for runtime training performance on Raspberry Pi class hardware; without it, GBR training is 3-10x slower than expected

## Implications for Roadmap

Based on research, the dependency chain mandates this order: fix the forecast foundation first, build measurement and safety infrastructure second, activate self-tuning third. Reversing any step creates compounding risk.

### Phase 1: Forecaster Foundation

**Rationale:** The current forecaster uses `neutral_temp = 10.0` for all predictions, making it substantially less accurate than it could be with zero additional dependencies. Everything that follows (self-tuning, anomaly detection, optimization scorecard) depends on a trustworthy forecast as its baseline. This phase has the highest value-to-effort ratio in the entire project.

**Delivers:** A materially improved consumption forecaster that uses real weather data, lagged consumption history, and proper day-of-week encoding. Forecast accuracy tracking (MAPE) persisted to InfluxDB. Optimization scorecard API endpoint.

**Addresses:** Weather-aware forecast, lagged features, day-of-week/holiday encoding, MAPE tracking, optimization scorecard (all P1 features from FEATURES.md)

**Implements:** FeaturePipeline, upgraded ConsumptionForecaster, ModelStore (no persistence initially — retrain on startup is safer per PITFALLS.md Pitfall 8)

**Avoids:** Feature leakage (Pitfall 5), model drift from neutral temp placeholder, cold-start model being worse than seasonal fallback (Pitfall 1)

**Research flag:** Standard patterns — sklearn GBR, Open-Meteo integration, HA statistics all well-documented in codebase and STACK.md; skip phase research

### Phase 2: Safety and Observability Layer

**Rationale:** The oscillation detector and anomaly detection infrastructure must exist and be proven stable before any self-tuning parameter writes happen. Building observability before action is the correct order — you cannot tune what you cannot measure. This phase also produces immediate user value (anomaly alerts) while building the foundation for Phase 3.

**Delivers:** AnomalyDetector with per-cycle scoring and nightly IsolationForest retrain. Oscillation counter from `ems_decision` InfluxDB data. New API endpoints (`/api/ml/status`, `/api/ml/anomalies`). Coordinator anomaly hook (setter injection pattern, ~40 lines).

**Addresses:** Consumption anomaly detection, multi-horizon 72h forecast (P2 features from FEATURES.md)

**Implements:** AnomalyDetector, Coordinator anomaly hook, oscillation counting infrastructure

**Avoids:** Anomaly false positive alert fatigue (Pitfall 4 — tiered alerts and confirmation periods must be in the initial design), blocking control loop with inference (Pitfall 3 — pre-computed thresholds only in the 5s loop)

**Research flag:** Standard patterns — IsolationForest, asyncio executor offload well-documented; skip phase research

### Phase 3: Self-Tuning Parameters

**Rationale:** Self-tuning activates only after Phase 1 (reliable forecast, MAPE tracking) and Phase 2 (oscillation detector that can catch and revert bad parameters) are stable. The 60-day data maturity requirement means this phase starts implementation during Phase 2 but only goes live once the data threshold is met. Shadow mode must run for 14+ days before any parameter is applied to the live system.

**Delivers:** SelfTuner with dead-band and ramp rate adjustment. Shadow mode logging. `TuningRecommendation` API. Auto-revert on oscillation detection. Dashboard showing outcome metrics (fewer mode switches, not raw parameter values).

**Addresses:** Self-tuning dead-bands, ramp rates (P2 features); defers min-SoC self-tuning to Phase 4 (requires forecast MAPE < 25% proven over 30+ days)

**Implements:** SelfTuner, Coordinator setter methods, ModelStore persistence with version metadata

**Avoids:** Self-tuning destabilizing control loop (Pitfall 2 — oscillation detector is prerequisite), cold-start parameter thrashing (Pitfall 1 — 60-day minimum, shadow mode gate), model versioning failures on Add-on update (Pitfall 8 — sklearn version check in ModelStore)

**Research flag:** Bounded parameter optimization with safety constraints is a nuanced area — consider `/gsd:research-phase` for the shadow mode activation logic and rollback mechanism design

### Phase 4: Advanced ML (Optional v1.3 or v1.4)

**Rationale:** These features require months of baseline data under ML control before they can be trusted. They are explicitly marked P3 in FEATURES.md and should only be planned once Phase 3 is stable and producing measurable improvements on the scorecard.

**Delivers:** Self-tuning min-SoC profiles, SoC curve anomaly detection, efficiency degradation tracking

**Addresses:** Remaining P3 features from FEATURES.md

**Avoids:** Under-reserve risk from min-SoC tuning with unreliable forecast (requires Phase 1 MAPE < 25% confirmed over 30+ days)

**Research flag:** Min-SoC dynamic programming or heuristic design warrants `/gsd:research-phase` — the safety constraints and interaction with the nightly scheduler are non-trivial

### Phase Ordering Rationale

- **Forecast before self-tuning:** Anomaly detection quality and self-tuning signal quality both degrade proportionally to forecast error. An inaccurate forecast makes everything downstream worse.
- **Observability before action:** You cannot safely auto-tune parameters you cannot measure. The oscillation detector (Phase 2) is a hard prerequisite for self-tuning (Phase 3), not just nice-to-have.
- **aarch64 fixes belong to Phase 1:** `OMP_NUM_THREADS=2`, `run_in_executor()` for training, and `HistGradientBoostingRegressor` migration should all land in Phase 1 before any new ML code is written — they are foundational correctness fixes.
- **Shadow mode gates Phase 3 activation:** The 60-day data requirement and 14-day shadow period mean Phase 3 implementation starts in parallel with Phase 2, but live activation is gated by data maturity. Implementation can proceed; deployment waits for the gate.

### Research Flags

Phases needing `/gsd:research-phase` during planning:
- **Phase 3 (Self-Tuning):** Shadow mode activation logic, rollback trigger design, and the interaction between SelfTuner and Coordinator setter methods — the bounded parameter optimization approach is sound but the activation gating and rollback protocol need careful design
- **Phase 4 (Min-SoC self-tuning):** Dynamic programming or heuristic for optimal SoC floor per hour, interaction with nightly charge scheduler, safety constraints around under-reserve

Phases with standard patterns (skip research-phase):
- **Phase 1 (Forecaster Foundation):** All patterns well-documented in existing codebase and STACK.md — Open-Meteo integration already exists, HA statistics reader already exists, GBR features are established practice
- **Phase 2 (Observability):** IsolationForest, asyncio executor offload, Coordinator injection pattern — all standard and well-documented

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All recommendations use existing dependencies. No new C-extension risk. scikit-learn and numpy already validated on Alpine aarch64 in CI. |
| Features | MEDIUM | Academic sources confirm feature importance rankings. EMHASS competitor analysis provides real-world validation. Exact MAPE thresholds (< 25% for self-tuning gate) are heuristic — validate against actual household data in Phase 1. |
| Architecture | HIGH | Based on direct codebase analysis of existing injection patterns, nightly loop structure, and Coordinator interface. All component boundaries trace directly to existing code. |
| Pitfalls | HIGH | Most pitfalls identified from direct codebase analysis (neutral temp placeholder, missing executor offload, no validation split) rather than inference. aarch64 performance issue has a known GitHub issue reference. |

**Overall confidence:** HIGH

### Gaps to Address

- **MAPE threshold for self-tuning activation (25%):** This is a research-derived heuristic. The actual threshold that works for this household's consumption patterns will only be known after Phase 1 produces real MAPE data. Plan to calibrate in Phase 2 before committing to Phase 3 activation logic.
- **Oscillation rate threshold (6 transitions/hour for revert):** Current EMS data shows existing oscillation rates. Before implementing the revert trigger, query actual `ems_decision` data to set a threshold that is above normal operation but below problematic behavior.
- **Shadow mode duration (14 days):** May need adjustment based on how quickly the self-tuner's recommendations stabilize. If recommendations converge in 5 days, shadow mode can be shortened; if they oscillate between runs, it should be extended.
- **aarch64 training time benchmarks:** PITFALLS.md estimates 15-60s for GBR training on aarch64. Actual timing on the target HA host should be measured in Phase 1 to set a realistic training timeout value.

## Sources

### Primary (HIGH confidence)
- scikit-learn 1.8.0 documentation (model persistence, HistGBR, IsolationForest, novelty/outlier detection)
- Existing EMS codebase: `backend/consumption_forecaster.py`, `backend/coordinator.py`, `backend/main.py`, `backend/weather_scheduler.py`
- scikit-learn GitHub issue #15824 — aarch64 OpenMP oversubscription

### Secondary (MEDIUM confidence)
- EMHASS documentation (ML forecaster, forecast module) — competitor feature comparison
- Gradient Boosting for home energy prediction (ScienceDirect) — validates GBR for residential forecasting
- Load forecasting for battery storage control (MDPI) — feature importance validation
- Seasonal hourly electricity demand forecasting (Nature) — weather + calendar feature confirmation
- Hybrid ML framework for battery anomaly detection (Nature 2025) — IsolationForest applicability
- Self-consumption and self-sufficiency metrics (MDPI) — SCR/SSR metric definitions
- Anomaly detection in energy consumption (Wiley 2025) — false positive rates, adaptive thresholds

### Tertiary (LOW confidence)
- Optimal PV-BESS household strategy (arXiv) — academic optimization approach, not directly applicable
- Stability-preserving RL-based PID tuning (OAE) — oscillation stability theory, adapted for ML tuning bounds

---
*Research completed: 2026-03-23*
*Ready for roadmap: yes*
