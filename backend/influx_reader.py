"""InfluxMetricsReader — async Flux query wrapper for EMS metrics history (S05).

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

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

logger = logging.getLogger(__name__)


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
