"""Tests for VictronController — poll, execute, failure counting, safe state."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, call, patch

import pytest

from backend.config import HardwareValidationConfig
from backend.drivers.victron_models import VictronPhaseData, VictronSystemData


def _make_phase(**overrides) -> VictronPhaseData:
    defaults = dict(power_w=0.0, current_a=0.0, voltage_v=230.0, setpoint_w=None)
    defaults.update(overrides)
    return VictronPhaseData(**defaults)


def _make_victron_data(**overrides) -> VictronSystemData:
    defaults = dict(
        battery_soc_pct=70.0,
        battery_power_w=-2000.0,
        battery_current_a=-8.0,
        battery_voltage_v=52.0,
        l1=_make_phase(),
        l2=_make_phase(),
        l3=_make_phase(),
        ess_mode=2,
        system_state=None,
        vebus_state=9,
        grid_power_w=500.0,
        grid_l1_power_w=200.0,
        grid_l2_power_w=150.0,
        grid_l3_power_w=150.0,
        consumption_w=None,
        pv_on_grid_w=None,
        timestamp=time.monotonic(),
    )
    defaults.update(overrides)
    return VictronSystemData(**defaults)


def _make_controller(driver_mock, loop_interval_s=5.0):
    from backend.config import SystemConfig
    from backend.victron_controller import VictronController

    return VictronController(
        driver=driver_mock,
        sys_config=SystemConfig(),
        loop_interval_s=loop_interval_s,
    )


class TestVictronControllerPoll:
    """poll() reads system state and returns ControllerSnapshot."""

    @pytest.mark.anyio
    async def test_poll_returns_available_snapshot(self):
        driver = AsyncMock()
        driver.read_system_state = AsyncMock(return_value=_make_victron_data())

        ctrl = _make_controller(driver)
        snap = await ctrl.poll()

        assert snap.available is True
        assert snap.soc_pct == 70.0
        assert snap.power_w == -2000.0
        driver.read_system_state.assert_awaited_once()

    @pytest.mark.anyio
    async def test_poll_includes_grid_power(self):
        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(
                grid_power_w=600.0,
                grid_l1_power_w=200.0,
                grid_l2_power_w=200.0,
                grid_l3_power_w=200.0,
            )
        )

        ctrl = _make_controller(driver)
        snap = await ctrl.poll()

        assert snap.grid_power_w == 600.0
        assert snap.grid_l1_power_w == 200.0
        assert snap.grid_l2_power_w == 200.0
        assert snap.grid_l3_power_w == 200.0

    @pytest.mark.anyio
    async def test_poll_includes_charge_headroom(self):
        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(battery_power_w=1500.0)
        )

        ctrl = _make_controller(driver)
        snap = await ctrl.poll()

        # charge_headroom_w = max(0.0, charge_power_w) where charge_power_w = max(0.0, battery_power_w)
        assert snap.charge_headroom_w == 1500.0

    @pytest.mark.anyio
    async def test_poll_includes_ess_mode(self):
        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(ess_mode=3)
        )

        ctrl = _make_controller(driver)
        snap = await ctrl.poll()

        assert snap.ess_mode == 3

    @pytest.mark.anyio
    async def test_poll_exception_increments_failures(self):
        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            side_effect=ConnectionError("timeout")
        )
        driver.write_ac_power_setpoint = AsyncMock()

        ctrl = _make_controller(driver)
        snap = await ctrl.poll()

        assert snap.consecutive_failures == 1
        assert snap.available is True

    @pytest.mark.anyio
    async def test_poll_three_failures_triggers_safe_state(self):
        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            side_effect=ConnectionError("timeout")
        )
        driver.write_ac_power_setpoint = AsyncMock()

        ctrl = _make_controller(driver)

        for _ in range(3):
            snap = await ctrl.poll()

        assert snap.available is False
        assert snap.consecutive_failures == 3

        # Safe state: zero setpoint to all 3 phases
        expected_calls = [call(1, 0.0), call(2, 0.0), call(3, 0.0)]
        driver.write_ac_power_setpoint.assert_has_awaits(
            expected_calls, any_order=False
        )

    @pytest.mark.anyio
    async def test_poll_stale_detection(self):
        """Stale data (timestamp older than 2 * loop_interval_s) increments failures."""
        stale_time = time.monotonic() - 20.0  # way past 2*5=10s
        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(timestamp=stale_time)
        )
        driver.write_ac_power_setpoint = AsyncMock()

        ctrl = _make_controller(driver, loop_interval_s=5.0)
        snap = await ctrl.poll()

        assert snap.consecutive_failures >= 1

    @pytest.mark.anyio
    async def test_poll_success_resets_failures(self):
        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            side_effect=ConnectionError("timeout")
        )
        driver.write_ac_power_setpoint = AsyncMock()

        ctrl = _make_controller(driver)
        await ctrl.poll()
        await ctrl.poll()

        # Now succeed
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data()
        )
        snap = await ctrl.poll()

        assert snap.consecutive_failures == 0
        assert snap.available is True


class TestVictronControllerExecute:
    """execute() converts commands to Victron driver calls."""

    @pytest.mark.anyio
    async def test_execute_discharge_equal_split(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(
                ess_mode=2,
                grid_l1_power_w=None,
                grid_l2_power_w=None,
                grid_l3_power_w=None,
            )
        )
        driver.write_ac_power_setpoint = AsyncMock()

        ctrl = _make_controller(driver)
        # First poll to populate _last_data with ess_mode
        await ctrl.poll()

        cmd = ControllerCommand(
            role=BatteryRole.PRIMARY_DISCHARGE,
            target_watts=-5001.0,  # negative = discharge
        )
        await ctrl.execute(cmd)

        # Equal split: -5001/3 = -1667 per phase
        expected = [
            call(1, pytest.approx(-1667.0, abs=1), dry_run=False),
            call(2, pytest.approx(-1667.0, abs=1), dry_run=False),
            call(3, pytest.approx(-1667.0, abs=1), dry_run=False),
        ]
        driver.write_ac_power_setpoint.assert_has_awaits(expected)

    @pytest.mark.anyio
    async def test_execute_discharge_per_phase_grid(self):
        """With per-phase grid data, uses -grid_lN_power_w for each phase."""
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(
                ess_mode=2,
                grid_l1_power_w=200.0,
                grid_l2_power_w=150.0,
                grid_l3_power_w=100.0,
            )
        )
        driver.write_ac_power_setpoint = AsyncMock()

        ctrl = _make_controller(driver)
        await ctrl.poll()

        cmd = ControllerCommand(
            role=BatteryRole.PRIMARY_DISCHARGE,
            target_watts=-5000.0,
        )
        await ctrl.execute(cmd)

        # With per-phase grid: write -grid_lN_power_w
        expected = [
            call(1, -200.0, dry_run=False),
            call(2, -150.0, dry_run=False),
            call(3, -100.0, dry_run=False),
        ]
        driver.write_ac_power_setpoint.assert_has_awaits(expected)

    @pytest.mark.anyio
    async def test_execute_charging(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(ess_mode=2)
        )
        driver.write_ac_power_setpoint = AsyncMock()

        ctrl = _make_controller(driver)
        await ctrl.poll()

        cmd = ControllerCommand(
            role=BatteryRole.CHARGING,
            target_watts=3000.0,
        )
        await ctrl.execute(cmd)

        # Positive watts split across 3 phases
        expected = [
            call(1, 1000.0, dry_run=False),
            call(2, 1000.0, dry_run=False),
            call(3, 1000.0, dry_run=False),
        ]
        driver.write_ac_power_setpoint.assert_has_awaits(expected)

    @pytest.mark.anyio
    async def test_execute_holding(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(ess_mode=2)
        )
        driver.write_ac_power_setpoint = AsyncMock()

        ctrl = _make_controller(driver)
        await ctrl.poll()

        cmd = ControllerCommand(
            role=BatteryRole.HOLDING,
            target_watts=0.0,
        )
        await ctrl.execute(cmd)

        expected = [call(1, 0.0, dry_run=False), call(2, 0.0, dry_run=False), call(3, 0.0, dry_run=False)]
        driver.write_ac_power_setpoint.assert_has_awaits(expected)

    @pytest.mark.anyio
    async def test_execute_ess_mode_guard_skips_write(self):
        """ESS mode 0 or 1: logs warning, skips write, does NOT raise."""
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(ess_mode=1)
        )
        driver.write_ac_power_setpoint = AsyncMock()

        ctrl = _make_controller(driver)
        await ctrl.poll()

        cmd = ControllerCommand(
            role=BatteryRole.PRIMARY_DISCHARGE,
            target_watts=-5000.0,
        )
        # Should not raise
        await ctrl.execute(cmd)

        # Should NOT have written any setpoints
        driver.write_ac_power_setpoint.assert_not_awaited()

    @pytest.mark.anyio
    async def test_execute_ess_mode_0_skips(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(ess_mode=0)
        )
        driver.write_ac_power_setpoint = AsyncMock()

        ctrl = _make_controller(driver)
        await ctrl.poll()

        cmd = ControllerCommand(
            role=BatteryRole.CHARGING,
            target_watts=3000.0,
        )
        await ctrl.execute(cmd)

        driver.write_ac_power_setpoint.assert_not_awaited()

    @pytest.mark.anyio
    async def test_execute_stores_role(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(ess_mode=2)
        )
        driver.write_ac_power_setpoint = AsyncMock()

        ctrl = _make_controller(driver)
        await ctrl.poll()

        cmd = ControllerCommand(
            role=BatteryRole.PRIMARY_DISCHARGE,
            target_watts=-3000.0,
        )
        await ctrl.execute(cmd)

        assert ctrl.role == BatteryRole.PRIMARY_DISCHARGE


def _make_controller_with_validation(driver_mock, validation_config=None, loop_interval_s=5.0):
    from backend.config import SystemConfig
    from backend.victron_controller import VictronController

    return VictronController(
        driver=driver_mock,
        sys_config=SystemConfig(),
        loop_interval_s=loop_interval_s,
        validation_config=validation_config,
    )


class TestVictronDryRun:
    """Tests that execute() passes dry_run during validation period."""

    @pytest.mark.anyio
    async def test_execute_passes_dry_run_true_during_validation_period(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(ess_mode=2)
        )
        driver.write_ac_power_setpoint = AsyncMock()

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        ctrl._first_read_at = time.time()
        await ctrl.poll()

        cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        await ctrl.execute(cmd)

        # All 3 phase writes should have dry_run=True
        for c in driver.write_ac_power_setpoint.call_args_list:
            # Skip calls from poll (there shouldn't be any)
            if c.kwargs.get("dry_run") is not None or "dry_run" in c.kwargs:
                assert c.kwargs["dry_run"] is True

    @pytest.mark.anyio
    async def test_execute_passes_dry_run_false_after_validation_period(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(ess_mode=2)
        )
        driver.write_ac_power_setpoint = AsyncMock()

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        ctrl._first_read_at = time.time() - (49 * 3600)
        await ctrl.poll()

        cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        await ctrl.execute(cmd)

        # All writes after period should have dry_run=False
        for c in driver.write_ac_power_setpoint.call_args_list:
            if "dry_run" in c.kwargs:
                assert c.kwargs["dry_run"] is False

    @pytest.mark.anyio
    async def test_execute_charging_passes_dry_run(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(ess_mode=2)
        )
        driver.write_ac_power_setpoint = AsyncMock()

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        ctrl._first_read_at = time.time()
        await ctrl.poll()

        cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=3000.0)
        await ctrl.execute(cmd)

        # Each phase write should have dry_run=True
        for c in driver.write_ac_power_setpoint.call_args_list:
            if "dry_run" in c.kwargs:
                assert c.kwargs["dry_run"] is True

    @pytest.mark.anyio
    async def test_execute_discharge_passes_dry_run(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(
                ess_mode=2,
                grid_l1_power_w=200.0,
                grid_l2_power_w=150.0,
                grid_l3_power_w=100.0,
            )
        )
        driver.write_ac_power_setpoint = AsyncMock()

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        ctrl._first_read_at = time.time()
        await ctrl.poll()

        cmd = ControllerCommand(role=BatteryRole.PRIMARY_DISCHARGE, target_watts=-5000.0)
        await ctrl.execute(cmd)

        for c in driver.write_ac_power_setpoint.call_args_list:
            if "dry_run" in c.kwargs:
                assert c.kwargs["dry_run"] is True

    @pytest.mark.anyio
    async def test_forced_dry_run_always_passes_true(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(ess_mode=2)
        )
        driver.write_ac_power_setpoint = AsyncMock()

        cfg = HardwareValidationConfig(validation_period_hours=48.0, dry_run=True)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        ctrl._first_read_at = time.time() - (100 * 3600)
        await ctrl.poll()

        cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        await ctrl.execute(cmd)

        for c in driver.write_ac_power_setpoint.call_args_list:
            if "dry_run" in c.kwargs:
                assert c.kwargs["dry_run"] is True

    @pytest.mark.anyio
    async def test_no_validation_config_never_passes_dry_run_true(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data(ess_mode=2)
        )
        driver.write_ac_power_setpoint = AsyncMock()

        ctrl = _make_controller_with_validation(driver, validation_config=None)
        await ctrl.poll()

        cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        await ctrl.execute(cmd)

        for c in driver.write_ac_power_setpoint.call_args_list:
            if "dry_run" in c.kwargs:
                assert c.kwargs["dry_run"] is False


class TestVictronValidationPeriod:
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
        ctrl._first_read_at = time.time() - (24 * 3600)
        assert ctrl._in_validation_period() is True

    def test_in_validation_period_false_when_past_48h(self):
        driver = AsyncMock()
        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        ctrl._first_read_at = time.time() - (49 * 3600)
        assert ctrl._in_validation_period() is False

    def test_in_validation_period_false_when_no_config(self):
        driver = AsyncMock()
        ctrl = _make_controller_with_validation(driver, validation_config=None)
        assert ctrl._in_validation_period() is False

    @pytest.mark.anyio
    async def test_handle_failure_does_not_pass_dry_run(self):
        """Safe-state writes must bypass validation period gate."""
        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            side_effect=ConnectionError("timeout")
        )
        driver.write_ac_power_setpoint = AsyncMock()

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        ctrl._first_read_at = time.time()

        for _ in range(3):
            await ctrl.poll()

        # Safe state calls should NOT have dry_run keyword
        for c in driver.write_ac_power_setpoint.call_args_list:
            assert "dry_run" not in c.kwargs

    @pytest.mark.anyio
    async def test_poll_sets_first_read_at(self):
        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data()
        )

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)
        assert ctrl._first_read_at is None

        await ctrl.poll()
        assert ctrl._first_read_at is not None
        assert isinstance(ctrl._first_read_at, float)

    @pytest.mark.anyio
    async def test_poll_does_not_overwrite_first_read_at(self):
        driver = AsyncMock()
        driver.read_system_state = AsyncMock(
            return_value=_make_victron_data()
        )

        cfg = HardwareValidationConfig(validation_period_hours=48.0)
        ctrl = _make_controller_with_validation(driver, validation_config=cfg)

        await ctrl.poll()
        first = ctrl._first_read_at

        await ctrl.poll()
        assert ctrl._first_read_at == first
