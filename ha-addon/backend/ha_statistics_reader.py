"""HaStatisticsReader — async read-only SQLite reader for HA long-term statistics.

Reads the ``statistics`` and ``statistics_meta`` tables from the Home Assistant
``home-assistant_v2.db`` database to extract per-entity hourly timeseries for
use by the ML consumption forecaster.

Design constraints
------------------
- All SQLite I/O is off-loaded to a thread-pool via ``anyio.to_thread.run_sync``
  so it never blocks the asyncio or Trio event loop.
- The DB is opened in read-only URI mode (``file:...?mode=ro``) to avoid
  accidental writes or WAL file creation.
- Every public method is *fire-and-forget* on error: exceptions are caught,
  logged as WARNING, and an empty / None result is returned.  This ensures
  the forecaster degrades gracefully when the DB is absent or corrupt.

Observability
-------------
- Logger name: ``ems.ha_statistics_reader``
- WARNING ``"entity <id> not found in HA statistics — skipping"`` — no rows
  in ``statistics_meta`` for the requested ``statistic_id``.
- WARNING ``"HA DB schema version <v> unrecognised — read may fail"`` — schema
  guard fired; forecaster will attempt to read anyway.
- WARNING ``"HaStatisticsReader.read_entity_hourly failed: <exc>"`` — any
  other SQLite error; returns ``[]``.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import anyio.to_thread

logger = logging.getLogger("ems.ha_statistics_reader")

# Known-good HA DB schema versions.  If the detected version is not in this
# set we emit a WARNING but continue — the schema hasn't changed materially
# for statistics tables across these releases.
_KNOWN_SCHEMA_VERSIONS: frozenset[str] = frozenset(
    [
        "2023.11.0", "2023.12.0",
        "2024.1.0", "2024.2.0", "2024.3.0", "2024.4.0",
        "2024.5.0", "2024.6.0", "2024.7.0", "2024.8.0",
        "2024.9.0", "2024.10.0", "2024.11.0", "2024.12.0",
        "2025.1.0", "2025.2.0", "2025.3.0", "2025.4.0",
        "2025.5.0", "2025.6.0", "2025.7.0", "2025.8.0",
        "2025.9.0", "2025.10.0", "2025.11.0", "2025.12.0",
        "2026.1.0", "2026.2.0", "2026.3.0",
    ]
)


class HaStatisticsReader:
    """Async read-only SQLite reader for HA long-term statistics.

    Parameters
    ----------
    db_path:
        Filesystem path to ``home-assistant_v2.db``.  No I/O is performed
        at construction time — the file is opened lazily on each query.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def read_entity_hourly(
        self,
        statistic_id: str,
        days: int = 90,
    ) -> list[tuple[datetime, float]]:
        """Return hourly ``(utc_datetime, value)`` tuples for *statistic_id*.

        Reads the ``mean`` column from the ``statistics`` table (appropriate
        for power sensors reported in W).  Results are sorted ascending by
        timestamp and span the last *days* calendar days.

        Parameters
        ----------
        statistic_id:
            HA entity ID / statistic ID, e.g.
            ``"sensor.warmepumpe_total_active_power"``.
        days:
            Rolling window in calendar days (default 90).

        Returns
        -------
        list[tuple[datetime, float]]
            Sorted list of ``(aware UTC datetime, value)`` tuples.
            Returns ``[]`` on any error or when the entity has no data.
        """
        try:
            return await anyio.to_thread.run_sync(
                self._read_entity_hourly_sync, statistic_id, days
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "HaStatisticsReader.read_entity_hourly failed for %s: %s",
                statistic_id,
                exc,
            )
            return []

    async def check_schema_version(self) -> Optional[str]:
        """Return the latest HA DB schema version string, or ``None``.

        Queries the ``schema_changes`` table if it exists.  Emits a WARNING
        when the version is not in the known-good set.  Does not raise.

        Returns
        -------
        str | None
            Latest schema version string, or ``None`` when the table does not
            exist or the DB cannot be opened.
        """
        try:
            return await anyio.to_thread.run_sync(
                self._check_schema_version_sync
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "HaStatisticsReader.check_schema_version failed: %s", exc
            )
            return None

    # ------------------------------------------------------------------
    # Synchronous helpers (run inside a thread — no async calls allowed)
    # ------------------------------------------------------------------

    def _read_entity_hourly_sync(
        self,
        statistic_id: str,
        days: int,
    ) -> list[tuple[datetime, float]]:
        """Blocking SQLite read — must only be called from a thread."""
        try:
            conn = sqlite3.connect(
                f"file:{self._db_path}?mode=ro", uri=True, check_same_thread=False
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "HaStatisticsReader: cannot open DB at %s: %s",
                self._db_path,
                exc,
            )
            return []

        try:
            with conn:
                # Verify the entity exists in statistics_meta
                cur = conn.execute(
                    "SELECT id FROM statistics_meta WHERE statistic_id = ? LIMIT 1",
                    (statistic_id,),
                )
                row = cur.fetchone()
                if row is None:
                    logger.warning(
                        "ConsumptionForecaster: entity %s not found in HA statistics"
                        " — skipping",
                        statistic_id,
                    )
                    return []

                # Fetch hourly rows from the statistics table using mean column
                rows = conn.execute(
                    """
                    SELECT start, mean
                    FROM statistics
                    WHERE metadata_id IN (
                        SELECT id FROM statistics_meta WHERE statistic_id = ?
                    )
                    AND start > datetime('now', ? || ' days')
                    AND mean IS NOT NULL
                    ORDER BY start ASC
                    """,
                    (statistic_id, f"-{days}"),
                ).fetchall()

            result: list[tuple[datetime, float]] = []
            for start_str, value in rows:
                try:
                    # HA stores start as "YYYY-MM-DD HH:MM:SS[.fff]" UTC
                    if isinstance(start_str, str):
                        dt = datetime.fromisoformat(start_str).replace(
                            tzinfo=timezone.utc
                        )
                    else:
                        # Some builds return a float epoch
                        dt = datetime.fromtimestamp(float(start_str), tz=timezone.utc)
                    result.append((dt, float(value)))
                except (ValueError, TypeError, OSError):
                    continue
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "HaStatisticsReader.read_entity_hourly failed: %s", exc
            )
            return []
        finally:
            conn.close()

    def _check_schema_version_sync(self) -> Optional[str]:
        """Blocking schema-version check — must only be called from a thread."""
        try:
            conn = sqlite3.connect(
                f"file:{self._db_path}?mode=ro", uri=True, check_same_thread=False
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "HaStatisticsReader: cannot open DB for schema check: %s", exc
            )
            return None

        try:
            with conn:
                # Check if schema_changes table exists
                row = conn.execute(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='table' AND name='schema_changes'"
                ).fetchone()
                if row is None:
                    return None

                version_row = conn.execute(
                    "SELECT schema_version FROM schema_changes"
                    " ORDER BY change_id DESC LIMIT 1"
                ).fetchone()

                if version_row is None:
                    return None

                version = str(version_row[0])
                if version not in _KNOWN_SCHEMA_VERSIONS:
                    logger.warning(
                        "ConsumptionForecaster: HA DB schema version %s"
                        " unrecognised — read may fail",
                        version,
                    )
                return version
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "HaStatisticsReader.check_schema_version failed: %s", exc
            )
            return None
        finally:
            conn.close()
