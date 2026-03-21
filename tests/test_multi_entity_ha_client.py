"""Tests for MultiEntityHaClient.

Covers:
  - Concurrent entity polling: all values populated on success
  - Single-entity failure: that field is None; others remain populated
  - get_all_values() returns a shallow copy (mutation does not affect cache)
  - get_cached_value() shim returns heat_pump_power_w value
  - Wildcard expansion: GET /api/states prefix-filters to 3 entities
  - All-entity failure: get_all_values() returns dict with all None values
  - Failure-path warning log emitted on entity poll error

Mock strategy: patch httpx.AsyncClient in backend.ha_rest_client, same pattern
as test_ha_rest_client.py.

anyio_mode = "auto" is configured globally in pyproject.toml — async def test_*
functions are collected without @pytest.mark.anyio.

Note on asyncio.gather vs anyio under trio: production `_poll_loop` uses
`asyncio.gather`, which requires the asyncio event loop.  Tests invoke
`_poll_one` individually and sequentially so they run correctly under both
asyncio and trio backends without pulling in asyncio-specific APIs.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.ha_rest_client import (
    MultiEntityHaClient,
    _float_converter,
    _str_converter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_URL = "http://homeassistant.local:8123"
_TOKEN = "test-token"


def _make_entity_map(
    *pairs: tuple[str, str],
) -> dict[str, tuple[str, object]]:
    """Build an entity_map with _float_converter for each (field, entity_id) pair."""
    return {field: (entity_id, _float_converter) for field, entity_id in pairs}


def _make_response(state: str, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"state": state, "entity_id": "sensor.x"}
    if status == 200:
        resp.raise_for_status.return_value = None
    else:
        req = httpx.Request("GET", f"{_URL}/api/states/sensor.x")
        real_resp = httpx.Response(status, request=req)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status}", request=req, response=real_resp
        )
    return resp


def _patch_http(side_effect_fn):
    """Patch httpx.AsyncClient so its async context manager uses side_effect_fn."""
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(side_effect=side_effect_fn)
    return patch(
        "backend.ha_rest_client.httpx.AsyncClient",
        return_value=mock_http,
    ), mock_http


# ---------------------------------------------------------------------------
# Test 1 — all entities succeed
# ---------------------------------------------------------------------------

async def test_get_entity_value_returns_float_on_success():
    """Two entities polled sequentially — both field values populated."""
    entity_map = _make_entity_map(
        ("heat_pump_power_w", "sensor.warmepumpe_total_active_power"),
        ("outdoor_temp_c", "sensor.ems_esp_boiler_aussentemperatur"),
    )

    responses = {
        "sensor.warmepumpe_total_active_power": "1500.0",
        "sensor.ems_esp_boiler_aussentemperatur": "7.5",
    }

    async def fake_get(url, headers=None):
        entity_id = url.split("/api/states/")[-1]
        return _make_response(responses[entity_id])

    patch_ctx, _ = _patch_http(fake_get)
    client = MultiEntityHaClient(_URL, _TOKEN, entity_map, poll_interval_s=999.0)

    with patch_ctx:
        for field, (eid, conv) in client._entity_map.items():
            await client._poll_one(field, eid, conv)

    assert client.get_entity_value("heat_pump_power_w") == pytest.approx(1500.0)
    assert client.get_entity_value("outdoor_temp_c") == pytest.approx(7.5)


# ---------------------------------------------------------------------------
# Test 2 — single entity failure → that field is None, other is populated
# ---------------------------------------------------------------------------

async def test_single_entity_failure_returns_none_for_that_field():
    """One entity returns 500; the other succeeds — failed field is None."""
    entity_map = _make_entity_map(
        ("heat_pump_power_w", "sensor.warmepumpe_total_active_power"),
        ("outdoor_temp_c", "sensor.ems_esp_boiler_aussentemperatur"),
    )

    async def fake_get(url, headers=None):
        if "warmepumpe" in url:
            return _make_response("error", status=500)
        return _make_response("12.3")

    patch_ctx, _ = _patch_http(fake_get)
    client = MultiEntityHaClient(_URL, _TOKEN, entity_map, poll_interval_s=999.0)

    with patch_ctx:
        for field, (eid, conv) in client._entity_map.items():
            await client._poll_one(field, eid, conv)

    assert client.get_entity_value("heat_pump_power_w") is None
    assert client.get_entity_value("outdoor_temp_c") == pytest.approx(12.3)


# ---------------------------------------------------------------------------
# Test 3 — get_all_values() returns shallow copy
# ---------------------------------------------------------------------------

async def test_get_all_values_returns_shallow_copy():
    """Mutating the returned dict does not affect the internal cache."""
    entity_map = _make_entity_map(("heat_pump_power_w", "sensor.warmepumpe_total_active_power"))

    async def fake_get(url, headers=None):
        return _make_response("999.0")

    patch_ctx, _ = _patch_http(fake_get)
    client = MultiEntityHaClient(_URL, _TOKEN, entity_map, poll_interval_s=999.0)

    with patch_ctx:
        for field, (eid, conv) in client._entity_map.items():
            await client._poll_one(field, eid, conv)

    snapshot = client.get_all_values()
    assert snapshot["heat_pump_power_w"] == pytest.approx(999.0)

    # Mutate the snapshot
    snapshot["heat_pump_power_w"] = -1.0
    snapshot["new_field"] = 42.0

    # Internal cache must be unchanged
    assert client.get_entity_value("heat_pump_power_w") == pytest.approx(999.0)
    assert client.get_entity_value("new_field") is None


# ---------------------------------------------------------------------------
# Test 4 — get_cached_value() shim returns heat_pump_power_w
# ---------------------------------------------------------------------------

async def test_get_cached_value_shim_returns_heat_pump_power_w():
    """get_cached_value() shim returns the heat_pump_power_w field value."""
    entity_map = _make_entity_map(
        ("heat_pump_power_w", "sensor.warmepumpe_total_active_power"),
        ("outdoor_temp_c", "sensor.ems_esp_boiler_aussentemperatur"),
    )

    responses = {
        "sensor.warmepumpe_total_active_power": "2200.0",
        "sensor.ems_esp_boiler_aussentemperatur": "5.0",
    }

    async def fake_get(url, headers=None):
        entity_id = url.split("/api/states/")[-1]
        return _make_response(responses[entity_id])

    patch_ctx, _ = _patch_http(fake_get)
    client = MultiEntityHaClient(_URL, _TOKEN, entity_map, poll_interval_s=999.0)

    with patch_ctx:
        for field, (eid, conv) in client._entity_map.items():
            await client._poll_one(field, eid, conv)

    shim_value = client.get_cached_value()
    assert shim_value == pytest.approx(2200.0)
    assert isinstance(shim_value, float)


# ---------------------------------------------------------------------------
# Test 5 — wildcard expansion
# ---------------------------------------------------------------------------

async def test_wildcard_expansion_fetches_all_matching_entities():
    """entity_id ending with * expands to all matching entities from /api/states."""
    entity_map: dict[str, tuple[str, object]] = {
        "ha_sensor": ("sensor.ha_*", _float_converter),
    }

    all_states = [
        {"entity_id": "sensor.ha_alpha", "state": "1.0"},
        {"entity_id": "sensor.ha_beta", "state": "2.0"},
        {"entity_id": "sensor.ha_gamma", "state": "3.0"},
        {"entity_id": "sensor.other_thing", "state": "99.0"},
    ]

    async def fake_get(url, headers=None):
        if url.endswith("/api/states"):
            resp = MagicMock()
            resp.json.return_value = all_states
            resp.raise_for_status.return_value = None
            return resp
        raise ValueError(f"Unexpected URL: {url}")

    patch_ctx, _ = _patch_http(fake_get)
    client = MultiEntityHaClient(_URL, _TOKEN, entity_map, poll_interval_s=999.0)

    with patch_ctx:
        await client._expand_wildcards()

    # Original wildcard field should be removed; 3 expanded fields added
    assert "ha_sensor" not in client._entity_map
    expanded_entity_ids = {eid for (eid, _) in client._entity_map.values()}
    assert "sensor.ha_alpha" in expanded_entity_ids
    assert "sensor.ha_beta" in expanded_entity_ids
    assert "sensor.ha_gamma" in expanded_entity_ids
    assert "sensor.other_thing" not in expanded_entity_ids
    assert len(client._entity_map) == 3


# ---------------------------------------------------------------------------
# Test 6 — all entities fail → all values are None
# ---------------------------------------------------------------------------

async def test_all_entities_fail_all_values_none():
    """All polls fail with a network error — get_all_values() has all None."""
    entity_map = _make_entity_map(
        ("heat_pump_power_w", "sensor.warmepumpe_total_active_power"),
        ("outdoor_temp_c", "sensor.ems_esp_boiler_aussentemperatur"),
    )

    async def fake_get(url, headers=None):
        raise httpx.ConnectError("connection refused")

    patch_ctx, _ = _patch_http(fake_get)
    client = MultiEntityHaClient(_URL, _TOKEN, entity_map, poll_interval_s=999.0)

    with patch_ctx:
        for field, (eid, conv) in client._entity_map.items():
            await client._poll_one(field, eid, conv)

    all_vals = client.get_all_values()
    assert set(all_vals.keys()) == {"heat_pump_power_w", "outdoor_temp_c"}
    assert all_vals["heat_pump_power_w"] is None
    assert all_vals["outdoor_temp_c"] is None


# ---------------------------------------------------------------------------
# Observability: failure-path warning log
# ---------------------------------------------------------------------------

async def test_entity_failure_logs_warning(caplog):
    """A failing entity poll logs a WARNING with entity_id and exc."""
    entity_map = _make_entity_map(("heat_pump_power_w", "sensor.warmepumpe_total_active_power"))

    async def fake_get(url, headers=None):
        raise httpx.ConnectError("refused")

    patch_ctx, _ = _patch_http(fake_get)
    client = MultiEntityHaClient(_URL, _TOKEN, entity_map, poll_interval_s=999.0)

    with caplog.at_level(logging.WARNING, logger="backend.ha_rest_client"):
        with patch_ctx:
            await client._poll_one(
                "heat_pump_power_w",
                "sensor.warmepumpe_total_active_power",
                _float_converter,
            )

    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("HA REST multi-entity poll failed" in m for m in warning_msgs)
    assert any("sensor.warmepumpe_total_active_power" in m for m in warning_msgs)
