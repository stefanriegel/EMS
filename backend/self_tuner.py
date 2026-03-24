"""Adaptive parameter tuning engine for dead-bands, ramp rates, and min-SoC.

Accumulates per-cycle transition data from the coordinator's 5-second control
loop and computes nightly parameter adjustments.  Operates in shadow mode for
14 days (logging recommendations without applying), then auto-promotes to live
mode where computed parameters are pushed to the coordinator's runtime fields.

All computation is pure arithmetic -- no sklearn, no external I/O in the hot
path.  State persists to ``/config/ems_models/tuning_state.json``.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backend.config import MinSocWindow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TuningParams:
    """Current tuned parameter values."""

    huawei_deadband_w: int = 300
    victron_deadband_w: int = 150
    ramp_rate_w: int = 2000
    huawei_min_soc_profile: list[dict] | None = None
    victron_min_soc_profile: list[dict] | None = None


@dataclass
class TuningState:
    """Persisted tuning state."""

    mode: str = "shadow"  # "shadow" or "live"
    shadow_start_date: str | None = None
    shadow_days: int = 0
    current_params: dict = field(default_factory=dict)
    base_params: dict = field(default_factory=dict)
    previous_params: dict = field(default_factory=dict)
    last_oscillation_rate: float | None = None
    shadow_log: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    ha_overrides: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLAMP_RANGES: dict[str, tuple[float, float]] = {
    "huawei_deadband_w": (50, 1000),
    "victron_deadband_w": (50, 500),
    "ramp_rate_w": (100, 2000),
}

_OSCILLATION_HIGH = 6       # transitions/hour to increase dead-band
_OSCILLATION_LOW = 2        # transitions/hour to decrease dead-band
_SPIKE_THRESHOLD_W = 500    # grid import spike detection
_SPIKES_PER_DAY_HIGH = 3    # daily spike count to increase ramp rate
_SHADOW_DAYS_REQUIRED = 14
_ROLLBACK_INCREASE_PCT = 20.0
_MAX_ADJUST_PCT = 0.10      # 10% per night
_ADJUST_STEP_PCT = 0.05     # 5% step per recommendation
_CYCLES_PER_HOUR = 720      # 3600s / 5s
_MAX_HOURLY_STATS = 168     # 7 days of hourly data


# ---------------------------------------------------------------------------
# SelfTuner
# ---------------------------------------------------------------------------

class SelfTuner:
    """Adaptive parameter tuner for dead-bands, ramp rates, and min-SoC."""

    def __init__(
        self, state_path: str = "/config/ems_models/tuning_state.json",
    ) -> None:
        self._state_path = Path(state_path)
        self._state = self._load_state()
        self._coordinator: object | None = None

        # Per-cycle counters (in-memory only, reset each hour)
        self._prev_pool_status: str | None = None
        self._hourly_transitions: int = 0
        self._hourly_grid_spikes: int = 0
        self._cycle_count: int = 0
        self._hourly_stats: list[dict] = []

        # Ensure base/current params are initialized
        if not self._state.current_params:
            defaults = TuningParams()
            self._state.current_params = {
                "huawei_deadband_w": defaults.huawei_deadband_w,
                "victron_deadband_w": defaults.victron_deadband_w,
                "ramp_rate_w": defaults.ramp_rate_w,
            }
        if not self._state.base_params:
            self._state.base_params = dict(self._state.current_params)

    # ----- Coordinator injection -------------------------------------------

    def set_coordinator(self, coordinator: object) -> None:
        """Store a reference to the live Coordinator instance.

        Called once from ``main.py`` lifespan.  Enables ``_apply_params()``
        to push computed values to the coordinator's runtime fields.
        """
        self._coordinator = coordinator

    # ----- Hot-path: per-cycle recording -----------------------------------

    def record_cycle(
        self, pool_status: str, grid_power_w: float,
    ) -> None:
        """Called every 5 s from coordinator.  Zero I/O, pure counters."""
        # Detect transition
        is_transition = (
            self._prev_pool_status is not None
            and pool_status != self._prev_pool_status
        )
        if is_transition:
            self._hourly_transitions += 1

        # Grid spike only on transition (pitfall #2)
        if is_transition and grid_power_w > _SPIKE_THRESHOLD_W:
            self._hourly_grid_spikes += 1

        self._prev_pool_status = pool_status
        self._cycle_count += 1

        # Roll over hourly
        if self._cycle_count >= _CYCLES_PER_HOUR:
            self._hourly_stats.append({
                "transitions": self._hourly_transitions,
                "grid_spikes": self._hourly_grid_spikes,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            })
            self._hourly_transitions = 0
            self._hourly_grid_spikes = 0
            self._cycle_count = 0
            if len(self._hourly_stats) > _MAX_HOURLY_STATS:
                self._hourly_stats = self._hourly_stats[-_MAX_HOURLY_STATS:]

    # ----- HA override tracking --------------------------------------------

    def mark_ha_override(self, param_name: str) -> None:
        """Record that *param_name* was set by an HA command.

        The next ``nightly_tune()`` will skip tuning for this parameter.
        """
        self._state.ha_overrides[param_name] = (
            datetime.now(tz=timezone.utc).isoformat()
        )

    # ----- Nightly computation ---------------------------------------------

    async def nightly_tune(
        self, forecaster: object | None = None,
    ) -> None:
        """Compute and optionally apply parameter adjustments."""
        # 1. Activation gate
        if not self._check_activation_gate(forecaster):
            return

        # 2. Compute 7-day average oscillation rate
        if not self._hourly_stats:
            logger.info("self-tuner: no hourly stats yet, skipping")
            return
        total_trans = sum(h["transitions"] for h in self._hourly_stats)
        avg_osc_rate = total_trans / len(self._hourly_stats)

        # 3. Check rollback
        if self._check_rollback(avg_osc_rate):
            self._state.current_params = dict(self._state.previous_params)
            self._state.last_oscillation_rate = avg_osc_rate
            self._apply_params()
            self._save_state()
            return

        ha = self._state.ha_overrides
        base = self._state.base_params
        current = self._state.current_params

        # 4. Dead-band recommendation
        rec_h_db = current.get("huawei_deadband_w", 300)
        rec_v_db = current.get("victron_deadband_w", 150)
        if avg_osc_rate > _OSCILLATION_HIGH:
            rec_h_db = current["huawei_deadband_w"] + _ADJUST_STEP_PCT * base["huawei_deadband_w"]
            rec_v_db = current["victron_deadband_w"] + _ADJUST_STEP_PCT * base["victron_deadband_w"]
        elif avg_osc_rate < _OSCILLATION_LOW:
            rec_h_db = current["huawei_deadband_w"] - _ADJUST_STEP_PCT * base["huawei_deadband_w"]
            rec_v_db = current["victron_deadband_w"] - _ADJUST_STEP_PCT * base["victron_deadband_w"]

        new_h_db = (
            current["huawei_deadband_w"]
            if "huawei_deadband_w" in ha
            else self._bounded_adjust(
                "huawei_deadband_w",
                current["huawei_deadband_w"],
                base["huawei_deadband_w"],
                rec_h_db,
            )
        )
        new_v_db = (
            current["victron_deadband_w"]
            if "victron_deadband_w" in ha
            else self._bounded_adjust(
                "victron_deadband_w",
                current["victron_deadband_w"],
                base["victron_deadband_w"],
                rec_v_db,
            )
        )

        # 5. Ramp rate recommendation
        total_spikes = sum(h["grid_spikes"] for h in self._hourly_stats)
        hours = len(self._hourly_stats)
        days = max(hours / 24, 1)
        avg_spikes_per_day = total_spikes / days

        rec_ramp = current.get("ramp_rate_w", 2000)
        if avg_spikes_per_day > _SPIKES_PER_DAY_HIGH:
            rec_ramp = current["ramp_rate_w"] + _ADJUST_STEP_PCT * base["ramp_rate_w"]
        elif total_spikes == 0 and hours >= 168:
            rec_ramp = current["ramp_rate_w"] - _ADJUST_STEP_PCT * base["ramp_rate_w"]

        new_ramp = (
            current["ramp_rate_w"]
            if "ramp_rate_w" in ha
            else self._bounded_adjust(
                "ramp_rate_w",
                current["ramp_rate_w"],
                base["ramp_rate_w"],
                rec_ramp,
            )
        )

        # 6. Min-SoC profile recommendation
        new_h_profile = current.get("huawei_min_soc_profile")
        new_v_profile = current.get("victron_min_soc_profile")
        if forecaster is not None and "huawei_min_soc_profile" not in ha:
            profile = await self._compute_min_soc_profile(forecaster)
            if profile is not None:
                new_h_profile = profile
                new_v_profile = profile

        # Build recommendation dict
        recommended = {
            "huawei_deadband_w": int(new_h_db),
            "victron_deadband_w": int(new_v_db),
            "ramp_rate_w": int(new_ramp),
            "huawei_min_soc_profile": new_h_profile,
            "victron_min_soc_profile": new_v_profile,
        }

        # 8/9. Shadow vs live
        if self._state.mode == "shadow":
            self._state.shadow_log.append({
                "date": datetime.now(tz=timezone.utc).isoformat(),
                "recommended": recommended,
                "current": dict(current),
                "avg_oscillation_rate": avg_osc_rate,
            })
            self._state.shadow_days += 1
            if self._state.shadow_start_date is None:
                self._state.shadow_start_date = (
                    datetime.now(tz=timezone.utc).date().isoformat()
                )
            if self._state.shadow_days >= _SHADOW_DAYS_REQUIRED:
                self._state.mode = "live"
                logger.info(
                    "self-tuner: promoted to live mode after %d shadow days",
                    self._state.shadow_days,
                )
        else:
            # Live mode -- apply changes
            self._state.previous_params = dict(self._state.current_params)
            self._state.current_params = {
                "huawei_deadband_w": recommended["huawei_deadband_w"],
                "victron_deadband_w": recommended["victron_deadband_w"],
                "ramp_rate_w": recommended["ramp_rate_w"],
                "huawei_min_soc_profile": recommended["huawei_min_soc_profile"],
                "victron_min_soc_profile": recommended["victron_min_soc_profile"],
            }
            self._state.history.append({
                "date": datetime.now(tz=timezone.utc).isoformat(),
                "params": dict(self._state.current_params),
                "avg_oscillation_rate": avg_osc_rate,
            })
            self._apply_params()
            logger.info(
                "self-tuner: live adjustment — deadband h=%d v=%d, "
                "ramp=%d, osc_rate=%.2f",
                recommended["huawei_deadband_w"],
                recommended["victron_deadband_w"],
                recommended["ramp_rate_w"],
                avg_osc_rate,
            )

        # 10. Update oscillation rate, clear overrides, save
        self._state.last_oscillation_rate = avg_osc_rate
        self._state.ha_overrides = {}
        self._save_state()

    # ----- Min-SoC profile computation -------------------------------------

    async def _compute_min_soc_profile(
        self, forecaster: object,
    ) -> list[dict] | None:
        """Generate 6 four-hour-block min-SoC profile from forecast."""
        try:
            tomorrow = datetime.now(tz=timezone.utc).date()
            hourly = await forecaster.predict_hourly(tomorrow)  # type: ignore[union-attr]
        except Exception:
            logger.warning("self-tuner: failed to get hourly forecast")
            return None
        if hourly is None or len(hourly) != 24:
            return None

        # Group into 4-hour blocks
        blocks: list[dict] = []
        avg_total = sum(hourly) / len(hourly)
        for start in range(0, 24, 4):
            end = start + 4
            block_avg = sum(hourly[start:end]) / 4
            # Above-average consumption -> 20% min-SoC, else 10%
            min_soc = 20.0 if block_avg > avg_total else 10.0
            blocks.append({
                "start_hour": start,
                "end_hour": end,
                "min_soc_pct": min_soc,
            })
        return blocks

    # ----- Parameter application -------------------------------------------

    def _apply_params(self) -> None:
        """Push current_params to the coordinator's live runtime fields.

        No-op when coordinator is ``None`` or mode is ``"shadow"``.
        """
        if self._coordinator is None or self._state.mode != "live":
            return

        params = self._state.current_params
        coord = self._coordinator

        coord._huawei_deadband_w = params.get(  # type: ignore[attr-defined]
            "huawei_deadband_w", 300,
        )
        coord._victron_deadband_w = params.get(  # type: ignore[attr-defined]
            "victron_deadband_w", 150,
        )
        coord._huawei_ramp_w_per_cycle = params.get(  # type: ignore[attr-defined]
            "ramp_rate_w", 2000,
        )
        coord._victron_ramp_w_per_cycle = params.get(  # type: ignore[attr-defined]
            "ramp_rate_w", 2000,
        )

        # Propagate min-SoC profiles
        h_profile = params.get("huawei_min_soc_profile")
        v_profile = params.get("victron_min_soc_profile")
        if h_profile is not None:
            coord._sys_config.huawei_min_soc_profile = [  # type: ignore[attr-defined]
                MinSocWindow(**w) if isinstance(w, dict) else w
                for w in h_profile
            ]
        if v_profile is not None:
            coord._sys_config.victron_min_soc_profile = [  # type: ignore[attr-defined]
                MinSocWindow(**w) if isinstance(w, dict) else w
                for w in v_profile
            ]

        logger.info(
            "self-tuner: applied params — deadband h=%s v=%s, "
            "ramp=%s, profiles=%s/%s",
            params.get("huawei_deadband_w"),
            params.get("victron_deadband_w"),
            params.get("ramp_rate_w"),
            "set" if h_profile else "none",
            "set" if v_profile else "none",
        )

    # ----- Activation gate -------------------------------------------------

    def _check_activation_gate(self, forecaster: object | None) -> bool:
        """Return ``True`` if self-tuning preconditions are met."""
        if forecaster is None:
            logger.info("self-tuner: gate check failed — no forecaster")
            return False
        status = forecaster.get_ml_status()  # type: ignore[union-attr]
        mape = status.get("mape", {}).get("current")
        days = status.get("days_of_history", 0)

        if mape is None or mape >= 25.0:
            logger.info(
                "self-tuner: gate check failed — MAPE=%.1f (need <25%%)",
                mape if mape is not None else -1,
            )
            return False
        if days < 60:
            logger.info(
                "self-tuner: gate check failed — "
                "days_of_history=%d (need >=60)",
                days,
            )
            return False
        return True

    # ----- Rollback --------------------------------------------------------

    def _check_rollback(self, current_osc_rate: float) -> bool:
        """Revert parameters if oscillation rate increased >20%."""
        prev_rate = self._state.last_oscillation_rate
        if prev_rate is None or prev_rate == 0:
            return False
        increase_pct = (current_osc_rate - prev_rate) / prev_rate * 100
        if increase_pct > _ROLLBACK_INCREASE_PCT:
            logger.warning(
                "self-tuner: rollback — oscillation rate increased %.1f%% "
                "(%.2f -> %.2f transitions/hour)",
                increase_pct,
                prev_rate,
                current_osc_rate,
            )
            return True
        return False

    # ----- Bounded adjustment ----------------------------------------------

    def _bounded_adjust(
        self,
        param_name: str,
        current: float,
        base: float,
        recommended: float,
    ) -> float:
        """Apply 10% per-night bound and absolute clamp range.

        The 10% cap is relative to the **base** value (not current), per
        pitfall #5 from research.
        """
        max_delta = _MAX_ADJUST_PCT * base
        delta = recommended - current
        if abs(delta) > max_delta:
            delta = max_delta if delta > 0 else -max_delta
        new_val = current + delta
        lo, hi = _CLAMP_RANGES[param_name]
        return max(lo, min(hi, new_val))

    # ----- State persistence -----------------------------------------------

    def _save_state(self) -> None:
        """Persist tuning state to JSON.  Fire-and-forget on error."""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            data = asdict(self._state)
            self._state_path.write_text(
                json.dumps(data, indent=2), encoding="utf-8",
            )
        except Exception:
            logger.warning(
                "self-tuner: failed to save state to %s",
                self._state_path,
                exc_info=True,
            )

    def _load_state(self) -> TuningState:
        """Load persisted state or return defaults."""
        try:
            if self._state_path.exists():
                data = json.loads(
                    self._state_path.read_text(encoding="utf-8"),
                )
                return TuningState(**{
                    k: v
                    for k, v in data.items()
                    if k in TuningState.__dataclass_fields__
                })
        except Exception:
            logger.warning(
                "self-tuner: failed to load state from %s, using defaults",
                self._state_path,
                exc_info=True,
            )
        return TuningState()

    # ----- Status API ------------------------------------------------------

    def get_tuning_status(self) -> dict:
        """Return current tuning status for the ``/api/ml/status`` endpoint."""
        recommended = None
        if self._state.shadow_log:
            recommended = self._state.shadow_log[-1].get("recommended")

        last_adjustment = None
        if self._state.history:
            last_adjustment = self._state.history[-1].get("date")

        return {
            "mode": self._state.mode,
            "shadow_days": self._state.shadow_days,
            "current_params": self._state.current_params,
            "recommended": recommended,
            "last_adjustment": last_adjustment,
            "activation_gate": self._state.mode != "shadow"
            or self._state.shadow_days > 0,
        }
