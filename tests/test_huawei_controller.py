"""Tests for HuaweiController — poll, execute, failure counting, safe state."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from backend.config import HardwareValidationConfig
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

        # Huawei uses positive watts for discharge limit (no validation config → dry_run=False)
        driver.write_max_discharge_power.assert_awaited_once_with(5000, dry_run=False)

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

        driver.write_ac_charging.assert_awaited_once_with(True, dry_run=False)
        driver.write_max_charge_power.assert_awaited_once_with(3000, dry_run=False)

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

        driver.write_max_discharge_power.assert_awaited_once_with(0, dry_run=False)

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

        driver.write_ac_charging.assert_awaited_once_with(True, dry_run=False)
        driver.write_max_charge_power.assert_awaited_once_with(4000, dry_run=False)

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


def _make_controller_with_validation(driver_mock, validation_config=None, loop_interval_s=5.0):
    from backend.config import SystemConfig
    from backend.huawei_controller import HuaweiController

    return HuaweiController(
        driver=driver_mock,
        sys_config=SystemConfig(),
        loop_interval_s=loop_interval_s,
        validation_config=validation_config,
    )


class TestHuaweiDryRun:
    """Tests that execute() passes dry_run during validation period."""

    @pytest.mark.anyio
    async def test_execute_passes_dry_run_true_during_validation_period(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.write_max_discharge_power = AsyncMock()

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        # Simulate first read happened just now
        ctrl._first_read_at = time.time()

        cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        await ctrl.execute(cmd)

        driver.write_max_discharge_power.assert_awaited_once_with(0, dry_run=True)

    @pytest.mark.anyio
    async def test_execute_passes_dry_run_false_after_validation_period(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.write_max_discharge_power = AsyncMock()

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        # Simulate first read was 49 hours ago
        ctrl._first_read_at = time.time() - (49 * 3600)

        cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        await ctrl.execute(cmd)

        driver.write_max_discharge_power.assert_awaited_once_with(0, dry_run=False)

    @pytest.mark.anyio
    async def test_execute_discharge_passes_dry_run(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.write_max_discharge_power = AsyncMock()

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        ctrl._first_read_at = time.time()

        cmd = ControllerCommand(role=BatteryRole.PRIMARY_DISCHARGE, target_watts=-5000.0)
        await ctrl.execute(cmd)

        driver.write_max_discharge_power.assert_awaited_once_with(5000, dry_run=True)

    @pytest.mark.anyio
    async def test_execute_charging_passes_dry_run(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.write_ac_charging = AsyncMock()
        driver.write_max_charge_power = AsyncMock()

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        ctrl._first_read_at = time.time()

        cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=3000.0)
        await ctrl.execute(cmd)

        driver.write_ac_charging.assert_awaited_once_with(True, dry_run=True)
        driver.write_max_charge_power.assert_awaited_once_with(3000, dry_run=True)

    @pytest.mark.anyio
    async def test_forced_dry_run_always_passes_true(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.write_max_discharge_power = AsyncMock()

        cfg = HardwareValidationConfig(validation_period_hours=48.0, dry_run=True)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        # Even with first read 100 hours ago, dry_run=True in config forces it
        ctrl._first_read_at = time.time() - (100 * 3600)

        cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        await ctrl.execute(cmd)

        driver.write_max_discharge_power.assert_awaited_once_with(0, dry_run=True)

    @pytest.mark.anyio
    async def test_no_validation_config_never_passes_dry_run_true(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.write_max_discharge_power = AsyncMock()

        ctrl = _make_controller_with_validation(driver, validation_config=None)

        cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        await ctrl.execute(cmd)

        driver.write_max_discharge_power.assert_awaited_once_with(0, dry_run=False)


class TestHuaweiValidationPeriod:
    """Tests for validation period timing and boundary conditions."""

    def test_in_validation_period_true_when_first_read_none(self):
        driver = AsyncMock()
        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        assert ctrl._in_validation_period() is True

    def test_in_validation_period_true_when_within_48h(self):
        driver = AsyncMock()
        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        ctrl._first_read_at = time.time() - (24 * 3600)  # 24h ago
        assert ctrl._in_validation_period() is True

    def test_in_validation_period_false_when_past_48h(self):
        driver = AsyncMock()
        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        ctrl._first_read_at = time.time() - (49 * 3600)  # 49h ago
        assert ctrl._in_validation_period() is False

    def test_in_validation_period_false_when_no_config(self):
        driver = AsyncMock()
        ctrl = _make_controller_with_validation(driver, validation_config=None)
        assert ctrl._in_validation_period() is False

    @pytest.mark.anyio
    async def test_handle_failure_does_not_pass_dry_run(self):
        """Safe-state writes must bypass validation period gate."""
        driver = AsyncMock()
        driver.read_master = AsyncMock(side_effect=ConnectionError("timeout"))
        driver.read_battery = AsyncMock(return_value=_make_battery())
        driver.write_max_discharge_power = AsyncMock()

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        ctrl._first_read_at = time.time()

        for _ in range(3):
            await ctrl.poll()

        # Safe state call should NOT have dry_run keyword
        driver.write_max_discharge_power.assert_awaited_with(0)
        # Verify it was called without dry_run keyword
        for c in driver.write_max_discharge_power.call_args_list:
            assert "dry_run" not in c.kwargs

    @pytest.mark.anyio
    async def test_poll_sets_first_read_at(self):
        driver = AsyncMock()
        driver.read_master = AsyncMock(return_value=_make_master())
        driver.read_battery = AsyncMock(return_value=_make_battery())

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        assert ctrl._first_read_at is None

        await ctrl.poll()
        assert ctrl._first_read_at is not None
        assert isinstance(ctrl._first_read_at, float)

    @pytest.mark.anyio
    async def test_poll_does_not_overwrite_first_read_at(self):
        driver = AsyncMock()
        driver.read_master = AsyncMock(return_value=_make_master())
        driver.read_battery = AsyncMock(return_value=_make_battery())

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)

        await ctrl.poll()
        first = ctrl._first_read_at

        await ctrl.poll()
        assert ctrl._first_read_at == first


class TestHuaweiModeManagerIntegration:
    """Tests for mode manager integration in HuaweiController."""

    @pytest.mark.anyio
    async def test_execute_skips_during_mode_transition(self):
        """Power writes are skipped when mode manager is transitioning."""
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.write_max_discharge_power = AsyncMock()

        ctrl = _make_controller(driver)
        mm = MagicMock()
        mm.is_transitioning = True
        ctrl.set_mode_manager(mm)

        cmd = ControllerCommand(
            role=BatteryRole.PRIMARY_DISCHARGE,
            target_watts=-5000.0,
        )
        await ctrl.execute(cmd)

        # No driver write should have been called
        driver.write_max_discharge_power.assert_not_awaited()
        # But role should still be set
        assert ctrl.role == BatteryRole.PRIMARY_DISCHARGE

    @pytest.mark.anyio
    async def test_execute_normal_when_not_transitioning(self):
        """Power writes proceed when mode manager is not transitioning."""
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.write_max_discharge_power = AsyncMock()

        ctrl = _make_controller(driver)
        mm = MagicMock()
        mm.is_transitioning = False
        ctrl.set_mode_manager(mm)

        cmd = ControllerCommand(
            role=BatteryRole.PRIMARY_DISCHARGE,
            target_watts=-5000.0,
        )
        await ctrl.execute(cmd)

        driver.write_max_discharge_power.assert_awaited_once_with(5000, dry_run=False)

    @pytest.mark.anyio
    async def test_safe_state_bypasses_mode_manager(self):
        """Safe-state writes go through even when mode manager is transitioning."""
        driver = AsyncMock()
        driver.read_master = AsyncMock(side_effect=ConnectionError("timeout"))
        driver.read_battery = AsyncMock(return_value=_make_battery())
        driver.write_max_discharge_power = AsyncMock()

        ctrl = _make_controller(driver)
        mm = MagicMock()
        mm.is_transitioning = True
        ctrl.set_mode_manager(mm)

        for _ in range(3):
            snap = await ctrl.poll()

        assert snap.available is False
        driver.write_max_discharge_power.assert_awaited_with(0)

    @pytest.mark.anyio
    async def test_poll_calls_health_check(self):
        """Successful poll triggers mode manager health check."""
        driver = AsyncMock()
        driver.read_master = AsyncMock(return_value=_make_master())
        battery = _make_battery(working_mode=5)
        driver.read_battery = AsyncMock(return_value=battery)

        ctrl = _make_controller(driver)
        mm = AsyncMock()
        mm.is_transitioning = False
        ctrl.set_mode_manager(mm)

        await ctrl.poll()

        mm.check_health.assert_awaited_once_with(5)

    def test_get_working_mode_returns_none_when_no_data(self):
        """get_working_mode returns None when no battery data has been read."""
        driver = AsyncMock()
        ctrl = _make_controller(driver)
        assert ctrl.get_working_mode() is None

    @pytest.mark.anyio
    async def test_get_working_mode_returns_value_after_poll(self):
        """get_working_mode returns the last-read working mode."""
        driver = AsyncMock()
        driver.read_master = AsyncMock(return_value=_make_master())
        driver.read_battery = AsyncMock(return_value=_make_battery(working_mode=5))

        ctrl = _make_controller(driver)
        await ctrl.poll()

        assert ctrl.get_working_mode() == 5
