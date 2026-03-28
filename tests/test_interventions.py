from __future__ import annotations

from datetime import datetime, timezone

from backend.interventions import (
    check_cross_charge,
    check_grid_charge,
    check_min_soc,
    check_soc_balance,
    evaluate_interventions,
)
from backend.schedule_models import ChargeSlot
from backend.supervisor_model import BatteryState, Observation


def _obs(
    huawei_soc: float = 50.0,
    victron_soc: float = 50.0,
    huawei_power: float = 0.0,
    victron_power: float = 0.0,
    pv_power: float = 0.0,
    **kwargs,
) -> Observation:
    defaults = dict(
        huawei_soc_pct=huawei_soc,
        victron_soc_pct=victron_soc,
        huawei_power_w=huawei_power,
        victron_power_w=victron_power,
        pv_power_w=pv_power,
        emma_load_power_w=0.0,
        victron_consumption_w=0.0,
        huawei_available=True,
        victron_available=True,
        timestamp=1000.0,
    )
    defaults.update(kwargs)
    return Observation(**defaults)


def _make_slot(
    battery: str = "huawei",
    target_soc_pct: float = 80.0,
    grid_charge_power_w: int = 3000,
) -> ChargeSlot:
    return ChargeSlot(
        battery=battery,
        target_soc_pct=target_soc_pct,
        start_utc=datetime(2026, 3, 28, 1, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 3, 28, 5, 0, tzinfo=timezone.utc),
        grid_charge_power_w=grid_charge_power_w,
    )


class TestMinSocGuard:
    def test_no_action_when_both_above_min(self) -> None:
        actions = check_min_soc(
            _obs(huawei_soc=50, victron_soc=50),
            min_soc_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
        )
        assert actions == []

    def test_hold_huawei_below_min(self) -> None:
        actions = check_min_soc(
            _obs(huawei_soc=8, victron_soc=50),
            min_soc_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
        )
        assert len(actions) == 1
        assert actions[0].target_system == "huawei"
        assert actions[0].target_state == BatteryState.HELD

    def test_hold_victron_below_min(self) -> None:
        actions = check_min_soc(
            _obs(huawei_soc=50, victron_soc=5),
            min_soc_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
        )
        assert len(actions) == 1
        assert actions[0].target_system == "victron"
        assert actions[0].target_state == BatteryState.HELD

    def test_hold_both_below_min(self) -> None:
        actions = check_min_soc(
            _obs(huawei_soc=5, victron_soc=5),
            min_soc_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
        )
        assert len(actions) == 2

    def test_release_requires_hysteresis(self) -> None:
        actions = check_min_soc(
            _obs(huawei_soc=12, victron_soc=50),
            min_soc_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.HELD,
            victron_state=BatteryState.AUTONOMOUS,
        )
        assert len(actions) == 1
        assert actions[0].target_system == "huawei"
        assert actions[0].target_state == BatteryState.HELD

    def test_release_above_hysteresis(self) -> None:
        actions = check_min_soc(
            _obs(huawei_soc=16, victron_soc=50),
            min_soc_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.HELD,
            victron_state=BatteryState.AUTONOMOUS,
        )
        assert actions == []


class TestCrossChargePrevention:
    def test_no_action_both_discharging(self) -> None:
        actions = check_cross_charge(
            _obs(huawei_power=-1000, victron_power=-2000, pv_power=0),
            consecutive_clear_count=0,
        )
        assert actions == []

    def test_no_action_charging_with_pv(self) -> None:
        actions = check_cross_charge(
            _obs(huawei_power=-1000, victron_power=500, pv_power=2000),
            consecutive_clear_count=0,
        )
        assert actions == []

    def test_detect_victron_charging_from_grid(self) -> None:
        obs = _obs(huawei_power=-1000, victron_power=500, pv_power=50)
        actions = check_cross_charge(obs, consecutive_clear_count=0)
        assert len(actions) == 1
        assert actions[0].target_system == "victron"
        assert actions[0].target_state == BatteryState.HELD

    def test_detect_huawei_charging_from_grid(self) -> None:
        obs = _obs(huawei_power=500, victron_power=-1000, pv_power=50)
        actions = check_cross_charge(obs, consecutive_clear_count=0)
        assert len(actions) == 1
        assert actions[0].target_system == "huawei"
        assert actions[0].target_state == BatteryState.HELD

    def test_no_action_both_charging(self) -> None:
        actions = check_cross_charge(
            _obs(huawei_power=500, victron_power=500, pv_power=0),
            consecutive_clear_count=0,
        )
        assert actions == []


class TestGridChargeWindow:
    def test_no_action_without_active_slot(self) -> None:
        actions = check_grid_charge(active_slot=None)
        assert actions == []

    def test_grid_charge_huawei(self) -> None:
        slot = _make_slot(battery="huawei", target_soc_pct=80, grid_charge_power_w=3000)
        actions = check_grid_charge(active_slot=slot)
        assert len(actions) == 1
        assert actions[0].target_system == "huawei"
        assert actions[0].target_state == BatteryState.GRID_CHARGING
        assert actions[0].target_soc_pct == 80.0
        assert actions[0].charge_power_w == 3000

    def test_grid_charge_victron(self) -> None:
        slot = _make_slot(battery="victron", target_soc_pct=90, grid_charge_power_w=5000)
        actions = check_grid_charge(active_slot=slot)
        assert len(actions) == 1
        assert actions[0].target_system == "victron"
        assert actions[0].target_state == BatteryState.GRID_CHARGING
        assert actions[0].target_soc_pct == 90.0
        assert actions[0].charge_power_w == 5000


class TestSocBalancing:
    def test_no_action_within_threshold(self) -> None:
        actions = check_soc_balance(
            _obs(huawei_soc=55, victron_soc=50),
            threshold_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            balancing_active=False,
        )
        assert actions == []

    def test_throttle_huawei_when_higher(self) -> None:
        actions = check_soc_balance(
            _obs(huawei_soc=80, victron_soc=60),
            threshold_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            balancing_active=False,
        )
        assert len(actions) == 1
        assert actions[0].target_system == "huawei"
        assert actions[0].target_state == BatteryState.AUTONOMOUS
        assert actions[0].max_discharge_power_w is not None

    def test_throttle_victron_when_higher(self) -> None:
        actions = check_soc_balance(
            _obs(huawei_soc=40, victron_soc=65),
            threshold_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            balancing_active=False,
        )
        assert len(actions) == 1
        assert actions[0].target_system == "victron"
        assert actions[0].target_state == BatteryState.AUTONOMOUS
        assert actions[0].target_soc_pct is not None

    def test_release_requires_hysteresis(self) -> None:
        actions = check_soc_balance(
            _obs(huawei_soc=57, victron_soc=50),
            threshold_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            balancing_active=True,
        )
        assert len(actions) == 1

    def test_release_below_hysteresis(self) -> None:
        actions = check_soc_balance(
            _obs(huawei_soc=53, victron_soc=50),
            threshold_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            balancing_active=True,
        )
        assert actions == []

    def test_skip_if_held(self) -> None:
        actions = check_soc_balance(
            _obs(huawei_soc=80, victron_soc=60),
            threshold_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.HELD,
            victron_state=BatteryState.AUTONOMOUS,
            balancing_active=False,
        )
        assert actions == []


class TestEvaluateInterventions:
    def test_no_interventions_normal_state(self) -> None:
        result = evaluate_interventions(
            obs=_obs(huawei_soc=50, victron_soc=50),
            min_soc_pct=10.0,
            min_soc_hysteresis_pct=5.0,
            soc_balance_threshold_pct=10.0,
            soc_balance_hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            active_slot=None,
            cross_charge_clear_count=0,
            balancing_active=False,
        )
        assert result.huawei_state == BatteryState.AUTONOMOUS
        assert result.victron_state == BatteryState.AUTONOMOUS
        assert result.actions == []

    def test_min_soc_overrides_soc_balance(self) -> None:
        result = evaluate_interventions(
            obs=_obs(huawei_soc=5, victron_soc=30),
            min_soc_pct=10.0,
            min_soc_hysteresis_pct=5.0,
            soc_balance_threshold_pct=10.0,
            soc_balance_hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            active_slot=None,
            cross_charge_clear_count=0,
            balancing_active=False,
        )
        assert result.huawei_state == BatteryState.HELD
        assert result.victron_state == BatteryState.AUTONOMOUS

    def test_grid_charge_applied(self) -> None:
        slot = _make_slot(battery="huawei", target_soc_pct=80, grid_charge_power_w=3000)
        result = evaluate_interventions(
            obs=_obs(huawei_soc=50, victron_soc=50),
            min_soc_pct=10.0,
            min_soc_hysteresis_pct=5.0,
            soc_balance_threshold_pct=10.0,
            soc_balance_hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            active_slot=slot,
            cross_charge_clear_count=0,
            balancing_active=False,
        )
        assert result.huawei_state == BatteryState.GRID_CHARGING
        assert result.victron_state == BatteryState.AUTONOMOUS

    def test_cross_charge_holds_charging_system(self) -> None:
        result = evaluate_interventions(
            obs=_obs(huawei_soc=50, victron_soc=50, huawei_power=-1000, victron_power=500, pv_power=0),
            min_soc_pct=10.0,
            min_soc_hysteresis_pct=5.0,
            soc_balance_threshold_pct=10.0,
            soc_balance_hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            active_slot=None,
            cross_charge_clear_count=0,
            balancing_active=False,
        )
        assert result.victron_state == BatteryState.HELD
