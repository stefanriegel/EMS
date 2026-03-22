# Feature Landscape

**Domain:** Dual-battery energy management system with independent dispatch
**Researched:** 2026-03-22
**Confidence:** MEDIUM (based on domain expertise, current codebase analysis, and established control theory for multi-storage systems; no live web search available for competitive landscape validation)

## Table Stakes

Features users expect. Missing = system is unreliable or unusable.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Independent per-battery control loops | Without this, the two systems fight each other. Current weighted-average SoC approach causes oscillation because Huawei (30 kWh, 500 ms Modbus latency) and Victron (64 kWh, <100 ms response) have fundamentally different characteristics. A unified setpoint proportionally split is mathematically wrong for asymmetric systems. | High | **Core architectural change.** Each battery needs its own controller with its own state machine, hysteresis thresholds, and debounce counters. This is the whole reason for the rewrite. |
| Per-system failure isolation | If Huawei Modbus goes down, Victron must continue operating normally (and vice versa). Current code partially handles this but the unified state model means a failure in one system degrades decisions for the other. | Medium | v1 has partial support. v2 needs complete isolation: each controller enters safe state independently, coordinator adapts dispatch to the remaining system. |
| Per-system hysteresis and dead-band | Huawei needs wider dead-band (~300-500 W) due to slower Modbus response; Victron can use tighter dead-band (~100-200 W) because of faster AC phase response. A single threshold causes either unnecessary Victron oscillation or sluggish Huawei response. | Low | Currently flagged as a concern in CONCERNS.md. Config already needs `huawei_hysteresis_w` and `victron_hysteresis_w` split. |
| Safe state on communication loss | Each battery must independently enter a zero-power safe state if its driver loses contact. Non-negotiable for any production battery system -- hardware damage and safety risk otherwise. | Low | v1 already has this. v2 must preserve it per-controller. |
| PV self-consumption maximization | The primary value proposition: use solar energy before grid. Both batteries should absorb excess PV generation, prioritizing the system with more headroom or lower SoC. Without this, the system has no purpose. | Medium | Requires the coordinator to distribute PV surplus intelligently across both systems based on current SoC, charge rate limits, and remaining capacity. |
| Tariff-aware grid charging | Charge from grid during cheap tariff windows (Octopus Go overnight, Modul3 low-fee windows). Must target each battery independently because they have different charge rates (Huawei 5 kW, Victron 3 kW) and different current SoC levels. | Medium | v1 scheduler already produces per-battery charge slots. v2 needs the controllers to execute those slots independently. |
| Per-system SoC monitoring and reporting | Operators need to see each battery's SoC, power flow, and health independently. A combined SoC hides which system is depleted and which has headroom. | Low | v1 already exposes `huawei_soc_pct` and `victron_soc_pct`. v2 needs richer per-system telemetry: charge/discharge power, temperature, cycle count if available. |
| EVCC coordination | EVCC's `batteryMode=hold` must lock both batteries. When the EV is fast-charging, batteries should not discharge to avoid overloading the house connection. | Low | v1 has this via EVCC MQTT driver. v2 must propagate hold to both controllers. |
| Graceful degradation for all integrations | InfluxDB, EVCC, HA, Telegram must all be optional. System must operate with zero external dependencies beyond the two inverters. | Low | v1 already implements this pattern. v2 must maintain it across the coordinator/controller split. |
| Coordinator-level power stability | Total household power draw from/to grid must remain stable even as individual battery setpoints change. No visible flicker or power swings when the coordinator reassigns load between systems. | High | This is the hardest table-stakes feature. Requires the coordinator to ensure that when one system ramps up, the other ramps down by the same amount, with timing synchronization. |

## Differentiators

Features that set the product apart. Not expected in basic EMS, but high value for a dual-battery setup.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Dynamic role assignment | Instead of fixed "Huawei = base load, Victron = peak", assign roles based on current conditions: SoC, tariff, PV generation, time of day. A nearly-full Victron should discharge first to create headroom for tomorrow's PV, while a low-SoC Huawei should hold. This maximizes the combined 94 kWh pool utilization. | High | Requires a role engine in the coordinator that evaluates conditions each cycle and assigns one of: PRIMARY_DISCHARGE, SECONDARY_DISCHARGE, CHARGING, HOLDING, GRID_CHARGE. Role transitions need their own hysteresis to avoid flip-flopping. |
| Anti-oscillation with soft-start/soft-stop ramps | Instead of step-function setpoint changes (0 W -> 3000 W), ramp power over 3-5 cycles. Prevents voltage transients on the house bus, reduces inverter stress, and eliminates visible flicker. Particularly important when both systems change state simultaneously. | Medium | Implement as a setpoint filter in each controller: `next_setpoint = current + min(delta, max_ramp_rate_per_cycle)`. Ramp rate configurable per system (Huawei can ramp faster, Victron needs gentler ramps for AC coupling stability). |
| SoC-based discharge priority | When both batteries are above min SoC, discharge the one with higher SoC first. This naturally balances the two systems over time without explicit balancing logic, and ensures the larger Victron (64 kWh) is utilized proportionally more during high-consumption periods. | Medium | Simple to implement in the coordinator: sort available controllers by SoC descending, assign discharge to highest-SoC system first, overflow to second system only if first cannot cover load. |
| Charge rate optimization per tariff window | During a cheap tariff window, charge the system with the larger energy deficit first (lower SoC * capacity = more kWh needed). If the window is short, prioritize the system with the higher charge rate (Huawei 5 kW > Victron 3 kW) to maximize captured energy. | Medium | Extends the existing scheduler: instead of parallel charge slots, produce sequential or staggered slots. Charge Huawei first (faster), then Victron, maximizing total energy captured in limited windows. |
| Decision transparency and audit log | Show operators WHY the system made each decision: "Discharging Victron because SoC=87% > Huawei SoC=42%, tariff=high, PV=0 W". Dual-battery systems are harder to understand; transparency builds trust and aids debugging. | Medium | Log structured decision records each cycle. Surface last N decisions in the dashboard. Store in InfluxDB for historical analysis. Each decision record: timestamp, inputs (SoC, tariff, PV, load), chosen action, reasoning string. |
| Per-battery nightly charge targets | Instead of "charge both to 80%", compute per-battery targets: "Huawei to 95% (small capacity, charge fast), Victron to 60% (large capacity, enough for tomorrow's forecast)". Saves grid energy costs by not overcharging. | Low | v1 scheduler already computes per-battery targets. v2 needs to make this more intelligent: factor in next-day solar forecast, per-system discharge efficiency, and historical consumption patterns per day-of-week. |
| Phase-aware Victron dispatch | Victron MultiPlus-II is AC-coupled with per-phase setpoints (L1/L2/L3). Current v1 splits setpoints evenly across phases or mirrors grid import. v2 can optimize: send more power to the phase with higher load, reducing grid import per-phase and avoiding phase imbalance penalties (relevant in German grid code). | Medium | Requires reading per-phase grid power from Victron (already available in VictronSystemData) and computing per-phase setpoints. Coordinator provides total Victron budget; Victron controller distributes across phases. |
| Predictive pre-charging | If tomorrow's solar forecast is low and consumption forecast is high, start grid-charging earlier (in the cheapest window). If solar forecast is abundant, skip grid charging entirely. Goes beyond simple tariff windows to weather-aware optimization. | Medium | Extends the existing ML consumption forecaster + EVCC solar forecast. The scheduler already has solar and consumption inputs; this refines the logic to produce "no charge needed" decisions when solar covers demand. |
| Configurable min-SoC per time-of-day | Instead of a static min SoC (e.g., 10%), allow time-based profiles: "keep 30% until 16:00 (afternoon peak), allow down to 10% after 22:00 (overnight, cheap grid available)". Prevents morning depletion while maximizing afternoon self-consumption. | Low | New config structure: list of `(time_range, min_soc_pct)` tuples. Controller uses the active time window's threshold. Falls back to static min SoC if no profile configured. |

## Anti-Features

Features to explicitly NOT build.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Virtual coupling / unified battery pool | This is the exact problem v1 has. Treating both batteries as one pool with weighted-average SoC produces incorrect setpoints for asymmetric systems, causes oscillation, and prevents independent failure handling. Every multi-battery EMS that aggregates into a virtual pool eventually hits the same oscillation problems. | Independent controllers with a coordinator. Each system is autonomous; the coordinator optimizes total output but never computes a "combined setpoint" that gets split. |
| Proportional setpoint splitting | Computing a total target watts and splitting 30/64 proportionally ignores that the two systems have different response times, different charge/discharge curves, and different AC coupling characteristics. Proportional splitting is a special case of virtual coupling. | Role-based dispatch: assign PRIMARY/SECONDARY roles, dispatch to one system at a time when possible, use the second only for overflow. |
| Real-time SoC balancing between batteries | Actively transferring energy between batteries (discharge one to charge the other) wastes energy through double conversion losses (DC->AC->DC, ~15-20% loss). It also stresses both inverters unnecessarily. | Let natural usage patterns balance over time: discharge the higher-SoC system first, charge the lower-SoC system first. SoC converges naturally without explicit balancing. |
| Cloud-based optimization | Adds latency, creates a single point of failure, requires internet, and sends household consumption data to third parties. For a 5-second control loop, cloud round-trip latency is unacceptable. | All optimization runs locally. The ML forecaster and scheduler already run on-device. Keep it that way. |
| Generic multi-battery support (N batteries) | Abstracting for N batteries when we have exactly 2 known systems (Huawei + Victron) adds complexity without value. The two systems have fundamentally different protocols (Modbus TCP vs Modbus TCP/MQTT), different capabilities, and different characteristics. A generic abstraction would be a leaky abstraction. | Purpose-built controllers for Huawei and Victron. Share interfaces (Protocol classes) but not implementation. If a third battery is ever added, refactor then -- YAGNI. |
| Automatic inverter firmware updates | Too dangerous for production battery systems. A bad firmware update could brick an inverter or cause safety issues. This is the manufacturer's domain. | Document supported firmware versions. Log warnings if detected firmware is outside tested range. |
| Grid export optimization / feed-in management | German regulations and UK Smart Export Guarantee add complex regulatory requirements. Feed-in tariff optimization is a separate product concern that requires certified metering and regulatory compliance. | Focus on self-consumption and grid-charge. Export is a byproduct of PV surplus, not an optimization target. Can be added as a future milestone if regulatory requirements are met. |
| Mobile app | Web dashboard served by FastAPI is sufficient. A mobile app doubles the frontend maintenance burden for minimal benefit -- the dashboard is already accessible on mobile browsers. HA companion app provides notifications. | Responsive web dashboard. Ensure it works well on mobile viewports. |

## Feature Dependencies

```
Independent control loops ─────┬──> Dynamic role assignment
                                ├──> SoC-based discharge priority
                                ├──> Anti-oscillation ramps
                                └──> Phase-aware Victron dispatch

Per-system failure isolation ──> Independent control loops (prerequisite)

Per-system hysteresis ─────────> Independent control loops (each controller owns its thresholds)

Coordinator power stability ───> Independent control loops + Anti-oscillation ramps

Tariff-aware grid charging ────> Per-battery nightly charge targets
                                └──> Charge rate optimization per tariff window

ML consumption forecaster ─────> Predictive pre-charging
EVCC solar forecast ───────────> Predictive pre-charging

Decision transparency ─────────> Dynamic role assignment (needs role decisions to log)
                                └──> Independent control loops (needs per-system state)

Configurable min-SoC profiles ─> Independent control loops (each controller respects its profile)

EVCC coordination ─────────────> Independent control loops (hold signal propagated to both)
```

## MVP Recommendation

**Phase 1 -- Foundation (must ship first):**
1. Independent per-battery control loops with dedicated state machines
2. Per-system failure isolation
3. Per-system hysteresis and dead-band (configurable per system)
4. Safe state on communication loss (preserve from v1)
5. PV self-consumption with basic coordinator (round-robin or SoC-based priority)

**Phase 2 -- Optimization:**
6. Dynamic role assignment (PRIMARY/SECONDARY/HOLDING/CHARGING)
7. Anti-oscillation ramps (soft-start/soft-stop)
8. Coordinator-level power stability
9. SoC-based discharge priority

**Phase 3 -- Intelligence:**
10. Tariff-aware grid charging with per-battery targets
11. Charge rate optimization per tariff window
12. Predictive pre-charging (solar + consumption forecasts)
13. Configurable min-SoC per time-of-day

**Phase 4 -- Visibility:**
14. Decision transparency and audit log
15. Phase-aware Victron dispatch
16. Per-system metrics dashboard rework

**Defer indefinitely:**
- Grid export optimization: regulatory complexity, not core value
- Generic N-battery support: YAGNI, only 2 systems exist
- Mobile app: responsive web is sufficient

**Phase ordering rationale:**
- Phase 1 must come first because every other feature depends on independent control loops existing.
- Phase 2 before Phase 3 because optimization logic (roles, ramps, stability) is needed before the scheduler can target individual controllers effectively.
- Phase 3 before Phase 4 because intelligence features generate the decisions that transparency features need to display.
- Phase 4 is last because the system must work correctly before it needs to explain itself -- and dashboard rework can use the final API shape.

## Sources

- Current codebase analysis: `backend/orchestrator.py`, `backend/scheduler.py`, `backend/unified_model.py`, `backend/config.py`
- Known concerns: `.planning/codebase/CONCERNS.md` (hysteresis per-system gap, driver lifecycle issues)
- Project requirements: `.planning/PROJECT.md` (validated and active requirements)
- Control theory for multi-storage dispatch: established patterns for coordinator/controller hierarchies in distributed energy systems (training data, LOW-MEDIUM confidence for competitive landscape claims)
- Note: Web search was unavailable for this research session. Competitive landscape claims (what commercial dual-battery EMS products offer) are based on domain knowledge and should be validated against products like SolarEdge Home, Enphase IQ, Sonnen, and GivEnergy when web access is available.
