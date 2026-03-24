# Milestones

## v1.4 Production Deployment & Cross-Charge Prevention (Shipped: 2026-03-24)

**Phases completed:** 5 phases, 11 plans, 17 tasks

**Key accomplishments:**

- dry_run flag on all 5 Modbus write methods, connectivity validation, and write-back verification for both Huawei and Victron drivers
- HardwareValidationConfig with 48h read-only validation period gating on both controllers, startup connectivity checks, and safe-state bypass
- CrossChargeDetector with 2-cycle debounce, 100W/200W thresholds, HOLDING mitigation, and episode waste tracking
- Cross-charge detector wired into coordinator with async guard in 6 dispatch paths, InfluxDB metrics, Telegram alerts, and API health status
- Red pulsing SVG cross-charge badge on EnergyFlowCard with conditional waste stats in OptimizationCard and Playwright E2E tests
- HuaweiModeManager state machine with TOU mode activation, shutdown restore, health check re-apply, and crash recovery via working mode read-back
- HuaweiModeManager wired into EMS lifecycle with transition-safe execute(), periodic health checks, coordinator working mode exposure, and HA MQTT sensor entity
- CommissioningManager state machine with READ_ONLY/SINGLE_BATTERY/DUAL_BATTERY staged rollout, shadow mode write suppression, and atomic JSON persistence
- Central _execute_commands() with shadow mode gating, Victron 45s watchdog guard, commissioning API health section, and main.py lifespan wiring
- DESS-aware discharge gating suppresses Huawei discharge during Victron DESS charge windows, with VRM/DESS lifespan wiring and health API visibility

---

## v1.3 Intelligent Self-Tuning (Shipped: 2026-03-24)

**Phases completed:** 4 phases, 9 plans, 14 tasks

**Key accomplishments:**

- ModelStore with joblib persistence and JSON metadata sidecar for sklearn version-aware model caching
- FeaturePipeline with 1-hour cached extraction from HA statistics and optional InfluxDB augmentation
- Non-blocking sklearn training via anyio executor, ModelStore persistence across restarts, and OMP_NUM_THREADS=2 in Docker
- HistGBR with 8-feature matrix (temp, calendar, lag), recency weighting, time-series CV, and real weather forecast integration via OpenMeteoClient
- Daily MAPE tracking with 30-day rolling history, near-zero filtering, and /api/ml/status endpoint exposing model health, training info, and prediction accuracy
- AnomalyDetector with 3 detection domains (comm loss, consumption spikes, battery health), tiered alert escalation, nightly IsolationForest training, and per-cycle float-only threshold checks
- AnomalyDetector wired into coordinator 5s loop, nightly scheduler, REST API (events + battery health), and Telegram alerts with per-type categories
- SelfTuner engine with adaptive dead-band/ramp/min-SoC tuning, 14-day shadow mode, bounded 10%-per-night changes, automatic rollback, and coordinator runtime field injection
- SelfTuner wired into coordinator 5s loop (record_cycle), nightly scheduler (after anomaly training), REST API (/api/ml/status self_tuning section), and all 5 HA command handlers (mark_ha_override)

---

## v1.2 Home Assistant Best Practice Alignment (Shipped: 2026-03-23)

**Phases completed:** 4 phases, 9 plans, 13 tasks

**Key accomplishments:**

- Deleted setup wizard backend (setup_api.py, setup_config.py) and simplified main.py lifespan to use env vars exclusively
- Deleted SetupWizard.tsx (618 LOC) and /setup route; App.tsx now serves only dashboard and login routes with auth-only redirect
- Typed EntityDefinition dataclass with three-device grouping, LWT availability, origin metadata, expire_after, and HA best-practice discovery fields
- Binary sensors for connectivity (huawei/victron online) and running states (grid charge, export) with one-time sensor-to-binary_sensor migration cleanup
- Verified en.yaml covers all 40 config.yaml option and schema keys with human-readable names and descriptions
- MQTT subscribe path with 8 controllable entities (5 number, 1 select, 2 button), BrokenPipeError-guarded subscriptions, and stale-publish health check
- Bidirectional MQTT control flow with 8-entity command handler, mode override in control loop, 60min force-grid-charge timeout, state echo, and Supervisor persistence
- Raw ASGI IngressMiddleware sets root_path from X-Ingress-Path header, with JWT auth bypass for Supervisor-authenticated Ingress requests
- Vite relative base config and dynamic WS/fetch URL construction for HA Ingress compatibility

---

## v1.1 Advanced Optimization (Shipped: 2026-03-23)

**Phases completed:** 5 phases, 10 plans, 17 tasks

**Key accomplishments:**

- ExportAdvisor with forward-looking reserve algorithm and feed_in_rate_eur_kwh flowing through all 10 config touchpoints (default 0.074 EUR/kWh)
- ExportAdvisor wired into Coordinator 5s control loop with transition-only DecisionEntry logging and 30-minute forecast refresh
- BatteryRole.EXPORTING enum and winter_months/winter_min_soc_boost_pct config fields wired through all 11 config touchpoints with 4 validation tests
- Export role wired into coordinator control loop with seasonal min-SoC boost and 9 TDD tests covering role assignment, seasonal boost, and _build_state EXPORTING support
- OpenMeteoClient with 72h solar irradiance from Open-Meteo API, cascading EVCC -> Open-Meteo -> seasonal fallback provider, and SolarForecastMultiDay dataclass
- 72h hourly consumption predictions via ML models with seasonal hour-of-day weighted fallback on cold-start
- WeatherScheduler with 3-day confidence-weighted charge algorithm, DayPlan containers, headroom ceiling, and winter floor
- Forecast deviation detection with 20% threshold gating and 6-hour intra-day replan loop wired into FastAPI lifespan
- REST endpoints for multi-day solar forecast and day plans, TypeScript types mirroring API shapes, and SVG export indicator on Grid node
- ForecastCard with 3-day solar bar chart and OptimizationCard multi-day outlook with advisory badges

---

## v1.0 Independent Dual-Battery EMS (Shipped: 2026-03-23)

**Phases completed:** 6 phases, 16 plans, 29 tasks

**Key accomplishments:**

- Replaced MQTT-based VictronDriver with pymodbus AsyncModbusTcpClient reading system/VE.Bus registers with batched reads, int16 sign handling, and configurable unit IDs
- Two-tier protocol hierarchy verified: LifecycleDriver (both drivers) and BatteryDriver (Victron-only) with 12 structural conformance tests
- Corrected VictronDriver call site in main.py to pass vebus_unit_id and system_unit_id instead of removed discovery_timeout_s
- TDD controller model with BatteryRole/PoolStatus enums, ControllerSnapshot/Command dataclasses, and HuaweiController + VictronController with failure counting, safe state, and ESS mode guard
- Dual-battery coordinator with SoC-based role assignment, per-system hysteresis/ramp limiting, 2-cycle debounce, PV surplus routing, and grid charge handling
- Coordinator and per-battery controllers wired into FastAPI lifespan, API layer serving CoordinatorState with full backward compatibility for existing frontend and tests
- SoC-headroom-weighted PV surplus distribution with time-of-day min-SoC profiles and verified parallel grid charge behavior
- Solar-aware grid charge target reduction: skip charge when solar covers 120% of demand, reduce with 0.8x discount for partial coverage, full charge as safety fallback
- DecisionEntry/IntegrationStatus models, per-system InfluxDB writes (ems_huawei/ems_victron/ems_decision), and 17-entity HA MQTT with CoordinatorState support
- Per-cycle InfluxDB and HA MQTT calls with decision ring buffer, integration health tracking, and EVCC hold verification
- REST endpoints for decision transparency, integration health, and per-system roles via /api/decisions, expanded /api/health, and enriched /api/devices
- Dual-battery BatteryStatus card and 5-node EnergyFlowCard SVG with per-system SoC arcs, role badges, and animated flow paths
- Decision log, optimization timeline bar, device detail restructure, and full dashboard grid wiring with E2E tests
- Multi-stage Dockerfile consolidation removing 33 stale files, plus HA Add-on config extension for Victron Modbus unit IDs, coordinator tuning, and Modul3 tariff fields
- Migrated setup wizard from Victron MQTT to Modbus TCP with unit ID config and pymodbus probe
- Setup wizard migrated from Victron MQTT to Modbus TCP with unit ID Advanced toggle, Modul3 tariff fields, full CSS dark-theme classes, and E2E test coverage

---
