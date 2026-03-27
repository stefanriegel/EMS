"""InfluxMetricsReader -- async InfluxQL query wrapper for EMS metrics history.

Uses the InfluxDB v1 HTTP query API (``GET /query?db=<database>&q=<influxql>``)
via a direct ``httpx.AsyncClient``.  No external InfluxDB client library required
— mirrors the implementation pattern of :class:`~backend.influx_writer.InfluxMetricsWriter`.

Three read surfaces are provided:

``query_range(measurement, start, stop)``
    Returns a flattened list of ``{"time", "field", "value"}`` dicts for all
    field values in the given time window.  InfluxQL returns wide-format rows
    (one column per field); this method pivots them to the long/flat format
    expected by callers.

``query_latest(measurement)``
    Returns the single most-recent record dict for the measurement, or ``None``
    if the measurement has no data.

``query_consumption_history(days, tz)``
    Returns a weekday-aware rolling mean of household consumption expressed as
    :class:`~backend.schedule_models.ConsumptionForecast`.

All methods are fire-and-forget on errors: exceptions are swallowed after a
``WARNING`` log so a query failure never surfaces to the caller.  The caller
receives an empty list / ``None`` / fallback ConsumptionForecast respectively.

Observability
-------------
- ``INFO  "InfluxDB reader connected -- url=... database=..."`` on construction.
- ``WARNING "influx query failed: <exc>"`` — connectivity or query-syntax error
  in ``query_range`` or ``query_latest``.
- ``WARNING "influx consumption query failed: <exc>"`` — error in
  ``query_consumption_history``.
- ``WARNING "consumption history: only N days of data, using seasonal fallback"``
  — cold-start or data-gap condition.
"""
from __future__ import annotations

import datetime
import logging
import urllib.parse
from collections import defaultdict

import httpx

from backend.schedule_models import ConsumptionForecast

logger = logging.getLogger(__name__)


def _seasonal_fallback_kwh(today: datetime.date) -> float:
    """Return seasonal household consumption estimate in kWh/day.

    Winter (Nov–Feb): 35.0, Summer (May–Aug): 15.0, Shoulder: 25.0.
    """
    if today.month in (11, 12, 1, 2):
        return 35.0
    if today.month in (5, 6, 7, 8):
        return 15.0
    return 25.0


def _flux_duration_to_influxql(value: str) -> str:
    """Translate a Flux-style duration to an InfluxQL time expression.

    Examples
    --------
    ``"-1h"``   → ``"now() - 1h"``
    ``"-14d"``  → ``"now() - 14d"``
    ``"now()"`` → ``"now()"``
    ``"-30d"``  → ``"now() - 30d"``
    """
    stripped = value.strip()
    if stripped == "now()":
        return "now()"
    if stripped.startswith("-"):
        # Insert a space between 'now()' and the negative duration for readability:
        # "-1h" → "now() - 1h"
        return f"now() - {stripped[1:]}"
    return stripped


class InfluxMetricsReader:
    """Async InfluxQL query client for EMS measurement history.

    Uses GET ``/query?db=<database>&q=<influxql>`` to read from InfluxDB v1.

    Parameters
    ----------
    url:
        Base URL of the InfluxDB instance (e.g. ``http://localhost:8086``).
    database:
        Target InfluxDB database name.
    username:
        Optional InfluxDB username for basic auth.
    password:
        Optional InfluxDB password for basic auth.
    """

    def __init__(
        self,
        url: str,
        database: str,
        username: str = "",
        password: str = "",
    ) -> None:
        self._url = url.rstrip("/")
        self._database = database
        self._query_url = f"{self._url}/query"

        auth: tuple[str, str] | None = None
        if username and password:
            auth = (username, password)

        self._http = httpx.AsyncClient(auth=auth, timeout=10.0)

        logger.info(
            "InfluxDB reader connected -- url=%s database=%s",
            self._url,
            database,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_query(self, influxql: str) -> dict:
        """Execute an InfluxQL query and return the parsed JSON response.

        Raises ``httpx.HTTPError`` on non-2xx responses.
        """
        params = {"db": self._database, "q": influxql}
        resp = await self._http.get(self._query_url, params=params)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _iter_series(response: dict):
        """Yield each series dict from an InfluxDB v1 JSON response.

        InfluxDB v1 response shape::

            {
              "results": [
                {
                  "series": [
                    {
                      "name": "measurement",
                      "columns": ["time", "col1", "col2"],
                      "values": [["2026-01-01T00:00:00Z", 1.0, 2.0], ...]
                    }
                  ]
                }
              ]
            }
        """
        for result in response.get("results", []):
            for series in result.get("series", []):
                yield series

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def query_range(
        self,
        measurement: str,
        start: str,
        stop: str,
    ) -> list[dict]:
        """Return all records in *[start, stop)* for *measurement*.

        InfluxQL returns wide-format rows (one column per field per timestamp).
        This method pivots them to the long/flat format::

            [{"time": str, "field": str, "value": Any}, ...]

        Parameters
        ----------
        measurement:
            InfluxDB measurement name, e.g. ``"ems_system"``.
        start:
            Flux-compatible start value, e.g. ``"-1h"`` or ``"now()"`` or an
            RFC3339 string.  Translated to InfluxQL time expression internally.
        stop:
            Flux-compatible stop value, e.g. ``"now()"`` or an RFC3339 string.

        Returns
        -------
        list[dict]
            Flat list of ``{"time": str, "field": str, "value": Any}`` dicts.
            Returns ``[]`` on any query error (exception is logged at WARNING).
        """
        start_expr = _flux_duration_to_influxql(start)
        stop_expr = _flux_duration_to_influxql(stop)

        if stop_expr == "now()":
            where = f"time >= {start_expr}"
        else:
            where = f"time >= {start_expr} AND time < {stop_expr}"

        influxql = f"SELECT * FROM {measurement} WHERE {where}"

        try:
            response = await self._run_query(influxql)
            result: list[dict] = []
            for series in self._iter_series(response):
                columns = series.get("columns", [])
                for row in series.get("values", []):
                    row_dict = dict(zip(columns, row))
                    time_val = str(row_dict.get("time", ""))
                    # Pivot: one dict per (timestamp, field) pair
                    for col, val in row_dict.items():
                        if col == "time":
                            continue
                        if val is None:
                            continue
                        result.append({"time": time_val, "field": col, "value": val})
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("influx query failed: %s", exc)
            return []

    async def query_latest(self, measurement: str) -> dict | None:
        """Return the most-recent record for *measurement*, or ``None``.

        Parameters
        ----------
        measurement:
            InfluxDB measurement name, e.g. ``"ems_system"``.

        Returns
        -------
        dict | None
            Single ``{"time": str, "field": str, "value": Any}`` dict
            (for the first non-null field in the most-recent row), or
            ``None`` if the measurement has no data or the query fails.
        """
        influxql = f"SELECT * FROM {measurement} ORDER BY time DESC LIMIT 1"
        try:
            response = await self._run_query(influxql)
            for series in self._iter_series(response):
                columns = series.get("columns", [])
                values = series.get("values", [])
                if not values:
                    continue
                row_dict = dict(zip(columns, values[0]))
                time_val = str(row_dict.get("time", ""))
                for col, val in row_dict.items():
                    if col == "time":
                        continue
                    if val is None:
                        continue
                    return {"time": time_val, "field": col, "value": val}
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("influx query failed: %s", exc)
            return None

    async def query_consumption_history(
        self,
        days: int = 14,
        tz: str = "Europe/Berlin",
    ) -> ConsumptionForecast:
        """Return a weekday-aware rolling mean of household consumption.

        Uses battery-discharge periods (``combined_power_w < 0``) as a
        consumption proxy.  Grouped by day via InfluxQL ``GROUP BY time(1d)``.

        Parameters
        ----------
        days:
            Rolling window in calendar days (default 14).
        tz:
            Timezone label included in the query comment for auditability
            (default ``"Europe/Berlin"``).

        Returns
        -------
        ConsumptionForecast
            Always returns a ``ConsumptionForecast`` — never ``None``,
            never raises.

        Observability
        -------------
        - ``WARNING "influx consumption query failed: <exc>"`` — InfluxDB error
        - ``WARNING "consumption history: only N days of data, using seasonal fallback"``
          — cold-start or data-gap condition; ``fallback_used=True`` in result
        """
        # InfluxQL: group daily means of discharge (negative combined_power_w)
        # The sign is kept negative; we flip it when computing kWh.
        # Comment embeds tz for ops auditability.
        influxql = (
            f"SELECT mean(combined_power_w) FROM ems_system "
            f"WHERE time >= now() - {days}d "
            f"AND combined_power_w < 0 "
            f"GROUP BY time(1d) -- tz:{tz}"
        )
        try:
            response = await self._run_query(influxql)

            watts_by_weekday: dict[int, list[float]] = defaultdict(list)
            dates_seen: set[datetime.date] = set()

            for series in self._iter_series(response):
                columns = series.get("columns", [])  # ["time", "mean"]
                for row in series.get("values", []):
                    row_dict = dict(zip(columns, row))
                    raw_time = row_dict.get("time")
                    mean_w = row_dict.get("mean")

                    # Skip null-valued buckets (no data in that day)
                    if raw_time is None or mean_w is None:
                        continue

                    # Parse the timestamp
                    if isinstance(raw_time, str):
                        dt = datetime.datetime.fromisoformat(
                            raw_time.replace("Z", "+00:00")
                        )
                    else:
                        dt = raw_time  # type: ignore[assignment]

                    dates_seen.add(dt.date())
                    weekday = dt.weekday()  # 0=Monday … 6=Sunday

                    # mean_w is negative (discharge); negate to get positive watts
                    watts_by_weekday[weekday].append(float(-mean_w))

            days_of_history = len(dates_seen)
            today = datetime.date.today()

            if days_of_history < 7:
                logger.warning(
                    "consumption history: only %d days of data, using seasonal fallback",
                    days_of_history,
                )
                return ConsumptionForecast(
                    kwh_by_weekday={},
                    today_expected_kwh=_seasonal_fallback_kwh(today),
                    days_of_history=days_of_history,
                    fallback_used=True,
                )

            # Convert mean watts → kWh (mean W × 24 h / 1000)
            kwh_by_weekday = {
                wd: (sum(vals) / len(vals)) * 24 / 1000
                for wd, vals in watts_by_weekday.items()
            }
            today_expected_kwh = kwh_by_weekday.get(
                today.weekday(), _seasonal_fallback_kwh(today)
            )
            return ConsumptionForecast(
                kwh_by_weekday=kwh_by_weekday,
                today_expected_kwh=today_expected_kwh,
                days_of_history=days_of_history,
                fallback_used=False,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning("influx consumption query failed: %s", exc)
            today = datetime.date.today()
            return ConsumptionForecast(
                kwh_by_weekday={},
                today_expected_kwh=_seasonal_fallback_kwh(today),
                days_of_history=0,
                fallback_used=True,
            )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
