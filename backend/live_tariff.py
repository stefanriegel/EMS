"""Live Octopus tariff layer — reads the raw electricity price from a Home
Assistant entity via :class:`~backend.ha_rest_client.MultiEntityHaClient` and
overlays §14a EnWG Modul 3 grid fees using the existing
:class:`~backend.tariff.CompositeTariffEngine`.

When the HA entity is unavailable (returns ``None``), the layer falls back to
``CompositeTariffEngine`` transparently.

Observability
-------------
- ``INFO  "Live Octopus tariff: entity=<field> value=<raw> modul3=<m3> effective=<total>"``
  — emitted on each ``get_effective_price()`` call when the HA entity is live.
- ``WARNING "Live Octopus tariff: entity=<field> returned None — falling back to CompositeTariffEngine"``
  — emitted on each ``get_effective_price()`` call when the entity is unavailable.
- ``type(app.state.tariff_engine).__name__`` → ``"LiveOctopusTariff"`` when active.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .ha_rest_client import MultiEntityHaClient
from .tariff import CompositeTariffEngine
from .tariff_models import TariffSlot

log = logging.getLogger("ems.live_tariff")


class LiveOctopusTariff:
    """Composite tariff engine that sources the Octopus base rate from a live HA entity.

    Drop-in replacement for :class:`~backend.tariff.CompositeTariffEngine` —
    exposes the same ``get_effective_price(dt)`` and ``get_price_schedule(date)``
    public methods plus ``_octopus`` and ``_modul3`` attribute delegates so all
    existing WS code in ``api.py`` that accesses
    ``tariff_engine._octopus.timezone`` continues to work without change.

    Parameters
    ----------
    ha_client:
        Active :class:`MultiEntityHaClient` instance whose cache is read on
        every call to ``get_effective_price()``.
    octopus_entity_field:
        Field name used to look up the Octopus price in the HA client cache
        (e.g. ``"octopus_electricity_price"``).
    fallback:
        :class:`CompositeTariffEngine` instance used when the HA entity
        returns ``None``, and as the source of Modul 3 rate arithmetic.
    """

    def __init__(
        self,
        ha_client: MultiEntityHaClient,
        octopus_entity_field: str,
        fallback: CompositeTariffEngine,
    ) -> None:
        self._ha_client = ha_client
        self._octopus_field = octopus_entity_field
        self._fallback = fallback

    # ------------------------------------------------------------------
    # Attribute delegates — keep api.py WS code unchanged
    # ------------------------------------------------------------------

    @property
    def _octopus(self):  # type: ignore[return]
        """Delegate to ``fallback._octopus`` for timezone / rate access."""
        return self._fallback._octopus

    @property
    def _modul3(self):  # type: ignore[return]
        """Delegate to ``fallback._modul3`` for window / rate access."""
        return self._fallback._modul3

    # ------------------------------------------------------------------
    # Rate helpers (delegate Modul 3 arithmetic to fallback)
    # ------------------------------------------------------------------

    def _octopus_rate_at(self, minute: int) -> float:
        """Delegate to ``fallback._octopus_rate_at``."""
        return self._fallback._octopus_rate_at(minute)

    def _modul3_rate_at(self, minute: int) -> float:
        """Delegate to ``fallback._modul3_rate_at``."""
        return self._fallback._modul3_rate_at(minute)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_effective_price(self, dt: datetime) -> float:
        """Return the composite electricity price in €/kWh for a given instant.

        Reads the raw Octopus price from the HA entity cache and adds the
        Modul 3 grid-fee adder for the same minute.  Falls back to the
        ``CompositeTariffEngine`` when the entity returns ``None``.

        Parameters
        ----------
        dt:
            The instant to price.  May be timezone-aware or naive.

        Returns
        -------
        float
            ``octopus_entity_value + modul3_rate_eur_kwh`` when the entity
            is populated; ``fallback.get_effective_price(dt)`` otherwise.
        """
        raw = self._ha_client.get_entity_value(self._octopus_field)

        if raw is None:
            log.warning(
                "Live Octopus tariff: entity=%s returned None — falling back to CompositeTariffEngine",
                self._octopus_field,
            )
            return self._fallback.get_effective_price(dt)

        try:
            raw_price = float(raw)
        except (ValueError, TypeError):
            log.warning(
                "Live Octopus tariff: entity=%s value=%r not numeric — falling back to CompositeTariffEngine",
                self._octopus_field,
                raw,
            )
            return self._fallback.get_effective_price(dt)

        # Compute Modul 3 minute in Berlin timezone
        m3_tz = ZoneInfo(self._fallback._modul3.timezone)
        if dt.tzinfo is None:
            # Interpret naive dt as UTC for a safe, reproducible conversion
            from datetime import timezone as _tz
            dt_aware = dt.replace(tzinfo=_tz.utc)
        else:
            dt_aware = dt
        dt_m3 = dt_aware.astimezone(m3_tz)
        m3_minute = dt_m3.hour * 60 + dt_m3.minute
        m3_rate = self._fallback._modul3_rate_at(m3_minute)

        effective = raw_price + m3_rate
        log.info(
            "Live Octopus tariff: entity=%s value=%.6f modul3=%.6f effective=%.6f",
            self._octopus_field,
            raw_price,
            m3_rate,
            effective,
        )
        return effective

    def get_price_schedule(self, target_date: date) -> list[TariffSlot]:
        """Return 48 half-hour tariff slots for *target_date*.

        Each slot is priced by calling ``get_effective_price()`` at the slot's
        start time.  The Octopus and Modul 3 sub-rates are decomposed from the
        effective price by delegating to the fallback engine's ``_octopus_rate_at``
        and ``_modul3_rate_at`` helpers.  The raw Octopus entity price feeds the
        ``octopus_rate_eur_kwh`` slot field when the entity is live; the fallback
        engine populates that field when the entity is absent.

        Parameters
        ----------
        target_date:
            Calendar date for which to build the schedule.

        Returns
        -------
        list[TariffSlot]
            48 contiguous half-hour slots spanning 00:00–24:00 in the Octopus
            timezone (``fallback._octopus.timezone``).
        """
        oct_tz = ZoneInfo(self._fallback._octopus.timezone)
        m3_tz = ZoneInfo(self._fallback._modul3.timezone)
        oct_midnight = datetime.combine(target_date, time(0, 0), tzinfo=oct_tz)

        slots: list[TariffSlot] = []
        for i in range(48):
            slot_start = oct_midnight + timedelta(minutes=30 * i)
            slot_end = slot_start + timedelta(minutes=30)

            # Compute Octopus and Modul 3 minutes at slot start
            oct_minute = slot_start.hour * 60 + slot_start.minute
            dt_m3 = slot_start.astimezone(m3_tz)
            m3_minute = dt_m3.hour * 60 + dt_m3.minute

            # Get effective price (uses live entity + M3 or fallback)
            effective = self.get_effective_price(slot_start)

            # Decompose into sub-rates: oct rate from fallback lookup, m3 from fallback
            m3_rate = self._fallback._modul3_rate_at(m3_minute)
            oct_rate = effective - m3_rate  # residual after M3

            slots.append(
                TariffSlot(
                    start=slot_start,
                    end=slot_end,
                    octopus_rate_eur_kwh=oct_rate,
                    modul3_rate_eur_kwh=m3_rate,
                    effective_rate_eur_kwh=effective,
                )
            )

        log.debug(
            "get_price_schedule date=%s slots=%d (LiveOctopusTariff)",
            target_date.isoformat(),
            len(slots),
        )
        return slots
