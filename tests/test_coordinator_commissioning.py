"""Tests for coordinator commissioning integration.

Covers:
- Shadow mode logs DecisionEntry without calling execute
- Stage gating blocks Victron/Huawei writes per commissioning stage
- No commissioning manager allows all writes (backward compat)
- All execute sites use central _execute_commands method
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import OrchestratorConfig, SystemConfig
from backend.controller_model import (
    BatteryRole,
    ControllerCommand,
    ControllerSnapshot,
    DecisionEntry,
)
from backend.coordinator import Coordinator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snap(
    soc: float = 50.0,
    power: float = 0.0,
    available: bool = True,
    role: BatteryRole = BatteryRole.HOLDING,
    failures: int = 0,
    grid_power_w: float | None = None,
    charge_headroom_w: float = 5000.0,
) -> ControllerSnapshot:
    return ControllerSnapshot(
        soc_pct=soc,
        power_w=power,
        available=available,
        role=role,
        consecutive_failures=failures,
        timestamp=time.monotonic(),
        grid_power_w=grid_power_w,
        charge_headroom_w=charge_headroom_w,
    )


def _make_coordinator() -> tuple[Coordinator, AsyncMock, AsyncMock]:
    h_ctrl = AsyncMock()
    v_ctrl = AsyncMock()
    coord = Coordinator(
        huawei_ctrl=h_ctrl,
        victron_ctrl=v_ctrl,
        sys_config=SystemConfig(),
        orch_config=OrchestratorConfig(),
    )
    return coord, h_ctrl, v_ctrl


def _mock_commissioning_manager(
    *,
    shadow_mode: bool = False,
    can_write_huawei: bool = True,
    can_write_victron: bool = True,
    stage: str = "DUAL_BATTERY",
) -> MagicMock:
    mgr = MagicMock()
    mgr.shadow_mode = shadow_mode
    state = MagicMock()
    state.can_write_huawei.return_value = can_write_huawei
    state.can_write_victron.return_value = can_write_victron
    mgr.state = state
    mgr.stage = MagicMock()
    mgr.stage.value = stage
    return mgr


# ===========================================================================
# Shadow mode
# ===========================================================================


class TestShadowMode:
    """Shadow mode logs decisions without executing."""

    async def test_shadow_mode_logs_decision_no_execute(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        mgr = _mock_commissioning_manager(shadow_mode=True)
        coord.set_commissioning_manager(mgr)

        h_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=1000.0)
        v_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=500.0)

        await coord._execute_commands(h_cmd, v_cmd)

        # Should NOT call execute on either controller
        h_ctrl.execute.assert_not_called()
        v_ctrl.execute.assert_not_called()

        # Should have logged a DecisionEntry with trigger=shadow_mode
        assert len(coord._decisions) == 1
        entry = coord._decisions[0]
        assert entry.trigger == "shadow_mode"
        assert "Shadow mode" in entry.reasoning


# ===========================================================================
# Stage gating
# ===========================================================================


class TestStageGating:
    """Stage gating blocks writes per commissioning stage."""

    async def test_stage_gates_victron_write(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        # READ_ONLY: neither should execute
        mgr = _mock_commissioning_manager(
            can_write_huawei=False, can_write_victron=False, stage="READ_ONLY"
        )
        coord.set_commissioning_manager(mgr)

        h_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=1000.0)
        v_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=500.0)
        await coord._execute_commands(h_cmd, v_cmd)

        h_ctrl.execute.assert_not_called()
        v_ctrl.execute.assert_not_called()

    async def test_single_battery_allows_victron(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        mgr = _mock_commissioning_manager(
            can_write_huawei=False, can_write_victron=True, stage="SINGLE_BATTERY"
        )
        coord.set_commissioning_manager(mgr)

        h_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=1000.0)
        v_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=500.0)
        await coord._execute_commands(h_cmd, v_cmd)

        h_ctrl.execute.assert_not_called()
        v_ctrl.execute.assert_called_once_with(v_cmd)

    async def test_stage_gates_huawei_write(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        # SINGLE_BATTERY: huawei blocked, victron allowed
        mgr = _mock_commissioning_manager(
            can_write_huawei=False, can_write_victron=True, stage="SINGLE_BATTERY"
        )
        coord.set_commissioning_manager(mgr)

        h_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=1000.0)
        v_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=500.0)
        await coord._execute_commands(h_cmd, v_cmd)

        h_ctrl.execute.assert_not_called()

    async def test_dual_battery_allows_both(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        mgr = _mock_commissioning_manager(
            can_write_huawei=True, can_write_victron=True, stage="DUAL_BATTERY"
        )
        coord.set_commissioning_manager(mgr)

        h_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=1000.0)
        v_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=500.0)
        await coord._execute_commands(h_cmd, v_cmd)

        h_ctrl.execute.assert_called_once_with(h_cmd)
        v_ctrl.execute.assert_called_once_with(v_cmd)


# ===========================================================================
# Backward compatibility
# ===========================================================================


class TestNoCommissioningManager:
    """When no commissioning manager, both controllers execute normally."""

    async def test_no_commissioning_manager_allows_all(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        # No set_commissioning_manager call

        h_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=1000.0)
        v_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=500.0)
        await coord._execute_commands(h_cmd, v_cmd)

        h_ctrl.execute.assert_called_once_with(h_cmd)
        v_ctrl.execute.assert_called_once_with(v_cmd)


# ===========================================================================
# All execute sites use central method
# ===========================================================================


class TestAllExecuteSitesUseCentralMethod:
    """Verify no direct execute calls leak in _run_cycle."""

    def test_all_execute_sites_use_central_method(self):
        import inspect
        import re

        source = inspect.getsource(Coordinator._run_cycle)
        # Find all .execute( calls in _run_cycle
        direct_calls = re.findall(r"\._(?:huawei|victron)_ctrl\.execute\(", source)
        # There should be ZERO direct execute calls in _run_cycle
        assert len(direct_calls) == 0, (
            f"Found {len(direct_calls)} direct execute() calls in _run_cycle; "
            "all should use _execute_commands()"
        )
