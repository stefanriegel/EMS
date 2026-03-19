"""InfluxDB metrics writer for the EMS control loop (S05).

Writes energy metrics to InfluxDB at ~5 s resolution.  Two measurements
are supported:

``ems_system``
    Per-cycle snapshot of the combined battery pool: SoC, power, setpoints,
    control state, and availability flags.

``ems_tariff``
    Current tariff rates: effective, Octopus, and Modul3 grid-fee.

Design decisions:
  - Both write methods are fire-and-forget: any ``Exception`` from the
    InfluxDB client is caught, logged as WARNING, and swallowed.  InfluxDB
    outages must never crash the orchestrator or affect ``/api/health``.
  - The InfluxDB token is **never** logged.  Only url/org/bucket appear in
    the INFO construction log.
  - ``write_system_state`` uses ``datetime.now(tz=timezone.utc)`` as the
    Point timestamp, not ``state.timestamp`` (which is ``time.monotonic()`` —
    a relative counter with no fixed epoch, not usable as an absolute
    wall-clock timestamp).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from influxdb_client import Point
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

from backend.unified_model import UnifiedPoolState

logger = logging.getLogger(__name__)


class InfluxMetricsWriter:
    """Async writer that persists EMS state to InfluxDB.

    Args:
        client: An open :class:`~influxdb_client.client.influxdb_client_async.InfluxDBClientAsync`
            instance.  The caller owns its lifecycle (open/close).
        bucket: Target InfluxDB bucket name.

    Logs at INFO level on construction (url/org/bucket — **not** token).
    Logs at WARNING level on any write failure (greppable: ``influx write failed``).
    """

    def __init__(self, client: InfluxDBClientAsync, bucket: str) -> None:
        self._client = client
        self._bucket = bucket
        self._write_api = client.write_api()

        # Log url/org/bucket at INFO so operators can confirm configuration at
        # startup.  The token is intentionally omitted — it must never appear
        # in logs.
        logger.info(
            "InfluxMetricsWriter initialised — url=%s org=%s bucket=%s",
            client.url,
            client.org,
            bucket,
        )

    async def write_system_state(self, state: UnifiedPoolState) -> None:
        """Write a single ``ems_system`` Point from *state*.

        Tags encode categorical / boolean attributes; fields carry numeric
        measurements.  All 8 schema fields are always written (no optionals).

        The Point timestamp uses ``datetime.now(tz=timezone.utc)``, not
        ``state.timestamp`` (monotonic counter), to produce an absolute
        wall-clock timestamp InfluxDB can store and query.

        Failures are caught and logged as WARNING — never raised to the caller.

        Args:
            state: The :class:`~backend.unified_model.UnifiedPoolState` snapshot
                from the current orchestrator cycle.
        """
        try:
            point = (
                Point("ems_system")
                .tag("control_state", state.control_state.value)
                .tag("huawei_available", "true" if state.huawei_available else "false")
                .tag("victron_available", "true" if state.victron_available else "false")
                .field("combined_soc_pct", float(state.combined_soc_pct))
                .field("huawei_soc_pct", float(state.huawei_soc_pct))
                .field("victron_soc_pct", float(state.victron_soc_pct))
                .field("combined_power_w", float(state.combined_power_w))
                .field("huawei_discharge_setpoint_w", int(state.huawei_discharge_setpoint_w))
                .field("victron_discharge_setpoint_w", int(state.victron_discharge_setpoint_w))
                .field("huawei_charge_headroom_w", int(state.huawei_charge_headroom_w))
                .field("victron_charge_headroom_w", float(state.victron_charge_headroom_w))
                .time(datetime.now(tz=timezone.utc))
            )
            await self._write_api.write(bucket=self._bucket, record=point)
        except Exception as exc:  # noqa: BLE001
            logger.warning("influx write failed: %s", exc)

    async def write_tariff(
        self,
        dt: datetime,
        effective_rate: float,
        octopus_rate: float,
        modul3_rate: float,
    ) -> None:
        """Write a single ``ems_tariff`` Point.

        Args:
            dt:             Timestamp for the tariff record (should be UTC).
            effective_rate: Combined effective rate in EUR/kWh.
            octopus_rate:   Octopus Go supply rate in EUR/kWh.
            modul3_rate:    §14a EnWG Modul 3 grid-fee rate in EUR/kWh.

        Failures are caught and logged as WARNING — never raised to the caller.
        """
        try:
            point = (
                Point("ems_tariff")
                .field("effective_rate_eur_kwh", float(effective_rate))
                .field("octopus_rate_eur_kwh", float(octopus_rate))
                .field("modul3_rate_eur_kwh", float(modul3_rate))
                .time(dt)
            )
            await self._write_api.write(bucket=self._bucket, record=point)
        except Exception as exc:  # noqa: BLE001
            logger.warning("influx write failed: %s", exc)
