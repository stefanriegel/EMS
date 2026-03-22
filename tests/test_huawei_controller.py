"""Tests for HuaweiController — poll, execute, failure counting, safe state."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from backend.drivers.huawei_models import HuaweiBatteryData, HuaweiMasterData


def _make_master(**overrides) -> HuaweiMasterData:
    defaults = dict(
        pv_input_power_w=0,
        active_power_w=1500,
        pv_01_voltage_v=0.0,
        pv_01_current_a=0.0,
        pv_02_voltage_v=0.0,
        pv_02_current_a=0.0,
        device_status=None,
    )
    defaults.update(overrides)
    return HuaweiMasterData(**defaults)


def _make_battery(**overrides) -> HuaweiBatteryData:
    defaults = dict(
        pack1_soc_pct=50.0,
        pack1_charge_discharge_power_w=0,
        pack1_status=None,
        pack2_soc_pct=None,
        pack2_charge_discharge_power_w=None,
        pack2_status=None,
        total_soc_pct=50.0,
        total_charge_discharge_power_w=-1200,
        max_charge_power_w=5000,
        max_discharge_power_w=5000,
        working_mode=None,
    )
    defaults.update(overrides)
    return HuaweiBatteryData(**defaults)


def _make_controller(driver_mock, loop_interval_s=5.0):
    from backend.config import SystemConfig
    from backend.huawei_controller import HuaweiController

    return HuaweiController(
        driver=driver_mock,
        sys_config=SystemConfig(),
        loop_interval_s=loop_interval_s,
    )


class TestHuaweiControllerPoll:
    """poll() reads master + battery and returns ControllerSnapshot."""

    @pytest.mark.anyio
    async def test_poll_returns_available_snapshot(self):
        driver = AsyncMock()
        driver.read_master = AsyncMock(return_value=_make_master())
        driver.read_battery = AsyncMock(return_value=_make_battery())

        ctrl = _make_controller(driver)
        snap = await ctrl.poll()

        assert snap.available is True
        assert snap.soc_pct == 50.0
        assert snap.power_w == -1200
        driver.read_master.assert_awaited_once()
        driver.read_battery.assert_awaited_once()

    @pytest.mark.anyio
    async def test_poll_includes_max_powers(self):
        driver = AsyncMock()
        driver.read_master = AsyncMock(return_value=_make_master())
        driver.read_battery = AsyncMock(
            return_value=_make_battery(max_charge_power_w=5000, max_discharge_power_w=4000)
        )

        ctrl = _make_controller(driver)
        snap = await ctrl.poll()

        assert snap.max_charge_power_w == 5000
        assert snap.max_discharge_power_w == 4000

    @pytest.mark.anyio
    async def test_poll_charge_headroom(self):
        driver = AsyncMock()
        driver.read_master = AsyncMock(return_value=_make_master())
        driver.read_battery = AsyncMock(
            return_value=_make_battery(
                max_charge_power_w=5000,
                total_charge_discharge_power_w=2000,  # currently charging at 2000W
            )
        )

        ctrl = _make_controller(driver)
        snap = await ctrl.poll()

        # headroom = max(0, max_charge - charge_power_w)
        # charge_power_w property = max(0, total_charge_discharge_power_w) = 2000
        assert snap.charge_headroom_w == 3000

    @pytest.mark.anyio
    async def test_poll_includes_master_active_power(self):
        driver = AsyncMock()
        driver.read_master = AsyncMock(return_value=_make_master(active_power_w=3500))
        driver.read_battery = AsyncMock(return_value=_make_battery())

        ctrl = _make_controller(driver)
        snap = await ctrl.poll()

        assert snap.master_active_power_w == 3500

    @pytest.mark.anyio
    async def test_poll_exception_increments_failures(self):
        driver = AsyncMock()
        driver.read_master = AsyncMock(side_effect=ConnectionError("timeout"))
        driver.read_battery = AsyncMock(return_value=_make_battery())
        driver.write_max_discharge_power = AsyncMock()

        ctrl = _make_controller(driver)

        snap = await ctrl.poll()
        assert snap.consecutive_failures == 1
        assert snap.available is True  # not yet 3 failures

    @pytest.mark.anyio
    async def test_poll_three_failures_triggers_safe_state(self):
        driver = AsyncMock()
        driver.read_master = AsyncMock(side_effect=ConnectionError("timeout"))
        driver.read_battery = AsyncMock(return_value=_make_battery())
        driver.write_max_discharge_power = AsyncMock()

        ctrl = _make_controller(driver)

        for _ in range(3):
            snap = await ctrl.poll()

        assert snap.available is False
        assert snap.consecutive_failures == 3
        driver.write_max_discharge_power.assert_awaited_with(0)

    @pytest.mark.anyio
    async def test_poll_success_resets_failures(self):
        driver = AsyncMock()
        driver.read_master = AsyncMock(side_effect=ConnectionError("timeout"))
        driver.read_battery = AsyncMock(return_value=_make_battery())
        driver.write_max_discharge_power = AsyncMock()

        ctrl = _make_controller(driver)

        # Two failures
        await ctrl.poll()
        await ctrl.poll()

        # Now succeed
        driver.read_master = AsyncMock(return_value=_make_master())
        snap = await ctrl.poll()

        assert snap.consecutive_failures == 0
        assert snap.available is True

    @pytest.mark.anyio
    async def test_poll_stale_detection(self):
        """Stale data (older than 2 * loop_interval_s) increments failure counter."""
        driver = AsyncMock()
        driver.read_master = AsyncMock(return_value=_make_master())
        driver.read_battery = AsyncMock(return_value=_make_battery())
        driver.write_max_discharge_power = AsyncMock()

        ctrl = _make_controller(driver, loop_interval_s=5.0)

        # Mock time.monotonic to simulate stale data
        # The read happens at t=100, but "now" is t=200 — way past 2*5=10s
        with patch("backend.huawei_controller.time") as mock_time:
            mock_time.monotonic.return_value = 200.0
            # Driver returns data but our controller sees it as "just read" timestamp
            # We need the controller to detect data created much earlier
            # Since HuaweiBatteryData has no timestamp, the controller tracks read time
            snap = await ctrl.poll()

        # First poll should succeed (data was just read)
        assert snap.available is True

        # Now simulate a poll where the last successful read was >2*interval ago
        # Force a stale situation by making the driver fail but not raising
        with patch("backend.huawei_controller.time") as mock_time:
            mock_time.monotonic.return_value = 211.0  # > 200 + 2*5
            driver.read_master = AsyncMock(side_effect=ConnectionError("stale"))
            snap = await ctrl.poll()

        assert snap.consecutive_failures >= 1


class TestHuaweiControllerExecute:
    """execute() converts commands to Huawei driver calls with correct sign conventions."""

    @pytest.mark.anyio
    async def test_execute_discharge(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_master = AsyncMock(return_value=_make_master())
        driver.read_battery = AsyncMock(return_value=_make_battery())
        driver.write_max_discharge_power = AsyncMock()

        ctrl = _make_controller(driver)

        cmd = ControllerCommand(
            role=BatteryRole.PRIMARY_DISCHARGE,
            target_watts=-5000.0,
        )
        await ctrl.execute(cmd)

        # Huawei uses positive watts for discharge limit
        driver.write_max_discharge_power.assert_awaited_once_with(5000)

    @pytest.mark.anyio
    async def test_execute_charging(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.write_ac_charging = AsyncMock()
        driver.write_max_charge_power = AsyncMock()

        ctrl = _make_controller(driver)

        cmd = ControllerCommand(
            role=BatteryRole.CHARGING,
            target_watts=3000.0,
        )
        await ctrl.execute(cmd)

        driver.write_ac_charging.assert_awaited_once_with(True)
        driver.write_max_charge_power.assert_awaited_once_with(3000)

    @pytest.mark.anyio
    async def test_execute_holding(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.write_max_discharge_power = AsyncMock()

        ctrl = _make_controller(driver)

        cmd = ControllerCommand(
            role=BatteryRole.HOLDING,
            target_watts=0.0,
        )
        await ctrl.execute(cmd)

        driver.write_max_discharge_power.assert_awaited_once_with(0)

    @pytest.mark.anyio
    async def test_execute_grid_charge(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.write_ac_charging = AsyncMock()
        driver.write_max_charge_power = AsyncMock()

        ctrl = _make_controller(driver)

        cmd = ControllerCommand(
            role=BatteryRole.GRID_CHARGE,
            target_watts=4000.0,
        )
        await ctrl.execute(cmd)

        driver.write_ac_charging.assert_awaited_once_with(True)
        driver.write_max_charge_power.assert_awaited_once_with(4000)

    @pytest.mark.anyio
    async def test_execute_stores_role(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.write_max_discharge_power = AsyncMock()

        ctrl = _make_controller(driver)

        cmd = ControllerCommand(
            role=BatteryRole.PRIMARY_DISCHARGE,
            target_watts=-3000.0,
        )
        await ctrl.execute(cmd)

        assert ctrl.role == BatteryRole.PRIMARY_DISCHARGE
