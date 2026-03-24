"""Unit tests for the VRM REST client and VRM/DESS config dataclasses."""
from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.dess_models import VrmDiagnostics


# ---------------------------------------------------------------------------
# VrmConfig tests
# ---------------------------------------------------------------------------


class TestVrmConfig:
    """VrmConfig.from_env() reads env vars with safe empty defaults."""

    def test_from_env_reads_all_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from backend.config import VrmConfig

        monkeypatch.setenv("VRM_TOKEN", "my-pat-token")
        monkeypatch.setenv("VRM_SITE_ID", "12345")
        monkeypatch.setenv("VRM_POLL_INTERVAL_S", "120")
        cfg = VrmConfig.from_env()
        assert cfg.token == "my-pat-token"
        assert cfg.site_id == "12345"
        assert cfg.poll_interval_s == 120.0

    def test_from_env_empty_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from backend.config import VrmConfig

        monkeypatch.delenv("VRM_TOKEN", raising=False)
        monkeypatch.delenv("VRM_SITE_ID", raising=False)
        monkeypatch.delenv("VRM_POLL_INTERVAL_S", raising=False)
        cfg = VrmConfig.from_env()
        assert cfg.token == ""
        assert cfg.site_id == ""
        assert cfg.poll_interval_s == 300.0


class TestDessConfig:
    """DessConfig.from_env() reads env vars with VICTRON_HOST fallback."""

    def test_from_env_reads_all_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from backend.config import DessConfig

        monkeypatch.setenv("DESS_MQTT_HOST", "10.0.0.5")
        monkeypatch.setenv("DESS_MQTT_PORT", "1884")
        monkeypatch.setenv("DESS_PORTAL_ID", "abc123")
        cfg = DessConfig.from_env()
        assert cfg.host == "10.0.0.5"
        assert cfg.port == 1884
        assert cfg.portal_id == "abc123"

    def test_from_env_falls_back_to_victron_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.config import DessConfig

        monkeypatch.delenv("DESS_MQTT_HOST", raising=False)
        monkeypatch.setenv("VICTRON_HOST", "192.168.1.99")
        monkeypatch.setenv("DESS_PORTAL_ID", "portal1")
        cfg = DessConfig.from_env()
        assert cfg.host == "192.168.1.99"

    def test_from_env_empty_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from backend.config import DessConfig

        monkeypatch.delenv("DESS_MQTT_HOST", raising=False)
        monkeypatch.delenv("VICTRON_HOST", raising=False)
        monkeypatch.delenv("DESS_MQTT_PORT", raising=False)
        monkeypatch.delenv("DESS_PORTAL_ID", raising=False)
        cfg = DessConfig.from_env()
        assert cfg.host == ""
        assert cfg.port == 1883
        assert cfg.portal_id == ""


# ---------------------------------------------------------------------------
# VrmClient tests
# ---------------------------------------------------------------------------


class TestVrmClientFetchDiagnostics:
    """VrmClient._fetch_diagnostics parses mock httpx responses."""

    @pytest.fixture()
    def vrm_diagnostics_response(self) -> dict:
        """Mock VRM diagnostics API response."""
        return {
            "records": [
                {
                    "idDataAttribute": 51,
                    "rawValue": "72.5",
                    "description": "Battery State of Charge",
                },
                {
                    "idDataAttribute": 49,
                    "rawValue": "-1500",
                    "description": "Battery Power",
                },
                {
                    "idDataAttribute": 1,
                    "rawValue": "200",
                    "description": "Grid power L1",
                },
                {
                    "idDataAttribute": 131,
                    "rawValue": "3200",
                    "description": "PV - AC-coupled on output L1",
                },
                {
                    "idDataAttribute": 73,
                    "rawValue": "1800",
                    "description": "Consumption L1",
                },
            ]
        }

    async def test_happy_path_parses_diagnostics(
        self, vrm_diagnostics_response: dict
    ) -> None:
        from backend.vrm_client import VrmClient

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=vrm_diagnostics_response)
        )
        client = VrmClient(token="test-token", site_id=99, poll_interval_s=60)
        client._client = httpx.AsyncClient(
            base_url="https://vrmapi.victronenergy.com",
            transport=transport,
        )
        await client._fetch_diagnostics()
        assert client.available is True
        assert client.diagnostics.battery_soc_pct == 72.5
        assert client.diagnostics.battery_power_w == -1500.0

    async def test_handles_429_rate_limit(self) -> None:
        from backend.vrm_client import VrmClient

        transport = httpx.MockTransport(
            lambda request: httpx.Response(429, text="Rate limited")
        )
        client = VrmClient(token="test-token", site_id=99)
        client._client = httpx.AsyncClient(
            base_url="https://vrmapi.victronenergy.com",
            transport=transport,
        )
        await client._fetch_diagnostics()
        assert client.available is False

    async def test_handles_connection_error(self) -> None:
        from backend.vrm_client import VrmClient

        client = VrmClient(token="test-token", site_id=99, poll_interval_s=0.01)

        def raise_error(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        client._client = httpx.AsyncClient(
            base_url="https://vrmapi.victronenergy.com",
            transport=httpx.MockTransport(raise_error),
        )
        # _poll_loop catches exceptions, but _fetch_diagnostics raises.
        # The poll_loop wrapper sets available=False on exception.
        with pytest.raises(httpx.ConnectError):
            await client._fetch_diagnostics()
        # available should not have been set to True
        assert client.available is False

    async def test_marks_stale_after_15_minutes(
        self, vrm_diagnostics_response: dict
    ) -> None:
        from backend.vrm_client import VrmClient

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=vrm_diagnostics_response)
        )
        client = VrmClient(token="test-token", site_id=99)
        client._client = httpx.AsyncClient(
            base_url="https://vrmapi.victronenergy.com",
            transport=transport,
        )
        await client._fetch_diagnostics()
        assert client.available is True

        # Simulate staleness by setting timestamp to 16 minutes ago
        client._diagnostics.timestamp = time.time() - 960
        # Re-fetch should parse new data with fresh timestamp
        await client._fetch_diagnostics()
        # After a fresh fetch, available should be True with a recent timestamp
        assert client.available is True

        # Now test actual staleness check: set old timestamp and check property
        client._diagnostics.timestamp = time.time() - 960
        client._check_staleness()
        assert client.available is False
