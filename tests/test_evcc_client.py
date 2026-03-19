"""Unit tests for EvccClient and EvoptResult.

Covers:
  - _parse_state: full fixture round-trip, individual section parsing
  - EvccClient.get_state(): success, HTTP 500, connection error, missing keys
  - EvoptResult.get_huawei_target_soc_pct(): name-match summing, exact math,
    clamping, partial pack, no packs
  - EvoptResult.get_victron_target_soc_pct(): name-match, fallback, clamping
  - SolarForecast: tomorrow_energy_wh, timeseries length
  - GridPriceSeries: import prices length and value
  - Slot timestamp derivation (not index-from-midnight)

K007: anyio_mode = "auto" auto-collects async def test_* without explicit
      @pytest.mark.anyio.  All async tests here rely on that.
K002: asyncio_mode = "auto" is for pytest-asyncio only; ignored by pytest-anyio.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.config import EvccConfig
from backend.evcc_client import EvccClient, _parse_state
from backend.schedule_models import (
    EvccState,
    EvoptBatteryTimeseries,
    EvoptResult,
    GridPriceSeries,
    SolarForecast,
)

# ---------------------------------------------------------------------------
# Module-level fixture — mirrors the real EVCC /api/state JSON shape.
# Timestamps start at 2026-01-15T23:00:00+00:00 (intentionally not midnight).
# ---------------------------------------------------------------------------

_T0 = "2026-01-15T23:00:00+00:00"
_T0_DT = datetime(2026, 1, 15, 23, 0, 0, tzinfo=timezone.utc)
_TS_8 = [
    f"2026-01-15T{23}:{i * 15:02d}:00+00:00" if i < 4
    else f"2026-01-16T00:{(i - 4) * 15:02d}:00+00:00"
    for i in range(8)
]

# Build a clean 8-slot timestamp list explicitly to avoid generator confusion
_TS_8 = [
    "2026-01-15T23:00:00+00:00",
    "2026-01-15T23:15:00+00:00",
    "2026-01-15T23:30:00+00:00",
    "2026-01-15T23:45:00+00:00",
    "2026-01-16T00:00:00+00:00",
    "2026-01-16T00:15:00+00:00",
    "2026-01-16T00:30:00+00:00",
    "2026-01-16T00:45:00+00:00",
]

_SOLAR_TS_4 = [
    {"start": "2026-01-16T06:00:00+00:00", "end": "2026-01-16T06:15:00+00:00", "value": 1000.0},
    {"start": "2026-01-16T06:15:00+00:00", "end": "2026-01-16T06:30:00+00:00", "value": 1200.0},
    {"start": "2026-01-16T06:30:00+00:00", "end": "2026-01-16T06:45:00+00:00", "value": 1500.0},
    {"start": "2026-01-16T06:45:00+00:00", "end": "2026-01-16T07:00:00+00:00", "value": 1300.0},
]

_GRID_TS_4 = [
    {"start": "2026-01-16T00:00:00+00:00", "end": "2026-01-16T00:15:00+00:00", "value": 0.18},
    {"start": "2026-01-16T00:15:00+00:00", "end": "2026-01-16T00:30:00+00:00", "value": 0.20},
    {"start": "2026-01-16T00:30:00+00:00", "end": "2026-01-16T00:45:00+00:00", "value": 0.19},
    {"start": "2026-01-16T00:45:00+00:00", "end": "2026-01-16T01:00:00+00:00", "value": 0.17},
]

_FEEDIN_TS_4 = [
    {"start": "2026-01-16T00:00:00+00:00", "end": "2026-01-16T00:15:00+00:00", "value": 0.08},
    {"start": "2026-01-16T00:15:00+00:00", "end": "2026-01-16T00:30:00+00:00", "value": 0.08},
    {"start": "2026-01-16T00:30:00+00:00", "end": "2026-01-16T00:45:00+00:00", "value": 0.08},
    {"start": "2026-01-16T00:45:00+00:00", "end": "2026-01-16T01:00:00+00:00", "value": 0.08},
]

EVCC_STATE_FIXTURE: dict = {
    "evopt": {
        "res": {
            "status": "Optimal",
            "objective_value": 42.5,
            "batteries": [
                {
                    "title": "Emma Akku 1",
                    "charging_power":    [3000.0, 2000.0, 1000.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "discharging_power": [0.0,    0.0,    0.0,    0.0, 0.0, 0.0, 0.0, 0.0],
                    "state_of_charge":   [0.50,   0.55,   0.58,   0.60, 0.60, 0.60, 0.60, 0.60],
                },
                {
                    "title": "Emma Akku 2",
                    "charging_power":    [2000.0, 1500.0, 500.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "discharging_power": [0.0,    0.0,    0.0,   0.0, 0.0, 0.0, 0.0, 0.0],
                    "state_of_charge":   [0.45,   0.50,   0.53,  0.55, 0.55, 0.55, 0.55, 0.55],
                },
                {
                    "title": "Victron",
                    "charging_power":    [1000.0, 800.0, 600.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "discharging_power": [0.0,    0.0,   0.0,   0.0, 0.0, 0.0, 0.0, 0.0],
                    "state_of_charge":   [0.40,   0.44,  0.47,  0.50, 0.50, 0.50, 0.50, 0.50],
                },
            ],
            "details": {
                "timestamp": _TS_8,
            },
        },
    },
    "forecast": {
        "solar": {
            "tomorrow": {"energy": 15000},
            "dayAfterTomorrow": {"energy": 20000},
            "timeseries": _SOLAR_TS_4,
        },
        "grid": _GRID_TS_4,
        "feedin": _FEEDIN_TS_4,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> EvccConfig:
    return EvccConfig(host="localhost", port=7070, timeout_s=5.0)


def _make_http_response_mock(fixture: dict, status_code: int = 200) -> MagicMock:
    """Build a MagicMock that looks like an httpx.Response returning fixture."""
    resp = MagicMock()
    resp.json.return_value = fixture
    if status_code == 200:
        resp.raise_for_status.return_value = None
    else:
        req = httpx.Request("GET", "http://localhost:7070/api/state")
        real_resp = httpx.Response(status_code, request=req)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code}", request=req, response=real_resp
        )
    return resp


def _make_evopt_result(
    batteries: list[EvoptBatteryTimeseries],
) -> EvoptResult:
    """Convenience builder for EvoptResult in sync tests."""
    return EvoptResult(status="Optimal", objective_value=0.0, batteries=batteries)


def _make_battery(
    title: str,
    charging: list[float],
    discharging: list[float] | None = None,
    soc: list[float] | None = None,
) -> EvoptBatteryTimeseries:
    n = len(charging)
    if discharging is None:
        discharging = [0.0] * n
    if soc is None:
        soc = [0.5] * n
    timestamps = [_T0_DT + timedelta(minutes=15 * i) for i in range(n)]
    return EvoptBatteryTimeseries(
        title=title,
        charging_power_w=charging,
        discharging_power_w=discharging,
        soc_fraction=soc,
        slot_timestamps_utc=timestamps,
    )


# ===========================================================================
# Section 1 — EvccClient.get_state() (async, mocked HTTP)
# ===========================================================================


async def test_get_state_success():
    """Happy path: 200 response parsed into EvccState with correct fields."""
    resp_mock = _make_http_response_mock(EVCC_STATE_FIXTURE, 200)
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=resp_mock)

    with patch("backend.evcc_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=client_mock)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await EvccClient(_make_config()).get_state()

    assert isinstance(result, EvccState)
    assert result.evopt_status == "Optimal"
    assert result.evopt is not None
    assert result.solar is not None
    assert result.grid_prices is not None


async def test_get_state_http_500():
    """HTTP 500 → returns None without raising."""
    resp_mock = _make_http_response_mock(EVCC_STATE_FIXTURE, 500)
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=resp_mock)

    with patch("backend.evcc_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=client_mock)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await EvccClient(_make_config()).get_state()

    assert result is None


async def test_get_state_connection_error():
    """ConnectError → returns None without raising."""
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with patch("backend.evcc_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=client_mock)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await EvccClient(_make_config()).get_state()

    assert result is None


async def test_get_state_missing_evopt_key():
    """Response missing 'evopt' key → EvccState with evopt=None (no raise)."""
    fixture_no_evopt = {k: v for k, v in EVCC_STATE_FIXTURE.items() if k != "evopt"}
    resp_mock = _make_http_response_mock(fixture_no_evopt, 200)
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=resp_mock)

    with patch("backend.evcc_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=client_mock)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await EvccClient(_make_config()).get_state()

    assert isinstance(result, EvccState)
    assert result.evopt is None
    assert result.solar is not None  # forecast still parsed


async def test_get_state_missing_forecast_key():
    """Response missing 'forecast' key → EvccState with solar=None (no raise)."""
    fixture_no_forecast = {k: v for k, v in EVCC_STATE_FIXTURE.items() if k != "forecast"}
    resp_mock = _make_http_response_mock(fixture_no_forecast, 200)
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=resp_mock)

    with patch("backend.evcc_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=client_mock)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await EvccClient(_make_config()).get_state()

    assert isinstance(result, EvccState)
    assert result.solar is None
    assert result.grid_prices is None
    assert result.evopt is not None  # evopt still parsed


async def test_get_state_logs_warning_on_error(caplog):
    """HTTP error emits WARNING log with 'evcc get_state failed' prefix."""
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(side_effect=httpx.ConnectError("unreachable"))

    with patch("backend.evcc_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=client_mock)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        with caplog.at_level(logging.WARNING, logger="ems.evcc"):
            result = await EvccClient(_make_config()).get_state()

    assert result is None
    assert any("evcc get_state failed" in r.message for r in caplog.records)


# ===========================================================================
# Section 2 — _parse_state: full fixture round-trip and structural assertions
# ===========================================================================


def test_parse_state_returns_evcc_state():
    """Full fixture round-trip produces an EvccState."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert isinstance(state, EvccState)


def test_parse_state_evopt_status():
    """evopt_status field carries the raw solver status string."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.evopt_status == "Optimal"


def test_parse_state_objective_value():
    """objective_value round-trips from fixture."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.evopt is not None
    assert state.evopt.objective_value == pytest.approx(42.5)


def test_evopt_result_from_fixture():
    """Parsed EvoptResult has exactly 3 batteries with the expected titles."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.evopt is not None
    assert len(state.evopt.batteries) == 3
    titles = [b.title for b in state.evopt.batteries]
    assert "Emma Akku 1" in titles
    assert "Emma Akku 2" in titles
    assert "Victron" in titles


def test_evopt_batteries_slot_count():
    """Each parsed battery has 8 slots matching the fixture timestamp list."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.evopt is not None
    for bat in state.evopt.batteries:
        assert len(bat.slot_timestamps_utc) == 8
        assert len(bat.charging_power_w) == 8
        assert len(bat.discharging_power_w) == 8
        assert len(bat.soc_fraction) == 8


def test_slot_timestamps_derived_from_t0_not_midnight():
    """Slot[0] timestamp equals _TS_8[0]; slot[1] = slot[0] + 15 min."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.evopt is not None
    bat = state.evopt.batteries[0]
    expected_t0 = datetime(2026, 1, 15, 23, 0, 0, tzinfo=timezone.utc)
    expected_t1 = expected_t0 + timedelta(minutes=15)
    assert bat.slot_timestamps_utc[0] == expected_t0
    assert bat.slot_timestamps_utc[1] == expected_t1


def test_slot_timestamps_all_slots_spaced_15_min():
    """All 8 consecutive timestamps are exactly 15 minutes apart."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.evopt is not None
    bat = state.evopt.batteries[0]
    for i in range(1, 8):
        delta = bat.slot_timestamps_utc[i] - bat.slot_timestamps_utc[i - 1]
        assert delta == timedelta(minutes=15)


def test_parse_state_missing_evopt():
    """Missing 'evopt' key → evopt=None; solar and grid still parsed."""
    data = {k: v for k, v in EVCC_STATE_FIXTURE.items() if k != "evopt"}
    state = _parse_state(data)
    assert state.evopt is None
    assert state.solar is not None
    assert state.grid_prices is not None


def test_parse_state_missing_forecast():
    """Missing 'forecast' key → solar=None, grid_prices=None; evopt still parsed."""
    data = {k: v for k, v in EVCC_STATE_FIXTURE.items() if k != "forecast"}
    state = _parse_state(data)
    assert state.solar is None
    assert state.grid_prices is None
    assert state.evopt is not None


# ===========================================================================
# Section 3 — SolarForecast
# ===========================================================================


def test_solar_forecast_tomorrow_energy_wh():
    """tomorrow_energy_wh round-trips from fixture (15000 Wh)."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.solar is not None
    assert state.solar.tomorrow_energy_wh == pytest.approx(15000.0)


def test_solar_forecast_day_after_energy_wh():
    """day_after_energy_wh round-trips from fixture (20000 Wh)."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.solar is not None
    assert state.solar.day_after_energy_wh == pytest.approx(20000.0)


def test_solar_forecast_timeseries_length():
    """Solar timeseries has 4 slots matching the fixture."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.solar is not None
    assert len(state.solar.timeseries_w) == 4


def test_solar_forecast_timeseries_values():
    """Solar timeseries values round-trip from fixture (spot check first slot)."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.solar is not None
    assert state.solar.timeseries_w[0] == pytest.approx(1000.0)
    assert state.solar.timeseries_w[2] == pytest.approx(1500.0)


def test_solar_forecast_missing_day_after():
    """dayAfterTomorrow absent → day_after_energy_wh defaults to 0.0."""
    data = copy.deepcopy(EVCC_STATE_FIXTURE)
    del data["forecast"]["solar"]["dayAfterTomorrow"]
    state = _parse_state(data)
    assert state.solar is not None
    assert state.solar.day_after_energy_wh == pytest.approx(0.0)


# ===========================================================================
# Section 4 — GridPriceSeries
# ===========================================================================


def test_grid_prices_import_length():
    """Import price timeseries has 4 slots."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.grid_prices is not None
    assert len(state.grid_prices.import_eur_kwh) == 4


def test_grid_prices_export_length():
    """Export price timeseries has 4 slots."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.grid_prices is not None
    assert len(state.grid_prices.export_eur_kwh) == 4


def test_grid_prices_import_value():
    """First import price round-trips from fixture (0.18 €/kWh)."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.grid_prices is not None
    assert state.grid_prices.import_eur_kwh[0] == pytest.approx(0.18)


def test_grid_prices_export_value():
    """First export (feedin) price round-trips from fixture (0.08 €/kWh)."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.grid_prices is not None
    assert state.grid_prices.export_eur_kwh[0] == pytest.approx(0.08)


def test_grid_prices_timestamps_populated():
    """Grid price timestamps are non-empty and timezone-aware."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.grid_prices is not None
    assert len(state.grid_prices.slot_timestamps_utc) == 4
    for ts in state.grid_prices.slot_timestamps_utc:
        assert ts.tzinfo is not None


# ===========================================================================
# Section 5 — EvoptResult.get_huawei_target_soc_pct()
# ===========================================================================


def test_huawei_target_soc_both_emma_packs_present():
    """Both Emma packs in fixture → target > initial_soc_pct (charging)."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.evopt is not None
    result = state.evopt.get_huawei_target_soc_pct(30.0, initial_soc_pct=50.0)
    assert result > 50.0
    assert result <= 95.0


def test_huawei_target_soc_math_exact():
    """Exact numeric proof: Emma 1 + Emma 2 net energy sums correctly.

    Emma 1: [2000W, 0, 0, 0] charge / [0, 0, 0, 0] discharge → 2000 * 0.25h = 500 Wh
    Emma 2: [1000W, 0, 0, 0] charge / [0, 0, 0, 0] discharge → 1000 * 0.25h = 250 Wh
    Total net energy = 750 Wh
    initial_soc_pct = 50.0, huawei_capacity_kwh = 30.0
    SoC delta = 750 / 30000 * 100 = 2.5%
    Expected result = 52.5%
    """
    emma1 = _make_battery("Emma Akku 1", [2000.0, 0.0, 0.0, 0.0])
    emma2 = _make_battery("Emma Akku 2", [1000.0, 0.0, 0.0, 0.0])
    evopt = _make_evopt_result([emma1, emma2])
    result = evopt.get_huawei_target_soc_pct(30.0, initial_soc_pct=50.0)
    assert result == pytest.approx(52.5)


def test_huawei_target_soc_clamped_at_max():
    """Large charging energy → result clamped at 95.0."""
    # 100 kW charging for 4 slots (1h) = 100,000 Wh per pack
    emma1 = _make_battery("Emma Akku 1", [100_000.0, 100_000.0, 100_000.0, 100_000.0])
    emma2 = _make_battery("Emma Akku 2", [100_000.0, 100_000.0, 100_000.0, 100_000.0])
    evopt = _make_evopt_result([emma1, emma2])
    result = evopt.get_huawei_target_soc_pct(30.0, initial_soc_pct=94.0)
    assert result == pytest.approx(95.0)


def test_huawei_target_soc_clamped_at_min():
    """Heavy discharging → result clamped at 10.0."""
    emma1 = _make_battery(
        "Emma Akku 1",
        [0.0, 0.0, 0.0, 0.0],
        discharging=[100_000.0, 100_000.0, 100_000.0, 100_000.0],
    )
    emma2 = _make_battery(
        "Emma Akku 2",
        [0.0, 0.0, 0.0, 0.0],
        discharging=[100_000.0, 100_000.0, 100_000.0, 100_000.0],
    )
    evopt = _make_evopt_result([emma1, emma2])
    result = evopt.get_huawei_target_soc_pct(30.0, initial_soc_pct=12.0)
    assert result == pytest.approx(10.0)


def test_huawei_target_only_one_emma_pack():
    """Only Emma Akku 1 present → partial sum used, no crash."""
    emma1 = _make_battery("Emma Akku 1", [2000.0, 0.0, 0.0, 0.0])
    victron = _make_battery("Victron", [500.0, 0.0, 0.0, 0.0])
    evopt = _make_evopt_result([emma1, victron])
    # Emma 1 only: 2000 * 0.25h = 500 Wh / 30000 * 100 = 1.667%, total = 51.667%
    result = evopt.get_huawei_target_soc_pct(30.0, initial_soc_pct=50.0)
    assert result == pytest.approx(50.0 + (500.0 / 30000.0) * 100, rel=1e-5)
    assert result > 50.0


def test_huawei_target_no_emma_packs():
    """No Emma batteries → returns initial_soc_pct unchanged (no crash)."""
    victron = _make_battery("Victron", [1000.0, 0.0, 0.0, 0.0])
    evopt = _make_evopt_result([victron])
    result = evopt.get_huawei_target_soc_pct(30.0, initial_soc_pct=55.0)
    assert result == pytest.approx(55.0)


def test_huawei_target_net_energy_charge_minus_discharge():
    """Net energy = charging minus discharging; discharge reduces target."""
    # charge 4000 W for 1 slot, discharge 2000 W for 1 slot → net = 2000 * 0.25h = 500 Wh
    emma1 = _make_battery(
        "Emma Akku 1",
        [4000.0, 0.0, 0.0, 0.0],
        discharging=[2000.0, 0.0, 0.0, 0.0],
    )
    evopt = _make_evopt_result([emma1])
    # net = (4000 - 2000) * 0.25 = 500 Wh → delta = 500/30000 * 100 = 1.667%
    result = evopt.get_huawei_target_soc_pct(30.0, initial_soc_pct=50.0)
    assert result == pytest.approx(50.0 + (500.0 / 30000.0) * 100, rel=1e-5)


def test_huawei_target_logs_warning_when_no_emma_packs(caplog):
    """No Emma packs → WARNING log emitted."""
    victron = _make_battery("Victron", [1000.0])
    evopt = _make_evopt_result([victron])
    with caplog.at_level(logging.WARNING, logger="ems.evcc"):
        evopt.get_huawei_target_soc_pct(30.0, initial_soc_pct=50.0)
    assert any("Emma Akku" in r.message for r in caplog.records)


# ===========================================================================
# Section 6 — EvoptResult.get_victron_target_soc_pct()
# ===========================================================================


def test_victron_target_soc_present():
    """Victron battery in fixture → target > initial_soc_pct (charging)."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.evopt is not None
    result = state.evopt.get_victron_target_soc_pct(10.0, initial_soc_pct=40.0)
    assert result > 40.0
    assert result <= 95.0


def test_victron_target_soc_math_exact():
    """Exact math: Victron 1000W for 1 slot (0.25h) = 250 Wh, capacity 10 kWh."""
    # 250 / 10000 * 100 = 2.5%, result = 52.5%
    victron = _make_battery("Victron", [1000.0, 0.0, 0.0, 0.0])
    evopt = _make_evopt_result([victron])
    result = evopt.get_victron_target_soc_pct(10.0, initial_soc_pct=50.0)
    assert result == pytest.approx(52.5)


def test_victron_target_no_victron_battery():
    """No Victron entry → returns initial_soc_pct fallback (no crash)."""
    emma1 = _make_battery("Emma Akku 1", [1000.0, 0.0])
    evopt = _make_evopt_result([emma1])
    result = evopt.get_victron_target_soc_pct(10.0, initial_soc_pct=45.0)
    assert result == pytest.approx(45.0)


def test_victron_target_soc_clamped_at_max():
    """Heavy Victron charging → clamped to 95.0."""
    victron = _make_battery("Victron", [100_000.0, 100_000.0, 100_000.0, 100_000.0])
    evopt = _make_evopt_result([victron])
    result = evopt.get_victron_target_soc_pct(10.0, initial_soc_pct=94.0)
    assert result == pytest.approx(95.0)


def test_victron_target_soc_clamped_at_min():
    """Heavy Victron discharging → clamped to 10.0."""
    victron = _make_battery(
        "Victron",
        [0.0, 0.0, 0.0, 0.0],
        discharging=[100_000.0, 100_000.0, 100_000.0, 100_000.0],
    )
    evopt = _make_evopt_result([victron])
    result = evopt.get_victron_target_soc_pct(10.0, initial_soc_pct=12.0)
    assert result == pytest.approx(10.0)


def test_victron_target_logs_warning_when_absent(caplog):
    """No Victron battery → WARNING log emitted."""
    emma1 = _make_battery("Emma Akku 1", [1000.0])
    evopt = _make_evopt_result([emma1])
    with caplog.at_level(logging.WARNING, logger="ems.evcc"):
        evopt.get_victron_target_soc_pct(10.0, initial_soc_pct=50.0)
    assert any("Victron" in r.message for r in caplog.records)


# ===========================================================================
# Section 7 — Additional edge cases and integration checks
# ===========================================================================


def test_parse_state_evopt_status_unknown_when_missing():
    """evopt_status defaults to 'unknown' when 'status' key absent from EVopt."""
    data = copy.deepcopy(EVCC_STATE_FIXTURE)
    del data["evopt"]["res"]["status"]
    state = _parse_state(data)
    assert state.evopt_status == "unknown"


def test_parse_state_infeasible_status():
    """evopt_status carries 'Infeasible' string when EVopt solver returns it."""
    data = copy.deepcopy(EVCC_STATE_FIXTURE)
    data["evopt"]["res"]["status"] = "Infeasible"
    state = _parse_state(data)
    assert state.evopt_status == "Infeasible"


def test_huawei_no_slots_returns_initial():
    """Empty slot lists → zero net energy → returns clamped initial_soc_pct."""
    emma1 = _make_battery("Emma Akku 1", [])
    emma2 = _make_battery("Emma Akku 2", [])
    evopt = _make_evopt_result([emma1, emma2])
    result = evopt.get_huawei_target_soc_pct(30.0, initial_soc_pct=60.0)
    # net energy = 0, delta = 0, result = 60.0 (clamped to [10, 95])
    assert result == pytest.approx(60.0)


def test_charge_schedule_stale_flag_default():
    """ChargeSchedule.stale defaults to False."""
    from backend.schedule_models import ChargeSchedule, OptimizationReasoning
    now = datetime.now(tz=timezone.utc)
    sched = ChargeSchedule(
        slots=[],
        reasoning=OptimizationReasoning(
            text="test",
            tomorrow_solar_kwh=0.0,
            expected_consumption_kwh=0.0,
            charge_energy_kwh=0.0,
            cost_estimate_eur=0.0,
        ),
        computed_at=now,
    )
    assert sched.stale is False


async def test_get_state_returns_evopt_status_from_fixture():
    """EvccState.evopt_status from get_state() matches fixture 'Optimal'."""
    resp_mock = _make_http_response_mock(EVCC_STATE_FIXTURE, 200)
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=resp_mock)

    with patch("backend.evcc_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=client_mock)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await EvccClient(_make_config()).get_state()

    assert result is not None
    assert result.evopt_status == "Optimal"


def test_grid_slot_timestamps_utc_aware():
    """All grid price slot timestamps are UTC-aware datetimes."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.grid_prices is not None
    for ts in state.grid_prices.slot_timestamps_utc:
        assert ts.tzinfo is not None
        assert ts.tzinfo == timezone.utc or ts.utcoffset().total_seconds() == 0


def test_solar_slot_timestamps_utc_aware():
    """All solar timeseries slot timestamps are UTC-aware datetimes."""
    state = _parse_state(EVCC_STATE_FIXTURE)
    assert state.solar is not None
    for ts in state.solar.slot_timestamps_utc:
        assert ts.tzinfo is not None
