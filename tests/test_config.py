"""Tests for backend/config.py — _require_env and Config.from_env() methods."""
from __future__ import annotations

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# _require_env
# ---------------------------------------------------------------------------

class TestRequireEnv:
    def test_returns_value_when_set(self):
        from backend.config import _require_env
        with patch.dict("os.environ", {"SOME_KEY": "hello"}, clear=False):
            assert _require_env("SOME_KEY") == "hello"

    def test_raises_key_error_when_missing(self):
        from backend.config import _require_env
        with patch.dict("os.environ", {}, clear=False):
            # ensure key is absent
            import os
            os.environ.pop("MISSING_KEY_XYZ", None)
            with pytest.raises(KeyError):
                _require_env("MISSING_KEY_XYZ")

    def test_raises_key_error_on_empty_string(self):
        from backend.config import _require_env
        with patch.dict("os.environ", {"EMPTY_KEY": ""}, clear=False):
            with pytest.raises(KeyError):
                _require_env("EMPTY_KEY")


# ---------------------------------------------------------------------------
# HuaweiConfig
# ---------------------------------------------------------------------------

class TestHuaweiConfig:
    def test_raises_when_host_not_set(self):
        from backend.config import HuaweiConfig
        env = {"HUAWEI_HOST": ""}
        with patch.dict("os.environ", env, clear=False):
            import os
            os.environ.pop("HUAWEI_HOST", None)
            with pytest.raises(KeyError):
                HuaweiConfig.from_env()

    def test_defaults_when_only_host_set(self):
        from backend.config import HuaweiConfig
        env = {"HUAWEI_HOST": "192.168.1.100"}
        with patch.dict("os.environ", env, clear=False):
            cfg = HuaweiConfig.from_env()
        assert cfg.host == "192.168.1.100"
        assert cfg.port == 502
        assert cfg.master_slave_id == 0
        assert cfg.slave_slave_id == 2

    def test_overrides_from_env(self):
        from backend.config import HuaweiConfig
        env = {
            "HUAWEI_HOST": "10.0.0.1",
            "HUAWEI_PORT": "8502",
            "HUAWEI_MASTER_SLAVE_ID": "2",
            "HUAWEI_SLAVE_SLAVE_ID": "8",
        }
        with patch.dict("os.environ", env, clear=False):
            cfg = HuaweiConfig.from_env()
        assert cfg.port == 8502
        assert cfg.master_slave_id == 2
        assert cfg.slave_slave_id == 8


# ---------------------------------------------------------------------------
# VictronConfig
# ---------------------------------------------------------------------------

class TestVictronConfig:
    def test_raises_when_host_not_set(self):
        from backend.config import VictronConfig
        import os
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("VICTRON_HOST", None)
            with pytest.raises(KeyError):
                VictronConfig.from_env()

    def test_defaults_when_only_host_set(self):
        from backend.config import VictronConfig
        with patch.dict("os.environ", {"VICTRON_HOST": "192.168.1.150"}, clear=False):
            cfg = VictronConfig.from_env()
        assert cfg.host == "192.168.1.150"
        assert cfg.port == 502
        assert cfg.vebus_unit_id == 227
        assert cfg.system_unit_id == 100
        assert cfg.battery_unit_id == 225


# ---------------------------------------------------------------------------
# InfluxConfig
# ---------------------------------------------------------------------------

class TestInfluxConfig:
    def test_disabled_when_url_absent(self):
        from backend.config import InfluxConfig
        import os
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("INFLUXDB_URL", None)
            cfg = InfluxConfig.from_env()
        assert cfg.enabled is False

    def test_enabled_when_url_present(self):
        from backend.config import InfluxConfig
        env = {"INFLUXDB_URL": "http://influx:8086"}
        with patch.dict("os.environ", env, clear=False):
            cfg = InfluxConfig.from_env()
        assert cfg.enabled is True
        assert cfg.url == "http://influx:8086"

    def test_database_fallback_to_bucket(self):
        from backend.config import InfluxConfig
        import os
        env = {"INFLUXDB_URL": "http://influx:8086", "INFLUXDB_BUCKET": "mybucket"}
        with patch.dict("os.environ", env, clear=False):
            os.environ.pop("INFLUXDB_DATABASE", None)
            cfg = InfluxConfig.from_env()
        assert cfg.database == "mybucket"

    def test_password_fallback_to_token(self):
        from backend.config import InfluxConfig
        import os
        env = {"INFLUXDB_URL": "http://influx:8086", "INFLUXDB_TOKEN": "secret-token"}
        with patch.dict("os.environ", env, clear=False):
            os.environ.pop("INFLUXDB_PASSWORD", None)
            cfg = InfluxConfig.from_env()
        assert cfg.password == "secret-token"


# ---------------------------------------------------------------------------
# SystemConfig defaults
# ---------------------------------------------------------------------------

class TestSystemConfig:
    def test_default_field_values(self):
        from backend.config import SystemConfig
        cfg = SystemConfig()
        assert cfg.huawei_min_soc_pct == 10.0
        assert cfg.victron_min_soc_pct == 15.0
        assert cfg.huawei_max_soc_pct == 95.0
        assert cfg.victron_max_soc_pct == 95.0
        assert cfg.huawei_min_soc_profile is None
        assert cfg.victron_min_soc_profile is None
