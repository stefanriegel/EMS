"""Unit tests for InfluxMetricsReader.

Covers:
  - query_range: correct Flux query construction; flattened record list shape
  - query_latest: Flux query includes |> last(); returns single dict or None
  - error handling: exception from query_api.query() is swallowed; [] / None returned

All tests mock InfluxDBClientAsync — no real InfluxDB connection required.

K007: Use @pytest.mark.anyio on async test functions.
K002: Do NOT rely on asyncio_mode = "auto"; use @pytest.mark.anyio explicitly.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.influx_reader import InfluxMetricsReader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_record(
    time_val: str = "2026-01-01T00:00:00+00:00",
    field_val: str = "combined_soc_pct",
    value_val: float = 62.5,
) -> MagicMock:
    """Return a mock FluxRecord with the three accessor methods wired up."""
    record = MagicMock()
    record.get_time.return_value = time_val
    record.get_field.return_value = field_val
    record.get_value.return_value = value_val
    return record


def _make_mock_table(records: list[MagicMock]) -> MagicMock:
    """Return a mock FluxTable with a populated .records list."""
    table = MagicMock()
    table.records = records
    return table


def _make_reader(
    tables: list[MagicMock] | Exception,
    org: str = "ems",
    bucket: str = "ems_data",
) -> InfluxMetricsReader:
    """Build an InfluxMetricsReader backed by a mock query_api.

    Parameters
    ----------
    tables:
        Either the list of FluxTable mocks to return from ``query()``, or an
        Exception instance to raise when ``query()`` is awaited.
    """
    query_api = MagicMock()
    if isinstance(tables, Exception):
        query_api.query = AsyncMock(side_effect=tables)
    else:
        query_api.query = AsyncMock(return_value=tables)

    client = MagicMock()
    client.query_api.return_value = query_api

    return InfluxMetricsReader(client=client, org=org, bucket=bucket)


# ---------------------------------------------------------------------------
# query_range — Flux query construction
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_range_flux_contains_bucket() -> None:
    """query_range Flux string includes the bucket name."""
    reader = _make_reader(tables=[])
    await reader.query_range("ems_system", "-1h", "now()")
    call_kwargs = reader._query_api.query.call_args
    flux: str = call_kwargs.kwargs.get("query") or call_kwargs.args[0]
    assert "ems_data" in flux


@pytest.mark.anyio
async def test_query_range_flux_contains_measurement() -> None:
    """query_range Flux string filters on measurement name."""
    reader = _make_reader(tables=[])
    await reader.query_range("ems_system", "-1h", "now()")
    call_kwargs = reader._query_api.query.call_args
    flux: str = call_kwargs.kwargs.get("query") or call_kwargs.args[0]
    assert "ems_system" in flux


@pytest.mark.anyio
async def test_query_range_flux_contains_start_and_stop() -> None:
    """query_range Flux string contains the start and stop parameters."""
    reader = _make_reader(tables=[])
    await reader.query_range("ems_system", "-2h", "now()")
    call_kwargs = reader._query_api.query.call_args
    flux: str = call_kwargs.kwargs.get("query") or call_kwargs.args[0]
    assert "-2h" in flux
    assert "now()" in flux


@pytest.mark.anyio
async def test_query_range_passes_org() -> None:
    """query_range passes org to query_api.query()."""
    reader = _make_reader(tables=[], org="my-org")
    await reader.query_range("ems_system", "-1h", "now()")
    call_kwargs = reader._query_api.query.call_args
    org_arg = call_kwargs.kwargs.get("org") or call_kwargs.args[1]
    assert org_arg == "my-org"


# ---------------------------------------------------------------------------
# query_range — record flattening
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_range_returns_empty_list_when_no_tables() -> None:
    """query_range returns [] when the query returns no tables."""
    reader = _make_reader(tables=[])
    result = await reader.query_range("ems_system", "-1h", "now()")
    assert result == []


@pytest.mark.anyio
async def test_query_range_returns_flattened_records() -> None:
    """query_range flattens records across tables into list[dict]."""
    rec1 = _make_mock_record("2026-01-01T00:00:00+00:00", "combined_soc_pct", 62.5)
    rec2 = _make_mock_record("2026-01-01T00:00:05+00:00", "combined_power_w", 1500.0)
    table1 = _make_mock_table([rec1])
    table2 = _make_mock_table([rec2])
    reader = _make_reader(tables=[table1, table2])

    result = await reader.query_range("ems_system", "-1h", "now()")

    assert len(result) == 2
    assert result[0] == {
        "time": "2026-01-01T00:00:00+00:00",
        "field": "combined_soc_pct",
        "value": 62.5,
    }
    assert result[1] == {
        "time": "2026-01-01T00:00:05+00:00",
        "field": "combined_power_w",
        "value": 1500.0,
    }


@pytest.mark.anyio
async def test_query_range_record_has_time_field_value_keys() -> None:
    """Each record dict has exactly the expected keys."""
    rec = _make_mock_record()
    table = _make_mock_table([rec])
    reader = _make_reader(tables=[table])

    result = await reader.query_range("ems_system", "-1h", "now()")

    assert len(result) == 1
    assert set(result[0].keys()) == {"time", "field", "value"}


@pytest.mark.anyio
async def test_query_range_multiple_records_in_one_table() -> None:
    """query_range flattens multiple records from a single table."""
    records = [
        _make_mock_record(f"2026-01-01T00:00:0{i}+00:00", "soc", float(i))
        for i in range(3)
    ]
    table = _make_mock_table(records)
    reader = _make_reader(tables=[table])

    result = await reader.query_range("ems_system", "-1h", "now()")
    assert len(result) == 3


# ---------------------------------------------------------------------------
# query_range — error handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_range_swallows_exception_and_returns_empty() -> None:
    """query_range swallows exceptions and returns [] — never raises."""
    reader = _make_reader(tables=RuntimeError("connection refused"))
    result = await reader.query_range("ems_system", "-1h", "now()")
    assert result == []


@pytest.mark.anyio
async def test_query_range_exception_logged_as_warning(caplog: pytest.LogCaptureFixture) -> None:
    """query_range logs at WARNING level when an exception occurs."""
    import logging

    reader = _make_reader(tables=RuntimeError("network error"))
    with caplog.at_level(logging.WARNING, logger="backend.influx_reader"):
        await reader.query_range("ems_system", "-1h", "now()")

    assert any("influx query failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# query_latest — Flux query construction
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_latest_flux_contains_last() -> None:
    """query_latest Flux query includes |> last()."""
    reader = _make_reader(tables=[])
    await reader.query_latest("ems_system")
    call_kwargs = reader._query_api.query.call_args
    flux: str = call_kwargs.kwargs.get("query") or call_kwargs.args[0]
    assert "|> last()" in flux


@pytest.mark.anyio
async def test_query_latest_flux_contains_measurement() -> None:
    """query_latest Flux query filters on measurement name."""
    reader = _make_reader(tables=[])
    await reader.query_latest("ems_tariff")
    call_kwargs = reader._query_api.query.call_args
    flux: str = call_kwargs.kwargs.get("query") or call_kwargs.args[0]
    assert "ems_tariff" in flux


@pytest.mark.anyio
async def test_query_latest_flux_contains_bucket() -> None:
    """query_latest Flux query includes the configured bucket name."""
    reader = _make_reader(tables=[], bucket="my_bucket")
    await reader.query_latest("ems_system")
    call_kwargs = reader._query_api.query.call_args
    flux: str = call_kwargs.kwargs.get("query") or call_kwargs.args[0]
    assert "my_bucket" in flux


# ---------------------------------------------------------------------------
# query_latest — return value
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_latest_returns_none_when_no_data() -> None:
    """query_latest returns None when no tables / records are returned."""
    reader = _make_reader(tables=[])
    result = await reader.query_latest("ems_system")
    assert result is None


@pytest.mark.anyio
async def test_query_latest_returns_none_when_tables_empty_records() -> None:
    """query_latest returns None when tables have no records."""
    table = _make_mock_table(records=[])
    reader = _make_reader(tables=[table])
    result = await reader.query_latest("ems_system")
    assert result is None


@pytest.mark.anyio
async def test_query_latest_returns_first_record_dict() -> None:
    """query_latest returns the first record as a dict."""
    rec = _make_mock_record("2026-01-01T12:00:00+00:00", "combined_soc_pct", 75.0)
    table = _make_mock_table([rec])
    reader = _make_reader(tables=[table])

    result = await reader.query_latest("ems_system")

    assert result == {
        "time": "2026-01-01T12:00:00+00:00",
        "field": "combined_soc_pct",
        "value": 75.0,
    }


@pytest.mark.anyio
async def test_query_latest_returns_single_dict_not_list() -> None:
    """query_latest returns a dict, not a list."""
    rec = _make_mock_record()
    table = _make_mock_table([rec])
    reader = _make_reader(tables=[table])

    result = await reader.query_latest("ems_system")

    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# query_latest — error handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_latest_swallows_exception_and_returns_none() -> None:
    """query_latest swallows exceptions and returns None — never raises."""
    reader = _make_reader(tables=ConnectionError("timeout"))
    result = await reader.query_latest("ems_system")
    assert result is None


@pytest.mark.anyio
async def test_query_latest_exception_logged_as_warning(caplog: pytest.LogCaptureFixture) -> None:
    """query_latest logs at WARNING level when an exception occurs."""
    import logging

    reader = _make_reader(tables=OSError("connection refused"))
    with caplog.at_level(logging.WARNING, logger="backend.influx_reader"):
        await reader.query_latest("ems_system")

    assert any("influx query failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Helpers for query_consumption_history tests
# ---------------------------------------------------------------------------


def _make_consumption_record(date_str: str, value_w: float = 500.0) -> MagicMock:
    """Return a mock record for one calendar day (post-Flux sign-flip).

    ``date_str`` is an ISO-8601 date string like ``"2026-01-05"``.
    ``value_w`` is positive watts (Flux map already flipped the sign).
    """
    time_str = f"{date_str}T12:00:00+00:00"
    return _make_mock_record(time_val=time_str, field_val="combined_power_w", value_val=value_w)


def _make_consumption_tables(dates: list[str], value_w: float = 500.0) -> list[MagicMock]:
    """One table with one record per date string."""
    records = [_make_consumption_record(d, value_w) for d in dates]
    return [_make_mock_table(records)]


def _fourteen_dates() -> list[str]:
    """Return 14 consecutive ISO date strings starting 2026-01-01."""
    import datetime as _dt
    return [
        (_dt.date(2026, 1, 1) + _dt.timedelta(days=i)).isoformat()
        for i in range(14)
    ]


# ---------------------------------------------------------------------------
# query_consumption_history — Flux query construction
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consumption_history_flux_contains_bucket() -> None:
    """Flux query includes the configured bucket name."""
    reader = _make_reader(tables=_make_consumption_tables(_fourteen_dates()), bucket="my_bucket")
    await reader.query_consumption_history()
    flux: str = reader._query_api.query.call_args.kwargs.get("query") or \
        reader._query_api.query.call_args.args[0]
    assert "my_bucket" in flux


@pytest.mark.anyio
async def test_consumption_history_flux_contains_measurement() -> None:
    """Flux query filters on ems_system measurement."""
    reader = _make_reader(tables=_make_consumption_tables(_fourteen_dates()))
    await reader.query_consumption_history()
    flux: str = reader._query_api.query.call_args.kwargs.get("query") or \
        reader._query_api.query.call_args.args[0]
    assert "ems_system" in flux


@pytest.mark.anyio
async def test_consumption_history_flux_contains_field_filter() -> None:
    """Flux query filters on the combined_power_w field."""
    reader = _make_reader(tables=_make_consumption_tables(_fourteen_dates()))
    await reader.query_consumption_history()
    flux: str = reader._query_api.query.call_args.kwargs.get("query") or \
        reader._query_api.query.call_args.args[0]
    assert "combined_power_w" in flux


@pytest.mark.anyio
async def test_consumption_history_flux_contains_range() -> None:
    """Default days=14 produces a -14d range in the Flux query."""
    reader = _make_reader(tables=_make_consumption_tables(_fourteen_dates()))
    await reader.query_consumption_history()
    flux: str = reader._query_api.query.call_args.kwargs.get("query") or \
        reader._query_api.query.call_args.args[0]
    assert "-14d" in flux


@pytest.mark.anyio
async def test_consumption_history_flux_custom_days() -> None:
    """Passing days=7 produces a -7d range in the Flux query."""
    import datetime as _dt
    seven_dates = [
        (_dt.date(2026, 1, 1) + _dt.timedelta(days=i)).isoformat()
        for i in range(7)
    ]
    reader = _make_reader(tables=_make_consumption_tables(seven_dates))
    await reader.query_consumption_history(days=7)
    flux: str = reader._query_api.query.call_args.kwargs.get("query") or \
        reader._query_api.query.call_args.args[0]
    assert "-7d" in flux
    assert "-14d" not in flux


@pytest.mark.anyio
async def test_consumption_history_flux_contains_tz() -> None:
    """The timezone string appears in the Flux query."""
    reader = _make_reader(tables=_make_consumption_tables(_fourteen_dates()))
    await reader.query_consumption_history(tz="Europe/Berlin")
    flux: str = reader._query_api.query.call_args.kwargs.get("query") or \
        reader._query_api.query.call_args.args[0]
    assert "Europe/Berlin" in flux


@pytest.mark.anyio
async def test_consumption_history_flux_negative_filter() -> None:
    """Flux query includes a filter for negative values (discharge proxy)."""
    reader = _make_reader(tables=_make_consumption_tables(_fourteen_dates()))
    await reader.query_consumption_history()
    flux: str = reader._query_api.query.call_args.kwargs.get("query") or \
        reader._query_api.query.call_args.args[0]
    assert "< 0" in flux


# ---------------------------------------------------------------------------
# query_consumption_history — return type and not-None contract
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consumption_history_returns_consumption_forecast_type() -> None:
    """Result is always a ConsumptionForecast instance."""
    from backend.schedule_models import ConsumptionForecast
    reader = _make_reader(tables=_make_consumption_tables(_fourteen_dates()))
    result = await reader.query_consumption_history()
    assert isinstance(result, ConsumptionForecast)


@pytest.mark.anyio
async def test_consumption_history_not_none() -> None:
    """query_consumption_history never returns None."""
    reader = _make_reader(tables=[])
    result = await reader.query_consumption_history()
    assert result is not None


# ---------------------------------------------------------------------------
# query_consumption_history — happy path (≥7 days)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consumption_history_fallback_false_with_sufficient_data() -> None:
    """fallback_used=False when records span 14 distinct dates."""
    reader = _make_reader(tables=_make_consumption_tables(_fourteen_dates()))
    result = await reader.query_consumption_history()
    assert result.fallback_used is False


@pytest.mark.anyio
async def test_consumption_history_kwh_by_weekday_keys_are_ints() -> None:
    """kwh_by_weekday keys are integers 0–6."""
    reader = _make_reader(tables=_make_consumption_tables(_fourteen_dates()))
    result = await reader.query_consumption_history()
    assert result.fallback_used is False
    for key in result.kwh_by_weekday:
        assert isinstance(key, int)
        assert 0 <= key <= 6


@pytest.mark.anyio
async def test_consumption_history_today_expected_kwh_from_weekday_map() -> None:
    """today_expected_kwh matches kwh_by_weekday[today.weekday()] when present."""
    import datetime as _dt
    reader = _make_reader(tables=_make_consumption_tables(_fourteen_dates()))
    result = await reader.query_consumption_history()
    today_wd = _dt.date.today().weekday()
    if today_wd in result.kwh_by_weekday:
        assert result.today_expected_kwh == pytest.approx(result.kwh_by_weekday[today_wd])


@pytest.mark.anyio
async def test_consumption_history_days_of_history_count() -> None:
    """days_of_history equals the number of distinct calendar dates in mock records."""
    reader = _make_reader(tables=_make_consumption_tables(_fourteen_dates()))
    result = await reader.query_consumption_history()
    assert result.days_of_history == 14


@pytest.mark.anyio
async def test_consumption_history_kwh_conversion() -> None:
    """500 W mean over 1 day → 12.0 kWh (500 * 24 / 1000)."""
    # Use 14 records all on different dates but same weekday — or just check
    # that any entry in kwh_by_weekday is 500*24/1000 = 12.0 when value=500W.
    dates = _fourteen_dates()
    reader = _make_reader(tables=_make_consumption_tables(dates, value_w=500.0))
    result = await reader.query_consumption_history()
    assert result.fallback_used is False
    # Every weekday bucket should be 12.0 kWh
    for kwh in result.kwh_by_weekday.values():
        assert kwh == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# query_consumption_history — fallback path (< 7 days)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consumption_history_fallback_true_with_few_records() -> None:
    """fallback_used=True when records span only 3 distinct dates."""
    import datetime as _dt
    three_dates = [(_dt.date(2026, 1, 1) + _dt.timedelta(days=i)).isoformat() for i in range(3)]
    reader = _make_reader(tables=_make_consumption_tables(three_dates))
    result = await reader.query_consumption_history()
    assert result.fallback_used is True


@pytest.mark.anyio
async def test_consumption_history_fallback_kwh_by_weekday_empty_on_few_records() -> None:
    """kwh_by_weekday == {} when fallback is triggered (< 7 days)."""
    import datetime as _dt
    few_dates = [(_dt.date(2026, 3, 1) + _dt.timedelta(days=i)).isoformat() for i in range(4)]
    reader = _make_reader(tables=_make_consumption_tables(few_dates))
    result = await reader.query_consumption_history()
    assert result.kwh_by_weekday == {}


@pytest.mark.anyio
async def test_consumption_history_no_records_returns_fallback() -> None:
    """No tables at all → fallback_used=True."""
    reader = _make_reader(tables=[])
    result = await reader.query_consumption_history()
    assert result.fallback_used is True


@pytest.mark.anyio
async def test_consumption_history_days_of_history_zero_on_no_records() -> None:
    """days_of_history==0 when there are no records."""
    reader = _make_reader(tables=[])
    result = await reader.query_consumption_history()
    assert result.days_of_history == 0


# ---------------------------------------------------------------------------
# query_consumption_history — seasonal fallback constants
# ---------------------------------------------------------------------------


def test_seasonal_fallback_winter_month() -> None:
    """Month 12 (December) → 35.0 kWh/day."""
    import datetime as _dt
    from backend.influx_reader import _seasonal_fallback_kwh
    assert _seasonal_fallback_kwh(_dt.date(2026, 12, 15)) == pytest.approx(35.0)


def test_seasonal_fallback_winter_month_january() -> None:
    """Month 1 (January) → 35.0 kWh/day."""
    import datetime as _dt
    from backend.influx_reader import _seasonal_fallback_kwh
    assert _seasonal_fallback_kwh(_dt.date(2026, 1, 10)) == pytest.approx(35.0)


def test_seasonal_fallback_summer_month() -> None:
    """Month 7 (July) → 15.0 kWh/day."""
    import datetime as _dt
    from backend.influx_reader import _seasonal_fallback_kwh
    assert _seasonal_fallback_kwh(_dt.date(2026, 7, 1)) == pytest.approx(15.0)


def test_seasonal_fallback_shoulder_month() -> None:
    """Month 4 (April) → 25.0 kWh/day."""
    import datetime as _dt
    from backend.influx_reader import _seasonal_fallback_kwh
    assert _seasonal_fallback_kwh(_dt.date(2026, 4, 15)) == pytest.approx(25.0)


def test_seasonal_fallback_shoulder_march() -> None:
    """Month 3 (March) → 25.0 kWh/day (shoulder season)."""
    import datetime as _dt
    from backend.influx_reader import _seasonal_fallback_kwh
    assert _seasonal_fallback_kwh(_dt.date(2026, 3, 20)) == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# query_consumption_history — error handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consumption_history_swallows_exception() -> None:
    """Exception from query_api.query() → returns ConsumptionForecast, does not raise."""
    from backend.schedule_models import ConsumptionForecast
    reader = _make_reader(tables=RuntimeError("influx down"))
    result = await reader.query_consumption_history()
    assert isinstance(result, ConsumptionForecast)


@pytest.mark.anyio
async def test_consumption_history_exception_fallback_used_true() -> None:
    """Exception path always returns fallback_used=True."""
    reader = _make_reader(tables=ConnectionError("timeout"))
    result = await reader.query_consumption_history()
    assert result.fallback_used is True


@pytest.mark.anyio
async def test_consumption_history_exception_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Exception path logs WARNING with 'influx consumption query failed'."""
    import logging
    reader = _make_reader(tables=OSError("network unreachable"))
    with caplog.at_level(logging.WARNING, logger="backend.influx_reader"):
        await reader.query_consumption_history()
    assert any("influx consumption query failed" in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_consumption_history_exception_days_of_history_zero() -> None:
    """Exception path returns days_of_history=0."""
    reader = _make_reader(tables=RuntimeError("boom"))
    result = await reader.query_consumption_history()
    assert result.days_of_history == 0


@pytest.mark.anyio
async def test_consumption_history_exception_kwh_by_weekday_empty() -> None:
    """Exception path returns kwh_by_weekday={}."""
    reader = _make_reader(tables=ValueError("bad query"))
    result = await reader.query_consumption_history()
    assert result.kwh_by_weekday == {}

