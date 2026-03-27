"""Periodic health logger — writes diagnostic snapshots to InfluxDB.

Runs as a background task inside the coordinator loop. Every 5 minutes,
captures a comprehensive system health snapshot including:
- Battery coordination metrics (SoC imbalance, cross-charge waste)
- Real-time grid / PV / consumption figures
- ML model health (training age, sample count, MAPE, last prediction)
- Scheduler state (schedule staleness, slot count, solar/consumption forecast)
- Integration availability for every subsystem
- Anomaly flags

Data is written to the ``ems_health`` InfluxDB measurement for later
analysis and ML troubleshooting.  This replaces the need for an external
monitoring agent.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_INTERVAL_S = 300  # 5 minutes


@dataclass
class HealthSnapshot:
    """Periodic health metrics for trend analysis."""

    timestamp: datetime

    # ── Battery state ──────────────────────────────────────────────────────
    huawei_soc_pct: float
    victron_soc_pct: float
    combined_soc_pct: float
    soc_imbalance_pct: float
    huawei_power_w: float
    victron_power_w: float
    huawei_max_discharge_w: float
    victron_max_discharge_w: float

    # ── Real-time energy flows ─────────────────────────────────────────────
    pv_power_w: float            # PV generation (EMMA or Huawei fallback)
    grid_power_w: float          # Grid import (+) / export (-) via Victron
    true_consumption_w: float    # EMMA load + Victron discharge
    victron_grid_l1_w: float
    victron_grid_l2_w: float
    victron_grid_l3_w: float

    # ── Control state ──────────────────────────────────────────────────────
    control_state: str           # IDLE / CHARGE / DISCHARGE / GRID_CHARGE …
    pool_status: str             # NORMAL / DEGRADED
    huawei_role: str             # HOLDING / CHARGING / DISCHARGING
    victron_role: str
    huawei_setpoint_w: float
    victron_setpoint_w: float

    # ── Cross-charge ───────────────────────────────────────────────────────
    cross_charge_active: bool
    cross_charge_waste_wh: float
    cross_charge_episodes: int

    # ── Commissioning / shadow ─────────────────────────────────────────────
    shadow_mode: bool
    commissioning_stage: str

    # ── ML forecaster health ───────────────────────────────────────────────
    ml_trained: bool             # True when at least one model is trained
    ml_days_of_history: int      # Days of HA stats data used for training
    ml_total_samples: int        # Total hourly samples in training set
    ml_last_prediction_kwh: float | None   # Last total-day prediction in kWh
    ml_last_trained_age_h: float | None    # Hours since last training run
    ml_last_mape_pct: float | None         # Most recent MAPE % (if available)

    # ── Scheduler / optimisation health ───────────────────────────────────
    sched_has_schedule: bool       # Whether an active schedule exists
    sched_stale: bool              # True if schedule couldn't be refreshed
    sched_slot_count: int          # Number of charge slots planned
    sched_solar_forecast_kwh: float        # Tomorrow solar forecast used
    sched_consumption_forecast_kwh: float  # Expected consumption used
    sched_target_soc_pct: float            # Huawei target SoC for tonight

    # ── Integration availability ───────────────────────────────────────────
    huawei_available: bool
    victron_available: bool
    emma_available: bool
    influx_available: bool
    ha_mqtt_available: bool
    evcc_available: bool
    telegram_available: bool

    # ── Anomaly flags ──────────────────────────────────────────────────────
    flag_soc_imbalance: bool
    flag_cross_charge: bool
    flag_system_degraded: bool
    flag_ml_stale: bool          # True if ML not trained or >25h since last train
    flag_sched_stale: bool       # True if schedule is stale


def _ml_metrics(forecaster: object | None) -> dict:
    """Extract ML health fields from an optional ConsumptionForecaster."""
    if forecaster is None:
        return {
            "trained": False,
            "days_of_history": 0,
            "total_samples": 0,
            "last_prediction_kwh": None,
            "last_trained_age_h": None,
            "last_mape_pct": None,
        }
    trained = (
        getattr(forecaster, "_heat_pump_model", None) is not None
        or getattr(forecaster, "_dhw_model", None) is not None
        or getattr(forecaster, "_base_model", None) is not None
    )
    last_trained_at: datetime | None = getattr(forecaster, "_last_trained_at", None)
    age_h: float | None = None
    if last_trained_at is not None:
        delta = datetime.now(tz=timezone.utc) - last_trained_at
        age_h = delta.total_seconds() / 3600.0

    # Last MAPE from the persisted history file
    last_mape: float | None = None
    mape_path = getattr(forecaster, "_mape_path", None)
    if mape_path is not None:
        try:
            import json
            history = json.loads(mape_path.read_text())
            if history:
                last_mape = history[-1].get("mape")
        except Exception:  # noqa: BLE001
            pass

    return {
        "trained": trained,
        "days_of_history": int(getattr(forecaster, "_days_of_history", 0)),
        "total_samples": int(getattr(forecaster, "_total_samples", 0)),
        "last_prediction_kwh": getattr(forecaster, "_last_prediction_kwh", None),
        "last_trained_age_h": age_h,
        "last_mape_pct": last_mape,
    }


def _sched_metrics(scheduler: object | None) -> dict:
    """Extract scheduler health fields from an optional Scheduler/WeatherScheduler."""
    empty = {
        "has_schedule": False,
        "stale": False,
        "slot_count": 0,
        "solar_forecast_kwh": 0.0,
        "consumption_forecast_kwh": 0.0,
        "target_soc_pct": 0.0,
    }
    if scheduler is None:
        return empty

    # WeatherScheduler wraps a plain Scheduler; try both
    inner = getattr(scheduler, "_scheduler", scheduler)
    schedule = getattr(inner, "active_schedule", None)
    if schedule is None:
        return empty

    reasoning = getattr(schedule, "reasoning", None)
    return {
        "has_schedule": True,
        "stale": bool(getattr(schedule, "stale", False)),
        "slot_count": len(getattr(schedule, "slots", [])),
        "solar_forecast_kwh": float(getattr(reasoning, "tomorrow_solar_kwh", 0.0) if reasoning else 0.0),
        "consumption_forecast_kwh": float(getattr(reasoning, "expected_consumption_kwh", 0.0) if reasoning else 0.0),
        "target_soc_pct": float(getattr(schedule, "target_soc_pct", 0.0) if hasattr(schedule, "target_soc_pct") else 0.0),
    }


class HealthLogger:
    """Background health logger that writes to InfluxDB every 5 minutes."""

    def __init__(self) -> None:
        self._last_log_time: float = 0.0
        self._snapshots: list[HealthSnapshot] = []
        self._max_snapshots = 288  # 24h at 5-min intervals

    def should_log(self) -> bool:
        """Return True if enough time has elapsed since last log."""
        return (time.monotonic() - self._last_log_time) >= _INTERVAL_S

    def capture(  # noqa: PLR0913
        self,
        # battery
        h_soc: float,
        v_soc: float,
        h_power: float,
        v_power: float,
        h_max_discharge_w: float,
        v_max_discharge_w: float,
        # energy flows
        pv_power: float,
        grid_power: float,
        true_consumption: float,
        v_l1_w: float,
        v_l2_w: float,
        v_l3_w: float,
        # control state
        control_state: str,
        pool_status: str,
        h_role: str,
        v_role: str,
        h_setpoint_w: float,
        v_setpoint_w: float,
        # cross-charge
        cross_charge_active: bool,
        cross_charge_waste: float,
        cross_charge_episodes: int,
        # commissioning
        shadow_mode: bool,
        commissioning_stage: str,
        # availability
        huawei_available: bool,
        victron_available: bool,
        emma_available: bool,
        influx_available: bool,
        ha_mqtt_available: bool,
        evcc_available: bool,
        telegram_available: bool,
        # optional rich objects
        forecaster: object | None = None,
        scheduler: object | None = None,
    ) -> HealthSnapshot:
        """Capture a health snapshot and return it for InfluxDB writing."""
        self._last_log_time = time.monotonic()

        imbalance = abs(h_soc - v_soc)
        combined_soc = (h_soc + v_soc) / 2.0

        ml = _ml_metrics(forecaster)
        sched = _sched_metrics(scheduler)

        flag_ml_stale = not ml["trained"] or (
            ml["last_trained_age_h"] is not None and ml["last_trained_age_h"] > 25.0
        )

        snap = HealthSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            # battery
            huawei_soc_pct=h_soc,
            victron_soc_pct=v_soc,
            combined_soc_pct=combined_soc,
            soc_imbalance_pct=imbalance,
            huawei_power_w=h_power,
            victron_power_w=v_power,
            huawei_max_discharge_w=h_max_discharge_w,
            victron_max_discharge_w=v_max_discharge_w,
            # energy flows
            pv_power_w=pv_power,
            grid_power_w=grid_power,
            true_consumption_w=true_consumption,
            victron_grid_l1_w=v_l1_w,
            victron_grid_l2_w=v_l2_w,
            victron_grid_l3_w=v_l3_w,
            # control state
            control_state=control_state,
            pool_status=pool_status,
            huawei_role=h_role,
            victron_role=v_role,
            huawei_setpoint_w=h_setpoint_w,
            victron_setpoint_w=v_setpoint_w,
            # cross-charge
            cross_charge_active=cross_charge_active,
            cross_charge_waste_wh=cross_charge_waste,
            cross_charge_episodes=cross_charge_episodes,
            # commissioning
            shadow_mode=shadow_mode,
            commissioning_stage=commissioning_stage,
            # ML
            ml_trained=ml["trained"],
            ml_days_of_history=ml["days_of_history"],
            ml_total_samples=ml["total_samples"],
            ml_last_prediction_kwh=ml["last_prediction_kwh"],
            ml_last_trained_age_h=ml["last_trained_age_h"],
            ml_last_mape_pct=ml["last_mape_pct"],
            # scheduler
            sched_has_schedule=sched["has_schedule"],
            sched_stale=sched["stale"],
            sched_slot_count=sched["slot_count"],
            sched_solar_forecast_kwh=sched["solar_forecast_kwh"],
            sched_consumption_forecast_kwh=sched["consumption_forecast_kwh"],
            sched_target_soc_pct=sched["target_soc_pct"],
            # availability
            huawei_available=huawei_available,
            victron_available=victron_available,
            emma_available=emma_available,
            influx_available=influx_available,
            ha_mqtt_available=ha_mqtt_available,
            evcc_available=evcc_available,
            telegram_available=telegram_available,
            # flags
            flag_soc_imbalance=imbalance > 30.0,
            flag_cross_charge=cross_charge_waste > 100.0,
            flag_system_degraded=not (huawei_available and victron_available),
            flag_ml_stale=flag_ml_stale,
            flag_sched_stale=sched["stale"],
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
        if snap.flag_ml_stale:
            logger.warning(
                "Health: ML forecaster stale — trained=%s age_h=%s days=%d",
                ml["trained"], ml["last_trained_age_h"], ml["days_of_history"],
            )

        return snap

    def get_recent(self, count: int = 12) -> list[HealthSnapshot]:
        """Return the last N snapshots (default 12 = last hour)."""
        return self._snapshots[-count:]
