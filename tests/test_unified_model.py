"""Unit tests for UnifiedPoolState, ControlState, SystemConfig, and OrchestratorConfig.

No live hardware required.  All tests are pure dataclass/enum math — no async,
no network, no mocking of external drivers beyond the helper constructors below.

Coverage:
  - ``UnifiedPoolState.from_readings()`` — weighted-average SoC math (30/64/94)
  - ``UnifiedPoolState.from_readings()`` — charge headroom computation
  - ``UnifiedPoolState.from_readings()`` — pack2_soc_pct=None gracefully ignored
  - ``UnifiedPoolState.is_stale()`` — fresh and aged timestamps
  - ``SystemConfig`` — default field values
  - ``OrchestratorConfig`` — default field values
  - ``ControlState`` enum — all four members present and string-serialisable
"""
from __future__ import annotations

import time

import pytest

from backend.config import OrchestratorConfig, SystemConfig
from backend.drivers.huawei_models import HuaweiBatteryData
from backend.drivers.victron_models import VictronPhaseData, VictronSystemData
from backend.unified_model import ControlState, UnifiedPoolState


# ---------------------------------------------------------------------------
# Helpers — match the _make_X() pattern from S01/S02 tests
# ---------------------------------------------------------------------------

def _make_battery(**overrides) -> HuaweiBatteryData:
    """Return a fully-populated HuaweiBatteryData with sensible defaults."""
    defaults: dict = {
        "pack1_soc_pct": 60.0,
        "pack1_charge_discharge_power_w": 0,
        "pack1_status": 1,
        "pack2_soc_pct": 58.0,
        "pack2_charge_discharge_power_w": 0,
        "pack2_status": 1,
        "total_soc_pct": 59.0,
        "total_charge_discharge_power_w": 0,
        "max_charge_power_w": 5000,
        "max_discharge_power_w": 5000,
        "working_mode": 2,
    }
    defaults.update(overrides)
    return HuaweiBatteryData(**defaults)


def _make_phase(**overrides) -> VictronPhaseData:
    """Return a fully-populated VictronPhaseData with sensible defaults."""
    defaults: dict = {
        "power_w": 1000.0,
        "current_a": 4.4,
        "voltage_v": 230.0,
        "setpoint_w": None,
    }
    defaults.update(overrides)
    return VictronPhaseData(**defaults)


def _make_system_data(**overrides) -> VictronSystemData:
    """Return a fully-populated VictronSystemData with sensible defaults."""
    defaults: dict = {
        "battery_soc_pct": 60.0,
        "battery_power_w": 0.0,
        "battery_current_a": 0.0,
        "battery_voltage_v": 48.0,
        "l1": _make_phase(),
        "l2": _make_phase(),
        "l3": _make_phase(),
        "ess_mode": 3,
        "system_state": 9,
        "vebus_state": 9,
        "timestamp": time.monotonic(),
    }
    defaults.update(overrides)
    return VictronSystemData(**defaults)


# ---------------------------------------------------------------------------
# ControlState enum
# ---------------------------------------------------------------------------

class TestControlState:
    def test_all_members_present(self):
        members = {m.name for m in ControlState}
        assert members == {"IDLE", "DISCHARGE", "CHARGE", "HOLD", "GRID_CHARGE"}

    def test_values_are_strings(self):
        """ControlState inherits str so JSON serialisation works without a custom encoder."""
        for member in ControlState:
            assert isinstance(member.value, str)
            assert member == member.value  # str equality via mixin


# ---------------------------------------------------------------------------
# UnifiedPoolState.from_readings() — SoC math
# ---------------------------------------------------------------------------

class TestUnifiedPoolStateFromReadings:
    def test_combined_soc_weighted_average(self):
        """(80*30 + 60*64)/94 ≈ 66.0%"""
        battery = _make_battery(total_soc_pct=80.0)
        victron = _make_system_data(battery_soc_pct=60.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.IDLE, (0, 0)
        )

        expected = (80.0 * 30.0 + 60.0 * 64.0) / 94.0
        assert state.combined_soc_pct == pytest.approx(expected, abs=0.01)
        assert state.huawei_soc_pct == pytest.approx(80.0)
        assert state.victron_soc_pct == pytest.approx(60.0)

    def test_combined_soc_both_full(self):
        """100% on both → combined = 100%"""
        battery = _make_battery(total_soc_pct=100.0)
        victron = _make_system_data(battery_soc_pct=100.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.CHARGE, (0, 0)
        )

        assert state.combined_soc_pct == pytest.approx(100.0)

    def test_combined_soc_both_empty(self):
        """0% on both → combined = 0%"""
        battery = _make_battery(total_soc_pct=0.0)
        victron = _make_system_data(battery_soc_pct=0.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.HOLD, (0, 0)
        )

        assert state.combined_soc_pct == pytest.approx(0.0)

    def test_combined_soc_victron_dominates_capacity(self):
        """With Victron at 100% and Huawei at 0%, the combined SoC is ~68%
        because Victron holds 64/94 of the total capacity."""
        battery = _make_battery(total_soc_pct=0.0)
        victron = _make_system_data(battery_soc_pct=100.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.DISCHARGE, (0, 0)
        )

        expected = (0.0 * 30.0 + 100.0 * 64.0) / 94.0
        assert state.combined_soc_pct == pytest.approx(expected, abs=0.01)

    def test_uses_total_soc_not_pack_fields(self):
        """from_readings() must use total_soc_pct; pack fields must not affect result."""
        # pack1_soc and pack2_soc are very different from total_soc_pct
        battery = _make_battery(
            pack1_soc_pct=10.0,
            pack2_soc_pct=90.0,
            total_soc_pct=50.0,
        )
        victron = _make_system_data(battery_soc_pct=50.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.IDLE, (0, 0)
        )

        # Should use total_soc_pct=50.0, not any pack average
        expected = (50.0 * 30.0 + 50.0 * 64.0) / 94.0  # = 50.0
        assert state.combined_soc_pct == pytest.approx(expected, abs=0.01)

    def test_pack2_soc_none_does_not_raise(self):
        """pack2_soc_pct=None must not cause any error — from_readings ignores pack fields."""
        battery = _make_battery(
            pack2_soc_pct=None,
            pack2_charge_discharge_power_w=None,
            pack2_status=None,
            total_soc_pct=70.0,
        )
        victron = _make_system_data(battery_soc_pct=70.0)

        # Must not raise
        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.IDLE, (0, 0)
        )
        assert state.huawei_soc_pct == pytest.approx(70.0)


# ---------------------------------------------------------------------------
# UnifiedPoolState.from_readings() — charge headroom
# ---------------------------------------------------------------------------

class TestChargeHeadroom:
    def test_huawei_headroom_idle(self):
        """When battery is idle (charge_power=0), headroom = max_charge_power."""
        battery = _make_battery(
            total_charge_discharge_power_w=0,
            max_charge_power_w=5000,
        )
        victron = _make_system_data(battery_power_w=0.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.IDLE, (0, 0)
        )

        assert state.huawei_charge_headroom_w == 5000

    def test_huawei_headroom_partially_charging(self):
        """Headroom = max_charge - current_charge (positive = charging)."""
        battery = _make_battery(
            total_charge_discharge_power_w=2000,  # charging at 2kW
            max_charge_power_w=5000,
        )
        victron = _make_system_data(battery_power_w=0.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.CHARGE, (0, 0)
        )

        assert state.huawei_charge_headroom_w == 3000

    def test_huawei_headroom_clamped_to_zero_when_discharging(self):
        """When discharging, charge_power_w=0, so headroom = max_charge_power."""
        battery = _make_battery(
            total_charge_discharge_power_w=-3000,  # discharging at 3kW
            max_charge_power_w=5000,
        )
        victron = _make_system_data(battery_power_w=0.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.DISCHARGE, (0, 0)
        )

        # charge_power_w = max(0, -3000) = 0; headroom = 5000 - 0 = 5000
        assert state.huawei_charge_headroom_w == 5000

    def test_huawei_headroom_at_min_soc(self):
        """Battery at minimum SoC still computes headroom from max_charge_power."""
        battery = _make_battery(
            total_soc_pct=10.0,
            total_charge_discharge_power_w=0,
            max_charge_power_w=5000,
        )
        victron = _make_system_data(battery_soc_pct=50.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.HOLD, (0, 0)
        )

        assert state.huawei_charge_headroom_w == 5000

    def test_victron_headroom_idle_is_zero(self):
        """Victron headroom when idle (battery_power_w=0) is 0.0."""
        battery = _make_battery()
        victron = _make_system_data(battery_power_w=0.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.IDLE, (0, 0)
        )

        assert state.victron_charge_headroom_w == pytest.approx(0.0)

    def test_victron_headroom_when_charging(self):
        """Victron headroom = current charge power (reflects ongoing charge)."""
        battery = _make_battery()
        victron = _make_system_data(battery_power_w=3000.0)  # charging at 3kW

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.CHARGE, (0, 0)
        )

        assert state.victron_charge_headroom_w == pytest.approx(3000.0)


# ---------------------------------------------------------------------------
# UnifiedPoolState — setpoints and combined power
# ---------------------------------------------------------------------------

class TestSetpointsAndPower:
    def test_setpoints_stored_correctly(self):
        battery = _make_battery(total_charge_discharge_power_w=-2000)
        victron = _make_system_data(battery_power_w=-4000.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.DISCHARGE, (2000, 4000)
        )

        assert state.huawei_discharge_setpoint_w == 2000
        assert state.victron_discharge_setpoint_w == 4000

    def test_combined_power_discharge(self):
        """combined_power_w = huawei + victron (both negative when discharging)."""
        battery = _make_battery(total_charge_discharge_power_w=-2000)
        victron = _make_system_data(battery_power_w=-4000.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.DISCHARGE, (0, 0)
        )

        assert state.combined_power_w == pytest.approx(-6000.0)

    def test_combined_power_charge(self):
        """combined_power_w is positive when both systems are charging."""
        battery = _make_battery(total_charge_discharge_power_w=1500)
        victron = _make_system_data(battery_power_w=2500.0)

        state = UnifiedPoolState.from_readings(
            battery, victron, ControlState.CHARGE, (0, 0)
        )

        assert state.combined_power_w == pytest.approx(4000.0)

    def test_availability_flags_set_true(self):
        """from_readings() marks both systems available (both readings provided)."""
        state = UnifiedPoolState.from_readings(
            _make_battery(), _make_system_data(), ControlState.IDLE, (0, 0)
        )
        assert state.huawei_available is True
        assert state.victron_available is True

    def test_control_state_stored(self):
        for cs in ControlState:
            state = UnifiedPoolState.from_readings(
                _make_battery(), _make_system_data(), cs, (0, 0)
            )
            assert state.control_state is cs


# ---------------------------------------------------------------------------
# UnifiedPoolState.is_stale()
# ---------------------------------------------------------------------------

class TestIsStale:
    def test_fresh_snapshot_not_stale(self):
        """A just-constructed snapshot should not be stale with a 30 s threshold."""
        state = UnifiedPoolState.from_readings(
            _make_battery(), _make_system_data(), ControlState.IDLE, (0, 0)
        )
        assert state.is_stale(30.0) is False

    def test_zero_max_age_always_stale(self):
        """max_age_s=0 means any age is stale (edge case)."""
        state = UnifiedPoolState.from_readings(
            _make_battery(), _make_system_data(), ControlState.IDLE, (0, 0)
        )
        # monotonic() advances; even an immediately-constructed snapshot
        # should be stale with max_age_s=0 after even a tiny elapsed time.
        # We force this by constructing with a past timestamp directly.
        import dataclasses
        old_state = dataclasses.replace(state, timestamp=time.monotonic() - 0.001)
        assert old_state.is_stale(0.0) is True

    def test_old_snapshot_is_stale(self):
        """A snapshot with a timestamp 60 seconds in the past must be stale."""
        import dataclasses
        state = UnifiedPoolState.from_readings(
            _make_battery(), _make_system_data(), ControlState.IDLE, (0, 0)
        )
        old_state = dataclasses.replace(state, timestamp=time.monotonic() - 60.0)
        assert old_state.is_stale(30.0) is True

    def test_boundary_not_stale(self):
        """A snapshot just under the threshold must NOT be stale."""
        import dataclasses
        state = UnifiedPoolState.from_readings(
            _make_battery(), _make_system_data(), ControlState.IDLE, (0, 0)
        )
        # 5 seconds old against a 30-second threshold → not stale
        almost_old = dataclasses.replace(state, timestamp=time.monotonic() - 5.0)
        assert almost_old.is_stale(30.0) is False


# ---------------------------------------------------------------------------
# SystemConfig defaults
# ---------------------------------------------------------------------------

class TestSystemConfig:
    def test_default_values(self):
        cfg = SystemConfig()
        assert cfg.huawei_min_soc_pct == pytest.approx(10.0)
        assert cfg.huawei_max_soc_pct == pytest.approx(95.0)
        assert cfg.victron_min_soc_pct == pytest.approx(15.0)
        assert cfg.victron_max_soc_pct == pytest.approx(95.0)
        assert cfg.huawei_feed_in_allowed is False
        assert cfg.victron_feed_in_allowed is False

    def test_override_feed_in(self):
        cfg = SystemConfig(huawei_feed_in_allowed=True)
        assert cfg.huawei_feed_in_allowed is True
        assert cfg.victron_feed_in_allowed is False  # untouched

    def test_override_soc_limits(self):
        cfg = SystemConfig(huawei_min_soc_pct=20.0, victron_max_soc_pct=90.0)
        assert cfg.huawei_min_soc_pct == pytest.approx(20.0)
        assert cfg.victron_max_soc_pct == pytest.approx(90.0)
        # Defaults preserved for untouched fields
        assert cfg.huawei_max_soc_pct == pytest.approx(95.0)


# ---------------------------------------------------------------------------
# OrchestratorConfig defaults
# ---------------------------------------------------------------------------

class TestOrchestratorConfig:
    def test_default_values(self):
        cfg = OrchestratorConfig()
        assert cfg.loop_interval_s == pytest.approx(5.0)
        assert cfg.hysteresis_w == 200
        assert cfg.debounce_cycles == 2
        assert cfg.stale_threshold_s == pytest.approx(30.0)
        assert cfg.max_offline_s == pytest.approx(60.0)
        assert cfg.victron_max_discharge_w == pytest.approx(10000.0)
        assert cfg.victron_max_charge_w == pytest.approx(10000.0)
        assert cfg.huawei_capacity_kwh == pytest.approx(30.0)
        assert cfg.victron_capacity_kwh == pytest.approx(64.0)

    def test_override_loop_interval(self):
        cfg = OrchestratorConfig(loop_interval_s=10.0)
        assert cfg.loop_interval_s == pytest.approx(10.0)
        assert cfg.hysteresis_w == 200  # default preserved

    def test_capacity_sum(self):
        """Sanity check: huawei + victron capacity == 94 kWh."""
        cfg = OrchestratorConfig()
        assert cfg.huawei_capacity_kwh + cfg.victron_capacity_kwh == pytest.approx(94.0)
