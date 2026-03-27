"""Tests for LiveOctopusTariff.

Covers:
  - Live path: get_effective_price returns raw Octopus price + Modul 3 rate
  - Fallback path: when entity returns None, delegates to CompositeTariffEngine
  - get_price_schedule returns exactly 48 half-hour TariffSlot instances
  - Log message contains entity field name and value on live path

All tests are synchronous — LiveOctopusTariff reads from a cache, not from
the network, so no async is required.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from backend.config import TariffConfig
from backend.live_tariff import LiveOctopusTariff
from backend.tariff import CompositeTariffEngine
from backend.tariff_models import TariffSlot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_engine() -> CompositeTariffEngine:
    """Return a CompositeTariffEngine built from default TariffConfig.

    Default Modul 3 windows (Europe/Berlin):
        NT 00:00–06:00 → 0.026 €/kWh
        ST 06:00–17:00 → 0.087 €/kWh
        HT 17:00–20:00 → 0.125 €/kWh
        ST 20:00–24:00 → 0.087 €/kWh

    Default Octopus Go (Europe/London):
        off-peak 00:30–05:30 → 0.08 €/kWh
        peak otherwise      → 0.28 €/kWh
    """
    cfg = TariffConfig.from_env()
    return CompositeTariffEngine(octopus=cfg.octopus, modul3=cfg.modul3)


def _make_ha_client(return_value) -> MagicMock:
    """Return a MagicMock that mimics MultiEntityHaClient.get_entity_value()."""
    client = MagicMock()
    client.get_entity_value.return_value = return_value
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_effective_price_live_path():
    """Live path: effective price = HA entity value directly (no Modul 3 adder)."""
    fallback = _make_engine()
    ha_client = _make_ha_client(0.389)  # fully-inclusive price from e.g. sensor.evcc_tariff_grid

    live = LiveOctopusTariff(
        ha_client=ha_client,
        octopus_entity_field="octopus_electricity_price",
        fallback=fallback,
    )

    dt = datetime(2026, 1, 15, 1, 0, 0, tzinfo=timezone.utc)
    result = live.get_effective_price(dt)

    # Entity value is returned as-is — no Modul 3 added
    assert abs(result - 0.389) < 1e-9


def test_get_effective_price_fallback_when_none(caplog):
    """Fallback path: when entity returns None, delegates to CompositeTariffEngine."""
    fallback = _make_engine()
    ha_client = _make_ha_client(None)

    live = LiveOctopusTariff(
        ha_client=ha_client,
        octopus_entity_field="octopus_electricity_price",
        fallback=fallback,
    )

    dt = datetime(2026, 1, 15, 1, 0, 0, tzinfo=timezone.utc)

    with caplog.at_level(logging.WARNING, logger="ems.live_tariff"):
        result = live.get_effective_price(dt)

    # Result should equal the fallback engine's output
    expected = fallback.get_effective_price(dt)
    assert abs(result - expected) < 1e-9

    # Warning log must mention the field and "falling back"
    assert any(
        "octopus_electricity_price" in r.message and "falling back" in r.message
        for r in caplog.records
    )


def test_get_price_schedule_returns_48_slots():
    """get_price_schedule returns exactly 48 half-hour TariffSlot instances."""
    fallback = _make_engine()
    ha_client = _make_ha_client(0.10)

    live = LiveOctopusTariff(
        ha_client=ha_client,
        octopus_entity_field="octopus_electricity_price",
        fallback=fallback,
    )

    slots = live.get_price_schedule(date(2026, 1, 15))

    assert len(slots) == 48
    assert all(isinstance(s, TariffSlot) for s in slots)
    # Slots should be contiguous: each slot's end == next slot's start
    for i in range(len(slots) - 1):
        assert slots[i].end == slots[i + 1].start


def test_live_tariff_logs_entity_id_and_value(caplog):
    """Live path: INFO log contains the entity field name and the raw value."""
    fallback = _make_engine()
    ha_client = _make_ha_client(0.15)

    live = LiveOctopusTariff(
        ha_client=ha_client,
        octopus_entity_field="my_octopus_sensor",
        fallback=fallback,
    )

    dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    with caplog.at_level(logging.INFO, logger="ems.live_tariff"):
        live.get_effective_price(dt)

    # Must log entity field name and "0.15"
    assert any(
        "my_octopus_sensor" in r.message and "0.15" in r.message
        for r in caplog.records
    )
