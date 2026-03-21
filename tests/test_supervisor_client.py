"""Tests for backend.supervisor_client."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.supervisor_client import (
    SupervisorClient,
    MqttServiceInfo,
    EvccAddonInfo,
    HaProxyConfig,
    _resolve_host_port,
)


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def test_resolve_host_port_found():
    network = {"7070/tcp": 7070, "5200/tcp": 5200, "1883/tcp": None}
    assert _resolve_host_port(network, 7070) == 7070
    assert _resolve_host_port(network, 5200) == 5200


def test_resolve_host_port_not_found():
    network = {"7070/tcp": 7070}
    assert _resolve_host_port(network, 9999) is None


def test_resolve_host_port_none_mapped():
    network = {"1883/tcp": None}  # port exists but host mapping disabled
    assert _resolve_host_port(network, 1883) is None


def test_resolve_host_port_malformed_key():
    network = {"badkey": 1234, "7070/tcp": 8080}
    assert _resolve_host_port(network, 7070) == 8080


# ---------------------------------------------------------------------------
# from_env
# ---------------------------------------------------------------------------

def test_from_env_without_token(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert SupervisorClient.from_env() is None


def test_from_env_with_token(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "mytoken")
    client = SupervisorClient.from_env()
    assert isinstance(client, SupervisorClient)


# ---------------------------------------------------------------------------
# get_ha_proxy_config
# ---------------------------------------------------------------------------

def test_get_ha_proxy_config():
    client = SupervisorClient(token="tok123")
    cfg = client.get_ha_proxy_config()
    assert isinstance(cfg, HaProxyConfig)
    assert cfg.base_url == "http://supervisor/core"
    assert cfg.token == "tok123"


# ---------------------------------------------------------------------------
# get_mqtt_service
# ---------------------------------------------------------------------------

def _make_http_response(json_data: dict, status: int = 200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = json_data
    mock.raise_for_status = MagicMock()
    return mock


@pytest.mark.anyio
async def test_get_mqtt_service_success():
    client = SupervisorClient(token="tok")
    payload = {
        "data": {
            "host": "core-mosquitto",
            "port": 1883,
            "username": "homeassistant",
            "password": "secret",
            "ssl": False,
        }
    }
    mock_resp = _make_http_response(payload)
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch("backend.supervisor_client.httpx.AsyncClient", return_value=mock_http):
        result = await client.get_mqtt_service()

    assert isinstance(result, MqttServiceInfo)
    assert result.host == "core-mosquitto"
    assert result.port == 1883
    assert result.username == "homeassistant"
    assert result.password == "secret"
    assert result.ssl is False


@pytest.mark.anyio
async def test_get_mqtt_service_empty_data():
    client = SupervisorClient(token="tok")
    mock_resp = _make_http_response({"data": {}})
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch("backend.supervisor_client.httpx.AsyncClient", return_value=mock_http):
        result = await client.get_mqtt_service()

    assert result is None


@pytest.mark.anyio
async def test_get_mqtt_service_network_error():
    client = SupervisorClient(token="tok")
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(side_effect=ConnectionError("refused"))

    with patch("backend.supervisor_client.httpx.AsyncClient", return_value=mock_http):
        result = await client.get_mqtt_service()

    assert result is None


# ---------------------------------------------------------------------------
# get_evcc_info
# ---------------------------------------------------------------------------

_ADDON_LIST = {
    "data": {
        "addons": [
            {"slug": "core_mosquitto", "state": "started", "name": "Mosquitto"},
            {"slug": "49686a9f_evcc", "state": "started", "name": "evcc"},
            {"slug": "49686a9f_evcc-nightly", "state": "error", "name": "evcc nightly"},
        ]
    }
}

_EVCC_INFO = {
    "data": {
        "slug": "49686a9f_evcc",
        "state": "started",
        "hostname": "49686a9f-evcc",
        "network": {
            "7070/tcp": 7070,
            "5200/tcp": 5200,
        },
    }
}


@pytest.mark.anyio
async def test_get_evcc_info_found():
    client = SupervisorClient(token="tok")

    call_count = 0

    async def fake_get(url, headers=None):
        nonlocal call_count
        call_count += 1
        if "/addons" in url and "evcc" not in url.split("/addons/")[-1]:
            return _make_http_response(_ADDON_LIST)
        return _make_http_response(_EVCC_INFO)

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(side_effect=fake_get)

    with patch("backend.supervisor_client.httpx.AsyncClient", return_value=mock_http):
        result = await client.get_evcc_info()

    assert isinstance(result, EvccAddonInfo)
    assert result.slug == "49686a9f_evcc"
    assert result.api_host == "127.0.0.1"
    assert result.api_port == 7070
    assert result.mqtt_port == 5200


@pytest.mark.anyio
async def test_get_evcc_info_not_installed():
    client = SupervisorClient(token="tok")
    payload = {"data": {"addons": [
        {"slug": "core_mosquitto", "state": "started", "name": "Mosquitto"},
    ]}}
    mock_resp = _make_http_response(payload)
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch("backend.supervisor_client.httpx.AsyncClient", return_value=mock_http):
        result = await client.get_evcc_info()

    assert result is None


@pytest.mark.anyio
async def test_get_evcc_info_stopped_addon_ignored():
    """A stopped EVCC addon should not be returned."""
    client = SupervisorClient(token="tok")
    payload = {"data": {"addons": [
        {"slug": "49686a9f_evcc", "state": "stopped", "name": "evcc"},
    ]}}
    mock_resp = _make_http_response(payload)
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch("backend.supervisor_client.httpx.AsyncClient", return_value=mock_http):
        result = await client.get_evcc_info()

    assert result is None


@pytest.mark.anyio
async def test_get_evcc_info_network_error():
    client = SupervisorClient(token="tok")
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(side_effect=ConnectionError("refused"))

    with patch("backend.supervisor_client.httpx.AsyncClient", return_value=mock_http):
        result = await client.get_evcc_info()

    assert result is None
