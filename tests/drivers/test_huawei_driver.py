"""Unit tests for Huawei data models and configuration.

No live hardware required.  No imports from ``huawei_solar`` — this module
only tests the dataclass contracts defined in ``backend.drivers.huawei_models``
and the env-reading logic in ``backend.config``.
"""
from __future__ import annotations

import pytest

from backend.config import HuaweiConfig
from backend.drivers.huawei_models import (
    HuaweiBatteryData,
    HuaweiMasterData,
    HuaweiSlaveData,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_battery(**overrides) -> HuaweiBatteryData:
    """Return a fully-populated HuaweiBatteryData, with optional field overrides."""
    defaults: dict = {
        "pack1_soc_pct": 60.0,
        "pack1_charge_discharge_power_w": 0,
        "pack1_status": 1,
        "pack2_soc_pct": 58.0,
        "pack2_charge_discharge_power_w": 0,
        "pack2_status": 1,
        "total_soc_pct": 59.0,
        "total_charge_discharge_power_w": 0,
        "max_charge_power_w": 5000,
        "max_discharge_power_w": 5000,
        "working_mode": 2,
    }
    defaults.update(overrides)
    return HuaweiBatteryData(**defaults)


# ---------------------------------------------------------------------------
# HuaweiMasterData construction
# ---------------------------------------------------------------------------

class TestHuaweiMasterData:
    def test_master_data_construction(self):
        """All fields must be accessible after construction with typical values."""
        data = HuaweiMasterData(
            pv_input_power_w=4200,
            active_power_w=3800,
            pv_01_voltage_v=380.5,
            pv_01_current_a=5.2,
            pv_02_voltage_v=375.0,
            pv_02_current_a=5.0,
            device_status=0x0002,
        )
        assert data.pv_input_power_w == 4200
        assert data.active_power_w == 3800
        assert data.pv_01_voltage_v == pytest.approx(380.5)
        assert data.pv_01_current_a == pytest.approx(5.2)
        assert data.pv_02_voltage_v == pytest.approx(375.0)
        assert data.pv_02_current_a == pytest.approx(5.0)
        assert data.device_status == 0x0002

    def test_master_data_device_status_none(self):
        """device_status may be None (e.g. during inverter startup)."""
        data = HuaweiMasterData(
            pv_input_power_w=0,
            active_power_w=0,
            pv_01_voltage_v=0.0,
            pv_01_current_a=0.0,
            pv_02_voltage_v=0.0,
            pv_02_current_a=0.0,
            device_status=None,
        )
        assert data.device_status is None


# ---------------------------------------------------------------------------
# HuaweiSlaveData construction
# ---------------------------------------------------------------------------

class TestHuaweiSlaveData:
    def test_slave_data_construction(self):
        """Slave data mirrors master fields; construction must succeed."""
        data = HuaweiSlaveData(
            pv_input_power_w=2100,
            active_power_w=1900,
            pv_01_voltage_v=381.0,
            pv_01_current_a=2.7,
            pv_02_voltage_v=0.0,
            pv_02_current_a=0.0,
            device_status=0x0002,
        )
        assert data.pv_input_power_w == 2100
        assert data.active_power_w == 1900


# ---------------------------------------------------------------------------
# HuaweiBatteryData — sign-convention properties
# ---------------------------------------------------------------------------

class TestHuaweiBatteryDataSignConvention:
    def test_charging(self):
        """Positive total_charge_discharge_power_w → charge_power_w, discharge_power_w==0."""
        bat = _make_battery(total_charge_discharge_power_w=5000)
        assert bat.charge_power_w == 5000
        assert bat.discharge_power_w == 0

    def test_discharging(self):
        """Negative total_charge_discharge_power_w → discharge_power_w, charge_power_w==0."""
        bat = _make_battery(total_charge_discharge_power_w=-3000)
        assert bat.charge_power_w == 0
        assert bat.discharge_power_w == 3000

    def test_zero(self):
        """Zero power → both charge_power_w and discharge_power_w are 0."""
        bat = _make_battery(total_charge_discharge_power_w=0)
        assert bat.charge_power_w == 0
        assert bat.discharge_power_w == 0

    def test_pack1_charging(self):
        """Pack-1 positive power → pack1_charge_power_w, pack1_discharge_power_w==0."""
        bat = _make_battery(pack1_charge_discharge_power_w=2500)
        assert bat.pack1_charge_power_w == 2500
        assert bat.pack1_discharge_power_w == 0

    def test_pack1_discharging(self):
        """Pack-1 negative power → pack1_discharge_power_w, pack1_charge_power_w==0."""
        bat = _make_battery(pack1_charge_discharge_power_w=-1500)
        assert bat.pack1_charge_power_w == 0
        assert bat.pack1_discharge_power_w == 1500


# ---------------------------------------------------------------------------
# HuaweiBatteryData — SoC boundary values
# ---------------------------------------------------------------------------

class TestHuaweiBatteryDataSoC:
    def test_soc_boundary_zero(self):
        """total_soc_pct=0.0 must be stored and retrieved exactly."""
        bat = _make_battery(total_soc_pct=0.0)
        assert bat.total_soc_pct == 0.0

    def test_soc_boundary_full(self):
        """total_soc_pct=100.0 must be stored and retrieved exactly."""
        bat = _make_battery(total_soc_pct=100.0)
        assert bat.total_soc_pct == 100.0


# ---------------------------------------------------------------------------
# HuaweiBatteryData — optional pack 2 fields
# ---------------------------------------------------------------------------

class TestHuaweiBatteryDataOptionalPack2:
    def test_pack2_all_none(self):
        """Constructing with all pack2 fields=None must succeed (single-pack systems)."""
        bat = _make_battery(
            pack2_soc_pct=None,
            pack2_charge_discharge_power_w=None,
            pack2_status=None,
        )
        assert bat.pack2_soc_pct is None
        assert bat.pack2_charge_discharge_power_w is None
        assert bat.pack2_status is None


# ---------------------------------------------------------------------------
# HuaweiConfig — environment variable reading
# ---------------------------------------------------------------------------

class TestHuaweiConfig:
    def test_from_env_host_and_default_port(self, monkeypatch):
        """HUAWEI_HOST is picked up; port defaults to 502 when HUAWEI_PORT unset."""
        monkeypatch.setenv("HUAWEI_HOST", "192.168.0.10")
        monkeypatch.delenv("HUAWEI_PORT", raising=False)
        monkeypatch.delenv("HUAWEI_MASTER_SLAVE_ID", raising=False)
        monkeypatch.delenv("HUAWEI_SLAVE_SLAVE_ID", raising=False)

        cfg = HuaweiConfig.from_env()

        assert cfg.host == "192.168.0.10"
        assert cfg.port == 502

    def test_from_env_custom_port(self, monkeypatch):
        """HUAWEI_PORT overrides the default port."""
        monkeypatch.setenv("HUAWEI_HOST", "10.0.0.1")
        monkeypatch.setenv("HUAWEI_PORT", "1502")

        cfg = HuaweiConfig.from_env()

        assert cfg.port == 1502

    def test_from_env_slave_ids(self, monkeypatch):
        """HUAWEI_MASTER_SLAVE_ID and HUAWEI_SLAVE_SLAVE_ID are read correctly."""
        monkeypatch.setenv("HUAWEI_HOST", "10.0.0.2")
        monkeypatch.setenv("HUAWEI_MASTER_SLAVE_ID", "1")
        monkeypatch.setenv("HUAWEI_SLAVE_SLAVE_ID", "3")

        cfg = HuaweiConfig.from_env()

        assert cfg.master_slave_id == 1
        assert cfg.slave_slave_id == 3

    def test_from_env_missing_host_raises(self, monkeypatch):
        """Missing HUAWEI_HOST must raise KeyError, not silently produce bad state."""
        monkeypatch.delenv("HUAWEI_HOST", raising=False)

        with pytest.raises(KeyError):
            HuaweiConfig.from_env()

    def test_default_values(self):
        """HuaweiConfig defaults are correct when constructed directly."""
        cfg = HuaweiConfig(host="localhost")
        assert cfg.port == 502
        assert cfg.master_slave_id == 0
        assert cfg.slave_slave_id == 2
        assert cfg.timeout_s == 10.0
