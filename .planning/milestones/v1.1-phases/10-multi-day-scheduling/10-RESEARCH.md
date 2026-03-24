# Phase 10: Multi-Day Scheduling - Research

**Researched:** 2026-03-23
**Domain:** Energy scheduling algorithms, multi-day forecast integration, Python dataclasses
**Confidence:** HIGH

## Summary

Phase 10 builds a `WeatherScheduler` that wraps the existing `Scheduler` (decorator pattern, no modifications to `scheduler.py`) to produce weather-aware multi-day charge plans. The building blocks are already in place from Phase 9: `SolarForecastMultiDay` (72h hourly solar Wh), `HourlyConsumptionForecast` (72h hourly demand kWh), and the cascading `get_solar_forecast()` provider. The core algorithm compares per-day solar supply vs. consumption demand across a 3-day horizon, applies confidence discounting (1.0/0.8/0.6), and adjusts tonight's grid charge target accordingly. A `DayPlan` dataclass wraps per-day charge slots with a day index; Day 0 (tonight) is actionable while Day 1/2 are advisory. Intra-day re-planning runs every ~6 hours and triggers a recompute when forecast deviation exceeds a threshold.

The existing `Scheduler.compute_schedule()` already handles the mechanics of SoC target derivation, tariff window selection, and slot construction. The `WeatherScheduler` only needs to: (1) gather multi-day forecasts, (2) compute a weather-adjusted `net_charge_kwh` for tonight, (3) delegate to `Scheduler.compute_schedule()` or replicate its slot-building logic with the adjusted target, and (4) package results into `DayPlan` containers. The coordinator's `_check_grid_charge()` reads `scheduler.active_schedule` -- the WeatherScheduler must set this same attribute so the coordinator consumes the enhanced schedule transparently.

**Primary recommendation:** WeatherScheduler as a wrapper class with the same `active_schedule` attribute interface. Store it on `app.state.scheduler` so the coordinator and API consume it without code changes.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
None explicitly locked -- all at Claude's discretion.

### Claude's Discretion
All implementation choices are at Claude's discretion -- algorithmic phase with clear success criteria. Key design notes from research and prior context:
- WeatherScheduler wraps existing Scheduler (decorator pattern -- don't modify Scheduler)
- DayPlan model extends ChargeSchedule with per-day containers and day index; Day 2/3 are advisory only
- Confidence weights: Day 1 = 1.0, Day 2 = 0.8, Day 3 = 0.6
- Intra-day re-planning: re-run approximately every 6h when forecast deviates significantly
- Conservative charge ceiling: leave headroom proportional to forecast uncertainty
- Don't over-charge: forecast can be wrong, leave room for PV to fill batteries
- Winter critical: too little in battery is worse than too much grid charge
- Summer: long cloudy stretch -> okay to charge on cheap slots, but don't max out

### Deferred Ideas (OUT OF SCOPE)
None
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MDS-02 | Nightly grid charge targets adjusted by multi-day forecast -- charge more before cloudy stretches, reduce/skip when sunny days ahead | WeatherScheduler algorithm compares 3-day solar vs. demand; adjusts net_charge_kwh up/down |
| MDS-03 | Confidence-weighted forecast discounting -- Day 1 at full weight, Day 2 at ~80%, Day 3 at ~60% | Confidence weights applied to daily solar totals before supply/demand comparison |
| MDS-04 | Intra-day re-planning -- re-run schedule approximately every 6 hours when forecast deviates significantly | New asyncio loop alongside nightly loop; deviation threshold triggers recompute |
| MDS-05 | DayPlan model evolution -- ChargeSchedule extended with per-day containers and day index | DayPlan dataclass in schedule_models.py; inherits/contains ChargeSchedule per day |
| MDS-07 | Conservative charge ceiling -- grid charge targets leave headroom proportional to forecast uncertainty | Headroom formula: reduce target SoC by (1 - confidence) * capacity_fraction |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib dataclasses | 3.12+ | DayPlan, WeatherScheduleResult models | Already used throughout schedule_models.py |
| Python stdlib asyncio | 3.12+ | Intra-day re-planning loop | Already used for nightly scheduler loop |
| Python stdlib datetime | 3.12+ | Timezone-aware scheduling | Already used in scheduler.py |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest-anyio | existing | Async test support | All WeatherScheduler tests |
| pytest-mock | existing | Mocking forecasters and scheduler | Unit tests |

No new dependencies needed. All building blocks exist in the codebase.

## Architecture Patterns

### Recommended Project Structure
```
backend/
  weather_scheduler.py    # NEW: WeatherScheduler wrapper class
  schedule_models.py      # MODIFY: add DayPlan dataclass
  main.py                 # MODIFY: wire WeatherScheduler, add intra-day loop
  scheduler.py            # DO NOT MODIFY
  weather_client.py       # DO NOT MODIFY
  consumption_forecaster.py  # DO NOT MODIFY
tests/
  test_weather_scheduler.py  # NEW: WeatherScheduler unit tests
```

### Pattern 1: Decorator/Wrapper Scheduler
**What:** `WeatherScheduler` wraps `Scheduler` and exposes the same `active_schedule` attribute. The coordinator reads `scheduler.active_schedule` -- by storing `WeatherScheduler` on `app.state.scheduler`, the coordinator and API work unchanged.

**When to use:** Always -- this is the locked design pattern from CONTEXT.md.

**Example:**
```python
class WeatherScheduler:
    """Multi-day weather-aware charge scheduler.

    Wraps the existing Scheduler to adjust charge targets based on
    multi-day solar and consumption forecasts.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        evcc_client,
        weather_client: OpenMeteoClient | None,
        consumption_forecaster,
        sys_config: SystemConfig,
        orch_config: OrchestratorConfig,
    ) -> None:
        self._scheduler = scheduler
        self._evcc_client = evcc_client
        self._weather_client = weather_client
        self._consumption_forecaster = consumption_forecaster
        self._sys_config = sys_config
        self._orch_config = orch_config
        self.active_schedule: ChargeSchedule | None = None
        self.schedule_stale: bool = False
        self.active_day_plans: list[DayPlan] | None = None

    async def compute_schedule(self, writer=None) -> ChargeSchedule:
        """Weather-aware schedule computation.

        1. Fetch multi-day solar forecast (72h)
        2. Fetch multi-day consumption forecast (72h)
        3. Compare supply vs demand per day with confidence weights
        4. Adjust tonight's net_charge_kwh
        5. Delegate to inner Scheduler or build slots directly
        6. Package into DayPlan containers
        """
        # ... algorithm here ...
```

### Pattern 2: DayPlan Container Model
**What:** `DayPlan` is a per-day container holding charge slots, solar/demand forecasts, and confidence for that day. A list of 3 `DayPlan` objects represents the multi-day outlook.

**Example:**
```python
@dataclass
class DayPlan:
    """Per-day charge plan within a multi-day schedule.

    Day 0 is actionable (tonight's charge window).
    Days 1-2 are advisory (shown in dashboard, not executed).
    """
    day_index: int              # 0=today/tonight, 1=tomorrow, 2=day_after
    date: date                  # Calendar date for this day
    solar_forecast_kwh: float   # Expected solar production
    consumption_forecast_kwh: float  # Expected consumption
    net_energy_kwh: float       # solar - consumption (positive = surplus)
    confidence: float           # 1.0, 0.8, or 0.6
    charge_target_kwh: float    # Grid charge energy needed
    slots: list[ChargeSlot]     # Charge slots for this day (empty for advisory)
    advisory: bool              # True for Day 1/2
```

### Pattern 3: Intra-day Re-planning Loop
**What:** A separate asyncio task that runs every ~6 hours, fetches fresh forecasts, compares against the active plan, and triggers a recompute if deviation exceeds a threshold.

**Example:**
```python
async def _intraday_replan_loop(
    weather_scheduler: WeatherScheduler,
    writer,
    interval_s: int = 21600,  # 6 hours
    deviation_threshold: float = 0.20,  # 20% change triggers replan
) -> None:
    await asyncio.sleep(interval_s)  # initial delay
    while True:
        try:
            changed = await weather_scheduler.check_forecast_deviation(deviation_threshold)
            if changed:
                await weather_scheduler.compute_schedule(writer)
                logger.info("intraday-replan: forecast deviation detected, schedule recomputed")
            else:
                logger.debug("intraday-replan: forecast stable, no replan needed")
        except Exception as exc:
            logger.warning("intraday-replan: failed: %s", exc)
        await asyncio.sleep(interval_s)
```

### Pattern 4: Weather-Aware Charge Algorithm
**What:** The core algorithm that determines how much grid charge is needed based on multi-day outlook.

**Algorithm outline:**
```
For each day d in [0, 1, 2]:
    solar_kwh[d] = daily_solar_forecast[d] * confidence[d]
    demand_kwh[d] = daily_consumption_forecast[d]
    deficit[d] = max(0, demand_kwh[d] - solar_kwh[d])

# Forward-looking adjustment:
# If tomorrow is cloudy (deficit[1] > threshold), increase tonight's charge
# If tomorrow is sunny (deficit[1] near 0), reduce tonight's charge
# Day 2 has less influence due to lower confidence

tonight_charge = deficit[0]  # base: tonight's gap
# Add fraction of tomorrow's deficit (batteries need reserves)
tomorrow_contribution = deficit[1] * 0.5  # partial pre-charge for tomorrow
# Day 2 has minimal influence
day2_contribution = deficit[2] * 0.2

total_charge = tonight_charge + tomorrow_contribution + day2_contribution

# Conservative ceiling: leave headroom for forecast uncertainty
max_charge = total_capacity * (1.0 - uncertainty_headroom)
total_charge = min(total_charge, max_charge)

# Winter safety: enforce minimum charge floor
if is_winter:
    total_charge = max(total_charge, winter_minimum_kwh)
```

### Anti-Patterns to Avoid
- **Modifying Scheduler directly:** The existing Scheduler.compute_schedule() must not be changed. WeatherScheduler wraps it.
- **Caching forecasts across runs:** Products change, forecasts change. Always fetch fresh on each compute_schedule call.
- **Over-charging on forecast uncertainty:** When confidence is low, charge less not more (exception: winter safety minimum).
- **Tightly coupling DayPlan to coordinator:** The coordinator only reads `active_schedule` (a `ChargeSchedule`). DayPlan is additional metadata for the API/dashboard.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Solar forecast fetching | Custom HTTP client | `get_solar_forecast()` from weather_client.py | Already cascading EVCC -> Open-Meteo -> seasonal |
| Consumption prediction | Custom estimation | `ConsumptionForecaster.predict_hourly()` | Already has ML + seasonal fallback |
| Tariff window selection | Custom slot picker | Existing Scheduler slot selection logic | Already handles cheapest window, contiguous slots |
| SoC clamping | Manual min/max | Existing SystemConfig limits | Already enforced in Scheduler |

**Key insight:** The hard infrastructure work (forecast data, ML consumption, tariff engine) was done in Phases 3 and 9. Phase 10 is an algorithm layer that consumes these existing data sources and produces adjusted charge targets.

## Common Pitfalls

### Pitfall 1: Breaking the Coordinator Interface
**What goes wrong:** WeatherScheduler doesn't expose `active_schedule` or `schedule_stale` the same way Scheduler does, causing coordinator to silently skip grid charge.
**Why it happens:** The coordinator reads `self._scheduler.active_schedule` directly (duck typing).
**How to avoid:** WeatherScheduler must have `active_schedule: ChargeSchedule | None` and `schedule_stale: bool` attributes, kept in sync after every compute.
**Warning signs:** Grid charge never activates after wiring WeatherScheduler.

### Pitfall 2: Double-Counting Solar Energy
**What goes wrong:** If WeatherScheduler adjusts the charge target AND the inner Scheduler also applies its own solar discount (the 0.8 factor in step 4), the solar benefit is counted twice.
**Why it happens:** Scheduler.compute_schedule() already subtracts solar forecast from consumption.
**How to avoid:** Two options: (a) WeatherScheduler computes the final adjusted `net_charge_kwh` and builds slots itself without delegating to Scheduler, or (b) WeatherScheduler calls Scheduler but overrides/adjusts the result. Option (a) is cleaner -- replicate the slot-building logic with the weather-adjusted target.
**Warning signs:** Batteries consistently under-charged compared to expectations.

### Pitfall 3: Intra-day Loop Conflicts with Nightly Loop
**What goes wrong:** Both loops call compute_schedule concurrently, producing race conditions on `active_schedule`.
**Why it happens:** asyncio tasks are concurrent within the same event loop.
**How to avoid:** Use an `asyncio.Lock` in WeatherScheduler.compute_schedule() to serialize access. Or, have the nightly loop call WeatherScheduler.compute_schedule() instead of Scheduler directly, and the intra-day loop also calls WeatherScheduler.
**Warning signs:** Intermittent schedule flips or stale schedules.

### Pitfall 4: Winter Under-Charging
**What goes wrong:** On a sunny winter day, the algorithm reduces grid charge, but short daylight hours mean actual solar production falls short.
**Why it happens:** Solar forecasts may overestimate winter production (low sun angle, cloud cover).
**How to avoid:** In winter months, enforce a minimum charge floor (e.g., 30% of capacity) regardless of solar forecast. Use the existing `sys_config.winter_months` and `winter_min_soc_boost_pct`.
**Warning signs:** Batteries depleted before evening in winter despite "sunny" forecast.

### Pitfall 5: Stale Forecast Triggering Unnecessary Replans
**What goes wrong:** Minor forecast fluctuations trigger constant re-planning, causing schedule churn.
**Why it happens:** Deviation threshold too low, or comparing absolute Wh differences instead of relative.
**How to avoid:** Use relative deviation (percentage change in daily total) with a meaningful threshold (e.g., 20%). Also add a cooldown period after each replan (e.g., no replan within 2 hours of last one).
**Warning signs:** Multiple replan log entries per day without significant weather change.

## Code Examples

### DayPlan Dataclass
```python
# In schedule_models.py
from datetime import date

@dataclass
class DayPlan:
    """Per-day charge plan within a multi-day weather-aware schedule."""
    day_index: int
    date: date
    solar_forecast_kwh: float
    consumption_forecast_kwh: float
    net_energy_kwh: float
    confidence: float
    charge_target_kwh: float
    slots: list[ChargeSlot]
    advisory: bool
```

### WeatherScheduler Confidence Weighting
```python
# Confidence weights by day horizon
_DAY_CONFIDENCE = [1.0, 0.8, 0.6]

def _compute_adjusted_charge(
    solar_daily_kwh: list[float],  # 3 values
    consumption_daily_kwh: list[float],  # 3 values
    total_capacity_kwh: float,
    is_winter: bool,
) -> tuple[float, list[DayPlan]]:
    """Compute weather-adjusted grid charge target for tonight."""
    deficits = []
    for d in range(3):
        conf = _DAY_CONFIDENCE[d]
        effective_solar = solar_daily_kwh[d] * conf
        deficit = max(0.0, consumption_daily_kwh[d] - effective_solar)
        deficits.append(deficit)

    # Tonight's charge covers today's deficit plus partial pre-charge
    tonight = deficits[0]
    # Tomorrow: pre-charge 50% of expected deficit (confidence-weighted)
    tonight += deficits[1] * 0.5
    # Day after: minimal contribution (20%)
    tonight += deficits[2] * 0.2

    # Conservative ceiling: never charge more than 85% of capacity
    # to leave room for unexpected PV
    headroom = 0.15 if not is_winter else 0.05
    max_charge = total_capacity_kwh * (1.0 - headroom)
    tonight = min(tonight, max_charge)

    # Winter floor: always charge at least 30% of capacity
    if is_winter:
        winter_floor = total_capacity_kwh * 0.30
        tonight = max(tonight, winter_floor)

    return tonight, deficits  # deficits used for DayPlan construction
```

### Forecast Deviation Check
```python
async def check_forecast_deviation(self, threshold: float = 0.20) -> bool:
    """Check if current forecast deviates significantly from plan basis."""
    if self._last_solar_daily_kwh is None:
        return False

    solar = await get_solar_forecast(self._evcc_client, self._weather_client)
    new_daily = [wh / 1000.0 for wh in solar.daily_energy_wh]

    for d in range(min(3, len(new_daily))):
        old = self._last_solar_daily_kwh[d]
        new = new_daily[d]
        if old > 0:
            deviation = abs(new - old) / old
            if deviation > threshold:
                return True
        elif new > 1.0:  # was zero, now significant
            return True

    return False
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-anyio |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `python -m pytest tests/test_weather_scheduler.py -x` |
| Full suite command | `python -m pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MDS-02 | Charge increases before cloudy days, decreases for sunny | unit | `python -m pytest tests/test_weather_scheduler.py::test_cloudy_increases_charge -x` | Wave 0 |
| MDS-02 | Charge decreases/skips when sunny days ahead | unit | `python -m pytest tests/test_weather_scheduler.py::test_sunny_reduces_charge -x` | Wave 0 |
| MDS-03 | Day 1 confidence 1.0, Day 2 0.8, Day 3 0.6 applied | unit | `python -m pytest tests/test_weather_scheduler.py::test_confidence_weights -x` | Wave 0 |
| MDS-04 | Re-plan triggered on >20% forecast deviation | unit | `python -m pytest tests/test_weather_scheduler.py::test_replan_on_deviation -x` | Wave 0 |
| MDS-04 | No re-plan when forecast stable | unit | `python -m pytest tests/test_weather_scheduler.py::test_no_replan_stable -x` | Wave 0 |
| MDS-05 | DayPlan has day_index, date, advisory flag | unit | `python -m pytest tests/test_weather_scheduler.py::test_dayplan_structure -x` | Wave 0 |
| MDS-05 | Day 0 actionable, Day 1/2 advisory | unit | `python -m pytest tests/test_weather_scheduler.py::test_day_advisory_flags -x` | Wave 0 |
| MDS-07 | Charge ceiling leaves headroom | unit | `python -m pytest tests/test_weather_scheduler.py::test_headroom_ceiling -x` | Wave 0 |
| MDS-07 | Winter floor enforced despite sunny forecast | unit | `python -m pytest tests/test_weather_scheduler.py::test_winter_floor -x` | Wave 0 |
| - | active_schedule compatible with coordinator | unit | `python -m pytest tests/test_weather_scheduler.py::test_active_schedule_interface -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_weather_scheduler.py -x`
- **Per wave merge:** `python -m pytest tests/ -x`
- **Phase gate:** Full suite green before verify

### Wave 0 Gaps
- [ ] `tests/test_weather_scheduler.py` -- all WeatherScheduler unit tests (10+ test functions)
- No framework install needed -- pytest-anyio already configured

## Sources

### Primary (HIGH confidence)
- `backend/scheduler.py` -- existing Scheduler implementation, active_schedule interface
- `backend/schedule_models.py` -- ChargeSchedule, ChargeSlot, SolarForecastMultiDay, HourlyConsumptionForecast dataclasses
- `backend/weather_client.py` -- get_solar_forecast() cascading provider, OpenMeteoClient
- `backend/consumption_forecaster.py` -- predict_hourly() for 72h consumption
- `backend/coordinator.py` -- _check_grid_charge() reads scheduler.active_schedule
- `backend/main.py` -- lifespan wiring, _nightly_scheduler_loop pattern
- `backend/api.py` -- /api/optimization/schedule endpoint reads scheduler.active_schedule

### Secondary (MEDIUM confidence)
- Phase 9 summaries -- confirmed SolarForecastMultiDay and HourlyConsumptionForecast are ready
- CONTEXT.md -- design decisions from user discussion

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, all building blocks exist
- Architecture: HIGH -- clear decorator pattern, well-understood interfaces, prior codebase patterns
- Pitfalls: HIGH -- derived from reading actual coordinator/scheduler integration code

**Research date:** 2026-03-23
**Valid until:** 2026-04-23 (stable algorithmic domain, no external API changes expected)
