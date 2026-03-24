"""Tests for the Victron 45s watchdog guard.

Covers:
- Guard fires zero-write to all 3 phases after 45s
- Guard skips writes during validation period
- Guard handles write failures gracefully
- Guard can be cancelled cleanly
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import HardwareValidationConfig, SystemConfig
from backend.controller_model import BatteryRole
from backend.victron_controller import VictronController


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_asyncio():
    """Skip test when not running under asyncio (e.g. trio)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pytest.skip("requires asyncio event loop")


def _make_controller(
    *,
    validation_config: HardwareValidationConfig | None = None,
) -> tuple[VictronController, AsyncMock]:
    driver = AsyncMock()
    ctrl = VictronController(
        driver=driver,
        sys_config=SystemConfig(),
        validation_config=validation_config,
    )
    return ctrl, driver


# ===========================================================================
# Guard fires zero writes
# ===========================================================================


class TestGuardFiresZeroWrite:
    """After the sleep interval, guard writes 0W to phases 1, 2, 3."""

    async def test_guard_fires_zero_write(self):
        _require_asyncio()
        ctrl, driver = _make_controller()

        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            task = asyncio.create_task(ctrl._watchdog_guard_loop())
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should have written 0W to all 3 phases
        assert driver.write_ac_power_setpoint.call_count == 3
        calls = driver.write_ac_power_setpoint.call_args_list
        assert calls[0].args == (1, 0.0)
        assert calls[1].args == (2, 0.0)
        assert calls[2].args == (3, 0.0)


# ===========================================================================
# Guard skips during validation
# ===========================================================================


class TestGuardSkipsDuringValidation:
    """Guard does not write when in validation period."""

    async def test_guard_skips_during_validation(self):
        _require_asyncio()
        ctrl, driver = _make_controller(
            validation_config=HardwareValidationConfig(dry_run=True),
        )

        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            task = asyncio.create_task(ctrl._watchdog_guard_loop())
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should NOT have written anything (validation period active)
        driver.write_ac_power_setpoint.assert_not_called()


# ===========================================================================
# Guard handles write failure
# ===========================================================================


class TestGuardHandlesWriteFailure:
    """Guard logs warning and continues on write failure."""

    async def test_guard_handles_write_failure(self):
        _require_asyncio()
        ctrl, driver = _make_controller()
        driver.write_ac_power_setpoint.side_effect = [
            Exception("Modbus error"),
            Exception("Modbus error"),
            Exception("Modbus error"),
        ]

        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            task = asyncio.create_task(ctrl._watchdog_guard_loop())
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should have attempted all 3 writes despite failures
        assert driver.write_ac_power_setpoint.call_count == 3


# ===========================================================================
# Guard cancellation
# ===========================================================================


class TestGuardCancellation:
    """Guard task can be cancelled cleanly."""

    async def test_guard_cancellation(self):
        _require_asyncio()
        ctrl, driver = _make_controller()

        ctrl.start_watchdog_guard()
        assert ctrl._watchdog_guard_task is not None

        await ctrl.stop_watchdog_guard()
        assert ctrl._watchdog_guard_task is None or ctrl._watchdog_guard_task.done()

    async def test_start_noop_if_already_running(self):
        _require_asyncio()
        ctrl, driver = _make_controller()

        ctrl.start_watchdog_guard()
        first_task = ctrl._watchdog_guard_task

        ctrl.start_watchdog_guard()  # second call is no-op
        assert ctrl._watchdog_guard_task is first_task

        await ctrl.stop_watchdog_guard()

    async def test_stop_noop_if_not_running(self):
        _require_asyncio()
        ctrl, driver = _make_controller()
        # Should not raise
        await ctrl.stop_watchdog_guard()
