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


# ---------------------------------------------------------------------------
# HuaweiDriver — mock-based integration tests
# ---------------------------------------------------------------------------
# These tests use pytest-mock to patch AsyncHuaweiSolar at the point where
# the driver imports it.  No live hardware or network required.
# ---------------------------------------------------------------------------

import pytest
import pytest_anyio  # noqa: F401 — side effect: registers the anyio backend
from unittest.mock import AsyncMock, MagicMock, call, patch

from huawei_solar.huawei_solar import Result
from huawei_solar.register_values import StorageWorkingModesC

from backend.drivers.huawei_driver import HuaweiDriver


# Helper: build a Result namedtuple (the type returned by get_multiple)
def _result(value, unit=""):
    return Result(value=value, unit=unit)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_client():
    """Return an AsyncMock that mimics AsyncHuaweiSolar's async interface."""
    client = AsyncMock()
    client.stop = AsyncMock()
    client.get_multiple = AsyncMock()
    client.set = AsyncMock(return_value=True)
    return client


@pytest.fixture
def driver():
    """Return an unconnected HuaweiDriver pointed at a dummy host."""
    return HuaweiDriver(host="127.0.0.1", port=502, master_slave_id=0, slave_slave_id=2)


# ---------------------------------------------------------------------------
# Test: read_master returns correct dataclass
# ---------------------------------------------------------------------------

class TestReadMaster:
    @pytest.mark.anyio
    async def test_read_master_returns_correct_dataclass(self, driver, mock_client):
        """get_multiple result list is mapped correctly to HuaweiMasterData fields."""
        # _MASTER_REGISTERS order: state_1, pv_01_voltage, pv_01_current,
        #   pv_02_voltage, pv_02_current, input_power, active_power
        mock_client.get_multiple.return_value = [
            _result(0x0002),    # state_1
            _result(380.5),     # pv_01_voltage
            _result(5.2),       # pv_01_current
            _result(375.0),     # pv_02_voltage
            _result(5.0),       # pv_02_current
            _result(4200),      # input_power
            _result(3800),      # active_power
        ]
        driver._client = mock_client

        data = await driver.read_master()

        assert data.device_status == 0x0002
        assert data.pv_01_voltage_v == pytest.approx(380.5)
        assert data.pv_01_current_a == pytest.approx(5.2)
        assert data.pv_02_voltage_v == pytest.approx(375.0)
        assert data.pv_02_current_a == pytest.approx(5.0)
        assert data.pv_input_power_w == 4200
        assert data.active_power_w == 3800

    @pytest.mark.anyio
    async def test_read_master_calls_get_multiple_with_correct_slave_id(self, driver, mock_client):
        """get_multiple is called with master_slave_id=0."""
        mock_client.get_multiple.return_value = [
            _result(0), _result(0.0), _result(0.0), _result(0.0),
            _result(0.0), _result(0), _result(0),
        ]
        driver._client = mock_client

        await driver.read_master()

        _, kwargs = mock_client.get_multiple.call_args
        assert kwargs.get("slave_id") == 0


# ---------------------------------------------------------------------------
# Test: read_battery makes exactly two get_multiple calls
# ---------------------------------------------------------------------------

class TestReadBattery:
    def _pack1_results(self):
        """Return values for _BATTERY_PACK1_REGISTERS (6 registers)."""
        return [
            _result(1),      # storage_unit_1_running_status
            _result(3000),   # storage_unit_1_charge_discharge_power (charging)
            _result(72.5),   # storage_unit_1_state_of_capacity
            _result(2),      # storage_unit_1_working_mode_b
            _result(5000),   # storage_maximum_charge_power
            _result(5000),   # storage_maximum_discharge_power
        ]

    def _pack2_results(self):
        """Return values for _BATTERY_PACK2_REGISTERS (5 registers)."""
        return [
            _result(70.0),   # storage_unit_2_state_of_capacity
            _result(1),      # storage_unit_2_running_status
            _result(2900),   # storage_unit_2_charge_discharge_power
            _result(71.0),   # storage_state_of_capacity
            _result(5900),   # storage_charge_discharge_power
        ]

    @pytest.mark.anyio
    async def test_read_battery_makes_two_get_multiple_calls(self, driver, mock_client):
        """read_battery() must call get_multiple exactly twice."""
        mock_client.get_multiple.side_effect = [
            self._pack1_results(),
            self._pack2_results(),
        ]
        driver._client = mock_client

        await driver.read_battery()

        assert mock_client.get_multiple.call_count == 2

    @pytest.mark.anyio
    async def test_read_battery_pack1_registers_come_first(self, driver, mock_client):
        """First get_multiple call must use pack-1 register list."""
        from backend.drivers.huawei_driver import _BATTERY_PACK1_REGISTERS, _BATTERY_PACK2_REGISTERS
        mock_client.get_multiple.side_effect = [
            self._pack1_results(),
            self._pack2_results(),
        ]
        driver._client = mock_client

        await driver.read_battery()

        first_call_args, first_call_kwargs = mock_client.get_multiple.call_args_list[0]
        second_call_args, second_call_kwargs = mock_client.get_multiple.call_args_list[1]
        assert first_call_args[0] == _BATTERY_PACK1_REGISTERS
        assert second_call_args[0] == _BATTERY_PACK2_REGISTERS

    @pytest.mark.anyio
    async def test_read_battery_pack2_absent_returns_none_fields(self, driver, mock_client):
        """If pack-2 get_multiple raises, pack-2 fields are None; no exception is raised."""
        mock_client.get_multiple.side_effect = [
            self._pack1_results(),
            Exception("IllegalAddress: register 37738 not available"),
        ]
        driver._client = mock_client

        data = await driver.read_battery()

        # Pack-2 specific fields must be None
        assert data.pack2_soc_pct is None
        assert data.pack2_charge_discharge_power_w is None
        assert data.pack2_status is None
        # Combined fields fall back to pack-1 values
        assert data.total_soc_pct == pytest.approx(72.5)
        assert data.total_charge_discharge_power_w == 3000

    @pytest.mark.anyio
    async def test_read_battery_populates_all_fields_when_pack2_present(self, driver, mock_client):
        """Full two-pack read populates all HuaweiBatteryData fields."""
        mock_client.get_multiple.side_effect = [
            self._pack1_results(),
            self._pack2_results(),
        ]
        driver._client = mock_client

        data = await driver.read_battery()

        assert data.pack1_soc_pct == pytest.approx(72.5)
        assert data.pack1_charge_discharge_power_w == 3000
        assert data.pack2_soc_pct == pytest.approx(70.0)
        assert data.pack2_charge_discharge_power_w == 2900
        assert data.total_soc_pct == pytest.approx(71.0)
        assert data.total_charge_discharge_power_w == 5900


# ---------------------------------------------------------------------------
# Test: write_battery_mode calls set with correct register and enum
# ---------------------------------------------------------------------------

class TestWriteMethods:
    @pytest.mark.anyio
    async def test_write_battery_mode_calls_set_with_correct_register(self, driver, mock_client):
        """write_battery_mode must call set('storage_working_mode_settings', enum_value)."""
        driver._client = mock_client

        await driver.write_battery_mode(StorageWorkingModesC.MAXIMISE_SELF_CONSUMPTION)

        mock_client.set.assert_called_once_with(
            "storage_working_mode_settings",
            StorageWorkingModesC.MAXIMISE_SELF_CONSUMPTION,
            slave_id=driver.master_slave_id,
        )

    @pytest.mark.anyio
    async def test_write_ac_charging_calls_set_with_bool(self, driver, mock_client):
        """write_ac_charging(True) must call set('storage_charge_from_grid_function', True)."""
        driver._client = mock_client

        await driver.write_ac_charging(True)

        mock_client.set.assert_called_once_with(
            "storage_charge_from_grid_function",
            True,
            slave_id=driver.master_slave_id,
        )

    @pytest.mark.anyio
    async def test_write_ac_charging_false(self, driver, mock_client):
        """write_ac_charging(False) must call set with False."""
        driver._client = mock_client

        await driver.write_ac_charging(False)

        mock_client.set.assert_called_once_with(
            "storage_charge_from_grid_function",
            False,
            slave_id=driver.master_slave_id,
        )

    @pytest.mark.anyio
    async def test_write_max_charge_power(self, driver, mock_client):
        """write_max_charge_power calls set with 'storage_maximum_charging_power' and watts."""
        driver._client = mock_client

        await driver.write_max_charge_power(4500)

        mock_client.set.assert_called_once_with(
            "storage_maximum_charging_power",
            4500,
            slave_id=driver.master_slave_id,
        )

    @pytest.mark.anyio
    async def test_write_max_discharge_power(self, driver, mock_client):
        """write_max_discharge_power calls set with 'storage_maximum_discharging_power' and watts."""
        driver._client = mock_client

        await driver.write_max_discharge_power(3000)

        mock_client.set.assert_called_once_with(
            "storage_maximum_discharging_power",
            3000,
            slave_id=driver.master_slave_id,
        )


# ---------------------------------------------------------------------------
# Test: async context manager calls connect and close
# ---------------------------------------------------------------------------

class TestContextManager:
    @pytest.mark.anyio
    async def test_context_manager_calls_connect_and_close(self, mock_client):
        """__aenter__ must connect; __aexit__ must close."""
        driver = HuaweiDriver(host="127.0.0.1")

        with patch(
            "backend.drivers.huawei_driver.AsyncHuaweiSolar.create",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            async with driver as d:
                assert d._client is mock_client

        # After exit, client should be stopped and cleared
        mock_client.stop.assert_called_once()
        assert driver._client is None


# ---------------------------------------------------------------------------
# Test: reconnect path on ConnectionException
# ---------------------------------------------------------------------------

class TestReconnect:
    @pytest.mark.anyio
    async def test_reconnect_on_connection_exception(self, driver, mock_client):
        """On ConnectionException, driver reconnects and retries — result succeeds."""
        from huawei_solar import ConnectionException

        # First get_multiple raises; after reconnect the second succeeds
        good_results = [
            _result(0x0002), _result(380.5), _result(5.2),
            _result(375.0), _result(5.0), _result(4200), _result(3800),
        ]
        mock_client.get_multiple.side_effect = [
            ConnectionException("Connection lost"),
            good_results,
        ]

        fresh_client = AsyncMock()
        fresh_client.stop = AsyncMock()
        fresh_client.get_multiple = AsyncMock(return_value=good_results)

        driver._client = mock_client

        with patch(
            "backend.drivers.huawei_driver.AsyncHuaweiSolar.create",
            new_callable=AsyncMock,
            return_value=fresh_client,
        ):
            data = await driver.read_master()

        assert data.pv_input_power_w == 4200
        mock_client.stop.assert_called_once()  # close() was called on the old client
