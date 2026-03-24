"""Tests for hardware validation primitives: dry_run, validate_connectivity, verify_write."""
from __future__ import annotations

import logging
import time
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from backend.drivers.huawei_driver import HuaweiDriver
from backend.drivers.huawei_models import (
    HuaweiBatteryData,
    HuaweiMasterData,
    HuaweiSlaveData,
)
from backend.drivers.victron_driver import VictronDriver, _signed16


# ---------------------------------------------------------------------------
# Huawei helpers
# ---------------------------------------------------------------------------

def _make_battery(**overrides) -> HuaweiBatteryData:
    defaults = dict(
        pack1_soc_pct=50.0,
        pack1_charge_discharge_power_w=0,
        pack1_status=None,
        pack2_soc_pct=None,
        pack2_charge_discharge_power_w=None,
        pack2_status=None,
        total_soc_pct=50.0,
        total_charge_discharge_power_w=0,
        max_charge_power_w=5000,
        max_discharge_power_w=5000,
        working_mode=None,
    )
    defaults.update(overrides)
    return HuaweiBatteryData(**defaults)


def _make_master(**overrides) -> HuaweiMasterData:
    defaults = dict(
        pv_input_power_w=3000,
        active_power_w=1500,
        pv_01_voltage_v=400.0,
        pv_01_current_a=5.0,
        pv_02_voltage_v=390.0,
        pv_02_current_a=4.8,
        device_status=None,
    )
    defaults.update(overrides)
    return HuaweiMasterData(**defaults)


def _make_slave(**overrides) -> HuaweiSlaveData:
    defaults = dict(
        pv_input_power_w=2000,
        active_power_w=1800,
        pv_01_voltage_v=380.0,
        pv_01_current_a=4.0,
        pv_02_voltage_v=370.0,
        pv_02_current_a=3.8,
        device_status=None,
    )
    defaults.update(overrides)
    return HuaweiSlaveData(**defaults)


def _huawei_driver_with_mock() -> HuaweiDriver:
    """Create a HuaweiDriver with a mocked _client."""
    driver = HuaweiDriver("localhost")
    driver._client = AsyncMock()
    return driver


# ---------------------------------------------------------------------------
# Victron helpers
# ---------------------------------------------------------------------------

def _victron_driver_with_mock() -> VictronDriver:
    """Create a VictronDriver with a mocked _client."""
    driver = VictronDriver("localhost")
    driver._client = AsyncMock()
    return driver


# ===================================================================
# Dry-run tests: Huawei
# ===================================================================


class TestDryRunHuawei:
    """dry_run=True logs but does NOT call the hardware client."""

    @pytest.mark.anyio
    async def test_write_max_charge_power_dry_run(self, caplog):
        driver = _huawei_driver_with_mock()
        with caplog.at_level(logging.INFO):
            await driver.write_max_charge_power(1000, dry_run=True)
        driver._client.set.assert_not_awaited()
        assert "DRY RUN" in caplog.text

    @pytest.mark.anyio
    async def test_write_max_discharge_power_dry_run(self, caplog):
        driver = _huawei_driver_with_mock()
        with caplog.at_level(logging.INFO):
            await driver.write_max_discharge_power(500, dry_run=True)
        driver._client.set.assert_not_awaited()
        assert "DRY RUN" in caplog.text

    @pytest.mark.anyio
    async def test_write_battery_mode_dry_run(self, caplog):
        driver = _huawei_driver_with_mock()
        with caplog.at_level(logging.INFO):
            await driver.write_battery_mode("MAXIMISE_SELF_CONSUMPTION", dry_run=True)
        driver._client.set.assert_not_awaited()
        assert "DRY RUN" in caplog.text

    @pytest.mark.anyio
    async def test_write_ac_charging_dry_run(self, caplog):
        driver = _huawei_driver_with_mock()
        with caplog.at_level(logging.INFO):
            await driver.write_ac_charging(True, dry_run=True)
        driver._client.set.assert_not_awaited()
        assert "DRY RUN" in caplog.text

    @pytest.mark.anyio
    async def test_write_max_charge_power_normal_still_writes(self):
        """Without dry_run, set() is called normally."""
        driver = _huawei_driver_with_mock()
        await driver.write_max_charge_power(1000)
        driver._client.set.assert_awaited_once()

    @pytest.mark.anyio
    async def test_write_max_discharge_power_normal_still_writes(self):
        driver = _huawei_driver_with_mock()
        await driver.write_max_discharge_power(500)
        driver._client.set.assert_awaited_once()


# ===================================================================
# Dry-run tests: Victron
# ===================================================================


class TestDryRunVictron:
    """dry_run=True logs but does NOT call write_register."""

    @pytest.mark.anyio
    async def test_write_ac_power_setpoint_dry_run(self, caplog):
        driver = _victron_driver_with_mock()
        with caplog.at_level(logging.INFO):
            await driver.write_ac_power_setpoint(1, -500.0, dry_run=True)
        driver._client.write_register.assert_not_awaited()
        assert "DRY RUN" in caplog.text

    @pytest.mark.anyio
    async def test_write_ac_power_setpoint_normal_still_writes(self):
        """Without dry_run, write_register is called normally."""
        driver = _victron_driver_with_mock()
        await driver.write_ac_power_setpoint(1, -500.0)
        driver._client.write_register.assert_awaited_once()


# ===================================================================
# Connectivity validation tests
# ===================================================================


class TestConnectivityValidation:
    """validate_connectivity returns True on success, False on failure."""

    @pytest.mark.anyio
    async def test_huawei_connectivity_success(self):
        driver = _huawei_driver_with_mock()
        driver.read_master = AsyncMock(return_value=_make_master())
        driver.read_battery = AsyncMock(return_value=_make_battery())
        driver.read_slave = AsyncMock(return_value=_make_slave())

        result = await driver.validate_connectivity()
        assert result is True

    @pytest.mark.anyio
    async def test_huawei_connectivity_failure_on_battery_error(self):
        driver = _huawei_driver_with_mock()
        driver.read_master = AsyncMock(return_value=_make_master())
        driver.read_battery = AsyncMock(side_effect=Exception("Modbus timeout"))
        driver.read_slave = AsyncMock(return_value=_make_slave())

        result = await driver.validate_connectivity()
        assert result is False

    @pytest.mark.anyio
    async def test_huawei_connectivity_failure_on_master_error(self):
        driver = _huawei_driver_with_mock()
        driver.read_master = AsyncMock(side_effect=Exception("Connection lost"))
        driver.read_battery = AsyncMock(return_value=_make_battery())
        driver.read_slave = AsyncMock(return_value=_make_slave())

        result = await driver.validate_connectivity()
        assert result is False

    @pytest.mark.anyio
    async def test_victron_connectivity_success(self):
        driver = _victron_driver_with_mock()
        driver.read_system_state = AsyncMock(
            return_value=MagicMock(
                battery_soc_pct=70.0,
                battery_power_w=-200.0,
                grid_power_w=100.0,
            )
        )

        result = await driver.validate_connectivity()
        assert result is True

    @pytest.mark.anyio
    async def test_victron_connectivity_failure(self):
        driver = _victron_driver_with_mock()
        driver.read_system_state = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        result = await driver.validate_connectivity()
        assert result is False


# ===================================================================
# Write-back verification tests
# ===================================================================


class TestWriteBackVerification:
    """verify_write methods write then read back to confirm the value."""

    @pytest.mark.anyio
    async def test_huawei_verify_charge_power_match(self):
        driver = _huawei_driver_with_mock()
        driver.write_max_charge_power = AsyncMock()
        driver.read_battery = AsyncMock(
            return_value=_make_battery(max_charge_power_w=1000)
        )

        result = await driver.verify_write_max_charge_power(1000)
        assert result is True
        driver.write_max_charge_power.assert_awaited_once_with(1000)

    @pytest.mark.anyio
    async def test_huawei_verify_charge_power_mismatch(self, caplog):
        driver = _huawei_driver_with_mock()
        driver.write_max_charge_power = AsyncMock()
        driver.read_battery = AsyncMock(
            return_value=_make_battery(max_charge_power_w=500)
        )

        with caplog.at_level(logging.WARNING):
            result = await driver.verify_write_max_charge_power(1000)
        assert result is False
        assert "mismatch" in caplog.text.lower()

    @pytest.mark.anyio
    async def test_huawei_verify_discharge_power_match(self):
        driver = _huawei_driver_with_mock()
        driver.write_max_discharge_power = AsyncMock()
        driver.read_battery = AsyncMock(
            return_value=_make_battery(max_discharge_power_w=2000)
        )

        result = await driver.verify_write_max_discharge_power(2000)
        assert result is True
        driver.write_max_discharge_power.assert_awaited_once_with(2000)

    @pytest.mark.anyio
    async def test_huawei_verify_discharge_power_mismatch(self, caplog):
        driver = _huawei_driver_with_mock()
        driver.write_max_discharge_power = AsyncMock()
        driver.read_battery = AsyncMock(
            return_value=_make_battery(max_discharge_power_w=3000)
        )

        with caplog.at_level(logging.WARNING):
            result = await driver.verify_write_max_discharge_power(2000)
        assert result is False
        assert "mismatch" in caplog.text.lower()

    @pytest.mark.anyio
    async def test_victron_verify_setpoint_match(self):
        driver = _victron_driver_with_mock()
        driver.write_ac_power_setpoint = AsyncMock()
        # -500 as signed16 -> 0xFE0C -> unsigned 65036
        mock_result = MagicMock()
        mock_result.registers = [(-500) & 0xFFFF]
        driver._client.read_holding_registers = AsyncMock(return_value=mock_result)

        result = await driver.verify_write_ac_power_setpoint(1, -500.0)
        assert result is True
        driver.write_ac_power_setpoint.assert_awaited_once_with(1, -500.0)

    @pytest.mark.anyio
    async def test_victron_verify_setpoint_mismatch(self, caplog):
        driver = _victron_driver_with_mock()
        driver.write_ac_power_setpoint = AsyncMock()
        # Return a different value
        mock_result = MagicMock()
        mock_result.registers = [0]  # 0W instead of -500W
        driver._client.read_holding_registers = AsyncMock(return_value=mock_result)

        with caplog.at_level(logging.WARNING):
            result = await driver.verify_write_ac_power_setpoint(1, -500.0)
        assert result is False
        assert "mismatch" in caplog.text.lower()
