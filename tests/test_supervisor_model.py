from __future__ import annotations

from backend.supervisor_model import (
    BatteryState,
    Observation,
    InterventionAction,
    InterventionRecord,
    SupervisorState,
)


class TestBatteryState:
    def test_values(self) -> None:
        assert BatteryState.AUTONOMOUS == "AUTONOMOUS"
        assert BatteryState.HELD == "HELD"
        assert BatteryState.GRID_CHARGING == "GRID_CHARGING"

    def test_is_str_enum(self) -> None:
        assert isinstance(BatteryState.AUTONOMOUS, str)


class TestObservation:
    def test_pool_soc(self) -> None:
        obs = Observation(
            huawei_soc_pct=50.0,
            victron_soc_pct=50.0,
            huawei_power_w=-1000.0,
            victron_power_w=-2000.0,
            pv_power_w=3000.0,
            emma_load_power_w=4000.0,
            victron_consumption_w=1000.0,
            huawei_available=True,
            victron_available=True,
            timestamp=1000.0,
        )
        # pool_soc = (50*30 + 50*64) / 94 = 50.0
        assert obs.pool_soc == 50.0

    def test_pool_soc_weighted(self) -> None:
        obs = Observation(
            huawei_soc_pct=100.0,
            victron_soc_pct=0.0,
            huawei_power_w=0.0,
            victron_power_w=0.0,
            pv_power_w=0.0,
            emma_load_power_w=0.0,
            victron_consumption_w=0.0,
            huawei_available=True,
            victron_available=True,
            timestamp=1000.0,
        )
        # pool_soc = (100*30 + 0*64) / 94 ≈ 31.91
        assert abs(obs.pool_soc - 31.91) < 0.1

    def test_soc_delta(self) -> None:
        obs = Observation(
            huawei_soc_pct=80.0,
            victron_soc_pct=60.0,
            huawei_power_w=0.0,
            victron_power_w=0.0,
            pv_power_w=0.0,
            emma_load_power_w=0.0,
            victron_consumption_w=0.0,
            huawei_available=True,
            victron_available=True,
            timestamp=1000.0,
        )
        assert obs.soc_delta == 20.0

    def test_true_consumption(self) -> None:
        obs = Observation(
            huawei_soc_pct=50.0,
            victron_soc_pct=50.0,
            huawei_power_w=0.0,
            victron_power_w=0.0,
            pv_power_w=0.0,
            emma_load_power_w=3000.0,
            victron_consumption_w=1500.0,
            huawei_available=True,
            victron_available=True,
            timestamp=1000.0,
        )
        assert obs.true_consumption_w == 4500.0


class TestInterventionAction:
    def test_hold_action(self) -> None:
        action = InterventionAction(
            target_system="huawei",
            target_state=BatteryState.HELD,
        )
        assert action.target_system == "huawei"
        assert action.target_state == BatteryState.HELD
        assert action.max_discharge_power_w is None
        assert action.target_soc_pct is None

    def test_throttle_action(self) -> None:
        action = InterventionAction(
            target_system="huawei",
            target_state=BatteryState.AUTONOMOUS,
            max_discharge_power_w=2500,
        )
        assert action.max_discharge_power_w == 2500


class TestInterventionRecord:
    def test_fields(self) -> None:
        rec = InterventionRecord(
            timestamp="2026-03-28T10:00:00Z",
            intervention_type="min_soc_guard",
            target_system="victron",
            action=BatteryState.HELD,
            reason="Victron SoC 8% below min_soc 10%",
        )
        assert rec.intervention_type == "min_soc_guard"
        assert rec.target_system == "victron"


class TestSupervisorState:
    def test_fields(self) -> None:
        state = SupervisorState(
            pool_soc_pct=50.0,
            huawei_soc_pct=50.0,
            victron_soc_pct=50.0,
            soc_delta=0.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            huawei_available=True,
            victron_available=True,
            true_consumption_w=5000.0,
            pv_power_w=3000.0,
            active_interventions=[],
            timestamp=1000.0,
        )
        assert state.huawei_state == BatteryState.AUTONOMOUS
        assert state.active_interventions == []
