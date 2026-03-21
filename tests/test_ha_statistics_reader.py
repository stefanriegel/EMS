"""Tests for HaStatisticsReader — async SQLite reader for HA long-term statistics.

All tests use a real in-memory SQLite database populated with synthetic data.
No mocks are used for the DB layer; only the asyncio event loop is provided
by pytest-anyio.

Test coverage:
- Schema guard: check_schema_version returns string when table present, None otherwise
- Schema guard: unknown version triggers WARNING
- Missing entity: read_entity_hourly returns [] (not raises) when entity absent
- Non-existent DB: read_entity_hourly returns [] (not raises) when file missing
- Happy path: correct (datetime, float) tuples returned and sorted ascending
- Day filter: rows older than the requested window are excluded
"""
from __future__ import annotations

import sqlite3
import tempfile
import os
from datetime import datetime, timezone, timedelta

import pytest

from backend.ha_statistics_reader import HaStatisticsReader


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _create_ha_db(path: str, *, with_schema_changes: bool = True, schema_version: str = "2025.3.0") -> None:
    """Create a minimal HA statistics SQLite DB at *path*."""
    conn = sqlite3.connect(path)
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS statistics_meta (
                id INTEGER PRIMARY KEY,
                statistic_id TEXT NOT NULL,
                source TEXT
            );
            CREATE TABLE IF NOT EXISTS statistics (
                id INTEGER PRIMARY KEY,
                metadata_id INTEGER NOT NULL,
                start DATETIME NOT NULL,
                mean REAL,
                state REAL,
                min REAL,
                max REAL
            );
        """)
        if with_schema_changes:
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS schema_changes (
                    change_id INTEGER PRIMARY KEY,
                    schema_version TEXT NOT NULL
                );
                INSERT INTO schema_changes (schema_version) VALUES ('{schema_version}');
            """)
    conn.close()


def _insert_entity_rows(
    path: str,
    statistic_id: str,
    rows: list[tuple[str, float]],  # (iso_datetime_utc, mean)
) -> None:
    """Insert test statistics rows for a given entity."""
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO statistics_meta (statistic_id, source)"
            " VALUES (?, 'recorder')",
            (statistic_id,),
        )
        meta_id = conn.execute(
            "SELECT id FROM statistics_meta WHERE statistic_id = ?",
            (statistic_id,),
        ).fetchone()[0]
        conn.executemany(
            "INSERT INTO statistics (metadata_id, start, mean) VALUES (?, ?, ?)",
            [(meta_id, ts, val) for ts, val in rows],
        )
    conn.close()


def _make_hourly_rows(days: int, base_value: float = 1000.0) -> list[tuple[str, float]]:
    """Generate *days* × 24 hourly rows ending now."""
    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    rows = []
    for h in range(days * 24 - 1, -1, -1):
        ts = now - timedelta(hours=h)
        rows.append((ts.strftime("%Y-%m-%d %H:%M:%S"), base_value))
    return rows


# ---------------------------------------------------------------------------
# Tests: check_schema_version
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_check_schema_version_returns_string_when_table_present(tmp_path):
    """check_schema_version returns a non-None string when schema_changes exists."""
    db_path = str(tmp_path / "ha.db")
    _create_ha_db(db_path, with_schema_changes=True, schema_version="2025.3.0")
    reader = HaStatisticsReader(db_path)
    version = await reader.check_schema_version()
    assert version == "2025.3.0", f"Expected '2025.3.0', got {version!r}"


@pytest.mark.anyio
async def test_check_schema_version_returns_none_when_table_absent(tmp_path):
    """check_schema_version returns None when schema_changes table does not exist."""
    db_path = str(tmp_path / "ha.db")
    _create_ha_db(db_path, with_schema_changes=False)
    reader = HaStatisticsReader(db_path)
    version = await reader.check_schema_version()
    assert version is None


@pytest.mark.anyio
async def test_check_schema_version_warns_on_unknown_version(tmp_path, caplog):
    """check_schema_version emits a WARNING for unrecognised schema versions."""
    db_path = str(tmp_path / "ha.db")
    _create_ha_db(db_path, with_schema_changes=True, schema_version="1999.1.0")
    reader = HaStatisticsReader(db_path)
    import logging
    with caplog.at_level(logging.WARNING, logger="ems.ha_statistics_reader"):
        version = await reader.check_schema_version()
    assert version == "1999.1.0"
    assert "unrecognised" in caplog.text.lower() or "1999.1.0" in caplog.text


@pytest.mark.anyio
async def test_check_schema_version_returns_none_when_db_missing():
    """check_schema_version returns None (not raises) when DB file does not exist."""
    reader = HaStatisticsReader("/nonexistent/path/ha.db")
    version = await reader.check_schema_version()
    assert version is None


# ---------------------------------------------------------------------------
# Tests: read_entity_hourly — error paths
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_read_entity_hourly_returns_empty_when_db_missing():
    """read_entity_hourly returns [] when the DB file does not exist."""
    reader = HaStatisticsReader("/nonexistent/path/ha.db")
    result = await reader.read_entity_hourly("sensor.some_entity")
    assert result == []


@pytest.mark.anyio
async def test_read_entity_hourly_returns_empty_when_entity_not_found(tmp_path):
    """read_entity_hourly returns [] when entity ID not in statistics_meta."""
    db_path = str(tmp_path / "ha.db")
    _create_ha_db(db_path)
    reader = HaStatisticsReader(db_path)
    result = await reader.read_entity_hourly("sensor.nonexistent_entity")
    assert result == []


@pytest.mark.anyio
async def test_read_entity_hourly_logs_warning_when_entity_not_found(tmp_path, caplog):
    """read_entity_hourly logs a WARNING mentioning the entity when not found."""
    db_path = str(tmp_path / "ha.db")
    _create_ha_db(db_path)
    reader = HaStatisticsReader(db_path)
    import logging
    with caplog.at_level(logging.WARNING, logger="ems.ha_statistics_reader"):
        await reader.read_entity_hourly("sensor.missing_entity_xyz")
    assert "missing_entity_xyz" in caplog.text or "not found" in caplog.text


# ---------------------------------------------------------------------------
# Tests: read_entity_hourly — happy path
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_read_entity_hourly_returns_correct_tuples(tmp_path):
    """read_entity_hourly returns (datetime, float) tuples sorted ascending."""
    db_path = str(tmp_path / "ha.db")
    _create_ha_db(db_path)
    rows = _make_hourly_rows(days=5, base_value=1234.5)
    _insert_entity_rows(db_path, "sensor.heat_pump", rows)

    reader = HaStatisticsReader(db_path)
    result = await reader.read_entity_hourly("sensor.heat_pump", days=30)

    assert len(result) == 5 * 24
    for ts, val in result:
        assert isinstance(ts, datetime)
        assert ts.tzinfo is not None, "Returned datetime must be timezone-aware"
        assert val == pytest.approx(1234.5)

    # Sorted ascending
    timestamps = [ts for ts, _ in result]
    assert timestamps == sorted(timestamps)


@pytest.mark.anyio
async def test_read_entity_hourly_filters_by_days(tmp_path):
    """read_entity_hourly excludes rows older than the requested day window."""
    db_path = str(tmp_path / "ha.db")
    _create_ha_db(db_path)

    # Insert 30 days of data
    rows = _make_hourly_rows(days=30, base_value=500.0)
    _insert_entity_rows(db_path, "sensor.hp", rows)

    reader = HaStatisticsReader(db_path)
    # Request only 7 days
    result = await reader.read_entity_hourly("sensor.hp", days=7)

    # Should have approximately 7 * 24 rows (SQLite datetime arithmetic may
    # include boundary row, so allow up to 8 * 24)
    assert len(result) <= 8 * 24
    assert len(result) >= 6 * 24


@pytest.mark.anyio
async def test_read_entity_hourly_multiple_entities_independent(tmp_path):
    """Two entities in the same DB are read independently."""
    db_path = str(tmp_path / "ha.db")
    _create_ha_db(db_path)
    _insert_entity_rows(db_path, "sensor.entity_a", _make_hourly_rows(10, 100.0))
    _insert_entity_rows(db_path, "sensor.entity_b", _make_hourly_rows(10, 200.0))

    reader = HaStatisticsReader(db_path)
    result_a = await reader.read_entity_hourly("sensor.entity_a", days=30)
    result_b = await reader.read_entity_hourly("sensor.entity_b", days=30)

    assert all(v == pytest.approx(100.0) for _, v in result_a)
    assert all(v == pytest.approx(200.0) for _, v in result_b)
    assert len(result_a) == len(result_b) == 10 * 24
