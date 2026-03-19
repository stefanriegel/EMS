"""Tariff domain models for the composite Octopus Go + Modul 3 engine.

All rates are stored in **euros per kWh**.  Window boundaries are represented
as **minutes from midnight** in the range [0, 1440) so that midnight-crossing
windows (e.g. the Octopus Go peak window that wraps from 05:30 to 00:30) can
be expressed without ambiguous ``datetime.time`` comparisons.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass
class Modul3Window:
    """A single time window in the §14a EnWG Modul 3 Netzgebühren schedule.

    Attributes:
        start_min: Window start in minutes from midnight, inclusive [0, 1440).
        end_min:   Window end in minutes from midnight, exclusive (0, 1440].
                   Must be strictly greater than ``start_min``.
        rate_eur_kwh: Grid-fee adder for this window in €/kWh.
        tier: One of ``"NT"`` (Niedertarif), ``"ST"`` (Standardtarif), or
              ``"HT"`` (Hochtarif).
    """

    start_min: int
    end_min: int
    rate_eur_kwh: float
    tier: Literal["NT", "ST", "HT"]


@dataclass
class OctopusGoConfig:
    """Static configuration for an Octopus Go electricity supply tariff.

    Octopus Go has a cheap off-peak window (typically 00:30–05:30 UK time)
    and a standard peak rate for all other hours.  The peak window wraps
    midnight, so ``off_peak_start_min < off_peak_end_min`` and the peak
    intervals are ``[0, off_peak_start_min)`` and ``[off_peak_end_min, 1440)``.

    Attributes:
        off_peak_start_min: Start of the cheap window in minutes from midnight,
            inclusive.  Octopus Go standard = 30 (00:30).
        off_peak_end_min: End of the cheap window in minutes from midnight,
            exclusive.  Octopus Go standard = 330 (05:30).
        off_peak_rate_eur_kwh: Electricity supply rate during the off-peak
            window in €/kWh.
        peak_rate_eur_kwh: Electricity supply rate during the peak window
            in €/kWh.
        timezone: IANA timezone name for wall-clock interpretation of the
            window boundaries (default ``"Europe/London"``).
    """

    off_peak_start_min: int
    off_peak_end_min: int
    off_peak_rate_eur_kwh: float
    peak_rate_eur_kwh: float
    timezone: str = "Europe/London"


@dataclass
class Modul3Config:
    """Full §14a EnWG Modul 3 Netzgebühren schedule for one DSO.

    The ``windows`` list must partition exactly 1440 minutes with no gap and
    no overlap.  The :class:`CompositeTariffEngine` validates this at
    construction and raises :exc:`ValueError` if the invariant is violated.

    Attributes:
        windows: Non-overlapping, gap-free windows that cover [0, 1440).
            Must be sorted by ``start_min``.
        timezone: IANA timezone name for wall-clock interpretation of window
            boundaries (default ``"Europe/Berlin"``).
    """

    windows: list[Modul3Window]
    timezone: str = "Europe/Berlin"


@dataclass
class TariffSlot:
    """A contiguous time interval with its composite electricity price.

    The ``start`` and ``end`` datetimes are always timezone-aware.  The
    effective rate is the arithmetic sum of the Octopus supply component and
    the Modul 3 grid-fee component.

    Attributes:
        start: Slot start (timezone-aware, inclusive).
        end: Slot end (timezone-aware, exclusive).
        octopus_rate_eur_kwh: Octopus Go supply rate for this slot in €/kWh.
        modul3_rate_eur_kwh: Modul 3 grid-fee adder for this slot in €/kWh.
        effective_rate_eur_kwh: Sum of both components in €/kWh.
    """

    start: datetime
    end: datetime
    octopus_rate_eur_kwh: float
    modul3_rate_eur_kwh: float
    effective_rate_eur_kwh: float
