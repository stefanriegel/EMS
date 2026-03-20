"""Tests for backend/setup_api.py — setup wizard API endpoints.

Endpoint tests use a minimal no-lifespan FastAPI app (K021 pattern) so they
run without any real hardware or env vars.

The lifespan degraded-mode test uses ``TestClient(app)`` from ``backend.main``
(K020 pattern) — it exercises the real lifespan with HUAWEI_HOST removed so
the KeyError path is exercised.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.setup_api import setup_router
from backend.setup_config import EmsSetupConfig, load_setup_config


# ---------------------------------------------------------------------------
# Minimal no-lifespan test app (K021 pattern)
# ---------------------------------------------------------------------------


def _make_setup_app(config_path: str) -> FastAPI:
    """Minimal FastAPI app with no lifespan — for pure endpoint tests."""
    test_app = FastAPI()
    test_app.include_router(setup_router)
    test_app.state.setup_config_path = config_path
    return test_app


# ---------------------------------------------------------------------------
# Endpoint tests (no lifespan, no drivers needed)
# ---------------------------------------------------------------------------


class TestSetupStatusEndpoint:
    def test_status_no_config(self, tmp_path):
        """GET /api/setup/status on a fresh (empty) tmp_path → setup_complete=False."""
        config_path = str(tmp_path / "ems_config.json")
        app = _make_setup_app(config_path)
        with TestClient(app) as client:
            resp = client.get("/api/setup/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["setup_complete"] is False
        assert body["config_exists"] is False
        assert body["config_path"] == config_path

    def test_status_after_complete(self, tmp_path):
        """POST /complete then GET /status → setup_complete=True, config_exists=True."""
        config_path = str(tmp_path / "ems_config.json")
        app = _make_setup_app(config_path)
        payload = _full_wizard_payload()
        with TestClient(app) as client:
            post_resp = client.post("/api/setup/complete", json=payload)
            assert post_resp.status_code == 200
            get_resp = client.get("/api/setup/status")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["setup_complete"] is True
        assert body["config_exists"] is True


class TestSetupCompleteEndpoint:
    def test_complete_writes_config(self, tmp_path):
        """POST /api/setup/complete → ok=True; config is persisted and loadable."""
        config_path = str(tmp_path / "ems_config.json")
        app = _make_setup_app(config_path)
        payload = _full_wizard_payload()
        with TestClient(app) as client:
            resp = client.post("/api/setup/complete", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # Verify persisted data
        cfg = load_setup_config(config_path)
        assert cfg is not None
        assert cfg.huawei_host == "10.0.0.1"
        assert cfg.victron_host == "10.0.0.2"
        assert cfg.ha_url == "http://homeassistant.local:8123"


# ---------------------------------------------------------------------------
# Probe endpoint tests
# ---------------------------------------------------------------------------


class TestProbeEndpoint:
    def test_probe_modbus_refused(self):
        """POST /api/setup/probe/modbus with port 1 → {ok: false, error: str}."""
        # Port 1 is always connection-refused on any standard system
        config_path = "/tmp/nonexistent_ems.json"
        app = _make_setup_app(config_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/setup/probe/modbus",
                json={"host": "127.0.0.1", "port": 1},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "error" in body
        assert isinstance(body["error"], str)
        assert len(body["error"]) > 0

    def test_probe_evcc_connection_error(self):
        """POST /api/setup/probe/evcc with port 1 → {ok: false, error: str}."""
        config_path = "/tmp/nonexistent_ems.json"
        app = _make_setup_app(config_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/setup/probe/evcc",
                json={"host": "127.0.0.1", "port": 1},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "error" in body
        assert isinstance(body["error"], str)
        assert len(body["error"]) > 0

    def test_probe_ha_rest_sensor_none(self, monkeypatch):
        """POST /api/setup/probe/ha_rest with monkeypatched sensor returning None → {ok: false}."""
        monkeypatch.setattr(
            "backend.setup_api.HomeAssistantClient.get_sensor_value",
            AsyncMock(return_value=None),
        )
        config_path = "/tmp/nonexistent_ems.json"
        app = _make_setup_app(config_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/setup/probe/ha_rest",
                json={
                    "url": "http://homeassistant.local:8123",
                    "token": "tok",
                    "entity_id": "sensor.test",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "error" in body
        assert "None" in body["error"]


# ---------------------------------------------------------------------------
# Lifespan degraded-mode test (K020 pattern — uses real lifespan from main.py)
# ---------------------------------------------------------------------------


class TestLifespanDegradedMode:
    def test_lifespan_degraded_mode(self, tmp_path):
        """When HUAWEI_HOST is absent and no setup config exists, lifespan must:
        - not crash (KeyError is caught)
        - set app.state.orchestrator = None
        - still serve GET /api/setup/status with 200
        """
        from backend.main import app

        # Point EMS_CONFIG_PATH at a non-existent file in tmp_path
        config_path = str(tmp_path / "ems_config.json")

        # Ensure HUAWEI_HOST is absent — remove all orchestrator env vars.
        # EMS_CONFIG_PATH is set so the lifespan uses tmp_path (no config there).
        with patch.dict(
            "os.environ",
            {"EMS_CONFIG_PATH": config_path},
            clear=True,  # Remove ALL env vars so HUAWEI_HOST, VICTRON_HOST etc. are gone
        ):
            # The degraded path raises KeyError before InfluxDB is ever constructed
            # so no influx patch is needed.
            with TestClient(app) as client:
                # Orchestrator must be None — degraded mode
                assert app.state.orchestrator is None

                # Setup status endpoint must still be reachable
                resp = client.get("/api/setup/status")
                assert resp.status_code == 200
                body = resp.json()
                assert body["setup_complete"] is False
                assert body["config_exists"] is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _full_wizard_payload() -> dict:
    """Return a complete wizard payload suitable for POST /api/setup/complete."""
    return {
        "huawei_host": "10.0.0.1",
        "huawei_port": 502,
        "victron_host": "10.0.0.2",
        "victron_port": 1883,
        "evcc_host": "10.0.0.3",
        "evcc_port": 7070,
        "evcc_mqtt_host": "10.0.0.3",
        "evcc_mqtt_port": 1883,
        "ha_url": "http://homeassistant.local:8123",
        "ha_token": "secret-token",
        "ha_heat_pump_entity_id": "sensor.heat_pump",
        "octopus_off_peak_start_min": 30,
        "octopus_off_peak_end_min": 330,
        "octopus_off_peak_rate_eur_kwh": 0.08,
        "octopus_peak_rate_eur_kwh": 0.28,
        "huawei_min_soc_pct": 10.0,
        "huawei_max_soc_pct": 95.0,
        "victron_min_soc_pct": 15.0,
        "victron_max_soc_pct": 95.0,
    }
