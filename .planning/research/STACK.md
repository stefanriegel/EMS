# Stack Research: Grid Export Optimization & Multi-Day Scheduling

**Domain:** Energy management — grid export arbitrage and multi-day weather-aware charge scheduling
**Researched:** 2026-03-23
**Confidence:** HIGH

## Executive Summary

The existing EMS stack already contains nearly everything needed for grid export optimization and multi-day scheduling. EVCC provides solar forecasts for tomorrow and day-after-tomorrow, plus export price timeseries. The fixed feed-in tariff is a single config constant. The real work is algorithmic — extending the scheduler and orchestrator with new decision logic — not adding libraries.

**No new runtime dependencies are required.** The existing `httpx`, tariff engine, consumption forecaster, and EVCC client provide all the data sources. One optional dependency (`open-meteo-solar-forecast`) is recommended only as a fallback if EVCC solar data is unavailable.

## Recommended Stack Additions

### Core Technologies — NONE NEEDED

The existing stack covers all requirements:

| Existing Technology | Role in New Features | Why Sufficient |
|---------------------|---------------------|----------------|
| CompositeTariffEngine | Export vs. import price comparison | Already computes `effective_rate_eur_kwh` per slot; adding a static `feed_in_rate_eur_kwh` config is trivial |
| EvccClient + SolarForecast | Multi-day solar data | Already parses `tomorrow_energy_wh` and `day_after_energy_wh` from EVCC |
| GridPriceSeries | Export price data | Already stores `export_eur_kwh` timeseries from EVCC |
| ConsumptionForecaster | Demand prediction for export-then-buyback avoidance | GradientBoostingRegressor models already produce hourly forecasts |
| Scheduler | Multi-day scheduling | Extend `compute_schedule()` — no new framework needed |
| SystemConfig | Feed-in control flags | `huawei_feed_in_allowed` / `victron_feed_in_allowed` already exist |

### Supporting Libraries — One Optional Addition

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `open-meteo-solar-forecast` | 0.1.29 | Fallback solar forecast when EVCC is offline | Only when EVCC solar data is `None` AND multi-day scheduling needs a forecast |

**Why optional, not required:** EVCC already provides day+1 and day+2 solar forecasts via its `/api/state` endpoint (parsed into `SolarForecast.tomorrow_energy_wh` and `SolarForecast.day_after_energy_wh`). The `open-meteo-solar-forecast` library is only valuable as a resilience fallback — the EMS should not depend on it for normal operation.

**If added:** `open-meteo-solar-forecast>=0.1.29` — async-native, supports Python 3.11+, uses `aiohttp` under the hood. Configure with `latitude`, `longitude`, `declination`, `azimuth`, `dc_kwp` (already known from the PV installation).

### Development Tools — No Additions

Existing pytest + pytest-anyio + pytest-mock cover all testing needs for the new algorithmic code.

## What Needs to Change (Config, Not Libraries)

### New Configuration Parameters

These are the only additions needed — all are config fields, not dependencies:

| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `FEED_IN_RATE_EUR_KWH` | float | 0.082 | Fixed feed-in tariff rate (German EEG Einspeiseverguetung, ~8.2 ct/kWh for new installations) |
| `EXPORT_MIN_SOC_PCT` | float | 30.0 | Don't export below this SoC — prevents export-then-buyback at higher prices |
| `SCHEDULER_HORIZON_DAYS` | int | 2 | How many days ahead the scheduler plans (1=today+tomorrow, 2=today+2 days) |
| `EXPORT_ENABLED` | bool | False | Master switch for grid export arbitrage |

### New/Extended Dataclasses

No new libraries needed — these are pure Python dataclass changes:

| Model | Change | Purpose |
|-------|--------|---------|
| `SystemConfig` | Add `feed_in_rate_eur_kwh: float`, `export_min_soc_pct: float`, `export_enabled: bool` | Export arbitrage parameters |
| `OrchestratorConfig` | Add `scheduler_horizon_days: int` | Multi-day horizon config |
| `ChargeSchedule` | Add `export_windows: list[ExportWindow]` | Schedule windows where export is profitable |
| `OptimizationReasoning` | Add `day2_solar_kwh: float`, `export_revenue_eur: float` | Multi-day reasoning fields |

### Extended Existing Modules

| Module | Change | Scope |
|--------|--------|-------|
| `scheduler.py` | Extend `compute_schedule()` to evaluate 2-day horizon, produce export windows | Medium — algorithmic, not library work |
| `orchestrator.py` | Add export dispatch state (new `ControlState.EXPORTING`?) or extend `DISCHARGE` with export-awareness | Medium — decision logic in the control loop |
| `tariff.py` | Add `get_export_spread()` method comparing feed-in rate to current import rate | Small — simple arithmetic |
| `config.py` | Add feed-in rate and export config fields | Small |
| `api.py` | Expose export windows and multi-day schedule via REST | Small |

## Installation

```bash
# No new core dependencies needed.

# Optional fallback solar forecast (only if EVCC solar data unreliable):
pip install "open-meteo-solar-forecast>=0.1.29"

# Or in pyproject.toml:
# [project.optional-dependencies]
# solar-fallback = ["open-meteo-solar-forecast>=0.1.29"]
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| EVCC solar forecast (existing) | `open-meteo-solar-forecast` | Only when EVCC is unreliable or offline; EVCC is preferred because it already runs on the same host and the data pipeline is proven |
| EVCC solar forecast (existing) | `forecast-solar` (PyPI) | Only if you need 7-day horizon; note it requires API key for extended forecasts, has rate limits on free tier |
| EVCC solar forecast (existing) | Open-Meteo raw weather API + custom PV model | Never — overengineered; building a PV production model from raw irradiance data is unnecessary when EVCC and `open-meteo-solar-forecast` already solve this |
| Fixed feed-in rate (config constant) | Dynamic feed-in tariff via EVCC `export_eur_kwh` | When the user has a dynamic feed-in tariff (spot market); the EVCC `GridPriceSeries.export_eur_kwh` already supports this, so the code should handle both fixed and dynamic |
| Pure Python scheduling logic | PuLP / scipy.optimize LP solver | Never for this scale — the optimization problem (should I export or store?) is a simple comparison, not a linear program; LP solvers add massive dependencies for zero benefit |
| Pure Python scheduling logic | Google OR-Tools | Same reason — 94 kWh across 2 batteries with a fixed feed-in rate is not a combinatorial optimization problem |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| PuLP / scipy.optimize | The export decision is `if feed_in_rate > import_rate: export`, not linear programming; adds 50+ MB of dependencies | Simple conditional logic in `scheduler.py` |
| Google OR-Tools | Same as above — the problem is not NP-hard | Simple conditional logic |
| Solcast API | Commercial, requires paid API key, adds cloud dependency (violates local-only constraint) | EVCC solar forecast or `open-meteo-solar-forecast` |
| pandas | Overkill for the simple timeseries comparisons needed; numpy is already available for any array math | `numpy` (already in deps) or plain Python lists |
| Any weather API requiring API keys | Violates the no-cloud-dependency constraint and complicates deployment | `open-meteo-solar-forecast` (no key needed) or EVCC |

## Integration Points

### Data Flow for Export Arbitrage

```
EVCC GridPriceSeries.export_eur_kwh ---+
                                       +---> ExportDecisionEngine ---> Orchestrator
SystemConfig.feed_in_rate_eur_kwh ----+        |                      (dispatch export)
                                               |
CompositeTariffEngine.effective_rate ----------+
  (current/upcoming import price)              (compare: export now vs. store for later self-consumption)
```

**Decision logic is simple comparison:**
- `feed_in_rate > upcoming_import_rate` AND `soc > export_min_soc` AND `no upcoming consumption spike` --> export
- Otherwise --> store for self-consumption

### Data Flow for Multi-Day Scheduling

```
EVCC SolarForecast ------+
  .tomorrow_energy_wh    |
  .day_after_energy_wh   +---> MultiDayScheduler ---> ChargeSchedule
                         |      |                      (with 2-day slots)
ConsumptionForecaster ---+      |
  .today_expected_kwh           |
  (extend to multi-day)        |
                                |
CompositeTariffEngine ---------+
  .get_price_schedule(day+1)
  .get_price_schedule(day+2)
```

**Key insight:** The tariff engine's `get_price_schedule(date)` already accepts any date. Calling it for day+1 and day+2 is trivial. The consumption forecaster needs a minor extension to forecast day+2 (currently forecasts "today").

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| `open-meteo-solar-forecast>=0.1.29` | Python 3.12+ | Uses `aiohttp`; no conflicts with existing `httpx` (different async HTTP client, both work fine side by side) |
| `open-meteo-solar-forecast>=0.1.29` | `numpy>=1.25` | No direct numpy dependency; compatible |
| Existing `httpx` | All new features | EVCC client uses `httpx` for all HTTP calls; no change needed |

## Key Architectural Decision

**Do NOT split the scheduler into a separate service or add a message queue.** The multi-day extension is a natural evolution of the existing `Scheduler.compute_schedule()` method. It runs once nightly, computes a 2-day plan instead of a 1-day plan, and stores it on `Scheduler.active_schedule`. The orchestrator reads the schedule on each control cycle — same pattern as today.

**Do NOT add a separate "export optimizer" service.** Export decisions are real-time (based on current SoC, current production, current import price) and belong in the orchestrator's control loop, not in a separate process.

## Sources

- Codebase analysis: `backend/scheduler.py`, `backend/tariff.py`, `backend/evcc_client.py`, `backend/schedule_models.py`, `backend/config.py`, `backend/orchestrator.py` — HIGH confidence (primary source)
- [EVCC solar forecast docs](https://docs.evcc.io/en/docs/tariffs) — EVCC provides tomorrow + day-after-tomorrow solar forecasts
- [open-meteo-solar-forecast PyPI](https://pypi.org/project/open-meteo-solar-forecast/) — v0.1.29, async-native, Python 3.11+, no API key
- [Open-Meteo](https://open-meteo.com/) — free weather API, no key, non-commercial
- [open-meteo-solar-forecast GitHub](https://github.com/rany2/open-meteo-solar-forecast) — async usage, estimate fields, configuration

---
*Stack research for: EMS v1.1 — Grid Export Optimization & Multi-Day Scheduling*
*Researched: 2026-03-23*
