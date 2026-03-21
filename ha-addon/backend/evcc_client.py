"""EvccClient — async HTTP client for EVCC's ``/api/state`` endpoint.

Fetches the full EVCC state, parses the EVopt optimisation result, solar
forecast, and grid-price timeseries into typed :mod:`backend.schedule_models`
dataclasses, and returns an :class:`~backend.schedule_models.EvccState`.

Observability
-------------
- ``WARNING "evcc get_state failed: <exc>"`` — emitted on any HTTP or parse
  error.  Grep this pattern to detect EVCC unreachability at runtime.
- ``WARNING`` — emitted by :meth:`EvoptResult.get_huawei_target_soc_pct` /
  :meth:`EvoptResult.get_victron_target_soc_pct` when the named packs are
  absent from the EVopt result.
- Returns ``None`` on any failure; callers (Scheduler, S03) check for
  ``None`` and set ``schedule.stale = True``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from backend.config import EvccConfig
from backend.schedule_models import (
    EvccState,
    EvoptBatteryTimeseries,
    EvoptResult,
    GridPriceSeries,
    SolarForecast,
)

logger = logging.getLogger("ems.evcc")


class EvccClient:
    """Async HTTP client for the EVCC ``/api/state`` endpoint.

    Parameters
    ----------
    config:
        :class:`~backend.config.EvccConfig` instance (host, port, timeout).
    """

    def __init__(self, config: EvccConfig) -> None:
        self._config = config
        self._base_url = f"http://{config.host}:{config.port}"

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def get_state(self) -> EvccState | None:
        """Fetch and parse ``/api/state`` from EVCC.

        Returns
        -------
        EvccState | None
            Parsed state on success; ``None`` on any HTTP or parse error
            (the error is logged as a WARNING).
        """
        url = f"{self._base_url}/api/state"
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_s) as client:
                response = await client.get(url)
                response.raise_for_status()
                data: dict[str, Any] = response.json()
            return _parse_state(data)
        except (
            httpx.HTTPError,
            KeyError,
            ValueError,
            TypeError,
            IndexError,
        ) as exc:
            logger.warning("evcc get_state failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Internal parser — module-level so it can be tested independently
# ---------------------------------------------------------------------------


def _parse_state(data: dict[str, Any]) -> EvccState:
    """Build an :class:`~backend.schedule_models.EvccState` from raw JSON.

    Each sub-section is parsed independently; a missing or malformed section
    sets that field to ``None`` without affecting the others.  This means a
    partial EVCC response (e.g. EVopt not yet run) still returns a valid
    ``EvccState``.

    Parameters
    ----------
    data:
        Raw JSON dict from ``GET /api/state``.

    Returns
    -------
    EvccState
        Parsed state with individual fields ``None`` when unavailable.
    """
    evopt_result: EvoptResult | None = None
    evopt_status: str = "unknown"
    solar: SolarForecast | None = None
    grid_prices: GridPriceSeries | None = None

    # -- EVopt -----------------------------------------------------------
    try:
        evopt_data = data["evopt"]["res"]
        evopt_status = str(evopt_data.get("status", "unknown"))
        objective_value = float(evopt_data.get("objective_value", 0.0))

        # Build per-battery timeseries
        raw_batteries: list[dict[str, Any]] = evopt_data["batteries"]
        raw_timestamps: list[str] = evopt_data["details"]["timestamp"]

        # Slot 0 is *now*, not midnight — parse and re-derive offsets
        t0 = datetime.fromisoformat(raw_timestamps[0])
        if t0.tzinfo is None:
            # Assume UTC when no timezone is embedded
            t0 = t0.replace(tzinfo=timezone.utc)

        batteries: list[EvoptBatteryTimeseries] = []
        for bat in raw_batteries:
            n_slots = len(bat["charging_power"])
            slot_ts = [t0 + timedelta(minutes=15 * i) for i in range(n_slots)]
            batteries.append(
                EvoptBatteryTimeseries(
                    title=str(bat["title"]),
                    charging_power_w=[float(v) for v in bat["charging_power"]],
                    discharging_power_w=[float(v) for v in bat["discharging_power"]],
                    soc_fraction=[float(v) for v in bat["state_of_charge"]],
                    slot_timestamps_utc=slot_ts,
                )
            )

        evopt_result = EvoptResult(
            status=evopt_status,
            objective_value=objective_value,
            batteries=batteries,
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        logger.warning("evcc _parse_state: evopt section unavailable: %s", exc)

    # -- Solar forecast --------------------------------------------------
    try:
        solar_data = data["forecast"]["solar"]
        raw_ts = solar_data["timeseries"]

        solar_w: list[float] = []
        solar_slot_ts: list[datetime] = []
        for entry in raw_ts:
            solar_w.append(float(entry["value"]))
            ts = datetime.fromisoformat(entry["start"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            solar_slot_ts.append(ts)

        tomorrow_energy_wh = float(solar_data["tomorrow"]["energy"])

        # day_after is optional — default 0.0 if not present
        try:
            day_after_energy_wh = float(solar_data["dayAfterTomorrow"]["energy"])
        except (KeyError, TypeError, ValueError):
            day_after_energy_wh = 0.0

        solar = SolarForecast(
            timeseries_w=solar_w,
            slot_timestamps_utc=solar_slot_ts,
            tomorrow_energy_wh=tomorrow_energy_wh,
            day_after_energy_wh=day_after_energy_wh,
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        logger.warning("evcc _parse_state: solar section unavailable: %s", exc)

    # -- Grid prices -----------------------------------------------------
    try:
        grid_raw = data["forecast"]["grid"]
        feedin_raw = data["forecast"]["feedin"]

        import_prices: list[float] = []
        export_prices: list[float] = []
        price_slot_ts: list[datetime] = []

        for g_entry, f_entry in zip(grid_raw, feedin_raw):
            import_prices.append(float(g_entry["value"]))
            export_prices.append(float(f_entry["value"]))
            ts = datetime.fromisoformat(g_entry["start"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            price_slot_ts.append(ts)

        grid_prices = GridPriceSeries(
            import_eur_kwh=import_prices,
            export_eur_kwh=export_prices,
            slot_timestamps_utc=price_slot_ts,
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        logger.warning("evcc _parse_state: grid prices unavailable: %s", exc)

    return EvccState(
        evopt=evopt_result,
        solar=solar,
        grid_prices=grid_prices,
        evopt_status=evopt_status,
    )
