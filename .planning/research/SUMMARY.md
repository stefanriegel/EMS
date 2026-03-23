# Project Research Summary

**Project:** EMS v1.1 — Grid Export Optimization & Multi-Day Weather-Aware Scheduling
**Domain:** Battery energy management — export arbitrage with fixed feed-in tariff and multi-day solar-aware charge scheduling
**Researched:** 2026-03-23
**Confidence:** HIGH

## Executive Summary

The EMS already has nearly everything needed for this feature set: a composite tariff engine, EVCC-sourced solar forecasts for today, tomorrow, and day-after-tomorrow, a GradientBoosting consumption forecaster, and an independent dual-battery coordinator. The v1.1 work is overwhelmingly algorithmic — adding decision logic, extending existing models, and wiring up new components — not adding infrastructure. No new core runtime dependencies are required. The only recommended optional addition is `open-meteo-solar-forecast>=0.1.29` as a fallback when EVCC solar data is unavailable.

The central challenge is economic correctness of the export-vs-store decision. With a fixed German feed-in rate (~0.082 EUR/kWh) far below typical peak import rates (0.25-0.28 EUR/kWh), the primary export scenario is batteries-full with ongoing PV production — not discharge-to-grid arbitrage. The critical failure mode is the export-then-buyback loop: exporting now at 0.082 EUR/kWh, then importing replacement energy at 0.25 EUR/kWh hours later. Every export decision must check a forward-looking consumption reserve floor before acting. The multi-day scheduling extension faces a parallel risk — cascading forecast errors across days — which requires independent daily plans with confidence discounts, not a single chained 3-day binding schedule.

The recommended build order follows strict dependency chains: export foundation (config + advisor class) first, then coordinator integration, weather client in parallel, multi-day scheduling on top of the weather client, and API/frontend last. The advisory pattern keeps new decision logic cleanly separated from the coordinator's control loop, preserving the dual-battery independence guarantee that was the core design goal of the v1.0 rewrite.

## Key Findings

### Recommended Stack

No new dependencies are needed for core functionality. The existing `CompositeTariffEngine`, `EvccClient`, `ConsumptionForecaster`, and `Scheduler` already provide all data sources. Changes are configuration additions and new Python dataclasses, not library changes.

**Core technologies (existing, unchanged):**
- `CompositeTariffEngine`: export vs. import price comparison — already computes per-slot rates; adding `feed_in_rate_eur_kwh` is a single config field
- `EvccClient + SolarForecast`: multi-day solar data — already parses `tomorrow_energy_wh` and `day_after_energy_wh`
- `ConsumptionForecaster`: demand prediction for export-then-buyback avoidance — GBR models already produce hourly forecasts, but the hourly resolution is not yet exposed via a public method
- `Scheduler.compute_schedule()`: extend to accept charge adjustment multiplier from the new `WeatherScheduler` wrapper

**Optional addition:**
- `open-meteo-solar-forecast>=0.1.29`: fallback solar forecast when EVCC is offline — async-native, no API key, Python 3.12 compatible. Install as an optional dependency only.

**Explicitly rejected:** PuLP, scipy.optimize, OR-Tools (the optimization problem is a threshold comparison, not a linear program), Solcast (commercial, cloud dependency), pandas (numpy already available).

### Expected Features

Full tables in `FEATURES.md`. Summary:

**Must have (table stakes):**
- Feed-in rate configuration — single float in `SystemConfig`, prerequisite for everything else
- PV-full forced export — when both batteries are full and PV is producing, export rather than curtail
- Export-vs-store decision logic — compare `feed_in_rate` against weighted future import rate, not current rate
- Multi-day solar awareness in charge scheduler — use `day_after_energy_wh` and adjust nightly charge target
- Avoid export-then-buyback — forward-looking consumption reserve floor before every export decision
- Export decision transparency — extend existing decision ring buffer with export reasoning

**Should have (differentiators):**
- Dual-battery export coordination — decide which battery exports based on SoC and inverter limits
- Battery cycle cost accounting — factor degradation cost (~0.01-0.04 EUR/kWh) into export profitability
- Cloudy-stretch pre-charging — charge more before multi-day cloud stretches identified in Day 2/3 forecast
- Rolling forecast confidence weighting — discount Day 2 forecast at 85%, Day 3 at 70%
- Export scheduling for high-rate windows — prefer battery discharge during peak import windows

**Defer to later milestone:**
- Battery cycle cost accounting (can default to 0 initially)
- Cloudy-stretch pre-charging (depends on solid multi-day scheduling first)
- Dual-battery export coordination (existing SoC-based assignment is adequate initially)
- Rolling forecast confidence weighting (simple add-on once multi-day scheduling is working)

**Anti-features (explicitly out of scope):**
- Dynamic feed-in rate tracking (contract rate is fixed)
- Grid arbitrage via forced battery discharge (margins don't cover round-trip losses at fixed feed-in rates)
- Second weather API source alongside EVCC (creates data conflict, duplicates existing pipeline)
- Automated export limit compliance (hardware-level concern, not EMS concern)

### Architecture Approach

The new components follow two patterns already established in the codebase: the advisory pattern (new decision logic lives in a dedicated class; the coordinator queries it) and the wrapper pattern (new functionality wraps existing components without modifying them). The `ExportAdvisor` inserts a check between the existing grid-charge check and P_target computation in the coordinator's 5-second loop. The `WeatherScheduler` wraps the existing `Scheduler`, adjusts the charge target multiplier based on multi-day outlook, and returns a `MultiDayPlan` where only `tonight_schedule` is binding. All new components are optional injections — the system degrades to v1.0 behavior if they are absent.

**Major components:**
1. `ExportAdvisor` (NEW) — real-time export vs. store arbitrage; queries tariff engine, consumption forecast, and scheduled charge windows; outputs `STORE`/`EXPORT` with power budget and reasoning
2. `WeatherClient` (NEW) — fetches multi-day solar irradiance from Open-Meteo; caches results; provides fallback when EVCC solar horizon is insufficient
3. `WeatherScheduler` (NEW) — wraps existing `Scheduler`; applies charge adjustment multiplier [0.3, 1.5] based on 2-3 day solar vs. consumption balance; produces `MultiDayPlan` with advisory Day 2/3 forecasts
4. `Coordinator` (MODIFIED) — new `EXPORTING` battery role; export check block inserted before P_target; P_target offset to prevent oscillation when export is active
5. `schedule_models.py` (MODIFIED) — new `DayPlan` container with `day_index` field; `ExportDecision`, `DaySolarForecast`, `MultiDaySolarForecast`, `DayAdvisory`, `MultiDayPlan` dataclasses
6. `config.py` (MODIFIED) — `feed_in_rate_eur_kwh`, `export_min_soc_pct` on `SystemConfig`; new `WeatherConfig` dataclass

### Critical Pitfalls

Full list in `PITFALLS.md`. Top five requiring design-time solutions:

1. **Export-then-buyback loop** — compute a minimum retained energy floor (consumption forecast until next cheap charge window) before every export decision. Export only PV surplus above this floor. Never export battery-stored energy below reserve. This is the primary failure mode and must be solved in the first export phase.

2. **Wrong import rate for comparison** — the export decision must compare feed-in rate against the *weighted average future import rate over the consumption forecast horizon*, not the current import rate. Exporting at 14:00 off-peak while evening peak load is predicted loses ~0.20 EUR/kWh. Log `value_of_stored_energy` on every decision.

3. **Dual-battery oscillation during export** — export must be a coordinator-level role (`EXPORTING`), not a per-controller decision. Only one battery exports at a time. P_target calculation must offset by intentional export power to prevent the other battery from reacting to the negative grid reading. Apply existing hysteresis (300W/150W deadband).

4. **Cascading multi-day forecast errors** — Day 2/3 plans must be advisory only (`DayAdvisory`), never binding `ChargeSlot` entries. Re-compute the plan at least twice daily (06:00 and 23:00). Apply confidence discounts: Day 1=1.0, Day 2=0.85, Day 3=0.70. Keep single-day fallback as safety net.

5. **Flat slot model incompatible with multi-day** — introduce `DayPlan` container with `day_index: int` before implementing any multi-day logic. The coordinator's `_check_grid_charge()` filters to `day_index == 0` only. Without this, Day 2 slots at 02:00 are indistinguishable from Day 1 slots at 02:00.

## Implications for Roadmap

Based on research, suggested phase structure:

### Phase 1: Export Foundation

**Rationale:** Feed-in rate config and the `ExportAdvisor` class are prerequisites for everything export-related. They have zero dependencies on weather or multi-day scheduling and can be built and unit-tested in complete isolation. Getting the economic logic right here is the most critical work in the entire feature set.
**Delivers:** `feed_in_rate_eur_kwh` and `export_min_soc_pct` config fields; `ExportAdvisor` class with full decision logic including consumption reserve floor and weighted future import rate comparison; `ExportDecision` dataclass; comprehensive unit tests covering the export-then-buyback prevention logic.
**Addresses:** Feed-in rate configuration (table stakes), export-vs-store decision logic (table stakes), export decision transparency foundation.
**Avoids:** Export-then-buyback loop (Pitfall 1), wrong import rate comparison (Pitfall 5).

### Phase 2: Coordinator Export Integration

**Rationale:** Coordinator changes are the riskiest modification in the entire feature set — touching the 5-second control loop. Running after the `ExportAdvisor` is thoroughly tested means integration risk is isolated to wiring, not logic. This phase also adds the `EXPORTING` battery role and P_target offset.
**Delivers:** `BatteryRole.EXPORTING` enum value; export check block in `_run_cycle()`; `_compute_export_commands()` method; export decisions in the ring buffer; InfluxDB export metrics (aggregated per 15-minute window, not per-cycle).
**Addresses:** PV-full forced export (table stakes), export decision transparency (table stakes), dual-battery export coordination.
**Avoids:** Dual-battery oscillation (Pitfall 3), inverter feed-in limits bypass (Pitfall 10), EVCC hold signal conflict (Pitfall 13).

### Phase 3: Weather Client

**Rationale:** Fully independent of export changes. Can be built in parallel with Phase 2. Establishes the data layer that Phase 4 depends on. Low risk — the integration pattern is identical to the existing EVCC client.
**Delivers:** `WeatherConfig` dataclass; `WeatherClient` with Open-Meteo integration and EVCC fallback; `DaySolarForecast` and `MultiDaySolarForecast` data models; caching (results survive nightly scheduler runs); graceful degradation to EVCC-only or seasonal averages when weather API is unreachable.
**Addresses:** Multi-day solar awareness prerequisite.
**Avoids:** Solar forecast API rate limits and staleness (Pitfall 6).

### Phase 4: Multi-Day Scheduling

**Rationale:** Requires Phase 3 (WeatherClient) and a stable existing Scheduler. The schedule model changes (`DayPlan` container) must be the first task in this phase — all subsequent scheduling logic depends on this foundation.
**Delivers:** `DayPlan` container with `day_index` in `schedule_models.py`; `WeatherScheduler` wrapper with charge adjustment multiplier [0.3, 1.5]; `DayAdvisory` and `MultiDayPlan` dataclasses; twice-daily re-planning trigger (06:00 and 23:00); extended `ConsumptionForecaster.predict_hourly(horizon_hours=72)` method.
**Addresses:** Multi-day solar awareness (table stakes), cloudy-stretch pre-charging (differentiator), rolling forecast confidence weighting (differentiator).
**Avoids:** Cascading forecast errors (Pitfall 2), flat slot model incompatibility (Pitfall 4), consumption forecaster scalar limitation (Pitfall 8), DST slot boundary errors (Pitfall 9), BMS SoC overshoot (Pitfall 7).

### Phase 5: API and Frontend

**Rationale:** Backend must be stable before UI work begins. Frontend changes are lower risk and are additive — the existing dashboard remains fully functional while new data surfaces progressively.
**Delivers:** `GET /api/export/status` endpoint; `GET /api/optimization/multi-day` endpoint; updated `/api/optimization/schedule` with multi-day context and `charge_adjustment`; frontend export indicator on energy flow diagram; expandable 3-day tariff timeline (collapsed by default); Day 2/3 summary cards.
**Addresses:** Export decision transparency (table stakes), multi-day visibility for users.
**Avoids:** Dashboard overload (Pitfall 12).

### Phase Ordering Rationale

- The export advisor must precede coordinator integration so the economic logic is independently testable before the 5-second control loop is touched.
- Weather client is independent of export logic and can proceed in parallel with Phase 2, unblocking Phase 4 sooner.
- Multi-day scheduling requires both the weather client (data) and a clean schedule model (foundation). The `DayPlan` container must be the first task in Phase 4.
- API/frontend is last because the backend state shape (especially `MultiDayPlan`) must be final before the frontend consumes it.
- All phases follow the existing optional-injection pattern: each new component can be absent without breaking the system, preserving the graceful degradation guarantee.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 4 (Multi-Day Scheduling):** The twice-daily re-planning trigger and intra-day forecast update mechanism need careful design. The interaction between `WeatherScheduler`, the nightly async loop in `main.py`, and the coordinator's read of `scheduler.active_schedule` is non-trivial. Review the existing loop scheduling in `main.py` before writing the phase spec.
- **Phase 4 (Consumption Forecaster extension):** Adding `predict_hourly(horizon_hours=72)` requires understanding how the existing GBR model generates per-hour predictions and whether it handles multi-day weekday variation. Review `consumption_forecaster.py` in detail before speccing this task.

Phases with standard patterns (skip research-phase):
- **Phase 1 (Export Foundation):** Config additions and a pure-logic advisory class. Well-defined inputs and outputs. No external dependencies. Standard EMS patterns throughout.
- **Phase 2 (Coordinator Integration):** The coordinator's structure is fully documented and the export check follows the exact same pattern as the existing grid-charge check. Read `coordinator.py` directly.
- **Phase 3 (Weather Client):** Open-Meteo API is free, no auth, well-documented. HTTP client pattern is identical to the existing EVCC client. Standard integration work.
- **Phase 5 (API + Frontend):** Adding endpoints and frontend cards follows established patterns in `api.py` and the React dashboard.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Primary source is the existing codebase. No external library research required for core work. Optional `open-meteo-solar-forecast` verified against PyPI and GitHub. |
| Features | MEDIUM-HIGH | Algorithm patterns (Predbat, Aurora Solar, gridX) well-established. Specific integration points verified against codebase. German feed-in rate range is approximate — user must verify their contract rate. |
| Architecture | HIGH | Built directly from codebase analysis. Component boundaries and integration points are explicit. Advisory and wrapper patterns are already established in the codebase. |
| Pitfalls | HIGH | Domain knowledge combined with direct codebase analysis. Oscillation risk and export-then-buyback are well-documented failure modes in battery EMS literature. DST and BMS accuracy pitfalls are code-verified. |

**Overall confidence:** HIGH

### Gaps to Address

- **Actual feed-in tariff rate:** Research uses 0.082 EUR/kWh as an example. The user must supply their actual contracted feed-in rate via `FEED_IN_RATE_EUR_KWH`. The feature works with any fixed rate.
- **EVCC solar forecast horizon:** The `SolarForecast.timeseries_w` field length is not verified against a live EVCC instance. If the timeseries covers less than 48 hours, Day 3 advisories will always use degraded estimates. Verify at first integration test.
- **Huawei hardware feed-in limit register:** The inverter may have a hardware feed-in limit register that overrides software commands. A read-and-expose step in `HuaweiDriver` is needed before Phase 2. Verify against `huawei-solar` library documentation.
- **ConsumptionForecaster multi-day accuracy:** The existing GBR model's accuracy for Day 2/3 predictions is unknown. May need larger confidence discounts than initially assumed. Measure during Phase 4 implementation.

## Sources

### Primary (HIGH confidence)
- Existing codebase: `backend/coordinator.py`, `backend/scheduler.py`, `backend/tariff.py`, `backend/evcc_client.py`, `backend/consumption_forecaster.py`, `backend/schedule_models.py`, `backend/config.py`, `backend/controller_model.py` — direct analysis, all findings are code-verified
- `.planning/PROJECT.md` — v1.0 design decisions, dual-battery independence principle, architecture context

### Secondary (MEDIUM confidence)
- [Predbat documentation — export threshold logic](https://springfall2008.github.io/batpred/what-does-predbat-do/) — export-vs-store decision patterns, battery cycle cost parameter
- [EVCC tariffs and forecasts documentation](https://docs.evcc.io/en/docs/tariffs) — solar forecast fields available via `/api/state`
- [Open-Meteo API documentation](https://open-meteo.com/en/docs) — free weather API, no key, hourly GHI and DNI available
- [open-meteo-solar-forecast PyPI](https://pypi.org/project/open-meteo-solar-forecast/) — v0.1.29 verified, async-native, Python 3.11+
- [Aurora Solar — storage modeling for energy arbitrage](https://help.aurorasolar.com/hc/en-us/articles/28998198908563-Understanding-Storage-Modeling-for-Energy-Arbitrage) — battery cycle economic analysis
- [gridX — self-sufficiency optimization](https://www.gridx.ai/knowledge/self-sufficiency-optimization) — self-consumption vs. export decision frameworks

### Tertiary (LOW confidence)
- German EEG feed-in tariff rates (0.082-0.12 EUR/kWh range for new PV installations > 10 kWp) — verify against actual contract; rates vary by installation date and capacity
- Huawei SUN2000 hardware export limit register — mentioned in PITFALLS.md; requires verification against `huawei-solar` library documentation during Phase 2

---
*Research completed: 2026-03-23*
*Ready for roadmap: yes*
