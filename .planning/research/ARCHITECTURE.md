# Architecture Patterns: Grid Export Optimization & Multi-Day Scheduling

**Domain:** Energy management — grid export arbitrage and multi-day weather-aware scheduling
**Researched:** 2026-03-23
**Scope:** New components and integration points for v1.1 features on top of existing dual-battery EMS

## Existing Architecture Summary

The current system has clear boundaries:

```
EvccClient ──> Scheduler ──> ChargeSchedule ──> Coordinator ──> Controllers
     |              |                                  |
SolarForecast   TariffEngine                     Grid meter (P_target)
ConsumptionForecast                              Role assignment
                                                 Hysteresis/ramp
```

**Coordinator** runs a 5s control loop: poll controllers, check EVCC hold, check grid charge slot, compute P_target from grid meter, assign roles (PRIMARY_DISCHARGE / SECONDARY_DISCHARGE / CHARGING / HOLDING / GRID_CHARGE), allocate watts with hysteresis and ramp limiting.

**Scheduler** runs nightly: fetches EVCC state (EVopt, solar forecast, grid prices), queries consumption history, derives per-battery SoC targets, selects cheapest tariff window, produces `ChargeSchedule` with `ChargeSlot` list.

**Key observation:** The coordinator currently has NO export-awareness. It zeroes grid import (P_target > 0 => discharge, P_target < 0 => charge from PV surplus). It never intentionally exports stored energy. The `feed_in_allowed` flags in `SystemConfig` exist but are not used in the coordinator's discharge logic.

## Recommended Architecture

### Component Map: New vs Modified

| Component | Status | Changes |
|-----------|--------|---------|
| `ExportAdvisor` | **NEW** | Real-time export vs. store decision engine |
| `WeatherScheduler` | **NEW** | Multi-day horizon scheduling wrapper |
| `WeatherClient` | **NEW** | Weather API client (solar irradiance forecast) |
| `Coordinator._run_cycle()` | MODIFY | Add export path between grid-charge check and P_target computation |
| `Scheduler.compute_schedule()` | MODIFY | Accept multi-day horizon, return `MultiDaySchedule` |
| `CompositeTariffEngine` | MODIFY | Add `feed_in_rate_eur_kwh` to pricing model |
| `SystemConfig` | MODIFY | Add `feed_in_rate_eur_kwh: float` field |
| `schedule_models.py` | MODIFY | Add `ExportSlot`, `MultiDaySchedule` types |
| `api.py` | MODIFY | Expose export decisions and multi-day schedule via REST |
| Frontend | MODIFY | Display export status, multi-day forecast in tariff timeline |

### Data Flow with Export Optimization

```
                                    Coordinator._run_cycle()
                                    ========================
                                    1. Poll controllers
                                    2. Check EVCC hold         (unchanged)
                                    3. Check grid charge slot   (unchanged)
                    NEW ──────────> 4. Check export advisory    <── ExportAdvisor
                                    5. Compute P_target         (unchanged)
                                    6. Route: charge / discharge (unchanged)

ExportAdvisor inputs:
  - Current import rate (from TariffEngine)
  - Fixed feed-in rate (from SystemConfig)
  - Pool SoC (from controller snapshots)
  - Upcoming consumption forecast (from ConsumptionForecaster)
  - Solar forecast (from EVCC or WeatherClient)
  - Next charge schedule (from Scheduler)

ExportAdvisor output:
  - STORE: do nothing special, let normal P_target logic run
  - EXPORT: override P_target to intentionally push stored energy to grid
  - export_power_w: recommended export power budget
  - reasoning: structured explanation for decision log
```

### Data Flow with Multi-Day Scheduling

```
                    Nightly Scheduler Loop
                    ======================
                    1. Fetch EVCC state (solar, prices)      (unchanged)
          NEW ───>  2. Fetch weather forecast (2-3 days)     <── WeatherClient
                    3. Query consumption history              (unchanged)
          NEW ───>  4. Compute multi-day energy balance       <── WeatherScheduler
                    5. Derive per-battery SoC targets          (modified)
                    6. Select cheapest tariff windows          (unchanged)
                    7. Build MultiDayPlan                      (new output)

WeatherScheduler wraps existing Scheduler:
  - Day 1: Existing Scheduler logic (enhanced with day-2/3 lookahead)
  - Day 2-3: Advisory forecasts only (no binding charge slots)
  - Decision: "charge more tonight" or "defer — sunny tomorrow"
```

### Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| `ExportAdvisor` | Real-time export vs. store arbitrage using feed-in rate vs. import rate, consumption lookahead | Coordinator (called per-cycle), TariffEngine (rate lookup), Scheduler (upcoming slots), ConsumptionForecaster (demand forecast) |
| `WeatherClient` | Fetch multi-day solar irradiance forecast from weather API | Scheduler (provides forecast data), EvccClient (fallback/supplement solar data) |
| `WeatherScheduler` | Extend scheduling horizon to 2-3 days, compute cross-day energy balance | Scheduler (wraps or extends), WeatherClient (forecast input), ConsumptionForecaster (multi-day demand) |
| `Coordinator` (modified) | Insert export advisory check into control loop | ExportAdvisor (query), Controllers (execute export commands) |

## New Component Designs

### ExportAdvisor

**Purpose:** Decide each control cycle whether to export stored energy to the grid for profit, or retain it for upcoming consumption.

**Why a separate class:** The coordinator should not contain tariff arbitrage logic directly. Export decisions require forward-looking analysis (consumption forecast, upcoming rate changes, scheduled charge windows) that does not belong in the 5s reactive control loop. The advisor pre-computes and the coordinator just asks "should I export now?"

```python
@dataclass
class ExportDecision:
    """Output of ExportAdvisor for a single control cycle."""
    action: Literal["STORE", "EXPORT"]
    export_power_w: float  # 0 when STORE
    reasoning: str
    margin_eur_kwh: float  # feed_in - import_rate (positive = export profitable)
    soc_after_export_pct: float  # projected pool SoC if export runs for 1 hour

class ExportAdvisor:
    """Real-time grid export arbitrage engine.

    Core rule: Export when feed_in_rate > current_import_rate AND
    pool SoC is high enough that upcoming consumption + next charge
    window can cover the deficit. Never export below reserve threshold.
    """

    def __init__(
        self,
        tariff_engine: CompositeTariffEngine,
        sys_config: SystemConfig,
        orch_config: OrchestratorConfig,
    ) -> None: ...

    def advise(
        self,
        h_snap: ControllerSnapshot,
        v_snap: ControllerSnapshot,
        scheduler: Scheduler | None,
        consumption_forecast_kwh: float,
        solar_forecast_kwh: float,
        now: datetime,
    ) -> ExportDecision: ...
```

**Decision logic:**

1. **Rate check:** `feed_in_rate > import_rate * safety_factor` (safety_factor ~0.95 to avoid marginal exports). With a fixed feed-in tariff (e.g., 0.082 EUR/kWh), this means export is profitable only during off-peak import windows where import rate < feed-in rate. With Octopus Go off-peak at ~0.07 EUR/kWh + Modul3 NT, there may be windows where this holds.

2. **SoC floor check:** Combined pool SoC must remain above a configurable `export_min_soc_pct` (suggest 30%) after projected export. This ensures enough stored energy for the household between now and the next cheap charge window.

3. **Consumption lookahead:** Project energy demand until the next charge slot. If `stored_energy - export_energy > projected_demand`, export is safe.

4. **Solar surplus handling:** When PV production exceeds load AND batteries are full (SoC >= 95%), always export -- there is no alternative. This is currently "lost" energy because the coordinator just holds when batteries are full.

5. **Export power budget:** Allocate watts to export based on the surplus and battery discharge limits. Cap at grid connection limits.

**Critical subtlety:** The fixed feed-in tariff (Einspeisevergutung) in Germany is typically 0.082 EUR/kWh for systems > 10 kWp. This is LOWER than most import rates (Octopus peak ~0.28 EUR/kWh). So the primary export scenario is: batteries full + PV producing. The secondary scenario (discharge batteries to grid for profit) only makes economic sense when import rate is very low (off-peak + NT window) AND you would charge again anyway. This is a narrow arbitrage.

**Recommended approach:** Start simple -- export only when batteries are full and PV is producing. Defer the "discharge stored energy for arbitrage" case to a later iteration since the margins are thin with a fixed feed-in rate.

### WeatherClient

**Purpose:** Fetch multi-day solar irradiance forecast from a free weather API.

```python
@dataclass
class DaySolarForecast:
    """Solar energy forecast for a single day."""
    date: date
    expected_kwh: float
    hourly_w: list[float]  # 24 values, average expected PV power per hour
    cloud_cover_pct: float  # average cloud cover (0=clear, 100=overcast)
    confidence: float  # 0.0-1.0, decreases with forecast horizon

@dataclass
class MultiDaySolarForecast:
    """2-3 day solar forecast horizon."""
    days: list[DaySolarForecast]
    source: str  # "evcc", "open-meteo", "fallback"
    fetched_at: datetime

class WeatherClient:
    """Fetch multi-day solar irradiance from Open-Meteo API.

    Open-Meteo is free, no API key required, provides hourly GHI
    (Global Horizontal Irradiance) for up to 16 days.
    """

    def __init__(self, latitude: float, longitude: float, peak_kwp: float) -> None: ...

    async def get_forecast(self, days: int = 3) -> MultiDaySolarForecast: ...
```

**API choice: Open-Meteo** because it is free, requires no API key, provides hourly global horizontal irradiance (GHI) and direct normal irradiance (DNI), and works entirely locally (no cloud account). The EMS already uses EVCC's solar forecast for day-1; the WeatherClient extends this to days 2-3.

**PV conversion:** GHI (W/m2) to expected PV output (W) requires `peak_kwp` and a simple linear model: `pv_w = ghi_w_m2 * peak_kwp / 1000 * efficiency_factor`. An efficiency factor of 0.75-0.85 accounts for temperature, inverter, and shading losses. This is configurable.

**Fallback:** When Open-Meteo is unreachable, fall back to EVCC's `SolarForecast.day_after_energy_wh` for day-2, and use day-2 as a proxy for day-3. When EVCC is also unavailable, use seasonal averages (pre-computed from HA statistics or hardcoded by month).

### WeatherScheduler

**Purpose:** Extend the nightly scheduling decision from 1-day to 2-3 day horizon.

This is NOT a separate scheduler -- it wraps the existing `Scheduler` and provides additional context.

```python
@dataclass
class MultiDayPlan:
    """Multi-day energy balance and scheduling advisory."""
    tonight_schedule: ChargeSchedule  # binding -- from existing Scheduler
    day2_advisory: DayAdvisory  # informational
    day3_advisory: DayAdvisory  # informational
    charge_adjustment: float  # multiplier on tonight's charge target (0.5-1.5)
    reasoning: str

@dataclass
class DayAdvisory:
    """Non-binding forecast for a future day."""
    date: date
    expected_solar_kwh: float
    expected_consumption_kwh: float
    energy_balance_kwh: float  # positive = surplus, negative = deficit
    recommendation: Literal["sunny_defer", "cloudy_charge_more", "neutral"]

class WeatherScheduler:
    """Wraps Scheduler with multi-day lookahead.

    Adjusts tonight's charge target based on weather outlook:
    - Sunny day-2 and day-3: reduce tonight's charge (solar will cover)
    - Cloudy day-2: charge more tonight (won't get solar tomorrow)
    - Mixed: use existing Scheduler logic unchanged
    """

    def __init__(
        self,
        scheduler: Scheduler,
        weather_client: WeatherClient,
        consumption_reader,
    ) -> None: ...

    async def compute_multi_day_plan(self, writer=None) -> MultiDayPlan: ...
```

**Integration with existing Scheduler:** The WeatherScheduler does NOT replace the Scheduler. It:
1. Calls `weather_client.get_forecast(days=3)` to get the multi-day outlook
2. Calls `scheduler.compute_schedule(writer)` to get the baseline 1-day schedule
3. Adjusts `charge_adjustment` multiplier based on day-2/day-3 forecast
4. Returns a `MultiDayPlan` where `tonight_schedule` is the adjusted binding schedule

**Adjustment rules:**
- Day-2 solar > 1.5x day-2 consumption: `charge_adjustment *= 0.7` (reduce charge, solar will cover)
- Day-2 solar < 0.5x day-2 consumption: `charge_adjustment *= 1.3` (charge more, cloudy ahead)
- Day-3 solar < 0.3x day-3 consumption: additional `*= 1.1` (extended cloudy stretch)
- Clamp `charge_adjustment` to [0.3, 1.5] to prevent extreme swings

## Patterns to Follow

### Pattern 1: Advisory Pattern (ExportAdvisor)

**What:** Components that make recommendations but do not execute actions. The coordinator retains full control over when and how to act.

**When:** Any new decision-making logic that feeds into the control loop.

**Why:** The coordinator is the single point of control. Adding export logic directly into `_run_cycle()` would make it a god method. The advisor pattern keeps the coordinator's structure (poll -> check conditions -> route) clean.

```python
# In coordinator._run_cycle(), after grid charge check:
if self._export_advisor is not None:
    export = self._export_advisor.advise(
        h_snap, v_snap, self._scheduler,
        consumption_kwh, solar_kwh, now,
    )
    if export.action == "EXPORT":
        h_cmd, v_cmd = self._compute_export_commands(
            export.export_power_w, h_snap, v_snap
        )
        await self._huawei_ctrl.execute(h_cmd)
        await self._victron_ctrl.execute(v_cmd)
        self._state = self._build_state(h_snap, v_snap, h_cmd, v_cmd)
        decision = self._check_and_log_decision(h_cmd, v_cmd, 0.0)
        await self._write_integrations(...)
        return
```

### Pattern 2: Wrapper/Enhancer (WeatherScheduler wraps Scheduler)

**What:** New component wraps an existing one to extend behavior without modifying the original.

**When:** Adding a new dimension (multi-day horizon) to an existing computation (nightly schedule).

**Why:** The existing Scheduler has 300+ lines of tested logic. Modifying it to handle multi-day horizons would touch every branch. Instead, WeatherScheduler calls it and adjusts the output.

```python
# In main.py lifespan:
scheduler = Scheduler(evcc_client, consumption_reader, tariff_engine, sys_config, orch_config)
weather_client = WeatherClient(lat, lon, peak_kwp)
weather_scheduler = WeatherScheduler(scheduler, weather_client, consumption_reader)

# In nightly loop:
plan = await weather_scheduler.compute_multi_day_plan(writer=writer)
scheduler.active_schedule = plan.tonight_schedule  # Coordinator reads this
```

### Pattern 3: Optional Dependency Injection (existing EMS pattern)

**What:** All new components follow the existing `set_X()` injection pattern on the Coordinator.

**When:** Any new integration that the system can function without.

```python
# ExportAdvisor is optional -- system works without it (no export, same as v1.0)
coordinator.set_export_advisor(export_advisor)  # or None if feed_in_rate not configured

# WeatherClient is optional -- scheduler falls back to EVCC solar only
weather_scheduler = WeatherScheduler(scheduler, weather_client=None, ...)
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Export in the Discharge Path

**What:** Adding export logic inside the existing discharge allocation (`_allocate()` or `_assign_discharge_roles()`).

**Why bad:** Discharge roles are about covering household load from batteries. Export is about pushing energy TO the grid -- it is a fundamentally different intent. Mixing them would corrupt the role semantics (what does PRIMARY_DISCHARGE mean when you are exporting?).

**Instead:** Add a new `EXPORTING` role to `BatteryRole` enum. Insert the export check as a separate code path in `_run_cycle()`, between grid-charge and P_target computation, just like how grid-charge is a separate path today.

### Anti-Pattern 2: Multi-Day Schedule as Binding Slots

**What:** Creating `ChargeSlot` entries for day-2 and day-3 that the coordinator tries to execute.

**Why bad:** Day-2/3 forecasts are unreliable. The scheduler runs nightly, so tomorrow night it will recompute with fresh data. Binding multi-day slots would create stale, conflicting schedules.

**Instead:** Day-2/3 are advisory only (`DayAdvisory`). They influence tonight's charge target but are never directly executed. Each night's run recomputes fresh.

### Anti-Pattern 3: External Weather API in the Control Loop

**What:** Calling Open-Meteo from the 5s coordinator cycle.

**Why bad:** Network latency (100-500ms) in a 5s loop. API rate limits. Unnecessary -- weather changes on hourly scale, not 5-second scale.

**Instead:** WeatherClient fetches once per scheduler run (nightly or every 6 hours). Results are cached on the WeatherScheduler. The ExportAdvisor uses the cached forecast.

## Integration Points with Existing Code

### 1. Coordinator Control Loop (`coordinator.py`)

**Where:** Between step 3 (grid charge check) and step 4 (P_target computation) in `_run_cycle()`.

**What changes:**
- New `self._export_advisor: ExportAdvisor | None = None` field
- New `set_export_advisor()` method
- New export check block (similar structure to grid charge block)
- New `BatteryRole.EXPORTING` value
- New `_compute_export_commands()` method

**Minimal diff area:** ~30-40 lines added to `_run_cycle()`, one new method.

### 2. Tariff Engine (`tariff.py` / `tariff_models.py`)

**What changes:**
- Add `feed_in_rate_eur_kwh: float` to `SystemConfig` (or a new `ExportConfig`)
- ExportAdvisor reads this to compare against current import rate

**Minimal change:** Single field addition. The feed-in rate is FIXED (not time-varying), so it does not need integration into `CompositeTariffEngine`'s slot schedule. Just a config value.

### 3. Scheduler (`scheduler.py`)

**What changes:**
- `compute_schedule()` now receives an optional `charge_adjustment: float` parameter from WeatherScheduler
- The SoC target calculation in step 4 applies the multiplier
- Alternatively: WeatherScheduler calls `compute_schedule()` unchanged, then scales `target_soc_pct` on the returned `ChargeSlot` objects

**Preferred approach:** WeatherScheduler post-processes the output (Pattern 2). Zero changes to `scheduler.py`.

### 4. Schedule Models (`schedule_models.py`)

**New types:**
- `ExportDecision` dataclass
- `DaySolarForecast` dataclass
- `MultiDaySolarForecast` dataclass
- `DayAdvisory` dataclass
- `MultiDayPlan` dataclass

**Modified:** None. Existing types remain unchanged.

### 5. Config (`config.py`)

**New fields on SystemConfig:**
- `feed_in_rate_eur_kwh: float = 0.0` (0 = export optimization disabled)
- `export_min_soc_pct: float = 30.0` (minimum pool SoC before allowing export)

**New dataclass:**
- `WeatherConfig` with `latitude`, `longitude`, `peak_kwp`, `efficiency_factor`

**Environment variables:**
- `FEED_IN_RATE_EUR_KWH` -- fixed feed-in tariff rate
- `EXPORT_MIN_SOC_PCT` -- minimum SoC for export (default 30)
- `WEATHER_LATITUDE`, `WEATHER_LONGITUDE` -- location for weather API
- `PV_PEAK_KWP` -- installed PV peak power in kWp
- `PV_EFFICIENCY` -- system efficiency factor (default 0.80)

### 6. API (`api.py`)

**New endpoints:**
- `GET /api/export/status` -- current ExportDecision (STORE/EXPORT, margin, reasoning)
- `GET /api/optimization/multi-day` -- MultiDayPlan with advisory forecasts

**Modified:**
- `GET /api/optimization/schedule` -- include `charge_adjustment` and multi-day context in response

### 7. Lifespan Wiring (`main.py`)

**New wiring in startup:**
```python
# Export advisor (optional -- only when feed_in_rate > 0)
if sys_config.feed_in_rate_eur_kwh > 0:
    export_advisor = ExportAdvisor(tariff_engine, sys_config, orch_config)
    coordinator.set_export_advisor(export_advisor)

# Weather client (optional -- only when lat/lon configured)
weather_config = WeatherConfig.from_env()
if weather_config.latitude != 0.0:
    weather_client = WeatherClient(
        weather_config.latitude, weather_config.longitude, weather_config.peak_kwp
    )
    weather_scheduler = WeatherScheduler(scheduler, weather_client, consumption_reader)
    app.state.weather_scheduler = weather_scheduler
```

## New File Structure

```
backend/
  export_advisor.py        # NEW -- ExportAdvisor + ExportDecision
  weather_client.py        # NEW -- Open-Meteo API client
  weather_scheduler.py     # NEW -- Multi-day scheduling wrapper
  export_models.py         # NEW -- ExportDecision, DayAdvisory, MultiDayPlan
  coordinator.py           # MODIFIED -- export check in control loop
  config.py                # MODIFIED -- feed_in_rate, WeatherConfig
  schedule_models.py       # MODIFIED -- MultiDaySolarForecast, DaySolarForecast
  api.py                   # MODIFIED -- new endpoints
  main.py                  # MODIFIED -- lifespan wiring
  tariff.py                # UNCHANGED
```

## Suggested Build Order

Based on dependency analysis:

### Phase 1: Export Foundation (config + advisor, no coordinator changes)

1. **Config changes** -- add `feed_in_rate_eur_kwh`, `export_min_soc_pct` to SystemConfig
2. **ExportAdvisor** -- implement decision logic, fully unit-testable without coordinator
3. **Export models** -- `ExportDecision` dataclass

Dependencies: None. Can be built and tested in isolation.

### Phase 2: Coordinator Integration (wire export into control loop)

4. **BatteryRole.EXPORTING** -- add to enum
5. **Coordinator export path** -- add check in `_run_cycle()`, `_compute_export_commands()`
6. **Decision logging** -- export decisions in ring buffer
7. **InfluxDB metrics** -- export power/revenue tracking

Dependencies: Phase 1 complete. Requires working coordinator for integration tests.

### Phase 3: Weather Client (independent of Phase 2)

8. **WeatherConfig** -- latitude, longitude, peak_kwp in config.py
9. **WeatherClient** -- Open-Meteo HTTP client with caching
10. **DaySolarForecast / MultiDaySolarForecast** -- data models

Dependencies: None. Can be built in parallel with Phase 2.

### Phase 4: Multi-Day Scheduling (requires Phase 3)

11. **WeatherScheduler** -- wraps Scheduler, applies charge_adjustment
12. **DayAdvisory / MultiDayPlan** -- advisory models
13. **Nightly loop integration** -- use WeatherScheduler instead of bare Scheduler

Dependencies: Phase 3 (WeatherClient) + existing Scheduler.

### Phase 5: API + Frontend

14. **API endpoints** -- `/api/export/status`, `/api/optimization/multi-day`
15. **Frontend** -- export badge on dashboard, multi-day forecast visualization

Dependencies: Phases 2 + 4.

**Phase ordering rationale:**
- Export advisor is independent of weather -- it works with real-time tariff data only
- Weather client is independent of export -- it provides data for scheduling
- Coordinator integration is the riskiest change (touching the control loop) so it comes after the advisor is well-tested
- Multi-day scheduling needs weather data, so it follows the weather client
- API/frontend comes last because the backend must be stable first

## Scalability Considerations

Not applicable -- this is a single-home EMS. The only scaling concern is API rate limits on Open-Meteo (free tier: 10,000 requests/day). With one request per scheduler run (nightly), this is a non-issue.

## Sources

- Existing codebase analysis: `backend/coordinator.py`, `backend/scheduler.py`, `backend/tariff.py`, `backend/config.py`, `backend/schedule_models.py`, `backend/controller_model.py`, `backend/evcc_client.py`, `backend/consumption_forecaster.py`
- Open-Meteo API documentation: https://open-meteo.com/en/docs (free weather API, no key required)
- German feed-in tariff (Einspeisevergutung) rates: fixed rate for systems > 10 kWp (LOW confidence -- verify with actual contract rate)
