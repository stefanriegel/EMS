"""Unit tests for backend/setup_config.py — persistence layer.

All tests are synchronous (no async, no anyio marks required).
"""
from __future__ import annotations

import json
import os

import pytest

from backend.setup_config import EmsSetupConfig, load_setup_config, save_setup_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _full_config(**overrides) -> EmsSetupConfig:
    """Return a fully-populated EmsSetupConfig suitable for round-trip tests."""
    defaults = dict(
        huawei_host="192.168.1.10",
        huawei_port=502,
        victron_host="192.168.1.20",
        victron_port=1883,
        evcc_host="192.168.1.30",
        evcc_port=7070,
        evcc_mqtt_host="192.168.1.30",
        evcc_mqtt_port=1883,
        ha_url="http://homeassistant.local:8123",
        ha_token="tok123",
        ha_heat_pump_entity_id="sensor.heat_pump",
        octopus_off_peak_start_min=30,
        octopus_off_peak_end_min=330,
        octopus_off_peak_rate_eur_kwh=0.08,
        octopus_peak_rate_eur_kwh=0.28,
        huawei_min_soc_pct=10.0,
        huawei_max_soc_pct=95.0,
        victron_min_soc_pct=15.0,
        victron_max_soc_pct=95.0,
    )
    defaults.update(overrides)
    return EmsSetupConfig(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_round_trip(tmp_path):
    """save_setup_config + load_setup_config produces identical values."""
    path = str(tmp_path / "ems.json")
    cfg = _full_config()
    save_setup_config(cfg, path)
    loaded = load_setup_config(path)
    assert loaded is not None
    assert loaded.huawei_host == "192.168.1.10"
    assert loaded.huawei_port == 502
    assert loaded.victron_host == "192.168.1.20"
    assert loaded.victron_port == 1883
    assert loaded.evcc_host == "192.168.1.30"
    assert loaded.evcc_port == 7070
    assert loaded.evcc_mqtt_host == "192.168.1.30"
    assert loaded.evcc_mqtt_port == 1883
    assert loaded.ha_url == "http://homeassistant.local:8123"
    assert loaded.ha_token == "tok123"
    assert loaded.ha_heat_pump_entity_id == "sensor.heat_pump"
    assert loaded.octopus_off_peak_start_min == 30
    assert loaded.octopus_off_peak_end_min == 330
    assert loaded.octopus_off_peak_rate_eur_kwh == pytest.approx(0.08)
    assert loaded.octopus_peak_rate_eur_kwh == pytest.approx(0.28)
    assert loaded.huawei_min_soc_pct == pytest.approx(10.0)
    assert loaded.huawei_max_soc_pct == pytest.approx(95.0)
    assert loaded.victron_min_soc_pct == pytest.approx(15.0)
    assert loaded.victron_max_soc_pct == pytest.approx(95.0)


def test_load_returns_none_when_absent(tmp_path):
    """load_setup_config returns None when the file does not exist."""
    result = load_setup_config(str(tmp_path / "absent.json"))
    assert result is None


def test_load_returns_none_on_malformed_json(tmp_path):
    """load_setup_config returns None when the file contains invalid JSON."""
    bad_path = str(tmp_path / "bad.json")
    with open(bad_path, "w") as fh:
        fh.write("not-json{{")
    result = load_setup_config(bad_path)
    assert result is None


def test_load_returns_none_on_unexpected_fields(tmp_path):
    """load_setup_config returns None when JSON contains unrecognised fields."""
    bad_path = str(tmp_path / "unknown.json")
    with open(bad_path, "w") as fh:
        json.dump({"unknown_field": "x"}, fh)
    result = load_setup_config(bad_path)
    assert result is None


def test_setup_complete_flag():
    """setup_complete logic: cfg must exist with non-empty huawei_host and victron_host."""
    # Empty hosts → not complete
    cfg_empty = EmsSetupConfig()
    assert cfg_empty.huawei_host == ""
    assert cfg_empty.victron_host == ""
    setup_complete = bool(cfg_empty.huawei_host) and bool(cfg_empty.victron_host)
    assert setup_complete is False

    # Both hosts populated → complete
    cfg_full = _full_config()
    setup_complete_full = bool(cfg_full.huawei_host) and bool(cfg_full.victron_host)
    assert setup_complete_full is True

    # Only one host populated → not complete
    cfg_partial = _full_config(victron_host="")
    setup_complete_partial = bool(cfg_partial.huawei_host) and bool(cfg_partial.victron_host)
    assert setup_complete_partial is False


def test_atomic_write_no_tmp_file(tmp_path):
    """save_setup_config leaves no <path>.tmp file after a successful write."""
    path = str(tmp_path / "ems.json")
    tmp_path_str = path + ".tmp"
    save_setup_config(_full_config(), path)
    assert os.path.exists(path), "config file should exist"
    assert not os.path.exists(tmp_path_str), ".tmp file should be cleaned up by os.replace()"


def test_default_fields():
    """EmsSetupConfig() with no args produces expected defaults."""
    cfg = EmsSetupConfig()
    assert cfg.huawei_host == ""
    assert cfg.victron_host == ""
    assert cfg.victron_min_soc_pct == pytest.approx(15.0)
    assert cfg.huawei_port == 502
    assert cfg.victron_port == 1883
