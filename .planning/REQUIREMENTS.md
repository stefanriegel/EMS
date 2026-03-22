# Requirements: EMS v2 -- Independent Dual-Battery Control

**Defined:** 2026-03-22
**Core Value:** Both battery systems operate independently with zero oscillation to maximize PV self-consumption

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Independent Control

- [ ] **CTRL-01**: Each battery system has a dedicated controller with its own state machine, hysteresis, and debounce
- [ ] **CTRL-02**: Coordinator allocates demand across controllers without directly writing to hardware
- [ ] **CTRL-03**: Per-system hysteresis dead-band: Huawei ~300-500 W, Victron ~100-200 W (configurable)
- [ ] **CTRL-04**: Each controller enters safe state independently on communication loss (zero-power, no cross-system impact)
- [ ] **CTRL-05**: Total household power remains stable when coordinator reassigns load between systems
- [ ] **CTRL-06**: Dynamic role assignment (PRIMARY_DISCHARGE, SECONDARY_DISCHARGE, CHARGING, HOLDING, GRID_CHARGE) based on SoC, tariff, PV
- [ ] **CTRL-07**: Anti-oscillation ramps: soft-start/soft-stop with configurable ramp rate per system
- [ ] **CTRL-08**: SoC-based discharge priority: higher-SoC system discharges first

### Drivers

- [x] **DRV-01**: Victron MultiPlus-II controlled via Modbus TCP (replacing MQTT)
- [x] **DRV-02**: Victron Modbus TCP driver reads system state (SoC, per-phase power, grid power, ESS mode)
- [x] **DRV-03**: Victron Modbus TCP driver writes ESS setpoints (total and per-phase AC power)
- [x] **DRV-04**: Victron Modbus unit IDs configurable (not hardcoded)
- [ ] **DRV-05**: Huawei driver retained from v1, adapted to work with per-battery controller interface
- [x] **DRV-06**: Canonical sign convention: positive = charge, negative = discharge, conversion only in drivers

### PV & Tariff Optimization

- [ ] **OPT-01**: PV surplus distributed across both batteries based on SoC headroom and charge rate limits
- [ ] **OPT-02**: Tariff-aware grid charging targets each battery independently (different charge rates)
- [ ] **OPT-03**: Charge rate optimization: stagger charging in short tariff windows (faster charger first)
- [ ] **OPT-04**: Predictive pre-charging: skip grid charge when solar forecast covers demand
- [ ] **OPT-05**: Configurable min-SoC per time-of-day profiles (e.g., 30% until 16:00, 10% after 22:00)

### Integration & Monitoring

- [ ] **INT-01**: EVCC hold signal propagated to both controllers
- [ ] **INT-02**: Per-system SoC, power, and health exposed via API
- [ ] **INT-03**: All external integrations optional (InfluxDB, EVCC, HA, Telegram)
- [ ] **INT-04**: Decision transparency: structured log of WHY each dispatch decision was made
- [ ] **INT-05**: Phase-aware Victron dispatch: per-phase L1/L2/L3 setpoints based on per-phase load
- [ ] **INT-06**: Per-battery nightly charge targets from scheduler
- [ ] **INT-07**: Per-system metrics in InfluxDB (separate measurements for Huawei and Victron)
- [ ] **INT-08**: HA MQTT discovery publishes per-system entities

### Frontend

- [ ] **UI-01**: Reworked dashboard showing per-system state (SoC, power, role, health)
- [ ] **UI-02**: Decision log view: last N coordinator decisions with reasoning
- [ ] **UI-03**: Per-system power flow visualization
- [ ] **UI-04**: Role indicator per battery (PRIMARY/SECONDARY/HOLDING/CHARGING)
- [ ] **UI-05**: Tariff schedule with per-battery charge targets

### Deployment

- [ ] **DEP-01**: HA Add-on as primary deployment target (aarch64 + amd64)
- [ ] **DEP-02**: Supervisor service discovery for MQTT, EVCC, InfluxDB
- [ ] **DEP-03**: Setup wizard updated for dual-controller config (Victron Modbus host/port/unit IDs)

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Advanced Optimization

- **ADV-01**: Grid export optimization with feed-in tariff management
- **ADV-02**: Multi-day scheduling with weather window optimization
- **ADV-03**: Battery degradation-aware dispatch (cycle count, temperature)

### Ecosystem

- **ECO-01**: Generic N-battery support (third battery system)
- **ECO-02**: Grafana dashboard templates for per-battery metrics

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Virtual coupling / unified battery pool | Fundamentally opposed to the architecture -- this is the v1 problem being solved |
| Proportional setpoint splitting | Special case of virtual coupling; causes oscillation with asymmetric systems |
| Real-time SoC balancing between batteries | Wastes 15-20% energy through double conversion losses |
| Cloud-based optimization | Adds latency, requires internet, 5s control loop incompatible with cloud round-trip |
| Mobile app | Responsive web dashboard is sufficient; HA companion app provides notifications |
| Automatic inverter firmware updates | Safety risk; manufacturer's domain |
| Victron MQTT control | Replaced by Modbus TCP for precise ESS control |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CTRL-01 | Phase 2 | Pending |
| CTRL-02 | Phase 2 | Pending |
| CTRL-03 | Phase 2 | Pending |
| CTRL-04 | Phase 2 | Pending |
| CTRL-05 | Phase 2 | Pending |
| CTRL-06 | Phase 2 | Pending |
| CTRL-07 | Phase 2 | Pending |
| CTRL-08 | Phase 2 | Pending |
| DRV-01 | Phase 1 | Complete |
| DRV-02 | Phase 1 | Complete |
| DRV-03 | Phase 1 | Complete |
| DRV-04 | Phase 1 | Complete |
| DRV-05 | Phase 1 | Pending |
| DRV-06 | Phase 1 | Complete |
| OPT-01 | Phase 3 | Pending |
| OPT-02 | Phase 3 | Pending |
| OPT-03 | Phase 3 | Pending |
| OPT-04 | Phase 3 | Pending |
| OPT-05 | Phase 3 | Pending |
| INT-01 | Phase 4 | Pending |
| INT-02 | Phase 4 | Pending |
| INT-03 | Phase 4 | Pending |
| INT-04 | Phase 4 | Pending |
| INT-05 | Phase 4 | Pending |
| INT-06 | Phase 4 | Pending |
| INT-07 | Phase 4 | Pending |
| INT-08 | Phase 4 | Pending |
| UI-01 | Phase 5 | Pending |
| UI-02 | Phase 5 | Pending |
| UI-03 | Phase 5 | Pending |
| UI-04 | Phase 5 | Pending |
| UI-05 | Phase 5 | Pending |
| DEP-01 | Phase 6 | Pending |
| DEP-02 | Phase 6 | Pending |
| DEP-03 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 30 total
- Mapped to phases: 30
- Unmapped: 0

---
*Requirements defined: 2026-03-22*
*Last updated: 2026-03-22 after roadmap creation*
