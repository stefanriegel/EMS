# Roadmap: EMS v2 -- Independent Dual-Battery Control

## Overview

Transform the EMS from a unified orchestrator (single-pool SoC, proportional setpoints) into independent per-battery controllers with coordinated dispatch. The roadmap starts with the highest-risk foundation (Victron Modbus TCP driver), builds the core control architecture, layers optimization and integration on top, then delivers the dashboard and deployment packaging. Each phase delivers a coherent, testable capability against real hardware.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Victron Modbus TCP Driver** - Build and verify the Victron driver against real hardware, adapt Huawei driver to unified interface
- [ ] **Phase 2: Independent Controllers & Coordinator** - Per-battery state machines with coordinated dispatch and anti-oscillation
- [ ] **Phase 3: PV & Tariff Optimization** - Smart dispatch with PV surplus distribution, tariff-aware charging, and predictive scheduling
- [ ] **Phase 4: Integration & Monitoring** - External system integrations, per-system APIs, metrics, and decision transparency
- [ ] **Phase 5: Dashboard** - React dashboard rewrite with per-system visibility, decision log, and power flow visualization
- [ ] **Phase 6: Deployment & Hardening** - HA Add-on packaging, service discovery, and setup wizard for dual-controller configuration

## Phase Details

### Phase 1: Victron Modbus TCP Driver
**Goal**: Both battery systems are readable and writable through a uniform driver interface over Modbus TCP
**Depends on**: Nothing (first phase)
**Requirements**: DRV-01, DRV-02, DRV-03, DRV-04, DRV-05, DRV-06
**Success Criteria** (what must be TRUE):
  1. Victron system state (SoC, per-phase power, grid power, ESS mode) can be read via Modbus TCP from the Venus OS GX device
  2. ESS setpoints (total and per-phase AC power) can be written to the Victron system via Modbus TCP and the inverter responds within 2 seconds
  3. Victron Modbus unit IDs are configurable at startup (not hardcoded)
  4. Huawei driver works through the same abstract interface as the Victron driver (read state, write setpoint)
  5. Both drivers use canonical sign convention (positive = charge, negative = discharge) with conversion only inside the driver
**Plans**: 3 plans

Plans:
- [x] 01-01-PLAN.md — Modbus TCP driver, BatteryDriver Protocol, VictronConfig, and driver tests
- [x] 01-02-PLAN.md — Protocol conformance verification for both drivers and package exports
- [x] 01-03-PLAN.md — Gap closure: fix VictronDriver instantiation in main.py

### Phase 2: Independent Controllers & Coordinator
**Goal**: Each battery system operates through its own controller with the coordinator allocating demand -- no oscillation, no cross-system coupling
**Depends on**: Phase 1
**Requirements**: CTRL-01, CTRL-02, CTRL-03, CTRL-04, CTRL-05, CTRL-06, CTRL-07, CTRL-08
**Success Criteria** (what must be TRUE):
  1. Each battery has a dedicated controller with its own state machine that transitions between roles (PRIMARY_DISCHARGE, SECONDARY_DISCHARGE, CHARGING, HOLDING, GRID_CHARGE) based on SoC, tariff, and PV conditions
  2. When coordinator reassigns load between systems, total household power remains stable (no visible spikes or drops)
  3. When one battery system loses communication, it enters safe state (zero-power) while the other continues operating unaffected
  4. Setpoint changes ramp smoothly (soft-start/soft-stop) and stay within per-system hysteresis dead-bands (Huawei ~300-500W, Victron ~100-200W)
  5. Higher-SoC system discharges first; coordinator never directly writes to hardware
**Plans**: 3 plans

Plans:
- [x] 02-01-PLAN.md — Controller model types (enums, dataclasses) and per-battery controllers (HuaweiController, VictronController) with TDD
- [x] 02-02-PLAN.md — Coordinator class with role assignment, allocation, hysteresis, ramp limiting, and debounce (TDD)
- [x] 02-03-PLAN.md — Integration wiring: update main.py lifespan and API layer for coordinator

### Phase 3: PV & Tariff Optimization
**Goal**: The system makes intelligent charge/discharge decisions based on PV surplus, tariff windows, solar forecasts, and time-of-day profiles
**Depends on**: Phase 2
**Requirements**: OPT-01, OPT-02, OPT-03, OPT-04, OPT-05
**Success Criteria** (what must be TRUE):
  1. PV surplus is distributed across both batteries weighted by SoC headroom and charge rate limits (not split 50/50)
  2. During cheap tariff windows, each battery charges independently at its own rate with the faster charger starting first
  3. Grid charge is skipped when solar forecast covers expected demand (predictive pre-charging)
  4. Min-SoC floors change by time-of-day (e.g., 30% until 16:00, 10% after 22:00) per configurable profiles
**Plans**: 2 plans

Plans:
- [x] 03-01-PLAN.md — Coordinator optimizations: SoC-headroom PV surplus weighting, time-of-day min-SoC profiles, grid charge staggering verification
- [x] 03-02-PLAN.md — Scheduler predictive pre-charging: solar-aware grid charge target reduction

### Phase 4: Integration & Monitoring
**Goal**: All external systems (EVCC, InfluxDB, HA, Telegram) integrate with the dual-battery architecture and every dispatch decision is traceable
**Depends on**: Phase 2
**Requirements**: INT-01, INT-02, INT-03, INT-04, INT-05, INT-06, INT-07, INT-08
**Success Criteria** (what must be TRUE):
  1. EVCC hold signal reaches both controllers and both systems respond appropriately
  2. Per-system SoC, power, role, and health are available via REST API
  3. Every external integration (InfluxDB, EVCC, HA, Telegram) degrades gracefully when unavailable -- system continues operating
  4. Each dispatch decision is logged with structured reasoning (WHY this allocation, not just WHAT)
  5. InfluxDB stores separate measurements for Huawei and Victron; HA MQTT discovery publishes per-system entities
**Plans**: 3 plans

Plans:
- [ ] 04-01-PLAN.md — Models (DecisionEntry, IntegrationStatus), per-system InfluxDB write methods, HA MQTT entity expansion
- [ ] 04-02-PLAN.md — Coordinator wiring: decision ring buffer, integration health tracking, per-cycle InfluxDB/HA MQTT calls
- [ ] 04-03-PLAN.md — API endpoints: /api/decisions, expanded /api/health, per-system role fields in /api/state and /api/devices

### Phase 5: Dashboard
**Goal**: Users see and understand both battery systems, their roles, power flows, and the reasoning behind dispatch decisions
**Depends on**: Phase 4
**Requirements**: UI-01, UI-02, UI-03, UI-04, UI-05
**Success Criteria** (what must be TRUE):
  1. Dashboard shows per-system state (SoC, power, role, health) for both Huawei and Victron simultaneously
  2. Last N coordinator decisions are visible with human-readable reasoning for each
  3. Power flow visualization shows per-system charge/discharge with direction indicators
  4. Tariff schedule view shows per-battery charge targets and upcoming windows
**Plans**: TBD

Plans:
- [ ] 05-01: TBD
- [ ] 05-02: TBD

### Phase 6: Deployment & Hardening
**Goal**: The dual-battery EMS runs as a production HA Add-on with automated service discovery and guided first-run setup
**Depends on**: Phase 5
**Requirements**: DEP-01, DEP-02, DEP-03
**Success Criteria** (what must be TRUE):
  1. HA Add-on installs and runs on both aarch64 and amd64 architectures
  2. Supervisor service discovery automatically detects MQTT broker, EVCC, and InfluxDB
  3. Setup wizard guides configuration of both battery systems including Victron Modbus host, port, and unit IDs
**Plans**: TBD

Plans:
- [ ] 06-01: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Victron Modbus TCP Driver | 2/3 | Gap closure planned | - |
| 2. Independent Controllers & Coordinator | 0/3 | Planned | - |
| 3. PV & Tariff Optimization | 0/2 | Planned | - |
| 4. Integration & Monitoring | 0/3 | Planned | - |
| 5. Dashboard | 0/? | Not started | - |
| 6. Deployment & Hardening | 0/? | Not started | - |
