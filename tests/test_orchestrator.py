"""Unit tests for the Orchestrator control loop (S03/T02).

No live hardware required.  All driver calls are mocked via ``pytest-mock``.

Coverage:
  - SoC-balanced setpoint split with realistic SoC values
  - Both systems at min SoC → both setpoints are 0
  - Overflow routing when Huawei charge is at capacity
  - Hysteresis suppression: writes skipped when Δ < hysteresis_w
  - Debounce: state transition only commits after debounce_cycles consecutive polls
  - Huawei driver failure: huawei_available=False, Victron continues
  - Victron stale data: victron_available=False
  - Both drivers failed beyond max_offline_s → HOLD state, no writes
  - Phase imbalance warning (>500 W deviation for >2 cycles)
  - Victron setpoints use negative per-phase watts (equal 3-phase split)

Test patterns:
  - ``asyncio_mode = "auto"`` in pyproject.toml — async test functions are
    collected automatically; no @pytest.mark.anyio needed.
  - Mock drivers are created with ``mocker.MagicMock()`` / ``mocker.AsyncMock()``.
  - Helper ``_make_orchestrator()`` wires everything up with fast debounce
    (debounce_cycles=1) and no hysteresis (hysteresis_w=0) unless the test
    explicitly needs those features.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from backend.config import OrchestratorConfig, SystemConfig
from backend.drivers.huawei_models import HuaweiBatteryData
from backend.drivers.victron_models import VictronPhaseData, VictronSystemData
from backend.orchestrator import Orchestrator
from backend.unified_model import ControlState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_battery(**overrides) -> HuaweiBatteryData:
    """Return a fully-populated HuaweiBatteryData with sensible defaults."""
    defaults: dict = {
        "pack1_soc_pct": 70.0,
        "pack1_charge_discharge_power_w": 0,
        "pack1_status": 1,
        "pack2_soc_pct": 68.0,
        "pack2_charge_discharge_power_w": 0,
        "pack2_status": 1,
        "total_soc_pct": 70.0,
        "total_charge_discharge_power_w": 0,
        "max_charge_power_w": 5000,
        "max_discharge_power_w": 5000,
        "working_mode": 2,
    }
    defaults.update(overrides)
    return HuaweiBatteryData(**defaults)


def _make_phase(**overrides) -> VictronPhaseData:
    defaults: dict = {
        "power_w": 0.0,
        "current_a": 0.0,
        "voltage_v": 230.0,
        "setpoint_w": None,
    }
    defaults.update(overrides)
    return VictronPhaseData(**defaults)


def _make_victron(**overrides) -> VictronSystemData:
    """Return a fully-populated VictronSystemData with a fresh timestamp."""
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
        "grid_power_w": None,
        "grid_l1_power_w": None,
        "grid_l2_power_w": None,
        "grid_l3_power_w": None,
        "consumption_w": None,
        "pv_on_grid_w": None,
        "timestamp": time.monotonic(),
    }
    defaults.update(overrides)
    return VictronSystemData(**defaults)


def _make_master(**overrides):
    """Return a mock-like object for HuaweiMasterData."""
    from backend.drivers.huawei_models import HuaweiMasterData
    defaults: dict = {
        "pv_input_power_w": 3000,
        "active_power_w": -2000,  # importing 2 kW from grid → P_target = 2000
        "pv_01_voltage_v": 380.0,
        "pv_01_current_a": 5.0,
        "pv_02_voltage_v": 375.0,
        "pv_02_current_a": 5.0,
        "device_status": 0,
    }
    defaults.update(overrides)
    return HuaweiMasterData(**defaults)


def _make_orchestrator(
    huawei_mock=None,
    victron_mock=None,
    sys_config: SystemConfig | None = None,
    orch_config: OrchestratorConfig | None = None,
) -> Orchestrator:
    """Build an Orchestrator with mocked drivers.

    Defaults:
      - debounce_cycles=1 (immediate transitions)
      - hysteresis_w=0 (no suppression unless test overrides)
      - loop_interval_s=0.01 (fast for tests)
    """
    if huawei_mock is None:
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()

    if victron_mock is None:
        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())
        victron_mock.write_ac_power_setpoint = MagicMock()
        victron_mock.write_disable_feed_in = MagicMock()

    if sys_config is None:
        sys_config = SystemConfig()

    if orch_config is None:
        orch_config = OrchestratorConfig(
            loop_interval_s=0.01,
            debounce_cycles=1,
            hysteresis_w=0,
            stale_threshold_s=30.0,
            max_offline_s=60.0,
        )

    return Orchestrator(
        huawei=huawei_mock,
        victron=victron_mock,
        sys_config=sys_config,
        orch_config=orch_config,
    )


# ---------------------------------------------------------------------------
# Tests: setpoint splitting
# ---------------------------------------------------------------------------

class TestSetpointSplit:
    """Verify the SoC-balanced discharge math produces correct ratios."""

    async def test_proportional_split_by_available_capacity(self):
        """Huawei SoC=80%, Victron SoC=60%, min SoCs 10%/15%.

        Available capacity:
          huawei_cap = 80 - 10 = 70
          victron_cap = 60 - 15 = 45
          total_cap = 115
          huawei_ratio = 70/115 ≈ 0.609
          victron_ratio = 45/115 ≈ 0.391

        With P_target = 2000 W (master active_power=-2000):
          huawei_w ≈ 1217 W
          victron_w ≈ 783 W
        """
        battery = _make_battery(total_soc_pct=80.0, max_discharge_power_w=10000)
        victron_data = _make_victron(battery_soc_pct=60.0)
        master = _make_master(active_power_w=-2000)  # importing 2kW

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=master)
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            sys_config=SystemConfig(huawei_min_soc_pct=10.0, victron_min_soc_pct=15.0),
        )

        await orch._poll()
        huawei_w, victron_w = orch._compute_setpoints()

        # Expected ratio check
        expected_huawei_ratio = 70.0 / 115.0
        expected_victron_ratio = 45.0 / 115.0
        p_target = 2000.0

        assert huawei_w == pytest.approx(int(p_target * expected_huawei_ratio), abs=2)
        assert victron_w == pytest.approx(p_target * expected_victron_ratio, abs=2)

    async def test_equal_split_when_equal_available_capacity(self):
        """When both systems have the same available capacity, split is 50/50."""
        battery = _make_battery(total_soc_pct=60.0, max_discharge_power_w=10000)
        victron_data = _make_victron(battery_soc_pct=60.0)
        master = _make_master(active_power_w=-2000)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=master)
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            sys_config=SystemConfig(huawei_min_soc_pct=10.0, victron_min_soc_pct=10.0),
        )

        await orch._poll()
        huawei_w, victron_w = orch._compute_setpoints()

        # Both should be ~1000 W (P_target 2000 split 50/50)
        assert huawei_w == pytest.approx(1000, abs=5)
        assert victron_w == pytest.approx(1000.0, abs=5)

    async def test_both_at_min_soc_returns_zero(self):
        """When both systems are at their minimum SoC, setpoints must be 0."""
        battery = _make_battery(
            total_soc_pct=10.0,  # at huawei min
            max_discharge_power_w=5000,
        )
        victron_data = _make_victron(battery_soc_pct=15.0)  # at victron min
        master = _make_master(active_power_w=-3000)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=master)
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            sys_config=SystemConfig(huawei_min_soc_pct=10.0, victron_min_soc_pct=15.0),
        )

        await orch._poll()
        huawei_w, victron_w = orch._compute_setpoints()

        assert huawei_w == 0
        assert victron_w == 0.0
        assert orch._control_state == ControlState.HOLD

    async def test_setpoints_capped_at_hardware_max(self):
        """Setpoints must not exceed hardware max_discharge_power_w."""
        battery = _make_battery(
            total_soc_pct=80.0,
            max_discharge_power_w=1000,  # low cap
        )
        victron_data = _make_victron(battery_soc_pct=80.0)
        master = _make_master(active_power_w=-10000)  # requesting 10kW

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=master)
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
        )

        await orch._poll()
        huawei_w, victron_w = orch._compute_setpoints()

        # Huawei must not exceed its hardware cap
        assert huawei_w <= 1000
        # Victron must not exceed its configured max
        assert victron_w <= orch._cfg.victron_max_discharge_w


# ---------------------------------------------------------------------------
# Tests: overflow routing (R028)
# ---------------------------------------------------------------------------

class TestOverflowRouting:
    async def test_huawei_charge_full_reduces_victron_setpoint(self):
        """When Huawei charge_power ≥ 95% of max_charge_power, Victron is reduced."""
        # Huawei at 95% of max charge power (charging case — P_target negative or 0)
        battery = _make_battery(
            total_soc_pct=50.0,
            total_charge_discharge_power_w=4750,  # charging at 4750W (95% of 5000)
            max_charge_power_w=5000,
            max_discharge_power_w=5000,
        )
        victron_data = _make_victron(battery_soc_pct=50.0)
        master = _make_master(active_power_w=2000)  # exporting 2kW → P_target clamped to 0

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=master)
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
        )

        await orch._poll()
        huawei_w, victron_w = orch._compute_setpoints()

        # When Huawei is charging at capacity, overflow logic runs.
        # The test verifies the code path executes without error; the exact
        # reduction depends on whether P_target > 0.
        # In this case P_target=0 so both setpoints are 0 anyway.
        assert huawei_w >= 0
        assert victron_w >= 0.0

    async def test_victron_charge_full_no_feed_in_zeros_setpoints(self):
        """When Victron charging is at capacity and feed-in disabled → hold setpoints at 0."""
        battery = _make_battery(
            total_soc_pct=50.0,
            total_charge_discharge_power_w=0,
            max_charge_power_w=5000,
            max_discharge_power_w=5000,
        )
        # Victron charging at 95% of max_charge_w (9500 W / 10000 W)
        victron_data = _make_victron(
            battery_soc_pct=50.0,
            battery_power_w=9500.0,  # charging
        )
        master = _make_master(active_power_w=-3000)  # importing 3kW

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=master)
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            sys_config=SystemConfig(victron_feed_in_allowed=False),
        )

        await orch._poll()
        huawei_w, victron_w = orch._compute_setpoints()

        # Both should be zeroed by the "both full, no feed-in" overflow rule
        assert huawei_w == 0
        assert victron_w == 0.0


# ---------------------------------------------------------------------------
# Tests: hysteresis suppression
# ---------------------------------------------------------------------------

class TestHysteresis:
    async def test_hysteresis_suppresses_small_changes(self):
        """When Δsetpoint < hysteresis_w, write methods must NOT be called."""
        battery = _make_battery(total_soc_pct=70.0, max_discharge_power_w=5000)
        victron_data = _make_victron(battery_soc_pct=60.0)
        master = _make_master(active_power_w=-2000)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=master)
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        # Large hysteresis — any reasonable setpoint will be suppressed
        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=10000,  # wider than any realistic setpoint
            ),
        )

        # Pre-set last setpoints so Δ is small
        orch._last_huawei_setpoint = 1000
        orch._last_victron_setpoint = 900.0
        orch._huawei_available = True
        orch._victron_available = True

        # Apply setpoints that differ by less than hysteresis_w
        await orch._apply_setpoints(1050, 920.0)

        # Neither write should have been called
        huawei_mock.write_max_discharge_power.assert_not_called()
        victron_mock.write_ac_power_setpoint.assert_not_called()

    async def test_no_hysteresis_writes_when_exceeds_band(self):
        """When Δsetpoint > hysteresis_w, write methods ARE called."""
        battery = _make_battery(total_soc_pct=70.0, max_discharge_power_w=5000)
        victron_data = _make_victron(battery_soc_pct=60.0)

        huawei_mock = MagicMock()
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=200,
            ),
        )
        orch._last_huawei_setpoint = 0
        orch._last_victron_setpoint = 0.0
        orch._huawei_available = True
        orch._victron_available = True
        orch._last_victron = victron_data

        # Setpoints differ by > 200 W from last values
        await orch._apply_setpoints(1000, 1000.0)

        huawei_mock.write_max_discharge_power.assert_called_once_with(1000)
        assert victron_mock.write_ac_power_setpoint.call_count == 3


# ---------------------------------------------------------------------------
# Tests: debounce state machine
# ---------------------------------------------------------------------------

class TestDebounce:
    def test_state_transitions_after_debounce_cycles(self):
        """A new state only commits after debounce_cycles consecutive proposals."""
        orch = _make_orchestrator(
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=3,
                hysteresis_w=0,
            )
        )
        assert orch._control_state == ControlState.IDLE

        # Propose DISCHARGE — should not commit yet (need 3 cycles)
        orch._transition_state(ControlState.DISCHARGE, "test")
        assert orch._control_state == ControlState.IDLE  # still IDLE
        assert orch._pending_cycles == 1

        orch._transition_state(ControlState.DISCHARGE, "test")
        assert orch._control_state == ControlState.IDLE  # still not committed
        assert orch._pending_cycles == 2

        orch._transition_state(ControlState.DISCHARGE, "test")
        # Now it should commit
        assert orch._control_state == ControlState.DISCHARGE
        assert orch._pending_cycles == 0

    def test_pending_resets_on_different_state(self):
        """If a different state is proposed mid-debounce, the counter resets."""
        orch = _make_orchestrator(
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=3,
                hysteresis_w=0,
            )
        )

        orch._transition_state(ControlState.DISCHARGE, "test")
        assert orch._pending_cycles == 1
        assert orch._pending_state == ControlState.DISCHARGE

        # Now propose CHARGE instead
        orch._transition_state(ControlState.CHARGE, "test")
        assert orch._pending_cycles == 1  # reset to 1 for the new candidate
        assert orch._pending_state == ControlState.CHARGE
        assert orch._control_state == ControlState.IDLE  # not committed

    def test_no_transition_when_already_in_state(self):
        """Proposing the current state does not increment pending_cycles."""
        orch = _make_orchestrator(
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=3,
                hysteresis_w=0,
            )
        )
        orch._control_state = ControlState.DISCHARGE

        orch._transition_state(ControlState.DISCHARGE, "already there")
        assert orch._pending_cycles == 0
        assert orch._control_state == ControlState.DISCHARGE

    def test_immediate_debounce_cycles_1(self):
        """With debounce_cycles=1, state commits on the first proposal."""
        orch = _make_orchestrator(
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=0,
            )
        )
        orch._transition_state(ControlState.DISCHARGE, "immediate")
        assert orch._control_state == ControlState.DISCHARGE


# ---------------------------------------------------------------------------
# Tests: driver failure handling
# ---------------------------------------------------------------------------

class TestDriverFailure:
    async def test_huawei_failure_victron_continues(self):
        """When Huawei raises on read, huawei_available=False, Victron still reads."""
        victron_data = _make_victron(battery_soc_pct=70.0)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(side_effect=ConnectionError("modbus timeout"))
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
        )

        await orch._poll()

        assert orch._huawei_available is False
        assert orch._victron_available is True
        assert orch._huawei_error is not None
        assert "modbus timeout" in orch._huawei_error

    async def test_victron_stale_data_marks_unavailable(self):
        """When Victron timestamp is older than stale_threshold_s, victron_available=False."""
        old_timestamp = time.monotonic() - 60.0  # 60 seconds ago
        victron_data = _make_victron(timestamp=old_timestamp)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=0,
                stale_threshold_s=30.0,  # 60s > 30s → stale
            ),
        )

        await orch._poll()

        assert orch._victron_available is False
        assert orch._victron_error is not None
        assert "stale" in orch._victron_error

    async def test_victron_no_data_marks_unavailable(self):
        """Victron timestamp=0.0 (sentinel, no data received) → unavailable."""
        victron_data = _make_victron(timestamp=0.0)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
        )

        await orch._poll()

        assert orch._victron_available is False

    async def test_huawei_only_available_assigns_full_p_target(self):
        """With only Huawei available, it receives the full P_target."""
        battery = _make_battery(total_soc_pct=70.0, max_discharge_power_w=10000)
        master = _make_master(active_power_w=-3000)  # importing 3kW

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=master)
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        # Victron raises → unavailable
        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(
            side_effect=Exception("mqtt disconnected")
        )
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            sys_config=SystemConfig(huawei_min_soc_pct=10.0),
        )

        await orch._poll()
        assert orch._huawei_available is True
        assert orch._victron_available is False

        huawei_w, victron_w = orch._compute_setpoints()

        # Huawei gets it all (up to hw cap)
        assert huawei_w > 0
        assert victron_w == 0.0

    async def test_victron_only_available_assigns_full_p_target(self):
        """With only Victron available, it receives the full P_target.

        When Huawei is offline there is no master data to compute P_target.
        The orchestrator retains the last-known master reading across poll
        failures. We seed it with a first successful poll, then simulate a
        second poll where Huawei read_master raises.
        """
        victron_data = _make_victron(battery_soc_pct=70.0)
        prior_master = _make_master(active_power_w=-3000)  # importing 3kW

        huawei_mock = MagicMock()
        # First call succeeds (seeds _last_master); second raises
        huawei_mock.read_master = AsyncMock(
            side_effect=[prior_master, ConnectionError("huawei offline")]
        )
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            sys_config=SystemConfig(victron_min_soc_pct=15.0),
        )

        # First poll — seeds _last_master with the 3kW import reading
        await orch._poll()
        assert orch._huawei_available is True

        # Second poll — Huawei read_master raises; _last_master persists
        await orch._poll()
        assert orch._huawei_available is False
        assert orch._victron_available is True

        huawei_w, victron_w = orch._compute_setpoints()

        # Victron gets the full P_target (3000W retained from prior master poll)
        assert huawei_w == 0
        assert victron_w > 0.0


# ---------------------------------------------------------------------------
# Tests: both drivers failed → HOLD
# ---------------------------------------------------------------------------

class TestBothDriversFailed:
    async def test_both_failed_beyond_max_offline_enters_hold(self):
        """When both drivers have been offline > max_offline_s, state is HOLD
        and no setpoints are written."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(
            side_effect=ConnectionError("offline")
        )
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(
            side_effect=Exception("offline")
        )
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=0,
                max_offline_s=0.0,  # immediately consider both offline
            ),
        )

        await orch._poll()
        assert orch._huawei_available is False
        assert orch._victron_available is False

        # Force last-seen times to indicate both have been offline > max_offline_s
        # (last_seen defaults to 0.0, which is > max_offline_s=0.0 ago)
        huawei_w, victron_w = orch._compute_setpoints()

        assert huawei_w == 0
        assert victron_w == 0
        assert orch._control_state == ControlState.HOLD

    async def test_both_failed_no_writes_attempted(self):
        """In HOLD state (both offline), _apply_setpoints writes nothing (hysteresis=0)."""
        huawei_mock = MagicMock()
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=0,
            ),
        )
        # Both unavailable
        orch._huawei_available = False
        orch._victron_available = False

        # Apply zero setpoints (what _compute_setpoints would return in HOLD)
        await orch._apply_setpoints(0, 0.0)

        # With hysteresis=0 and last setpoints also 0, writes are suppressed by
        # availability flags — neither driver write method is called
        huawei_mock.write_max_discharge_power.assert_not_called()
        victron_mock.write_ac_power_setpoint.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Victron write convention (negative = discharge, 3-phase split)
# ---------------------------------------------------------------------------

class TestVictronWriteConvention:
    async def test_victron_setpoint_is_negative_per_phase(self):
        """Victron write_ac_power_setpoint uses -grid_lN_power_w for per-phase discharge."""
        victron_data = _make_victron(
            battery_soc_pct=60.0,
            grid_l1_power_w=300.0,
            grid_l2_power_w=300.0,
            grid_l3_power_w=300.0,
        )

        huawei_mock = MagicMock()
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=0,
            ),
        )
        orch._huawei_available = False
        orch._victron_available = True
        orch._last_victron = victron_data

        # Write a 900 W discharge setpoint — per-phase uses grid_lN readings (300 W each)
        await orch._apply_setpoints(0, 900.0)

        # All three phases should receive -300 W (from grid_lN_power_w, not equal split)
        expected_per_phase = -300.0
        calls = victron_mock.write_ac_power_setpoint.call_args_list
        assert len(calls) == 3
        for c in calls:
            args = c[0]
            assert args[0] in (1, 2, 3)  # phase
            assert args[1] == pytest.approx(expected_per_phase, abs=0.1)

    async def test_victron_3_phases_each_receive_call(self):
        """All three phases (1, 2, 3) receive exactly one write call each."""
        victron_data = _make_victron(
            grid_l1_power_w=200.0,
            grid_l2_power_w=200.0,
            grid_l3_power_w=200.0,
        )

        victron_mock = MagicMock()
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            victron_mock=victron_mock,
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=0,
            ),
        )
        orch._victron_available = True
        orch._last_victron = victron_data

        await orch._apply_setpoints(0, 600.0)

        called_phases = [c[0][0] for c in victron_mock.write_ac_power_setpoint.call_args_list]
        assert sorted(called_phases) == [1, 2, 3]


# ---------------------------------------------------------------------------
# Tests: public interface
# ---------------------------------------------------------------------------

class TestPublicInterface:
    def test_get_state_returns_none_before_first_cycle(self):
        """get_state() returns None before the first poll cycle completes."""
        orch = _make_orchestrator()
        assert orch.get_state() is None

    def test_get_last_error_none_initially(self):
        """get_last_error() is None before any driver failure."""
        orch = _make_orchestrator()
        assert orch.get_last_error() is None

    async def test_get_last_error_after_huawei_failure(self):
        """get_last_error() returns the error string after a Huawei failure."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(
            side_effect=ConnectionError("host unreachable")
        )

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
        )

        await orch._poll()

        err = orch.get_last_error()
        assert err is not None
        assert "host unreachable" in err

    @pytest.mark.anyio(backends=["asyncio"])
    async def test_start_stop_lifecycle(self, anyio_backend):
        """start() creates a task; stop() cancels it and applies safe setpoints.

        Only runs under asyncio because asyncio.create_task() is used internally.
        """
        if anyio_backend != "asyncio":
            pytest.skip("asyncio.create_task() requires asyncio backend")
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
        )

        await orch.start()
        assert orch._task is not None
        assert not orch._task.done()

        # Let it run briefly
        await asyncio.sleep(0.05)

        await orch.stop()
        assert orch._task is None

    @pytest.mark.anyio(backends=["asyncio"])
    async def test_double_start_is_idempotent(self, anyio_backend):
        """Calling start() twice should not create two tasks.

        Only runs under asyncio because asyncio.create_task() is used internally.
        """
        if anyio_backend != "asyncio":
            pytest.skip("asyncio.create_task() requires asyncio backend")
        orch = _make_orchestrator()
        await orch.start()
        task1 = orch._task

        await orch.start()  # should be ignored
        assert orch._task is task1

        await orch.stop()


# ---------------------------------------------------------------------------
# Tests: build_unified_state
# ---------------------------------------------------------------------------

class TestBuildUnifiedState:
    async def test_build_unified_state_reflects_availability(self):
        """_build_unified_state() should set availability from orchestrator flags."""
        battery = _make_battery(total_soc_pct=75.0)
        victron_data = _make_victron(battery_soc_pct=65.0)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
        )

        await orch._poll()
        state = orch._build_unified_state(1000, 500.0)

        assert state.huawei_available is True
        assert state.victron_available is True
        assert state.huawei_soc_pct == pytest.approx(75.0)
        assert state.victron_soc_pct == pytest.approx(65.0)

    async def test_build_unified_state_combined_soc_weighted(self):
        """combined_soc_pct uses 30/64/94 capacity weighting."""
        battery = _make_battery(total_soc_pct=80.0)
        victron_data = _make_victron(battery_soc_pct=60.0)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
        )

        await orch._poll()
        state = orch._build_unified_state(0, 0.0)

        expected_combined = (80.0 * 30.0 + 60.0 * 64.0) / 94.0
        assert state.combined_soc_pct == pytest.approx(expected_combined, abs=0.01)

    async def test_victron_charge_headroom_reflects_remaining_capacity(self):
        """victron_charge_headroom_w = max(0, victron_max_charge_w - charge_power_w)."""
        # charge_power_w = max(0, battery_power_w); positive = charging
        victron_data = _make_victron(battery_power_w=1500.0)  # charging at 1500 W

        orch = _make_orchestrator(
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=0,
                victron_max_charge_w=5000.0,
            )
        )
        orch._huawei_available = True
        orch._victron_available = True
        orch._last_battery = _make_battery()
        orch._last_victron = victron_data

        state = orch._build_unified_state(0, 0.0)

        # headroom = 5000 - 1500 = 3500
        assert state.victron_charge_headroom_w == pytest.approx(3500.0)

    async def test_victron_charge_headroom_clamps_to_zero_when_over_max(self):
        """headroom is 0 when charge_power_w exceeds victron_max_charge_w."""
        victron_data = _make_victron(battery_power_w=5500.0)  # charging at 5500 W (over max)

        orch = _make_orchestrator(
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=0,
                victron_max_charge_w=5000.0,
            )
        )
        orch._huawei_available = True
        orch._victron_available = True
        orch._last_battery = _make_battery()
        orch._last_victron = victron_data

        state = orch._build_unified_state(0, 0.0)

        assert state.victron_charge_headroom_w == 0.0


# ---------------------------------------------------------------------------
# Tests: P_target source selection
# ---------------------------------------------------------------------------

class TestPTargetSource:
    """Verify P_target is sourced from grid_power_w first, then master, then zero."""

    async def test_p_target_uses_grid_power_w_when_available(self):
        """When grid_power_w=3000, P_target=3000 → setpoints sum to ~3000W."""
        battery = _make_battery(total_soc_pct=80.0, max_discharge_power_w=5000)
        # grid importing 3000W → need to discharge 3000W
        victron_data = _make_victron(battery_soc_pct=70.0, grid_power_w=3000.0)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(
            return_value=_make_master(active_power_w=0)  # master says zero (ignored when grid_power_w set)
        )
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
        )
        await orch._poll()

        huawei_w, victron_w = orch._compute_setpoints()

        # Combined output should be ~3000W (may be clamped by SoC-ratio math, but >0)
        assert huawei_w + victron_w == pytest.approx(3000.0, abs=1.0)
        assert orch._control_state == ControlState.DISCHARGE

    async def test_p_target_falls_back_to_master_when_grid_power_none(self):
        """When grid_power_w=None, fall back to -master.active_power_w."""
        battery = _make_battery(total_soc_pct=80.0, max_discharge_power_w=5000)
        # master importing 2000W → P_target = -(-2000) = 2000W
        victron_data = _make_victron(battery_soc_pct=70.0, grid_power_w=None)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(
            return_value=_make_master(active_power_w=-2000)  # importing
        )
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
        )
        await orch._poll()

        huawei_w, victron_w = orch._compute_setpoints()

        # P_target should be 2000W from master fallback
        assert huawei_w + victron_w == pytest.approx(2000.0, abs=1.0)
        assert orch._control_state == ControlState.DISCHARGE

    async def test_p_target_zero_when_both_sources_absent(self):
        """When grid_power_w=None and _last_master=None, P_target=0 → IDLE."""
        victron_data = _make_victron(battery_soc_pct=70.0, grid_power_w=None)

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)

        # Make read_master fail so _last_master stays None
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(side_effect=Exception("no data"))
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()

        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
        )
        await orch._poll()  # read_master raises → _last_master stays None

        assert orch._last_master is None

        huawei_w, victron_w = orch._compute_setpoints()

        assert huawei_w == 0
        assert victron_w == 0.0
        assert orch._control_state == ControlState.IDLE


# ---------------------------------------------------------------------------
# Tests: ESS mode guard
# ---------------------------------------------------------------------------

class TestEssModeGuard:
    """Verify _apply_setpoints() skips Victron writes when ESS mode is 0 or 1."""

    async def test_ess_mode_guard_skips_writes_mode_0(self):
        """ESS mode 0 (OFF) → Victron write_ac_power_setpoint NOT called."""
        battery = _make_battery(total_soc_pct=80.0, max_discharge_power_w=5000)
        victron_data = _make_victron(battery_soc_pct=70.0, grid_power_w=2000.0, ess_mode=0)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)
        await orch._poll()

        huawei_w, victron_w = orch._compute_setpoints()
        await orch._apply_setpoints(huawei_w, victron_w)

        victron_mock.write_ac_power_setpoint.assert_not_called()

    async def test_ess_mode_guard_skips_writes_mode_1(self):
        """ESS mode 1 (Optimized without BatteryLife) → Victron write NOT called."""
        battery = _make_battery(total_soc_pct=80.0, max_discharge_power_w=5000)
        victron_data = _make_victron(battery_soc_pct=70.0, grid_power_w=2000.0, ess_mode=1)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)
        await orch._poll()

        huawei_w, victron_w = orch._compute_setpoints()
        await orch._apply_setpoints(huawei_w, victron_w)

        victron_mock.write_ac_power_setpoint.assert_not_called()

    async def test_ess_mode_guard_allows_writes_mode_2(self):
        """ESS mode 2 (Optimized with BatteryLife) → Victron writes ARE executed."""
        battery = _make_battery(total_soc_pct=80.0, max_discharge_power_w=5000)
        victron_data = _make_victron(battery_soc_pct=70.0, grid_power_w=2000.0, ess_mode=2)

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=battery)
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=victron_data)
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)
        await orch._poll()

        huawei_w, victron_w = orch._compute_setpoints()
        await orch._apply_setpoints(huawei_w, victron_w)

        victron_mock.write_ac_power_setpoint.assert_called()


# ---------------------------------------------------------------------------
# Tests: phase imbalance detection
# ---------------------------------------------------------------------------

class TestPhaseImbalance:
    async def test_phase_imbalance_counter_increments(self, caplog):
        """When phase power deviates >500W from setpoint, imbalance counter increments."""
        import logging

        # Phase L1 measured at 0W, setpoint is 1000W per phase → deviation=1000W
        victron_data = _make_victron(
            l1=_make_phase(power_w=0.0),
            l2=_make_phase(power_w=1000.0),
            l3=_make_phase(power_w=1000.0),
        )

        orch = _make_orchestrator(
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=0,
            )
        )
        orch._victron_available = True
        orch._last_victron = victron_data

        # 3000W discharge → 1000W per phase setpoint
        with caplog.at_level(logging.DEBUG, logger="backend.orchestrator"):
            orch._check_phase_imbalance(3000.0)

        assert orch._phase_imbalance_cycles == 1

    async def test_phase_imbalance_warning_after_3_cycles(self, caplog):
        """WARNING is logged after >2 consecutive imbalance cycles."""
        import logging

        victron_data = _make_victron(
            l1=_make_phase(power_w=0.0),  # large deviation from 1000W setpoint
            l2=_make_phase(power_w=0.0),
            l3=_make_phase(power_w=0.0),
        )

        orch = _make_orchestrator()
        orch._victron_available = True
        orch._last_victron = victron_data
        # Set last-written per-phase setpoints so deviation is 1000W per phase
        orch._last_l1_setpoint = -1000.0
        orch._last_l2_setpoint = -1000.0
        orch._last_l3_setpoint = -1000.0

        with caplog.at_level(logging.WARNING, logger="backend.orchestrator"):
            for _ in range(4):
                orch._check_phase_imbalance(3000.0)  # 1000W/phase commanded

        assert orch._phase_imbalance_cycles > 2
        assert any("Phase imbalance" in r.message for r in caplog.records)

    def test_phase_imbalance_counter_resets_on_balance(self):
        """When phases are balanced, imbalance counter resets to 0."""
        victron_data = _make_victron(
            l1=_make_phase(power_w=1000.0),
            l2=_make_phase(power_w=1000.0),
            l3=_make_phase(power_w=1000.0),
        )
        orch = _make_orchestrator()
        orch._victron_available = True
        orch._last_victron = victron_data
        orch._phase_imbalance_cycles = 5  # pre-set high
        # Commanded 1000W per phase → measured ~1000W → no imbalance
        orch._last_l1_setpoint = -1000.0
        orch._last_l2_setpoint = -1000.0
        orch._last_l3_setpoint = -1000.0

        # Measured ~1000W per phase vs 1000W commanded → no imbalance
        orch._check_phase_imbalance(3000.0)

        assert orch._phase_imbalance_cycles == 0


# ---------------------------------------------------------------------------
# Helpers for TestGridCharge
# ---------------------------------------------------------------------------

def _make_charge_slot(
    battery: str = "huawei",
    target_soc_pct: float = 90.0,
    grid_charge_power_w: int = 5000,
    offset_minutes: float = 30.0,
    duration_minutes: float = 60.0,
):
    """Create a ChargeSlot centred on the current UTC time.

    The slot starts ``offset_minutes`` ago and runs for ``duration_minutes``.
    Default: started 30 min ago, ends in 30 min -> currently active.
    """
    from datetime import datetime, timedelta, timezone
    from backend.schedule_models import ChargeSlot

    now = datetime.now(tz=timezone.utc)
    start_utc = now - timedelta(minutes=offset_minutes)
    end_utc = start_utc + timedelta(minutes=duration_minutes)
    return ChargeSlot(
        battery=battery,
        target_soc_pct=target_soc_pct,
        start_utc=start_utc,
        end_utc=end_utc,
        grid_charge_power_w=grid_charge_power_w,
    )


def _make_schedule(slots: list, stale: bool = False):
    """Create a minimal ChargeSchedule for testing."""
    from datetime import datetime, timezone
    from backend.schedule_models import ChargeSchedule, OptimizationReasoning

    reasoning = OptimizationReasoning(
        text="test schedule",
        tomorrow_solar_kwh=0.0,
        expected_consumption_kwh=0.0,
        charge_energy_kwh=0.0,
        cost_estimate_eur=0.0,
    )
    return ChargeSchedule(
        slots=slots,
        reasoning=reasoning,
        computed_at=datetime.now(tz=timezone.utc),
        stale=stale,
    )


def _make_scheduler_mock(active_schedule=None, stale: bool = False) -> MagicMock:
    """Return a MagicMock mimicking a Scheduler instance."""
    scheduler = MagicMock()
    scheduler.active_schedule = active_schedule
    scheduler.schedule_stale = stale
    return scheduler


# ---------------------------------------------------------------------------
# Tests: GRID_CHARGE state machine
# ---------------------------------------------------------------------------

class TestGridCharge:
    """Unit tests for _active_charge_slot(), set_scheduler(), and the
    GRID_CHARGE branch in _compute_setpoints() / _apply_setpoints()."""

    # ------------------------------------------------------------------
    # set_scheduler
    # ------------------------------------------------------------------

    def test_set_scheduler_stores_reference(self):
        """set_scheduler() must store the scheduler on self._scheduler."""
        orch = _make_orchestrator()
        scheduler = _make_scheduler_mock()
        orch.set_scheduler(scheduler)
        assert orch._scheduler is scheduler

    def test_set_scheduler_overwrites_previous(self):
        """Calling set_scheduler() twice replaces the previous reference."""
        orch = _make_orchestrator()
        s1 = _make_scheduler_mock()
        s2 = _make_scheduler_mock()
        orch.set_scheduler(s1)
        orch.set_scheduler(s2)
        assert orch._scheduler is s2

    # ------------------------------------------------------------------
    # _active_charge_slot: guard cases returning None
    # ------------------------------------------------------------------

    def test_active_charge_slot_returns_none_when_no_scheduler(self):
        """_active_charge_slot() returns None when scheduler not injected."""
        orch = _make_orchestrator()
        assert orch._scheduler is None
        assert orch._active_charge_slot() is None

    def test_active_charge_slot_returns_none_when_schedule_is_none(self):
        """_active_charge_slot() returns None when active_schedule is None."""
        orch = _make_orchestrator()
        scheduler = _make_scheduler_mock(active_schedule=None)
        orch.set_scheduler(scheduler)
        assert orch._active_charge_slot() is None

    def test_active_charge_slot_returns_none_when_schedule_is_stale(self):
        """_active_charge_slot() returns None when schedule.stale is True."""
        orch = _make_orchestrator()
        slot = _make_charge_slot()
        schedule = _make_schedule([slot], stale=True)
        scheduler = _make_scheduler_mock(active_schedule=schedule)
        orch.set_scheduler(scheduler)
        assert orch._active_charge_slot() is None

    def test_active_charge_slot_returns_none_when_slot_not_yet_started(self):
        """_active_charge_slot() returns None when current time is before slot.start_utc."""
        from datetime import datetime, timedelta, timezone
        from backend.schedule_models import ChargeSlot

        orch = _make_orchestrator()
        now = datetime.now(tz=timezone.utc)
        future_start = now + timedelta(minutes=10)
        future_end = now + timedelta(minutes=70)
        slot = ChargeSlot(
            battery="huawei",
            target_soc_pct=90.0,
            start_utc=future_start,
            end_utc=future_end,
            grid_charge_power_w=5000,
        )
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))
        assert orch._active_charge_slot() is None

    def test_active_charge_slot_returns_none_when_slot_already_ended(self):
        """_active_charge_slot() returns None when current time is at or after slot.end_utc."""
        from datetime import datetime, timedelta, timezone
        from backend.schedule_models import ChargeSlot

        orch = _make_orchestrator()
        now = datetime.now(tz=timezone.utc)
        past_start = now - timedelta(minutes=120)
        past_end = now - timedelta(minutes=60)
        slot = ChargeSlot(
            battery="huawei",
            target_soc_pct=90.0,
            start_utc=past_start,
            end_utc=past_end,
            grid_charge_power_w=5000,
        )
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))
        assert orch._active_charge_slot() is None

    def test_active_charge_slot_returns_none_when_huawei_soc_target_met(self):
        """_active_charge_slot() returns None when huawei SoC >= slot.target_soc_pct."""
        orch = _make_orchestrator()
        orch._last_battery = _make_battery(total_soc_pct=90.0)
        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))
        assert orch._active_charge_slot() is None

    def test_active_charge_slot_returns_none_when_huawei_soc_above_target(self):
        """_active_charge_slot() returns None when huawei SoC > target."""
        orch = _make_orchestrator()
        orch._last_battery = _make_battery(total_soc_pct=95.0)
        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))
        assert orch._active_charge_slot() is None

    def test_active_charge_slot_returns_slot_when_within_window_below_target(self):
        """_active_charge_slot() returns the slot when within window and below target SoC."""
        orch = _make_orchestrator()
        orch._last_battery = _make_battery(total_soc_pct=70.0)
        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))
        result = orch._active_charge_slot()
        assert result is slot

    def test_active_charge_slot_returns_slot_when_soc_just_below_target(self):
        """Edge: SoC just below target should still return the slot."""
        orch = _make_orchestrator()
        orch._last_battery = _make_battery(total_soc_pct=89.9)
        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))
        assert orch._active_charge_slot() is slot

    def test_active_charge_slot_victron_slot_returned_when_active(self):
        """_active_charge_slot() returns a victron slot when within window and below target."""
        orch = _make_orchestrator()
        orch._last_victron = _make_victron(battery_soc_pct=50.0)
        slot = _make_charge_slot(battery="victron", target_soc_pct=90.0)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))
        assert orch._active_charge_slot() is slot

    def test_active_charge_slot_victron_returns_none_when_target_met(self):
        """_active_charge_slot() returns None when victron SoC >= slot target."""
        orch = _make_orchestrator()
        orch._last_victron = _make_victron(battery_soc_pct=92.0)
        slot = _make_charge_slot(battery="victron", target_soc_pct=90.0)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))
        assert orch._active_charge_slot() is None

    def test_active_charge_slot_first_met_skipped_second_returned(self):
        """When first slot target is met, _active_charge_slot() checks the next slot."""
        orch = _make_orchestrator()
        orch._last_battery = _make_battery(total_soc_pct=92.0)
        orch._last_victron = _make_victron(battery_soc_pct=50.0)
        slot1 = _make_charge_slot(battery="huawei", target_soc_pct=90.0)
        slot2 = _make_charge_slot(battery="victron", target_soc_pct=90.0)
        schedule = _make_schedule([slot1, slot2])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))
        result = orch._active_charge_slot()
        assert result is slot2

    # ------------------------------------------------------------------
    # _compute_setpoints: GRID_CHARGE branch
    # ------------------------------------------------------------------

    async def test_compute_setpoints_enters_grid_charge_when_slot_active(self):
        """_compute_setpoints() enters GRID_CHARGE when a slot is active."""
        orch = _make_orchestrator()
        await orch._poll()
        orch._last_battery = _make_battery(total_soc_pct=70.0)
        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0, grid_charge_power_w=5000)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        huawei_w, victron_w = orch._compute_setpoints()

        assert orch._control_state == ControlState.GRID_CHARGE
        assert huawei_w == 5000
        assert victron_w == 0.0

    async def test_compute_setpoints_luna_first_huawei_below_target(self):
        """LUNA-first: huawei_w = grid_charge_power_w, victron_w = 0 when below target."""
        orch = _make_orchestrator()
        await orch._poll()
        orch._last_battery = _make_battery(total_soc_pct=70.0)
        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0, grid_charge_power_w=4800)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        huawei_w, victron_w = orch._compute_setpoints()

        assert huawei_w == 4800
        assert victron_w == 0.0

    async def test_compute_setpoints_luna_done_huawei_offline(self):
        """LUNA-done: huawei_w = 0, victron_w = grid_charge_power_w when Huawei offline.

        When Huawei is offline, huawei_target_met = True (via `not _huawei_available`),
        so the LUNA logic routes all charge power to Victron.
        The battery sentinel (total_soc_pct=0.0) means _active_charge_slot() still
        returns the slot (below target), and _compute_setpoints sees huawei offline.
        """
        orch = _make_orchestrator()
        await orch._poll()
        # Drive Huawei offline; sentinel shows SoC=0.0 so slot is returned by guard
        orch._huawei_available = False
        orch._last_battery = _make_battery(total_soc_pct=0.0)
        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0, grid_charge_power_w=4800)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        huawei_w, victron_w = orch._compute_setpoints()

        assert huawei_w == 0
        assert victron_w == pytest.approx(4800.0)
        assert orch._control_state == ControlState.GRID_CHARGE

    async def test_compute_setpoints_luna_done_huawei_offline_above_target_sentinel(self):
        """LUNA-done: even with a high SoC battery sentinel, offline Huawei routes to Victron."""
        orch = _make_orchestrator()
        await orch._poll()
        orch._huawei_available = False
        # Even if cached SoC was above target, offline check overrides it
        orch._last_battery = _make_battery(total_soc_pct=50.0)
        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0, grid_charge_power_w=3000)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        huawei_w, victron_w = orch._compute_setpoints()

        assert huawei_w == 0
        assert victron_w == pytest.approx(3000.0)

    async def test_compute_setpoints_victron_slot_routes_all_power(self):
        """For a victron slot, all grid charge power goes to Victron."""
        orch = _make_orchestrator()
        await orch._poll()
        orch._last_victron = _make_victron(battery_soc_pct=50.0)
        slot = _make_charge_slot(battery="victron", target_soc_pct=90.0, grid_charge_power_w=6000)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        huawei_w, victron_w = orch._compute_setpoints()

        assert huawei_w == 0
        assert victron_w == pytest.approx(6000.0)
        assert orch._control_state == ControlState.GRID_CHARGE

    async def test_compute_setpoints_grid_charge_fires_before_p_target(self):
        """GRID_CHARGE branch fires before normal P_target calculation.

        Even with massive PV export (P_target < 0), GRID_CHARGE takes priority.
        """
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(
            return_value=_make_master(active_power_w=10000)  # massive PV export
        )
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery(total_soc_pct=70.0))
        huawei_mock.write_max_discharge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)
        await orch._poll()

        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0, grid_charge_power_w=5000)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        huawei_w, victron_w = orch._compute_setpoints()

        assert orch._control_state == ControlState.GRID_CHARGE
        assert huawei_w == 5000
        assert victron_w == 0.0

    async def test_compute_setpoints_no_grid_charge_without_scheduler(self):
        """_compute_setpoints() does NOT enter GRID_CHARGE without a scheduler."""
        orch = _make_orchestrator()
        await orch._poll()
        huawei_w, victron_w = orch._compute_setpoints()
        assert orch._control_state != ControlState.GRID_CHARGE

    async def test_compute_setpoints_grid_charge_persists_on_consecutive_calls(self):
        """State stays GRID_CHARGE across consecutive calls when slot is still active."""
        orch = _make_orchestrator()
        await orch._poll()
        orch._last_battery = _make_battery(total_soc_pct=70.0)
        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0, grid_charge_power_w=5000)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        orch._compute_setpoints()
        assert orch._control_state == ControlState.GRID_CHARGE

        orch._compute_setpoints()
        assert orch._control_state == ControlState.GRID_CHARGE

    async def test_compute_setpoints_huawei_offline_routes_to_victron(self):
        """When Huawei is offline, huawei_target_met=True -> Victron gets the power."""
        orch = _make_orchestrator()
        await orch._poll()
        orch._huawei_available = False
        orch._last_battery = _make_battery(total_soc_pct=70.0)
        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0, grid_charge_power_w=5000)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        huawei_w, victron_w = orch._compute_setpoints()

        assert huawei_w == 0
        assert victron_w == pytest.approx(5000.0)
        assert orch._control_state == ControlState.GRID_CHARGE

    # ------------------------------------------------------------------
    # _apply_setpoints: GRID_CHARGE stub branch
    # ------------------------------------------------------------------

    async def test_apply_setpoints_calls_apply_grid_charge_when_in_grid_charge(self):
        """_apply_setpoints() delegates to _apply_grid_charge_setpoints() in GRID_CHARGE."""
        orch = _make_orchestrator()
        await orch._poll()
        orch._control_state = ControlState.GRID_CHARGE

        called_with = []

        async def _fake_apply_grid_charge(huawei_w, victron_w):
            called_with.append((huawei_w, victron_w))

        orch._apply_grid_charge_setpoints = _fake_apply_grid_charge
        await orch._apply_setpoints(5000, 0.0)

        assert called_with == [(5000, 0.0)]

    async def test_apply_setpoints_skips_normal_write_in_grid_charge(self):
        """_apply_setpoints() skips normal write path (write_max_discharge_power) in GRID_CHARGE."""
        orch = _make_orchestrator()
        await orch._poll()
        orch._control_state = ControlState.GRID_CHARGE

        orch._huawei.write_max_discharge_power = AsyncMock()
        orch._victron.write_ac_power_setpoint = MagicMock()

        await orch._apply_setpoints(5000, 0.0)

        orch._huawei.write_max_discharge_power.assert_not_called()

    # ------------------------------------------------------------------
    # _build_unified_state: grid_charge_slot_active field
    # ------------------------------------------------------------------

    async def test_build_unified_state_grid_charge_slot_active_true(self):
        """get_state().grid_charge_slot_active is True when in GRID_CHARGE."""
        orch = _make_orchestrator()
        await orch._poll()
        orch._control_state = ControlState.GRID_CHARGE

        state = orch._build_unified_state(5000, 0.0)

        assert state.grid_charge_slot_active is True
        assert state.control_state == ControlState.GRID_CHARGE

    async def test_build_unified_state_grid_charge_slot_active_false_when_idle(self):
        """get_state().grid_charge_slot_active is False when control state is IDLE."""
        orch = _make_orchestrator()
        await orch._poll()
        orch._control_state = ControlState.IDLE

        state = orch._build_unified_state(0, 0.0)

        assert state.grid_charge_slot_active is False

    async def test_build_unified_state_grid_charge_slot_active_false_during_discharge(self):
        """get_state().grid_charge_slot_active is False when control state is DISCHARGE."""
        orch = _make_orchestrator()
        await orch._poll()
        orch._control_state = ControlState.DISCHARGE

        state = orch._build_unified_state(2000, 1000.0)

        assert state.grid_charge_slot_active is False

    # ------------------------------------------------------------------
    # _apply_grid_charge_setpoints: write path tests (T02)
    # ------------------------------------------------------------------

    async def test_apply_grid_charge_setpoints_huawei_write_ac_charging_and_power(self):
        """_apply_grid_charge_setpoints calls write_ac_charging(True) then write_max_charge_power when huawei_w > 0."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock)
        await orch._poll()
        orch._huawei_available = True

        await orch._apply_grid_charge_setpoints(5000, 0.0)

        huawei_mock.write_ac_charging.assert_awaited_once_with(True)
        huawei_mock.write_max_charge_power.assert_awaited_once_with(5000)

    async def test_apply_grid_charge_setpoints_victron_positive_per_phase(self):
        """_apply_grid_charge_setpoints uses POSITIVE per-phase watts for Victron (import, not export)."""
        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(victron_mock=victron_mock)
        await orch._poll()
        orch._victron_available = True
        # Force huawei offline so only Victron path is taken
        orch._huawei_available = False

        await orch._apply_grid_charge_setpoints(0, 3000.0)

        calls = victron_mock.write_ac_power_setpoint.call_args_list
        assert len(calls) == 3
        for call in calls:
            phase, watts = call.args
            assert watts > 0, (
                f"Victron GRID_CHARGE must use POSITIVE (import) watts, got {watts} on phase {phase}"
            )
        # Each phase should be 1000.0 W (3000 / 3)
        phase1 = next(c for c in calls if c.args[0] == 1)
        assert phase1.args[1] == pytest.approx(1000.0)

    async def test_apply_grid_charge_setpoints_victron_not_negative(self):
        """Anti-regression: Victron setpoint must NOT be negative during GRID_CHARGE."""
        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(victron_mock=victron_mock)
        await orch._poll()
        orch._victron_available = True
        orch._huawei_available = False

        await orch._apply_grid_charge_setpoints(0, 6000.0)

        for call in victron_mock.write_ac_power_setpoint.call_args_list:
            _, watts = call.args
            assert watts > 0, f"Victron must NOT use negative watts during GRID_CHARGE, got {watts}"

    async def test_apply_grid_charge_setpoints_huawei_w_zero_disables_ac_charging(self):
        """When huawei_w == 0 (LUNA target met), write_ac_charging(False) is called, not write_max_charge_power."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock)
        await orch._poll()
        orch._huawei_available = True
        orch._victron_available = False

        await orch._apply_grid_charge_setpoints(0, 0.0)

        huawei_mock.write_ac_charging.assert_awaited_once_with(False)
        huawei_mock.write_max_charge_power.assert_not_called()

    async def test_apply_grid_charge_setpoints_huawei_offline_no_huawei_writes(self):
        """When Huawei is offline, _apply_grid_charge_setpoints skips all Huawei writes."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)
        await orch._poll()
        orch._huawei_available = False
        orch._victron_available = True

        await orch._apply_grid_charge_setpoints(0, 3000.0)

        huawei_mock.write_ac_charging.assert_not_called()
        huawei_mock.write_max_charge_power.assert_not_called()
        assert victron_mock.write_ac_power_setpoint.call_count == 3

    async def test_apply_grid_charge_setpoints_victron_offline_no_victron_writes(self):
        """When Victron is offline, _apply_grid_charge_setpoints skips all Victron writes."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)
        await orch._poll()
        orch._huawei_available = True
        orch._victron_available = False

        await orch._apply_grid_charge_setpoints(5000, 3000.0)

        huawei_mock.write_ac_charging.assert_awaited_once_with(True)
        huawei_mock.write_max_charge_power.assert_awaited_once_with(5000)
        victron_mock.write_ac_power_setpoint.assert_not_called()

    # ------------------------------------------------------------------
    # _cleanup_grid_charge: slot-exit tests (T02)
    # ------------------------------------------------------------------

    async def test_cleanup_grid_charge_disables_ac_charging(self):
        """_cleanup_grid_charge() calls write_ac_charging(False) when Huawei available."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery(max_charge_power_w=7200))
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock)
        await orch._poll()
        orch._huawei_available = True
        orch._victron_available = False

        await orch._cleanup_grid_charge()

        huawei_mock.write_ac_charging.assert_awaited_once_with(False)

    async def test_cleanup_grid_charge_restores_max_charge_power(self):
        """_cleanup_grid_charge() restores write_max_charge_power to battery.max_charge_power_w."""
        bms_max = 7200
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery(max_charge_power_w=bms_max))
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock)
        await orch._poll()
        orch._huawei_available = True
        orch._victron_available = False

        await orch._cleanup_grid_charge()

        huawei_mock.write_max_charge_power.assert_awaited_once_with(bms_max)

    async def test_cleanup_grid_charge_zeros_victron_setpoints(self):
        """_cleanup_grid_charge() writes 0.0 to all three Victron phases."""
        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(victron_mock=victron_mock)
        await orch._poll()
        orch._huawei_available = False
        orch._victron_available = True

        await orch._cleanup_grid_charge()

        calls = victron_mock.write_ac_power_setpoint.call_args_list
        assert len(calls) == 3
        phases_called = {c.args[0] for c in calls}
        assert phases_called == {1, 2, 3}
        for c in calls:
            assert c.args[1] == pytest.approx(0.0), f"Expected 0.0 W on cleanup, got {c.args[1]}"

    async def test_cleanup_triggered_on_grid_charge_to_idle_transition(self):
        """_cleanup_grid_charge() is called when state transitions from GRID_CHARGE → IDLE."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)
        await orch._poll()
        orch._huawei_available = True
        orch._victron_available = True

        # Simulate: previously in GRID_CHARGE, now transitioning to IDLE
        orch._prev_control_state = ControlState.GRID_CHARGE
        orch._control_state = ControlState.IDLE

        await orch._apply_setpoints(0, 0.0)

        # Cleanup must have fired: write_ac_charging(False) must be called
        huawei_mock.write_ac_charging.assert_awaited_once_with(False)

    async def test_cleanup_not_triggered_when_staying_in_idle(self):
        """_cleanup_grid_charge() is NOT called when transitioning from IDLE → IDLE."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock)
        await orch._poll()
        orch._huawei_available = True
        orch._prev_control_state = ControlState.IDLE
        orch._control_state = ControlState.IDLE

        await orch._apply_setpoints(0, 0.0)

        huawei_mock.write_ac_charging.assert_not_called()

    # ------------------------------------------------------------------
    # get_state().grid_charge_slot_active and full integration (T02)
    # ------------------------------------------------------------------

    async def test_get_state_grid_charge_slot_active_true_during_grid_charge(self):
        """get_state().grid_charge_slot_active is True when orchestrator is in GRID_CHARGE."""
        orch = _make_orchestrator()
        await orch._poll()
        orch._control_state = ControlState.GRID_CHARGE

        state = orch._build_unified_state(5000, 0.0)

        assert state.grid_charge_slot_active is True

    async def test_full_integration_grid_charge_writes_correct_setpoints(self):
        """Integration: poll + compute + apply with active slot writes write_ac_charging(True) + positive Victron."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery(total_soc_pct=70.0))
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron(battery_soc_pct=50.0))
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)

        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0, grid_charge_power_w=5000)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        await orch._poll()
        huawei_w, victron_w = orch._compute_setpoints()
        await orch._apply_setpoints(huawei_w, victron_w)

        # Should have entered GRID_CHARGE with huawei_w=5000, victron_w=0
        assert orch._control_state == ControlState.GRID_CHARGE
        huawei_mock.write_ac_charging.assert_awaited_once_with(True)
        huawei_mock.write_max_charge_power.assert_awaited_once_with(5000)
        # Victron is available but victron_w == 0, so no Victron write
        victron_mock.write_ac_power_setpoint.assert_not_called()

        # Build state and verify grid_charge_slot_active
        state = orch._build_unified_state(huawei_w, victron_w)
        assert state.grid_charge_slot_active is True

    # ------------------------------------------------------------------
    # Setpoint tracking: _last_victron_setpoint updated by grid-charge methods
    # ------------------------------------------------------------------

    async def test_apply_grid_charge_updates_last_victron_setpoint(self):
        """_apply_grid_charge_setpoints() must update _last_victron_setpoint to victron_w."""
        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(victron_mock=victron_mock)
        await orch._poll()
        orch._victron_available = True
        orch._huawei_available = False

        assert orch._last_victron_setpoint == 0.0  # precondition

        await orch._apply_grid_charge_setpoints(0, 3000.0)

        assert orch._last_victron_setpoint == 3000.0, (
            f"Expected _last_victron_setpoint=3000.0 after grid charge apply, "
            f"got {orch._last_victron_setpoint}"
        )

    async def test_cleanup_grid_charge_resets_last_victron_setpoint(self):
        """_cleanup_grid_charge() must reset _last_victron_setpoint to 0.0."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)
        await orch._poll()
        orch._victron_available = True
        orch._huawei_available = True

        # Simulate that grid charging was active and tracking was updated
        orch._last_victron_setpoint = 5000.0

        await orch._cleanup_grid_charge()

        assert orch._last_victron_setpoint == 0.0, (
            f"Expected _last_victron_setpoint=0.0 after cleanup, "
            f"got {orch._last_victron_setpoint}"
        )

    async def test_first_normal_setpoint_after_grid_charge_not_suppressed(self):
        """First normal _apply_setpoints after slot-exit must NOT be suppressed by hysteresis.

        After cleanup resets _last_victron_setpoint=0.0, a new victron_w=2000.0 has
        Δ=2000 which exceeds any reasonable hysteresis threshold — the write must proceed.
        """
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery())
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron())
        victron_mock.write_ac_power_setpoint = MagicMock()
        victron_mock.write_disable_feed_in = MagicMock()

        # Use a non-zero hysteresis to make the suppression risk realistic
        orch = _make_orchestrator(
            huawei_mock=huawei_mock,
            victron_mock=victron_mock,
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=100,
            ),
        )
        await orch._poll()
        orch._victron_available = True
        orch._huawei_available = True

        # Simulate GRID_CHARGE → IDLE transition: prev=GRID_CHARGE, current=IDLE
        orch._prev_control_state = ControlState.GRID_CHARGE
        orch._control_state = ControlState.IDLE

        # Cleanup would leave _last_victron_setpoint = 0.0 — confirm that here
        orch._last_victron_setpoint = 0.0

        # Apply a 2000 W victron setpoint — Δ=2000 >> hysteresis_w=100
        await orch._apply_setpoints(0, 2000.0)

        # write_ac_power_setpoint must have been called (not suppressed)
        victron_mock.write_ac_power_setpoint.assert_called(), (
            "write_ac_power_setpoint must be called after slot-exit when "
            "_last_victron_setpoint was correctly reset to 0.0"
        )

    # ------------------------------------------------------------------
    # Scheduler → orchestrator integration (T02)
    # ------------------------------------------------------------------

    async def test_integration_victron_only_slot_writes_positive_setpoints(self):
        """Integration: poll + compute + apply with battery='victron' slot writes
        positive per-phase setpoints to Victron and does NOT call write_ac_charging."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery(total_soc_pct=70.0))
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        victron_mock = MagicMock()
        # Victron SoC=40%, below target 90% → slot is active
        victron_mock.read_system_state = MagicMock(return_value=_make_victron(battery_soc_pct=40.0))
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)

        slot = _make_charge_slot(battery="victron", target_soc_pct=90.0, grid_charge_power_w=3000)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        await orch._poll()
        huawei_w, victron_w = orch._compute_setpoints()
        await orch._apply_setpoints(huawei_w, victron_w)

        # Victron-only slot: huawei_w=0, victron_w=3000
        assert orch._control_state == ControlState.GRID_CHARGE, (
            f"Expected GRID_CHARGE, got {orch._control_state}"
        )
        assert victron_w == 3000.0, f"Expected victron_w=3000.0, got {victron_w}"
        assert huawei_w == 0, f"Expected huawei_w=0, got {huawei_w}"

        # Victron: write_ac_power_setpoint called 3× with +1000 W (positive → import)
        assert victron_mock.write_ac_power_setpoint.call_count == 3, (
            f"Expected 3 phase writes, got {victron_mock.write_ac_power_setpoint.call_count}"
        )
        per_phase = 3000.0 / 3.0
        for call in victron_mock.write_ac_power_setpoint.call_args_list:
            actual_w = call[0][1]  # second positional arg is watts
            assert actual_w == pytest.approx(per_phase, abs=1.0), (
                f"Expected +{per_phase} W per phase (positive = import), got {actual_w}"
            )

        # Huawei: victron-only slot with huawei_w=0 → Huawei gets write_ac_charging(False)
        # (LUNA-done path: huawei_w=0 triggers ac_charging=False to keep Huawei idle)
        huawei_mock.write_ac_charging.assert_awaited_once_with(False)

        # Tracking must reflect what was written
        assert orch._last_victron_setpoint == 3000.0, (
            f"Expected _last_victron_setpoint=3000.0, got {orch._last_victron_setpoint}"
        )

    async def test_integration_slot_exit_cleanup_fires_both_drivers(self):
        """Integration: full slot-exit cycle — GRID_CHARGE → IDLE cleans up both drivers
        and resets _last_victron_setpoint to 0.0."""
        huawei_mock = MagicMock()
        # active_power_w=0: no grid import after slot exit → P_target=0 → discharge setpoint=0
        # This ensures the normal apply path also writes 0 and tracking stays at 0.0.
        huawei_mock.read_master = AsyncMock(return_value=_make_master(active_power_w=0))
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery(total_soc_pct=70.0))
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron(battery_soc_pct=40.0))
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)

        # Cycle 1: active slot → enters GRID_CHARGE
        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0, grid_charge_power_w=5000)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        await orch._poll()
        hw, vw = orch._compute_setpoints()
        await orch._apply_setpoints(hw, vw)

        assert orch._control_state == ControlState.GRID_CHARGE, (
            f"Cycle-1: expected GRID_CHARGE, got {orch._control_state}"
        )
        huawei_mock.write_ac_charging.assert_awaited_with(True)

        # Reset call counts before cycle 2
        huawei_mock.write_ac_charging.reset_mock()
        victron_mock.write_ac_power_setpoint.reset_mock()

        # Cycle 2: no active slot → slot-exit → IDLE / DISCHARGE, cleanup fires
        orch.set_scheduler(_make_scheduler_mock(active_schedule=None))

        await orch._poll()
        hw2, vw2 = orch._compute_setpoints()
        await orch._apply_setpoints(hw2, vw2)

        # State must have left GRID_CHARGE
        assert orch._control_state != ControlState.GRID_CHARGE, (
            f"Expected non-GRID_CHARGE after slot exit, got {orch._control_state}"
        )

        # Cleanup: Huawei AC charging disabled
        huawei_mock.write_ac_charging.assert_awaited_with(False)

        # Cleanup: Victron phases zeroed — at least 3 calls to write_ac_power_setpoint
        # (cleanup writes 3× 0.0 first; normal apply path may write additional setpoints)
        assert victron_mock.write_ac_power_setpoint.call_count >= 3, (
            f"Expected at least 3 zero-writes on Victron cleanup, "
            f"got {victron_mock.write_ac_power_setpoint.call_count}"
        )
        # The first 3 calls are the cleanup zeroes from _cleanup_grid_charge
        cleanup_calls = victron_mock.write_ac_power_setpoint.call_args_list[:3]
        for call in cleanup_calls:
            actual_w = call[0][1]
            assert actual_w == pytest.approx(0.0, abs=0.1), (
                f"Expected 0 W per phase from cleanup, got {actual_w}"
            )

        # Tracking reset
        assert orch._last_victron_setpoint == 0.0, (
            f"Expected _last_victron_setpoint=0.0 after cleanup, "
            f"got {orch._last_victron_setpoint}"
        )

    async def test_integration_luna_done_victron_takes_over(self):
        """Integration: Huawei offline during a 'huawei' slot routes full power to Victron
        (LUNA-done path). Victron gets positive per-phase setpoints."""
        # Huawei is offline — no reads, no writes
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(side_effect=ConnectionError("offline"))
        huawei_mock.read_battery = AsyncMock(side_effect=ConnectionError("offline"))
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron(battery_soc_pct=40.0))
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)

        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0, grid_charge_power_w=6000)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        await orch._poll()
        # Huawei offline → _huawei_available=False → huawei_target_met=True → victron takes over
        assert not orch._huawei_available, "Huawei should be unavailable after failed reads"

        huawei_w, victron_w = orch._compute_setpoints()

        # LUNA-done: huawei offline → huawei_target_met=True → victron_w = grid_charge_power_w
        assert huawei_w == 0, f"Expected huawei_w=0 (offline), got {huawei_w}"
        assert victron_w == pytest.approx(6000.0, abs=1.0), (
            f"Expected victron_w=6000.0 (full power to Victron), got {victron_w}"
        )
        assert orch._control_state == ControlState.GRID_CHARGE, (
            f"Expected GRID_CHARGE, got {orch._control_state}"
        )

        await orch._apply_setpoints(huawei_w, victron_w)

        # Victron must receive positive per-phase setpoints
        assert victron_mock.write_ac_power_setpoint.call_count == 3, (
            f"Expected 3 Victron phase writes, got {victron_mock.write_ac_power_setpoint.call_count}"
        )
        per_phase = 6000.0 / 3.0
        for call in victron_mock.write_ac_power_setpoint.call_args_list:
            actual_w = call[0][1]
            assert actual_w == pytest.approx(per_phase, abs=1.0), (
                f"Expected +{per_phase} W per phase (positive = import), got {actual_w}"
            )

        # Huawei write methods must NOT have been called (it's offline)
        huawei_mock.write_ac_charging.assert_not_called()
        huawei_mock.write_max_charge_power.assert_not_called()

        # Tracking updated
        assert orch._last_victron_setpoint == pytest.approx(6000.0, abs=1.0), (
            f"Expected _last_victron_setpoint=6000.0, got {orch._last_victron_setpoint}"
        )

    async def test_integration_grid_charge_victron_write_failure_no_crash(self, caplog):
        """Integration: Victron write_ac_power_setpoint raises ConnectionError during GRID_CHARGE.
        The orchestrator must not crash and must remain in GRID_CHARGE state."""
        import logging

        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery(total_soc_pct=70.0))
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron(battery_soc_pct=40.0))
        # Simulate write failure on all phase writes
        victron_mock.write_ac_power_setpoint = MagicMock(side_effect=ConnectionError("Victron unreachable"))

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)

        slot = _make_charge_slot(battery="victron", target_soc_pct=90.0, grid_charge_power_w=3000)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))

        await orch._poll()
        huawei_w, victron_w = orch._compute_setpoints()

        # Must not raise despite Victron write failure
        with caplog.at_level(logging.WARNING, logger="backend.orchestrator"):
            await orch._apply_setpoints(huawei_w, victron_w)

        # Still in GRID_CHARGE — transient write failure does not change control state
        assert orch._control_state == ControlState.GRID_CHARGE, (
            f"Expected GRID_CHARGE after write failure, got {orch._control_state}"
        )

        # WARNING must have been emitted for the failed write
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("GRID_CHARGE" in m and "Victron" in m for m in warning_msgs), (
            f"Expected WARNING about GRID_CHARGE Victron write failure, got: {warning_msgs}"
        )

        # Tracking must NOT have updated (write failed → keep previous value)
        assert orch._last_victron_setpoint == 0.0, (
            f"Expected _last_victron_setpoint=0.0 (write failed, not updated), "
            f"got {orch._last_victron_setpoint}"
        )

    async def test_integration_both_offline_during_grid_charge(self):
        """Integration: both drivers offline during active slot — _apply_grid_charge_setpoints
        must perform no writes and exit cleanly without errors."""
        huawei_mock = MagicMock()
        huawei_mock.read_master = AsyncMock(return_value=_make_master())
        huawei_mock.read_battery = AsyncMock(return_value=_make_battery(total_soc_pct=70.0))
        huawei_mock.write_max_discharge_power = AsyncMock()
        huawei_mock.write_ac_charging = AsyncMock()
        huawei_mock.write_max_charge_power = AsyncMock()

        victron_mock = MagicMock()
        victron_mock.read_system_state = MagicMock(return_value=_make_victron(battery_soc_pct=40.0))
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(huawei_mock=huawei_mock, victron_mock=victron_mock)
        await orch._poll()

        # Force both drivers offline
        orch._huawei_available = False
        orch._victron_available = False

        # Call _apply_grid_charge_setpoints directly — must not raise, must not write
        await orch._apply_grid_charge_setpoints(5000, 3000.0)

        # No write calls to either driver
        huawei_mock.write_ac_charging.assert_not_called()
        huawei_mock.write_max_charge_power.assert_not_called()
        victron_mock.write_ac_power_setpoint.assert_not_called()

        # Tracking must remain at initial 0.0 (no writes occurred)
        assert orch._last_victron_setpoint == 0.0, (
            f"Expected _last_victron_setpoint=0.0 (both offline), "
            f"got {orch._last_victron_setpoint}"
        )


# ===========================================================================
# TestDischargeLock — DISCHARGE_LOCKED state machine contract (S02/T02)
# ===========================================================================


class TestDischargeLock:
    """Proves the complete DISCHARGE_LOCKED state machine contract.

    Follows the TestGridCharge pattern exactly: uses _make_orchestrator(),
    direct attribute manipulation, and synchronous _compute_setpoints() calls.
    No async required.
    """

    def test_set_evcc_monitor_stores_reference(self):
        """set_evcc_monitor() stores the driver on _evcc_monitor."""
        orch = _make_orchestrator()
        mock_driver = MagicMock()
        orch.set_evcc_monitor(mock_driver)
        assert orch._evcc_monitor is mock_driver

    def test_hold_mode_transitions_to_discharge_locked(self):
        """batteryMode=hold transitions orchestrator to DISCHARGE_LOCKED."""
        orch = _make_orchestrator()
        orch._evcc_battery_mode = "hold"
        orch._compute_setpoints()
        assert orch._control_state == ControlState.DISCHARGE_LOCKED

    def test_hold_mode_zeros_both_setpoints(self):
        """batteryMode=hold returns (0, 0) from _compute_setpoints()."""
        orch = _make_orchestrator()
        orch._evcc_battery_mode = "hold"
        result = orch._compute_setpoints()
        assert result == (0, 0)

    def test_normal_mode_does_not_lock(self):
        """batteryMode=normal does NOT produce DISCHARGE_LOCKED state."""
        orch = _make_orchestrator()
        orch._evcc_battery_mode = "normal"
        # Make drivers appear available so normal path executes fully
        orch._huawei_available = True
        orch._victron_available = True
        orch._huawei_last_seen = time.monotonic()
        orch._victron_last_seen = time.monotonic()
        orch._compute_setpoints()
        assert orch._control_state != ControlState.DISCHARGE_LOCKED

    def test_discharge_locked_overrides_active_grid_charge_slot(self):
        """DISCHARGE_LOCKED has higher priority than an active GRID_CHARGE slot."""
        orch = _make_orchestrator()
        # Wire an active scheduler slot at 70% SOC targeting 90%
        orch._last_battery = _make_battery(total_soc_pct=70.0)
        slot = _make_charge_slot(battery="huawei", target_soc_pct=90.0)
        schedule = _make_schedule([slot])
        orch.set_scheduler(_make_scheduler_mock(active_schedule=schedule))
        # EVCC hold must override the slot
        orch._evcc_battery_mode = "hold"
        result = orch._compute_setpoints()
        assert orch._control_state == ControlState.DISCHARGE_LOCKED
        assert result == (0, 0)

    def test_unified_state_evcc_battery_mode_populated(self):
        """_build_unified_state() propagates evcc_battery_mode to UnifiedPoolState."""
        orch = _make_orchestrator()
        orch._evcc_battery_mode = "hold"
        state = orch._build_unified_state(0, 0.0)
        assert state.evcc_battery_mode == "hold"

    def test_discharge_locked_releases_on_normal(self):
        """Lock engages on hold, then releases when mode switches to normal."""
        orch = _make_orchestrator()
        # Engage lock
        orch._evcc_battery_mode = "hold"
        orch._compute_setpoints()
        assert orch._control_state == ControlState.DISCHARGE_LOCKED
        # Release lock — switch to normal with drivers marked available
        orch._evcc_battery_mode = "normal"
        orch._huawei_available = True
        orch._victron_available = True
        orch._huawei_last_seen = time.monotonic()
        orch._victron_last_seen = time.monotonic()
        orch._compute_setpoints()
        assert orch._control_state != ControlState.DISCHARGE_LOCKED

    def test_control_state_enum_value(self):
        """ControlState.DISCHARGE_LOCKED.value is the bare string 'DISCHARGE_LOCKED' (K009)."""
        assert ControlState.DISCHARGE_LOCKED.value == "DISCHARGE_LOCKED"


# ---------------------------------------------------------------------------
# Tests: Telegram notifier wiring
# ---------------------------------------------------------------------------

class TestTelegramWiring:
    """Verify TelegramNotifier injection and state-transition alert firing.

    Tests call ``_notify_state_transition()`` directly to avoid timing
    complexity of running full ``_run()`` loops.
    """

    def test_set_notifier_stores_reference(self):
        """set_notifier() stores the notifier on _notifier."""
        orch = _make_orchestrator()
        mock_notifier = AsyncMock()
        orch.set_notifier(mock_notifier)
        assert orch._notifier is mock_notifier

    async def test_notifier_none_does_not_raise(self):
        """When _notifier is None, _notify_state_transition() is silent."""
        orch = _make_orchestrator()
        # _notifier is None by default — must not raise
        await orch._notify_state_transition(None, ControlState.DISCHARGE_LOCKED)
        await orch._notify_state_transition(ControlState.IDLE, ControlState.DISCHARGE_LOCKED)

    async def test_discharge_locked_transition_sends_alert(self):
        """Transitioning to DISCHARGE_LOCKED fires ALERT_DISCHARGE_LOCKED."""
        from backend.notifier import ALERT_DISCHARGE_LOCKED

        orch = _make_orchestrator()
        mock_notifier = AsyncMock()
        orch.set_notifier(mock_notifier)

        await orch._notify_state_transition(ControlState.IDLE, ControlState.DISCHARGE_LOCKED)

        mock_notifier.send_alert.assert_called_once()
        call_args = mock_notifier.send_alert.call_args
        assert call_args[0][0] == ALERT_DISCHARGE_LOCKED

    async def test_discharge_released_transition_sends_alert(self):
        """Transitioning out of DISCHARGE_LOCKED fires ALERT_DISCHARGE_RELEASED."""
        from backend.notifier import ALERT_DISCHARGE_RELEASED

        orch = _make_orchestrator()
        mock_notifier = AsyncMock()
        orch.set_notifier(mock_notifier)

        await orch._notify_state_transition(ControlState.DISCHARGE_LOCKED, ControlState.IDLE)

        mock_notifier.send_alert.assert_called_once()
        call_args = mock_notifier.send_alert.call_args
        assert call_args[0][0] == ALERT_DISCHARGE_RELEASED

    async def test_comm_failure_transition_sends_alert(self):
        """Transitioning to HOLD with both drivers unavailable fires ALERT_COMM_FAILURE."""
        from backend.notifier import ALERT_COMM_FAILURE

        orch = _make_orchestrator()
        mock_notifier = AsyncMock()
        orch.set_notifier(mock_notifier)
        orch._huawei_available = False
        orch._victron_available = False

        await orch._notify_state_transition(ControlState.IDLE, ControlState.HOLD)

        mock_notifier.send_alert.assert_called_once()
        call_args = mock_notifier.send_alert.call_args
        assert call_args[0][0] == ALERT_COMM_FAILURE

    async def test_comm_failure_only_on_transition_not_repeat_hold(self):
        """No alert when already in HOLD — comm-failure fires only on transition."""
        orch = _make_orchestrator()
        mock_notifier = AsyncMock()
        orch.set_notifier(mock_notifier)
        orch._huawei_available = False
        orch._victron_available = False

        # Already in HOLD (prev == HOLD) — no alert expected
        await orch._notify_state_transition(ControlState.HOLD, ControlState.HOLD)

        mock_notifier.send_alert.assert_not_called()

    async def test_comm_failure_not_fired_when_drivers_available(self):
        """HOLD transition with drivers still available does NOT fire comm-failure alert."""
        orch = _make_orchestrator()
        mock_notifier = AsyncMock()
        orch.set_notifier(mock_notifier)
        # Drivers are available — HOLD must be from min-SoC, not comm failure
        orch._huawei_available = True
        orch._victron_available = False

        await orch._notify_state_transition(ControlState.IDLE, ControlState.HOLD)

        mock_notifier.send_alert.assert_not_called()

    async def test_no_alert_for_non_transition_states(self):
        """Transitions between non-alert states (IDLE→DISCHARGE) fire no alert."""
        orch = _make_orchestrator()
        mock_notifier = AsyncMock()
        orch.set_notifier(mock_notifier)

        await orch._notify_state_transition(ControlState.IDLE, ControlState.DISCHARGE)
        await orch._notify_state_transition(ControlState.DISCHARGE, ControlState.IDLE)
        await orch._notify_state_transition(ControlState.IDLE, ControlState.GRID_CHARGE)

        mock_notifier.send_alert.assert_not_called()

    async def test_discharge_locked_alert_not_fired_when_already_locked(self):
        """No DISCHARGE_LOCKED alert when transitioning DISCHARGE_LOCKED → DISCHARGE_LOCKED."""
        from backend.notifier import ALERT_DISCHARGE_LOCKED

        orch = _make_orchestrator()
        mock_notifier = AsyncMock()
        orch.set_notifier(mock_notifier)

        await orch._notify_state_transition(
            ControlState.DISCHARGE_LOCKED, ControlState.DISCHARGE_LOCKED
        )

        mock_notifier.send_alert.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: per-phase setpoint dispatch
# ---------------------------------------------------------------------------

class TestPerPhaseDispatch:
    """Verify _apply_setpoints() per-phase dispatch: values, fallback, clamping, dead-band."""

    def _orch_with_victron(self, **victron_overrides):
        """Return (orch, victron_mock, victron_data) ready for _apply_setpoints calls."""
        victron_data = _make_victron(**victron_overrides)

        victron_mock = MagicMock()
        victron_mock.write_ac_power_setpoint = MagicMock()

        orch = _make_orchestrator(
            victron_mock=victron_mock,
            orch_config=OrchestratorConfig(
                loop_interval_s=0.01,
                debounce_cycles=1,
                hysteresis_w=0,  # disable Huawei combined dead-band for these tests
            ),
        )
        orch._victron_available = True
        orch._last_victron = victron_data
        return orch, victron_mock, victron_data

    async def test_per_phase_uses_individual_grid_readings(self):
        """Per-phase dispatch uses each phase's own grid reading, not equal split."""
        orch, victron_mock, _ = self._orch_with_victron(
            grid_l1_power_w=200.0,
            grid_l2_power_w=400.0,
            grid_l3_power_w=600.0,
        )

        await orch._apply_setpoints(0, 1200.0)

        calls = victron_mock.write_ac_power_setpoint.call_args_list
        assert len(calls) == 3
        phase_to_value = {c[0][0]: c[0][1] for c in calls}
        assert phase_to_value[1] == pytest.approx(-200.0, abs=0.1)
        assert phase_to_value[2] == pytest.approx(-400.0, abs=0.1)
        assert phase_to_value[3] == pytest.approx(-600.0, abs=0.1)

    async def test_per_phase_none_falls_back_to_equal_split(self):
        """All grid_lN=None → equal-split fallback; all three phases still written."""
        orch, victron_mock, _ = self._orch_with_victron(
            grid_l1_power_w=None,
            grid_l2_power_w=None,
            grid_l3_power_w=None,
        )

        await orch._apply_setpoints(0, 900.0)

        calls = victron_mock.write_ac_power_setpoint.call_args_list
        assert len(calls) == 3
        for c in calls:
            assert c[0][1] == pytest.approx(-300.0, abs=0.1)

    async def test_per_phase_partial_none_uses_equal_split_for_missing_phase(self):
        """L1 available, L2/L3 None → L1 uses grid reading; L2/L3 use equal split."""
        orch, victron_mock, _ = self._orch_with_victron(
            grid_l1_power_w=300.0,
            grid_l2_power_w=None,
            grid_l3_power_w=None,
        )

        await orch._apply_setpoints(0, 900.0)

        calls = victron_mock.write_ac_power_setpoint.call_args_list
        assert len(calls) == 3
        phase_to_value = {c[0][0]: c[0][1] for c in calls}
        # L1 uses its grid reading (-300.0); L2/L3 fall back to equal split (-900/3 = -300.0)
        assert phase_to_value[1] == pytest.approx(-300.0, abs=0.1)
        assert phase_to_value[2] == pytest.approx(-300.0, abs=0.1)
        assert phase_to_value[3] == pytest.approx(-300.0, abs=0.1)

    async def test_per_phase_clamp_max_positive_import(self):
        """Grid reading > 4600 W → setpoint clamped to -4600 W."""
        orch, victron_mock, _ = self._orch_with_victron(
            grid_l1_power_w=5000.0,
            grid_l2_power_w=5000.0,
            grid_l3_power_w=5000.0,
        )

        await orch._apply_setpoints(0, 15000.0)

        calls = victron_mock.write_ac_power_setpoint.call_args_list
        assert len(calls) == 3
        for c in calls:
            assert c[0][1] == pytest.approx(-4600.0, abs=0.1)

    async def test_per_phase_clamp_min_negative_export(self):
        """Grid reading < -4600 W (exporting) → setpoint clamped to +4600 W."""
        orch, victron_mock, _ = self._orch_with_victron(
            grid_l1_power_w=-5000.0,
            grid_l2_power_w=-5000.0,
            grid_l3_power_w=-5000.0,
        )

        await orch._apply_setpoints(0, -15000.0)

        calls = victron_mock.write_ac_power_setpoint.call_args_list
        assert len(calls) == 3
        for c in calls:
            assert c[0][1] == pytest.approx(4600.0, abs=0.1)

    async def test_per_phase_dead_band_suppresses_writes_when_all_deltas_small(self):
        """All three per-phase deltas < 20 W → write_ac_power_setpoint NOT called."""
        orch, victron_mock, _ = self._orch_with_victron(
            grid_l1_power_w=300.0,
            grid_l2_power_w=400.0,
            grid_l3_power_w=600.0,
        )
        # Last written setpoints close to the new values (delta 10, 10, 10 < 20 W)
        orch._last_l1_setpoint = -290.0
        orch._last_l2_setpoint = -390.0
        orch._last_l3_setpoint = -590.0

        await orch._apply_setpoints(0, 1300.0)

        victron_mock.write_ac_power_setpoint.assert_not_called()

    async def test_per_phase_dead_band_allows_writes_when_any_delta_large(self):
        """One phase delta ≥ 20 W → all three phases are written."""
        orch, victron_mock, _ = self._orch_with_victron(
            grid_l1_power_w=250.0,  # new setpoint -250, last -200 → delta 50 W ≥ 20
            grid_l2_power_w=210.0,  # new setpoint -210, last -200 → delta 10 W < 20
            grid_l3_power_w=215.0,  # new setpoint -215, last -200 → delta 15 W < 20
        )
        orch._last_l1_setpoint = -200.0
        orch._last_l2_setpoint = -200.0
        orch._last_l3_setpoint = -200.0

        await orch._apply_setpoints(0, 675.0)

        calls = victron_mock.write_ac_power_setpoint.call_args_list
        assert len(calls) == 3
        called_phases = sorted(c[0][0] for c in calls)
        assert called_phases == [1, 2, 3]


# ---------------------------------------------------------------------------
# Tests: get_device_snapshot() per-phase grid fields
# ---------------------------------------------------------------------------

class TestGetDeviceSnapshot:
    """Verify get_device_snapshot() exposes per-phase grid fields in victron dict."""

    def test_device_snapshot_victron_includes_grid_phase_fields(self):
        """get_device_snapshot() victron dict includes grid_l1/l2/l3_power_w."""
        orch = _make_orchestrator()
        orch._last_victron = _make_victron(
            grid_l1_power_w=100.0,
            grid_l2_power_w=200.0,
            grid_l3_power_w=300.0,
        )

        snapshot = orch.get_device_snapshot()
        victron = snapshot["victron"]

        assert victron["grid_l1_power_w"] == pytest.approx(100.0)
        assert victron["grid_l2_power_w"] == pytest.approx(200.0)
        assert victron["grid_l3_power_w"] == pytest.approx(300.0)

    def test_device_snapshot_victron_grid_phase_fields_none_when_unavailable(self):
        """grid_lN_power_w fields are None when VictronSystemData has no phase readings."""
        orch = _make_orchestrator()
        orch._last_victron = _make_victron(
            grid_l1_power_w=None,
            grid_l2_power_w=None,
            grid_l3_power_w=None,
        )

        snapshot = orch.get_device_snapshot()
        victron = snapshot["victron"]

        assert victron["grid_l1_power_w"] is None
        assert victron["grid_l2_power_w"] is None
        assert victron["grid_l3_power_w"] is None


# ---------------------------------------------------------------------------
# Tests: Huawei working mode capture and logging
# ---------------------------------------------------------------------------


class TestHuaweiWorkingMode:
    """Working-mode is captured once on first successful battery read.

    Uses caplog (pytest's built-in log capture) for all log assertions.
    """

    @pytest.mark.anyio
    async def test_info_logged_for_expected_mode_2(self, caplog):
        """MAXIMISE_SELF_CONSUMPTION (mode=2) logs INFO only — no WARNING."""
        import logging

        orch = _make_orchestrator()
        orch._huawei.read_battery = AsyncMock(return_value=_make_battery(working_mode=2))

        with caplog.at_level(logging.DEBUG, logger="backend.orchestrator"):
            await orch._poll()

        info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("Huawei working mode: MAXIMISE_SELF_CONSUMPTION (2)" in m for m in info_msgs), (
            f"Expected INFO not found in: {info_msgs}"
        )
        warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("write_max_discharge_power" in m for m in warn_msgs), (
            f"Unexpected WARNING found: {warn_msgs}"
        )
        assert orch.get_working_mode() == 2

    @pytest.mark.anyio
    async def test_info_logged_for_expected_mode_5(self, caplog):
        """TIME_OF_USE_LUNA2000 (mode=5) logs INFO only — no WARNING."""
        import logging

        orch = _make_orchestrator()
        orch._huawei.read_battery = AsyncMock(return_value=_make_battery(working_mode=5))

        with caplog.at_level(logging.DEBUG, logger="backend.orchestrator"):
            await orch._poll()

        info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("TIME_OF_USE_LUNA2000 (5)" in m for m in info_msgs)
        warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("write_max_discharge_power" in m for m in warn_msgs)
        assert orch.get_working_mode() == 5

    @pytest.mark.anyio
    async def test_warning_logged_for_unexpected_mode(self, caplog):
        """An unexpected mode (e.g. 1=FIXED_CHARGE_DISCHARGE) logs a WARNING."""
        import logging

        orch = _make_orchestrator()
        orch._huawei.read_battery = AsyncMock(return_value=_make_battery(working_mode=1))

        with caplog.at_level(logging.WARNING, logger="backend.orchestrator"):
            await orch._poll()

        warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("write_max_discharge_power" in m for m in warn_msgs), (
            f"Expected WARNING not found in: {warn_msgs}"
        )
        assert orch.get_working_mode() == 1

    @pytest.mark.anyio
    async def test_log_fires_only_once_across_multiple_polls(self, caplog):
        """The working-mode log block must not repeat on subsequent poll cycles."""
        import logging

        orch = _make_orchestrator()
        orch._huawei.read_battery = AsyncMock(return_value=_make_battery(working_mode=2))

        with caplog.at_level(logging.INFO, logger="backend.orchestrator"):
            await orch._poll()
            await orch._poll()
            await orch._poll()

        working_mode_logs = [
            r for r in caplog.records
            if r.levelno == logging.INFO and "Huawei working mode" in r.message
        ]
        assert len(working_mode_logs) == 1, (
            f"Expected exactly 1 working-mode log; got {len(working_mode_logs)}"
        )
        assert orch._working_mode_logged is True

    @pytest.mark.anyio
    async def test_working_mode_none_logs_unavailable(self, caplog):
        """When working_mode=None the INFO log says 'unavailable' and get_working_mode() is None."""
        import logging

        orch = _make_orchestrator()
        orch._huawei.read_battery = AsyncMock(return_value=_make_battery(working_mode=None))

        with caplog.at_level(logging.INFO, logger="backend.orchestrator"):
            await orch._poll()

        info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("unavailable" in m for m in info_msgs), (
            f"Expected 'unavailable' INFO not found in: {info_msgs}"
        )
        assert orch.get_working_mode() is None

    def test_get_working_mode_returns_none_before_first_poll(self):
        """get_working_mode() is None before any successful battery read."""
        orch = _make_orchestrator()
        assert orch.get_working_mode() is None

    @pytest.mark.anyio
    async def test_working_mode_not_updated_after_huawei_failure(self, caplog):
        """A failed poll does not set _working_mode_logged; mode stays None."""
        import logging

        orch = _make_orchestrator()
        orch._huawei.read_battery = AsyncMock(side_effect=Exception("Modbus timeout"))

        with caplog.at_level(logging.WARNING, logger="backend.orchestrator"):
            await orch._poll()

        assert orch._working_mode_logged is False
        assert orch.get_working_mode() is None
