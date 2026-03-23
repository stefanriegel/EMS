# Phase 9: Weather & Forecast Data - Research

**Researched:** 2026-03-23
**Domain:** Solar forecasting, consumption prediction, weather API integration
**Confidence:** HIGH

## Summary

Phase 9 adds multi-day solar production forecasts and extends consumption predictions to a 72-hour horizon. The existing codebase already has strong foundations: EVCC's `SolarForecast` dataclass provides `tomorrow_energy_wh` and `day_after_energy_wh`, the `ConsumptionForecaster` has hourly ML predictions (currently 24h), and `httpx` is already a project dependency for async HTTP.

The primary work is: (1) build an Open-Meteo weather client as fallback solar forecaster when EVCC is unavailable, (2) extend `ConsumptionForecaster.query_consumption_history()` to produce hourly predictions for 72 hours instead of a single 24h scalar, and (3) wire both into the scheduler/lifespan with graceful degradation (seasonal averages as last resort).

**Primary recommendation:** Use raw httpx calls to Open-Meteo's forecast API (no third-party library) for solar irradiance, converting W/m2 to estimated Wh using panel specs from config. Extend `ConsumptionForecaster` to return a new `HourlyConsumptionForecast` dataclass with 72 hourly values.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
None -- all implementation choices are at Claude's discretion per CONTEXT.md.

### Claude's Discretion
All implementation choices are at Claude's discretion -- infrastructure phase with clear success criteria from ROADMAP. Key considerations:
- EVCC already parsed in backend/evcc_client.py (has tomorrow_energy_wh and day_after_energy_wh)
- Open-Meteo API is free/keyless -- use open-meteo-solar-forecast library or raw httpx
- ConsumptionForecaster already has hourly resolution internally -- extend to 72h
- Graceful degradation: seasonal averages as last-resort fallback
- All new data sources must be optional (fire-and-forget pattern)

### Deferred Ideas (OUT OF SCOPE)
None
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MDS-01 | Scheduler looks 2-3 days ahead using EVCC solar forecast data and Open-Meteo as fallback when EVCC is unavailable | EVCC already provides `SolarForecast` with `tomorrow_energy_wh` and `day_after_energy_wh`. Open-Meteo forecast API returns 72h hourly irradiance (W/m2) at `api.open-meteo.com/v1/forecast` with `global_tilted_irradiance` parameter. New `OpenMeteoClient` wraps this with httpx. |
| MDS-06 | ConsumptionForecaster extended to predict hourly demand for a 72-hour horizon | Current `query_consumption_history()` returns a scalar `today_expected_kwh`. The internal ML models already predict per-hour. Extension loops 72 hours instead of 24, returns a new `HourlyConsumptionForecast` dataclass. |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Graceful degradation mandatory**: Every external dependency must be optional -- Open-Meteo client must handle unreachability gracefully
- **Python 3.12+, async-first**: All I/O via httpx async client
- **Dataclass configs with `from_env()`**: New `OpenMeteoConfig` follows this pattern
- **Fire-and-forget on errors**: Exceptions logged as WARNING, never propagated to caller
- **Module-level logger**: `logger = logging.getLogger("ems.weather")`
- **No new required dependencies**: httpx already in pyproject.toml
- **Test files**: `test_*.py` in `tests/` directory

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| httpx | (already installed) | Async HTTP to Open-Meteo API | Already used by EvccClient, no new dep |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Raw httpx | `open-meteo-solar-forecast` 0.1.29 | Library adds a dependency for a simple GET request; only supports today/tomorrow (not day 3); httpx is already available and the API is trivial |

**Recommendation:** Use raw httpx. The Open-Meteo forecast API is a single GET returning JSON with parallel arrays. Adding a library for this is unnecessary complexity. The `open-meteo-solar-forecast` package (0.1.29) has limited multi-day support (today/tomorrow only, no day 3), while the raw API supports `forecast_days=3` returning 72 hourly values.

## Architecture Patterns

### Recommended Project Structure
```
backend/
  weather_client.py      # OpenMeteoClient -- async solar forecast from Open-Meteo
  consumption_forecaster.py  # Extended with predict_hourly_72h()
  schedule_models.py     # New HourlyConsumptionForecast + SolarForecastMultiDay dataclasses
  config.py              # New OpenMeteoConfig dataclass
  scheduler.py           # Updated to use multi-day data (Phase 10 scope, not this phase)
  main.py                # Lifespan wires OpenMeteoClient
tests/
  test_weather_client.py     # Unit tests for Open-Meteo client
  test_consumption_forecaster.py  # Extended for 72h predictions
```

### Pattern 1: Cascading Forecast Provider
**What:** Solar forecast tries EVCC first, falls back to Open-Meteo, then to seasonal averages.
**When to use:** Whenever the scheduler needs solar forecast data.
**Example:**
```python
@dataclass
class SolarForecastResult:
    """Multi-day solar forecast with source attribution."""
    hourly_wh: list[float]           # 72 hourly values
    daily_energy_wh: list[float]     # [day0, day1, day2]
    source: str                       # "evcc", "open_meteo", "seasonal_fallback"
    fetched_at: datetime

async def get_solar_forecast(
    evcc_client: EvccClient,
    weather_client: OpenMeteoClient | None,
) -> SolarForecastResult:
    # Try EVCC first
    evcc_state = await evcc_client.get_state()
    if evcc_state is not None and evcc_state.solar is not None:
        return _from_evcc(evcc_state.solar)

    # Fallback to Open-Meteo
    if weather_client is not None:
        result = await weather_client.get_solar_forecast()
        if result is not None:
            return result

    # Last resort: seasonal averages
    return _seasonal_solar_fallback()
```

### Pattern 2: OpenMeteoClient (fire-and-forget)
**What:** Async HTTP client matching EvccClient's pattern -- returns `None` on any failure.
**When to use:** As fallback solar data source.
**Example:**
```python
class OpenMeteoClient:
    def __init__(self, config: OpenMeteoConfig) -> None:
        self._config = config
        self._base_url = "https://api.open-meteo.com/v1/forecast"

    async def get_solar_forecast(self) -> SolarForecastResult | None:
        params = {
            "latitude": self._config.latitude,
            "longitude": self._config.longitude,
            "hourly": "global_tilted_irradiance",
            "tilt": self._config.tilt,
            "azimuth": self._config.azimuth,
            "forecast_days": 3,
            "timezone": "UTC",
        }
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_s) as client:
                resp = await client.get(self._base_url, params=params)
                resp.raise_for_status()
                return _parse_solar_response(resp.json(), self._config.dc_kwp)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning("open-meteo get_solar_forecast failed: %s", exc)
            return None
```

### Pattern 3: Extended ConsumptionForecaster
**What:** New method `predict_hourly(horizon_hours=72)` that returns per-hour consumption predictions.
**When to use:** Scheduler needs hourly demand for multi-day planning.
**Example:**
```python
async def predict_hourly(self, horizon_hours: int = 72) -> HourlyConsumptionForecast:
    """Predict hourly consumption for the next N hours."""
    if self._heat_pump_model is None:
        return _seasonal_hourly_fallback(horizon_hours)

    now_utc = datetime.now(tz=timezone.utc)
    hourly_kwh: list[float] = []
    for h in range(horizon_hours):
        ts = now_utc + timedelta(hours=h)
        features = [[neutral_temp, ewm, float(ts.weekday()),
                      float(ts.hour), float(ts.month)]]
        total_w = max(0.0, float(self._heat_pump_model.predict(features)[0]))
        if self._dhw_model is not None:
            total_w += max(0.0, float(self._dhw_model.predict(features)[0]))
        if self._base_model is not None:
            total_w += max(0.0, float(self._base_model.predict(features)[0]))
        else:
            total_w += _BASE_LOAD_W
        hourly_kwh.append(total_w / 1000.0)

    return HourlyConsumptionForecast(
        hourly_kwh=hourly_kwh,
        total_kwh=sum(hourly_kwh),
        horizon_hours=horizon_hours,
        source="ml" if not fallback else "seasonal",
        fallback_used=False,
    )
```

### Anti-Patterns to Avoid
- **Caching solar forecasts across days:** Products change daily. Always fetch fresh on each scheduler run.
- **Coupling Open-Meteo response structure to scheduler:** Parse into intermediate dataclass, don't pass raw JSON.
- **Making Open-Meteo config required:** Must be optional -- system works without it via EVCC or seasonal fallback.
- **Blocking on weather API in the control loop:** Weather fetch runs in the nightly scheduler, not in the 5-second control cycle.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Solar irradiance to Wh conversion | Complex solar geometry model | Simple linear: `irradiance_w_m2 * panel_area_m2 * efficiency` or `irradiance_w_m2 / 1000 * dc_kwp * derating` | For scheduling decisions, approximate is fine; EVCC already does precise modeling when available |
| Timezone handling | Manual UTC offset math | `datetime.timezone.utc` + API parameter `timezone=UTC` | Open-Meteo returns UTC timestamps when requested; all internal timestamps are UTC |
| HTTP retry logic | Custom retry wrapper | Single attempt with timeout, return None on failure | Fire-and-forget pattern; scheduler retries next night anyway |

**Key insight:** The solar forecast doesn't need to be precise -- it's an input to a heuristic charge scheduler that already handles uncertainty through conservative targets. A rough irradiance-to-Wh conversion is sufficient.

## Common Pitfalls

### Pitfall 1: Open-Meteo Global Tilted Irradiance Units
**What goes wrong:** Confusing W/m2 (irradiance) with Wh (energy). The API returns instantaneous power density per hour slot.
**Why it happens:** The hourly values are averages over the preceding hour, so each value in W/m2 represents 1 Wh/m2 of energy for that hour.
**How to avoid:** Multiply by dc_kwp (kWp) and a derating factor (0.75-0.85) to get estimated panel output in Wh: `hourly_wh = irradiance_w_m2 / 1000 * dc_kwp * 1000 * derating_factor`
**Warning signs:** Daily energy totals that are 10x too high or too low compared to EVCC forecasts.

### Pitfall 2: ConsumptionForecaster Cold-Start on 72h
**What goes wrong:** Extending to 72h with untrained models returns 72 zeros or nonsensical values.
**Why it happens:** The cold-start guard only checks `_heat_pump_model is None` -- extending the horizon doesn't change this, but the seasonal fallback must also produce 72 hourly values.
**How to avoid:** The seasonal fallback for hourly prediction should use hour-of-day patterns (higher during day, lower at night), not just divide daily total by 24.
**Warning signs:** Flat hourly predictions regardless of time of day.

### Pitfall 3: EVCC Solar Timeseries Horizon Mismatch
**What goes wrong:** Assuming EVCC timeseries always covers 72h. The EVCC solar forecast timeseries may only extend ~48h depending on the forecast provider configuration.
**Why it happens:** `day_after_energy_wh` exists as a scalar but the timeseries slots may not reach day 3.
**How to avoid:** Check `len(solar.slot_timestamps_utc)` and fall back to Open-Meteo for day 3 if EVCC timeseries is shorter.
**Warning signs:** IndexError when accessing slots beyond the EVCC horizon.

### Pitfall 4: Panel Configuration Not Available
**What goes wrong:** Open-Meteo returns irradiance (W/m2) but the system needs Wh output. Without panel specs (kWp, tilt, azimuth), the conversion is meaningless.
**Why it happens:** The config may not have panel specs because EVCC handles this normally.
**How to avoid:** Make `OpenMeteoConfig` optional. If latitude/longitude are set but panel specs are missing, use sensible defaults (e.g., 10 kWp, 30 deg tilt, 0 deg azimuth for south-facing). Log a warning.
**Warning signs:** Open-Meteo fallback producing wildly different values than EVCC.

## Code Examples

### Open-Meteo API Call
```python
# Source: Verified against live API response (2026-03-23)
# GET https://api.open-meteo.com/v1/forecast
#   ?latitude=48.14&longitude=11.58
#   &hourly=global_tilted_irradiance
#   &tilt=30&azimuth=0
#   &forecast_days=3&timezone=UTC
#
# Response structure:
# {
#   "hourly": {
#     "time": ["2026-03-23T00:00", ...],  // 72 entries
#     "global_tilted_irradiance": [0.0, ...]  // W/m2, 72 entries
#   }
# }
```

### Irradiance to Panel Output Conversion
```python
def _irradiance_to_wh(
    irradiance_w_m2: list[float],
    dc_kwp: float,
    derating: float = 0.80,
) -> list[float]:
    """Convert hourly irradiance (W/m2) to estimated panel output (Wh).

    Each hourly irradiance value represents the average over the preceding
    hour.  At Standard Test Conditions (STC), 1000 W/m2 produces dc_kwp kW.

    Parameters
    ----------
    irradiance_w_m2:
        Hourly global tilted irradiance values from Open-Meteo.
    dc_kwp:
        PV system rated capacity in kWp.
    derating:
        System derating factor (inverter losses, wiring, soiling).
        Default 0.80 is conservative for rooftop systems.
    """
    return [
        (gti / 1000.0) * dc_kwp * 1000.0 * derating
        for gti in irradiance_w_m2
    ]
```

### New Dataclasses
```python
@dataclass
class SolarForecastMultiDay:
    """Multi-day solar forecast with hourly resolution."""
    hourly_wh: list[float]              # 72 hourly values (Wh)
    daily_energy_wh: list[float]        # [today, tomorrow, day_after] (Wh)
    source: str                          # "evcc", "open_meteo", "seasonal"
    fetched_at: datetime

@dataclass
class HourlyConsumptionForecast:
    """Hourly consumption prediction for multi-day horizon."""
    hourly_kwh: list[float]             # Per-hour predictions (kWh)
    total_kwh: float                     # Sum of hourly values
    horizon_hours: int                   # Number of hours predicted
    source: str                          # "ml" or "seasonal"
    fallback_used: bool
```

### OpenMeteoConfig Dataclass
```python
@dataclass
class OpenMeteoConfig:
    """Configuration for the Open-Meteo solar forecast fallback."""
    latitude: float = 0.0
    longitude: float = 0.0
    tilt: float = 30.0          # Panel tilt in degrees
    azimuth: float = 0.0        # 0=south, -90=east, 90=west
    dc_kwp: float = 10.0        # PV system capacity in kWp
    derating: float = 0.80      # System derating factor
    timeout_s: float = 10.0

    @classmethod
    def from_env(cls) -> "OpenMeteoConfig | None":
        lat = os.environ.get("OPEN_METEO_LATITUDE", "")
        lon = os.environ.get("OPEN_METEO_LONGITUDE", "")
        if not lat or not lon:
            return None
        return cls(
            latitude=float(lat),
            longitude=float(lon),
            tilt=float(os.environ.get("OPEN_METEO_TILT", "30")),
            azimuth=float(os.environ.get("OPEN_METEO_AZIMUTH", "0")),
            dc_kwp=float(os.environ.get("OPEN_METEO_DC_KWP", "10")),
        )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| EVCC only for solar forecast | EVCC + Open-Meteo fallback | This phase | System works without EVCC |
| 24h scalar consumption prediction | 72h hourly consumption prediction | This phase | Enables multi-day scheduling (Phase 10) |
| No weather API integration | Open-Meteo free/keyless API | This phase | Zero-config fallback |

## Open Questions

1. **EVCC timeseries actual horizon**
   - What we know: EVCC provides `day_after_energy_wh` as a scalar, timeseries slots are 15-minute resolution
   - What's unclear: Whether the EVCC timeseries consistently extends to 72h or stops at 48h (blocker noted in STATE.md)
   - Recommendation: Code defensively -- check timeseries length, fill gaps from Open-Meteo

2. **Panel configuration source**
   - What we know: The user's PV system specs (kWp, tilt, azimuth) are needed for irradiance-to-Wh conversion
   - What's unclear: Whether these can be auto-detected from EVCC or must be manually configured
   - Recommendation: Add to `OpenMeteoConfig` with sensible defaults; document in setup wizard for Phase 6

3. **Outdoor temperature for 72h consumption forecast**
   - What we know: Current forecaster uses a neutral 10C placeholder because no temp forecast exists
   - What's unclear: Whether adding Open-Meteo temperature forecast would meaningfully improve accuracy
   - Recommendation: Phase 9 scope should add Open-Meteo temperature as a bonus (the API already returns it with `hourly=temperature_2m`); improves heat pump prediction significantly

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| httpx | Open-Meteo client | Yes | (in pyproject.toml) | -- |
| Open-Meteo API | Solar forecast fallback | Yes (free/keyless) | v1 | Seasonal averages |
| scikit-learn | ConsumptionForecaster ML | Yes | >=1.4,<2 (in pyproject.toml) | Seasonal fallback |

**Missing dependencies with no fallback:** None

**Missing dependencies with fallback:** None -- all dependencies are already available or have built-in fallbacks.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-anyio |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `python -m pytest tests/test_weather_client.py tests/test_consumption_forecaster.py -x` |
| Full suite command | `python -m pytest tests/ -x` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MDS-01 | EVCC solar forecast used when available | unit | `python -m pytest tests/test_scheduler.py -x -k solar` | Exists (extend) |
| MDS-01 | Open-Meteo fallback when EVCC unavailable | unit | `python -m pytest tests/test_weather_client.py -x` | Wave 0 |
| MDS-01 | Seasonal fallback when both EVCC and Open-Meteo unavailable | unit | `python -m pytest tests/test_weather_client.py -x -k fallback` | Wave 0 |
| MDS-06 | ConsumptionForecaster produces 72h hourly predictions | unit | `python -m pytest tests/test_consumption_forecaster.py -x -k hourly` | Wave 0 |
| MDS-06 | ConsumptionForecaster cold-start returns seasonal hourly fallback | unit | `python -m pytest tests/test_consumption_forecaster.py -x -k cold_start` | Exists (extend) |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_weather_client.py tests/test_consumption_forecaster.py -x`
- **Per wave merge:** `python -m pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_weather_client.py` -- covers MDS-01 (Open-Meteo client, cascade, seasonal fallback)
- [ ] Extend `tests/test_consumption_forecaster.py` -- covers MDS-06 (72h hourly predictions)

## Sources

### Primary (HIGH confidence)
- Open-Meteo forecast API -- verified live response structure with `global_tilted_irradiance`, `tilt`, `azimuth` parameters, 72h horizon (3 forecast_days)
- Existing codebase: `backend/evcc_client.py`, `backend/consumption_forecaster.py`, `backend/scheduler.py`, `backend/schedule_models.py` -- full source review

### Secondary (MEDIUM confidence)
- [open-meteo-solar-forecast PyPI](https://pypi.org/project/open-meteo-solar-forecast/) -- v0.1.29, limited to today/tomorrow, evaluated and rejected
- [Open-Meteo Weather Forecast API docs](https://open-meteo.com/en/docs) -- up to 16 days forecast, solar radiation variables

### Tertiary (LOW confidence)
- Derating factor 0.80 for rooftop PV -- industry rule of thumb, actual value depends on specific installation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, httpx already in use
- Architecture: HIGH -- follows existing EvccClient and ConsumptionForecaster patterns exactly
- Pitfalls: MEDIUM -- EVCC timeseries horizon and panel config are real unknowns, mitigated by defensive coding

**Research date:** 2026-03-23
**Valid until:** 2026-04-23 (stable APIs, no breaking changes expected)
