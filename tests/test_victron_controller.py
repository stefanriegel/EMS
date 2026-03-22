"""Tests for VictronController — poll, execute, failure counting, safe state."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, call, patch

import pytest

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
            call(1, pytest.approx(-1667.0, abs=1)),
            call(2, pytest.approx(-1667.0, abs=1)),
            call(3, pytest.approx(-1667.0, abs=1)),
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
            call(1, -200.0),
            call(2, -150.0),
            call(3, -100.0),
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
            call(1, 1000.0),
            call(2, 1000.0),
            call(3, 1000.0),
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

        expected = [call(1, 0.0), call(2, 0.0), call(3, 0.0)]
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
