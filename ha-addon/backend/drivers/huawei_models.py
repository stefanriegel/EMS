"""Huawei SUN2000 / LUNA2000 data model dataclasses.

These are the boundary contract types that the orchestrator (S03) imports.
Field names and types must not be renamed without a coordinated update to S03.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HuaweiMasterData:
    """State snapshot from the master SUN2000 inverter (unit ID 0).

    The master inverter is connected to LUNA2000 battery packs, so it
    exposes both PV generation and storage telemetry.
    """

    pv_input_power_w: int
    """DC PV generation in watts (register ``input_power``, 32064)."""

    active_power_w: int
    """AC active power output in watts (register ``active_power``, 32080).
    Positive = exporting to grid; negative = importing from grid.
    """

    pv_01_voltage_v: float
    """PV string 1 voltage in volts (register ``pv_01_voltage``, 32016)."""

    pv_01_current_a: float
    """PV string 1 current in amperes (register ``pv_01_current``, 32017)."""

    pv_02_voltage_v: float
    """PV string 2 voltage in volts (register ``pv_02_voltage``, 32018)."""

    pv_02_current_a: float
    """PV string 2 current in amperes (register ``pv_02_current``, 32019)."""

    device_status: int | None
    """Raw device status code (register ``state_1``, 32089). ``None`` if
    the register was not readable (e.g. during startup).
    """


@dataclass
class HuaweiSlaveData:
    """State snapshot from the slave SUN2000 inverter (unit ID 2).

    The slave inverter is PV-only — no battery packs are connected.
    """

    pv_input_power_w: int
    """DC PV generation in watts (register ``input_power``, 32064)."""

    active_power_w: int
    """AC active power output in watts (register ``active_power``, 32080)."""

    pv_01_voltage_v: float
    """PV string 1 voltage in volts."""

    pv_01_current_a: float
    """PV string 1 current in amperes."""

    pv_02_voltage_v: float
    """PV string 2 voltage in volts."""

    pv_02_current_a: float
    """PV string 2 current in amperes."""

    device_status: int | None
    """Raw device status code. ``None`` if not readable."""


@dataclass
class HuaweiBatteryData:
    """State snapshot from the LUNA2000 battery system attached to the master.

    Sign convention (matching Huawei register semantics):
      * ``total_charge_discharge_power_w > 0``  →  **charging**
      * ``total_charge_discharge_power_w < 0``  →  **discharging**
      * ``total_charge_discharge_power_w == 0`` →  idle

    Use the ``charge_power_w`` and ``discharge_power_w`` properties to get
    non-negative, directional values for energy accounting.

    Pack 2 fields are ``None`` when only a single LUNA2000 pack is present.
    """

    # --- Pack 1 (always present) ---
    pack1_soc_pct: float
    """State of charge for pack 1, 0–100 %."""

    pack1_charge_discharge_power_w: int
    """Charge/discharge power for pack 1 in watts (positive = charging)."""

    pack1_status: int | None
    """Raw status register for pack 1; ``None`` if unavailable."""

    # --- Pack 2 (optional — absent on single-pack installations) ---
    pack2_soc_pct: float | None
    """State of charge for pack 2, 0–100 %, or ``None``."""

    pack2_charge_discharge_power_w: int | None
    """Charge/discharge power for pack 2 in watts, or ``None``."""

    pack2_status: int | None
    """Raw status register for pack 2; ``None`` if unavailable."""

    # --- Combined / system-level ---
    total_soc_pct: float
    """Combined system state of charge, 0–100 %."""

    total_charge_discharge_power_w: int
    """Combined charge/discharge power in watts (positive = charging)."""

    max_charge_power_w: int
    """Maximum allowed charge power in watts at this moment."""

    max_discharge_power_w: int
    """Maximum allowed discharge power in watts at this moment."""

    working_mode: int | None
    """Raw working-mode register value; ``None`` if unavailable."""

    # --- Derived: system-level direction split ---

    @property
    def charge_power_w(self) -> int:
        """Charging power in watts; 0 when idle or discharging."""
        return max(0, self.total_charge_discharge_power_w)

    @property
    def discharge_power_w(self) -> int:
        """Discharging power in watts; 0 when idle or charging."""
        return max(0, -self.total_charge_discharge_power_w)

    # --- Derived: pack-1 direction split ---

    @property
    def pack1_charge_power_w(self) -> int:
        """Pack 1 charging power in watts; 0 when idle or discharging."""
        return max(0, self.pack1_charge_discharge_power_w)

    @property
    def pack1_discharge_power_w(self) -> int:
        """Pack 1 discharging power in watts; 0 when idle or charging."""
        return max(0, -self.pack1_charge_discharge_power_w)
