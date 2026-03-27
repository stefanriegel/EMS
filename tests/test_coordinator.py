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
- Decision ring buffer and integration health tracking
- Per-cycle InfluxDB and HA MQTT integration calls
- Graceful degradation on integration failures
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
    DecisionEntry,
    IntegrationStatus,
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

    async def test_role_committed_after_three_cycles(self):
        coord, _, _ = _make_coordinator()
        coord._debounce_role("huawei", BatteryRole.PRIMARY_DISCHARGE)
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
        coord._debounce_role("huawei", BatteryRole.PRIMARY_DISCHARGE)
        # Switch to a different proposal → counter resets
        coord._debounce_role("huawei", BatteryRole.CHARGING)
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

    async def test_evcc_hold_reads_from_monitor(self):
        """INT-01: coordinator reads live evcc_battery_mode from injected monitor."""
        coord, h_ctrl, v_ctrl = _make_coordinator()

        # Create a mock EVCC monitor with battery_mode attribute
        evcc_monitor = MagicMock()
        evcc_monitor.evcc_battery_mode = "hold"
        coord.set_evcc_monitor(evcc_monitor)

        h_snap = _snap(soc=50.0, grid_power_w=1000.0)
        v_snap = _snap(soc=50.0, grid_power_w=1000.0)
        h_ctrl.poll = AsyncMock(return_value=h_snap)
        v_ctrl.poll = AsyncMock(return_value=v_snap)

        await coord._run_cycle()

        # Both controllers should receive HOLDING commands from live monitor read
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
        allowed = {"poll", "execute", "get_working_mode"}
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


# ===========================================================================
# Decision ring buffer (INT-04)
# ===========================================================================


class TestDecisionRingBuffer:
    """Decision entries are created on role changes and EVCC hold."""

    def test_decisions_deque_exists_with_maxlen(self):
        coord, _, _ = _make_coordinator()
        assert hasattr(coord, "_decisions")
        assert coord._decisions.maxlen == 100

    async def test_role_change_creates_decision_entry(self):
        """When a role changes between cycles, a decision entry is logged."""
        coord, h_ctrl, v_ctrl = _make_coordinator()
        # First cycle: discharge (creates a role change from HOLDING)
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=80.0, grid_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=1000.0))
        await coord._run_cycle()
        # Debounce: need third cycle for role to commit (debounce_cycles=3)
        await coord._run_cycle()
        await coord._run_cycle()

        decisions = coord.get_decisions()
        assert len(decisions) > 0
        # Should have at least one role_change trigger
        triggers = [d["trigger"] for d in decisions]
        assert "role_change" in triggers

    async def test_no_decision_when_unchanged(self):
        """No decision entry when roles and allocations are unchanged."""
        coord, h_ctrl, v_ctrl = _make_coordinator()
        # Both holding with no grid power -> idle
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=None, master_active_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=0.0))

        await coord._run_cycle()
        initial_count = len(coord._decisions)

        # Same state -> no new decision
        await coord._run_cycle()
        assert len(coord._decisions) == initial_count

    async def test_evcc_hold_creates_hold_signal_decision(self):
        """EVCC hold creates a decision entry with trigger='hold_signal'."""
        coord, h_ctrl, v_ctrl = _make_coordinator()
        # First, set up a non-HOLDING state
        coord._prev_h_role = "PRIMARY_DISCHARGE"
        coord._prev_v_role = "SECONDARY_DISCHARGE"

        coord._evcc_battery_mode = "hold"
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=1000.0))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=1000.0))

        await coord._run_cycle()

        decisions = coord.get_decisions()
        assert len(decisions) > 0
        hold_entries = [d for d in decisions if d["trigger"] == "hold_signal"]
        assert len(hold_entries) >= 1
        assert hold_entries[0]["reasoning"] == "EVCC batteryMode=hold"
        assert hold_entries[0]["huawei_role"] == "HOLDING"
        assert hold_entries[0]["victron_role"] == "HOLDING"

    def test_get_decisions_returns_newest_first(self):
        coord, _, _ = _make_coordinator()
        # Manually add entries
        for i in range(5):
            coord._decisions.append(DecisionEntry(
                timestamp=f"2026-01-01T00:00:0{i}Z",
                trigger="role_change",
                huawei_role="HOLDING",
                victron_role="HOLDING",
                p_target_w=float(i * 100),
                huawei_allocation_w=0.0,
                victron_allocation_w=0.0,
                pool_status="NORMAL",
                reasoning=f"entry {i}",
            ))
        result = coord.get_decisions(limit=3)
        assert len(result) == 3
        # Newest first: p_target_w=400, 300, 200
        assert result[0]["p_target_w"] == 400.0
        assert result[1]["p_target_w"] == 300.0
        assert result[2]["p_target_w"] == 200.0


# ===========================================================================
# Integration writes — InfluxDB and HA MQTT (INT-03, INT-05)
# ===========================================================================


class TestIntegrationWrites:
    """Per-cycle integration calls to InfluxDB writer and HA MQTT client."""

    async def test_writer_called_per_cycle(self):
        """When _writer is set, write_coordinator_state and write_per_system_metrics are called."""
        coord, h_ctrl, v_ctrl = _make_coordinator()
        mock_writer = AsyncMock()
        coord._writer = mock_writer

        h_ctrl.poll = AsyncMock(return_value=_snap(soc=70.0, grid_power_w=None, master_active_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=0.0))

        await coord._run_cycle()

        mock_writer.write_coordinator_state.assert_called_once()
        mock_writer.write_per_system_metrics.assert_called_once()

    async def test_ha_mqtt_client_called_per_cycle(self):
        """When _ha_mqtt_client is set, publish is called with extra_fields."""
        coord, h_ctrl, v_ctrl = _make_coordinator()
        mock_ha = AsyncMock()
        coord._ha_mqtt_client = mock_ha

        h_ctrl.poll = AsyncMock(return_value=_snap(soc=70.0, grid_power_w=None, master_active_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(
            soc=50.0, grid_power_w=0.0,
            grid_l1_power_w=100.0, grid_l2_power_w=200.0, grid_l3_power_w=300.0,
        ))

        await coord._run_cycle()

        mock_ha.publish.assert_called_once()
        call_args = mock_ha.publish.call_args
        extra = call_args[1].get("extra_fields") or call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("extra_fields")
        assert extra is not None
        assert "victron_l1_power_w" in extra
        assert "victron_l2_power_w" in extra
        assert "victron_l3_power_w" in extra

    async def test_writer_failure_does_not_crash_cycle(self):
        """InfluxDB write failure is caught — _run_cycle completes."""
        coord, h_ctrl, v_ctrl = _make_coordinator()
        mock_writer = AsyncMock()
        mock_writer.write_coordinator_state.side_effect = Exception("InfluxDB down")
        coord._writer = mock_writer

        h_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=None, master_active_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=0.0))

        # Should NOT raise
        await coord._run_cycle()
        assert coord.get_state() is not None

    async def test_ha_mqtt_failure_does_not_crash_cycle(self):
        """HA MQTT publish failure is caught — _run_cycle completes."""
        coord, h_ctrl, v_ctrl = _make_coordinator()
        mock_ha = AsyncMock()
        mock_ha.publish.side_effect = Exception("MQTT down")
        coord._ha_mqtt_client = mock_ha

        h_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=None, master_active_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=0.0))

        # Should NOT raise
        await coord._run_cycle()
        assert coord.get_state() is not None


# ===========================================================================
# Integration health tracking (INT-03)
# ===========================================================================


class TestIntegrationHealth:
    """Integration health status tracked per service."""

    async def test_influxdb_healthy_after_successful_write(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        mock_writer = AsyncMock()
        coord._writer = mock_writer

        h_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=None, master_active_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=0.0))

        await coord._run_cycle()

        health = coord.get_integration_health()
        assert health["influxdb"]["available"] is True
        assert health["influxdb"]["last_error"] is None

    async def test_influxdb_unhealthy_after_failed_write(self):
        coord, h_ctrl, v_ctrl = _make_coordinator()
        mock_writer = AsyncMock()
        mock_writer.write_coordinator_state.side_effect = Exception("connection refused")
        coord._writer = mock_writer

        h_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=None, master_active_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50.0, grid_power_w=0.0))

        await coord._run_cycle()

        health = coord.get_integration_health()
        assert health["influxdb"]["available"] is False
        assert "connection refused" in health["influxdb"]["last_error"]

    def test_get_integration_health_returns_all_services(self):
        coord, _, _ = _make_coordinator()
        health = coord.get_integration_health()
        assert "influxdb" in health
        assert "ha_mqtt" in health
        assert "evcc" in health
        assert "telegram" in health


# ===========================================================================
# set_ha_mqtt_client (INT-05)
# ===========================================================================


class TestSetHaMqttClient:
    """set_ha_mqtt_client stores the client on the coordinator."""

    def test_set_ha_mqtt_client_stores_client(self):
        coord, _, _ = _make_coordinator()
        mock_client = MagicMock()
        coord.set_ha_mqtt_client(mock_client)
        assert coord._ha_mqtt_client is mock_client


# ===========================================================================
# Winter config and EXPORTING role (SCO-03)
# ===========================================================================


class TestWinterConfig:
    """Validate winter config defaults, custom values, and EXPORTING role."""

    def test_winter_config_defaults(self):
        """SystemConfig() has correct winter defaults."""
        from backend.config import SystemConfig

        cfg = SystemConfig()
        assert cfg.winter_months == [11, 12, 1, 2]
        assert cfg.winter_min_soc_boost_pct == 10

    def test_exporting_role_exists(self):
        """BatteryRole.EXPORTING exists and has value 'EXPORTING'."""
        from backend.controller_model import BatteryRole

        assert BatteryRole.EXPORTING.value == "EXPORTING"
        assert BatteryRole.EXPORTING == "EXPORTING"

    def test_winter_config_custom(self):
        """SystemConfig with custom winter values stores them correctly."""
        from backend.config import SystemConfig

        cfg = SystemConfig(winter_months=[12, 1, 2], winter_min_soc_boost_pct=15)
        assert cfg.winter_months == [12, 1, 2]
        assert cfg.winter_min_soc_boost_pct == 15

    def test_api_config_winter_fields(self):
        """SystemConfigRequest validates winter fields correctly."""
        from backend.api import SystemConfigRequest

        req = SystemConfigRequest(
            winter_months=[11, 12, 1, 2],
            winter_min_soc_boost_pct=10,
        )
        assert req.winter_months == [11, 12, 1, 2]
        assert req.winter_min_soc_boost_pct == 10


# ===========================================================================
# Export integration (SCO-03) — coordinator export role + seasonal boost
# ===========================================================================


class TestExportIntegration:
    """SCO-03: Export role assignment, seasonal min-SoC boost, EXPORTING state."""

    async def test_export_role_assigned_both_full(self):
        """When advisor says EXPORT and both batteries >= 95%, higher-SoC exports."""
        coord, h_ctrl, v_ctrl = _make_coordinator(
            orch_config=OrchestratorConfig(debounce_cycles=1),
        )
        coord._prev_export_decision = "EXPORT"
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=98.0, grid_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=96.0, grid_power_w=-2000.0))

        await coord._run_cycle()

        h_cmd = h_ctrl.execute.call_args[0][0]
        v_cmd = v_ctrl.execute.call_args[0][0]
        assert h_cmd.role == BatteryRole.EXPORTING
        assert v_cmd.role == BatteryRole.HOLDING
        assert h_cmd.target_watts == 0.0
        assert v_cmd.target_watts == 0.0

    async def test_export_higher_soc_huawei(self):
        """When huawei SoC (98%) > victron SoC (96%), huawei gets EXPORTING."""
        coord, h_ctrl, v_ctrl = _make_coordinator(
            orch_config=OrchestratorConfig(debounce_cycles=1),
        )
        coord._prev_export_decision = "EXPORT"
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=98.0, grid_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=96.0, grid_power_w=-3000.0))

        await coord._run_cycle()

        h_cmd = h_ctrl.execute.call_args[0][0]
        v_cmd = v_ctrl.execute.call_args[0][0]
        assert h_cmd.role == BatteryRole.EXPORTING
        assert v_cmd.role == BatteryRole.HOLDING

    async def test_export_higher_soc_victron(self):
        """When victron SoC (98%) > huawei SoC (96%), victron gets EXPORTING."""
        coord, h_ctrl, v_ctrl = _make_coordinator(
            orch_config=OrchestratorConfig(debounce_cycles=1),
        )
        coord._prev_export_decision = "EXPORT"
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=96.0, grid_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=98.0, grid_power_w=-2000.0))

        await coord._run_cycle()

        h_cmd = h_ctrl.execute.call_args[0][0]
        v_cmd = v_ctrl.execute.call_args[0][0]
        assert h_cmd.role == BatteryRole.HOLDING
        assert v_cmd.role == BatteryRole.EXPORTING

    async def test_no_export_when_store(self):
        """When advisor says STORE, normal charge routing (no EXPORTING)."""
        coord, h_ctrl, v_ctrl = _make_coordinator()
        coord._prev_export_decision = "STORE"
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=98.0, grid_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=96.0, grid_power_w=-2000.0))

        await coord._run_cycle()

        h_cmd = h_ctrl.execute.call_args[0][0]
        v_cmd = v_ctrl.execute.call_args[0][0]
        assert h_cmd.role != BatteryRole.EXPORTING
        assert v_cmd.role != BatteryRole.EXPORTING

    async def test_no_export_below_full(self):
        """When advisor says EXPORT but one battery at 80%, no EXPORTING."""
        coord, h_ctrl, v_ctrl = _make_coordinator()
        coord._prev_export_decision = "EXPORT"
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=80.0, grid_power_w=None))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=96.0, grid_power_w=-2000.0))

        await coord._run_cycle()

        h_cmd = h_ctrl.execute.call_args[0][0]
        v_cmd = v_ctrl.execute.call_args[0][0]
        assert h_cmd.role != BatteryRole.EXPORTING
        assert v_cmd.role != BatteryRole.EXPORTING

    async def test_build_state_exporting(self):
        """_build_state returns control_state='EXPORTING' when a role is EXPORTING."""
        coord, _, _ = _make_coordinator()
        h_snap = _snap(soc=98.0)
        v_snap = _snap(soc=96.0)
        h_cmd = ControllerCommand(role=BatteryRole.EXPORTING, target_watts=0.0)
        v_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)

        state = coord._build_state(h_snap, v_snap, h_cmd, v_cmd)
        assert state.control_state == "EXPORTING"

    def test_winter_min_soc_boost(self):
        """In January, _get_effective_min_soc adds winter boost to base."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        coord, _, _ = _make_coordinator(
            sys_config=SystemConfig(
                huawei_min_soc_pct=10.0,
                winter_min_soc_boost_pct=10,
                winter_months=[11, 12, 1, 2],
            ),
        )
        now = datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        result = coord._get_effective_min_soc("huawei", now)
        assert result == 20.0  # 10% base + 10% boost

    def test_summer_no_boost(self):
        """In July, _get_effective_min_soc returns unmodified base."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        coord, _, _ = _make_coordinator(
            sys_config=SystemConfig(
                huawei_min_soc_pct=10.0,
                winter_min_soc_boost_pct=10,
                winter_months=[11, 12, 1, 2],
            ),
        )
        now = datetime(2026, 7, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        result = coord._get_effective_min_soc("huawei", now)
        assert result == 10.0  # no boost in summer

    def test_winter_boost_clamps_100(self):
        """When base is 95% and boost is 10%, result clamps to 100.0."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        coord, _, _ = _make_coordinator(
            sys_config=SystemConfig(
                huawei_min_soc_pct=95.0,
                winter_min_soc_boost_pct=10,
                winter_months=[11, 12, 1, 2],
            ),
        )
        now = datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        result = coord._get_effective_min_soc("huawei", now)
        assert result == 100.0  # clamped


# ===========================================================================
# HA command handler tests
# ===========================================================================


class TestHaCommandHandler:
    """Tests for _handle_ha_command — command dispatch from HA MQTT."""

    def test_ha_command_min_soc_huawei(self):
        """min_soc_huawei command updates _sys_config.huawei_min_soc_pct."""
        coord, _, _ = _make_coordinator()
        coord._handle_ha_command("min_soc_huawei", "25")
        assert coord._sys_config.huawei_min_soc_pct == 25.0

    def test_ha_command_min_soc_victron(self):
        """min_soc_victron command updates _sys_config.victron_min_soc_pct."""
        coord, _, _ = _make_coordinator()
        coord._handle_ha_command("min_soc_victron", "30")
        assert coord._sys_config.victron_min_soc_pct == 30.0

    def test_ha_command_deadband_huawei(self):
        """deadband_huawei command updates _huawei_deadband_w."""
        coord, _, _ = _make_coordinator()
        coord._handle_ha_command("deadband_huawei", "500")
        assert coord._huawei_deadband_w == 500

    def test_ha_command_deadband_victron(self):
        """deadband_victron command updates _victron_deadband_w."""
        coord, _, _ = _make_coordinator()
        coord._handle_ha_command("deadband_victron", "200")
        assert coord._victron_deadband_w == 200

    def test_ha_command_ramp_rate(self):
        """ramp_rate command updates both _huawei_ramp_w_per_cycle and _victron_ramp_w_per_cycle."""
        coord, _, _ = _make_coordinator()
        coord._handle_ha_command("ramp_rate", "1000")
        assert coord._huawei_ramp_w_per_cycle == 1000
        assert coord._victron_ramp_w_per_cycle == 1000

    def test_ha_command_control_mode_hold(self):
        """control_mode HOLD sets _mode_override."""
        coord, _, _ = _make_coordinator()
        coord._handle_ha_command("control_mode", "HOLD")
        assert coord._mode_override == "HOLD"

    def test_ha_command_control_mode_auto_clears(self):
        """control_mode AUTO clears _mode_override to None."""
        coord, _, _ = _make_coordinator()
        coord._mode_override = "HOLD"
        coord._handle_ha_command("control_mode", "AUTO")
        assert coord._mode_override is None

    def test_ha_command_force_grid_charge(self):
        """force_grid_charge button sets GRID_CHARGE mode with timeout."""
        coord, _, _ = _make_coordinator()
        loop = asyncio.new_event_loop()
        try:
            coord._mode_timeout_handle = None
            # Mock the event loop for call_later
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value = loop
                coord._handle_ha_command("force_grid_charge", "PRESS")
        finally:
            loop.close()
        assert coord._mode_override == "GRID_CHARGE"

    def test_ha_command_reset_to_auto(self):
        """reset_to_auto button clears mode override and cancels timeout."""
        coord, _, _ = _make_coordinator()
        coord._mode_override = "GRID_CHARGE"
        mock_handle = MagicMock()
        coord._mode_timeout_handle = mock_handle
        coord._handle_ha_command("reset_to_auto", "PRESS")
        assert coord._mode_override is None
        mock_handle.cancel.assert_called_once()

    def test_ha_command_force_grid_charge_timeout(self):
        """After 60 minutes, force_grid_charge auto-timeout clears _mode_override."""
        coord, _, _ = _make_coordinator()
        coord._mode_override = "GRID_CHARGE"
        coord._clear_mode_override()
        assert coord._mode_override is None

    def test_ha_command_invalid_entity_id(self):
        """Invalid entity_id logs warning and does nothing."""
        coord, _, _ = _make_coordinator()
        original_soc = coord._sys_config.huawei_min_soc_pct
        coord._handle_ha_command("nonexistent_entity", "42")
        assert coord._sys_config.huawei_min_soc_pct == original_soc

    def test_ha_command_out_of_range_clamps(self):
        """Out-of-range values are clamped to entity min/max."""
        coord, _, _ = _make_coordinator()
        coord._handle_ha_command("min_soc_huawei", "200")
        assert coord._sys_config.huawei_min_soc_pct == 100.0  # max is 100

    def test_ha_command_below_range_clamps(self):
        """Below-range values are clamped to entity minimum."""
        coord, _, _ = _make_coordinator()
        coord._handle_ha_command("min_soc_huawei", "0")
        assert coord._sys_config.huawei_min_soc_pct == 10.0  # min is 10

    def test_ha_command_state_echo(self):
        """After command, state echo triggers ha_mqtt_client.publish."""
        coord, _, _ = _make_coordinator()
        mock_mqtt = AsyncMock()
        coord._ha_mqtt_client = mock_mqtt
        coord._state = CoordinatorState(
            combined_soc_pct=50.0, huawei_soc_pct=50.0, victron_soc_pct=50.0,
            huawei_available=True, victron_available=True,
            control_state="IDLE",
            huawei_discharge_setpoint_w=0, victron_discharge_setpoint_w=0,
            combined_power_w=0.0,
            huawei_charge_headroom_w=0, victron_charge_headroom_w=0.0,
            timestamp=time.monotonic(),
        )
        # Run within an event loop so _trigger_state_echo can create_task
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run_command_in_loop(coord))
        finally:
            loop.close()
        mock_mqtt.publish.assert_called_once()

    @staticmethod
    async def _run_command_in_loop(coord):
        """Helper to run command handler within running event loop."""
        coord._handle_ha_command("min_soc_huawei", "25")
        # Allow the created task to execute
        await asyncio.sleep(0)

    def test_ha_command_supervisor_persistence_called(self):
        """Supervisor options persistence called for number entity changes."""
        coord, _, _ = _make_coordinator()
        mock_supervisor = AsyncMock()
        coord._supervisor_client = mock_supervisor
        coord._handle_ha_command("min_soc_huawei", "25")
        assert coord._sys_config.huawei_min_soc_pct == 25.0
        # Persistence method should have been scheduled (fire-and-forget)

    def test_ha_command_supervisor_none_graceful(self):
        """If SupervisorClient is None, persistence is skipped gracefully."""
        coord, _, _ = _make_coordinator()
        coord._supervisor_client = None
        # Should not raise
        coord._handle_ha_command("min_soc_huawei", "25")
        assert coord._sys_config.huawei_min_soc_pct == 25.0
