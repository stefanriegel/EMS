from __future__ import annotations

from dataclasses import dataclass, field

from backend.schedule_models import ChargeSlot
from backend.supervisor_model import BatteryState, InterventionAction, Observation

PV_THRESHOLD_W = 100.0
HUAWEI_RATED_DISCHARGE_W = 5000
VICTRON_THROTTLE_SOC_OFFSET = 10.0


def check_min_soc(
    obs: Observation,
    min_soc_pct: float,
    hysteresis_pct: float,
    huawei_state: BatteryState,
    victron_state: BatteryState,
) -> list[InterventionAction]:
    """Hold batteries below min SoC. Release only above min + hysteresis."""
    release_threshold = min_soc_pct + hysteresis_pct
    actions: list[InterventionAction] = []

    for system, soc, current_state in [
        ("huawei", obs.huawei_soc_pct, huawei_state),
        ("victron", obs.victron_soc_pct, victron_state),
    ]:
        if soc < min_soc_pct:
            actions.append(
                InterventionAction(
                    target_system=system, target_state=BatteryState.HELD
                )
            )
        elif current_state == BatteryState.HELD and soc < release_threshold:
            actions.append(
                InterventionAction(
                    target_system=system, target_state=BatteryState.HELD
                )
            )

    return actions


def check_cross_charge(
    obs: Observation,
    consecutive_clear_count: int,
) -> list[InterventionAction]:
    """Detect one battery discharging while the other charges from grid.

    Cross-charge: battery powers have opposite signs AND PV < 100W.
    """
    h_discharging = obs.huawei_power_w < 0
    v_discharging = obs.victron_power_w < 0
    h_charging = obs.huawei_power_w > 0
    v_charging = obs.victron_power_w > 0
    low_pv = obs.pv_power_w < PV_THRESHOLD_W

    if h_discharging and v_charging and low_pv:
        return [
            InterventionAction(
                target_system="victron", target_state=BatteryState.HELD
            )
        ]
    if v_discharging and h_charging and low_pv:
        return [
            InterventionAction(
                target_system="huawei", target_state=BatteryState.HELD
            )
        ]
    return []


def check_grid_charge(
    active_slot: ChargeSlot | None,
) -> list[InterventionAction]:
    """Switch battery to grid-charge mode during cheap tariff window."""
    if active_slot is None:
        return []
    return [
        InterventionAction(
            target_system=active_slot.battery,
            target_state=BatteryState.GRID_CHARGING,
            target_soc_pct=active_slot.target_soc_pct,
            charge_power_w=active_slot.grid_charge_power_w,
        )
    ]


def check_soc_balance(
    obs: Observation,
    threshold_pct: float,
    hysteresis_pct: float,
    huawei_state: BatteryState,
    victron_state: BatteryState,
    balancing_active: bool,
) -> list[InterventionAction]:
    """Throttle higher-SoC battery when delta exceeds threshold."""
    delta = obs.soc_delta
    release_threshold = threshold_pct - hysteresis_pct

    if balancing_active and delta < release_threshold:
        return []
    if not balancing_active and delta <= threshold_pct:
        return []

    if obs.huawei_soc_pct > obs.victron_soc_pct:
        if huawei_state == BatteryState.HELD:
            return []
        return [
            InterventionAction(
                target_system="huawei",
                target_state=BatteryState.AUTONOMOUS,
                max_discharge_power_w=HUAWEI_RATED_DISCHARGE_W // 2,
            )
        ]
    else:
        if victron_state == BatteryState.HELD:
            return []
        floor_soc = max(0.0, obs.victron_soc_pct - VICTRON_THROTTLE_SOC_OFFSET)
        return [
            InterventionAction(
                target_system="victron",
                target_state=BatteryState.AUTONOMOUS,
                target_soc_pct=floor_soc,
            )
        ]


@dataclass
class InterventionResult:
    huawei_state: BatteryState
    victron_state: BatteryState
    actions: list[InterventionAction] = field(default_factory=list)
    huawei_max_discharge_w: int | None = None
    victron_target_soc_pct: float | None = None
    huawei_charge_power_w: int | None = None
    huawei_charge_target_soc_pct: float | None = None
    victron_charge_power_w: int | None = None
    victron_charge_target_soc_pct: float | None = None
    balancing_active: bool = False


def evaluate_interventions(
    obs: Observation,
    min_soc_pct: float,
    min_soc_hysteresis_pct: float,
    soc_balance_threshold_pct: float,
    soc_balance_hysteresis_pct: float,
    huawei_state: BatteryState,
    victron_state: BatteryState,
    active_slot: ChargeSlot | None,
    cross_charge_clear_count: int,
    balancing_active: bool,
) -> InterventionResult:
    """Evaluate all interventions in priority order, return resolved states."""
    h_state = BatteryState.AUTONOMOUS
    v_state = BatteryState.AUTONOMOUS
    all_actions: list[InterventionAction] = []
    result = InterventionResult(huawei_state=h_state, victron_state=v_state)

    # Priority 1: Min-SoC Guard
    for action in check_min_soc(obs, min_soc_pct, min_soc_hysteresis_pct, huawei_state, victron_state):
        if action.target_system == "huawei":
            h_state = action.target_state
        else:
            v_state = action.target_state
        all_actions.append(action)

    # Priority 2: Cross-Charge Prevention
    if h_state != BatteryState.HELD and v_state != BatteryState.HELD:
        for action in check_cross_charge(obs, cross_charge_clear_count):
            if action.target_system == "huawei" and h_state == BatteryState.AUTONOMOUS:
                h_state = action.target_state
                all_actions.append(action)
            elif action.target_system == "victron" and v_state == BatteryState.AUTONOMOUS:
                v_state = action.target_state
                all_actions.append(action)

    # Priority 3: Grid Charge Window
    for action in check_grid_charge(active_slot):
        if action.target_system == "huawei" and h_state == BatteryState.AUTONOMOUS:
            h_state = action.target_state
            result.huawei_charge_power_w = action.charge_power_w
            result.huawei_charge_target_soc_pct = action.target_soc_pct
            all_actions.append(action)
        elif action.target_system == "victron" and v_state == BatteryState.AUTONOMOUS:
            v_state = action.target_state
            result.victron_charge_power_w = action.charge_power_w
            result.victron_charge_target_soc_pct = action.target_soc_pct
            all_actions.append(action)

    # Priority 4: SoC Balancing
    balance_actions = check_soc_balance(
        obs, soc_balance_threshold_pct, soc_balance_hysteresis_pct,
        h_state, v_state, balancing_active,
    )
    for action in balance_actions:
        if action.target_system == "huawei" and h_state == BatteryState.AUTONOMOUS:
            result.huawei_max_discharge_w = action.max_discharge_power_w
            all_actions.append(action)
        elif action.target_system == "victron" and v_state == BatteryState.AUTONOMOUS:
            result.victron_target_soc_pct = action.target_soc_pct
            all_actions.append(action)
    result.balancing_active = len(balance_actions) > 0

    result.huawei_state = h_state
    result.victron_state = v_state
    result.actions = all_actions
    return result
