"""Tests for main.py lifespan wiring of EVCC, HA MQTT, and Telegram services.

Lifespan tests use TestClient(app) with all hardware drivers mocked.
WS payload tests bypass the lifespan using a minimal test app (same pattern
as test_ws_state_sends_first_frame in test_api.py).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.main import app


_REQUIRED_ENV = {
    "HUAWEI_HOST": "127.0.0.1",
    "VICTRON_HOST": "127.0.0.1",
}


def _make_mock_huawei():
    d = MagicMock()
    d.connect = AsyncMock()
    d.close = AsyncMock()
    d.read_master = AsyncMock(return_value=None)
    d.read_battery = AsyncMock(return_value=None)
    d.validate_connectivity = AsyncMock(return_value=True)
    d.write_max_charge_power = AsyncMock()
    d.write_max_discharge_power = AsyncMock()
    d.write_battery_mode = AsyncMock()
    return d


def _make_mock_victron():
    d = MagicMock()
    d.connect = AsyncMock()
    d.close = AsyncMock()
    d.read_system_state = MagicMock(return_value=None)
    d.validate_connectivity = AsyncMock(return_value=True)
    return d


def _make_mock_evcc_driver():
    driver = MagicMock()
    driver.connect = AsyncMock()
    driver.close = AsyncMock()
    driver.evcc_battery_mode = "normal"
    driver.evcc_loadpoint_state = MagicMock(
        mode="off", charge_power_w=0.0, vehicle_soc_pct=None,
        charging=False, connected=False,
    )
    return driver


def _make_mock_ha_client():
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client._connected = True
    return client


@contextmanager
def _lifespan_patches(evcc_driver=None, ha_client=None, extra_env=None):
    evcc_driver = evcc_driver or _make_mock_evcc_driver()
    ha_client = ha_client or _make_mock_ha_client()
    env = {**_REQUIRED_ENV, **(extra_env or {})}
    with patch.dict("os.environ", env):
        with patch("backend.main.HuaweiDriver", return_value=_make_mock_huawei()):
            with patch("backend.main.VictronDriver", return_value=_make_mock_victron()):
                with patch("backend.main.EvccMqttDriver", return_value=evcc_driver):
                    with patch("backend.main.HomeAssistantMqttClient", return_value=ha_client):
                        yield evcc_driver, ha_client


def _make_ws_test_app(evcc_driver=None, ha_client=None):
    """Minimal FastAPI app (no lifespan) for WS payload tests."""
    from backend.api import api_router, get_orchestrator
    mock_orch = MagicMock()
    mock_orch.get_state = MagicMock(return_value=None)
    mock_orch.get_device_snapshot = MagicMock(return_value={
        "huawei": {"available": False},
        "victron": {"available": False},
    })
    test_app = FastAPI(title="EMS-ws-test")
    test_app.include_router(api_router)
    test_app.dependency_overrides[get_orchestrator] = lambda: mock_orch
    test_app.state.orchestrator = mock_orch
    test_app.state.evcc_driver = evcc_driver or _make_mock_evcc_driver()
    test_app.state.ha_mqtt_client = ha_client or _make_mock_ha_client()
    return test_app


class TestLifespanWiring:
    def test_evcc_driver_stored_on_app_state(self):
        with _lifespan_patches() as (mock_driver, _):
            with TestClient(app) as client:
                assert app.state.evcc_driver is mock_driver

    def test_ha_mqtt_client_stored_on_app_state(self):
        with _lifespan_patches() as (_, mock_ha):
            with TestClient(app) as client:
                assert app.state.ha_mqtt_client is mock_ha

    def test_notifier_none_when_no_env_vars(self):
        env_clean = {k: v for k, v in os.environ.items()
                     if k not in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
        env_clean.update(_REQUIRED_ENV)
        with patch("backend.main.HuaweiDriver", return_value=_make_mock_huawei()):
            with patch("backend.main.VictronDriver", return_value=_make_mock_victron()):
                with patch("backend.main.EvccMqttDriver", return_value=_make_mock_evcc_driver()):
                    with patch("backend.main.HomeAssistantMqttClient", return_value=_make_mock_ha_client()):
                        with patch.dict("os.environ", env_clean, clear=True):
                            with TestClient(app) as client:
                                assert app.state.notifier is None

    def test_notifier_set_when_env_vars_present(self):
        from backend.notifier import TelegramNotifier
        extra = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "999"}
        with _lifespan_patches(extra_env=extra):
            with TestClient(app) as client:
                assert isinstance(app.state.notifier, TelegramNotifier)

    def test_evcc_monitor_injected_into_orchestrator(self):
        with _lifespan_patches(extra_env={"EMS_CONTROL_MODE": "legacy"}) as (mock_driver, _):
            with TestClient(app) as client:
                assert app.state.orchestrator._evcc_monitor is mock_driver

    def test_degraded_mode_when_hosts_empty(self):
        """Empty HUAWEI_HOST/VICTRON_HOST (HA add-on default) → setup-only mode."""
        env = {"HUAWEI_HOST": "", "VICTRON_HOST": ""}
        with patch.dict("os.environ", env, clear=False):
            with TestClient(app) as client:
                assert app.state.orchestrator is None
                resp = client.get("/api/health")
                assert resp.status_code == 200
                assert resp.json()["status"] == "offline"

    def test_degraded_mode_when_hosts_missing(self):
        """Missing HUAWEI_HOST/VICTRON_HOST → setup-only mode."""
        # Remove both keys entirely
        env = {k: v for k, v in os.environ.items()
               if k not in ("HUAWEI_HOST", "VICTRON_HOST")}
        with patch.dict("os.environ", env, clear=True):
            with TestClient(app) as client:
                assert app.state.orchestrator is None


class TestWebSocketEvccPayload:
    def test_ws_state_includes_evcc_key(self):
        test_app = _make_ws_test_app()
        with TestClient(test_app).websocket_connect("/api/ws/state") as ws:
            data = ws.receive_json()
        assert "evcc" in data

    def test_ws_state_evcc_has_expected_keys(self):
        test_app = _make_ws_test_app()
        with TestClient(test_app).websocket_connect("/api/ws/state") as ws:
            data = ws.receive_json()
        evcc = data["evcc"]
        assert "battery_mode" in evcc
        assert "loadpoint_mode" in evcc
        assert "charge_power_w" in evcc
        assert "vehicle_soc_pct" in evcc
        assert "charging" in evcc
        assert "connected" in evcc

    def test_ws_state_includes_ha_mqtt_connected(self):
        test_app = _make_ws_test_app()
        with TestClient(test_app).websocket_connect("/api/ws/state") as ws:
            data = ws.receive_json()
        assert "ha_mqtt_connected" in data
        assert isinstance(data["ha_mqtt_connected"], bool)

    def test_ws_state_evcc_values_from_driver(self):
        mock_driver = _make_mock_evcc_driver()
        mock_driver.evcc_battery_mode = "hold"
        test_app = _make_ws_test_app(evcc_driver=mock_driver)
        with TestClient(test_app).websocket_connect("/api/ws/state") as ws:
            data = ws.receive_json()
        assert data["evcc"]["battery_mode"] == "hold"

    def test_ws_state_ha_mqtt_connected_reflects_client(self):
        mock_ha = _make_mock_ha_client()
        mock_ha._connected = False
        test_app = _make_ws_test_app(ha_client=mock_ha)
        with TestClient(test_app).websocket_connect("/api/ws/state") as ws:
            data = ws.receive_json()
        assert data["ha_mqtt_connected"] is False


class TestInfluxLifespanWiring:
    """Verify InfluxDB is optional — lifespan must work with and without it."""

    def test_metrics_reader_none_when_influx_not_configured(self):
        """metrics_reader must be None when INFLUXDB_URL is absent."""
        env_no_influx = {
            k: v for k, v in os.environ.items()
            if k not in ("INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_DATABASE",
                          "INFLUXDB_BUCKET", "INFLUXDB_USERNAME", "INFLUXDB_PASSWORD")
        }
        env_no_influx.update(_REQUIRED_ENV)
        with patch("backend.main.HuaweiDriver", return_value=_make_mock_huawei()):
            with patch("backend.main.VictronDriver", return_value=_make_mock_victron()):
                with patch("backend.main.EvccMqttDriver", return_value=_make_mock_evcc_driver()):
                    with patch("backend.main.HomeAssistantMqttClient", return_value=_make_mock_ha_client()):
                        with patch.dict("os.environ", env_no_influx, clear=True):
                            with TestClient(app) as client:
                                assert app.state.metrics_reader is None

    def test_influx_writer_connected_when_url_configured(self, caplog):
        """InfluxDB writer and reader must be created when INFLUXDB_URL is set."""
        import logging
        from backend.influx_reader import InfluxMetricsReader

        extra = {"INFLUXDB_URL": "http://influx:8086"}
        with patch("backend.main.HuaweiDriver", return_value=_make_mock_huawei()):
            with patch("backend.main.VictronDriver", return_value=_make_mock_victron()):
                with patch("backend.main.EvccMqttDriver", return_value=_make_mock_evcc_driver()):
                    with patch("backend.main.HomeAssistantMqttClient", return_value=_make_mock_ha_client()):
                        with patch.dict("os.environ", {**_REQUIRED_ENV, **extra}):
                            with caplog.at_level(logging.INFO, logger="backend.main"):
                                with TestClient(app) as client:
                                    # Reader now wired — v1 InfluxQL migration complete
                                    assert isinstance(app.state.metrics_reader, InfluxMetricsReader)
        assert any("InfluxDB writer connected" in r.message for r in caplog.records)

    def test_influx_disabled_log_message(self, caplog):
        """Startup must log that InfluxDB is disabled when not configured."""
        import logging
        env_no_influx = {
            k: v for k, v in os.environ.items()
            if k not in ("INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_DATABASE",
                          "INFLUXDB_BUCKET", "INFLUXDB_USERNAME", "INFLUXDB_PASSWORD")
        }
        env_no_influx.update(_REQUIRED_ENV)
        with patch("backend.main.HuaweiDriver", return_value=_make_mock_huawei()):
            with patch("backend.main.VictronDriver", return_value=_make_mock_victron()):
                with patch("backend.main.EvccMqttDriver", return_value=_make_mock_evcc_driver()):
                    with patch("backend.main.HomeAssistantMqttClient", return_value=_make_mock_ha_client()):
                        with patch.dict("os.environ", env_no_influx, clear=True):
                            with caplog.at_level(logging.INFO, logger="backend.main"):
                                with TestClient(app):
                                    pass
        assert any("InfluxDB disabled" in r.message for r in caplog.records)
