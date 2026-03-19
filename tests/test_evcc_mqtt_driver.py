"""Unit tests for EvccMqttDriver.

No live broker required.  The paho MQTT client is patched at construction
time so no real network connections are made.

Coverage:
  - Battery mode attribute updates via ``_on_message`` (hold, normal)
  - Loadpoint state updates via ``_on_message`` (chargePower, mode, vehicleSoc)
  - ``connect()`` does not raise when broker is unreachable
  - ``_on_connect`` callback sets ``evcc_available = True``
  - ``_on_disconnect`` callback with non-zero rc sets ``evcc_available = False``
  - Default attribute values on a freshly constructed driver

Threading model note
--------------------
``EvccMqttDriver._on_message`` calls ``self._loop.call_soon_threadsafe()`` to
cross the paho thread → asyncio boundary.  In async test functions under anyio
(asyncio backend), the running loop is the anyio-managed asyncio loop — we
set ``driver._loop`` to it and then ``await asyncio.sleep(0)`` to drain the
callbacks it schedules.  ``loop.run_until_complete()`` cannot be called from
within a running loop, so all draining happens inline in async test bodies.

``anyio_mode = "auto"`` in ``pyproject.toml`` means each ``async def test_*``
is collected and run under both asyncio and trio backends (K007).  Tests that
rely on asyncio-specific APIs (``call_soon_threadsafe``) skip the trio variant
via an ``anyio_backend`` fixture guard.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from backend.evcc_mqtt_driver import EvccMqttDriver
from backend.evcc_models import EvccLoadpointState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_driver() -> EvccMqttDriver:
    """Return an EvccMqttDriver with the paho client replaced by a MagicMock.

    The paho client is mocked at the ``mqtt.Client`` constructor level so
    no real network calls are made.  ``driver._loop`` is NOT set here —
    set it in the test body when the callback exercises the thread bridge.
    """
    with patch("paho.mqtt.client.Client"):
        driver = EvccMqttDriver("192.168.0.10", 1883)
    # Replace with a fresh MagicMock so tests can inspect publish/connect calls
    driver._client = MagicMock()
    return driver


def _make_message(topic: str, payload: bytes) -> MagicMock:
    """Return a MagicMock that looks like a paho MQTTMessage."""
    msg = MagicMock()
    msg.topic = topic
    msg.payload = payload
    return msg


# ---------------------------------------------------------------------------
# Battery mode tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_battery_mode_hold_updates_attribute(anyio_backend):
    """_on_message with batteryMode payload 'hold' sets evcc_battery_mode='hold'."""
    if anyio_backend != "asyncio":
        pytest.skip("call_soon_threadsafe is asyncio-specific")

    driver = _make_driver()
    driver._loop = asyncio.get_event_loop()

    driver._on_message(None, None, _make_message("evcc/site/batteryMode", b"hold"))
    # Drain callbacks scheduled via call_soon_threadsafe
    await asyncio.sleep(0)

    assert driver.evcc_battery_mode == "hold"


@pytest.mark.anyio
async def test_battery_mode_normal_updates_attribute(anyio_backend):
    """_on_message with batteryMode payload 'normal' sets evcc_battery_mode='normal'."""
    if anyio_backend != "asyncio":
        pytest.skip("call_soon_threadsafe is asyncio-specific")

    driver = _make_driver()
    driver._loop = asyncio.get_event_loop()
    driver.evcc_battery_mode = "hold"  # pre-set to confirm the update fires

    driver._on_message(None, None, _make_message("evcc/site/batteryMode", b"normal"))
    await asyncio.sleep(0)

    assert driver.evcc_battery_mode == "normal"


# ---------------------------------------------------------------------------
# Loadpoint state tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_loadpoint_charge_power_updates_state(anyio_backend):
    """_on_message with chargePower updates evcc_loadpoint_state.charge_power_w."""
    if anyio_backend != "asyncio":
        pytest.skip("call_soon_threadsafe is asyncio-specific")

    driver = _make_driver()
    driver._loop = asyncio.get_event_loop()

    driver._on_message(None, None, _make_message("evcc/loadpoints/1/chargePower", b"3450.0"))
    await asyncio.sleep(0)

    assert driver.evcc_loadpoint_state.charge_power_w == pytest.approx(3450.0)


@pytest.mark.anyio
async def test_loadpoint_mode_updates_state(anyio_backend):
    """_on_message with mode='pv' updates evcc_loadpoint_state.mode."""
    if anyio_backend != "asyncio":
        pytest.skip("call_soon_threadsafe is asyncio-specific")

    driver = _make_driver()
    driver._loop = asyncio.get_event_loop()

    driver._on_message(None, None, _make_message("evcc/loadpoints/1/mode", b"pv"))
    await asyncio.sleep(0)

    assert driver.evcc_loadpoint_state.mode == "pv"


@pytest.mark.anyio
async def test_loadpoint_vehicle_soc_updates_state(anyio_backend):
    """_on_message with vehicleSoc='72.5' updates evcc_loadpoint_state.vehicle_soc_pct."""
    if anyio_backend != "asyncio":
        pytest.skip("call_soon_threadsafe is asyncio-specific")

    driver = _make_driver()
    driver._loop = asyncio.get_event_loop()

    driver._on_message(None, None, _make_message("evcc/loadpoints/1/vehicleSoc", b"72.5"))
    await asyncio.sleep(0)

    assert driver.evcc_loadpoint_state.vehicle_soc_pct == pytest.approx(72.5)


# ---------------------------------------------------------------------------
# connect() no-raise test
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_connect_does_not_raise_when_broker_unreachable(anyio_backend):
    """connect() catches ConnectionRefusedError and leaves evcc_available=False."""
    if anyio_backend != "asyncio":
        pytest.skip("driver.connect() calls asyncio.get_event_loop() — asyncio-specific")

    driver = _make_driver()
    driver._client.connect.side_effect = ConnectionRefusedError("Connection refused")

    # Must not raise
    await driver.connect()

    assert driver.evcc_available is False
    # loop_start must NOT have been called (connect aborted early)
    driver._client.loop_start.assert_not_called()


# ---------------------------------------------------------------------------
# _on_connect / _on_disconnect callback simulation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_on_connect_sets_available_true(anyio_backend):
    """_on_connect callback with rc=0 sets evcc_available=True."""
    if anyio_backend != "asyncio":
        pytest.skip("call_soon_threadsafe is asyncio-specific")

    driver = _make_driver()
    driver._loop = asyncio.get_event_loop()
    driver.evcc_available = False

    # Simulate paho calling the on_connect callback after CONNACK (rc=0)
    driver._on_connect(
        client=driver._client,
        userdata=None,
        connect_flags=MagicMock(),
        reason_code=0,
        properties=None,
    )
    # Drain call_soon_threadsafe callbacks
    await asyncio.sleep(0)

    assert driver.evcc_available is True


@pytest.mark.anyio
async def test_on_disconnect_with_nonzero_rc_sets_available_false(anyio_backend):
    """_on_disconnect callback with rc=1 sets evcc_available=False."""
    if anyio_backend != "asyncio":
        pytest.skip("call_soon_threadsafe is asyncio-specific")

    driver = _make_driver()
    driver._loop = asyncio.get_event_loop()
    driver.evcc_available = True  # pre-set to confirm the update fires

    # Simulate paho calling the on_disconnect callback with unexpected disconnect
    driver._on_disconnect(
        client=driver._client,
        userdata=None,
        disconnect_flags=MagicMock(),
        reason_code=1,
        properties=None,
    )
    # Drain call_soon_threadsafe callbacks
    await asyncio.sleep(0)

    assert driver.evcc_available is False


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def test_default_state():
    """Fresh EvccMqttDriver has safe default attribute values."""
    driver = _make_driver()
    assert driver.evcc_battery_mode == "normal"
    assert driver.evcc_available is False
    assert isinstance(driver.evcc_loadpoint_state, EvccLoadpointState)
    assert driver.evcc_loadpoint_state.mode == "off"
    assert driver.evcc_loadpoint_state.charge_power_w == pytest.approx(0.0)
    assert driver.evcc_loadpoint_state.vehicle_soc_pct is None
    assert driver.evcc_loadpoint_state.charging is False
    assert driver.evcc_loadpoint_state.connected is False
