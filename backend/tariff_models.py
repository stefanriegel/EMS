"""Tariff domain models.

All rates are stored in **euros per kWh**.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TariffSlot:
    """A contiguous time interval with its electricity price.

    Attributes:
        start: Slot start (timezone-aware, inclusive).
        end: Slot end (timezone-aware, exclusive).
        octopus_rate_eur_kwh: Supply rate for this slot in €/kWh
            (same as effective when tariff is fully-inclusive from EVCC).
        modul3_rate_eur_kwh: Grid-fee component (0.0 when using EVCC).
        effective_rate_eur_kwh: Total price in €/kWh.
    """

    start: datetime
    end: datetime
    octopus_rate_eur_kwh: float
    modul3_rate_eur_kwh: float
    effective_rate_eur_kwh: float
