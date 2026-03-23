# Milestones

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
