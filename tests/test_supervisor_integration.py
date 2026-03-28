from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from backend.config import SupervisoryConfig, OrchestratorConfig, SystemConfig
from backend.controller_model import BatteryRole, ControllerSnapshot
from backend.supervisor import Supervisor
from backend.supervisor_model import BatteryState


def _snap(soc: float = 50.0, power: float = 0.0, **kwargs) -> ControllerSnapshot:
    defaults = dict(
        soc_pct=soc,
        power_w=power,
        available=True,
        role=BatteryRole.HOLDING,
        consecutive_failures=0,
        timestamp=1000.0,
        pv_input_power_w=kwargs.get("pv", 0),
        grid_power_w=kwargs.get("grid", None),
        consumption_w=kwargs.get("consumption", 0),
        master_active_power_w=kwargs.get("load", 0),
    )
    return ControllerSnapshot(**defaults)


class TestSupervisorIntegration:
    @pytest.mark.anyio
    async def test_normal_operation_no_writes(self) -> None:
        """Both batteries healthy, balanced SoC, no tariff slot — no controller writes."""
        h = AsyncMock()
        v = AsyncMock()
        h.poll = AsyncMock(return_value=_snap(soc=50, power=-1000, pv=3000, load=4000))
        v.poll = AsyncMock(return_value=_snap(soc=50, power=-2000, consumption=1500))

        sup = Supervisor(
            huawei_ctrl=h, victron_ctrl=v,
            supervisory_config=SupervisoryConfig(),
            orch_config=OrchestratorConfig(),
            sys_config=SystemConfig(),
        )
        await sup._run_cycle()

        h.execute.assert_not_awaited()
        v.execute.assert_not_awaited()
        state = sup.get_state()
        assert state.huawei_state == BatteryState.AUTONOMOUS
        assert state.victron_state == BatteryState.AUTONOMOUS

    @pytest.mark.anyio
    async def test_min_soc_then_recovery(self) -> None:
        """Huawei drops below min — held. Recovers above min+hysteresis — released."""
        h = AsyncMock()
        v = AsyncMock()
        v.poll = AsyncMock(return_value=_snap(soc=50, consumption=1000))

        sup = Supervisor(
            huawei_ctrl=h, victron_ctrl=v,
            supervisory_config=SupervisoryConfig(min_soc_pct=10.0, min_soc_hysteresis_pct=5.0),
            orch_config=OrchestratorConfig(),
            sys_config=SystemConfig(),
        )

        # Cycle 1: SoC=5% — hold
        h.poll = AsyncMock(return_value=_snap(soc=5, power=-500, pv=0, load=1000))
        await sup._run_cycle()
        assert sup.get_state().huawei_state == BatteryState.HELD

        # Cycle 2: SoC=12% — still held (below 10+5=15)
        h.poll = AsyncMock(return_value=_snap(soc=12, power=0, pv=0, load=1000))
        h.execute.reset_mock()
        await sup._run_cycle()
        assert sup.get_state().huawei_state == BatteryState.HELD

        # Cycle 3: SoC=16% — released (above 15)
        h.poll = AsyncMock(return_value=_snap(soc=16, power=0, pv=3000, load=1000))
        h.execute.reset_mock()
        await sup._run_cycle()
        assert sup.get_state().huawei_state == BatteryState.AUTONOMOUS

    @pytest.mark.anyio
    async def test_cross_charge_detected_and_cleared(self) -> None:
        """Cross-charge detected — victron held."""
        h = AsyncMock()
        v = AsyncMock()

        sup = Supervisor(
            huawei_ctrl=h, victron_ctrl=v,
            supervisory_config=SupervisoryConfig(),
            orch_config=OrchestratorConfig(),
            sys_config=SystemConfig(),
        )

        # Huawei discharging, Victron charging, no PV — cross-charge
        h.poll = AsyncMock(return_value=_snap(soc=50, power=-1000, pv=50, load=2000))
        v.poll = AsyncMock(return_value=_snap(soc=50, power=500, consumption=1000))
        await sup._run_cycle()
        assert sup.get_state().victron_state == BatteryState.HELD

    @pytest.mark.anyio
    async def test_intervention_history_populated(self) -> None:
        """Interventions are recorded in history."""
        h = AsyncMock()
        v = AsyncMock()
        h.poll = AsyncMock(return_value=_snap(soc=5, power=-500, pv=0, load=1000))
        v.poll = AsyncMock(return_value=_snap(soc=50, consumption=1000))

        sup = Supervisor(
            huawei_ctrl=h, victron_ctrl=v,
            supervisory_config=SupervisoryConfig(),
            orch_config=OrchestratorConfig(),
            sys_config=SystemConfig(),
        )
        await sup._run_cycle()

        history = sup.get_interventions(limit=10)
        assert len(history) >= 1
        assert history[0]["target_system"] == "huawei"

    @pytest.mark.anyio
    async def test_soc_balancing_throttles_higher_battery(self) -> None:
        """When SoC delta exceeds threshold, the higher-SoC battery is throttled."""
        h = AsyncMock()
        v = AsyncMock()
        h.poll = AsyncMock(return_value=_snap(soc=80, power=-2000, pv=3000, load=4000))
        v.poll = AsyncMock(return_value=_snap(soc=50, power=-1000, consumption=1500))

        sup = Supervisor(
            huawei_ctrl=h, victron_ctrl=v,
            supervisory_config=SupervisoryConfig(soc_balance_threshold_pct=10.0),
            orch_config=OrchestratorConfig(),
            sys_config=SystemConfig(),
        )
        await sup._run_cycle()

        # Huawei has higher SoC, should get a throttle command
        h.execute.assert_awaited_once()
        state = sup.get_state()
        assert state.soc_delta == 30.0

    @pytest.mark.anyio
    async def test_multiple_cycles_state_persistence(self) -> None:
        """State persists across cycles correctly."""
        h = AsyncMock()
        v = AsyncMock()
        h.poll = AsyncMock(return_value=_snap(soc=50, power=-1000, pv=3000, load=4000))
        v.poll = AsyncMock(return_value=_snap(soc=50, power=-2000, consumption=1500))

        sup = Supervisor(
            huawei_ctrl=h, victron_ctrl=v,
            supervisory_config=SupervisoryConfig(),
            orch_config=OrchestratorConfig(),
            sys_config=SystemConfig(),
        )

        # Run 3 normal cycles
        for _ in range(3):
            await sup._run_cycle()

        state = sup.get_state()
        assert state is not None
        assert state.huawei_state == BatteryState.AUTONOMOUS
        assert state.victron_state == BatteryState.AUTONOMOUS
        # No writes in normal operation
        h.execute.assert_not_awaited()
        v.execute.assert_not_awaited()
