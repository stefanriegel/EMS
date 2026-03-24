"""Unit tests for the DESS MQTT subscriber.

Threading model note
--------------------
``DessMqttSubscriber._on_disconnect`` and ``_on_connect`` use
``loop.call_soon_threadsafe()`` to cross the paho thread -> asyncio boundary.
Tests that rely on asyncio-specific APIs skip the trio variant via an
``anyio_backend`` fixture guard.

Sync tests (message parsing, get_active_slot) don't need an event loop.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.dess_models import DessScheduleSlot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(topic: str, value: object) -> SimpleNamespace:
    """Create a mock MQTT message."""
    payload = json.dumps({"value": value}).encode("utf-8")
    return SimpleNamespace(topic=topic, payload=payload)


def _make_subscriber() -> object:
    """Create a DessMqttSubscriber without connecting."""
    from backend.dess_mqtt import DessMqttSubscriber

    return DessMqttSubscriber(
        host="127.0.0.1", port=1883, portal_id="e0ff50a097c0"
    )


# ---------------------------------------------------------------------------
# Message parsing tests (sync -- no event loop needed)
# ---------------------------------------------------------------------------


class TestDessMqttOnMessage:
    """DessMqttSubscriber._on_message parses DESS MQTT topics correctly."""

    def test_parse_schedule_soc(self) -> None:
        sub = _make_subscriber()
        topic = "N/e0ff50a097c0/settings/0/Settings/DynamicEss/Schedule/0/Soc"
        sub._on_message(None, None, _make_message(topic, 85.0))
        assert sub.schedule.slots[0].soc_pct == 85.0

    def test_parse_schedule_start(self) -> None:
        sub = _make_subscriber()
        topic = "N/e0ff50a097c0/settings/0/Settings/DynamicEss/Schedule/2/Start"
        sub._on_message(None, None, _make_message(topic, 3600))
        assert sub.schedule.slots[2].start_s == 3600

    def test_parse_schedule_duration(self) -> None:
        sub = _make_subscriber()
        topic = "N/e0ff50a097c0/settings/0/Settings/DynamicEss/Schedule/1/Duration"
        sub._on_message(None, None, _make_message(topic, 7200))
        assert sub.schedule.slots[1].duration_s == 7200

    def test_parse_schedule_strategy(self) -> None:
        sub = _make_subscriber()
        topic = "N/e0ff50a097c0/settings/0/Settings/DynamicEss/Schedule/3/Strategy"
        sub._on_message(None, None, _make_message(topic, 1))
        assert sub.schedule.slots[3].strategy == 1

    def test_parse_mode(self) -> None:
        sub = _make_subscriber()
        topic = "N/e0ff50a097c0/settings/0/Settings/DynamicEss/Mode"
        sub._on_message(None, None, _make_message(topic, 4))
        assert sub.schedule.mode == 4

    def test_ignores_unknown_subtopic(self) -> None:
        sub = _make_subscriber()
        topic = "N/e0ff50a097c0/settings/0/Settings/DynamicEss/UnknownTopic"
        # Should not raise
        sub._on_message(None, None, _make_message(topic, 42))

    def test_updates_last_update(self) -> None:
        sub = _make_subscriber()
        topic = "N/e0ff50a097c0/settings/0/Settings/DynamicEss/Mode"
        sub._on_message(None, None, _make_message(topic, 1))
        assert sub.schedule.last_update > 0


# ---------------------------------------------------------------------------
# Connection tests (async -- need asyncio event loop)
# ---------------------------------------------------------------------------


class TestDessMqttConnect:
    """DessMqttSubscriber handles connect failure gracefully."""

    async def test_connect_failure_graceful(self, anyio_backend: str) -> None:
        if anyio_backend != "asyncio":
            pytest.skip("paho-mqtt requires asyncio")
        from backend.dess_mqtt import DessMqttSubscriber

        sub = DessMqttSubscriber(host="192.168.99.99", port=9999, portal_id="test")
        sub._client.connect = MagicMock(
            side_effect=ConnectionRefusedError("refused")
        )
        await sub.connect()
        assert sub.dess_available is False

    async def test_connect_oserror_graceful(self, anyio_backend: str) -> None:
        if anyio_backend != "asyncio":
            pytest.skip("paho-mqtt requires asyncio")
        from backend.dess_mqtt import DessMqttSubscriber

        sub = DessMqttSubscriber(host="bad-host", port=1883, portal_id="test")
        sub._client.connect = MagicMock(side_effect=OSError("no route"))
        await sub.connect()
        assert sub.dess_available is False


class TestDessMqttDisconnect:
    """DessMqttSubscriber._on_disconnect sets dess_available to False."""

    async def test_on_disconnect_sets_unavailable(
        self, anyio_backend: str
    ) -> None:
        if anyio_backend != "asyncio":
            pytest.skip("paho-mqtt requires asyncio")
        from backend.dess_mqtt import DessMqttSubscriber

        sub = DessMqttSubscriber(host="127.0.0.1", portal_id="test")
        sub._loop = asyncio.get_running_loop()
        sub.dess_available = True
        sub._on_disconnect(None, None, None, 1, None)
        # call_soon_threadsafe schedules on the loop; yield to process it
        await asyncio.sleep(0)
        assert sub.dess_available is False


# ---------------------------------------------------------------------------
# get_active_slot tests (sync -- no event loop needed)
# ---------------------------------------------------------------------------


class TestGetActiveSlot:
    """get_active_slot() returns correct slot based on mode and time."""

    def test_mode_0_returns_none(self) -> None:
        sub = _make_subscriber()
        sub.schedule.mode = 0
        sub.schedule.slots[0] = DessScheduleSlot(
            soc_pct=80, start_s=3600, duration_s=7200, strategy=1, active=True
        )
        result = sub.get_active_slot(now_seconds_from_midnight=5000)
        assert result is None

    def test_returns_matching_slot(self) -> None:
        sub = _make_subscriber()
        sub.schedule.mode = 1
        sub.schedule.slots[1] = DessScheduleSlot(
            soc_pct=90, start_s=3600, duration_s=7200, strategy=1, active=True
        )
        # 5000 seconds is within [3600, 3600+7200=10800)
        result = sub.get_active_slot(now_seconds_from_midnight=5000)
        assert result is not None
        assert result.soc_pct == 90

    def test_returns_none_outside_window(self) -> None:
        sub = _make_subscriber()
        sub.schedule.mode = 1
        sub.schedule.slots[0] = DessScheduleSlot(
            soc_pct=80, start_s=3600, duration_s=3600, strategy=1, active=True
        )
        # 8000 seconds is after [3600, 7200)
        result = sub.get_active_slot(now_seconds_from_midnight=8000)
        assert result is None
