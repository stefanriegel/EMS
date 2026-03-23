# Domain Pitfalls

**Domain:** Grid export optimization + multi-day weather-aware scheduling for dual-battery EMS
**Researched:** 2026-03-23

## Critical Pitfalls

Mistakes that cause rewrites, safety issues, or significant economic losses.

### Pitfall 1: Export-Then-Buyback Loop

**What goes wrong:** The system exports stored energy at the fixed feed-in rate (e.g. 0.082 EUR/kWh), then hours later buys the same energy back at a much higher import rate (e.g. 0.25+ EUR/kWh) because it underestimated upcoming consumption. The net economic loss is (import_rate - feed_in_rate) * kWh for every kWh that round-trips through the grid.

**Why it happens:** Export decisions are made in the current 5-second control cycle based on instantaneous surplus, without considering the next 4-12 hours of consumption. The existing coordinator has no forward-looking consumption model in its real-time dispatch loop. The scheduler runs nightly and produces a static charge plan, but nothing prevents the real-time loop from exporting energy that the nightly plan assumed would be available for evening discharge.

**Consequences:** With 94 kWh of battery pool and a typical 40 kWh daily consumption, a single bad export decision could waste 5-15 EUR per occurrence. Over a month of daily mistakes, this erases the entire economic benefit of the optimization.

**Prevention:**
- Never export battery-stored energy unless both batteries are above a "reserve threshold" that guarantees coverage of predicted consumption until the next cheap charging window
- Compute a "minimum retained energy" floor based on the consumption forecast for the remaining hours until the next grid charge opportunity
- Export priority: PV surplus (zero opportunity cost) >> battery energy above reserve >> never export below reserve
- The reserve calculation must be per-battery, respecting independent SoC floors already in SystemConfig

**Detection:** Log every export decision with the computed reserve margin. Alert when post-export combined SoC drops below the consumption-coverage threshold. Track actual vs. predicted consumption after export decisions to measure forecast accuracy.

**Which phase should address it:** Must be solved in the grid export optimization phase, not deferred. This is the primary failure mode of export arbitrage.

---

### Pitfall 2: Forecast Coupling Creates Cascading Errors Across Days

**What goes wrong:** The multi-day scheduler chains decisions across 2-3 days: "sunny tomorrow, so charge less tonight; cloudy day-after, so charge more tomorrow night." If the Day 1 solar forecast is wrong (cloud cover 4 hours earlier than predicted), the system enters Day 2 under-charged, and the Day 2 plan was already computed assuming Day 1 went well. The error compounds.

**Why it happens:** The existing scheduler runs once at 23:00 and produces a single static schedule. Extending to multi-day means the Day 2/3 plans are computed with Day 1 still in the future. There is no intra-day re-planning when forecasts deviate from reality.

**Consequences:** Two consecutive wrong forecasts can leave 94 kWh of batteries at 15-20% SoC entering a cloudy day with no cheap tariff window. The system then buys expensive peak electricity to cover basic consumption. Worse, if the system also exported on Day 1 based on the incorrect sunny forecast, the loss doubles.

**Prevention:**
- Re-compute the multi-day schedule at least twice daily (e.g., 06:00 and 23:00) to incorporate updated weather forecasts
- Each day's plan must be independently valid: even if Day 2 is wrong, Day 1 decisions must not leave the system in a dangerous state
- Apply a "forecast confidence discount" that increases with horizon: Day 1 solar at 80% of forecast, Day 2 at 60%, Day 3 at 40% (these are initial values; tune from real data)
- Keep the existing single-day fallback as the safety net: if multi-day planning is uncertain, fall back to conservative single-day behavior

**Detection:** Compare planned vs. actual SoC at each schedule boundary (midnight, 06:00). Log the forecast error for each day. Alert when combined SoC deviates more than 15% from the plan.

**Which phase should address it:** Multi-day scheduling phase. The re-planning mechanism and confidence discounts must be designed upfront, not bolted on.

---

### Pitfall 3: Dual-Battery Export Coordination Breaks Independence

**What goes wrong:** The grid export optimization adds a new coordination concern between the two battery systems: "which battery should export?" If the coordinator starts routing export decisions through a centralized export planner, it violates the fundamental architecture principle of independent controllers. Worse, if one battery is exporting while the other is charging from PV, the system can create a local energy loop (battery A discharges to grid, battery B charges from grid) that wastes energy through round-trip losses.

**Why it happens:** The existing coordinator assigns roles (PRIMARY_DISCHARGE, CHARGING, GRID_CHARGE) independently per battery. Adding an EXPORTING role creates a new interaction: export from battery A affects the grid meter reading that battery B uses for its P_target calculation. The P_target computation in `_compute_p_target()` reads Victron's grid_power_w, which includes any export currently happening.

**Consequences:** Oscillation between batteries: Huawei exports -> grid goes negative -> Victron sees surplus -> Victron charges -> grid goes positive -> Huawei stops exporting -> Victron stops charging -> cycle repeats every 5 seconds. This is exactly the oscillation pattern the v1.0 rewrite was designed to eliminate.

**Prevention:**
- Export must be a coordinator-level decision, not a per-controller decision. The coordinator already owns role assignment; export is just another role (or a modifier on HOLDING/CHARGING)
- Only one battery system should export at a time, and only when the other is either HOLDING or CHARGING from PV (not from grid)
- Use the existing hysteresis and debounce machinery: apply the same dead-band (300W/150W) and 2-cycle debounce to export transitions
- The P_target calculation must account for intentional export: if the coordinator commanded export of X watts, subtract X from the grid reading before computing P_target for the next cycle

**Detection:** Monitor for rapid role transitions involving export (more than 2 in 30 seconds). Log when both batteries have non-zero grid-facing power in opposite directions simultaneously.

**Which phase should address it:** Grid export optimization phase. Must be solved before any export logic touches the coordinator.

---

### Pitfall 4: Schedule Model Incompatibility with Multi-Day Horizon

**What goes wrong:** The existing `ChargeSchedule` and `ChargeSlot` models are designed for single-night operation: two slots (huawei + victron) with one shared tariff window. Extending to multi-day creates a model explosion: 2 batteries x 3 days x multiple slot types (charge, hold, export) = potentially 18+ slots with complex temporal relationships. Bolting multi-day onto the existing flat list of `ChargeSlot` objects produces an unmaintainable mess.

**Why it happens:** The `ChargeSlot` dataclass has `battery`, `target_soc_pct`, `start_utc`, `end_utc`, `grid_charge_power_w`. It has no concept of "which day" or "which planning horizon" a slot belongs to. The `OptimizationReasoning` has a single `charge_energy_kwh` and `cost_estimate_eur` with no per-day breakdown. The coordinator's `_check_grid_charge()` method iterates `scheduler.active_schedule.slots` and checks if `now` falls within any slot's `[start_utc, end_utc)`. This works for one night but becomes ambiguous with overlapping days.

**Consequences:** Without a clean model evolution, the scheduler produces schedules that the coordinator misinterprets. For example, a Day 2 charge slot at 02:00 looks identical to a Day 1 charge slot at 02:00 if the coordinator only checks time-of-day. Or the API returns a flat list of 18 slots that the dashboard cannot meaningfully display.

**Prevention:**
- Introduce a `DayPlan` container that groups slots by calendar date, with per-day reasoning and cost estimates
- The `ChargeSchedule` evolves to contain `List[DayPlan]` instead of `List[ChargeSlot]`
- Add a `day_index: int` (0=tonight, 1=tomorrow night, 2=day-after-tomorrow night) to each slot for unambiguous identification
- Keep backward compatibility: the coordinator's `_check_grid_charge()` should still work with a flat view of all slots, filtered to `day_index == 0`
- The API endpoint `/api/optimization/schedule` should return the multi-day structure, but the existing frontend can initially render only `day_index == 0`

**Detection:** Unit test that creates a 3-day schedule and verifies `_check_grid_charge()` only activates Day 0 slots at the correct times. Integration test that verifies Day 1 slots do not accidentally trigger on Day 0.

**Which phase should address it:** Must be addressed at the start of multi-day scheduling work. The model change is foundational.

---

### Pitfall 5: Feed-In Tariff Comparison Using Wrong Import Rate

**What goes wrong:** The export arbitrage decision compares `feed_in_rate` vs. `current_import_rate` to decide whether to export or store. But the relevant comparison is not the *current* import rate — it is the import rate at the time the stored energy would actually be consumed. If the system exports at 14:00 (feed-in = 0.082, current import = 0.08 off-peak), but the stored energy would have been consumed at 18:00 (import = 0.28 peak), the export decision lost 0.198 EUR/kWh.

**Why it happens:** The tariff engine provides `get_effective_price(dt)` for any instant, but the export decision logic naively compares `feed_in_rate` vs. `get_effective_price(now)` instead of looking ahead to when the energy would actually be needed.

**Consequences:** Systematically wrong export decisions during off-peak hours. The system exports cheap-seeming energy that would have been worth peak-rate later. With 94 kWh pool capacity, this can mean 10-20 EUR/day of value destruction.

**Prevention:**
- The export decision must compare feed-in rate against the *weighted average import rate over the consumption forecast horizon*
- Specifically: `value_of_stored_energy = sum(forecasted_consumption_per_slot * import_rate_per_slot) / total_forecasted_consumption`
- Only export when `feed_in_rate >= value_of_stored_energy * safety_margin` (safety_margin ~ 0.9 to account for forecast uncertainty)
- For a fixed feed-in tariff (the case here), this simplifies to: "never export if there is ANY upcoming import slot with rate > feed_in_rate AND predicted consumption exceeds current battery reserves minus planned solar input"

**Detection:** Log the computed `value_of_stored_energy` alongside every export decision. Post-hoc analysis: compare export revenue against the actual import cost of replacement energy.

**Which phase should address it:** Grid export optimization phase. This is the core economic logic of the feature.

## Moderate Pitfalls

### Pitfall 6: Solar Forecast API Rate Limits and Staleness

**What goes wrong:** The existing system gets solar forecasts from EVCC's `/api/state` endpoint, which includes `SolarForecast.tomorrow_energy_wh` and `day_after_energy_wh`. For multi-day scheduling, this needs 3-day granular forecasts (hourly or 15-min resolution). The EVCC forecast is updated periodically by its own forecast provider (forecast.solar, Solcast, etc.). Hitting the underlying API too frequently triggers rate limits; not hitting it often enough means stale data.

**Why it happens:** The EMS does not control the solar forecast refresh cycle — EVCC does. The `EvccState.solar` data has `timeseries_w` and `slot_timestamps_utc` but the existing scheduler only uses the scalar `tomorrow_energy_wh`. The multi-day scheduler needs the timeseries at hourly resolution for 72 hours, which may exceed what EVCC provides.

**Prevention:**
- Verify what EVCC actually provides in its solar forecast timeseries (how many hours ahead, what resolution). The `SolarForecast` model already has `timeseries_w` and `slot_timestamps_utc` — check the actual horizon length at runtime
- If EVCC provides < 48 hours, the Day 3 forecast must use a degraded estimate (e.g., historical average for that month, or repeat Day 2 with a confidence discount)
- Cache the last-received forecast and track its age. If the forecast is > 6 hours old, increase the confidence discount
- Never fail the entire schedule computation because the solar forecast is stale — fall back to the existing single-day conservative approach

**Detection:** Log forecast age on every scheduler run. Warn when forecast is > 6 hours old. Track forecast accuracy (predicted vs. actual daily solar yield) to calibrate confidence discounts.

**Which phase should address it:** Multi-day scheduling phase.

---

### Pitfall 7: Grid Charge Target Overshoot with Two Independent Chargers

**What goes wrong:** When the multi-day scheduler computes "charge 40 kWh tonight split as 13 kWh Huawei + 27 kWh Victron," the two controllers charge independently. Neither knows the other's progress. If Huawei finishes early (30 kWh pack charges faster at 5 kW), it idles while Victron is still charging at 3 kW. Fine so far. But if the tariff window ends before Victron finishes, the system has under-charged. Conversely, if the scheduler over-estimates how much energy is needed, both batteries reach target early, and the remaining cheap-rate window is wasted.

**Why it happens:** The existing scheduler sets `target_soc_pct` per battery, not `target_energy_kwh`. SoC is measured by the BMS, which has its own calibration drift. A Victron BMS reporting 60% might actually be at 55% or 65%. Over a 64 kWh battery, a 5% SoC error is 3.2 kWh — significant when the total charge target is 27 kWh.

**Prevention:**
- Use energy-based targets (`charge_energy_kwh`) alongside SoC targets, and stop when either is reached
- Monitor actual energy delivered (integrate power over time) during the charge window, not just BMS-reported SoC
- Add a "charge progress" check at the midpoint of the tariff window: if the current charging rate will not reach the target by window end, increase the charge power (if hardware allows) or extend to the next cheapest slot
- Accept that BMS SoC accuracy is +/- 5% and build that into the safety margin

**Detection:** Log energy delivered vs. energy planned for each charge session. Alert when the delta exceeds 10%.

**Which phase should address it:** Multi-day scheduling phase, but only after the model changes from Pitfall 4.

---

### Pitfall 8: Consumption Forecaster Not Adapted for Multi-Day

**What goes wrong:** The existing `ConsumptionForecaster` predicts next-24h consumption as a single scalar (`today_expected_kwh`). The multi-day scheduler needs per-hour consumption profiles for 72 hours. Using the scalar 3 times (one per day) ignores weekday variation (e.g., Monday vs. Sunday), weather-dependent heating loads, and time-of-use patterns within each day.

**Why it happens:** The forecaster trains GBR models on `[outdoor_temp, ewm_temp, day_of_week, hour_of_day, month]` features and predicts hourly loads — but `query_consumption_history()` sums these into a single scalar. The hourly resolution is already in the model; it is just not exposed.

**Prevention:**
- Add a `predict_hourly(horizon_hours: int = 72) -> list[tuple[datetime, float]]` method that returns per-hour consumption predictions for the requested horizon
- The multi-day scheduler uses this hourly profile, not the scalar
- The scalar `query_consumption_history()` interface remains for backward compatibility with the existing single-day scheduler
- Use the outdoor temperature forecast (if available from HA or weather integration) instead of the placeholder 10 C. This becomes critical for multi-day: a 15 C day vs. a 5 C day can differ by 10+ kWh in heat pump load

**Detection:** Compare hourly predictions against actual consumption (from InfluxDB or HA statistics) to validate per-hour accuracy, not just daily total.

**Which phase should address it:** Must be extended before or during multi-day scheduling implementation.

---

### Pitfall 9: DST Transitions Break Multi-Day Slot Boundaries

**What goes wrong:** The existing tariff engine carefully handles DST transitions for single-day schedules (documented in `tariff.py`). Multi-day schedules that span a DST transition (e.g., schedule computed Saturday for Saturday-Monday, with DST change on Sunday) can produce slots with incorrect UTC boundaries. A charge window "02:00-05:00 Berlin time" on the DST spring-forward night only lasts 2 hours instead of 3, under-delivering charge energy.

**Why it happens:** The `ChargeSlot` stores `start_utc` and `end_utc`. If the scheduler computes "3 hours of charging" by naively adding `timedelta(hours=3)` to a wall-clock time that crosses DST, the UTC slot is 2 or 4 hours instead of 3.

**Prevention:**
- Always compute slot boundaries in wall-clock time first, then convert to UTC. Never add `timedelta` to UTC and convert back
- The existing `get_price_schedule` already handles this correctly for one day. The multi-day extension must use the same pattern (convert per-day, concatenate)
- Add explicit DST-transition-spanning test cases: schedules that cross the last Sunday of March and October

**Detection:** Unit test: create a 3-day schedule spanning the March DST transition, verify all slot durations in UTC match expected wall-clock durations.

**Which phase should address it:** Multi-day scheduling phase.

---

### Pitfall 10: Export Power Allocation Ignores Inverter Limitations

**What goes wrong:** The Huawei SUN2000 inverter has specific feed-in power limits set in its configuration (often 0 W for zero-export installations). The Victron MultiPlus-II has its own AC output limits per phase. The export optimization logic computes "export 3 kW" without checking whether the hardware is actually configured to allow grid export.

**Why it happens:** The existing `SystemConfig` has `huawei_feed_in_allowed: bool` and `victron_feed_in_allowed: bool`, both defaulting to `False`. The coordinator respects these flags for PV surplus handling but the new export optimization might bypass these checks if it operates at a different layer.

**Prevention:**
- The export optimization must check `feed_in_allowed` before commanding any export
- For Huawei: the inverter itself may have a hardware feed-in limit register that overrides software commands. The driver should read and expose this limit
- For Victron: ESS mode settings on Venus OS control whether grid feed-in is permitted. The driver's `ess_mode` register value must be checked
- If neither system allows feed-in, the export optimization feature should log "export optimization disabled: no systems allow feed-in" and skip entirely

**Detection:** Startup check: if export optimization is enabled but both `feed_in_allowed` flags are False, log a clear warning. Never silently skip.

**Which phase should address it:** Grid export optimization phase, as a prerequisite check before any export logic.

## Minor Pitfalls

### Pitfall 11: InfluxDB Write Volume Explosion

**What goes wrong:** The existing system writes one `ems_decision` point per role change and one `ems_huawei` + `ems_victron` point per control cycle (every 5 seconds). Adding export decisions, multi-day schedule metrics, and forecast accuracy tracking can easily triple the write volume. On a Raspberry Pi running HA, this can exhaust the SD card's write endurance or fill the InfluxDB bucket.

**Prevention:**
- Aggregate export metrics per 15-minute window, not per control cycle
- Write multi-day schedule data once per computation (twice daily), not per cycle
- Respect the existing InfluxDB-optional pattern: all new metrics must gracefully degrade when InfluxDB is unavailable

**Which phase should address it:** Both phases, as each adds new metrics.

---

### Pitfall 12: Dashboard Overload with Multi-Day Data

**What goes wrong:** The existing dashboard shows a tariff timeline for 24 hours and per-battery charge slots for one night. Extending to 72 hours makes the timeline unreadable on mobile. Adding export indicators and forecast confidence bands further clutters the UI.

**Prevention:**
- Default view: today + tonight (same as current). Expandable to 3-day view on tap
- Use a summary card for Day 2/3: "Tomorrow: mostly sunny, light grid charge planned" rather than showing full timelines
- Export activity: show as a simple indicator on the energy flow diagram, not a separate timeline

**Which phase should address it:** After core logic works. UI changes should follow the backend implementation.

---

### Pitfall 13: EVCC Interaction with Export Decisions

**What goes wrong:** EVCC manages EV charging and can send `batteryMode=hold` to prevent battery discharge during EV charging. If the EMS is exporting from battery while EVCC sends a hold signal, the export must stop immediately. But if the export is already committed (e.g., the grid meter is reading negative because of ongoing export), the transition to hold can cause a brief grid import spike.

**Prevention:**
- The existing EVCC hold signal already takes priority over all other decisions in the coordinator (lines 330-363 of coordinator.py). Export must follow the same pattern: EVCC hold = immediate stop of export
- Export decisions should check EVCC state before committing, not just react to hold signals

**Which phase should address it:** Grid export optimization phase.

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Grid export: core arbitrage logic | Export-then-buyback (Pitfall 1) | Forward-looking consumption reserve before every export decision |
| Grid export: coordinator integration | Oscillation between export and charge (Pitfall 3) | Export as coordinator role, one battery at a time, P_target offset |
| Grid export: tariff comparison | Wrong import rate comparison (Pitfall 5) | Compare against weighted future import rate, not current rate |
| Grid export: hardware limits | Inverter feed-in limits (Pitfall 10) | Check `feed_in_allowed` + hardware register limits before any export |
| Multi-day: schedule model | Flat slot list incompatibility (Pitfall 4) | Introduce `DayPlan` container before implementing multi-day logic |
| Multi-day: forecast chaining | Cascading forecast errors (Pitfall 2) | Independent daily plans, confidence discounts, intra-day replanning |
| Multi-day: consumption model | Scalar forecast insufficient (Pitfall 8) | Expose hourly predictions from existing GBR models |
| Multi-day: time handling | DST slot boundary errors (Pitfall 9) | Wall-clock-first computation, DST-spanning test cases |
| Multi-day: solar forecast | Limited horizon from EVCC (Pitfall 6) | Verify EVCC timeseries length, degrade gracefully for Day 3 |
| Both phases: metrics | InfluxDB write volume (Pitfall 11) | Aggregate per-window, not per-cycle; respect optional pattern |

## Sources

- Direct codebase analysis: `backend/scheduler.py`, `backend/coordinator.py`, `backend/tariff.py`, `backend/live_tariff.py`, `backend/consumption_forecaster.py`, `backend/schedule_models.py`, `backend/controller_model.py`, `backend/config.py`
- Architecture context: `.planning/PROJECT.md` (v1.0 design decisions, dual-battery independence principle)
- Domain knowledge: HIGH confidence -- battery energy management and tariff arbitrage are well-understood engineering domains with documented failure modes
