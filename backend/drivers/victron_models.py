"""Victron Multiplus II 3-phase system data model dataclasses.

These are the S03 boundary contract types — the orchestrator imports
``VictronSystemData`` and ``VictronPhaseData`` from this module.  Field names
and types must not be renamed without a coordinated update to S03.

Sign convention for battery power:
  * ``battery_power_w > 0``  →  **charging**
  * ``battery_power_w < 0``  →  **discharging**
  * ``battery_power_w == 0`` →  idle

Use ``charge_power_w`` and ``discharge_power_w`` properties to obtain
non-negative, directional values for energy accounting.

``VictronPhaseData.setpoint_w`` is ``None`` until a setpoint readback
arrives via MQTT (topic ``W/.../AcPowerSetpoint``).

``VictronSystemData.timestamp`` is set to ``time.monotonic()`` at snapshot
time and lets the orchestrator (S03) detect stale data.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VictronPhaseData:
    """Live telemetry for a single AC phase (L1, L2, or L3).

    All power/current/voltage values are read from the VE.Bus MQTT tree.
    ``setpoint_w`` starts as ``None`` and is populated when the driver
    receives a readback on the AcPowerSetpoint topic for this phase.
    """

    power_w: float
    """AC power on this phase in watts."""

    current_a: float
    """AC current on this phase in amperes."""

    voltage_v: float
    """AC voltage on this phase in volts."""

    setpoint_w: float | None
    """Last written AcPowerSetpoint in watts, or ``None`` if not yet written."""


@dataclass
class VictronSystemData:
    """Full system snapshot from the Victron Multiplus II 3-phase installation.

    Covers battery state, per-phase AC power, and ESS control registers.
    Constructed by ``VictronDriver`` on each polling cycle and handed to S03.
    """

    # --- Battery ---

    battery_soc_pct: float
    """Battery state of charge, 0–100 %."""

    battery_power_w: float
    """Battery charge/discharge power in watts (positive = charging)."""

    battery_current_a: float
    """Battery current in amperes (positive = charging)."""

    battery_voltage_v: float
    """Battery voltage in volts."""

    # --- Per-phase AC ---

    l1: VictronPhaseData
    """Phase L1 telemetry."""

    l2: VictronPhaseData
    """Phase L2 telemetry."""

    l3: VictronPhaseData
    """Phase L3 telemetry."""

    # --- ESS / VE.Bus control state ---

    ess_mode: int | None
    """ESS mode register value, or ``None`` if not yet received."""

    system_state: int | None
    """System state register value, or ``None`` if not yet received."""

    vebus_state: int | None
    """VE.Bus state register value, or ``None`` if not yet received."""

    # --- Metadata ---

    timestamp: float
    """``time.monotonic()`` value at the time this snapshot was taken.
    S03 uses this to detect stale data.
    """

    # --- Derived: battery direction split ---

    @property
    def charge_power_w(self) -> float:
        """Charging power in watts; 0.0 when idle or discharging."""
        return max(0.0, self.battery_power_w)

    @property
    def discharge_power_w(self) -> float:
        """Discharging power in watts; 0.0 when idle or charging."""
        return max(0.0, -self.battery_power_w)
