"""Unit tests for :mod:`backend.tariff` and :mod:`backend.tariff_models`.

Canonical test configuration
-----------------------------
All tests share a single fixture pair to avoid re-defining configs:

* **OctopusGoConfig** — off-peak 00:30–05:30 London (``off_peak_start_min=30``,
  ``off_peak_end_min=330``), off-peak rate 0.08 €/kWh, peak rate 0.28 €/kWh.

* **Modul3Config** — Berlin timezone, four windows:

  +-----------+-------+-------+----------+-----------+
  | Tier      | Start | End   | Minutes  | €/kWh     |
  +===========+=======+=======+==========+===========+
  | NT        | 00:00 | 06:00 | 0–360    | 0.026     |
  +-----------+-------+-------+----------+-----------+
  | ST        | 06:00 | 17:00 | 360–1020 | 0.087     |
  +-----------+-------+-------+----------+-----------+
  | HT        | 17:00 | 20:00 | 1020–1200| 0.125     |
  +-----------+-------+-------+----------+-----------+
  | ST        | 20:00 | 24:00 | 1200–1440| 0.087     |
  +-----------+-------+-------+----------+-----------+

Timezone offsets on 2026-01-15 (winter, no DST):
  * London  = UTC+0
  * Berlin  = UTC+1

So 02:00 London = 03:00 Berlin (NT window → off-peak + NT).
   18:00 London = 19:00 Berlin (HT window → peak + HT).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.tariff import CompositeTariffEngine
from backend.tariff_models import Modul3Config, Modul3Window, OctopusGoConfig, TariffSlot

# ---------------------------------------------------------------------------
# Canonical test fixtures
# ---------------------------------------------------------------------------

LONDON_TZ = ZoneInfo("Europe/London")
BERLIN_TZ = ZoneInfo("Europe/Berlin")

OCTOPUS_CFG = OctopusGoConfig(
    off_peak_start_min=30,
    off_peak_end_min=330,
    off_peak_rate_eur_kwh=0.08,
    peak_rate_eur_kwh=0.28,
    timezone="Europe/London",
)

MODUL3_CFG = Modul3Config(
    windows=[
        Modul3Window(start_min=0, end_min=360, rate_eur_kwh=0.026, tier="NT"),    # 00:00–06:00
        Modul3Window(start_min=360, end_min=1020, rate_eur_kwh=0.087, tier="ST"),  # 06:00–17:00
        Modul3Window(start_min=1020, end_min=1200, rate_eur_kwh=0.125, tier="HT"), # 17:00–20:00
        Modul3Window(start_min=1200, end_min=1440, rate_eur_kwh=0.087, tier="ST"), # 20:00–24:00
    ],
    timezone="Europe/Berlin",
)


@pytest.fixture
def engine() -> CompositeTariffEngine:
    """A :class:`CompositeTariffEngine` built from the canonical test config."""
    return CompositeTariffEngine(octopus=OCTOPUS_CFG, modul3=MODUL3_CFG)


def _london(year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Return a timezone-aware datetime in Europe/London."""
    return datetime(year, month, day, hour, minute, second, tzinfo=LONDON_TZ)


# ---------------------------------------------------------------------------
# get_effective_price — named window tests
# ---------------------------------------------------------------------------


def test_off_peak_rate(engine: CompositeTariffEngine) -> None:
    """02:00 London is inside the off-peak window; Berlin is 03:00 → NT tier."""
    dt = _london(2026, 1, 15, 2, 0)
    price = engine.get_effective_price(dt)
    expected = OCTOPUS_CFG.off_peak_rate_eur_kwh + 0.026  # off-peak + NT
    assert abs(price - expected) < 1e-9, f"Expected {expected}, got {price}"


def test_peak_rate_evening(engine: CompositeTariffEngine) -> None:
    """18:00 London is peak; Berlin is 19:00 → HT tier."""
    dt = _london(2026, 1, 15, 18, 0)
    price = engine.get_effective_price(dt)
    expected = OCTOPUS_CFG.peak_rate_eur_kwh + 0.125  # peak + HT
    assert abs(price - expected) < 1e-9, f"Expected {expected}, got {price}"


# ---------------------------------------------------------------------------
# Boundary instant tests
# ---------------------------------------------------------------------------


def test_boundary_before_off_peak(engine: CompositeTariffEngine) -> None:
    """00:29:59 London → minute 29 < 30 → still peak."""
    dt = _london(2026, 1, 15, 0, 29, 59)
    price = engine.get_effective_price(dt)
    # Berlin = 01:29:59 → minute 89 → ST window (360–1020 starts at 06:00)
    # Actually 01:29 Berlin → minute 89 → NT (0–360)
    expected = OCTOPUS_CFG.peak_rate_eur_kwh + 0.026  # peak + NT
    assert abs(price - expected) < 1e-9, f"Expected {expected}, got {price}"


def test_boundary_at_off_peak_start(engine: CompositeTariffEngine) -> None:
    """00:30:00 London → minute 30 → off-peak starts (inclusive)."""
    dt = _london(2026, 1, 15, 0, 30, 0)
    price = engine.get_effective_price(dt)
    # Berlin = 01:30 → minute 90 → NT
    expected = OCTOPUS_CFG.off_peak_rate_eur_kwh + 0.026  # off-peak + NT
    assert abs(price - expected) < 1e-9, f"Expected {expected}, got {price}"


def test_boundary_at_off_peak_end(engine: CompositeTariffEngine) -> None:
    """05:30:00 London → minute 330 = off_peak_end_min → back to peak (exclusive end)."""
    dt = _london(2026, 1, 15, 5, 30, 0)
    price = engine.get_effective_price(dt)
    # Berlin = 06:30 → minute 390 → ST (360–1020)
    expected = OCTOPUS_CFG.peak_rate_eur_kwh + 0.087  # peak + ST
    assert abs(price - expected) < 1e-9, f"Expected {expected}, got {price}"


def test_midnight_is_peak(engine: CompositeTariffEngine) -> None:
    """00:00 London → minute 0 → before off-peak starts at minute 30 → peak."""
    dt = _london(2026, 1, 15, 0, 0, 0)
    price = engine.get_effective_price(dt)
    # Berlin = 01:00 → minute 60 → NT (0–360)
    expected = OCTOPUS_CFG.peak_rate_eur_kwh + 0.026  # peak + NT
    assert abs(price - expected) < 1e-9, f"Expected {expected}, got {price}"


# ---------------------------------------------------------------------------
# get_price_schedule — full-day structure tests
# ---------------------------------------------------------------------------


def test_schedule_covers_full_day(engine: CompositeTariffEngine) -> None:
    """Schedule slots must be contiguous and span exactly 00:00–24:00."""
    slots = engine.get_price_schedule(date(2026, 1, 15))
    assert len(slots) > 0, "Schedule must contain at least one slot"

    oct_tz = ZoneInfo("Europe/London")
    expected_start = datetime(2026, 1, 15, 0, 0, tzinfo=oct_tz)
    expected_end = datetime(2026, 1, 16, 0, 0, tzinfo=oct_tz)

    assert slots[0].start == expected_start, (
        f"First slot must start at midnight, got {slots[0].start}"
    )
    assert slots[-1].end == expected_end, (
        f"Last slot must end at next midnight, got {slots[-1].end}"
    )

    # Check contiguity
    for i in range(1, len(slots)):
        assert slots[i].start == slots[i - 1].end, (
            f"Gap or overlap between slot {i-1} and {i}: "
            f"{slots[i-1].end} vs {slots[i].start}"
        )

    # Check total duration = exactly 24h
    total = slots[-1].end - slots[0].start
    assert total == timedelta(hours=24), (
        f"Total schedule duration must be 24h, got {total}"
    )


def test_schedule_effective_rate_equals_sum(engine: CompositeTariffEngine) -> None:
    """For every slot, effective_rate == octopus_rate + modul3_rate."""
    slots = engine.get_price_schedule(date(2026, 1, 15))
    for slot in slots:
        expected = slot.octopus_rate_eur_kwh + slot.modul3_rate_eur_kwh
        assert abs(slot.effective_rate_eur_kwh - expected) < 1e-9, (
            f"Slot {slot.start}–{slot.end}: effective={slot.effective_rate_eur_kwh} "
            f"!= {slot.octopus_rate_eur_kwh} + {slot.modul3_rate_eur_kwh}"
        )


# ---------------------------------------------------------------------------
# Invalid Modul3 config — construction raises ValueError
# ---------------------------------------------------------------------------


def test_invalid_modul3_gap_raises() -> None:
    """Modul3 config with a gap must raise ValueError at engine construction."""
    # Gap: 360–420 (06:00–07:00) is uncovered
    bad_modul3 = Modul3Config(
        windows=[
            Modul3Window(start_min=0, end_min=360, rate_eur_kwh=0.026, tier="NT"),
            # intentional gap: 360–420 missing
            Modul3Window(start_min=420, end_min=1440, rate_eur_kwh=0.087, tier="ST"),
        ],
        timezone="Europe/Berlin",
    )
    with pytest.raises(ValueError, match="gap"):
        CompositeTariffEngine(octopus=OCTOPUS_CFG, modul3=bad_modul3)


def test_invalid_modul3_overlap_raises() -> None:
    """Modul3 config with overlapping windows must raise ValueError at construction."""
    # Overlap: 300–400 overlaps with 0–360
    bad_modul3 = Modul3Config(
        windows=[
            Modul3Window(start_min=0, end_min=400, rate_eur_kwh=0.026, tier="NT"),
            Modul3Window(start_min=300, end_min=1440, rate_eur_kwh=0.087, tier="ST"),
        ],
        timezone="Europe/Berlin",
    )
    with pytest.raises(ValueError, match="overlap"):
        CompositeTariffEngine(octopus=OCTOPUS_CFG, modul3=bad_modul3)
