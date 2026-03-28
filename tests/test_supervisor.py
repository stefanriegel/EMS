from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from backend.config import SupervisoryConfig, OrchestratorConfig, SystemConfig
from backend.controller_model import BatteryRole, ControllerSnapshot
from backend.supervisor import Supervisor
from backend.supervisor_model import BatteryState


def _snap(
    soc: float = 50.0,
    power: float = 0.0,
    available: bool = True,
    pv_power: float = 0.0,
    consumption: float | None = None,
    load_power: float | None = None,
) -> ControllerSnapshot:
    return ControllerSnapshot(
        soc_pct=soc,
        power_w=power,
        available=available,
        role=BatteryRole.HOLDING,
        consecutive_failures=0,
        timestamp=1000.0,
        pv_input_power_w=int(pv_power) if pv_power else None,
        consumption_w=consumption,
        master_active_power_w=load_power,
    )


def _make_supervisor(
    supervisory_config: SupervisoryConfig | None = None,
) -> tuple[Supervisor, AsyncMock, AsyncMock]:
    h_ctrl = AsyncMock()
    v_ctrl = AsyncMock()
    h_ctrl.poll = AsyncMock(return_value=_snap(soc=50, power=-1000, pv_power=3000, load_power=4000))
    v_ctrl.poll = AsyncMock(return_value=_snap(soc=50, power=-2000, consumption=1500))

    sup = Supervisor(
        huawei_ctrl=h_ctrl,
        victron_ctrl=v_ctrl,
        supervisory_config=supervisory_config or SupervisoryConfig(),
        orch_config=OrchestratorConfig(),
        sys_config=SystemConfig(),
    )
    return sup, h_ctrl, v_ctrl


class TestSupervisorObserve:
    @pytest.mark.anyio
    async def test_observe_reads_both_controllers(self) -> None:
        sup, h_ctrl, v_ctrl = _make_supervisor()
        obs = await sup._observe()
        h_ctrl.poll.assert_awaited_once()
        v_ctrl.poll.assert_awaited_once()
        assert obs.huawei_soc_pct == 50.0
        assert obs.victron_soc_pct == 50.0

    @pytest.mark.anyio
    async def test_observe_extracts_pv_power(self) -> None:
        sup, _, _ = _make_supervisor()
        obs = await sup._observe()
        assert obs.pv_power_w == 3000.0

    @pytest.mark.anyio
    async def test_observe_handles_unavailable_huawei(self) -> None:
        sup, h_ctrl, _ = _make_supervisor()
        h_ctrl.poll = AsyncMock(return_value=_snap(available=False, soc=0))
        obs = await sup._observe()
        assert obs.huawei_available is False


class TestSupervisorCycle:
    @pytest.mark.anyio
    async def test_normal_state_no_writes(self) -> None:
        sup, h_ctrl, v_ctrl = _make_supervisor()
        await sup._run_cycle()
        h_ctrl.execute.assert_not_awaited()
        v_ctrl.execute.assert_not_awaited()

    @pytest.mark.anyio
    async def test_min_soc_holds_battery(self) -> None:
        sup, h_ctrl, v_ctrl = _make_supervisor()
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=5, power=-1000, pv_power=0, load_power=2000))
        await sup._run_cycle()
        h_ctrl.execute.assert_awaited_once()
        cmd = h_ctrl.execute.call_args[0][0]
        assert cmd.role == BatteryRole.HOLDING
        assert cmd.target_watts == 0

    @pytest.mark.anyio
    async def test_state_reflects_intervention(self) -> None:
        sup, h_ctrl, _ = _make_supervisor()
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=5, power=-1000, pv_power=0, load_power=2000))
        await sup._run_cycle()
        state = sup.get_state()
        assert state is not None
        assert state.huawei_state == BatteryState.HELD

    @pytest.mark.anyio
    async def test_cross_charge_holds_charging_system(self) -> None:
        sup, h_ctrl, v_ctrl = _make_supervisor()
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=50, power=-1000, pv_power=50, load_power=2000))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50, power=500, consumption=1000))
        await sup._run_cycle()
        v_ctrl.execute.assert_awaited_once()
        cmd = v_ctrl.execute.call_args[0][0]
        assert cmd.role == BatteryRole.HOLDING

    @pytest.mark.anyio
    async def test_get_interventions_returns_history(self) -> None:
        sup, h_ctrl, _ = _make_supervisor()
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=5, power=-1000, pv_power=0, load_power=2000))
        await sup._run_cycle()
        interventions = sup.get_interventions(limit=10)
        assert len(interventions) >= 1
        assert interventions[0]["intervention_type"] == "min_soc_guard"
