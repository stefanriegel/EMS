"""Tests for the Coordinator — dual-battery control loop.

Covers:
- P_target computation (grid_power_w primary, master fallback)
- SoC-based role assignment (PRIMARY/SECONDARY/HOLDING)
- Swap hysteresis (3% threshold prevents flapping)
- Power allocation (primary gets full, secondary gets proportional)
- PV surplus routing (Huawei first, then Victron)
- Per-system hysteresis dead-band (Huawei 300W, Victron 150W)
- Ramp limiting per system
- Debounce (2-cycle delay for role transitions)
- Grid charge slot handling
- EVCC hold mode
- Failover (survivor gets full P_target)
- State building (CoordinatorState backward-compat)
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import OrchestratorConfig, SystemConfig
from backend.controller_model import (
    BatteryRole,
    ControllerCommand,
    ControllerSnapshot,
    CoordinatorState,
    PoolStatus,
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
    master_active_power_w: float | None = None,
    charge_headroom_w: float = 5000.0,
    max_charge_power_w: int | None = None,
    max_discharge_power_w: int | None = None,
    grid_l1_power_w: float | None = None,
    grid_l2_power_w: float | None = None,
    grid_l3_power_w: float | None = None,
    ess_mode: int | None = None,
) -> ControllerSnapshot:
    """Build a ControllerSnapshot with sensible defaults."""
    return ControllerSnapshot(
        soc_pct=soc,
        power_w=power,
        available=available,
        role=role,
        consecutive_failures=failures,
        timestamp=time.monotonic(),
        grid_power_w=grid_power_w,
        master_active_power_w=master_active_power_w,
        charge_headroom_w=charge_headroom_w,
        max_charge_power_w=max_charge_power_w,
        max_discharge_power_w=max_discharge_power_w,
        grid_l1_power_w=grid_l1_power_w,
        grid_l2_power_w=grid_l2_power_w,
        grid_l3_power_w=grid_l3_power_w,
        ess_mode=ess_mode,
    )


def _make_coordinator(
    sys_config: SystemConfig | None = None,
    orch_config: OrchestratorConfig | None = None,
) -> tuple[Coordinator, AsyncMock, AsyncMock]:
    """Create a Coordinator with mocked controllers."""
    h_ctrl = AsyncMock()
    v_ctrl = AsyncMock()
    sys_cfg = sys_config or SystemConfig()
    orch_cfg = orch_config or OrchestratorConfig()
    coord = Coordinator(
        huawei_ctrl=h_ctrl,
        victron_ctrl=v_ctrl,
        sys_config=sys_cfg,
        orch_config=orch_cfg,
    )
    return coord, h_ctrl, v_ctrl


# ===========================================================================
# P_target computation
# ===========================================================================


class TestPTargetComputation:
    """_compute_p_target uses victron grid_power_w as primary source."""

    async def test_primary_source_victron_grid_power(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        h_snap = _snap(soc=50.0, master_active_power_w=100.0)
        v_snap = _snap(soc=50.0, grid_power_w=1500.0)
        p = coord._compute_p_target(h_snap, v_snap)
        assert p == 1500.0

    async def test_fallback_huawei_master_power(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        h_snap = _snap(soc=50.0, master_active_power_w=800.0)
        v_snap = _snap(soc=50.0, grid_power_w=None, available=False)
        p = coord._compute_p_target(h_snap, v_snap)
        # Huawei active_power positive=export, so P_target = -master
        assert p == -800.0

    async def test_no_source_returns_zero(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        h_snap = _snap(soc=50.0, master_active_power_w=None)
        v_snap = _snap(soc=50.0, grid_power_w=None)
        p = coord._compute_p_target(h_snap, v_snap)
        assert p == 0.0


# ===========================================================================
# Role assignment
# ===========================================================================


class TestRoleAssignment:
    """SoC-based role assignment with swap hysteresis."""

    async def test_higher_soc_gets_primary(self):
        coord, _, _ = _make_coordinator()
        h_role, v_role = coord._assign_discharge_roles(80.0, 50.0)
        assert h_role == BatteryRole.PRIMARY_DISCHARGE
        # Gap is 30% >= 5%, so other gets HOLDING
        assert v_role == BatteryRole.HOLDING

    async def test_lower_soc_gets_holding_when_gap_large(self):
        coord, _, _ = _make_coordinator()
        h_role, v_role = coord._assign_discharge_roles(40.0, 80.0)
        assert v_role == BatteryRole.PRIMARY_DISCHARGE
        assert h_role == BatteryRole.HOLDING

    async def test_both_discharge_when_gap_small(self):
        coord, _, _ = _make_coordinator()
        h_role, v_role = coord._assign_discharge_roles(52.0, 50.0)
        # Gap 2% < 5% → both discharge
        assert h_role == BatteryRole.PRIMARY_DISCHARGE
        assert v_role == BatteryRole.SECONDARY_DISCHARGE

    async def test_swap_hysteresis_prevents_flapping(self):
        """Current PRIMARY keeps role unless challenger exceeds +3%."""
        coord, _, _ = _make_coordinator()
        # First call: Huawei is higher → PRIMARY
        coord._assign_discharge_roles(60.0, 50.0)
        # Now Victron is slightly higher but not by 3% → Huawei stays PRIMARY
        h_role, v_role = coord._assign_discharge_roles(58.0, 59.0)
        assert h_role == BatteryRole.PRIMARY_DISCHARGE

    async def test_swap_hysteresis_allows_large_change(self):
        """Challenger exceeds +3% → swap happens."""
        coord, _, _ = _make_coordinator()
        coord._assign_discharge_roles(60.0, 50.0)
        # Now Victron is much higher: 65 vs 58 → gap is 7, definitely > 3%
        h_role, v_role = coord._assign_discharge_roles(58.0, 65.0)
        assert v_role == BatteryRole.PRIMARY_DISCHARGE

    async def test_both_below_min_soc_gets_holding_in_run_cycle(self):
        """Both below min SoC: _run_cycle overrides roles to HOLDING."""
        coord, h_ctrl, v_ctrl = _make_coordinator()
        # Both below min SoC (huawei_min=10, victron_min=15)
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=5.0, grid_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=10.0, grid_power_w=500.0))

        await coord._run_cycle()

        # Both should get HOLDING commands
        h_cmd = h_ctrl.execute.call_args[0][0]
        v_cmd = v_ctrl.execute.call_args[0][0]
        assert h_cmd.role == BatteryRole.HOLDING
        assert v_cmd.role == BatteryRole.HOLDING


# ===========================================================================
# Allocation
# ===========================================================================


class TestAllocation:
    """P_target allocation to PRIMARY / SECONDARY systems."""

    async def test_primary_gets_full_target(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate(
            p_target=2000.0,
            h_role=BatteryRole.PRIMARY_DISCHARGE,
            v_role=BatteryRole.HOLDING,
            h_snap=_snap(soc=70.0),
            v_snap=_snap(soc=40.0),
        )
        # Primary (Huawei) gets full -2000 (discharge), Victron gets 0
        assert h_w == -2000.0
        assert v_w == 0.0

    async def test_secondary_splits_proportionally(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate(
            p_target=3000.0,
            h_role=BatteryRole.PRIMARY_DISCHARGE,
            v_role=BatteryRole.SECONDARY_DISCHARGE,
            h_snap=_snap(soc=50.0),
            v_snap=_snap(soc=50.0),
        )
        # Both available, equal SoC → split by capacity ratio (30/94, 64/94)
        total = abs(h_w) + abs(v_w)
        assert abs(total - 3000.0) < 1.0

    async def test_unavailable_system_gets_zero(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate(
            p_target=2000.0,
            h_role=BatteryRole.PRIMARY_DISCHARGE,
            v_role=BatteryRole.HOLDING,
            h_snap=_snap(soc=50.0, available=False),
            v_snap=_snap(soc=50.0),
        )
        # Huawei unavailable → Victron gets it all
        assert h_w == 0.0
        assert abs(v_w) == 2000.0


# ===========================================================================
# PV surplus routing (D-03, D-04)
# ===========================================================================


class TestPvSurplusRouting:
    """PV surplus charge routing — superseded by TestPvSurplusHeadroomWeighting.

    These tests verify headroom-weighted allocation behavior (OPT-01).
    The old Huawei-first logic was replaced by proportional SoC headroom
    weighting in Phase 3.
    """

    async def test_equal_soc_splits_evenly(self):
        """Equal SoC = equal headroom = equal split."""
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate_charge(
            surplus_w=3000.0,
            h_snap=_snap(soc=50.0, charge_headroom_w=5000.0),
            v_snap=_snap(soc=50.0, charge_headroom_w=8000.0),
        )
        # Equal SoC headroom → 50/50 split
        assert abs(h_w - 1500.0) < 1.0
        assert abs(v_w - 1500.0) < 1.0

    async def test_overflow_when_rate_limited(self):
        """Surplus clamped to charge rate, overflow to other."""
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate_charge(
            surplus_w=7000.0,
            h_snap=_snap(soc=50.0, charge_headroom_w=3000.0),
            v_snap=_snap(soc=50.0, charge_headroom_w=8000.0),
        )
        # Equal SoC: 3500 each by headroom, Huawei clamped to 3000
        # Overflow 500 routes to Victron: 3500 + 500 = 4000
        assert h_w == 3000.0
        assert v_w == 4000.0

    async def test_full_soc_routes_to_other(self):
        """Battery at 95% SoC routes all surplus to the other (D-04)."""
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate_charge(
            surplus_w=3000.0,
            h_snap=_snap(soc=96.0, charge_headroom_w=0.0),
            v_snap=_snap(soc=50.0, charge_headroom_w=8000.0),
        )
        assert h_w == 0.0
        assert v_w == 3000.0


# ===========================================================================
# Hysteresis (CTRL-03, D-06)
# ===========================================================================


class TestHysteresis:
    """Per-system dead-band hysteresis."""

    async def test_huawei_small_change_suppressed(self):
        coord, _, _ = _make_coordinator()
        coord._last_huawei_cmd_w = -1000.0
        result = coord._apply_hysteresis(-1200.0, "huawei")
        # Delta 200 < deadband 300 → suppressed, returns previous
        assert result == -1000.0

    async def test_huawei_large_change_passes(self):
        coord, _, _ = _make_coordinator()
        coord._last_huawei_cmd_w = -1000.0
        result = coord._apply_hysteresis(-1500.0, "huawei")
        # Delta 500 > deadband 300 → passes through
        assert result == -1500.0

    async def test_victron_small_change_suppressed(self):
        coord, _, _ = _make_coordinator()
        coord._last_victron_cmd_w = -500.0
        result = coord._apply_hysteresis(-600.0, "victron")
        # Delta 100 < deadband 150 → suppressed
        assert result == -500.0

    async def test_victron_large_change_passes(self):
        coord, _, _ = _make_coordinator()
        coord._last_victron_cmd_w = -500.0
        result = coord._apply_hysteresis(-700.0, "victron")
        # Delta 200 > deadband 150 → passes through
        assert result == -700.0


# ===========================================================================
# Ramp limiting (CTRL-07)
# ===========================================================================


class TestRampLimiting:
    """Per-system ramp rate limiting."""

    async def test_huawei_ramp_limits_large_step(self):
        coord, _, _ = _make_coordinator()
        coord._last_huawei_cmd_w = 0.0
        result = coord._apply_ramp(-5000.0, "huawei")
        # Max ramp 2000 W/cycle → limited to -2000
        assert result == -2000.0

    async def test_victron_ramp_limits_large_step(self):
        coord, _, _ = _make_coordinator()
        coord._last_victron_cmd_w = 0.0
        result = coord._apply_ramp(-3000.0, "victron")
        # Max ramp 1000 W/cycle → limited to -1000
        assert result == -1000.0

    async def test_small_step_passes_through(self):
        coord, _, _ = _make_coordinator()
        coord._last_huawei_cmd_w = -1000.0
        result = coord._apply_ramp(-1500.0, "huawei")
        # Delta 500 < ramp 2000 → passes through
        assert result == -1500.0


# ===========================================================================
# Debounce (D-16)
# ===========================================================================


class TestDebounce:
    """Role transitions require 2 consecutive cycles."""

    async def test_role_not_committed_on_first_proposal(self):
        coord, _, _ = _make_coordinator()
        # Initially HOLDING; propose PRIMARY_DISCHARGE once
        result = coord._debounce_role("huawei", BatteryRole.PRIMARY_DISCHARGE)
        assert result == BatteryRole.HOLDING  # Not yet committed

    async def test_role_committed_after_two_cycles(self):
        coord, _, _ = _make_coordinator()
        coord._debounce_role("huawei", BatteryRole.PRIMARY_DISCHARGE)
        result = coord._debounce_role("huawei", BatteryRole.PRIMARY_DISCHARGE)
        assert result == BatteryRole.PRIMARY_DISCHARGE

    async def test_safe_state_bypasses_debounce(self):
        """HOLDING due to comms loss bypasses debounce — immediate."""
        coord, _, _ = _make_coordinator()
        # Set current role to PRIMARY_DISCHARGE
        coord._committed_roles["huawei"] = BatteryRole.PRIMARY_DISCHARGE
        result = coord._debounce_role(
            "huawei", BatteryRole.HOLDING, safe_state=True
        )
        assert result == BatteryRole.HOLDING

    async def test_debounce_resets_on_different_proposal(self):
        coord, _, _ = _make_coordinator()
        coord._debounce_role("huawei", BatteryRole.PRIMARY_DISCHARGE)
        # Switch to a different proposal → counter resets
        coord._debounce_role("huawei", BatteryRole.CHARGING)
        result = coord._debounce_role("huawei", BatteryRole.CHARGING)
        assert result == BatteryRole.CHARGING


# ===========================================================================
# Grid charge (D-08)
# ===========================================================================


class TestGridCharge:
    """Coordinator detects active charge slots and sends GRID_CHARGE commands."""

    async def test_grid_charge_sends_commands(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        # Mock scheduler with an active slot
        slot = MagicMock()
        slot.battery = "huawei"
        slot.grid_charge_power_w = 3000
        slot.target_soc_pct = 90.0
        coord._check_grid_charge = MagicMock(return_value=slot)

        h_snap = _snap(soc=50.0)
        v_snap = _snap(soc=50.0)

        h_cmd, v_cmd = coord._compute_grid_charge_commands(slot, h_snap, v_snap)
        assert h_cmd.role == BatteryRole.GRID_CHARGE
        assert h_cmd.target_watts == 3000

    async def test_grid_charge_cleanup_on_slot_exit(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        coord._grid_charge_was_active = True
        h_cmd, v_cmd = coord._compute_grid_charge_cleanup()
        assert h_cmd.role == BatteryRole.HOLDING
        assert h_cmd.target_watts == 0
        assert v_cmd.role == BatteryRole.HOLDING
        assert v_cmd.target_watts == 0


# ===========================================================================
# EVCC hold (D-07)
# ===========================================================================


class TestEvccHold:
    """EVCC batteryMode=hold sets evcc_hold=True on all commands."""

    async def test_evcc_hold_flag_propagates(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        coord._evcc_battery_mode = "hold"

        h_snap = _snap(soc=50.0, grid_power_w=1000.0)
        v_snap = _snap(soc=50.0, grid_power_w=1000.0)
        h_ctrl.poll = AsyncMock(return_value=h_snap)
        v_ctrl.poll = AsyncMock(return_value=v_snap)

        await coord._run_cycle()

        # Both execute calls should have evcc_hold=True
        for call in h_ctrl.execute.call_args_list:
            cmd = call[0][0]
            assert cmd.evcc_hold is True
        for call in v_ctrl.execute.call_args_list:
            cmd = call[0][0]
            assert cmd.evcc_hold is True


# ===========================================================================
# Failover (D-10, CTRL-05)
# ===========================================================================


class TestFailover:
    """When one system goes offline, full P_target routes to the survivor."""

    async def test_huawei_offline_victron_gets_all(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate(
            p_target=3000.0,
            h_role=BatteryRole.PRIMARY_DISCHARGE,
            v_role=BatteryRole.SECONDARY_DISCHARGE,
            h_snap=_snap(soc=50.0, available=False),
            v_snap=_snap(soc=50.0),
        )
        assert h_w == 0.0
        assert abs(v_w) == 3000.0

    async def test_victron_offline_huawei_gets_all(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate(
            p_target=3000.0,
            h_role=BatteryRole.PRIMARY_DISCHARGE,
            v_role=BatteryRole.SECONDARY_DISCHARGE,
            h_snap=_snap(soc=50.0),
            v_snap=_snap(soc=50.0, available=False),
        )
        assert abs(h_w) == 3000.0
        assert v_w == 0.0


# ===========================================================================
# State building
# ===========================================================================


class TestStateBuilding:
    """CoordinatorState construction with backward-compat fields."""

    async def test_build_state_has_backward_compat_fields(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        h_snap = _snap(soc=80.0, charge_headroom_w=2000.0)
        v_snap = _snap(soc=60.0, charge_headroom_w=5000.0)
        h_cmd = ControllerCommand(
            role=BatteryRole.PRIMARY_DISCHARGE, target_watts=-1500.0
        )
        v_cmd = ControllerCommand(
            role=BatteryRole.HOLDING, target_watts=0.0
        )

        state = coord._build_state(h_snap, v_snap, h_cmd, v_cmd)
        assert isinstance(state, CoordinatorState)
        # Weighted SoC: (80*30 + 60*64) / 94
        expected_soc = (80.0 * 30 + 60.0 * 64) / 94.0
        assert abs(state.combined_soc_pct - expected_soc) < 0.1
        assert state.huawei_soc_pct == 80.0
        assert state.victron_soc_pct == 60.0
        assert state.huawei_available is True
        assert state.victron_available is True

    async def test_build_state_has_per_system_roles(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        h_snap = _snap(soc=80.0)
        v_snap = _snap(soc=60.0)
        h_cmd = ControllerCommand(
            role=BatteryRole.PRIMARY_DISCHARGE, target_watts=-1500.0
        )
        v_cmd = ControllerCommand(
            role=BatteryRole.HOLDING, target_watts=0.0
        )
        state = coord._build_state(h_snap, v_snap, h_cmd, v_cmd)
        assert state.huawei_role == "PRIMARY_DISCHARGE"
        assert state.victron_role == "HOLDING"

    async def test_pool_status_normal_when_both_available(self):
        coord, _, _ = _make_coordinator()
        h_snap = _snap(soc=50.0, available=True)
        v_snap = _snap(soc=50.0, available=True)
        h_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        v_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        state = coord._build_state(h_snap, v_snap, h_cmd, v_cmd)
        assert state.pool_status == "NORMAL"

    async def test_pool_status_degraded_when_one_offline(self):
        coord, _, _ = _make_coordinator()
        h_snap = _snap(soc=50.0, available=True)
        v_snap = _snap(soc=50.0, available=False)
        h_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        v_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        state = coord._build_state(h_snap, v_snap, h_cmd, v_cmd)
        assert state.pool_status == "DEGRADED"

    async def test_pool_status_offline_when_both_offline(self):
        coord, _, _ = _make_coordinator()
        h_snap = _snap(soc=0.0, available=False)
        v_snap = _snap(soc=0.0, available=False)
        h_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        v_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        state = coord._build_state(h_snap, v_snap, h_cmd, v_cmd)
        assert state.pool_status == "OFFLINE"


# ===========================================================================
# Control loop integration
# ===========================================================================


class TestControlLoop:
    """Integration: _run_cycle polls both controllers and sends commands."""

    async def test_run_cycle_polls_and_executes(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        h_ctrl.poll = AsyncMock(return_value=_snap(
            soc=70.0, grid_power_w=None, master_active_power_w=None,
        ))
        v_ctrl.poll = AsyncMock(return_value=_snap(
            soc=50.0, grid_power_w=1000.0,
        ))

        await coord._run_cycle()

        h_ctrl.poll.assert_called_once()
        v_ctrl.poll.assert_called_once()
        h_ctrl.execute.assert_called_once()
        v_ctrl.execute.assert_called_once()

    async def test_get_state_returns_none_before_first_cycle(self):
        coord, _, _ = _make_coordinator()
        assert coord.get_state() is None

    async def test_get_state_returns_state_after_cycle(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=70.0))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=500.0))

        await coord._run_cycle()

        state = coord.get_state()
        assert state is not None
        assert isinstance(state, CoordinatorState)

    async def test_start_stop_lifecycle(self):
        # asyncio.create_task requires asyncio event loop — skip on trio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pytest.skip("requires asyncio event loop")

        coord, h_ctrl, v_ctrl = _make_coordinator(
            orch_config=OrchestratorConfig(loop_interval_s=0.05),
        )
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=70.0))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=0.0))

        await coord.start()
        await asyncio.sleep(0.15)
        await coord.stop()

        # Should have run at least 2 cycles
        assert h_ctrl.poll.call_count >= 2


# ===========================================================================
# Setter methods (same interface as Orchestrator)
# ===========================================================================


class TestSetters:
    """Coordinator has set_scheduler, set_evcc_monitor, set_notifier."""

    def test_set_scheduler(self):
        coord, _, _ = _make_coordinator()
        sched = MagicMock()
        coord.set_scheduler(sched)
        assert coord._scheduler is sched

    def test_set_evcc_monitor(self):
        coord, _, _ = _make_coordinator()
        evcc = MagicMock()
        coord.set_evcc_monitor(evcc)
        assert coord._evcc_monitor is evcc

    def test_set_notifier(self):
        coord, _, _ = _make_coordinator()
        notifier = MagicMock()
        coord.set_notifier(notifier)
        assert coord._notifier is notifier

    async def test_sys_config_property_update(self):
        coord, _, _ = _make_coordinator()
        new_config = SystemConfig(huawei_min_soc_pct=20.0)
        coord.sys_config = new_config
        assert coord._sys_config.huawei_min_soc_pct == 20.0


# ===========================================================================
# CTRL-02 verification: coordinator NEVER calls driver methods
# ===========================================================================


class TestCtrl02NoDriverAccess:
    """Coordinator only uses controller.poll() and controller.execute()."""

    async def test_run_cycle_only_uses_poll_and_execute(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=70.0))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=500.0))

        await coord._run_cycle()

        # Verify only poll and execute were called on controllers
        h_called = {name for name, _, _ in h_ctrl.method_calls}
        v_called = {name for name, _, _ in v_ctrl.method_calls}
        allowed = {"poll", "execute"}
        assert h_called.issubset(allowed), f"Unexpected Huawei calls: {h_called - allowed}"
        assert v_called.issubset(allowed), f"Unexpected Victron calls: {v_called - allowed}"


class TestGetDeviceSnapshot:
    """Test Coordinator.get_device_snapshot() with real ControllerSnapshot objects."""

    def _make_coordinator(self):
        h_ctrl = AsyncMock()
        v_ctrl = AsyncMock()
        sys_cfg = SystemConfig()
        orch_cfg = OrchestratorConfig()
        coord = Coordinator(h_ctrl, v_ctrl, sys_cfg, orch_cfg)
        return coord

    def test_device_snapshot_with_available_huawei(self):
        coord = self._make_coordinator()
        h_snap = ControllerSnapshot(
            soc_pct=75.0,
            power_w=-3000.0,
            available=True,
            role=BatteryRole.PRIMARY_DISCHARGE,
            consecutive_failures=0,
            timestamp=time.monotonic(),
            max_charge_power_w=5000,
            max_discharge_power_w=5000,
        )
        v_snap = ControllerSnapshot(
            soc_pct=60.0,
            power_w=1000.0,
            available=True,
            role=BatteryRole.HOLDING,
            consecutive_failures=0,
            timestamp=time.monotonic(),
        )
        coord._last_h_snap = h_snap
        coord._last_v_snap = v_snap
        result = coord.get_device_snapshot()
        assert result["huawei"]["available"] is True
        assert result["huawei"]["total_power_w"] == -3000
        assert result["huawei"]["max_charge_w"] == 5000
        assert result["huawei"]["max_discharge_w"] == 5000
        assert result["victron"]["available"] is True
        assert result["victron"]["battery_power_w"] == 1000.0

    def test_device_snapshot_with_none_max_power(self):
        coord = self._make_coordinator()
        h_snap = ControllerSnapshot(
            soc_pct=50.0,
            power_w=0.0,
            available=True,
            role=BatteryRole.HOLDING,
            consecutive_failures=0,
            timestamp=time.monotonic(),
            max_charge_power_w=None,
            max_discharge_power_w=None,
        )
        coord._last_h_snap = h_snap
        coord._last_v_snap = None
        result = coord.get_device_snapshot()
        assert result["huawei"]["max_charge_w"] == 0
        assert result["huawei"]["max_discharge_w"] == 0
        assert result["victron"]["available"] is False


# ===========================================================================
# PV surplus headroom weighting (OPT-01) — replaces Huawei-first
# ===========================================================================


class TestPvSurplusHeadroomWeighting:
    """OPT-01: PV surplus weighted by SoC headroom, not Huawei-first."""

    async def test_equal_soc_equal_split(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate_charge(
            surplus_w=4000.0,
            h_snap=_snap(soc=50.0, charge_headroom_w=5000.0),
            v_snap=_snap(soc=50.0, charge_headroom_w=8000.0),
        )
        assert abs(h_w - 2000.0) < 1.0
        assert abs(v_w - 2000.0) < 1.0

    async def test_lower_soc_gets_more(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate_charge(
            surplus_w=3000.0,
            h_snap=_snap(soc=80.0, charge_headroom_w=5000.0),
            v_snap=_snap(soc=50.0, charge_headroom_w=8000.0),
        )
        # Huawei headroom=15, Victron headroom=45, total=60
        # h_share = 3000*(15/60) = 750, v_share = 3000*(45/60) = 2250
        assert v_w > h_w

    async def test_both_full_returns_zero(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate_charge(
            surplus_w=3000.0,
            h_snap=_snap(soc=96.0, charge_headroom_w=0.0),
            v_snap=_snap(soc=96.0, charge_headroom_w=0.0),
        )
        assert h_w == 0.0
        assert v_w == 0.0

    async def test_overflow_routing_when_rate_limited(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate_charge(
            surplus_w=6000.0,
            h_snap=_snap(soc=50.0, charge_headroom_w=2000.0),
            v_snap=_snap(soc=50.0, charge_headroom_w=8000.0),
        )
        assert h_w == 2000.0
        assert v_w == 4000.0

    async def test_one_at_max_soc_all_to_other(self):
        coord, _, _ = _make_coordinator()
        h_w, v_w = coord._allocate_charge(
            surplus_w=3000.0,
            h_snap=_snap(soc=96.0, charge_headroom_w=0.0),
            v_snap=_snap(soc=50.0, charge_headroom_w=8000.0),
        )
        assert h_w == 0.0
        assert v_w == 3000.0


# ===========================================================================
# Min-SoC profiles (OPT-05)
# ===========================================================================


class TestMinSocProfiles:
    """OPT-05: Time-of-day min-SoC profiles with wraparound."""

    def test_normal_window_matches(self):
        from backend.config import MinSocWindow
        coord, _, _ = _make_coordinator(
            sys_config=SystemConfig(
                huawei_min_soc_profile=[
                    MinSocWindow(6, 16, 30.0),
                    MinSocWindow(16, 22, 20.0),
                ],
            ),
        )
        from datetime import datetime
        now = datetime(2026, 3, 22, 10, 0)  # 10:00 -> matches (6,16,30)
        result = coord._get_effective_min_soc("huawei", now)
        assert result == 30.0

    def test_wrapping_window_matches_late_night(self):
        from backend.config import MinSocWindow
        coord, _, _ = _make_coordinator(
            sys_config=SystemConfig(
                huawei_min_soc_profile=[MinSocWindow(22, 6, 10.0)],
            ),
        )
        from datetime import datetime
        now = datetime(2026, 3, 22, 23, 0)  # 23:00 -> matches (22,6,10)
        result = coord._get_effective_min_soc("huawei", now)
        assert result == 10.0

    def test_wrapping_window_matches_early_morning(self):
        from backend.config import MinSocWindow
        coord, _, _ = _make_coordinator(
            sys_config=SystemConfig(
                huawei_min_soc_profile=[MinSocWindow(22, 6, 10.0)],
            ),
        )
        from datetime import datetime
        now = datetime(2026, 3, 22, 2, 0)  # 02:00 -> matches (22,6,10)
        result = coord._get_effective_min_soc("huawei", now)
        assert result == 10.0

    def test_no_profile_returns_static(self):
        coord, _, _ = _make_coordinator(
            sys_config=SystemConfig(huawei_min_soc_pct=12.0),
        )
        from datetime import datetime
        now = datetime(2026, 3, 22, 10, 0)
        result = coord._get_effective_min_soc("huawei", now)
        assert result == 12.0

    def test_profile_gap_returns_static(self):
        from backend.config import MinSocWindow
        coord, _, _ = _make_coordinator(
            sys_config=SystemConfig(
                huawei_min_soc_pct=10.0,
                huawei_min_soc_profile=[
                    MinSocWindow(6, 16, 30.0),
                    MinSocWindow(16, 22, 20.0),
                ],
            ),
        )
        from datetime import datetime
        now = datetime(2026, 3, 22, 23, 0)  # 23:00 -> gap -> static 10.0
        result = coord._get_effective_min_soc("huawei", now)
        assert result == 10.0

    async def test_run_cycle_uses_effective_min_soc(self):
        from backend.config import MinSocWindow
        coord, h_ctrl, v_ctrl = _make_coordinator(
            sys_config=SystemConfig(
                huawei_min_soc_pct=10.0,
                huawei_min_soc_profile=[MinSocWindow(0, 24, 40.0)],
                victron_min_soc_pct=15.0,
                victron_min_soc_profile=[MinSocWindow(0, 24, 40.0)],
            ),
        )
        # Both at 35% SoC -- above static min but below profile min (40%)
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=35.0, grid_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=35.0, grid_power_w=500.0))

        await coord._run_cycle()

        h_cmd = h_ctrl.execute.call_args[0][0]
        v_cmd = v_ctrl.execute.call_args[0][0]
        # With profile min=40%, both at 35% should be below min -> HOLDING
        assert h_cmd.role == BatteryRole.HOLDING
        assert v_cmd.role == BatteryRole.HOLDING


# ===========================================================================
# Grid charge staggering (OPT-02, OPT-03)
# ===========================================================================


class TestGridChargeStaggering:
    """OPT-02/OPT-03: Both batteries charge in parallel; Huawei finishes first."""

    async def test_both_charge_simultaneously(self):
        coord, _, _ = _make_coordinator()
        slot = MagicMock()
        slot.battery = "huawei"
        slot.grid_charge_power_w = 5000
        slot.target_soc_pct = 90.0

        h_snap = _snap(soc=50.0)
        v_snap = _snap(soc=50.0)
        h_cmd, v_cmd = coord._compute_grid_charge_commands(slot, h_snap, v_snap)
        assert h_cmd.role == BatteryRole.GRID_CHARGE
        assert h_cmd.target_watts == 5000

    async def test_huawei_target_met_redirects_to_victron(self):
        coord, _, _ = _make_coordinator()
        slot = MagicMock()
        slot.battery = "huawei"
        slot.grid_charge_power_w = 5000
        slot.target_soc_pct = 90.0

        h_snap = _snap(soc=92.0)  # Above target
        v_snap = _snap(soc=50.0)
        h_cmd, v_cmd = coord._compute_grid_charge_commands(slot, h_snap, v_snap)
        # Huawei target met -> power redirects to Victron
        assert h_cmd.target_watts == 0
        assert v_cmd.role == BatteryRole.GRID_CHARGE
        assert v_cmd.target_watts == 5000

    async def test_both_at_target_produces_valid_commands(self):
        coord, _, _ = _make_coordinator()
        slot = MagicMock()
        slot.battery = "victron"
        slot.grid_charge_power_w = 3000
        slot.target_soc_pct = 90.0

        h_snap = _snap(soc=92.0)
        v_snap = _snap(soc=92.0)
        h_cmd, v_cmd = coord._compute_grid_charge_commands(slot, h_snap, v_snap)
        assert isinstance(h_cmd, ControllerCommand)
        assert isinstance(v_cmd, ControllerCommand)
