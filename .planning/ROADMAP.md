# Roadmap: EMS v1.1 Advanced Optimization

## Milestones

- ✅ **v1.0 Independent Dual-Battery EMS** - Phases 1-6 (shipped 2026-03-23)
- 🚧 **v1.1 Advanced Optimization** - Phases 7-11 (in progress)

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

<details>
<summary>v1.0 Independent Dual-Battery EMS (Phases 1-6) - SHIPPED 2026-03-23</summary>

Phase 1: Victron Modbus Driver - 3 plans, complete
Phase 2: Dual-Battery Coordinator - 3 plans, complete
Phase 3: PV & Tariff Optimization - 2 plans, complete
Phase 4: Integration & Observability - 3 plans, complete
Phase 5: Dashboard - 2 plans, complete
Phase 6: Deployment & Hardening - 3 plans, complete

</details>

### v1.1 Advanced Optimization

**Milestone Goal:** Maximize economic value through grid export arbitrage against a fixed feed-in tariff and multi-day weather-aware scheduling

- [ ] **Phase 7: Export Foundation** - Feed-in rate config and ExportAdvisor with export-vs-store decision logic
- [ ] **Phase 8: Coordinator Export Integration** - EXPORTING battery role, seasonal strategy, and coordinator wiring
- [ ] **Phase 9: Weather & Forecast Data** - Multi-day solar forecast client and 72-hour consumption forecaster extension
- [ ] **Phase 10: Multi-Day Scheduling** - DayPlan model, WeatherScheduler, confidence weighting, and intra-day re-planning
- [ ] **Phase 11: Dashboard & API** - Export indicator, multi-day forecast visualization, and multi-day schedule view

## Phase Details

### Phase 7: Export Foundation
**Goal**: System can evaluate whether PV surplus should be exported or stored, based on economic analysis of fixed feed-in rate vs. future import costs
**Depends on**: Phase 6 (v1.0 complete)
**Requirements**: SCO-01, SCO-02, SCO-04
**Success Criteria** (what must be TRUE):
  1. Feed-in rate is configurable in setup config and HA Add-on options as a single EUR/kWh value
  2. System never commands a battery to discharge energy to the grid — export occurs only from direct PV surplus when batteries are full
  3. ExportAdvisor produces STORE/EXPORT decisions with structured reasoning that accounts for forward-looking consumption (no export-then-buyback)
  4. Export and self-consumption decisions appear in /api/decisions with human-readable reasoning
**Plans:** 2 plans
Plans:
- [ ] 07-01-PLAN.md — ExportAdvisor module + config pipeline + tests
- [ ] 07-02-PLAN.md — Coordinator wiring + decision logging

### Phase 8: Coordinator Export Integration
**Goal**: Coordinator executes export decisions in real time with seasonal awareness, adding the EXPORTING battery role to the control loop
**Depends on**: Phase 7
**Requirements**: SCO-03
**Success Criteria** (what must be TRUE):
  1. Coordinator assigns EXPORTING role and routes PV surplus to grid export when ExportAdvisor recommends it
  2. Winter behavior prioritizes battery reserves with more aggressive grid charging; summer allows natural PV export when batteries are full
  3. Export does not cause dual-battery oscillation — only one system exports at a time, P_target offset prevents the other from reacting
**Plans**: TBD

### Phase 9: Weather & Forecast Data
**Goal**: System has multi-day solar production forecasts and extended consumption predictions available for scheduling decisions
**Depends on**: Phase 6 (v1.0 complete)
**Requirements**: MDS-01, MDS-06
**Success Criteria** (what must be TRUE):
  1. Scheduler can access 2-3 day solar forecast data from EVCC, with Open-Meteo as automatic fallback when EVCC is unavailable
  2. ConsumptionForecaster produces hourly demand predictions for a 72-hour horizon
  3. Weather data degrades gracefully — if both EVCC and Open-Meteo are unreachable, system falls back to seasonal averages and continues operating
**Plans**: TBD

### Phase 10: Multi-Day Scheduling
**Goal**: Nightly charge scheduling uses multi-day weather and consumption outlook to set smarter grid charge targets
**Depends on**: Phase 9
**Requirements**: MDS-02, MDS-03, MDS-04, MDS-05, MDS-07
**Success Criteria** (what must be TRUE):
  1. ChargeSchedule is extended with DayPlan containers and day index — Day 2/3 plans are advisory only, never binding charge slots
  2. Nightly grid charge target increases before predicted cloudy stretches and decreases/skips when sunny days are ahead
  3. Forecast confidence is weighted by day — Day 1 at full weight, Day 2 at ~80%, Day 3 at ~60%
  4. Schedule re-runs approximately every 6 hours when actual conditions deviate significantly from the plan
  5. Grid charge targets leave headroom proportional to forecast uncertainty so unexpected PV surplus can still be absorbed
**Plans**: TBD

### Phase 11: Dashboard & API
**Goal**: Users can see export activity, multi-day solar forecasts, and multi-day charge plans in the dashboard
**Depends on**: Phase 8, Phase 10
**Requirements**: DSH-01, DSH-02, DSH-03
**Success Criteria** (what must be TRUE):
  1. Energy flow visualization shows an export indicator when PV surplus flows to the grid
  2. A multi-day solar forecast view displays expected solar production for the next 2-3 days
  3. Charge schedule view shows a multi-day plan with per-day breakdown
**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 7 -> 8 -> 9 -> 10 -> 11
Note: Phase 8 depends on Phase 7. Phase 9 is independent of Phases 7-8 and could overlap. Phase 10 depends on Phase 9. Phase 11 depends on both Phase 8 and Phase 10.

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 7. Export Foundation | v1.1 | 0/2 | Planned | - |
| 8. Coordinator Export Integration | v1.1 | 0/0 | Not started | - |
| 9. Weather & Forecast Data | v1.1 | 0/0 | Not started | - |
| 10. Multi-Day Scheduling | v1.1 | 0/0 | Not started | - |
| 11. Dashboard & API | v1.1 | 0/0 | Not started | - |
