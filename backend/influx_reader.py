"""InfluxMetricsReader -- async Flux query wrapper for EMS metrics history.

TODO: This module still uses the InfluxDB v2 ``influxdb_client`` Flux query
API.  It needs migration to InfluxDB v1 InfluxQL queries via direct HTTP
(``GET /query?db=<database>&q=<influxql>``).  Until then, the reader is
disabled in ``main.py`` and callers receive ``None``.

Wraps the async ``influxdb_client`` query API and provides two read surfaces:

``query_range(measurement, start, stop)``
    Returns a flattened list of ``{"time", "field", "value"}`` dicts for all
    records in the given time window.

``query_latest(measurement)``
    Returns the single most-recent record dict for the measurement, or ``None``
    if the measurement has no data.

Both methods are fire-and-forget on errors: exceptions are swallowed after a
``WARNING`` log so a query failure never surfaces to the caller.  The caller
receives an empty list / ``None`` respectively.

Observability
-------------
- ``WARNING`` log on query failure: ``influx query failed: <exc>`` — grep this
  to detect InfluxDB connectivity or query-syntax issues at runtime.
"""
from __future__ import annotations

import datetime
import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from backend.schedule_models import ConsumptionForecast

if TYPE_CHECKING:
    from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

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


class InfluxMetricsReader:
    """Async Flux query client for EMS measurement history.

    Parameters
    ----------
    client:
        Live ``InfluxDBClientAsync`` instance (constructed in lifespan).
    org:
        InfluxDB organisation name.
    bucket:
        InfluxDB bucket to query.
    """

    def __init__(
        self,
        client: "InfluxDBClientAsync",
        org: str,
        bucket: str,
    ) -> None:
        self._client = client
        self._org = org
        self._bucket = bucket
        self._query_api = client.query_api()

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

        Parameters
        ----------
        measurement:
            InfluxDB measurement name, e.g. ``"ems_system"``.
        start:
            Flux-compatible start value, e.g. ``"-1h"`` or an RFC3339 string.
        stop:
            Flux-compatible stop value, e.g. ``"now()"`` or an RFC3339 string.

        Returns
        -------
        list[dict]
            Flat list of ``{"time": str, "field": str, "value": Any}`` dicts.
            Returns ``[]`` on any query error (exception is logged at WARNING).
        """
        flux = (
            f'from(bucket:"{self._bucket}")'
            f" |> range(start:{start}, stop:{stop})"
            f' |> filter(fn: (r) => r._measurement == "{measurement}")'
        )
        try:
            tables = await self._query_api.query(query=flux, org=self._org)
            result: list[dict] = []
            for table in tables:
                for record in table.records:
                    result.append(
                        {
                            "time": str(record.get_time()),
                            "field": record.get_field(),
                            "value": record.get_value(),
                        }
                    )
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
            Single ``{"time": str, "field": str, "value": Any}`` dict, or
            ``None`` if the measurement has no data or the query fails.
        """
        flux = (
            f'from(bucket:"{self._bucket}")'
            " |> range(start:-30d)"
            f' |> filter(fn: (r) => r._measurement == "{measurement}")'
            " |> last()"
            " |> limit(n:1)"
        )
        try:
            tables = await self._query_api.query(query=flux, org=self._org)
            for table in tables:
                for record in table.records:
                    return {
                        "time": str(record.get_time()),
                        "field": record.get_field(),
                        "value": record.get_value(),
                    }
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
        consumption proxy.  Weekday grouping is done in Python, not in Flux.

        Parameters
        ----------
        days:
            Rolling window in calendar days (default 14).
        tz:
            Timezone label surfaced in the Flux query comment for auditability
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
        flux = (
            f'from(bucket:"{self._bucket}")'
            f" |> range(start:-{days}d)"
            f' |> filter(fn: (r) => r._measurement == "ems_system")'
            f' |> filter(fn: (r) => r._field == "combined_power_w")'
            f" |> filter(fn: (r) => r._value < 0.0)"
            f" |> aggregateWindow(every: 1d, fn: mean, createEmpty: false)"
            f" |> map(fn: (r) => ({{r with _value: -r._value}}))"
            f" // tz:{tz}"
        )
        try:
            tables = await self._query_api.query(query=flux, org=self._org)

            # Group daily means (positive watts after sign-flip) by weekday
            watts_by_weekday: dict[int, list[float]] = defaultdict(list)
            dates_seen: set[datetime.date] = set()

            for table in tables:
                for record in table.records:
                    raw_time = record.get_time()
                    if isinstance(raw_time, str):
                        dt = datetime.datetime.fromisoformat(
                            raw_time.replace("Z", "+00:00")
                        )
                    else:
                        # Already a datetime-like object
                        dt = raw_time  # type: ignore[assignment]

                    dates_seen.add(dt.date())
                    weekday = dt.weekday()  # 0=Monday…6=Sunday
                    value_w = record.get_value()
                    watts_by_weekday[weekday].append(float(value_w))

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

            # Convert mean watts → kWh (mean W × 24h / 1000)
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
