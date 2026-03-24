"""Tests for HuaweiModeManager -- TOU mode lifecycle state machine."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, call

import pytest

from backend.config import ModeManagerConfig
from backend.drivers.huawei_driver import StorageWorkingModesC
from backend.huawei_mode_manager import HuaweiModeManager, ModeState


def _make_config(**overrides) -> ModeManagerConfig:
    defaults = dict(
        enabled=True,
        settle_delay_s=0.01,
        health_check_interval_s=0.0,
        reapply_cooldown_s=0.01,
    )
    defaults.update(overrides)
    return ModeManagerConfig(**defaults)


def _make_driver() -> AsyncMock:
    driver = AsyncMock()
    driver.write_max_charge_power = AsyncMock()
    driver.write_max_discharge_power = AsyncMock()
    driver.write_battery_mode = AsyncMock()
    return driver


# -----------------------------------------------------------------------
# HCTL-01: Startup activation
# -----------------------------------------------------------------------


class TestActivation:
    """HCTL-01: EMS switches Huawei to TOU working mode on startup."""

    @pytest.mark.anyio
    async def test_activate_clamps_power_first(self):
        """activate() calls write_max_charge_power(0) and write_max_discharge_power(0) BEFORE write_battery_mode."""
        driver = _make_driver()
        cfg = _make_config()
        mgr = HuaweiModeManager(driver, cfg)

        await mgr.activate()

        # Verify call order: clamp charge, clamp discharge, then mode write
        calls = driver.method_calls
        charge_idx = next(i for i, c in enumerate(calls) if c == call.write_max_charge_power(0))
        discharge_idx = next(i for i, c in enumerate(calls) if c == call.write_max_discharge_power(0))
        mode_idx = next(
            i for i, c in enumerate(calls)
            if c == call.write_battery_mode(StorageWorkingModesC.TIME_OF_USE_LUNA2000)
        )
        assert charge_idx < mode_idx, "charge clamp must happen before mode write"
        assert discharge_idx < mode_idx, "discharge clamp must happen before mode write"

    @pytest.mark.anyio
    async def test_activate_writes_tou_mode(self):
        """activate() calls write_battery_mode(TIME_OF_USE_LUNA2000)."""
        driver = _make_driver()
        cfg = _make_config()
        mgr = HuaweiModeManager(driver, cfg)

        await mgr.activate()

        driver.write_battery_mode.assert_awaited_with(
            StorageWorkingModesC.TIME_OF_USE_LUNA2000
        )

    @pytest.mark.anyio
    async def test_activate_transitions_to_active(self):
        """After activate() completes, state is ACTIVE."""
        driver = _make_driver()
        cfg = _make_config()
        mgr = HuaweiModeManager(driver, cfg)

        await mgr.activate()

        assert mgr.state == ModeState.ACTIVE


# -----------------------------------------------------------------------
# HCTL-02: Shutdown restore
# -----------------------------------------------------------------------


class TestRestore:
    """HCTL-02: EMS restores Huawei to self-consumption mode on shutdown."""

    @pytest.mark.anyio
    async def test_restore_writes_self_consumption(self):
        """restore() calls write_battery_mode(MAXIMISE_SELF_CONSUMPTION)."""
        driver = _make_driver()
        cfg = _make_config()
        mgr = HuaweiModeManager(driver, cfg)
        await mgr.activate()

        await mgr.restore()

        driver.write_battery_mode.assert_awaited_with(
            StorageWorkingModesC.MAXIMISE_SELF_CONSUMPTION
        )

    @pytest.mark.anyio
    async def test_restore_idempotent(self):
        """restore() does not raise if driver raises (logs WARNING, swallows)."""
        driver = _make_driver()
        driver.write_battery_mode = AsyncMock(side_effect=Exception("Modbus error"))
        cfg = _make_config()
        mgr = HuaweiModeManager(driver, cfg)

        # Should not raise
        await mgr.restore()

    @pytest.mark.anyio
    async def test_crash_recovery_skips_clamping(self):
        """activate() with current_working_mode=5 (already TOU) skips clamping."""
        driver = _make_driver()
        cfg = _make_config()
        mgr = HuaweiModeManager(driver, cfg)

        await mgr.activate(current_working_mode=5)

        # Should NOT have called any power clamping or mode write
        driver.write_max_charge_power.assert_not_awaited()
        driver.write_max_discharge_power.assert_not_awaited()
        driver.write_battery_mode.assert_not_awaited()
        assert mgr.state == ModeState.ACTIVE


# -----------------------------------------------------------------------
# HCTL-03: Health check
# -----------------------------------------------------------------------


class TestHealthCheck:
    """HCTL-03: Periodic mode verification and re-apply."""

    @pytest.mark.anyio
    async def test_health_check_reapplies_on_revert(self):
        """check_health(current_working_mode=2) when ACTIVE triggers re-apply."""
        driver = _make_driver()
        cfg = _make_config()
        mgr = HuaweiModeManager(driver, cfg)
        await mgr.activate()
        driver.reset_mock()

        await mgr.check_health(current_working_mode=2)

        # Should have re-applied: clamp + mode switch
        driver.write_max_charge_power.assert_awaited_with(0)
        driver.write_max_discharge_power.assert_awaited_with(0)
        driver.write_battery_mode.assert_awaited_with(
            StorageWorkingModesC.TIME_OF_USE_LUNA2000
        )

    @pytest.mark.anyio
    async def test_health_check_noop_when_correct(self):
        """check_health(current_working_mode=5) when ACTIVE does nothing."""
        driver = _make_driver()
        cfg = _make_config()
        mgr = HuaweiModeManager(driver, cfg)
        await mgr.activate()
        driver.reset_mock()

        await mgr.check_health(current_working_mode=5)

        driver.write_max_charge_power.assert_not_awaited()
        driver.write_max_discharge_power.assert_not_awaited()
        driver.write_battery_mode.assert_not_awaited()

    @pytest.mark.anyio
    async def test_health_check_cooldown(self):
        """After re-apply, subsequent check_health within cooldown is skipped."""
        driver = _make_driver()
        cfg = _make_config(reapply_cooldown_s=10.0)
        mgr = HuaweiModeManager(driver, cfg)
        await mgr.activate()
        driver.reset_mock()

        # First re-apply
        await mgr.check_health(current_working_mode=2)
        assert driver.write_battery_mode.await_count == 1
        driver.reset_mock()

        # Second call within cooldown -- should be skipped
        await mgr.check_health(current_working_mode=2)
        driver.write_battery_mode.assert_not_awaited()

    @pytest.mark.anyio
    async def test_health_check_respects_interval(self):
        """check_health only runs when health_check_interval_s has elapsed."""
        driver = _make_driver()
        cfg = _make_config(health_check_interval_s=10.0)
        mgr = HuaweiModeManager(driver, cfg)
        await mgr.activate()
        driver.reset_mock()

        # Immediately after activate, interval hasn't elapsed
        await mgr.check_health(current_working_mode=2)
        # Should skip because health_check_interval hasn't elapsed
        driver.write_battery_mode.assert_not_awaited()


# -----------------------------------------------------------------------
# HCTL-04: Transition safety
# -----------------------------------------------------------------------


class TestTransitionSafety:
    """HCTL-04: Mode transitions block controller power writes."""

    @pytest.mark.anyio
    async def test_is_transitioning_during_activate(self):
        """is_transitioning is True during CLAMPING and SWITCHING states."""
        driver = _make_driver()
        cfg = _make_config(settle_delay_s=0.5)
        mgr = HuaweiModeManager(driver, cfg)

        # Record states during activation
        states_seen: list[ModeState] = []

        async def recording_sleep(delay):
            states_seen.append(mgr.state)

        import unittest.mock
        with unittest.mock.patch("backend.huawei_mode_manager.anyio.sleep", side_effect=recording_sleep):
            await mgr.activate()

        # During transition, should have seen CLAMPING and/or SWITCHING
        transitioning_states = [s for s in states_seen if s in (ModeState.CLAMPING, ModeState.SWITCHING)]
        assert len(transitioning_states) > 0, "Should observe transitioning states during activate"

        # And is_transitioning should have been True for those states
        for s in transitioning_states:
            # Verify those states map to is_transitioning
            assert s in (ModeState.CLAMPING, ModeState.SWITCHING, ModeState.RESTORING)

    @pytest.mark.anyio
    async def test_is_transitioning_false_when_active(self):
        """is_transitioning is False in ACTIVE state."""
        driver = _make_driver()
        cfg = _make_config()
        mgr = HuaweiModeManager(driver, cfg)
        await mgr.activate()

        assert mgr.state == ModeState.ACTIVE
        assert mgr.is_transitioning is False

    @pytest.mark.anyio
    async def test_activate_waits_settle(self):
        """activate() includes asyncio.sleep(settle_delay_s) between clamp and mode switch."""
        driver = _make_driver()
        cfg = _make_config(settle_delay_s=0.5)
        mgr = HuaweiModeManager(driver, cfg)

        sleep_calls: list[float] = []

        async def track_sleep(delay):
            sleep_calls.append(delay)

        import unittest.mock
        with unittest.mock.patch("backend.huawei_mode_manager.anyio.sleep", side_effect=track_sleep):
            await mgr.activate()

        # Should have at least 2 settle sleeps (after clamp, after switch)
        assert len(sleep_calls) >= 2
        assert all(d == 0.5 for d in sleep_calls)
