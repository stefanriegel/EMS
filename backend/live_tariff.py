"""Live tariff layer — reads the complete electricity price from a Home
Assistant entity (e.g. ``sensor.evcc_tariff_grid``) via
:class:`~backend.ha_rest_client.MultiEntityHaClient`.

The HA entity value is treated as the **fully-inclusive effective price**
(supply rate + all grid fees already baked in).  No Modul 3 adder is applied.

When the HA entity is unavailable (returns ``None``), the layer falls back to
``CompositeTariffEngine`` transparently.

Observability
-------------
- ``INFO  "Live tariff: entity=<field> value=<effective>"``
  — emitted on each ``get_effective_price()`` call when the HA entity is live.
- ``WARNING "Live tariff: entity=<field> returned None — falling back to CompositeTariffEngine"``
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

log = logging.getLogger("ems.live_tariff")


class LiveOctopusTariff:
    """Tariff engine that reads the complete electricity price from a live HA entity.

    Drop-in replacement for :class:`~backend.tariff.CompositeTariffEngine` —
    exposes the same ``get_effective_price(dt)`` and ``get_price_schedule(date)``
    public methods plus ``_octopus`` and ``_modul3`` attribute delegates so all
    existing WS code in ``api.py`` that accesses
    ``tariff_engine._octopus.timezone`` continues to work without change.

    The HA entity (e.g. ``sensor.evcc_tariff_grid``) is expected to carry the
    **fully-inclusive** price — supply rate + all grid fees already included.
    No Modul 3 adder is applied on top.

    Parameters
    ----------
    ha_client:
        Active :class:`MultiEntityHaClient` instance whose cache is read on
        every call to ``get_effective_price()``.
    octopus_entity_field:
        Field name used to look up the price in the HA client cache
        (e.g. ``"octopus_electricity_price"``).
    fallback:
        :class:`CompositeTariffEngine` instance used when the HA entity
        returns ``None``.
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
        """Return the electricity price in €/kWh for a given instant.

        Reads the value directly from the HA entity cache.  The entity value is
        treated as the fully-inclusive effective price — no Modul 3 adder is
        applied.  Falls back to ``CompositeTariffEngine`` when the entity is
        unavailable.

        Parameters
        ----------
        dt:
            The instant to price (used only for fallback path).

        Returns
        -------
        float
            HA entity value when live; ``fallback.get_effective_price(dt)`` otherwise.
        """
        raw = self._ha_client.get_entity_value(self._octopus_field)

        if raw is None:
            log.warning(
                "Live tariff: entity=%s returned None — falling back to CompositeTariffEngine",
                self._octopus_field,
            )
            return self._fallback.get_effective_price(dt)

        try:
            effective = float(raw)
        except (ValueError, TypeError):
            log.warning(
                "Live tariff: entity=%s value=%r not numeric — falling back to CompositeTariffEngine",
                self._octopus_field,
                raw,
            )
            return self._fallback.get_effective_price(dt)

        log.info(
            "Live tariff: entity=%s value=%.6f",
            self._octopus_field,
            effective,
        )
        return effective

    def get_price_schedule(self, target_date: date) -> list[TariffSlot]:
        """Return 48 half-hour tariff slots for *target_date*.

        Each slot is priced by calling ``get_effective_price()`` at the slot's
        start time.  When the HA entity is live, the effective price is the
        complete price and ``modul3_rate_eur_kwh`` is set to 0.0.  When the
        entity is absent, the fallback engine's slot decomposition is used.

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
        oct_midnight = datetime.combine(target_date, time(0, 0), tzinfo=oct_tz)

        slots: list[TariffSlot] = []
        for i in range(48):
            slot_start = oct_midnight + timedelta(minutes=30 * i)
            slot_end = slot_start + timedelta(minutes=30)

            effective = self.get_effective_price(slot_start)

            slots.append(
                TariffSlot(
                    start=slot_start,
                    end=slot_end,
                    octopus_rate_eur_kwh=effective,
                    modul3_rate_eur_kwh=0.0,
                    effective_rate_eur_kwh=effective,
                )
            )

        log.debug(
            "get_price_schedule date=%s slots=%d (LiveOctopusTariff)",
            target_date.isoformat(),
            len(slots),
        )
        return slots
