"""Weather client — Open-Meteo solar forecast and cascading provider.

Provides :class:`OpenMeteoClient` for fetching solar irradiance forecasts
from the free Open-Meteo API, plus a cascading :func:`get_solar_forecast`
function that tries EVCC first, Open-Meteo second, and seasonal averages
as a last resort.

Observability
-------------
- ``WARNING "open-meteo get_solar_forecast failed: <exc>"`` — emitted on
  any HTTP or parse error from the Open-Meteo API.
- ``INFO "solar forecast: source=<src>"`` — emitted by the cascade to
  indicate which provider was used.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import httpx

from backend.config import OpenMeteoConfig
from backend.schedule_models import SolarForecast, SolarForecastMultiDay

logger = logging.getLogger("ems.weather")


# ---------------------------------------------------------------------------
# Irradiance conversion helper
# ---------------------------------------------------------------------------


def _irradiance_to_wh(
    irradiance_w_m2: list[float],
    dc_kwp: float,
    derating: float = 0.80,
) -> list[float]:
    """Convert hourly irradiance (W/m2) to estimated panel output (Wh).

    Each hourly irradiance value represents the average over the preceding
    hour.  At Standard Test Conditions (STC), 1000 W/m2 produces *dc_kwp* kW.

    Parameters
    ----------
    irradiance_w_m2:
        Hourly global tilted irradiance values from Open-Meteo.
    dc_kwp:
        PV system rated capacity in kWp.
    derating:
        System derating factor (inverter losses, wiring, soiling).
        Default 0.80 is conservative for rooftop systems.

    Returns
    -------
    list[float]
        Hourly energy output in Wh.
    """
    return [
        (gti / 1000.0) * dc_kwp * 1000.0 * derating
        for gti in irradiance_w_m2
    ]


# ---------------------------------------------------------------------------
# OpenMeteoClient
# ---------------------------------------------------------------------------


class OpenMeteoClient:
    """Async HTTP client for the Open-Meteo solar forecast API.

    Fetches global tilted irradiance for 3 days (72 hourly values) and
    converts to estimated PV output in Wh using the configured panel
    parameters.

    Parameters
    ----------
    config:
        :class:`~backend.config.OpenMeteoConfig` with site coordinates
        and panel specifications.
    """

    def __init__(self, config: OpenMeteoConfig) -> None:
        self._config = config
        self._base_url = "https://api.open-meteo.com/v1/forecast"

    async def get_solar_forecast(self) -> SolarForecastMultiDay | None:
        """Fetch 72-hour solar forecast from Open-Meteo.

        Returns
        -------
        SolarForecastMultiDay | None
            Parsed forecast on success; ``None`` on any HTTP or parse
            error (the error is logged as a WARNING).
        """
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
                data = resp.json()

            irradiance = data["hourly"]["global_tilted_irradiance"]
            hourly_wh = _irradiance_to_wh(
                irradiance, self._config.dc_kwp, self._config.derating
            )

            # Compute daily energy by summing 24h chunks
            daily_energy_wh: list[float] = []
            for day in range(3):
                start = day * 24
                end = start + 24
                daily_energy_wh.append(sum(hourly_wh[start:end]))

            return SolarForecastMultiDay(
                hourly_wh=hourly_wh,
                daily_energy_wh=daily_energy_wh,
                source="open_meteo",
                fetched_at=datetime.now(tz=timezone.utc),
            )
        except (httpx.HTTPError, KeyError, ValueError, TypeError, IndexError) as exc:
            logger.warning("open-meteo get_solar_forecast failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# EVCC solar forecast converter
# ---------------------------------------------------------------------------


def _from_evcc(solar: SolarForecast) -> SolarForecastMultiDay:
    """Convert EVCC 15-min timeseries to SolarForecastMultiDay hourly format.

    EVCC provides power in watts at 15-minute resolution.  Each hour is
    the sum of 4 consecutive 15-min slots multiplied by 0.25 h to get Wh.

    Parameters
    ----------
    solar:
        :class:`~backend.schedule_models.SolarForecast` from EVCC.

    Returns
    -------
    SolarForecastMultiDay
        Hourly forecast with source ``"evcc"``.
    """
    ts = solar.timeseries_w
    n_slots = len(ts)
    n_hours = n_slots // 4

    hourly_wh: list[float] = []
    for h in range(n_hours):
        start = h * 4
        # Sum of 4 x 15-min power values, each contributing 0.25 h
        wh = sum(ts[start : start + 4]) * 0.25
        hourly_wh.append(wh)

    # Pad to 72 hours if shorter (EVCC may only provide ~48h)
    while len(hourly_wh) < 72:
        hourly_wh.append(0.0)

    # Build daily totals: today from hourly, tomorrow/day_after from EVCC scalars
    today_wh = sum(hourly_wh[:24])
    daily_energy_wh = [
        today_wh,
        solar.tomorrow_energy_wh,
        solar.day_after_energy_wh,
    ]

    return SolarForecastMultiDay(
        hourly_wh=hourly_wh[:72],
        daily_energy_wh=daily_energy_wh,
        source="evcc",
        fetched_at=datetime.now(tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Seasonal solar fallback
# ---------------------------------------------------------------------------


def _seasonal_solar_fallback() -> SolarForecastMultiDay:
    """Generate a seasonal solar fallback forecast.

    Uses month-based seasonal averages distributed across daytime hours
    (6-19 UTC) with a Gaussian-like shape.  Nighttime hours are 0.

    Summer (~40 kWh/day), winter (~10 kWh/day), shoulder (~25 kWh/day).

    Returns
    -------
    SolarForecastMultiDay
        72-hour forecast with source ``"seasonal"``.
    """
    now = datetime.now(tz=timezone.utc)
    month = now.month

    # Seasonal daily energy in Wh
    if month in (6, 7, 8):
        daily_wh = 40000.0  # summer
    elif month in (12, 1, 2):
        daily_wh = 10000.0  # winter
    else:
        daily_wh = 25000.0  # shoulder

    # Gaussian-like distribution across daytime hours (6-19 UTC)
    # Peak at solar noon (13 UTC ~ 1 PM, reasonable for central Europe)
    daytime_hours = list(range(6, 20))  # 14 hours
    center = 13.0
    sigma = 3.0
    raw_weights = [
        math.exp(-0.5 * ((h - center) / sigma) ** 2) for h in daytime_hours
    ]
    total_weight = sum(raw_weights)
    weights = [w / total_weight for w in raw_weights]

    # Build one day of hourly values
    day_hourly: list[float] = []
    for hour in range(24):
        if hour in daytime_hours:
            idx = daytime_hours.index(hour)
            day_hourly.append(daily_wh * weights[idx])
        else:
            day_hourly.append(0.0)

    # Repeat for 3 days
    hourly_wh = day_hourly * 3
    daily_energy_wh = [daily_wh, daily_wh, daily_wh]

    return SolarForecastMultiDay(
        hourly_wh=hourly_wh,
        daily_energy_wh=daily_energy_wh,
        source="seasonal",
        fetched_at=now,
    )


# ---------------------------------------------------------------------------
# Cascading solar forecast provider
# ---------------------------------------------------------------------------


async def get_solar_forecast(
    evcc_client,
    weather_client: OpenMeteoClient | None,
) -> SolarForecastMultiDay:
    """Cascading solar forecast: EVCC -> Open-Meteo -> seasonal fallback.

    Parameters
    ----------
    evcc_client:
        :class:`~backend.evcc_client.EvccClient` instance.
    weather_client:
        Optional :class:`OpenMeteoClient` instance (``None`` when
        Open-Meteo is not configured).

    Returns
    -------
    SolarForecastMultiDay
        Always returns a valid forecast; never returns ``None``.
    """
    # 1. Try EVCC
    try:
        evcc_state = await evcc_client.get_state()
        if evcc_state is not None and evcc_state.solar is not None:
            result = _from_evcc(evcc_state.solar)
            logger.info("solar forecast: source=evcc")
            return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("solar forecast: EVCC failed: %s", exc)

    # 2. Try Open-Meteo
    if weather_client is not None:
        try:
            result = await weather_client.get_solar_forecast()
            if result is not None:
                logger.info("solar forecast: source=open_meteo")
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("solar forecast: Open-Meteo failed: %s", exc)

    # 3. Seasonal fallback
    logger.info("solar forecast: source=seasonal (both EVCC and Open-Meteo unavailable)")
    return _seasonal_solar_fallback()
