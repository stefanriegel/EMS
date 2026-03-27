"""Unit tests for InfluxMetricsReader (InfluxDB v1 InfluxQL via httpx).

Covers:
  - query_range: correct InfluxQL query construction; flattened record list shape
  - query_latest: InfluxQL includes ORDER BY time DESC LIMIT 1; returns single dict or None
  - query_consumption_history: InfluxQL GROUP BY time(1d); weekday grouping; kWh conversion
  - error handling: HTTP errors and exceptions are swallowed; [] / None / fallback returned
  - INFO startup log at construction time

K007: Use @pytest.mark.anyio on async test functions.
"""
from __future__ import annotations

import datetime
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.influx_reader import InfluxMetricsReader, _seasonal_fallback_kwh


# ---------------------------------------------------------------------------
# Helpers — mock httpx responses
# ---------------------------------------------------------------------------


def _v1_response(series: list[dict] | None) -> dict:
    """Build a minimal InfluxDB v1 JSON response.

    ``series`` is a list of series dicts, each with ``columns`` and ``values``.
    Pass ``None`` (or ``[]``) to simulate an empty / no-data response.
    """
    if not series:
        return {"results": [{}]}
    return {"results": [{"series": series}]}


def _make_reader(
    response: dict | Exception,
    url: str = "http://influx:8086",
    database: str = "ems_data",
    username: str = "",
    password: str = "",
) -> InfluxMetricsReader:
    """Build an InfluxMetricsReader backed by a mock httpx.AsyncClient.

    Parameters
    ----------
    response:
        Either the dict to return from the mock GET, or an Exception to raise.
    """
    reader = InfluxMetricsReader(
        url=url,
        database=database,
        username=username,
        password=password,
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    if isinstance(response, Exception):
        reader._http.get = AsyncMock(side_effect=response)
    else:
        mock_resp.json = MagicMock(return_value=response)
        reader._http.get = AsyncMock(return_value=mock_resp)

    return reader


def _wide_series(
    measurement: str,
    fields: list[str],
    rows: list[list],
) -> dict:
    """Return a wide-format series dict (InfluxDB v1 multi-field SELECT *).

    ``fields`` are the non-time column names.
    ``rows`` is a list of [time_str, val1, val2, ...] lists.
    """
    return {
        "name": measurement,
        "columns": ["time"] + fields,
        "values": rows,
    }


# ---------------------------------------------------------------------------
# Construction — INFO log
# ---------------------------------------------------------------------------


def test_constructor_logs_info(caplog: pytest.LogCaptureFixture) -> None:
    """InfluxMetricsReader logs an INFO message on construction."""
    with caplog.at_level(logging.INFO, logger="backend.influx_reader"):
        InfluxMetricsReader(url="http://influx:8086", database="ems_data")
    assert any(
        "InfluxDB reader connected" in r.message
        and "http://influx:8086" in r.message
        and "ems_data" in r.message
        for r in caplog.records
    )


def test_constructor_new_signature_accepted() -> None:
    """InfluxMetricsReader accepts (url, database, username, password)."""
    import inspect
    sig = inspect.signature(InfluxMetricsReader.__init__)
    assert "url" in sig.parameters
    assert "database" in sig.parameters
    assert "client" not in sig.parameters
    assert "bucket" not in sig.parameters
    assert "org" not in sig.parameters


# ---------------------------------------------------------------------------
# _flux_duration_to_influxql
# ---------------------------------------------------------------------------


def test_flux_to_influxql_now() -> None:
    """'now()' passes through unchanged."""
    from backend.influx_reader import _flux_duration_to_influxql
    assert _flux_duration_to_influxql("now()") == "now()"


def test_flux_to_influxql_negative_duration() -> None:
    """'-1h' → 'now() - 1h'."""
    from backend.influx_reader import _flux_duration_to_influxql
    assert _flux_duration_to_influxql("-1h") == "now() - 1h"


def test_flux_to_influxql_negative_days() -> None:
    """-14d → 'now() - 14d'."""
    from backend.influx_reader import _flux_duration_to_influxql
    assert _flux_duration_to_influxql("-14d") == "now() - 14d"


# ---------------------------------------------------------------------------
# query_range — InfluxQL construction
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_range_influxql_contains_measurement() -> None:
    """query_range InfluxQL includes the measurement name."""
    reader = _make_reader(_v1_response(None))
    await reader.query_range("ems_system", "-1h", "now()")
    call_kwargs = reader._http.get.call_args
    params = call_kwargs.kwargs.get("params", {})
    q = params.get("q", "")
    assert "ems_system" in q


@pytest.mark.anyio
async def test_query_range_influxql_contains_start() -> None:
    """query_range InfluxQL WHERE clause contains translated start."""
    reader = _make_reader(_v1_response(None))
    await reader.query_range("ems_system", "-2h", "now()")
    params = reader._http.get.call_args.kwargs.get("params", {})
    q = params.get("q", "")
    assert "now() - 2h" in q


@pytest.mark.anyio
async def test_query_range_influxql_uses_select_star() -> None:
    """query_range uses SELECT * to fetch all fields."""
    reader = _make_reader(_v1_response(None))
    await reader.query_range("ems_system", "-1h", "now()")
    params = reader._http.get.call_args.kwargs.get("params", {})
    assert "SELECT *" in params.get("q", "")


@pytest.mark.anyio
async def test_query_range_db_param_sent() -> None:
    """GET request includes db=<database> as a query param."""
    reader = _make_reader(_v1_response(None), database="mydb")
    await reader.query_range("ems_system", "-1h", "now()")
    params = reader._http.get.call_args.kwargs.get("params", {})
    assert params.get("db") == "mydb"


# ---------------------------------------------------------------------------
# query_range — record flattening (wide → long pivot)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_range_returns_empty_list_when_no_series() -> None:
    """query_range returns [] when the response has no series."""
    reader = _make_reader(_v1_response(None))
    result = await reader.query_range("ems_system", "-1h", "now()")
    assert result == []


@pytest.mark.anyio
async def test_query_range_pivots_wide_to_long() -> None:
    """Wide row (time, fieldA, fieldB) → two long-format dicts."""
    series = _wide_series(
        "ems_system",
        fields=["combined_soc_pct", "combined_power_w"],
        rows=[["2026-01-01T00:00:00Z", 62.5, 1500.0]],
    )
    reader = _make_reader(_v1_response([series]))
    result = await reader.query_range("ems_system", "-1h", "now()")

    assert len(result) == 2
    times = {r["time"] for r in result}
    fields = {r["field"] for r in result}
    assert "2026-01-01T00:00:00Z" in times
    assert "combined_soc_pct" in fields
    assert "combined_power_w" in fields


@pytest.mark.anyio
async def test_query_range_each_dict_has_time_field_value() -> None:
    """Each record dict has exactly {time, field, value} keys."""
    series = _wide_series(
        "ems_system",
        fields=["combined_soc_pct"],
        rows=[["2026-01-01T00:00:00Z", 62.5]],
    )
    reader = _make_reader(_v1_response([series]))
    result = await reader.query_range("ems_system", "-1h", "now()")
    assert len(result) == 1
    assert set(result[0].keys()) == {"time", "field", "value"}


@pytest.mark.anyio
async def test_query_range_skips_null_values() -> None:
    """Null field values in a wide row are skipped (not emitted)."""
    series = _wide_series(
        "ems_system",
        fields=["combined_soc_pct", "combined_power_w"],
        rows=[["2026-01-01T00:00:00Z", 62.5, None]],
    )
    reader = _make_reader(_v1_response([series]))
    result = await reader.query_range("ems_system", "-1h", "now()")
    # Only combined_soc_pct emitted; combined_power_w was None
    assert len(result) == 1
    assert result[0]["field"] == "combined_soc_pct"


@pytest.mark.anyio
async def test_query_range_multiple_rows() -> None:
    """Multiple rows → one long-format dict per (row, field) pair."""
    series = _wide_series(
        "ems_system",
        fields=["soc"],
        rows=[
            ["2026-01-01T00:00:00Z", 60.0],
            ["2026-01-01T00:00:05Z", 61.0],
            ["2026-01-01T00:00:10Z", 62.0],
        ],
    )
    reader = _make_reader(_v1_response([series]))
    result = await reader.query_range("ems_system", "-1h", "now()")
    assert len(result) == 3


# ---------------------------------------------------------------------------
# query_range — error handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_range_swallows_exception_returns_empty() -> None:
    """Exception from httpx.get() → [] returned, no raise."""
    reader = _make_reader(RuntimeError("connection refused"))
    result = await reader.query_range("ems_system", "-1h", "now()")
    assert result == []


@pytest.mark.anyio
async def test_query_range_exception_logged_as_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """query_range logs WARNING when exception occurs."""
    reader = _make_reader(OSError("network error"))
    with caplog.at_level(logging.WARNING, logger="backend.influx_reader"):
        await reader.query_range("ems_system", "-1h", "now()")
    assert any("influx query failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# query_latest — InfluxQL construction
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_latest_influxql_order_desc_limit_1() -> None:
    """query_latest InfluxQL includes ORDER BY time DESC LIMIT 1."""
    reader = _make_reader(_v1_response(None))
    await reader.query_latest("ems_system")
    params = reader._http.get.call_args.kwargs.get("params", {})
    q = params.get("q", "")
    assert "ORDER BY time DESC" in q
    assert "LIMIT 1" in q


@pytest.mark.anyio
async def test_query_latest_influxql_contains_measurement() -> None:
    """query_latest InfluxQL includes the measurement name."""
    reader = _make_reader(_v1_response(None))
    await reader.query_latest("ems_tariff")
    params = reader._http.get.call_args.kwargs.get("params", {})
    assert "ems_tariff" in params.get("q", "")


# ---------------------------------------------------------------------------
# query_latest — return value
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_latest_returns_none_when_no_series() -> None:
    """query_latest returns None when response has no series."""
    reader = _make_reader(_v1_response(None))
    result = await reader.query_latest("ems_system")
    assert result is None


@pytest.mark.anyio
async def test_query_latest_returns_none_when_series_empty_values() -> None:
    """query_latest returns None when series has no values rows."""
    series = {"name": "ems_system", "columns": ["time", "soc"], "values": []}
    reader = _make_reader(_v1_response([series]))
    result = await reader.query_latest("ems_system")
    assert result is None


@pytest.mark.anyio
async def test_query_latest_returns_first_field_dict() -> None:
    """query_latest returns a dict with the first non-null field."""
    series = _wide_series(
        "ems_system",
        fields=["combined_soc_pct"],
        rows=[["2026-01-01T12:00:00Z", 75.0]],
    )
    reader = _make_reader(_v1_response([series]))
    result = await reader.query_latest("ems_system")
    assert result is not None
    assert result["time"] == "2026-01-01T12:00:00Z"
    assert result["field"] == "combined_soc_pct"
    assert result["value"] == pytest.approx(75.0)


@pytest.mark.anyio
async def test_query_latest_returns_dict_not_list() -> None:
    """query_latest returns a dict, not a list."""
    series = _wide_series(
        "ems_system",
        fields=["soc"],
        rows=[["2026-01-01T12:00:00Z", 55.0]],
    )
    reader = _make_reader(_v1_response([series]))
    result = await reader.query_latest("ems_system")
    assert isinstance(result, dict)


@pytest.mark.anyio
async def test_query_latest_skips_null_fields_returns_next() -> None:
    """query_latest skips null values and returns the first non-null field."""
    series = _wide_series(
        "ems_system",
        fields=["field_null", "field_ok"],
        rows=[["2026-01-01T12:00:00Z", None, 42.0]],
    )
    reader = _make_reader(_v1_response([series]))
    result = await reader.query_latest("ems_system")
    assert result is not None
    assert result["field"] == "field_ok"
    assert result["value"] == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# query_latest — error handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_latest_swallows_exception_returns_none() -> None:
    """Exception from httpx → None returned, no raise."""
    reader = _make_reader(ConnectionError("timeout"))
    result = await reader.query_latest("ems_system")
    assert result is None


@pytest.mark.anyio
async def test_query_latest_exception_logged_as_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """query_latest logs WARNING when exception occurs."""
    reader = _make_reader(OSError("connection refused"))
    with caplog.at_level(logging.WARNING, logger="backend.influx_reader"):
        await reader.query_latest("ems_system")
    assert any("influx query failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Helpers for query_consumption_history tests
# ---------------------------------------------------------------------------


def _consumption_series(dates: list[str], mean_w: float = -500.0) -> dict:
    """Build a grouped InfluxQL series for query_consumption_history.

    ``dates`` are ISO-8601 date strings (e.g. '2026-01-05').
    ``mean_w`` is the mean combined_power_w value — negative for discharge.
    """
    rows = [[f"{d}T12:00:00Z", mean_w] for d in dates]
    return {
        "name": "ems_system",
        "columns": ["time", "mean"],
        "values": rows,
    }


def _fourteen_dates() -> list[str]:
    """Return 14 consecutive ISO date strings starting 2026-01-01."""
    return [
        (datetime.date(2026, 1, 1) + datetime.timedelta(days=i)).isoformat()
        for i in range(14)
    ]


# ---------------------------------------------------------------------------
# query_consumption_history — InfluxQL construction
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consumption_history_influxql_contains_measurement() -> None:
    """InfluxQL includes the ems_system measurement."""
    reader = _make_reader(_v1_response([_consumption_series(_fourteen_dates())]))
    await reader.query_consumption_history()
    params = reader._http.get.call_args.kwargs.get("params", {})
    assert "ems_system" in params.get("q", "")


@pytest.mark.anyio
async def test_consumption_history_influxql_contains_range() -> None:
    """Default days=14 produces a now() - 14d range in the InfluxQL."""
    reader = _make_reader(_v1_response([_consumption_series(_fourteen_dates())]))
    await reader.query_consumption_history()
    params = reader._http.get.call_args.kwargs.get("params", {})
    assert "14d" in params.get("q", "")


@pytest.mark.anyio
async def test_consumption_history_influxql_custom_days() -> None:
    """Passing days=7 produces a 7d range in the InfluxQL."""
    seven_dates = [
        (datetime.date(2026, 1, 1) + datetime.timedelta(days=i)).isoformat()
        for i in range(7)
    ]
    reader = _make_reader(_v1_response([_consumption_series(seven_dates)]))
    await reader.query_consumption_history(days=7)
    params = reader._http.get.call_args.kwargs.get("params", {})
    q = params.get("q", "")
    assert "7d" in q
    assert "14d" not in q


@pytest.mark.anyio
async def test_consumption_history_influxql_group_by_1d() -> None:
    """InfluxQL uses GROUP BY time(1d) for daily aggregation."""
    reader = _make_reader(_v1_response([_consumption_series(_fourteen_dates())]))
    await reader.query_consumption_history()
    params = reader._http.get.call_args.kwargs.get("params", {})
    assert "time(1d)" in params.get("q", "")


@pytest.mark.anyio
async def test_consumption_history_influxql_negative_filter() -> None:
    """InfluxQL WHERE clause filters combined_power_w < 0."""
    reader = _make_reader(_v1_response([_consumption_series(_fourteen_dates())]))
    await reader.query_consumption_history()
    params = reader._http.get.call_args.kwargs.get("params", {})
    assert "combined_power_w < 0" in params.get("q", "")


@pytest.mark.anyio
async def test_consumption_history_influxql_contains_tz_comment() -> None:
    """Timezone label appears in InfluxQL query string."""
    reader = _make_reader(_v1_response([_consumption_series(_fourteen_dates())]))
    await reader.query_consumption_history(tz="Europe/Berlin")
    params = reader._http.get.call_args.kwargs.get("params", {})
    assert "Europe/Berlin" in params.get("q", "")


# ---------------------------------------------------------------------------
# query_consumption_history — return type / not-None contract
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consumption_history_returns_consumption_forecast_type() -> None:
    """Result is always a ConsumptionForecast instance."""
    from backend.schedule_models import ConsumptionForecast

    reader = _make_reader(_v1_response([_consumption_series(_fourteen_dates())]))
    result = await reader.query_consumption_history()
    assert isinstance(result, ConsumptionForecast)


@pytest.mark.anyio
async def test_consumption_history_not_none() -> None:
    """query_consumption_history never returns None."""
    reader = _make_reader(_v1_response(None))
    result = await reader.query_consumption_history()
    assert result is not None


# ---------------------------------------------------------------------------
# query_consumption_history — happy path (≥7 days)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consumption_history_fallback_false_with_sufficient_data() -> None:
    """fallback_used=False when records span 14 distinct dates."""
    reader = _make_reader(_v1_response([_consumption_series(_fourteen_dates())]))
    result = await reader.query_consumption_history()
    assert result.fallback_used is False


@pytest.mark.anyio
async def test_consumption_history_kwh_by_weekday_keys_are_ints() -> None:
    """kwh_by_weekday keys are integers 0–6."""
    reader = _make_reader(_v1_response([_consumption_series(_fourteen_dates())]))
    result = await reader.query_consumption_history()
    assert result.fallback_used is False
    for key in result.kwh_by_weekday:
        assert isinstance(key, int)
        assert 0 <= key <= 6


@pytest.mark.anyio
async def test_consumption_history_today_expected_kwh_from_weekday_map() -> None:
    """today_expected_kwh matches kwh_by_weekday[today.weekday()] when present."""
    reader = _make_reader(_v1_response([_consumption_series(_fourteen_dates())]))
    result = await reader.query_consumption_history()
    today_wd = datetime.date.today().weekday()
    if today_wd in result.kwh_by_weekday:
        assert result.today_expected_kwh == pytest.approx(result.kwh_by_weekday[today_wd])


@pytest.mark.anyio
async def test_consumption_history_days_of_history_count() -> None:
    """days_of_history equals the number of distinct calendar dates."""
    reader = _make_reader(_v1_response([_consumption_series(_fourteen_dates())]))
    result = await reader.query_consumption_history()
    assert result.days_of_history == 14


@pytest.mark.anyio
async def test_consumption_history_kwh_conversion() -> None:
    """mean=-500 W over 1 day → 12.0 kWh (abs(500) * 24 / 1000)."""
    reader = _make_reader(
        _v1_response([_consumption_series(_fourteen_dates(), mean_w=-500.0)])
    )
    result = await reader.query_consumption_history()
    assert result.fallback_used is False
    for kwh in result.kwh_by_weekday.values():
        assert kwh == pytest.approx(12.0)


@pytest.mark.anyio
async def test_consumption_history_null_buckets_skipped() -> None:
    """Null-value rows (empty GROUP BY buckets) are skipped, not counted."""
    # 14 real dates + 2 null rows
    dates = _fourteen_dates()
    rows = [[f"{d}T12:00:00Z", -500.0] for d in dates]
    # Null rows from GROUP BY time(1d) with no data:
    rows += [["2026-01-20T00:00:00Z", None], ["2026-01-21T00:00:00Z", None]]
    series = {"name": "ems_system", "columns": ["time", "mean"], "values": rows}
    reader = _make_reader(_v1_response([series]))
    result = await reader.query_consumption_history()
    # Only 14 real dates counted; nulls not added to dates_seen
    assert result.days_of_history == 14
    assert result.fallback_used is False


# ---------------------------------------------------------------------------
# query_consumption_history — fallback path (< 7 days)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consumption_history_fallback_true_with_few_records() -> None:
    """fallback_used=True when records span only 3 distinct dates."""
    three_dates = [
        (datetime.date(2026, 1, 1) + datetime.timedelta(days=i)).isoformat()
        for i in range(3)
    ]
    reader = _make_reader(_v1_response([_consumption_series(three_dates)]))
    result = await reader.query_consumption_history()
    assert result.fallback_used is True


@pytest.mark.anyio
async def test_consumption_history_fallback_kwh_by_weekday_empty_on_few_records() -> None:
    """kwh_by_weekday == {} when fallback is triggered (< 7 days)."""
    few_dates = [
        (datetime.date(2026, 3, 1) + datetime.timedelta(days=i)).isoformat()
        for i in range(4)
    ]
    reader = _make_reader(_v1_response([_consumption_series(few_dates)]))
    result = await reader.query_consumption_history()
    assert result.kwh_by_weekday == {}


@pytest.mark.anyio
async def test_consumption_history_no_records_returns_fallback() -> None:
    """No series at all → fallback_used=True."""
    reader = _make_reader(_v1_response(None))
    result = await reader.query_consumption_history()
    assert result.fallback_used is True


@pytest.mark.anyio
async def test_consumption_history_days_of_history_zero_on_no_records() -> None:
    """days_of_history==0 when there are no records."""
    reader = _make_reader(_v1_response(None))
    result = await reader.query_consumption_history()
    assert result.days_of_history == 0


# ---------------------------------------------------------------------------
# query_consumption_history — seasonal fallback constants
# ---------------------------------------------------------------------------


def test_seasonal_fallback_winter_month() -> None:
    """Month 12 (December) → 35.0 kWh/day."""
    assert _seasonal_fallback_kwh(datetime.date(2026, 12, 15)) == pytest.approx(35.0)


def test_seasonal_fallback_winter_month_january() -> None:
    """Month 1 (January) → 35.0 kWh/day."""
    assert _seasonal_fallback_kwh(datetime.date(2026, 1, 10)) == pytest.approx(35.0)


def test_seasonal_fallback_summer_month() -> None:
    """Month 7 (July) → 15.0 kWh/day."""
    assert _seasonal_fallback_kwh(datetime.date(2026, 7, 1)) == pytest.approx(15.0)


def test_seasonal_fallback_shoulder_month() -> None:
    """Month 4 (April) → 25.0 kWh/day."""
    assert _seasonal_fallback_kwh(datetime.date(2026, 4, 15)) == pytest.approx(25.0)


def test_seasonal_fallback_shoulder_march() -> None:
    """Month 3 (March) → 25.0 kWh/day (shoulder season)."""
    assert _seasonal_fallback_kwh(datetime.date(2026, 3, 20)) == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# query_consumption_history — error handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consumption_history_swallows_exception() -> None:
    """Exception from httpx → ConsumptionForecast returned, no raise."""
    from backend.schedule_models import ConsumptionForecast

    reader = _make_reader(RuntimeError("influx down"))
    result = await reader.query_consumption_history()
    assert isinstance(result, ConsumptionForecast)


@pytest.mark.anyio
async def test_consumption_history_exception_fallback_used_true() -> None:
    """Exception path always returns fallback_used=True."""
    reader = _make_reader(ConnectionError("timeout"))
    result = await reader.query_consumption_history()
    assert result.fallback_used is True


@pytest.mark.anyio
async def test_consumption_history_exception_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Exception path logs WARNING with 'influx consumption query failed'."""
    reader = _make_reader(OSError("network unreachable"))
    with caplog.at_level(logging.WARNING, logger="backend.influx_reader"):
        await reader.query_consumption_history()
    assert any("influx consumption query failed" in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_consumption_history_exception_days_of_history_zero() -> None:
    """Exception path returns days_of_history=0."""
    reader = _make_reader(RuntimeError("boom"))
    result = await reader.query_consumption_history()
    assert result.days_of_history == 0


@pytest.mark.anyio
async def test_consumption_history_exception_kwh_by_weekday_empty() -> None:
    """Exception path returns kwh_by_weekday={}."""
    reader = _make_reader(ValueError("bad query"))
    result = await reader.query_consumption_history()
    assert result.kwh_by_weekday == {}
