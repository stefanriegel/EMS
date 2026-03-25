"""InfluxDB metrics writer for the EMS control loop.

Writes energy metrics to InfluxDB at ~5 s resolution using the InfluxDB v1
line protocol API (``POST /write?db=<database>&precision=ns``).

Two measurements are supported:

``ems_system``
    Per-cycle snapshot of the combined battery pool: SoC, power, setpoints,
    control state, and availability flags.

``ems_tariff``
    Current tariff rates: effective, Octopus, and Modul3 grid-fee.

Design decisions:
  - Both write methods are fire-and-forget: any ``Exception`` from the
    HTTP client is caught, logged as WARNING, and swallowed.  InfluxDB
    outages must never crash the orchestrator or affect ``/api/health``.
  - The InfluxDB password is **never** logged.  Only url/database appear in
    the INFO construction log.
  - ``write_system_state`` uses ``datetime.now(tz=timezone.utc)`` as the
    Point timestamp, not ``state.timestamp`` (which is ``time.monotonic()`` --
    a relative counter with no fixed epoch, not usable as an absolute
    wall-clock timestamp).
  - Uses httpx AsyncClient to POST line protocol directly to the InfluxDB v1
    ``/write`` endpoint.  No external InfluxDB client library required.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from backend.controller_model import ControllerSnapshot, CoordinatorState, DecisionEntry
from backend.schedule_models import ChargeSchedule
from backend.unified_model import UnifiedPoolState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Line protocol builder
# ---------------------------------------------------------------------------


def _escape_tag(value: str) -> str:
    """Escape special characters in InfluxDB line protocol tag values."""
    return value.replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _escape_measurement(value: str) -> str:
    """Escape special characters in InfluxDB line protocol measurement names."""
    return value.replace(" ", "\\ ").replace(",", "\\,")


class _LineProtocolBuilder:
    """Fluent builder for InfluxDB line protocol strings.

    Mimics the ``influxdb_client.Point`` API but produces raw line protocol
    text suitable for ``POST /write`` to InfluxDB v1.
    """

    def __init__(self, measurement: str) -> None:
        self._measurement = _escape_measurement(measurement)
        self._tags: list[tuple[str, str]] = []
        self._fields: list[tuple[str, str]] = []
        self._timestamp: int | None = None

    def tag(self, key: str, value: str) -> _LineProtocolBuilder:
        """Add a tag key/value pair."""
        self._tags.append((key, _escape_tag(value)))
        return self

    def field_float(self, key: str, value: float) -> _LineProtocolBuilder:
        """Add a float field (no suffix in line protocol)."""
        self._fields.append((key, f"{value}"))
        return self

    def field_int(self, key: str, value: int) -> _LineProtocolBuilder:
        """Add an integer field (``i`` suffix in line protocol)."""
        self._fields.append((key, f"{value}i"))
        return self

    def field_str(self, key: str, value: str) -> _LineProtocolBuilder:
        """Add a string field (double-quoted in line protocol)."""
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        self._fields.append((key, f'"{escaped}"'))
        return self

    def field_bool(self, key: str, value: bool) -> _LineProtocolBuilder:
        """Add a boolean field (``true``/``false`` in line protocol)."""
        self._fields.append((key, "true" if value else "false"))
        return self

    def time_ns(self, dt: datetime) -> _LineProtocolBuilder:
        """Set the timestamp from a datetime (converted to nanoseconds)."""
        self._timestamp = int(dt.timestamp() * 1_000_000_000)
        return self

    def to_line(self) -> str:
        """Serialise to a single line protocol string."""
        measurement = self._measurement
        if self._tags:
            measurement += "," + ",".join(f"{k}={v}" for k, v in self._tags)
        fields = ",".join(f"{k}={v}" for k, v in self._fields)
        line = f"{measurement} {fields}"
        if self._timestamp is not None:
            line += f" {self._timestamp}"
        return line


class InfluxMetricsWriter:
    """Async writer that persists EMS state to InfluxDB v1 via line protocol.

    Args:
        url:      Base URL of the InfluxDB instance (e.g. ``http://localhost:8086``).
        database: Target InfluxDB database name.
        username: Optional InfluxDB username for basic auth.
        password: Optional InfluxDB password for basic auth.

    Logs at INFO level on construction (url/database -- **not** password).
    Logs at WARNING level on any write failure (greppable: ``influx write failed``).
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
        self._write_url = f"{self._url}/write"
        self._params = {"db": database, "precision": "ns"}

        # Build auth tuple only if credentials are provided
        auth: tuple[str, str] | None = None
        if username and password:
            auth = (username, password)

        self._http = httpx.AsyncClient(auth=auth, timeout=10.0)

        logger.info(
            "InfluxMetricsWriter initialised -- url=%s database=%s",
            self._url,
            database,
        )

    async def _write_lines(self, lines: list[str]) -> None:
        """POST one or more line protocol lines to InfluxDB."""
        body = "\n".join(lines)
        resp = await self._http.post(
            self._write_url, params=self._params, content=body
        )
        resp.raise_for_status()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def write_system_state(self, state: UnifiedPoolState) -> None:
        """Write a single ``ems_system`` point from *state*.

        Tags encode categorical / boolean attributes; fields carry numeric
        measurements.  All 8 schema fields are always written (no optionals).

        The point timestamp uses ``datetime.now(tz=timezone.utc)``, not
        ``state.timestamp`` (monotonic counter), to produce an absolute
        wall-clock timestamp InfluxDB can store and query.

        Failures are caught and logged as WARNING -- never raised to the caller.
        """
        try:
            point = (
                _LineProtocolBuilder("ems_system")
                .tag("control_state", state.control_state.value)
                .tag("huawei_available", "true" if state.huawei_available else "false")
                .tag("victron_available", "true" if state.victron_available else "false")
                .field_float("combined_soc_pct", float(state.combined_soc_pct))
                .field_float("huawei_soc_pct", float(state.huawei_soc_pct))
                .field_float("victron_soc_pct", float(state.victron_soc_pct))
                .field_float("combined_power_w", float(state.combined_power_w))
                .field_int("huawei_discharge_setpoint_w", int(state.huawei_discharge_setpoint_w))
                .field_int("victron_discharge_setpoint_w", int(state.victron_discharge_setpoint_w))
                .field_int("huawei_charge_headroom_w", int(state.huawei_charge_headroom_w))
                .field_float("victron_charge_headroom_w", float(state.victron_charge_headroom_w))
                .time_ns(datetime.now(tz=timezone.utc))
            )
            await self._write_lines([point.to_line()])
        except Exception as exc:  # noqa: BLE001
            logger.warning("influx write failed: %s", exc)

    async def write_tariff(
        self,
        dt: datetime,
        effective_rate: float,
        octopus_rate: float,
        modul3_rate: float,
    ) -> None:
        """Write a single ``ems_tariff`` point.

        Failures are caught and logged as WARNING -- never raised to the caller.
        """
        try:
            point = (
                _LineProtocolBuilder("ems_tariff")
                .field_float("effective_rate_eur_kwh", float(effective_rate))
                .field_float("octopus_rate_eur_kwh", float(octopus_rate))
                .field_float("modul3_rate_eur_kwh", float(modul3_rate))
                .time_ns(dt)
            )
            await self._write_lines([point.to_line()])
        except Exception as exc:  # noqa: BLE001
            logger.warning("influx write failed: %s", exc)

    async def write_per_system_metrics(
        self,
        h_snap: ControllerSnapshot,
        v_snap: ControllerSnapshot,
        h_role: str,
        v_role: str,
    ) -> None:
        """Write per-system ``ems_huawei`` and ``ems_victron`` points.

        Both points are written in a single HTTP call.  Fire-and-forget: any
        ``Exception`` is caught, logged as WARNING, and swallowed.
        """
        try:
            now = datetime.now(tz=timezone.utc)
            huawei_pt = (
                _LineProtocolBuilder("ems_huawei")
                .tag("role", h_role)
                .tag("available", "true" if h_snap.available else "false")
                .field_float("soc_pct", float(h_snap.soc_pct))
                .field_float("power_w", float(h_snap.power_w))
                .field_float("setpoint_w", 0.0)
                .field_float("charge_headroom_w", float(h_snap.charge_headroom_w))
                .time_ns(now)
            )
            victron_pt = (
                _LineProtocolBuilder("ems_victron")
                .tag("role", v_role)
                .tag("available", "true" if v_snap.available else "false")
                .field_float("soc_pct", float(v_snap.soc_pct))
                .field_float("power_w", float(v_snap.power_w))
                .field_float("charge_headroom_w", float(v_snap.charge_headroom_w))
                .field_float("grid_l1_power_w", float(v_snap.grid_l1_power_w or 0.0))
                .field_float("grid_l2_power_w", float(v_snap.grid_l2_power_w or 0.0))
                .field_float("grid_l3_power_w", float(v_snap.grid_l3_power_w or 0.0))
                .time_ns(now)
            )
            await self._write_lines([huawei_pt.to_line(), victron_pt.to_line()])
        except Exception as exc:  # noqa: BLE001
            logger.warning("influx per-system write failed: %s", exc)

    async def write_decision(self, entry: DecisionEntry) -> None:
        """Write a single ``ems_decision`` point from *entry*.

        ``trigger`` is stored as a tag for efficient filtering.  Roles are
        stored as fields (not tags) to avoid high-cardinality tag explosion.

        Fire-and-forget: any ``Exception`` is caught and logged as WARNING.
        """
        try:
            point = (
                _LineProtocolBuilder("ems_decision")
                .tag("trigger", entry.trigger)
                .field_str("huawei_role", entry.huawei_role)
                .field_str("victron_role", entry.victron_role)
                .field_float("p_target_w", float(entry.p_target_w))
                .field_float("huawei_allocation_w", float(entry.huawei_allocation_w))
                .field_float("victron_allocation_w", float(entry.victron_allocation_w))
                .field_str("pool_status", entry.pool_status)
                .field_str("reasoning", entry.reasoning)
                .time_ns(datetime.now(tz=timezone.utc))
            )
            await self._write_lines([point.to_line()])
        except Exception as exc:  # noqa: BLE001
            logger.warning("influx decision write failed: %s", exc)

    async def write_coordinator_state(self, state: CoordinatorState) -> None:
        """Write an ``ems_system`` point from a :class:`CoordinatorState`.

        Same measurement as :meth:`write_system_state` but accepts the new
        ``CoordinatorState`` type.  Adds ``huawei_role``, ``victron_role``,
        and ``pool_status`` as tags.

        Fire-and-forget: any ``Exception`` is caught and logged as WARNING.
        """
        try:
            point = (
                _LineProtocolBuilder("ems_system")
                .tag("control_state", state.control_state)
                .tag("huawei_available", "true" if state.huawei_available else "false")
                .tag("victron_available", "true" if state.victron_available else "false")
                .tag("huawei_role", state.huawei_role)
                .tag("victron_role", state.victron_role)
                .tag("pool_status", state.pool_status)
                .field_float("combined_soc_pct", float(state.combined_soc_pct))
                .field_float("huawei_soc_pct", float(state.huawei_soc_pct))
                .field_float("victron_soc_pct", float(state.victron_soc_pct))
                .field_float("combined_power_w", float(state.combined_power_w))
                .field_int("huawei_discharge_setpoint_w", int(state.huawei_discharge_setpoint_w))
                .field_int("victron_discharge_setpoint_w", int(state.victron_discharge_setpoint_w))
                .field_int("huawei_charge_headroom_w", int(state.huawei_charge_headroom_w))
                .field_float("victron_charge_headroom_w", float(state.victron_charge_headroom_w))
                .time_ns(datetime.now(tz=timezone.utc))
            )
            await self._write_lines([point.to_line()])
        except Exception as exc:  # noqa: BLE001
            logger.warning("influx write failed: %s", exc)

    async def write_cross_charge_point(
        self, active: bool, waste_wh: float, episode_count: int
    ) -> None:
        """Write a single ``ems_cross_charge`` point.

        Fire-and-forget: any ``Exception`` is caught, logged as WARNING,
        and swallowed.
        """
        try:
            point = (
                _LineProtocolBuilder("ems_cross_charge")
                .field_bool("active", active)
                .field_float("waste_wh", float(waste_wh))
                .field_int("episode_count", int(episode_count))
                .time_ns(datetime.now(tz=timezone.utc))
            )
            await self._write_lines([point.to_line()])
        except Exception as exc:  # noqa: BLE001
            logger.warning("influx cross-charge write failed: %s", exc)

    async def write_charge_schedule(self, schedule: ChargeSchedule) -> None:
        """Write a single ``ems_schedule`` point from *schedule*.

        Tags encode staleness; fields carry SoC targets, charge energy, cost
        estimate, and slot count.

        Fire-and-forget: any ``Exception`` is caught and logged as WARNING --
        never raised to the caller.
        """
        try:
            huawei_target = (
                float(schedule.slots[0].target_soc_pct)
                if len(schedule.slots) >= 1 and schedule.slots[0].battery == "huawei"
                else 0.0
            )
            victron_target = (
                float(schedule.slots[1].target_soc_pct)
                if len(schedule.slots) >= 2 and schedule.slots[1].battery == "victron"
                else 0.0
            )
            point = (
                _LineProtocolBuilder("ems_schedule")
                .tag("stale", "true" if schedule.stale else "false")
                .field_float("huawei_target_soc_pct", huawei_target)
                .field_float("victron_target_soc_pct", victron_target)
                .field_float("charge_energy_kwh", float(schedule.reasoning.charge_energy_kwh))
                .field_float("cost_estimate_eur", float(schedule.reasoning.cost_estimate_eur))
                .field_int("slot_count", int(len(schedule.slots)))
                .time_ns(datetime.now(tz=timezone.utc))
            )
            await self._write_lines([point.to_line()])
        except Exception as exc:  # noqa: BLE001
            logger.warning("influx write_charge_schedule failed: %s", exc)
