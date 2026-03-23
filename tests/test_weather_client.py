"""Tests for backend.weather_client — OpenMeteoClient, cascade, and helpers."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.schedule_models import SolarForecast, SolarForecastMultiDay


# ---------------------------------------------------------------------------
# Test 1: OpenMeteoClient parses valid API JSON
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_meteo_client_parses_valid_response():
    """OpenMeteoClient.get_solar_forecast() parses valid API JSON into
    SolarForecastMultiDay with 72 hourly_wh values, 3 daily totals,
    and source='open_meteo'."""
    from backend.config import OpenMeteoConfig
    from backend.weather_client import OpenMeteoClient

    cfg = OpenMeteoConfig(
        latitude=48.14, longitude=11.58, dc_kwp=10.0, derating=0.80
    )
    client = OpenMeteoClient(cfg)

    # Build a mock API response with 72 irradiance values
    irradiance = [500.0] * 24 + [300.0] * 24 + [100.0] * 24  # 72 values
    times = [f"2026-03-23T{h:02d}:00" for h in range(24)] * 3
    mock_json = {
        "hourly": {
            "time": times,
            "global_tilted_irradiance": irradiance,
        }
    }

    mock_response = MagicMock()
    mock_response.json.return_value = mock_json
    mock_response.raise_for_status = MagicMock()

    with patch("backend.weather_client.httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_http

        result = await client.get_solar_forecast()

    assert result is not None
    assert isinstance(result, SolarForecastMultiDay)
    assert len(result.hourly_wh) == 72
    assert len(result.daily_energy_wh) == 3
    assert result.source == "open_meteo"
    # Check conversion: 500 W/m2 * (10 kWp / 1000) * 1000 * 0.80 = 4000 Wh
    assert result.hourly_wh[0] == pytest.approx(4000.0)


# ---------------------------------------------------------------------------
# Test 2: OpenMeteoClient returns None on HTTP error
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_meteo_client_returns_none_on_http_error():
    """OpenMeteoClient.get_solar_forecast() returns None on httpx.HTTPError."""
    from backend.config import OpenMeteoConfig
    from backend.weather_client import OpenMeteoClient

    cfg = OpenMeteoConfig(latitude=48.14, longitude=11.58)
    client = OpenMeteoClient(cfg)

    with patch("backend.weather_client.httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.get.side_effect = httpx.ConnectError("connection refused")
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_http

        result = await client.get_solar_forecast()

    assert result is None


# ---------------------------------------------------------------------------
# Test 3: OpenMeteoClient returns None on malformed JSON
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_meteo_client_returns_none_on_malformed_json():
    """OpenMeteoClient.get_solar_forecast() returns None on KeyError from
    malformed JSON."""
    from backend.config import OpenMeteoConfig
    from backend.weather_client import OpenMeteoClient

    cfg = OpenMeteoConfig(latitude=48.14, longitude=11.58)
    client = OpenMeteoClient(cfg)

    mock_response = MagicMock()
    mock_response.json.return_value = {"unexpected": "data"}
    mock_response.raise_for_status = MagicMock()

    with patch("backend.weather_client.httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_http

        result = await client.get_solar_forecast()

    assert result is None


# ---------------------------------------------------------------------------
# Test 4: _irradiance_to_wh conversion
# ---------------------------------------------------------------------------


def test_irradiance_to_wh_conversion():
    """_irradiance_to_wh converts [1000.0, 500.0] with dc_kwp=10,
    derating=0.80 to [8000.0, 4000.0]."""
    from backend.weather_client import _irradiance_to_wh

    result = _irradiance_to_wh([1000.0, 500.0], dc_kwp=10.0, derating=0.80)
    assert result == pytest.approx([8000.0, 4000.0])


# ---------------------------------------------------------------------------
# Test 5: Cascade returns EVCC data when available
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cascade_returns_evcc_when_available():
    """get_solar_forecast() cascade returns EVCC data (source='evcc') when
    EVCC has solar forecast."""
    from backend.weather_client import get_solar_forecast

    # Build a mock EVCC state with solar forecast
    solar = SolarForecast(
        timeseries_w=[1000.0] * 96,  # 96 x 15-min slots = 24h
        slot_timestamps_utc=[
            datetime(2026, 3, 23, tzinfo=timezone.utc)
        ] * 96,
        tomorrow_energy_wh=30000.0,
        day_after_energy_wh=25000.0,
    )

    evcc_state = MagicMock()
    evcc_state.solar = solar

    evcc_client = AsyncMock()
    evcc_client.get_state.return_value = evcc_state

    result = await get_solar_forecast(evcc_client, weather_client=None)

    assert result.source == "evcc"
    assert isinstance(result, SolarForecastMultiDay)


# ---------------------------------------------------------------------------
# Test 6: Cascade returns Open-Meteo when EVCC returns None
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cascade_returns_open_meteo_when_evcc_none():
    """get_solar_forecast() cascade returns Open-Meteo data
    (source='open_meteo') when EVCC returns None."""
    from backend.weather_client import get_solar_forecast

    evcc_client = AsyncMock()
    evcc_client.get_state.return_value = None

    # Mock weather client that returns a valid forecast
    weather = AsyncMock()
    weather.get_solar_forecast.return_value = SolarForecastMultiDay(
        hourly_wh=[100.0] * 72,
        daily_energy_wh=[2400.0, 2400.0, 2400.0],
        source="open_meteo",
        fetched_at=datetime.now(tz=timezone.utc),
    )

    result = await get_solar_forecast(evcc_client, weather_client=weather)

    assert result.source == "open_meteo"


# ---------------------------------------------------------------------------
# Test 7: Cascade returns seasonal fallback when both fail
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cascade_returns_seasonal_when_both_fail():
    """get_solar_forecast() cascade returns seasonal fallback
    (source='seasonal') when both EVCC and Open-Meteo return None."""
    from backend.weather_client import get_solar_forecast

    evcc_client = AsyncMock()
    evcc_client.get_state.return_value = None

    weather = AsyncMock()
    weather.get_solar_forecast.return_value = None

    result = await get_solar_forecast(evcc_client, weather_client=weather)

    assert result.source == "seasonal"
    assert isinstance(result, SolarForecastMultiDay)


# ---------------------------------------------------------------------------
# Test 8: Seasonal fallback shape (72 hourly values, daytime > 0, night = 0)
# ---------------------------------------------------------------------------


def test_seasonal_fallback_shape():
    """Seasonal fallback produces 72 hourly values with daytime > 0
    and nighttime = 0."""
    from backend.weather_client import _seasonal_solar_fallback

    result = _seasonal_solar_fallback()

    assert len(result.hourly_wh) == 72
    assert len(result.daily_energy_wh) == 3
    assert result.source == "seasonal"

    # Check that nighttime hours (0-5, 21-23 UTC) are 0
    for day in range(3):
        for hour in range(24):
            idx = day * 24 + hour
            if hour < 6 or hour >= 20:
                assert result.hourly_wh[idx] == 0.0, (
                    f"Expected 0 at hour {hour} (idx {idx})"
                )
            # Daytime hours (6-19 UTC) should have some > 0
    daytime_values = [
        result.hourly_wh[day * 24 + h]
        for day in range(3)
        for h in range(6, 20)
    ]
    assert any(v > 0 for v in daytime_values), "Daytime should have positive values"


# ---------------------------------------------------------------------------
# Test 9: OpenMeteoConfig.from_env() returns None when lat not set
# ---------------------------------------------------------------------------


def test_open_meteo_config_returns_none_without_lat():
    """OpenMeteoConfig.from_env() returns None when OPEN_METEO_LATITUDE
    is not set."""
    from backend.config import OpenMeteoConfig

    with patch.dict("os.environ", {}, clear=True):
        result = OpenMeteoConfig.from_env()

    assert result is None


# ---------------------------------------------------------------------------
# Test 10: OpenMeteoConfig.from_env() returns config when env vars set
# ---------------------------------------------------------------------------


def test_open_meteo_config_returns_config_when_env_set():
    """OpenMeteoConfig.from_env() returns config with correct values
    when env vars are set."""
    from backend.config import OpenMeteoConfig

    env = {
        "OPEN_METEO_LATITUDE": "48.14",
        "OPEN_METEO_LONGITUDE": "11.58",
        "OPEN_METEO_TILT": "35",
        "OPEN_METEO_AZIMUTH": "-10",
        "OPEN_METEO_DC_KWP": "12.5",
    }
    with patch.dict("os.environ", env, clear=True):
        result = OpenMeteoConfig.from_env()

    assert result is not None
    assert result.latitude == pytest.approx(48.14)
    assert result.longitude == pytest.approx(11.58)
    assert result.tilt == pytest.approx(35.0)
    assert result.azimuth == pytest.approx(-10.0)
    assert result.dc_kwp == pytest.approx(12.5)


# ---------------------------------------------------------------------------
# Test 11: _from_evcc converts 15-min timeseries to hourly
# ---------------------------------------------------------------------------


def test_from_evcc_converts_15min_to_hourly():
    """_from_evcc() converts SolarForecast timeseries_w (15-min slots)
    to SolarForecastMultiDay hourly_wh correctly."""
    from backend.weather_client import _from_evcc

    # 96 slots of 15 min = 24 hours, each at 1000 W
    # Hourly energy = 4 slots * 1000 W * 0.25 h = 1000 Wh
    solar = SolarForecast(
        timeseries_w=[1000.0] * 96,
        slot_timestamps_utc=[
            datetime(2026, 3, 23, tzinfo=timezone.utc)
        ] * 96,
        tomorrow_energy_wh=30000.0,
        day_after_energy_wh=25000.0,
    )

    result = _from_evcc(solar)

    assert result.source == "evcc"
    # 96 slots / 4 = 24 hours, but result should have 72 (padded or from daily)
    assert len(result.hourly_wh) >= 24
    # Check first hour: sum of 4 slots * 1000W * 0.25h = 1000 Wh
    assert result.hourly_wh[0] == pytest.approx(1000.0)
    assert result.daily_energy_wh[1] == pytest.approx(30000.0)
    assert result.daily_energy_wh[2] == pytest.approx(25000.0)
