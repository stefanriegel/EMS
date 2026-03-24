# EMS v2 — Independent Dual-Battery Energy Management

## What This Is

A complete rewrite of the Energy Management System that controls two physically and logically separate battery systems (Huawei LUNA2000 via Modbus TCP and Victron MultiPlus-II via Modbus TCP) as independent units with coordinated dispatch. Each system has its own control path, setpoint logic, and failure handling. The system maximizes PV self-consumption, supports dynamic tariff optimization, and runs as a Home Assistant Add-on.

## Core Value

Both battery systems operate independently with zero oscillation — coordinated but never coupled — to maximize PV self-consumption across the combined 94 kWh pool.

## Requirements

### Validated

- ✓ Huawei LUNA2000 driver (Modbus TCP read/write) — existing
- ✓ FastAPI async backend with lifespan management — existing
- ✓ InfluxDB time-series metrics (optional, graceful degradation) — existing
- ✓ Home Assistant Add-on packaging and Supervisor integration — existing
- ✓ Tariff engine (Octopus Go + Modul3 composite) — existing
- ✓ EVCC coordination (HTTP API + MQTT monitoring) — existing
- ✓ Telegram alert notifications — existing
- ✓ HA MQTT discovery and entity publishing — existing
- ✓ ML consumption forecaster (HA SQLite statistics) — existing
- ✓ JWT-based authentication — existing
- ✓ Setup wizard for first-run configuration — existing (removed in v1.2, replaced by Add-on options)
- ✓ Victron MultiPlus-II Modbus TCP driver (read state + write setpoints) — Phase 1
- ✓ Unified driver interface (LifecycleDriver + BatteryDriver Protocol) — Phase 1
- ✓ Canonical sign convention (positive=charge) with per-driver conversion — Phase 1
- ✓ Independent control paths per battery system (HuaweiController, VictronController) — Phase 2
- ✓ Dynamic role assignment (PRIMARY_DISCHARGE, SECONDARY_DISCHARGE, CHARGING, HOLDING, GRID_CHARGE) based on SoC — Phase 2
- ✓ Anti-oscillation: per-system hysteresis dead-bands (Huawei 300W, Victron 150W), ramp limiting, 2-cycle debounce — Phase 2
- ✓ Coordinated dispatch: Coordinator assigns roles and allocates watts without direct driver access — Phase 2
- ✓ Per-system failure isolation (3 consecutive failures → safe state, survivor gets full P_target) — Phase 2
- ✓ SoC-headroom-weighted PV surplus distribution (not 50/50 or Huawei-first) — Phase 3
- ✓ Tariff-aware grid charging with per-battery independent charge rates — Phase 3
- ✓ Predictive pre-charging: skip/reduce grid charge when solar forecast covers demand — Phase 3
- ✓ Time-of-day min-SoC profiles per battery system (configurable windows) — Phase 3
- ✓ EVCC hold signal propagated to both controllers via coordinator — Phase 4
- ✓ Per-system SoC, power, role, and health exposed via REST API — Phase 4
- ✓ All external integrations optional with graceful degradation — Phase 4
- ✓ Decision transparency: structured ring buffer with /api/decisions endpoint — Phase 4
- ✓ Per-system InfluxDB metrics (ems_huawei, ems_victron, ems_decision) — Phase 4
- ✓ HA MQTT discovery: 17 per-system entities with role, power, availability — Phase 4
- ✓ Dual-battery dashboard with per-system SoC, power, role badges, and 5-node energy flow — Phase 5
- ✓ Decision log view with expandable reasoning and REST polling — Phase 5
- ✓ Per-battery tariff timeline with charge slot visualization — Phase 5
- ✓ Collapsible DeviceDetail with role prominence — Phase 5
- ✓ Consolidated multi-stage Dockerfile (Node.js frontend build + HA base Python runtime) — Phase 6
- ✓ HA Add-on config schema with Victron unit IDs, coordinator tuning, and Modul3 tariff fields — Phase 6
- ✓ Setup wizard backend migrated from Victron MQTT to Modbus TCP with pymodbus probe — Phase 6
- ✓ Setup wizard frontend with Advanced unit ID toggle and Modul3 tariff fields — Phase 6
- ✓ ExportAdvisor with forward-looking consumption reserve and feed-in rate config — Phase 7
- ✓ EXPORTING battery role with seasonal min-SoC boost (winter priority) — Phase 8
- ✓ Multi-day solar forecast (EVCC + Open-Meteo fallback) and 72h consumption prediction — Phase 9
- ✓ WeatherScheduler with DayPlan model, confidence weighting, intra-day re-planning — Phase 10
- ✓ Dashboard export indicator, solar forecast card, multi-day charge schedule view — Phase 11
- ✓ Setup wizard removed — Add-on options page sole config surface — Phase 12
- ✓ MQTT discovery overhaul: EntityDefinition model, 3 HA devices, LWT availability, origin metadata, binary sensors, translations — Phase 13
- ✓ Controllable entities: 5 number + 1 select + 2 button entities with MQTT subscribe, mode override, auto-timeout — Phase 14
- ✓ HA Ingress support: ASGI middleware, auth bypass, relative frontend paths, dynamic WebSocket URL — Phase 15

- ✓ ModelStore with joblib persistence and sklearn version-aware model caching — Phase 16
- ✓ FeaturePipeline with cached multi-source extraction (HA statistics + InfluxDB) — Phase 16
- ✓ Non-blocking sklearn training via anyio executor with OMP_NUM_THREADS=2 — Phase 16
- ✓ HistGradientBoostingRegressor with 8-feature matrix (weather, calendar, lag), recency weighting, and time-series CV — Phase 17
- ✓ Daily MAPE tracking with /api/ml/status endpoint — Phase 17
- ✓ AnomalyDetector with 3 detection domains, tiered alert escalation, nightly IsolationForest — Phase 18
- ✓ Anomaly REST API (/api/anomaly/events) and Telegram notifications — Phase 18
- ✓ SelfTuner with adaptive dead-band/ramp/min-SoC tuning, 14-day shadow mode, bounded changes, automatic rollback — Phase 19

### Active

(No active milestone — planning next)

### Out of Scope

- Virtual coupling / parallel battery aggregation — fundamentally opposed to the architecture
- Mobile app — web dashboard only
- Cloud connectivity — fully local operation
- Third-party battery brands — Huawei + Victron only for v1
- Victron MQTT control — replaced by Modbus TCP

## Current State

**v1.3 shipped 2026-03-24.** Intelligent Self-Tuning: ML infrastructure (ModelStore, FeaturePipeline, executor offloading), upgraded consumption forecaster (HistGBR, weather features, MAPE tracking), anomaly detection (3 domains, tiered alerts, IsolationForest), and self-tuning control parameters (shadow mode, bounded changes, automatic rollback).

**Codebase:**
- Backend: ~14,200 LOC Python (FastAPI, pymodbus, paho-mqtt, scikit-learn)
- Frontend: ~2,600 LOC TypeScript/React (Vite, wouter)
- Tests: ~18,300 LOC across 1,509 tests
- 4 new ML modules: model_store, feature_pipeline, anomaly_detector, self_tuner

**Hardware environment:**
- Huawei SUN2000 inverter with LUNA2000 battery (30 kWh) — Modbus TCP on port 502
- Victron MultiPlus-II with Pylontech/similar (64 kWh) — Venus OS GX device via Modbus TCP
- EVCC for EV charging optimization (co-installed HA add-on)
- Home Assistant OS as the host platform

**Architecture:** Each battery system has a dedicated controller (HuaweiController, VictronController) receiving instructions from a Coordinator. SoC-based role assignment (PRIMARY_DISCHARGE, SECONDARY_DISCHARGE, CHARGING, HOLDING, GRID_CHARGE) with per-system hysteresis, ramp limiting, and failure isolation. PV surplus distributed by SoC headroom weighting. Solar-aware grid charge target reduction.

**Known areas needing field validation:**
- Victron Venus OS Modbus register addresses vs. actual firmware (v3.20+)
- Unit ID assignments need probing or manual config on real hardware
- Self-tuner shadow mode needs 14+ days of production data before live parameter adjustment
- Anomaly detection thresholds may need real-world calibration
- MAPE activation gate (25% threshold, 60 days) needs production validation

## Constraints

- **Deployment**: Must run as HA Add-on (primary) — Docker container on aarch64/amd64
- **Hardware**: Huawei Modbus TCP, Victron Modbus TCP (replacing MQTT)
- **Stack**: Python 3.12+ (FastAPI/uvicorn), React 19+ (Vite), TypeScript
- **Network**: Local network only, no cloud dependencies
- **Graceful degradation**: Every external dependency (InfluxDB, EVCC, HA, Telegram) must be optional
- **Safety**: Each battery must enter safe state independently on communication loss

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Fresh rewrite over incremental refactor | Current unified orchestrator architecture is fundamentally incompatible with independent control | ✓ Validated v1.0 |
| Victron Modbus TCP instead of MQTT | More precise ESS control via direct register writes | ✓ Validated Phase 1+6 |
| Dynamic roles instead of fixed specialization | SoC/tariff/PV conditions change throughout the day; fixed roles waste capacity | ✓ Validated Phase 2+3 |
| SoC-headroom weighting for PV surplus | Proportional distribution by available capacity, not battery order | ✓ Validated Phase 3 |
| Predictive pre-charging with solar forecast | Skip grid charge when solar covers demand (1.2x threshold) | ✓ Validated Phase 3 |
| Independent controllers with coordinator pattern | Prevents oscillation while allowing optimization; each system is autonomous | ✓ Validated Phase 2 |
| Add-on options as sole config surface | Setup wizard adds complexity; HA users expect Add-on config page | ✓ Validated Phase 12 |
| MQTT discovery with EntityDefinition dataclass | Typed entity registry enables clean extension for controllable entities | ✓ Validated Phase 13+14 |
| Three HA devices (Huawei/Victron/System) | Matches physical hardware reality; cleaner entity grouping | ✓ Validated Phase 13 |
| Bidirectional MQTT control via number/select/button | Enables HA automation control without custom integration | ✓ Validated Phase 14 |
| HA Ingress with ASGI middleware | Raw ASGI handles both HTTP and WebSocket; simpler than BaseHTTPMiddleware | ✓ Validated Phase 15 |
| HistGradientBoostingRegressor for consumption forecasting | Native NaN handling eliminates imputation complexity; early_stopping prevents overfitting | ✓ Validated Phase 17 |
| JSON file persistence for ML state (/config/ems_models/) | No DB dependency; survives container restarts; consistent across ModelStore/MAPE/anomaly/tuning | ✓ Validated Phase 16-19 |
| Pre-computed thresholds for per-cycle anomaly checks | No sklearn predict in 5s loop; float comparisons only; IsolationForest runs nightly | ✓ Validated Phase 18 |
| 14-day shadow mode before live self-tuning | Prevents premature parameter changes; logs recommended vs actual for confidence building | ✓ Validated Phase 19 |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
---
*Last updated: 2026-03-24 after v1.3 milestone completion*
