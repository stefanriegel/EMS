"""Unit tests for the CrossChargeDetector module.

Covers detection algorithm (threshold, debounce, opposing signs, grid check),
mitigation (force sink to HOLDING), episode tracking (start, waste accumulation,
cooldown reset), and CoordinatorState extension with cross-charge fields.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from backend.controller_model import (
    BatteryRole,
    ControllerCommand,
    ControllerSnapshot,
    CoordinatorState,
)
from backend.cross_charge import (
    CrossChargeDetector,
    CrossChargeEpisode,
    CrossChargeState,
)


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
    grid_l1_power_w: float | None = None,
    grid_l2_power_w: float | None = None,
    grid_l3_power_w: float | None = None,
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
        grid_l1_power_w=grid_l1_power_w,
        grid_l2_power_w=grid_l2_power_w,
        grid_l3_power_w=grid_l3_power_w,
    )


def _cmd(
    role: BatteryRole = BatteryRole.PRIMARY_DISCHARGE,
    target_watts: float = -1000.0,
) -> ControllerCommand:
    """Build a ControllerCommand with defaults."""
    return ControllerCommand(role=role, target_watts=target_watts)


# ---------------------------------------------------------------------------
# Detection — basic conditions
# ---------------------------------------------------------------------------


class TestDetectionBasic:
    """Tests for basic cross-charge detection conditions."""

    def test_no_detection_when_same_sign(self) -> None:
        """Both batteries charging (positive power_w) with low grid -> not detected."""
        det = CrossChargeDetector()
        h = _snap(power=500.0)
        v = _snap(power=400.0, grid_power_w=50.0)
        state = det.check(h, v)
        assert state.detected is False

    def test_no_detection_below_threshold(self) -> None:
        """One battery at +50W, other at -50W (below 100W threshold) -> not detected."""
        det = CrossChargeDetector()
        h = _snap(power=50.0)
        v = _snap(power=-50.0, grid_power_w=10.0)
        # Run two cycles to ensure debounce is not the issue
        det.check(h, v)
        state = det.check(h, v)
        assert state.detected is False

    def test_no_detection_high_grid(self) -> None:
        """Opposing signs but abs(grid_power) > 200W -> not detected (household load)."""
        det = CrossChargeDetector()
        h = _snap(power=-500.0)
        v = _snap(power=400.0, grid_power_w=300.0)
        det.check(h, v)
        state = det.check(h, v)
        assert state.detected is False


# ---------------------------------------------------------------------------
# Detection — debounce
# ---------------------------------------------------------------------------


class TestDebounce:
    """Tests for the 2-cycle debounce requirement."""

    def test_debounce_requires_two_cycles(self) -> None:
        """First cycle with cross-charge condition -> detected=False.
        Second cycle -> detected=True."""
        det = CrossChargeDetector()
        h = _snap(power=-500.0)
        v = _snap(power=400.0, grid_power_w=50.0)

        state1 = det.check(h, v)
        assert state1.detected is False
        assert state1.consecutive_cycles == 1

        state2 = det.check(h, v)
        assert state2.detected is True
        assert state2.consecutive_cycles == 2

    def test_debounce_resets_on_clear(self) -> None:
        """Two detection cycles then one clear cycle -> consecutive resets to 0."""
        det = CrossChargeDetector()
        h_xc = _snap(power=-500.0)
        v_xc = _snap(power=400.0, grid_power_w=50.0)
        h_ok = _snap(power=0.0)
        v_ok = _snap(power=0.0, grid_power_w=50.0)

        det.check(h_xc, v_xc)
        det.check(h_xc, v_xc)  # detected=True

        state = det.check(h_ok, v_ok)
        assert state.detected is False
        assert state.consecutive_cycles == 0


# ---------------------------------------------------------------------------
# Detection — source/sink identification
# ---------------------------------------------------------------------------


class TestSourceSink:
    """Tests for identifying which battery is source vs sink."""

    def test_detection_identifies_source_and_sink(self) -> None:
        """h_power=-500 (discharging), v_power=+400 (charging), grid~0
        -> source=huawei, sink=victron."""
        det = CrossChargeDetector()
        h = _snap(power=-500.0)
        v = _snap(power=400.0, grid_power_w=20.0)

        det.check(h, v)
        state = det.check(h, v)

        assert state.detected is True
        assert state.source_system == "huawei"
        assert state.sink_system == "victron"
        assert state.source_power_w == 500.0
        assert state.sink_power_w == 400.0

    def test_detection_reverse_direction(self) -> None:
        """h_power=+300 (charging), v_power=-400 (discharging), grid~0
        -> source=victron, sink=huawei."""
        det = CrossChargeDetector()
        h = _snap(power=300.0)
        v = _snap(power=-400.0, grid_power_w=10.0)

        det.check(h, v)
        state = det.check(h, v)

        assert state.detected is True
        assert state.source_system == "victron"
        assert state.sink_system == "huawei"


# ---------------------------------------------------------------------------
# Mitigation
# ---------------------------------------------------------------------------


class TestMitigation:
    """Tests for mitigate() forcing sink battery to HOLDING."""

    def test_mitigation_forces_sink_to_holding(self) -> None:
        """When sink_system='victron', mitigate() returns v_cmd with HOLDING."""
        det = CrossChargeDetector()
        state = CrossChargeState(
            detected=True,
            source_system="huawei",
            sink_system="victron",
            source_power_w=500.0,
            sink_power_w=400.0,
            net_grid_power_w=20.0,
            consecutive_cycles=2,
        )
        h_cmd = _cmd(BatteryRole.PRIMARY_DISCHARGE, -1000.0)
        v_cmd = _cmd(BatteryRole.CHARGING, 500.0)

        new_h, new_v = det.mitigate(state, h_cmd, v_cmd)

        # Huawei command unchanged
        assert new_h.role == BatteryRole.PRIMARY_DISCHARGE
        assert new_h.target_watts == -1000.0
        # Victron forced to HOLDING
        assert new_v.role == BatteryRole.HOLDING
        assert new_v.target_watts == 0.0

    def test_mitigation_forces_huawei_holding(self) -> None:
        """When sink_system='huawei', mitigate() returns h_cmd with HOLDING."""
        det = CrossChargeDetector()
        state = CrossChargeState(
            detected=True,
            source_system="victron",
            sink_system="huawei",
            source_power_w=400.0,
            sink_power_w=300.0,
            net_grid_power_w=10.0,
            consecutive_cycles=2,
        )
        h_cmd = _cmd(BatteryRole.CHARGING, 500.0)
        v_cmd = _cmd(BatteryRole.PRIMARY_DISCHARGE, -1000.0)

        new_h, new_v = det.mitigate(state, h_cmd, v_cmd)

        assert new_h.role == BatteryRole.HOLDING
        assert new_h.target_watts == 0.0
        assert new_v.role == BatteryRole.PRIMARY_DISCHARGE
        assert new_v.target_watts == -1000.0


# ---------------------------------------------------------------------------
# Grid power resolution
# ---------------------------------------------------------------------------


class TestGridPower:
    """Tests for grid power source resolution."""

    def test_grid_power_uses_l1_l2_l3_sum(self) -> None:
        """v_snap with l1=50, l2=30, l3=20 (sum=100) and grid_power_w=None
        -> uses 100W as grid power, which is below threshold -> can detect."""
        det = CrossChargeDetector()
        h = _snap(power=-500.0)
        v = _snap(
            power=400.0,
            grid_power_w=None,
            grid_l1_power_w=50.0,
            grid_l2_power_w=30.0,
            grid_l3_power_w=20.0,
        )

        det.check(h, v)
        state = det.check(h, v)
        assert state.detected is True
        assert state.net_grid_power_w == 100.0

    def test_grid_power_none_skips_detection(self) -> None:
        """v_snap with all grid fields None -> detected=False."""
        det = CrossChargeDetector()
        h = _snap(power=-500.0)
        v = _snap(power=400.0)  # all grid fields None

        det.check(h, v)
        state = det.check(h, v)
        assert state.detected is False


# ---------------------------------------------------------------------------
# Episode tracking
# ---------------------------------------------------------------------------


class TestEpisodeTracking:
    """Tests for episode start, waste tracking, reset, and persistence."""

    def test_episode_starts_on_first_detection(self) -> None:
        """After 2 detection cycles, active_episode is created."""
        det = CrossChargeDetector()
        h = _snap(power=-500.0)
        v = _snap(power=400.0, grid_power_w=20.0)

        det.check(h, v)
        assert det.current_episode is None

        det.check(h, v)
        assert det.current_episode is not None
        assert det.current_episode.start_time > 0

    def test_episode_tracks_waste(self) -> None:
        """waste = min(abs(charge_power), abs(discharge_power)) * cycle_duration_s / 3600."""
        det = CrossChargeDetector(cycle_duration_s=5.0)
        h = _snap(power=-500.0)
        v = _snap(power=400.0, grid_power_w=20.0)

        det.check(h, v)  # consecutive=1, no detection yet
        det.check(h, v)  # consecutive=2, detection! episode starts

        # Expected waste for one detection cycle:
        # min(500, 400) * 5.0 / 3600 = 400 * 5 / 3600 = 0.5556 Wh
        expected_waste = min(500.0, 400.0) * 5.0 / 3600.0
        assert det.current_episode is not None
        assert abs(det.current_episode.cumulative_waste_wh - expected_waste) < 0.001

        # Another cycle accumulates more waste
        det.check(h, v)
        assert abs(det.current_episode.cumulative_waste_wh - 2 * expected_waste) < 0.001

    def test_episode_resets_after_cooldown(self) -> None:
        """Episode clears after episode_reset_s (300s) of no detection."""
        det = CrossChargeDetector(episode_reset_s=300.0)
        h = _snap(power=-500.0)
        v = _snap(power=400.0, grid_power_w=20.0)
        h_ok = _snap(power=0.0)
        v_ok = _snap(power=0.0, grid_power_w=50.0)

        # Start episode
        det.check(h, v)
        det.check(h, v)
        assert det.current_episode is not None

        # Clear condition
        det.check(h_ok, v_ok)

        # Simulate 301s passing by patching _last_clear_time
        det._last_clear_time = time.monotonic() - 301.0
        det.check(h_ok, v_ok)

        assert det.current_episode is None
        assert det.total_episodes == 1

    def test_episode_persists_during_brief_clear(self) -> None:
        """One clear cycle within episode does not reset episode."""
        det = CrossChargeDetector(episode_reset_s=300.0)
        h = _snap(power=-500.0)
        v = _snap(power=400.0, grid_power_w=20.0)
        h_ok = _snap(power=0.0)
        v_ok = _snap(power=0.0, grid_power_w=50.0)

        # Start episode
        det.check(h, v)
        det.check(h, v)
        assert det.current_episode is not None

        # Brief clear (not 5 minutes)
        det.check(h_ok, v_ok)
        assert det.current_episode is not None  # still active


# ---------------------------------------------------------------------------
# CoordinatorState extension
# ---------------------------------------------------------------------------


class TestCoordinatorStateFields:
    """Tests for new cross-charge fields on CoordinatorState."""

    def test_state_fields_on_coordinator_state(self) -> None:
        """CoordinatorState has cross_charge_active, waste_wh, episode_count."""
        state = CoordinatorState(
            combined_soc_pct=50.0,
            huawei_soc_pct=50.0,
            victron_soc_pct=50.0,
            huawei_available=True,
            victron_available=True,
            control_state="ACTIVE",
            huawei_discharge_setpoint_w=0,
            victron_discharge_setpoint_w=0,
            combined_power_w=0.0,
            huawei_charge_headroom_w=5000,
            victron_charge_headroom_w=5000.0,
            timestamp=time.monotonic(),
        )
        # Verify defaults
        assert state.cross_charge_active is False
        assert state.cross_charge_waste_wh == 0.0
        assert state.cross_charge_episode_count == 0

        # Verify can be set
        state.cross_charge_active = True
        state.cross_charge_waste_wh = 1.5
        state.cross_charge_episode_count = 3
        assert state.cross_charge_active is True
        assert state.cross_charge_waste_wh == 1.5
        assert state.cross_charge_episode_count == 3
