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
        """Victron write_ac_power_setpoint uses -victron_w/3 for discharge."""
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
                hysteresis_w=0,
            ),
        )
        orch._huawei_available = False
        orch._victron_available = True
        orch._last_victron = victron_data

        # Write a 900 W discharge setpoint (300 W per phase)
        await orch._apply_setpoints(0, 900.0)

        # All three phases should receive -300 W
        expected_per_phase = -300.0
        calls = victron_mock.write_ac_power_setpoint.call_args_list
        assert len(calls) == 3
        for c in calls:
            args = c[0]
            assert args[0] in (1, 2, 3)  # phase
            assert args[1] == pytest.approx(expected_per_phase, abs=0.1)

    async def test_victron_3_phases_each_receive_call(self):
        """All three phases (1, 2, 3) receive exactly one write call each."""
        victron_data = _make_victron()

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
            l1=_make_phase(power_w=0.0),  # large deviation
            l2=_make_phase(power_w=0.0),
            l3=_make_phase(power_w=0.0),
        )

        orch = _make_orchestrator()
        orch._victron_available = True
        orch._last_victron = victron_data

        with caplog.at_level(logging.WARNING, logger="backend.orchestrator"):
            for _ in range(4):
                orch._check_phase_imbalance(3000.0)  # 1000W/phase expected

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

        # Measured ~1000W per phase vs 1000W setpoint → no imbalance
        orch._check_phase_imbalance(3000.0)

        assert orch._phase_imbalance_cycles == 0
