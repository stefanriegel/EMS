"""Tests for Coordinator DESS guard — discharge gating during DESS charge windows.

Covers:
- _apply_dess_guard suppresses Huawei discharge when DESS active slot has strategy=1 (charge)
- _apply_dess_guard does NOT suppress when strategy=0 (optimize)
- Guard returns commands unchanged when DESS subscriber is None
- Guard returns commands unchanged when dess_available is False
- Guard returns commands unchanged when schedule.mode is 0
- Guard returns commands unchanged when no active slot
- DecisionEntry logged with trigger="dess_coordination" on suppression
- CoordinatorState includes dess_mode, dess_active_slot, dess_available, vrm_available
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from unittest.mock import MagicMock

import pytest

from backend.controller_model import (
    BatteryRole,
    ControllerCommand,
    ControllerSnapshot,
    CoordinatorState,
    DecisionEntry,
)
from backend.config import OrchestratorConfig, SystemConfig
from backend.coordinator import Coordinator
from backend.dess_models import DessSchedule, DessScheduleSlot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap(
    soc: float = 50.0,
    power: float = 0.0,
    available: bool = True,
    role: BatteryRole = BatteryRole.HOLDING,
    charge_headroom_w: float = 5000.0,
) -> ControllerSnapshot:
    """Build a ControllerSnapshot with sensible defaults."""
    return ControllerSnapshot(
        soc_pct=soc,
        power_w=power,
        available=available,
        role=role,
        consecutive_failures=0,
        timestamp=time.monotonic(),
        charge_headroom_w=charge_headroom_w,
    )


def _make_coordinator() -> Coordinator:
    """Build a minimal Coordinator for DESS guard testing."""
    h_ctrl = MagicMock()
    v_ctrl = MagicMock()
    sys_cfg = SystemConfig()
    orch_cfg = OrchestratorConfig()
    return Coordinator(h_ctrl, v_ctrl, sys_cfg, orch_cfg)


class FakeDessSub:
    """Fake DessMqttSubscriber with controllable state."""

    def __init__(
        self,
        available: bool = True,
        mode: int = 1,
        slots: list[DessScheduleSlot] | None = None,
    ) -> None:
        self.dess_available = available
        self.schedule = DessSchedule(
            mode=mode,
            slots=slots or [DessScheduleSlot() for _ in range(4)],
        )

    def get_active_slot(self, now_seconds_from_midnight: int) -> DessScheduleSlot | None:
        if self.schedule.mode < 1:
            return None
        for slot in self.schedule.slots:
            end_s = slot.start_s + slot.duration_s
            if slot.duration_s > 0 and slot.start_s <= now_seconds_from_midnight < end_s:
                return slot
        return None


class FakeVrmClient:
    """Fake VrmClient with controllable availability."""

    def __init__(self, available: bool = True) -> None:
        self._available = available

    @property
    def available(self) -> bool:
        return self._available


# ---------------------------------------------------------------------------
# Tests: _apply_dess_guard
# ---------------------------------------------------------------------------

class TestApplyDessGuard:
    """Test the DESS guard logic on the coordinator."""

    def _discharge_cmds(self) -> tuple[ControllerCommand, ControllerCommand]:
        """Commands where Huawei is discharging."""
        h = ControllerCommand(role=BatteryRole.PRIMARY_DISCHARGE, target_watts=-3000.0)
        v = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        return h, v

    def test_suppresses_huawei_discharge_during_dess_charge(self):
        """DESS active slot with strategy=1 (charge) should suppress Huawei discharge."""
        coord = _make_coordinator()
        # Active slot: starts at 0s, lasts 3600s, strategy=1 (charge)
        slots = [DessScheduleSlot() for _ in range(4)]
        slots[0] = DessScheduleSlot(start_s=0, duration_s=86400, strategy=1)
        sub = FakeDessSub(available=True, mode=1, slots=slots)
        coord.set_dess_subscriber(sub)

        h_cmd, v_cmd = self._discharge_cmds()
        h_out, v_out = coord._apply_dess_guard(h_cmd, v_cmd)

        assert h_out.role == BatteryRole.HOLDING
        assert h_out.target_watts == 0.0
        # Victron command unchanged
        assert v_out.role == v_cmd.role
        assert v_out.target_watts == v_cmd.target_watts

    def test_does_not_suppress_during_dess_optimize(self):
        """DESS active slot with strategy=0 (optimize) should NOT suppress."""
        coord = _make_coordinator()
        slots = [DessScheduleSlot() for _ in range(4)]
        slots[0] = DessScheduleSlot(start_s=0, duration_s=86400, strategy=0)
        sub = FakeDessSub(available=True, mode=1, slots=slots)
        coord.set_dess_subscriber(sub)

        h_cmd, v_cmd = self._discharge_cmds()
        h_out, v_out = coord._apply_dess_guard(h_cmd, v_cmd)

        assert h_out.role == BatteryRole.PRIMARY_DISCHARGE
        assert h_out.target_watts == -3000.0

    def test_unchanged_when_subscriber_none(self):
        """No DESS subscriber -> commands pass through unchanged."""
        coord = _make_coordinator()
        # No subscriber set
        h_cmd, v_cmd = self._discharge_cmds()
        h_out, v_out = coord._apply_dess_guard(h_cmd, v_cmd)

        assert h_out.role == h_cmd.role
        assert h_out.target_watts == h_cmd.target_watts

    def test_unchanged_when_dess_unavailable(self):
        """DESS subscriber exists but dess_available=False -> unchanged."""
        coord = _make_coordinator()
        sub = FakeDessSub(available=False, mode=1)
        coord.set_dess_subscriber(sub)

        h_cmd, v_cmd = self._discharge_cmds()
        h_out, v_out = coord._apply_dess_guard(h_cmd, v_cmd)

        assert h_out.role == h_cmd.role
        assert h_out.target_watts == h_cmd.target_watts

    def test_unchanged_when_mode_zero(self):
        """DESS mode=0 (off) -> guard skipped entirely."""
        coord = _make_coordinator()
        slots = [DessScheduleSlot() for _ in range(4)]
        slots[0] = DessScheduleSlot(start_s=0, duration_s=86400, strategy=1)
        sub = FakeDessSub(available=True, mode=0, slots=slots)
        coord.set_dess_subscriber(sub)

        h_cmd, v_cmd = self._discharge_cmds()
        h_out, v_out = coord._apply_dess_guard(h_cmd, v_cmd)

        assert h_out.role == h_cmd.role
        assert h_out.target_watts == h_cmd.target_watts

    def test_unchanged_when_no_active_slot(self):
        """No active slot at current time -> unchanged."""
        coord = _make_coordinator()
        # No slots have duration > 0
        sub = FakeDessSub(available=True, mode=1)
        coord.set_dess_subscriber(sub)

        h_cmd, v_cmd = self._discharge_cmds()
        h_out, v_out = coord._apply_dess_guard(h_cmd, v_cmd)

        assert h_out.role == h_cmd.role
        assert h_out.target_watts == h_cmd.target_watts

    def test_logs_decision_entry_on_suppression(self):
        """Suppression should log a DecisionEntry with trigger='dess_coordination'."""
        coord = _make_coordinator()
        slots = [DessScheduleSlot() for _ in range(4)]
        slots[0] = DessScheduleSlot(start_s=0, duration_s=86400, strategy=1)
        sub = FakeDessSub(available=True, mode=1, slots=slots)
        coord.set_dess_subscriber(sub)

        h_cmd, v_cmd = self._discharge_cmds()
        coord._apply_dess_guard(h_cmd, v_cmd)

        # Check decision ring buffer
        assert len(coord._decisions) == 1
        entry = coord._decisions[0]
        assert entry.trigger == "dess_coordination"
        assert "DESS" in entry.reasoning

    def test_no_suppression_when_huawei_charging(self):
        """Huawei charging (positive watts) should not be suppressed."""
        coord = _make_coordinator()
        slots = [DessScheduleSlot() for _ in range(4)]
        slots[0] = DessScheduleSlot(start_s=0, duration_s=86400, strategy=1)
        sub = FakeDessSub(available=True, mode=1, slots=slots)
        coord.set_dess_subscriber(sub)

        h_cmd = ControllerCommand(role=BatteryRole.CHARGING, target_watts=2000.0)
        v_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        h_out, v_out = coord._apply_dess_guard(h_cmd, v_cmd)

        # Should pass through unchanged (charging, not discharging)
        assert h_out.role == BatteryRole.CHARGING
        assert h_out.target_watts == 2000.0


# ---------------------------------------------------------------------------
# Tests: CoordinatorState DESS fields
# ---------------------------------------------------------------------------

class TestCoordinatorStateDessFields:
    """Verify CoordinatorState includes DESS/VRM fields."""

    def test_coordinator_state_has_dess_fields(self):
        """CoordinatorState should have dess_mode, dess_active_slot, dess_available, vrm_available."""
        state = CoordinatorState(
            combined_soc_pct=50.0,
            huawei_soc_pct=50.0,
            victron_soc_pct=50.0,
            huawei_available=True,
            victron_available=True,
            control_state="IDLE",
            huawei_discharge_setpoint_w=0,
            victron_discharge_setpoint_w=0,
            combined_power_w=0.0,
            huawei_charge_headroom_w=0,
            victron_charge_headroom_w=0.0,
            timestamp=0.0,
        )
        assert state.dess_mode == 0
        assert state.dess_active_slot is None
        assert state.dess_available is False
        assert state.vrm_available is False

    def test_coordinator_state_dess_fields_settable(self):
        """DESS fields can be set to non-default values."""
        state = CoordinatorState(
            combined_soc_pct=50.0,
            huawei_soc_pct=50.0,
            victron_soc_pct=50.0,
            huawei_available=True,
            victron_available=True,
            control_state="IDLE",
            huawei_discharge_setpoint_w=0,
            victron_discharge_setpoint_w=0,
            combined_power_w=0.0,
            huawei_charge_headroom_w=0,
            victron_charge_headroom_w=0.0,
            timestamp=0.0,
            dess_mode=1,
            dess_active_slot=2,
            dess_available=True,
            vrm_available=True,
        )
        assert state.dess_mode == 1
        assert state.dess_active_slot == 2
        assert state.dess_available is True
        assert state.vrm_available is True
