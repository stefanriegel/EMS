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
