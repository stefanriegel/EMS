# Feature Landscape: Grid Export Optimization & Multi-Day Scheduling

**Domain:** Battery energy management -- export arbitrage with fixed feed-in tariff and multi-day weather-aware charge scheduling
**Researched:** 2026-03-23
**Overall confidence:** MEDIUM-HIGH (algorithm patterns well-established in domain; specific integration points verified against existing codebase)

## Table Stakes

Features users expect from an export-aware, weather-planning battery system. Missing = system leaves money on the table or makes obviously bad decisions.

| Feature | Why Expected | Complexity | Dependencies on Existing | Notes |
|---------|--------------|------------|--------------------------|-------|
| Export-vs-store decision logic | Core value proposition -- with a fixed feed-in rate, the system must decide whether PV surplus goes to battery or grid based on whether stored energy will displace a more expensive future import | Medium | Tariff engine (`get_effective_price`), feed-in config (NEW), coordinator dispatch | The fundamental decision: if `feed_in_rate >= upcoming_import_rate * round_trip_efficiency`, export now rather than store. With a FIXED feed-in rate this simplifies to a threshold comparison against time-varying import prices. |
| Feed-in rate configuration | Users must be able to set their fixed feed-in tariff (EUR/kWh). Germany: typically 0.08-0.12 EUR/kWh for PV < 10 kWp | Low | Config system (`SystemConfig`), setup wizard, API | Single float value. Already have `feed_in_allowed` booleans per system -- this adds the economic rate. |
| Avoid export-then-buyback | System must not export energy it will need to buy back at a higher rate within the planning horizon. If evening consumption is predictable, don't export afternoon PV surplus that will be needed at 18:00-22:00 | High | Consumption forecaster (ML), solar forecast (EVCC), tariff engine | This is the hardest table-stakes feature. Requires forward-looking simulation: "If I export this kWh now at 0.08 EUR, will I need to import it tonight at 0.25 EUR?" The answer depends on consumption forecast + remaining solar forecast for the day. |
| Multi-day solar awareness in charge scheduler | Scheduler must consider 2-3 day solar forecast horizon, not just tomorrow. Charge more before cloudy stretches, defer grid charge when sunny days ahead | Medium | EVCC solar data (`tomorrow_energy_wh`, `day_after_energy_wh` -- already parsed), scheduler | EVCC already provides `day_after_energy_wh`. The scheduler currently only uses `tomorrow_energy_wh`. Extending to a 2-3 day horizon is a natural evolution of the existing `net_charge_kwh` formula. |
| PV-full forced export | When both batteries are full (SoC >= max) and PV is still producing, the system MUST allow grid export rather than curtailing PV. This is the simplest export case -- no decision needed, it is pure waste otherwise | Low | Coordinator (`_compute_setpoints`), existing `feed_in_allowed` flags | Partially exists: coordinator already checks `victron_feed_in_allowed` when both systems are charging at capacity. Needs to transition from a binary flag to an always-on behavior when feed-in rate is configured. |
| Export decision transparency | Every export/store decision must be logged with reasoning (feed-in rate vs upcoming import rate, consumption forecast, battery headroom) so users can verify the system is making good choices | Medium | Decision ring buffer (`/api/decisions`), existing logging infrastructure | Users of battery systems are deeply analytical. Opaque export decisions erode trust fast. The existing decision transparency infrastructure (ring buffer + API) is a strong foundation. |

## Differentiators

Features that set this system apart. Not expected by every user, but valuable for advanced optimization.

| Feature | Value Proposition | Complexity | Dependencies on Existing | Notes |
|---------|-------------------|------------|--------------------------|-------|
| Dual-battery export coordination | Decide WHICH battery to discharge for export based on SoC, capacity, and inverter efficiency. Huawei (30 kWh) exports faster to free headroom for afternoon PV; Victron (64 kWh) has more energy but different max discharge rates | Medium | Coordinator role assignment, per-system SoC tracking | Unique to this system -- most home batteries are single-system. The existing SoC-headroom-weighted dispatch can be extended with an export allocation strategy. |
| Battery cycle cost accounting | Factor in battery degradation cost per kWh cycle when deciding export. If `feed_in_rate - degradation_cost <= 0`, don't force-discharge from battery to grid -- only export direct PV surplus | Low | Config (NEW: cycle cost parameter), export decision logic | Predbat calls this `metric_battery_cycle`. Typical value: 0.01-0.04 EUR/kWh depending on battery chemistry and warranty. Without this, the system might force-discharge batteries for marginal 1-2 cent gains that cost more in degradation. |
| Cloudy-stretch pre-charging | When day+2 and day+3 forecasts show low solar (< 30% of average), proactively charge batteries during cheap off-peak hours the night before, even if tomorrow's forecast alone wouldn't warrant it | Medium | Multi-day solar data from EVCC, consumption forecaster, scheduler | This is the key multi-day value add. Single-day optimization misses the "sunny today, cloudy next 3 days" pattern where you should store today's PV rather than export it. |
| Rolling forecast confidence weighting | Weight day+1 forecast more heavily than day+2, day+2 more than day+3. Weather forecasts degrade with horizon -- solar forecast for day+3 has 2-3x the error of day+1 | Low | Scheduler logic only | Simple multiplicative discount factors (e.g., day+1: 1.0, day+2: 0.85, day+3: 0.70). Prevents over-reliance on unreliable distant forecasts. |
| Export scheduling for high-rate windows | With time-varying import tariffs (Octopus Go), schedule battery-to-grid export during peak import windows when it is most valuable to have displaced grid power, even if the feed-in rate is fixed | Medium | Tariff engine (`get_price_schedule`), coordinator, scheduler | The feed-in rate is fixed, but the VALUE of stored energy is time-varying. 1 kWh stored is worth more if it displaces a 0.30 EUR/kWh peak import than a 0.15 EUR/kWh off-peak import. This means: prefer to use battery during peak hours, export PV directly during off-peak hours if battery headroom is limited. |

## Anti-Features

Features to explicitly NOT build. These seem appealing but create more problems than they solve.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Dynamic feed-in rate tracking | The feed-in rate is FIXED by contract (German EEG Verguetung or similar). Building infrastructure for time-varying export rates adds complexity for a use case that doesn't exist here. EVCC already provides `export_eur_kwh` timeseries but it will be a flat line for this installation. | Store the fixed feed-in rate as a single config value. If dynamic export tariffs are ever needed, the tariff engine can be extended then. |
| Grid export as revenue maximization | Forcing battery discharge to grid purely for revenue (grid arbitrage -- buy low, sell high using the battery as a trading instrument) destroys battery life for marginal gains when the feed-in rate is low (0.08-0.12 EUR/kWh). Round-trip efficiency losses (8-15%) eat most of the spread. | Only export DIRECT PV surplus when batteries are full or when storing would cause export-then-buyback. Never force-discharge batteries to grid unless the spread clearly exceeds round-trip losses + degradation cost. |
| Weather API integration (Forecast.Solar, Solcast, Open-Meteo) | EVCC already provides solar forecasts sourced from these services. Adding a second forecast source creates conflict (which to trust?), adds API key management, and duplicates existing functionality. | Use EVCC's solar forecast exclusively. EVCC already aggregates and normalizes forecast data. If EVCC is unavailable, fall back to seasonal averages (existing behavior). |
| Real-time electricity market integration | Spot market prices, balancing market signals, etc. are irrelevant for a residential system with a fixed feed-in tariff and Octopus Go import tariff. The complexity of real-time market integration is enormous for zero benefit. | Keep using the existing Octopus Go + Modul3 composite tariff engine. |
| Automated export limit compliance | Some grid operators impose export limits (e.g., 70% rule in Germany, now abolished for new installations). Building automated compliance for various regulatory regimes is a rabbit hole. | Document that the user is responsible for configuring hardware-level export limits on their inverter. The EMS controls battery dispatch, not inverter export limits. |

## Feature Dependencies

```
Feed-in rate config ──> Export-vs-store decision logic
                    ──> PV-full forced export (enhanced)
                    ──> Battery cycle cost accounting
                    ──> Export decision transparency

Consumption forecaster (existing) ──> Avoid export-then-buyback
Solar forecast multi-day (existing data, new logic) ──> Multi-day charge scheduling
                                                    ──> Cloudy-stretch pre-charging

Multi-day charge scheduling ──> Cloudy-stretch pre-charging
Export-vs-store decision ──> Dual-battery export coordination
                         ──> Export scheduling for high-rate windows

Tariff engine (existing) ──> Export-vs-store decision logic
                         ──> Export scheduling for high-rate windows
```

## MVP Recommendation

Prioritize in this order:

1. **Feed-in rate configuration** -- prerequisite for everything else. Single float in `SystemConfig`, exposed via setup wizard and API. LOW effort, HIGH dependency.

2. **PV-full forced export** -- simplest export case, immediate value. When both batteries hit max SoC and PV is producing, allow grid export. Extends existing `feed_in_allowed` logic. LOW effort.

3. **Export-vs-store decision logic** -- the core economic brain. Compare `feed_in_rate` against `upcoming_import_rate * round_trip_efficiency` to decide whether PV surplus goes to battery or grid. MEDIUM effort.

4. **Multi-day solar awareness in scheduler** -- extend existing scheduler to use `day_after_energy_wh` and adjust `net_charge_kwh` based on 2-3 day horizon. MEDIUM effort, builds on existing formula.

5. **Avoid export-then-buyback** -- the hardest table-stakes feature. Requires forward-looking simulation combining consumption forecast + solar forecast + tariff schedule. HIGH effort but critical for economic correctness.

6. **Export decision transparency** -- extend decision ring buffer with export reasoning. MEDIUM effort, builds on existing infrastructure.

**Defer to later milestone:**
- **Battery cycle cost accounting**: Nice-to-have optimization parameter. Can default to 0 (ignore degradation cost) initially.
- **Cloudy-stretch pre-charging**: Requires multi-day scheduling to be solid first. Can be a follow-on enhancement.
- **Dual-battery export coordination**: The existing SoC-based role assignment will produce reasonable results. Explicit export coordination is an optimization on top.
- **Rolling forecast confidence weighting**: Simple to add but only matters once multi-day scheduling is working correctly.

## Key Algorithmic Insight: The Export Decision

With a FIXED feed-in rate, the export decision simplifies significantly compared to dynamic tariffs:

```
For each kWh of PV surplus:
  current_import_rate = tariff_engine.get_effective_price(now)
  future_import_rate = max(tariff_engine.get_effective_price(t) for t in remaining_peak_hours_today)

  effective_stored_value = future_import_rate * round_trip_efficiency  # ~0.90

  if feed_in_rate >= effective_stored_value:
    # Export: feed-in pays more than the stored energy is worth
    ACTION: allow grid export
  elif battery_soc >= max_soc:
    # Batteries full: export regardless (waste prevention)
    ACTION: allow grid export
  elif remaining_solar_kwh > remaining_consumption_kwh + remaining_battery_headroom_kwh:
    # More solar coming than can be consumed or stored: export surplus
    ACTION: allow grid export
  else:
    # Store: energy is worth more as future import displacement
    ACTION: charge battery
```

The critical nuance: with typical German feed-in rates (0.08-0.12 EUR/kWh) and Octopus Go peak rates (~0.25-0.30 EUR/kWh), the stored value almost always exceeds the feed-in rate. This means the primary export scenario is batteries-full with ongoing PV production, not economic arbitrage. The "avoid export-then-buyback" logic matters most in the transitional hours (14:00-16:00) when batteries are filling and afternoon/evening consumption is uncertain.

## Sources

- [Predbat documentation -- export threshold logic](https://springfall2008.github.io/batpred/what-does-predbat-do/)
- [Predbat customisation -- metric_battery_cycle](https://springfall2008.github.io/batpred/customisation/)
- [Aurora Solar -- storage modeling for energy arbitrage](https://help.aurorasolar.com/hc/en-us/articles/28998198908563-Understanding-Storage-Modeling-for-Energy-Arbitrage)
- [EVCC tariffs and forecasts documentation](https://docs.evcc.io/en/docs/tariffs)
- [Forecast.Solar -- HA integration](https://www.home-assistant.io/integrations/forecast_solar/)
- [gridX -- self-sufficiency optimization](https://www.gridx.ai/knowledge/self-sufficiency-optimization)
- Existing codebase analysis: `backend/scheduler.py`, `backend/tariff.py`, `backend/consumption_forecaster.py`, `backend/coordinator.py`, `backend/config.py`, `backend/schedule_models.py`
