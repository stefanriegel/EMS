"""Coordinator integration tests for _write_integrations health-logger and EMMA paths.

These tests exercise the code paths that had zero coverage before M013-kk1vuq S01.
The T01 bug (AttributeError from .value on a plain str) went undetected because
no test ever called _write_integrations with a real HealthLogger.

Covers:
- Health logger fires write_health after interval elapses
- HealthSnapshot.control_state is a plain string (not enum with .value)
- Health logger is skipped when interval has not elapsed
- EMMA write fires when _last_emma_snap is set
- EMMA write is skipped when _last_emma_snap is None
- No AttributeError when writer is None (health logger inside writer guard)
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import OrchestratorConfig, SystemConfig
from backend.controller_model import (
    BatteryRole,
    ControllerCommand,
    CoordinatorState,
)
from backend.coordinator import Coordinator
from backend.cross_charge import CrossChargeDetector
from backend.drivers.emma_driver import EmmaSnapshot
from backend.health_logger import HealthLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snap(
    soc: float = 50.0,
    power: float = 0.0,
    available: bool = True,
    grid_power_w: float | None = None,
) -> "ControllerSnapshot":
    """Minimal ControllerSnapshot for _write_integrations calls."""
    from backend.controller_model import ControllerSnapshot

    return ControllerSnapshot(
        soc_pct=soc,
        power_w=power,
        available=available,
        role=BatteryRole.HOLDING,
        consecutive_failures=0,
        timestamp=time.monotonic(),
        grid_power_w=grid_power_w,
        master_active_power_w=None,
        charge_headroom_w=5000.0,
        max_charge_power_w=None,
        max_discharge_power_w=None,
        grid_l1_power_w=None,
        grid_l2_power_w=None,
        grid_l3_power_w=None,
        ess_mode=None,
    )


def _make_cmd(role: BatteryRole = BatteryRole.HOLDING, watts: float = 0.0) -> ControllerCommand:
    return ControllerCommand(role=role, target_watts=watts)


def _make_coordinator_with_writer() -> tuple[Coordinator, MagicMock]:
    """Create a Coordinator wired with a fully-mocked InfluxMetricsWriter."""
    h_ctrl = AsyncMock()
    v_ctrl = AsyncMock()
    mock_writer = MagicMock()
    mock_writer.write_coordinator_state = AsyncMock()
    mock_writer.write_per_system_metrics = AsyncMock()
    mock_writer.write_health = AsyncMock()
    mock_writer.write_emma_state = AsyncMock()
    mock_writer.write_decision = AsyncMock()
    coord = Coordinator(
        huawei_ctrl=h_ctrl,
        victron_ctrl=v_ctrl,
        sys_config=SystemConfig(),
        orch_config=OrchestratorConfig(),
        writer=mock_writer,
    )
    coord.set_cross_charge_detector(CrossChargeDetector())
    # Inject a real HealthLogger directly (no set_health_logger method exists)
    coord._health_logger = HealthLogger()
    # Build minimal coordinator state so health-logger capture() doesn't early-return
    h_snap = _make_snap(soc=60.0)
    v_snap = _make_snap(soc=55.0, grid_power_w=0.0)
    h_cmd = _make_cmd()
    v_cmd = _make_cmd()
    coord._state = coord._build_state(h_snap, v_snap, h_cmd, v_cmd)
    return coord, mock_writer


def _force_log_interval_elapsed(coord: Coordinator) -> None:
    """Set _last_log_time to epoch-0 so should_log() returns True immediately."""
    coord._health_logger._last_log_time = 0.0  # epoch means definitely elapsed


def _suppress_log_interval(coord: Coordinator) -> None:
    """Set _last_log_time to now so should_log() returns False."""
    coord._health_logger._last_log_time = time.monotonic()


# ---------------------------------------------------------------------------
# Health logger tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_logger_calls_write_health_after_interval():
    """When the 5-minute interval has elapsed, write_health should be awaited once."""
    coord, writer = _make_coordinator_with_writer()
    _force_log_interval_elapsed(coord)

    h_snap = _make_snap(soc=60.0)
    v_snap = _make_snap(soc=55.0, grid_power_w=0.0)
    await coord._write_integrations(h_snap, v_snap, _make_cmd(), _make_cmd(), None)

    writer.write_health.assert_awaited_once()


@pytest.mark.anyio
async def test_health_logger_control_state_is_plain_string():
    """HealthSnapshot.control_state must be a plain str, never an enum instance.

    This is the regression test for the T01 bug: .value was called on
    coordinator._state.control_state which is already a plain str.
    """
    coord, writer = _make_coordinator_with_writer()
    _force_log_interval_elapsed(coord)

    h_snap = _make_snap(soc=60.0)
    v_snap = _make_snap(soc=55.0, grid_power_w=0.0)
    await coord._write_integrations(h_snap, v_snap, _make_cmd(), _make_cmd(), None)

    # Retrieve the HealthSnapshot that was passed to write_health
    call_args = writer.write_health.call_args
    assert call_args is not None, "write_health was never called"
    health_snap = call_args.args[0]  # positional arg 0

    assert isinstance(health_snap.control_state, str), (
        f"control_state should be str, got {type(health_snap.control_state)}"
    )
    # It should NOT have a .value attribute (i.e., not an enum)
    assert not hasattr(health_snap.control_state, "value") or isinstance(
        health_snap.control_state, str
    ), "control_state should be a plain string, not an enum with .value"
    # The value itself must be a known control state
    assert health_snap.control_state in {"IDLE", "DISCHARGE", "CHARGE", "GRID_CHARGE",
                                          "HOLDING", "FAILOVER", "EXPORT", "EXPORTING",
                                          "UNKNOWN"}, (
        f"Unexpected control_state value: {health_snap.control_state!r}"
    )


@pytest.mark.anyio
async def test_health_logger_skipped_before_interval():
    """When the 5-minute interval has not yet elapsed, write_health must not be called."""
    coord, writer = _make_coordinator_with_writer()
    _suppress_log_interval(coord)

    h_snap = _make_snap(soc=60.0)
    v_snap = _make_snap(soc=55.0, grid_power_w=0.0)
    await coord._write_integrations(h_snap, v_snap, _make_cmd(), _make_cmd(), None)

    writer.write_health.assert_not_awaited()


@pytest.mark.anyio
async def test_health_logger_not_called_when_writer_is_none():
    """When writer=None, _write_integrations must not raise AttributeError.

    The health logger code is inside the ``if self._writer is not None:`` guard,
    so it should simply be skipped.
    """
    h_ctrl = AsyncMock()
    v_ctrl = AsyncMock()
    coord = Coordinator(
        huawei_ctrl=h_ctrl,
        victron_ctrl=v_ctrl,
        sys_config=SystemConfig(),
        orch_config=OrchestratorConfig(),
        writer=None,  # <-- no writer
    )
    coord.set_cross_charge_detector(CrossChargeDetector())
    coord._health_logger = HealthLogger()
    h_snap = _make_snap(soc=60.0)
    v_snap = _make_snap(soc=55.0, grid_power_w=0.0)
    coord._state = coord._build_state(h_snap, v_snap, _make_cmd(), _make_cmd())
    _force_log_interval_elapsed(coord)

    # Must not raise — health logger is guarded by ``if self._writer is not None``
    await coord._write_integrations(h_snap, v_snap, _make_cmd(), _make_cmd(), None)


# ---------------------------------------------------------------------------
# EMMA write tests
# ---------------------------------------------------------------------------


def _make_emma_snap() -> EmmaSnapshot:
    return EmmaSnapshot(
        pv_power_w=3000,
        load_power_w=1200,
        feed_in_power_w=-500,
        battery_power_w=0,
        battery_soc_pct=75.0,
        pv_yield_today_kwh=12.5,
        consumption_today_kwh=8.3,
        charged_today_kwh=4.1,
        discharged_today_kwh=2.0,
        ess_control_mode=2,
    )


@pytest.mark.anyio
async def test_emma_write_called_when_snap_available():
    """When _last_emma_snap is set, write_emma_state must be awaited once."""
    coord, writer = _make_coordinator_with_writer()
    coord._last_emma_snap = _make_emma_snap()

    h_snap = _make_snap(soc=60.0)
    v_snap = _make_snap(soc=55.0, grid_power_w=0.0)
    await coord._write_integrations(h_snap, v_snap, _make_cmd(), _make_cmd(), None)

    writer.write_emma_state.assert_awaited_once()
    # Verify the snapshot passed is the one we set
    call_args = writer.write_emma_state.call_args
    assert call_args.args[0] is coord._last_emma_snap


@pytest.mark.anyio
async def test_emma_write_skipped_when_snap_none():
    """When _last_emma_snap is None (default), write_emma_state must not be called."""
    coord, writer = _make_coordinator_with_writer()
    # _last_emma_snap is None by default — no explicit set needed

    h_snap = _make_snap(soc=60.0)
    v_snap = _make_snap(soc=55.0, grid_power_w=0.0)
    await coord._write_integrations(h_snap, v_snap, _make_cmd(), _make_cmd(), None)

    writer.write_emma_state.assert_not_awaited()


@pytest.mark.anyio
async def test_emma_write_passes_true_consumption():
    """write_emma_state receives true_consumption = load_power_w + victron_discharge."""
    coord, writer = _make_coordinator_with_writer()
    emma = _make_emma_snap()  # load_power_w=1200
    coord._last_emma_snap = emma

    # Victron discharging at 400 W (power_w=-400 means discharging)
    v_snap = _make_snap(soc=55.0, power=-400.0, grid_power_w=0.0)
    h_snap = _make_snap(soc=60.0)
    await coord._write_integrations(h_snap, v_snap, _make_cmd(), _make_cmd(), None)

    call_args = writer.write_emma_state.call_args
    true_consumption = call_args.args[1]
    # v_discharge = max(0, -int(-400)) = 400; true_consumption = 1200 + 400 = 1600
    assert true_consumption == 1600


@pytest.mark.anyio
async def test_health_and_emma_both_fire_when_both_active():
    """Both write_health and write_emma_state are awaited in the same cycle."""
    coord, writer = _make_coordinator_with_writer()
    coord._last_emma_snap = _make_emma_snap()
    _force_log_interval_elapsed(coord)

    h_snap = _make_snap(soc=60.0)
    v_snap = _make_snap(soc=55.0, grid_power_w=0.0)
    await coord._write_integrations(h_snap, v_snap, _make_cmd(), _make_cmd(), None)

    writer.write_health.assert_awaited_once()
    writer.write_emma_state.assert_awaited_once()
