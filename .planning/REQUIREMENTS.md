# Requirements: EMS v1.1 -- Advanced Optimization

**Defined:** 2026-03-23
**Core Value:** Both battery systems operate independently with zero oscillation to maximize PV self-consumption

## v1.1 Requirements

Requirements for this milestone. Each maps to roadmap phases.

### Self-Consumption Optimization

- [x] **SCO-01**: System never actively discharges battery to grid — export only from direct PV surplus when batteries are full
- [x] **SCO-02**: Feed-in rate configurable as a single EUR/kWh value (default 0.074) in setup config and HA Add-on options
- [x] **SCO-03**: Seasonal self-consumption strategy — winter prioritizes battery reserves and more aggressive grid charging; summer allows natural PV export when batteries full
- [x] **SCO-04**: Self-consumption and export decisions logged with structured reasoning in /api/decisions

### Multi-Day Scheduling

- [ ] **MDS-01**: Scheduler looks 2-3 days ahead using EVCC solar forecast data and Open-Meteo as fallback when EVCC is unavailable
- [ ] **MDS-02**: Nightly grid charge targets adjusted by multi-day forecast — charge more before cloudy stretches, reduce/skip when sunny days ahead (forward-looking demand vs. solar comparison)
- [ ] **MDS-03**: Confidence-weighted forecast discounting — Day 1 at full weight, Day 2 at ~80%, Day 3 at ~60%
- [ ] **MDS-04**: Intra-day re-planning — re-run schedule approximately every 6 hours when forecast deviates significantly from plan
- [ ] **MDS-05**: DayPlan model evolution — ChargeSchedule extended with per-day containers and day index for multi-day slot management
- [ ] **MDS-06**: ConsumptionForecaster extended to predict hourly demand for a 72-hour horizon
- [ ] **MDS-07**: Conservative charge ceiling — grid charge targets leave headroom proportional to forecast uncertainty so unexpected PV surplus can still be absorbed

### Dashboard

- [ ] **DSH-01**: Energy flow visualization shows export indicator when PV surplus goes to grid
- [ ] **DSH-02**: Multi-day solar forecast visualization showing expected solar production for the next 2-3 days
- [ ] **DSH-03**: Charge schedule view shows multi-day plan with per-day breakdown

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Advanced Optimization

- **ADV-03**: Battery degradation-aware dispatch (cycle count, temperature)

### Ecosystem

- **ECO-01**: Generic N-battery support (third battery system)
- **ECO-02**: Grafana dashboard templates for per-battery metrics

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Dynamic export rate tracking | Fixed feed-in rate (7.4 ct/kWh); dynamic rates not applicable |
| Active battery-to-grid discharge for arbitrage | Feed-in rate (0.074) always below any import rate; net loss after round-trip efficiency |
| Weather API key management | Open-Meteo is free and keyless; no API key infrastructure needed |
| Grid arbitrage (buy-low-sell-high via battery) | Economics don't support it with fixed German feed-in rates |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| SCO-01 | Phase 7 | Complete |
| SCO-02 | Phase 7 | Complete |
| SCO-03 | Phase 8 | Complete |
| SCO-04 | Phase 7 | Complete |
| MDS-01 | Phase 9 | Pending |
| MDS-02 | Phase 10 | Pending |
| MDS-03 | Phase 10 | Pending |
| MDS-04 | Phase 10 | Pending |
| MDS-05 | Phase 10 | Pending |
| MDS-06 | Phase 9 | Pending |
| MDS-07 | Phase 10 | Pending |
| DSH-01 | Phase 11 | Pending |
| DSH-02 | Phase 11 | Pending |
| DSH-03 | Phase 11 | Pending |

**Coverage:**
- v1.1 requirements: 14 total
- Mapped to phases: 14
- Unmapped: 0

---
*Requirements defined: 2026-03-23*
*Last updated: 2026-03-23 after roadmap creation*
