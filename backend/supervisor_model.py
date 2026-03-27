from __future__ import annotations

import enum
from dataclasses import dataclass, field

HUAWEI_CAPACITY_KWH = 30.0
VICTRON_CAPACITY_KWH = 64.0
TOTAL_CAPACITY_KWH = HUAWEI_CAPACITY_KWH + VICTRON_CAPACITY_KWH


class BatteryState(str, enum.Enum):
    AUTONOMOUS = "AUTONOMOUS"
    HELD = "HELD"
    GRID_CHARGING = "GRID_CHARGING"


@dataclass
class Observation:
    huawei_soc_pct: float
    victron_soc_pct: float
    huawei_power_w: float
    victron_power_w: float
    pv_power_w: float
    emma_load_power_w: float
    victron_consumption_w: float
    huawei_available: bool
    victron_available: bool
    timestamp: float

    @property
    def pool_soc(self) -> float:
        return (
            self.huawei_soc_pct * HUAWEI_CAPACITY_KWH
            + self.victron_soc_pct * VICTRON_CAPACITY_KWH
        ) / TOTAL_CAPACITY_KWH

    @property
    def soc_delta(self) -> float:
        return abs(self.huawei_soc_pct - self.victron_soc_pct)

    @property
    def true_consumption_w(self) -> float:
        return self.emma_load_power_w + self.victron_consumption_w


@dataclass
class InterventionAction:
    target_system: str  # "huawei" or "victron"
    target_state: BatteryState
    max_discharge_power_w: int | None = None
    target_soc_pct: float | None = None
    charge_power_w: int | None = None


@dataclass
class InterventionRecord:
    timestamp: str
    intervention_type: str
    target_system: str
    action: BatteryState
    reason: str


@dataclass
class SupervisorState:
    pool_soc_pct: float
    huawei_soc_pct: float
    victron_soc_pct: float
    soc_delta: float
    huawei_state: BatteryState
    victron_state: BatteryState
    huawei_available: bool
    victron_available: bool
    true_consumption_w: float
    pv_power_w: float
    active_interventions: list[InterventionRecord] = field(default_factory=list)
    timestamp: float = 0.0
    grid_charge_slot_active: bool = False
    control_mode: str = "supervisory"
