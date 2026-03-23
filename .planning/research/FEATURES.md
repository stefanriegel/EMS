# Feature Research

**Domain:** ML-driven self-tuning for residential dual-battery energy management
**Researched:** 2026-03-23
**Confidence:** MEDIUM

## Feature Landscape

### Table Stakes (Users Expect These)

Features that any ML-enhanced battery EMS must have to be credible. The existing system already has basic ML forecasting; v1.3 needs to make it genuinely useful.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Weather-aware consumption forecast | Current forecaster uses a neutral 10C placeholder instead of actual/forecast temps. Heat pump power correlates directly with outdoor temp. Without real weather data, the ML model is severely handicapped. | LOW | Solar forecast already available via `get_solar_forecast()`. Need to pipe actual outdoor temp from HA stats into prediction features instead of the hardcoded `neutral_temp = 10.0`. Biggest accuracy win for lowest effort. |
| Day-of-week and holiday awareness | Household consumption varies 15-30% between weekdays and weekends. Current model includes `day_of_week` as a feature but treats it as a raw integer — not categorical. Holidays are ignored entirely. | LOW | Already have `float(ts.weekday())` in `_build_features()`. Needs: (1) one-hot or cyclical encoding for day-of-week, (2) German public holiday calendar (the `holidays` PyPI package covers this), (3) weekend/holiday binary flag. |
| Forecast accuracy tracking with MAPE | Without measuring forecast error, you cannot know if ML is helping or hurting. Current `get_forecast_comparison()` returns error_pct but nothing persists or tracks trends. | LOW | Write daily MAPE to InfluxDB. Expose via API. Target: MAPE under 20% for next-day consumption (literature shows 7-15% is achievable with GBR on residential data). |
| Lagged consumption features | Research consistently shows that consumption 24h and 168h (1 week) ago are the strongest predictors — more important than weather for many households. Current model has zero lagged features. | MEDIUM | Requires storing and retrieving hourly consumption history. HA statistics already has this via `read_entity_hourly()`. Add features: `load_24h_ago`, `load_168h_ago`, `avg_load_last_24h`. |
| Retraining on fresh data | Current `retrain_if_stale()` retrains every 24h. This is correct cadence — daily retraining captures seasonal drift without overfitting. Already implemented. | ALREADY DONE | Keep the 24h retrain cycle. Add: log train vs. validation RMSE to detect overfitting. |
| Communication loss detection | Both drivers already have failure counters and `max_offline_s` timeout. Users expect the system to notice when hardware goes silent. | ALREADY DONE | Existing: 3 consecutive failures triggers safe state, Telegram alert via `ALERT_COMM_FAILURE`. Enhance: track failure frequency over time to detect degrading connections (intermittent failures that stay below the 3-strike threshold). |

### Differentiators (Competitive Advantage)

Features that go beyond what EMHASS and typical HA battery integrations offer. These leverage the dual-battery architecture and 5s granularity metrics unique to this system.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Self-tuning dead-bands and ramp rates | Current hysteresis (200W) and per-system dead-bands (Huawei 300W, Victron 150W) are manual estimates. Self-tuning measures oscillation frequency over rolling windows and adjusts dead-bands to minimize state transitions while maintaining responsiveness. | MEDIUM | Metric: count state transitions per hour from `ems_decision` data. If transitions > threshold (e.g., 6/hour), widen dead-band by 10%. If transitions < floor (e.g., 1/hour) and grid import is high, narrow dead-band. Safety bounds: dead-band range 50W-500W per system. Ramp rate range 100W/s - 2000W/s. |
| Self-tuning min-SoC profiles | Current min-SoC is static or manual time-of-day windows. Self-tuning analyzes actual consumption patterns per time-of-day window and adjusts min-SoC to keep just enough reserve for expected demand before next PV production window. | HIGH | Requires: (1) reliable hourly consumption forecast, (2) next-day solar forecast, (3) dynamic programming or simple heuristic to compute optimal SoC floor per hour. Safety: min-SoC never below hardware minimum (Huawei 10%, Victron 15%). Max adjustment per day: 5% to prevent oscillation. |
| SoC curve anomaly detection | Track expected vs. actual SoC change given known charge/discharge power. Divergence indicates battery degradation, sensor drift, or calibration error. No competing HA add-on does this. | MEDIUM | Physics: delta_SoC_expected = (power_w * interval_s) / (capacity_kwh * 3600 * 10). Compare with actual delta_SoC from driver readings. Track cumulative drift over days. Alert when round-trip efficiency drops below threshold (e.g., 85% for lithium). |
| Efficiency degradation tracking | Monitor round-trip efficiency (energy_out / energy_in) over weeks/months. Detect gradual battery aging before it becomes a problem. | MEDIUM | Requires: InfluxDB integration for historical energy totals. Compute weekly round-trip efficiency from cumulative charge/discharge energy. Trend analysis: linear regression on weekly efficiency values. Alert when efficiency drops below configurable threshold or declines faster than expected. |
| Consumption anomaly detection | Detect unusual consumption spikes (appliance left on, HVAC malfunction, water heater stuck). Alert user via Telegram. | MEDIUM | Method: compare current hour's consumption against the ML forecast. If actual > 2x predicted for 2+ consecutive hours, flag anomaly. Use z-score against historical same-hour-same-weekday distribution. Avoid false positives by requiring persistence (not single spikes). |
| Optimization scorecard | Daily/weekly self-consumption ratio, self-sufficiency ratio, grid import kWh, and cost tracking. Shows whether ML changes are actually helping. | LOW | Metrics: SCR = PV_self_consumed / PV_total, SSR = PV_self_consumed / total_consumption. Already have the raw data in InfluxDB. Expose as `/api/optimization/scorecard` endpoint with daily/weekly/monthly aggregation. This is the "did it work?" metric for all other features. |
| Multi-horizon consumption forecast | Extend from 24h to 72h forecast aligned with the existing WeatherScheduler 3-day outlook. Current `predict_hourly(72)` exists but uses neutral 10C for all hours — useless for 72h planning. | MEDIUM | Requires weather forecast integration into the consumption model. Open-Meteo already provides 72h hourly temperature forecasts (used by solar forecast). Pipe hourly temp forecasts into the GBR features instead of the neutral placeholder. |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Reinforcement learning for battery control | Academic papers show RL outperforms rule-based control in simulation. Sounds cutting-edge. | RL needs thousands of episodes to converge. Each "episode" is a real day with real money. Exploration means deliberately making bad decisions. Sim-to-real gap is massive for household consumption. RL adds massive complexity for marginal gains over well-tuned heuristics. EMHASS uses LP, not RL, for good reason. | Stick with heuristic control + parameter tuning. The coordinator's role-based dispatch is already well-structured. Self-tune the parameters, not the policy. |
| Deep learning (LSTM/Transformer) for consumption forecast | Papers show LSTM beats GBR on some benchmarks. | Single household data is tiny (2000-8000 hourly samples per year). Deep learning overfits catastrophically on small datasets. GBR with 100 estimators is the right tool. Training must run on a Raspberry Pi / HA host — no GPU available. scikit-learn GBR trains in <1s on 90 days of hourly data. | Keep GradientBoostingRegressor. Consider XGBoost or LightGBM only if GBR accuracy plateaus — they are faster but not more accurate at this data scale. |
| Fully autonomous parameter changes without bounds | "Let the ML decide everything" — remove safety limits so the optimizer has full freedom. | Battery hardware has hard limits. Setting min-SoC too low damages cells. Setting dead-bands too narrow causes relay cycling that physically wears components. A runaway optimizer could cause real hardware damage or unexpected bills. | Every auto-tuned parameter must have hard safety bounds defined in config. Changes are bounded per cycle (e.g., max 5% SoC adjustment per day, max 50W dead-band change per hour). Human can always override via HA number entities. |
| Real-time ML inference on every control cycle | Run ML prediction every 5 seconds for maximum responsiveness. | Consumption changes slowly (minutes, not seconds). Running GBR.predict() every 5s wastes CPU on an embedded device for no benefit. Forecast horizon is hours — re-predicting every 5s is meaningless. | Predict hourly or on significant state changes (e.g., heat pump starts/stops). Cache predictions. Control loop uses cached forecast, not live inference. |
| Cloud-based ML training or inference | Offload computation to cloud for better models. | Violates core constraint: "Local network only, no cloud dependencies." Adds latency, availability risk, and privacy concerns. The data volume is small enough for local training. | All ML runs locally. scikit-learn GBR on 90 days of hourly data trains in <1s on arm64. No cloud needed. |
| Automated tariff switching / energy trading | ML predicts grid prices and automatically buys/sells energy. | This system uses fixed/semi-fixed tariffs (Octopus Go, Modul3). Dynamic wholesale trading requires financial regulation compliance, real-time grid operator APIs, and risk management. Completely different domain. | Optimize within existing tariff structure. The scheduler already picks cheapest slots. ML can improve the consumption forecast that feeds into slot selection. |

## Feature Dependencies

```
[Weather-aware forecast]
    └──requires──> [HA outdoor temp entity] (ALREADY EXISTS)
    └──requires──> [Weather forecast API] (ALREADY EXISTS via Open-Meteo)
    └──enhances──> [Multi-horizon 72h forecast]
                       └──enhances──> [Self-tuning min-SoC profiles]

[Lagged consumption features]
    └──requires──> [HA statistics hourly history] (ALREADY EXISTS)
    └──enhances──> [Weather-aware forecast]

[Forecast accuracy tracking (MAPE)]
    └──requires──> [InfluxDB metrics] (ALREADY EXISTS, optional)
    └──enhances──> [Self-tuning dead-bands] (knows if forecast improved)
    └──enhances──> [Optimization scorecard]

[Self-tuning dead-bands]
    └──requires──> [Oscillation counting from ems_decision] (data EXISTS)
    └──requires──> [Safety bounds in config]

[Self-tuning min-SoC]
    └──requires──> [Weather-aware forecast]
    └──requires──> [Multi-horizon 72h forecast]
    └──requires──> [Forecast accuracy tracking] (must trust the forecast first)

[SoC curve anomaly detection]
    └──requires──> [Per-system InfluxDB metrics] (ALREADY EXISTS)
    └──enhances──> [Efficiency degradation tracking]

[Consumption anomaly detection]
    └──requires──> [Weather-aware forecast] (need accurate baseline to detect anomalies)

[Optimization scorecard]
    └──requires──> [InfluxDB metrics] (ALREADY EXISTS)
    └──enhances──> ALL self-tuning features (measures their impact)
```

### Dependency Notes

- **Self-tuning min-SoC requires weather-aware forecast:** You cannot dynamically set SoC floors without knowing tomorrow's solar production and tonight's expected consumption. The forecast must be reasonably accurate (MAPE < 25%) before trusting it for SoC decisions.
- **Consumption anomaly detection requires weather-aware forecast:** Anomaly = actual significantly exceeds expected. Without good forecast, everything looks anomalous on cold days.
- **Optimization scorecard enhances everything:** Without measuring self-consumption ratio and grid import, you cannot know if any tuning change helped. Build this early.
- **Forecast accuracy tracking is the gatekeeper:** Self-tuning features should only activate when MAPE is below a threshold, otherwise they amplify forecast errors.

## MVP Definition

### Launch With (Phase 1 of v1.3)

Foundation: make the existing forecaster actually useful, measure its impact.

- [ ] **Weather-aware consumption forecast** — Replace neutral 10C placeholder with actual outdoor temp from HA statistics and forecast temps from Open-Meteo. Single biggest accuracy improvement.
- [ ] **Lagged consumption features** — Add load_24h_ago, load_168h_ago, avg_load_last_24h to feature matrix. Research shows these are the top predictors.
- [ ] **Day-of-week / holiday encoding** — Proper categorical encoding + German holiday flag. Low effort, meaningful accuracy gain.
- [ ] **Forecast accuracy tracking (MAPE)** — Persist daily predicted vs. actual to InfluxDB. Expose via API. This gates everything that follows.
- [ ] **Optimization scorecard** — Daily self-consumption ratio, self-sufficiency ratio, grid import. The "did it work?" dashboard.

### Add After Validation (Phase 2 of v1.3)

Self-tuning: only activate once forecast MAPE is consistently under 25%.

- [ ] **Self-tuning dead-bands** — Count oscillations, adjust hysteresis within safety bounds. Trigger: observing > 6 state transitions/hour in production data.
- [ ] **Multi-horizon 72h forecast with real weather** — Pipe Open-Meteo hourly temps into predict_hourly(). Enables better WeatherScheduler decisions.
- [ ] **Consumption anomaly detection** — Flag hours where actual > 2x forecast for 2+ consecutive hours. Telegram alert.

### Future Consideration (Phase 3 of v1.3 or v1.4)

Advanced features requiring stable foundation and proven forecast accuracy.

- [ ] **Self-tuning min-SoC profiles** — Defer because: requires high forecast confidence, complex safety constraints, high risk of under-reserving if forecast is wrong.
- [ ] **SoC curve anomaly detection** — Defer because: needs weeks of baseline data under ML control, physics model for expected SoC delta needs calibration per battery chemistry.
- [ ] **Efficiency degradation tracking** — Defer because: meaningful only over months of data, low urgency.

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Weather-aware forecast | HIGH | LOW | P1 |
| Lagged consumption features | HIGH | LOW | P1 |
| Day-of-week / holiday encoding | MEDIUM | LOW | P1 |
| Forecast accuracy tracking (MAPE) | HIGH | LOW | P1 |
| Optimization scorecard | HIGH | LOW | P1 |
| Self-tuning dead-bands | HIGH | MEDIUM | P2 |
| Multi-horizon 72h forecast | MEDIUM | MEDIUM | P2 |
| Consumption anomaly detection | MEDIUM | MEDIUM | P2 |
| Self-tuning min-SoC profiles | HIGH | HIGH | P3 |
| SoC curve anomaly detection | MEDIUM | MEDIUM | P3 |
| Efficiency degradation tracking | LOW | MEDIUM | P3 |

**Priority key:**
- P1: Foundation — must have before any self-tuning makes sense
- P2: Self-tuning — activate after forecast proves accurate
- P3: Advanced — needs months of baseline data

## Competitor Feature Analysis

| Feature | EMHASS | Batrium/BMS | SolarAssistant | Our Approach |
|---------|--------|-------------|----------------|--------------|
| Consumption forecast | ML via skforecast, supports multiple sklearn models, Bayesian hyperparameter tuning via optuna | None (BMS only) | Basic solar forecast only | GBR with weather + lagged features. Simpler than EMHASS but tailored to dual-battery. No optuna needed — GBR defaults work well at this data scale. |
| Self-tuning control | None — uses Linear Programming for optimal schedule, no runtime parameter adaptation | Cell balancing only | None | Unique differentiator: auto-tune dead-bands, ramp rates, min-SoC from actual oscillation and consumption data. No competitor does this for residential. |
| Anomaly detection | None | Cell-level voltage anomaly | None | SoC curve deviation, consumption spike detection, communication failure patterns. Goes beyond BMS-level monitoring. |
| Optimization metric | None built-in | None | Basic self-consumption display | Full scorecard: SCR, SSR, grid import, cost. Daily/weekly/monthly. Tracks improvement over time. |
| Dual-battery coordination | Not applicable (single inverter focus) | Not applicable | Not applicable | Core architecture advantage. ML tuning applies independently per system with coordinated dispatch. |

## Data Requirements Summary

### Already Available (no new integrations needed)

| Data Source | Entity/Measurement | Granularity | Used By |
|-------------|-------------------|-------------|---------|
| HA statistics | outdoor temp entity | Hourly | Weather-aware forecast |
| HA statistics | heat pump power entity | Hourly | Consumption forecast |
| HA statistics | DHW power entity | Hourly | Consumption forecast |
| Open-Meteo | Hourly temperature forecast | Hourly, 72h | Multi-horizon forecast |
| InfluxDB | ems_huawei, ems_victron | 5s | SoC anomaly, efficiency tracking |
| InfluxDB | ems_decision | 5s | Oscillation counting, dead-band tuning |
| EVCC solar forecast | Daily kWh | Daily, 3-day | WeatherScheduler (existing) |

### New Data to Collect

| Data | Source | Storage | Used By |
|------|--------|---------|---------|
| Daily MAPE (predicted vs actual kWh) | Computed from forecast + HA stats | InfluxDB `ems_forecast_accuracy` | Accuracy tracking, self-tuning gate |
| Hourly state transition count | Computed from ems_decision | InfluxDB `ems_oscillation` or in-memory ring buffer | Dead-band self-tuning |
| Weekly round-trip efficiency per system | Computed from ems_huawei/ems_victron charge/discharge totals | InfluxDB `ems_efficiency` | Degradation tracking |
| Self-consumption ratio (daily) | Computed from PV production + grid import/export | InfluxDB `ems_scorecard` | Optimization scorecard |

## Training and Inference Constraints

| Constraint | Requirement | Rationale |
|------------|-------------|-----------|
| Training time | < 2 seconds on arm64 | Must not block the control loop or noticeably delay nightly scheduler |
| Inference time | < 50ms per 72h forecast | Called once per hour max, but should be fast enough for on-demand API calls |
| Memory | < 50 MB model + feature data | HA host has limited RAM; other add-ons compete for resources |
| Training cadence | Once daily (04:00 local, with scheduler) | Already implemented in `retrain_if_stale(24)` |
| Training data window | 90 days rolling | Current implementation. Sufficient for seasonal patterns without overfitting. |
| Model persistence | In-memory only (retrain on restart) | GBR trains in <1s. No need for model serialization complexity. Avoids stale model issues. |
| Fallback | Seasonal constant (existing) | Must always work even if ML fails. Current `_seasonal_fallback_kwh` is correct safety net. |

## Sources

- [EMHASS documentation — ML forecaster](https://emhass.readthedocs.io/en/latest/mlforecaster.html) — MEDIUM confidence, direct competitor analysis
- [EMHASS documentation — forecast module](https://emhass.readthedocs.io/en/latest/forecasts.html) — MEDIUM confidence, feature comparison
- [Gradient Boosting for home energy prediction (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S2352484721001049) — MEDIUM confidence, validates GBR for residential forecasting
- [Load forecasting for battery storage control (MDPI)](https://www.mdpi.com/1996-1073/13/15/3946) — MEDIUM confidence, feature importance validation
- [Seasonal hourly electricity demand forecasting (Nature)](https://www.nature.com/articles/s41598-025-91878-0) — MEDIUM confidence, confirms weather + calendar features
- [Hybrid ML framework for battery anomaly detection (Nature)](https://www.nature.com/articles/s41598-025-90810-w) — MEDIUM confidence, anomaly detection patterns
- [Self-consumption and self-sufficiency metrics (MDPI)](https://www.mdpi.com/1996-1073/14/6/1591) — MEDIUM confidence, metric definitions
- [Optimal PV-BESS household strategy (arXiv)](https://arxiv.org/html/2506.17268) — LOW confidence, academic optimization approach
- Existing codebase: `backend/consumption_forecaster.py`, `backend/config.py`, `backend/orchestrator.py`, `backend/weather_scheduler.py` — HIGH confidence, current implementation analysis

---
*Feature research for: ML self-tuning in dual-battery EMS*
*Researched: 2026-03-23*
