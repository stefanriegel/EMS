"""Periodic health logger — writes diagnostic snapshots to InfluxDB.

Runs as a background task inside the coordinator loop. Every 5 minutes,
captures a comprehensive system health snapshot including:
- Battery coordination metrics (SoC imbalance, cross-charge waste)
- PV utilization efficiency
- Decision pattern statistics
- Anomalies and flags

Data is written to the ``ems_health`` InfluxDB measurement for later
analysis. This replaces the need for an external monitoring agent.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_INTERVAL_S = 300  # 5 minutes


@dataclass
class HealthSnapshot:
    """Periodic health metrics for trend analysis."""

    timestamp: datetime
    huawei_soc_pct: float
    victron_soc_pct: float
    soc_imbalance_pct: float
    huawei_power_w: float
    victron_power_w: float
    pv_power_w: float
    true_consumption_w: float
    cross_charge_active: bool
    cross_charge_waste_wh: float
    cross_charge_episodes: int
    shadow_mode: bool
    commissioning_stage: str
    huawei_available: bool
    victron_available: bool
    emma_available: bool
    influx_available: bool
    # Flags
    flag_soc_imbalance: bool
    flag_cross_charge: bool
    flag_system_degraded: bool


class HealthLogger:
    """Background health logger that writes to InfluxDB every 5 minutes."""

    def __init__(self) -> None:
        self._last_log_time: float = 0.0
        self._snapshots: list[HealthSnapshot] = []
        self._max_snapshots = 288  # 24h at 5-min intervals

    def should_log(self) -> bool:
        """Return True if enough time has elapsed since last log."""
        return (time.monotonic() - self._last_log_time) >= _INTERVAL_S

    def capture(
        self,
        h_soc: float,
        v_soc: float,
        h_power: float,
        v_power: float,
        pv_power: float,
        true_consumption: float,
        cross_charge_active: bool,
        cross_charge_waste: float,
        cross_charge_episodes: int,
        shadow_mode: bool,
        commissioning_stage: str,
        huawei_available: bool,
        victron_available: bool,
        emma_available: bool,
        influx_available: bool,
    ) -> HealthSnapshot:
        """Capture a health snapshot and return it for InfluxDB writing."""
        self._last_log_time = time.monotonic()

        imbalance = abs(h_soc - v_soc)

        snap = HealthSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            huawei_soc_pct=h_soc,
            victron_soc_pct=v_soc,
            soc_imbalance_pct=imbalance,
            huawei_power_w=h_power,
            victron_power_w=v_power,
            pv_power_w=pv_power,
            true_consumption_w=true_consumption,
            cross_charge_active=cross_charge_active,
            cross_charge_waste_wh=cross_charge_waste,
            cross_charge_episodes=cross_charge_episodes,
            shadow_mode=shadow_mode,
            commissioning_stage=commissioning_stage,
            huawei_available=huawei_available,
            victron_available=victron_available,
            emma_available=emma_available,
            influx_available=influx_available,
            flag_soc_imbalance=imbalance > 30.0,
            flag_cross_charge=cross_charge_waste > 100.0,
            flag_system_degraded=not (huawei_available and victron_available),
        )

        self._snapshots.append(snap)
        if len(self._snapshots) > self._max_snapshots:
            self._snapshots.pop(0)

        if snap.flag_soc_imbalance:
            logger.warning(
                "Health: SoC imbalance %.1f%% (H=%.1f%% V=%.1f%%)",
                imbalance, h_soc, v_soc,
            )
        if snap.flag_cross_charge:
            logger.warning(
                "Health: cross-charge waste %.1f Wh over %d episodes",
                cross_charge_waste, cross_charge_episodes,
            )

        return snap

    def get_recent(self, count: int = 12) -> list[HealthSnapshot]:
        """Return the last N snapshots (default 12 = last hour)."""
        return self._snapshots[-count:]
