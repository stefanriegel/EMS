# Phase 3: PV & Tariff Optimization - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

The system makes intelligent charge/discharge decisions based on PV surplus, tariff windows, solar forecasts, and time-of-day profiles. Each battery charges/discharges independently at its own rate, weighted by SoC headroom and hardware limits.

Requirements: OPT-01, OPT-02, OPT-03, OPT-04, OPT-05

</domain>

<decisions>
## Implementation Decisions

### PV surplus distribution (OPT-01)
- **D-01:** PV surplus is detected from negative P_target (grid export) — when the grid meter reads negative, surplus exists. No separate "PV surplus detector" class needed; the coordinator already computes P_target from grid flow.
- **D-02:** Surplus allocation uses SoC headroom weighting: `headroom = max_soc - current_soc`. Battery with more headroom gets a proportionally larger share. This replaces the current capacity-proportional split for charge scenarios.
- **D-03:** Charge rate limits are respected per system: Huawei max from `battery.max_charge_power_w` (hardware-reported), Victron max from `victron_max_charge_w` config. If one battery hits its rate limit, overflow routes to the other.
- **D-04:** When one battery reaches max_soc (95%), it enters HOLDING and all surplus routes to the other battery — preserving existing overflow routing behavior from the orchestrator.

### Tariff-aware grid charging (OPT-02, OPT-03)
- **D-05:** Each battery gets its own `ChargeSlot` with independent target SoC and charge rate — this already exists in the scheduler. Phase 3 enhances the slot computation, not the slot structure.
- **D-06:** In short tariff windows, the faster charger (Huawei at 5 kW) starts first, Victron (3 kW) starts simultaneously. Both charge in parallel but Huawei reaches target first, then full cheap-window time goes to Victron. No sequential staggering — parallel is better because cheap windows are time-limited.
- **D-07:** Grid charge power budgets remain configurable constants (not dynamic). Huawei: 5000W, Victron: 3000W — matching existing scheduler defaults. Users can override via config.
- **D-08:** Coordinator detects active charge slots (existing `_active_charge_slot()` logic) and sets each controller to GRID_CHARGE with the slot's power budget. Controller exits GRID_CHARGE when its target SoC is reached — existing behavior preserved.

### Predictive pre-charging (OPT-04)
- **D-09:** Solar forecast comes from EVCC (`SolarForecast.tomorrow_energy_wh`) — already fetched by the scheduler. No new data source needed.
- **D-10:** Skip grid charge when `solar_forecast_kwh >= expected_consumption_kwh * 1.2` (20% margin). The 1.2 multiplier accounts for forecast uncertainty and cloudy-period gaps.
- **D-11:** When solar forecast partially covers demand, reduce grid charge target proportionally: `target_kwh = max(0, consumption - solar * 0.8)`. The 0.8 discount on solar reflects real-world yield losses (clouds, shading, inverter limits).
- **D-12:** If no solar forecast is available (EVCC offline), fall back to full grid charge — never skip charging on missing data. Safety over optimization.

### Time-of-day min-SoC profiles (OPT-05)
- **D-13:** Min-SoC profiles are a list of `(start_hour, end_hour, min_soc_pct)` tuples per battery system. Evaluated in order; first matching window wins. If no window matches, fall back to the static `SystemConfig` min_soc value.
- **D-14:** Default profiles (sensible starting point, configurable):
  - Huawei: `[(6, 16, 30), (16, 22, 20), (22, 6, 10)]` — hold 30% during daytime peak, 20% evening, 10% overnight
  - Victron: `[(6, 16, 25), (16, 22, 15), (22, 6, 10)]` — slightly lower thresholds (larger battery, more headroom)
- **D-15:** Profiles are stored in `SystemConfig` as optional lists. If empty/None, the existing static min_soc values apply — backward compatible.
- **D-16:** The coordinator evaluates the active min-SoC profile on each cycle (using local time) and passes the effective floor to each controller. Controllers don't know about profiles — they just see a min_soc value.

### Integration with coordinator (from Phase 2)
- **D-17:** All optimization logic lives in the coordinator, not in controllers. Controllers receive setpoints and execute them. The coordinator owns: PV surplus allocation, tariff-aware scheduling, min-SoC profile evaluation.
- **D-18:** The existing scheduler remains the source for charge schedules. Phase 3 enhances `compute_schedule()` with solar-aware target reduction (D-10, D-11) but does not change the scheduler's nightly-run cadence.
- **D-19:** No intra-day schedule recomputation in this phase. The scheduler runs once per night. Real-time adjustments (PV surplus routing, min-SoC profiles) happen in the coordinator's 5s control loop without touching the schedule.

### Claude's Discretion
- Internal method decomposition within coordinator for optimization logic
- Config dataclass structure for min-SoC profiles (list of tuples vs dedicated dataclass)
- Test scenario selection and fixture organization
- Whether to extract PV surplus allocation into a helper or keep it inline in the coordinator
- Exact logging format for optimization decisions

</decisions>

<specifics>
## Specific Ideas

- The coordinator's `_run_cycle()` already has the right shape: read state → compute allocation → dispatch. PV surplus weighting and min-SoC profile evaluation slot into the "compute allocation" step naturally.
- The scheduler's `compute_schedule()` already fetches solar forecast from EVCC and consumption from the forecaster — the predictive pre-charging logic (D-10, D-11) is a few lines in the existing flow.
- Keep the 5s control loop interval unchanged — PV surplus changes slowly (cloud shadows are ~30s minimum), so 5s is more than fast enough.
- The existing `_CHEAP_THRESHOLD_EUR_KWH = 0.15` constant in the scheduler is a good default for cheap window detection. No need to make it configurable yet.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Coordinator (Phase 2 output — target for modification)
- `backend/coordinator.py` — Coordinator class with role assignment, allocation, hysteresis. The `_run_cycle()` method is where PV surplus weighting and min-SoC profiles will be added.

### Scheduler & forecasting
- `backend/scheduler.py` — Nightly charge schedule computation. Lines 179–237: solar forecast usage and SoC target derivation — enhance with predictive pre-charging (D-10, D-11).
- `backend/consumption_forecaster.py` — ML-based consumption prediction. Provides `today_expected_kwh` for solar vs consumption comparison.
- `backend/schedule_models.py` — `ChargeSlot`, `ChargeSchedule`, `SolarForecast`, `OptimizationReasoning` dataclasses.

### Tariff engine
- `backend/tariff.py` — `CompositeTariffEngine` with `get_effective_price()` and `get_price_schedule()`. Already production-ready — no changes needed for Phase 3.
- `backend/live_tariff.py` — `LiveOctopusTariff` live-rate override. Same API surface.

### Configuration
- `backend/config.py` — `SystemConfig` (min/max SoC per system), `OrchestratorConfig` (loop timing, capacity limits), `SchedulerConfig` (run hour, charge window). Add min-SoC profiles to `SystemConfig`.

### Driver models (read-only context)
- `backend/drivers/huawei_models.py` — `HuaweiBatteryData.max_charge_power_w` for charge rate limits
- `backend/drivers/victron_models.py` — `VictronSystemData.battery_soc_pct`, `pv_on_grid_w`, `grid_power_w`

### Existing tests
- `tests/test_coordinator.py` — Coordinator tests from Phase 2 (if present)
- `tests/test_scheduler.py` — Schedule computation tests to extend
- `tests/test_orchestrator.py` — Legacy orchestrator tests (reference patterns)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **Coordinator P_target computation** — Already computes net grid flow; negative P_target = surplus. PV surplus routing is an extension of existing allocation logic.
- **Scheduler solar forecast fetch** — `evcc_state.solar.tomorrow_energy_wh` already available. Predictive pre-charging adds a comparison against consumption forecast.
- **Overflow routing** — Existing logic routes surplus to the other battery when one is full. Extends naturally to SoC-headroom weighting.
- **`CompositeTariffEngine.get_price_schedule()`** — Returns all tariff slots for a day. Already used by scheduler for cheap window detection.
- **`ConsumptionForecaster.query_consumption_history()`** — Returns `ConsumptionForecast` with `today_expected_kwh`. Direct input for solar vs consumption comparison.

### Established Patterns
- **Coordinator owns all optimization** — Controllers are dumb executors (Phase 2 decision D-05, D-06). All new logic goes in coordinator.
- **Dataclass configs with `from_env()`** — New config fields follow the same pattern. Optional fields default to None for backward compat.
- **Sentinel/fallback values** — When data is missing, use safe defaults (full charge, static min-SoC). Never optimize on missing data.
- **5s control loop** — All real-time decisions must be O(1) — no blocking calls, no DB reads in the hot path.

### Integration Points
- **Coordinator → Controllers** — Coordinator sets controller targets via method calls each cycle. Add effective min_soc to the per-cycle dispatch.
- **Scheduler → Coordinator** — Coordinator reads `scheduler.active_schedule` for charge slots. Enhanced schedule (with solar-reduced targets) flows through the same path.
- **Config → Coordinator** — `SystemConfig` changes propagate via `update_config()`. New min-SoC profiles flow through the same mechanism.
- **API** — `/api/optimization/schedule` already exposes the charge schedule. Enhanced reasoning (solar skip, reduced targets) appears automatically.

</code_context>

<deferred>
## Deferred Ideas

- **Intra-day schedule recomputation** — Re-run scheduler at noon when actual solar differs from forecast. Adds complexity; defer to a future enhancement once base optimization proves itself.
- **Tariff-aware discharge timing** — Prefer discharging during expensive tariff windows rather than cheap ones. Requires real-time tariff engine integration in coordinator. Useful but not in OPT-01–05 scope.
- **Grid export optimization** — Feed surplus to grid when feed-in tariff exceeds storage round-trip losses. Requires feed-in tariff tracking — Phase 4 or v2 (ADV-01).
- **Weather-enhanced consumption forecast** — Add outdoor temperature forecast (from HA weather entity) to improve ML model accuracy. Enhancement to forecaster, not core optimization.
- **Multi-day scheduling** — Plan charge/discharge across 2–3 days for weather front optimization. Deferred to v2 (ADV-02).

</deferred>

---

*Phase: 03-pv-tariff-optimization*
*Context gathered: 2026-03-22*
