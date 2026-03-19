"""Unified data model for the EMS orchestration layer (S03).

``UnifiedPoolState`` is the primary state object produced by the orchestrator
on every control cycle and consumed by the API layer (S05/S06) and any
downstream consumers.

``ControlState`` encodes the current operating mode of the combined 94 kWh
battery pool.

Capacity constants reflect the physical installation:
  - Huawei LUNA2000:  30 kWh
  - Victron MPII:     64 kWh
  - Combined pool:    94 kWh

Weighted-average SoC formula:
  ``combined_soc_pct = (huawei_soc * 30 + victron_soc * 64) / 94``
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from backend.drivers.huawei_models import HuaweiBatteryData
from backend.drivers.victron_models import VictronSystemData

# Physical capacity constants (kWh)
_HUAWEI_KWH: float = 30.0
_VICTRON_KWH: float = 64.0
_TOTAL_KWH: float = _HUAWEI_KWH + _VICTRON_KWH  # 94.0


class ControlState(str, Enum):
    """Operating mode of the combined battery pool.

    ``str`` mixin ensures JSON-serialisable values without an extra encoder.
    """

    IDLE = "IDLE"
    """Neither charging nor discharging — pool is holding steady."""

    DISCHARGE = "DISCHARGE"
    """Pool is actively discharging to cover load or export."""

    CHARGE = "CHARGE"
    """Pool is actively charging from surplus PV or grid."""

    HOLD = "HOLD"
    """Orchestrator is actively clamping setpoints to prevent movement
    (e.g. one driver offline, SoC limit reached, debounce window)."""


@dataclass
class UnifiedPoolState:
    """Full state snapshot of the combined 94 kWh battery pool.

    Produced by the orchestrator on every control cycle.  All fields are
    populated from live driver readings and applied setpoints; no field is
    optional — callers can rely on every value being present.

    Power sign conventions:
      * Positive  → charging (battery is absorbing energy)
      * Negative  → discharging (battery is supplying energy)

    Setpoint fields carry the *applied* value from the most-recent cycle;
    zero when the respective system is offline or in HOLD/IDLE.
    """

    # --- Composite SoC ---
    combined_soc_pct: float
    """Capacity-weighted average state of charge across both systems, 0–100 %."""

    huawei_soc_pct: float
    """Huawei LUNA2000 state of charge, 0–100 %."""

    victron_soc_pct: float
    """Victron MPII battery state of charge, 0–100 %."""

    # --- Availability ---
    huawei_available: bool
    """True when the Huawei driver returned fresh data this cycle."""

    victron_available: bool
    """True when the Victron driver returned fresh data this cycle."""

    # --- Control ---
    control_state: ControlState
    """Current operating mode applied by the orchestrator."""

    # --- Applied setpoints ---
    huawei_discharge_setpoint_w: int
    """Discharge power setpoint sent to Huawei this cycle, in watts."""

    victron_discharge_setpoint_w: int
    """Discharge power setpoint sent to Victron this cycle, in watts."""

    # --- Power telemetry ---
    combined_power_w: float
    """Total instantaneous battery power across both systems, in watts.
    Positive = charging; negative = discharging.
    """

    # --- Charge headroom ---
    huawei_charge_headroom_w: int
    """How much additional charge power the Huawei system can accept this
    cycle: ``max_charge_power_w - current_charge_power_w``.  Always >= 0.
    """

    victron_charge_headroom_w: float
    """How much additional charge power the Victron system can accept this
    cycle.  Always >= 0.0.
    """

    # --- Metadata ---
    timestamp: float
    """``time.monotonic()`` value when this snapshot was constructed."""

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_readings(
        cls,
        battery: HuaweiBatteryData,
        victron: VictronSystemData,
        control_state: ControlState,
        setpoints: tuple[int, float],
    ) -> "UnifiedPoolState":
        """Construct a :class:`UnifiedPoolState` from live driver readings.

        Args:
            battery: Latest :class:`~backend.drivers.huawei_models.HuaweiBatteryData`
                from the Huawei driver.  Must not be ``None``; pass a zeroed
                sentinel if the driver is offline.
            victron: Latest :class:`~backend.drivers.victron_models.VictronSystemData`
                from the Victron driver.
            control_state: The :class:`ControlState` the orchestrator is
                transitioning *into* this cycle.
            setpoints: ``(huawei_w, victron_w)`` tuple of the setpoints
                applied this cycle.  Both are discharge magnitudes in watts
                (positive = discharging).

        Returns:
            A fully-populated :class:`UnifiedPoolState` snapshot.

        Notes:
            * Always uses ``battery.total_soc_pct`` — never pack-level fields —
              because pack 2 is optional and absent on single-pack installations.
            * ``combined_soc_pct`` uses the 30/64/94 kWh capacity weighting.
            * ``huawei_charge_headroom_w`` is clamped to zero from below.
        """
        huawei_soc = battery.total_soc_pct
        victron_soc = victron.battery_soc_pct

        combined_soc = (huawei_soc * _HUAWEI_KWH + victron_soc * _VICTRON_KWH) / _TOTAL_KWH

        huawei_w, victron_w = setpoints

        # Headroom = how much charge capacity is currently unused.
        huawei_headroom = max(0, battery.max_charge_power_w - battery.charge_power_w)
        victron_headroom = max(0.0, victron.charge_power_w)  # Victron doesn't expose max; use 0 base

        combined_power = battery.total_charge_discharge_power_w + victron.battery_power_w

        return cls(
            combined_soc_pct=combined_soc,
            huawei_soc_pct=huawei_soc,
            victron_soc_pct=victron_soc,
            huawei_available=True,
            victron_available=True,
            control_state=control_state,
            huawei_discharge_setpoint_w=huawei_w,
            victron_discharge_setpoint_w=victron_w,
            combined_power_w=combined_power,
            huawei_charge_headroom_w=huawei_headroom,
            victron_charge_headroom_w=victron_headroom,
            timestamp=time.monotonic(),
        )

    # ------------------------------------------------------------------
    # Staleness check
    # ------------------------------------------------------------------

    def is_stale(self, max_age_s: float) -> bool:
        """Return True if this snapshot is older than *max_age_s* seconds.

        Uses ``time.monotonic()`` to avoid wall-clock skew.

        Args:
            max_age_s: Maximum acceptable age in seconds.

        Returns:
            ``True`` when ``time.monotonic() - self.timestamp > max_age_s``.
        """
        return time.monotonic() - self.timestamp > max_age_s
