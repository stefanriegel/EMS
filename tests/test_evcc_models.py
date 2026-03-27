"""Tests for backend/evcc_models.py — EvccLoadpointState, EvccMqttConfig."""
from __future__ import annotations

import pytest
from unittest.mock import patch


class TestEvccLoadpointState:
    def test_all_defaults(self):
        from backend.evcc_models import EvccLoadpointState
        s = EvccLoadpointState()
        assert s.mode == "off"
        assert s.charge_power_w == 0.0
        assert s.vehicle_soc_pct is None
        assert s.charging is False
        assert s.connected is False

    def test_field_overrides(self):
        from backend.evcc_models import EvccLoadpointState
        s = EvccLoadpointState(
            mode="now",
            charge_power_w=7400.0,
            vehicle_soc_pct=55.0,
            charging=True,
            connected=True,
        )
        assert s.mode == "now"
        assert s.charge_power_w == 7400.0
        assert s.vehicle_soc_pct == 55.0
        assert s.charging is True
        assert s.connected is True

    def test_partial_override_leaves_defaults(self):
        from backend.evcc_models import EvccLoadpointState
        s = EvccLoadpointState(charging=True)
        assert s.mode == "off"
        assert s.charging is True
        assert s.connected is False


class TestEvccMqttConfig:
    def test_defaults_when_env_vars_absent(self):
        from backend.evcc_models import EvccMqttConfig
        import os
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("EVCC_MQTT_HOST", None)
            os.environ.pop("EVCC_MQTT_PORT", None)
            cfg = EvccMqttConfig.from_env()
        assert cfg.host == "192.168.0.10"
        assert cfg.port == 1883

    def test_uses_evcc_mqtt_host_when_set(self):
        from backend.evcc_models import EvccMqttConfig
        with patch.dict("os.environ", {"EVCC_MQTT_HOST": "10.0.0.5"}, clear=False):
            cfg = EvccMqttConfig.from_env()
        assert cfg.host == "10.0.0.5"

    def test_uses_evcc_mqtt_port_when_set(self):
        from backend.evcc_models import EvccMqttConfig
        with patch.dict("os.environ", {"EVCC_MQTT_PORT": "1884"}, clear=False):
            cfg = EvccMqttConfig.from_env()
        assert cfg.port == 1884

    def test_both_overrides(self):
        from backend.evcc_models import EvccMqttConfig
        env = {"EVCC_MQTT_HOST": "mqtt.local", "EVCC_MQTT_PORT": "8883"}
        with patch.dict("os.environ", env, clear=False):
            cfg = EvccMqttConfig.from_env()
        assert cfg.host == "mqtt.local"
        assert cfg.port == 8883
