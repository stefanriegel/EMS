"""Controller model types for the independent dual-battery coordinator.

Defines the typed contracts consumed by the coordinator (Plan 02) and
produced by the per-battery controllers (HuaweiController, VictronController).

Enums use the ``str`` mixin for direct JSON serialization without a custom
encoder.

``CoordinatorState`` is a backward-compatible superset of ``UnifiedPoolState``
with additional per-system role and pool-health fields.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class BatteryRole(str, Enum):
    """Role assigned to an individual battery system by the coordinator.

    Each battery is assigned exactly one role per control cycle.
    """

    PRIMARY_DISCHARGE = "PRIMARY_DISCHARGE"
    """Battery is the primary source for household load coverage."""

    SECONDARY_DISCHARGE = "SECONDARY_DISCHARGE"
    """Battery supplements the primary when demand exceeds its capacity."""

    CHARGING = "CHARGING"
    """Battery is actively charging from surplus PV."""

    HOLDING = "HOLDING"
    """Battery is idle — zero power setpoint applied."""

    GRID_CHARGE = "GRID_CHARGE"
    """Battery is charging from the grid during a cheap-tariff window."""

    EXPORTING = "EXPORTING"
    """Battery system is allowing PV surplus to flow to grid (no active discharge)."""


class PoolStatus(str, Enum):
    """Health status of the combined battery pool."""

    NORMAL = "NORMAL"
    """Both battery systems are online and reporting fresh data."""

    DEGRADED = "DEGRADED"
    """One battery system is offline or reporting stale data."""

    OFFLINE = "OFFLINE"
    """Both battery systems are offline."""


@dataclass
class ControllerSnapshot:
    """Point-in-time state from a single battery controller.

    Produced by ``HuaweiController.poll()`` or ``VictronController.poll()``
    on every control cycle.  Contains both common fields and optional
    hardware-specific fields (None when not applicable).
    """

    soc_pct: float
    """State of charge, 0-100 %."""

    power_w: float
    """Instantaneous power in watts. Positive = charging, negative = discharging."""

    available: bool
    """True when the controller returned fresh data this cycle."""

    role: BatteryRole
    """Current role assigned to this battery."""

    consecutive_failures: int
    """Number of consecutive poll failures. Reset to 0 on success."""

    timestamp: float
    """``time.monotonic()`` value when this snapshot was produced."""

    # --- Optional hardware-specific fields ---

    max_charge_power_w: int | None = None
    """Maximum charge power in watts (Huawei-specific)."""

    max_discharge_power_w: int | None = None
    """Maximum discharge power in watts (Huawei-specific)."""

    charge_headroom_w: float = 0.0
    """Available charge headroom in watts (max_charge - current_charge)."""

    master_active_power_w: float | None = None
    """Huawei master inverter active power for P_target fallback."""

    pv_input_power_w: int | None = None
    """PV DC input power in watts (Huawei master inverter)."""

    slave_pv_power_w: int | None = None
    """PV DC input power in watts (Huawei slave inverter)."""

    grid_power_w: float | None = None
    """Total grid power in watts (Victron-specific)."""

    grid_l1_power_w: float | None = None
    """Per-phase L1 grid power in watts (Victron-specific)."""

    grid_l2_power_w: float | None = None
    """Per-phase L2 grid power in watts (Victron-specific)."""

    grid_l3_power_w: float | None = None
    """Per-phase L3 grid power in watts (Victron-specific)."""

    ess_mode: int | None = None
    """Victron ESS mode register value for guard check."""


@dataclass
class ControllerCommand:
    """Instruction from the coordinator to a battery controller.

    The controller translates this into hardware-specific driver calls
    using its own sign conventions.
    """

    role: BatteryRole
    """Target role for this control cycle."""

    target_watts: float
    """Target power in watts. Positive = charge, negative = discharge."""

    evcc_hold: bool = False
    """True when EVCC battery-hold mode is active (lock discharge to zero)."""


@dataclass
class CoordinatorState:
    """Full state snapshot from the coordinator.

    Backward-compatible superset of ``UnifiedPoolState`` with additional
    per-system role assignments and pool health status.
    """

    # --- Composite SoC (same as UnifiedPoolState) ---
    combined_soc_pct: float
    huawei_soc_pct: float
    victron_soc_pct: float

    # --- Availability ---
    huawei_available: bool
    victron_available: bool

    # --- Control ---
    control_state: str
    """Current operating mode (backward-compatible string, not ControlState enum)."""

    # --- Applied setpoints ---
    huawei_discharge_setpoint_w: int
    victron_discharge_setpoint_w: int

    # --- Power telemetry ---
    combined_power_w: float

    # --- Charge headroom ---
    huawei_charge_headroom_w: int
    victron_charge_headroom_w: float

    # --- Metadata ---
    timestamp: float

    # --- Flags (backward-compatible defaults) ---
    grid_charge_slot_active: bool = False
    export_active: bool = False
    """True when the export advisor recommends exporting (selling to grid)."""
    evcc_battery_mode: str = "normal"

    # --- New coordinator fields ---
    huawei_role: str = "HOLDING"
    """Current role of the Huawei battery system."""

    victron_role: str = "HOLDING"
    """Current role of the Victron battery system."""

    pool_status: str = "NORMAL"
    """Health status of the combined pool."""

    huawei_effective_min_soc_pct: float = 10.0
    """Effective min-SoC for Huawei (from profile or static fallback)."""

    victron_effective_min_soc_pct: float = 15.0
    """Effective min-SoC for Victron (from profile or static fallback)."""

    # --- Cross-charge detection ---
    cross_charge_active: bool = False
    """True when cross-charge is currently detected between batteries."""

    cross_charge_waste_wh: float = 0.0
    """Cumulative waste energy from cross-charge episodes in Wh."""

    cross_charge_episode_count: int = 0
    """Total number of cross-charge episodes detected."""

    huawei_working_mode: str = "unknown"
    """Current Huawei storage working mode (e.g. 'TIME_OF_USE_LUNA2000', 'MAXIMISE_SELF_CONSUMPTION')."""

    commissioning_stage: str = "DUAL_BATTERY"
    """Current commissioning stage (READ_ONLY, SINGLE_BATTERY, DUAL_BATTERY).
    Default DUAL_BATTERY for backward compatibility (fully operational)."""

    commissioning_shadow_mode: bool = False
    """True when shadow mode is active (decisions logged, writes suppressed)."""

    # --- DESS/VRM integration ---
    dess_mode: int = 0
    """DESS mode: 0=off, 1=auto(VRM), 4=Node-RED."""

    dess_active_slot: int | None = None
    """Index of the currently active DESS schedule slot, or None."""

    dess_available: bool = False
    """True when DESS MQTT subscriber is connected and receiving data."""

    vrm_available: bool = False
    """True when VRM REST client is fetching fresh diagnostics."""


@dataclass
class DecisionEntry:
    """Single coordinator dispatch decision for the audit trail."""

    timestamp: str
    """ISO 8601 UTC timestamp of this decision."""

    trigger: str
    """Decision trigger: role_change, hold_signal, slot_start, slot_end, failover, allocation_shift."""

    huawei_role: str
    victron_role: str
    p_target_w: float
    huawei_allocation_w: float
    victron_allocation_w: float
    pool_status: str
    reasoning: str
    """Human-readable WHY text."""


@dataclass
class IntegrationStatus:
    """Health status of a single external integration."""

    service: str
    available: bool
    last_error: str | None = None
    last_seen: datetime | None = None
