"""EvccTariffEngine — tariff engine sourced entirely from EVCC grid prices.

EVCC provides a fully-inclusive import price timeseries (supply + all grid
fees). This engine caches the latest ``GridPriceSeries`` and exposes
``get_effective_price`` / ``get_price_schedule``.

When no EVCC data has been received yet, returns ``None`` for prices and
empty schedules — never hardcoded rates.

Observability:
    - DEBUG ``"EvccTariffEngine: ..."`` on every price lookup and schedule build.
    - The scheduler calls :meth:`update` after each successful EVCC fetch.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .tariff_models import TariffSlot

log = logging.getLogger("ems.tariff")

# Default timezone for schedule generation when no EVCC data
_DEFAULT_TZ = "Europe/Berlin"


class EvccTariffEngine:
    """Tariff engine driven entirely by EVCC's grid-price timeseries.

    No hardcoded fallback rates — if EVCC hasn't delivered prices yet,
    ``get_effective_price`` returns ``None`` and ``get_price_schedule``
    returns an empty list.
    """

    def __init__(self) -> None:
        self._grid_prices: "GridPriceSeries | None" = None  # type: ignore[name-defined]
        self._timezone: str = _DEFAULT_TZ

    # ------------------------------------------------------------------
    # Mutator — called by the scheduler after each EVCC fetch
    # ------------------------------------------------------------------

    def update(self, grid_prices: "GridPriceSeries") -> None:  # type: ignore[name-defined]
        """Replace the cached EVCC grid-price series."""
        self._grid_prices = grid_prices

    # ------------------------------------------------------------------
    # Public tariff API
    # ------------------------------------------------------------------

    def get_effective_price(self, dt: datetime) -> float | None:
        """Return the effective import price in €/kWh at *dt*, or ``None``.

        Looks up *dt* in the cached EVCC timeseries. Returns ``None``
        when no EVCC data is available.
        """
        gp = self._grid_prices
        if gp is None or not gp.import_eur_kwh:
            log.debug("EvccTariffEngine: no EVCC prices for dt=%s", dt)
            return None

        from datetime import timezone as _tz
        dt_utc = dt if dt.tzinfo is not None else dt.replace(tzinfo=_tz.utc)

        # Walk timeseries to find current slot
        price = gp.import_eur_kwh[-1]  # default: last known
        for i, ts in enumerate(gp.slot_timestamps_utc):
            if i + 1 < len(gp.slot_timestamps_utc):
                if ts <= dt_utc < gp.slot_timestamps_utc[i + 1]:
                    price = gp.import_eur_kwh[i]
                    break
            elif ts <= dt_utc:
                price = gp.import_eur_kwh[i]

        log.debug("EvccTariffEngine: dt=%s price=%.6f", dt, price)
        return price

    def get_price_schedule(self, target_date: date) -> list[TariffSlot]:
        """Return tariff slots for *target_date* from EVCC prices.

        Returns an empty list when no EVCC data covers the requested date.
        """
        gp = self._grid_prices
        if gp is None or not gp.import_eur_kwh:
            log.debug("EvccTariffEngine: no EVCC prices for date=%s", target_date)
            return []

        tz = ZoneInfo(self._timezone)
        day_start = datetime.combine(target_date, time(0, 0), tzinfo=tz)
        day_end = day_start + timedelta(days=1)

        slots: list[TariffSlot] = []
        for i, ts in enumerate(gp.slot_timestamps_utc):
            ts_local = ts.astimezone(tz)
            if not (day_start <= ts_local < day_end):
                continue
            if i + 1 < len(gp.slot_timestamps_utc):
                slot_end = gp.slot_timestamps_utc[i + 1].astimezone(tz)
                if slot_end > day_end:
                    slot_end = day_end
            else:
                slot_end = ts_local + timedelta(minutes=15)

            price = gp.import_eur_kwh[i]
            slots.append(TariffSlot(
                start=ts_local,
                end=slot_end,
                octopus_rate_eur_kwh=price,
                modul3_rate_eur_kwh=0.0,
                effective_rate_eur_kwh=price,
            ))

        log.debug("EvccTariffEngine: date=%s slots=%d", target_date, len(slots))
        return slots
