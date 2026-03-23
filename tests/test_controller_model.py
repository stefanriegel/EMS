"""Tests for controller_model.py — enums and dataclasses."""
from __future__ import annotations

import json
import time

import pytest


class TestBatteryRole:
    """BatteryRole enum has exactly 5 members and is JSON-serializable."""

    def test_has_six_members(self):
        from backend.controller_model import BatteryRole

        assert len(BatteryRole) == 6

    def test_member_names(self):
        from backend.controller_model import BatteryRole

        expected = {
            "PRIMARY_DISCHARGE",
            "SECONDARY_DISCHARGE",
            "CHARGING",
            "HOLDING",
            "GRID_CHARGE",
            "EXPORTING",
        }
        assert {m.name for m in BatteryRole} == expected

    def test_json_serializable(self):
        from backend.controller_model import BatteryRole

        for member in BatteryRole:
            serialized = json.dumps(member)
            assert isinstance(serialized, str)

    def test_str_mixin(self):
        from backend.controller_model import BatteryRole

        assert isinstance(BatteryRole.HOLDING, str)
        assert BatteryRole.HOLDING == "HOLDING"


class TestPoolStatus:
    """PoolStatus enum has exactly 3 members and is JSON-serializable."""

    def test_has_three_members(self):
        from backend.controller_model import PoolStatus

        assert len(PoolStatus) == 3

    def test_member_names(self):
        from backend.controller_model import PoolStatus

        expected = {"NORMAL", "DEGRADED", "OFFLINE"}
        assert {m.name for m in PoolStatus} == expected

    def test_json_serializable(self):
        from backend.controller_model import PoolStatus

        for member in PoolStatus:
            serialized = json.dumps(member)
            assert isinstance(serialized, str)

    def test_str_mixin(self):
        from backend.controller_model import PoolStatus

        assert isinstance(PoolStatus.NORMAL, str)
        assert PoolStatus.NORMAL == "NORMAL"


class TestControllerSnapshot:
    """ControllerSnapshot dataclass construction and field types."""

    def test_construct_with_required_fields(self):
        from backend.controller_model import BatteryRole, ControllerSnapshot

        snap = ControllerSnapshot(
            soc_pct=55.0,
            power_w=-1200.0,
            available=True,
            role=BatteryRole.HOLDING,
            consecutive_failures=0,
            timestamp=time.monotonic(),
        )
        assert snap.soc_pct == 55.0
        assert snap.power_w == -1200.0
        assert snap.available is True
        assert snap.role == BatteryRole.HOLDING
        assert snap.consecutive_failures == 0

    def test_optional_fields_default_none(self):
        from backend.controller_model import BatteryRole, ControllerSnapshot

        snap = ControllerSnapshot(
            soc_pct=50.0,
            power_w=0.0,
            available=True,
            role=BatteryRole.HOLDING,
            consecutive_failures=0,
            timestamp=time.monotonic(),
        )
        assert snap.max_charge_power_w is None
        assert snap.max_discharge_power_w is None
        assert snap.master_active_power_w is None
        assert snap.grid_power_w is None
        assert snap.grid_l1_power_w is None
        assert snap.grid_l2_power_w is None
        assert snap.grid_l3_power_w is None
        assert snap.ess_mode is None
        assert snap.charge_headroom_w == 0.0


class TestControllerCommand:
    """ControllerCommand dataclass construction."""

    def test_construct_with_role_and_watts(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        cmd = ControllerCommand(
            role=BatteryRole.PRIMARY_DISCHARGE,
            target_watts=-5000.0,
        )
        assert cmd.role == BatteryRole.PRIMARY_DISCHARGE
        assert cmd.target_watts == -5000.0
        assert cmd.evcc_hold is False  # default

    def test_evcc_hold_default_false(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        cmd = ControllerCommand(
            role=BatteryRole.HOLDING, target_watts=0.0
        )
        assert cmd.evcc_hold is False

    def test_evcc_hold_can_be_set(self):
        from backend.controller_model import BatteryRole, ControllerCommand

        cmd = ControllerCommand(
            role=BatteryRole.HOLDING, target_watts=0.0, evcc_hold=True
        )
        assert cmd.evcc_hold is True


class TestCoordinatorState:
    """CoordinatorState backward-compatible superset of UnifiedPoolState."""

    def test_has_all_unified_pool_state_fields(self):
        from backend.controller_model import CoordinatorState

        state = CoordinatorState(
            combined_soc_pct=60.0,
            huawei_soc_pct=50.0,
            victron_soc_pct=65.0,
            huawei_available=True,
            victron_available=True,
            control_state="DISCHARGE",
            huawei_discharge_setpoint_w=2000,
            victron_discharge_setpoint_w=3000,
            combined_power_w=-5000.0,
            huawei_charge_headroom_w=1000,
            victron_charge_headroom_w=2000.0,
            timestamp=time.monotonic(),
        )
        assert state.combined_soc_pct == 60.0
        assert state.huawei_soc_pct == 50.0
        assert state.victron_soc_pct == 65.0

    def test_new_role_fields_have_defaults(self):
        from backend.controller_model import CoordinatorState

        state = CoordinatorState(
            combined_soc_pct=60.0,
            huawei_soc_pct=50.0,
            victron_soc_pct=65.0,
            huawei_available=True,
            victron_available=True,
            control_state="DISCHARGE",
            huawei_discharge_setpoint_w=2000,
            victron_discharge_setpoint_w=3000,
            combined_power_w=-5000.0,
            huawei_charge_headroom_w=1000,
            victron_charge_headroom_w=2000.0,
            timestamp=time.monotonic(),
        )
        assert state.huawei_role == "HOLDING"
        assert state.victron_role == "HOLDING"
        assert state.pool_status == "NORMAL"

    def test_grid_charge_slot_active_default(self):
        from backend.controller_model import CoordinatorState

        state = CoordinatorState(
            combined_soc_pct=60.0,
            huawei_soc_pct=50.0,
            victron_soc_pct=65.0,
            huawei_available=True,
            victron_available=True,
            control_state="HOLD",
            huawei_discharge_setpoint_w=0,
            victron_discharge_setpoint_w=0,
            combined_power_w=0.0,
            huawei_charge_headroom_w=0,
            victron_charge_headroom_w=0.0,
            timestamp=time.monotonic(),
        )
        assert state.grid_charge_slot_active is False
        assert state.evcc_battery_mode == "normal"
