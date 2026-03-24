"""Anomaly detection engine for the EMS control loop.

Detects three classes of anomaly across sliding windows:

1. **Communication loss** -- recurring driver timeouts (``consecutive_failures``).
2. **Consumption spikes** -- unusual system activity vs hourly baselines.
3. **Battery health drift** -- SoC charge/discharge rate deviations and
   round-trip efficiency degradation.

``check_cycle()`` is called every 5 s from the coordinator.  It uses only
in-memory float comparisons -- no sklearn calls, no I/O on the hot path.

``nightly_train()`` runs once per night via ``anyio.to_thread.run_sync``
to fit an IsolationForest on accumulated feature data and update thresholds.

Observability
-------------
* **WARNING**  ``anomaly-check-failed``    -- check_cycle exception (swallowed).
* **WARNING**  ``anomaly-train-failed``    -- nightly training exception.
* **INFO**     ``anomaly-event``           -- new anomaly event emitted.
* **INFO**     ``anomaly-train-complete``  -- nightly training succeeded.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import AnomalyDetectorConfig
from backend.controller_model import ControllerSnapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AnomalyEvent:
    """Single anomaly observation."""

    timestamp: str
    """ISO 8601 UTC timestamp."""
    anomaly_type: str
    """One of ``comm_loss``, ``consumption_spike``, ``soc_curve``, ``efficiency``."""
    severity: str
    """``info``, ``warning``, or ``alert``."""
    message: str
    value: float
    threshold: float
    system: str | None
    """``huawei``, ``victron``, or ``None`` for system-wide events."""


@dataclass
class HourlyBaseline:
    """Per-hour-of-day consumption baseline with rolling EMA stats."""

    mean: float = 0.0
    std: float = 0.0
    count: int = 0

    def update(self, value: float, alpha: float = 0.1) -> None:
        """Exponential moving average update."""
        if self.count == 0:
            self.mean = value
            self.std = 0.0
        else:
            delta = value - self.mean
            self.mean += alpha * delta
            self.std = math.sqrt(
                max(0.0, (1 - alpha) * (self.std ** 2 + alpha * delta ** 2))
            )
        self.count += 1


@dataclass
class SocBandBaseline:
    """Rolling baseline for charge/discharge rate within a SoC band."""

    mean: float = 0.0
    std: float = 0.0
    count: int = 0
    first_update: float = 0.0
    """``time.monotonic()`` of the first update (for data-age check)."""

    def update(self, value: float, alpha: float = 0.1) -> None:
        """Exponential moving average update for charge/discharge rate."""
        if self.count == 0:
            self.mean = value
            self.std = 0.0
            self.first_update = time.monotonic()
        else:
            delta = value - self.mean
            self.mean += alpha * delta
            self.std = math.sqrt(
                max(0.0, (1 - alpha) * (self.std ** 2 + alpha * delta ** 2))
            )
        self.count += 1


# ---------------------------------------------------------------------------
# Internal trackers
# ---------------------------------------------------------------------------


class _EscalationTracker:
    """Track occurrences per anomaly type in a 24 h sliding window."""

    def __init__(self) -> None:
        self._events: dict[str, list[float]] = {}

    def record(self, anomaly_type: str, now_mono: float) -> str:
        """Record occurrence and return severity (``warning`` or ``alert``)."""
        window = self._events.setdefault(anomaly_type, [])
        cutoff = now_mono - 86400.0
        window[:] = [t for t in window if t > cutoff]
        window.append(now_mono)
        if len(window) >= 3:
            return "alert"
        return "warning"


class _CooldownTracker:
    """Per-type cooldown gate using ``time.monotonic()``."""

    def __init__(
        self, warning_cooldown_s: float, alert_cooldown_s: float
    ) -> None:
        self._warning_cd = warning_cooldown_s
        self._alert_cd = alert_cooldown_s
        self._last_fire: dict[str, float] = {}

    def can_fire(
        self, anomaly_type: str, severity: str, now_mono: float
    ) -> bool:
        """Return ``True`` if enough time has elapsed since last fire."""
        key = f"{anomaly_type}:{severity}"
        last = self._last_fire.get(key)
        if last is None:
            return True
        cd = self._alert_cd if severity == "alert" else self._warning_cd
        return (now_mono - last) >= cd

    def record_fire(
        self, anomaly_type: str, severity: str, now_mono: float
    ) -> None:
        """Update last-fire timestamp."""
        self._last_fire[f"{anomaly_type}:{severity}"] = now_mono


# ---------------------------------------------------------------------------
# SOC band helpers
# ---------------------------------------------------------------------------

_SOC_BANDS: list[tuple[str, float, float]] = [
    ("0-20", 0.0, 20.0),
    ("20-50", 20.0, 50.0),
    ("50-80", 50.0, 80.0),
    ("80-100", 80.0, 100.0),
]


def _soc_band(soc_pct: float) -> str | None:
    """Return the band key for a given SoC, or ``None`` if out of range."""
    for name, lo, hi in _SOC_BANDS:
        if lo <= soc_pct < hi:
            return name
    if soc_pct >= 100.0:
        return "80-100"
    return None


# ---------------------------------------------------------------------------
# AnomalyDetector
# ---------------------------------------------------------------------------


class AnomalyDetector:
    """Core anomaly detection engine.

    Parameters
    ----------
    cfg:
        AnomalyDetectorConfig with thresholds and paths.
    model_store:
        Optional ``ModelStore`` for persisting the IsolationForest model.
    """

    def __init__(
        self,
        cfg: AnomalyDetectorConfig,
        model_store: Any | None = None,
    ) -> None:
        self._cfg = cfg
        self._model_store = model_store
        self._events_path = Path(cfg.events_path)
        self._baselines_path = Path(cfg.baselines_path)

        # Escalation and cooldown
        self._escalation = _EscalationTracker()
        self._cooldown = _CooldownTracker(
            cfg.warning_cooldown_s, cfg.alert_cooldown_s
        )

        # Hourly consumption baselines (24 slots)
        self._hourly_baselines: list[HourlyBaseline] = [
            HourlyBaseline() for _ in range(24)
        ]
        self._total_consumption_updates: int = 0

        # SoC band baselines: band -> direction -> SocBandBaseline
        self._soc_baselines: dict[str, dict[str, SocBandBaseline]] = {
            band: {"charge": SocBandBaseline(), "discharge": SocBandBaseline()}
            for band, _, _ in _SOC_BANDS
        }

        # Per-system state tracking
        self._comm_failure_history: dict[str, list[float]] = {
            "huawei": [],
            "victron": [],
        }
        self._last_soc: dict[str, float | None] = {
            "huawei": None,
            "victron": None,
        }
        self._last_snap_time: dict[str, float | None] = {
            "huawei": None,
            "victron": None,
        }

        # Efficiency accumulators
        self._charge_kwh: dict[str, float] = {"huawei": 0.0, "victron": 0.0}
        self._discharge_kwh: dict[str, float] = {
            "huawei": 0.0,
            "victron": 0.0,
        }
        self._efficiency_window_start: dict[str, float] = {
            "huawei": time.monotonic(),
            "victron": time.monotonic(),
        }

        # Overridable clock (for testing)
        self._now_mono = time.monotonic

        # Load persisted state
        self._events: list[AnomalyEvent] = self._load_events()
        self._load_baselines()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_cycle(
        self,
        h_snap: ControllerSnapshot | None,
        v_snap: ControllerSnapshot | None,
    ) -> list[AnomalyEvent]:
        """Run all anomaly checks for one control cycle.

        Uses only float comparisons -- no ML library calls, no disk I/O
        on the hot path (persistence is fire-and-forget).

        Returns a list of new anomaly events (may be empty).
        """
        new_events: list[AnomalyEvent] = []
        now = self._now_mono()

        new_events.extend(self._check_comm_loss(h_snap, v_snap, now))
        new_events.extend(self._check_consumption(h_snap, v_snap, now))
        new_events.extend(self._check_soc_rate(h_snap, v_snap, now))
        new_events.extend(self._check_efficiency(h_snap, v_snap, now))

        if new_events:
            self._events.extend(new_events)
            for evt in new_events:
                logger.info(
                    "anomaly-event  type=%s severity=%s system=%s msg=%s",
                    evt.anomaly_type,
                    evt.severity,
                    evt.system,
                    evt.message,
                )
            try:
                self._save_events()
            except Exception:  # noqa: BLE001
                logger.warning("anomaly-event-save-failed", exc_info=True)

        return new_events

    async def nightly_train(self) -> None:
        """Train IsolationForest on accumulated baselines.

        Runs sklearn fitting via ``anyio.to_thread.run_sync`` to avoid
        blocking the event loop.
        """
        import anyio  # noqa: PLC0415

        import numpy as np  # noqa: PLC0415
        from sklearn.ensemble import IsolationForest  # noqa: PLC0415

        from backend.model_store import ModelMetadata, ModelStore  # noqa: PLC0415

        # Build feature matrix from hourly baselines
        features: list[list[float]] = []
        for hour_bl in self._hourly_baselines:
            if hour_bl.count > 0:
                features.append([hour_bl.mean, hour_bl.std])

        if len(features) < 10:
            logger.info("anomaly-train-skipped: insufficient baseline data (%d)", len(features))
            return

        X_train = np.array(features, dtype=np.float64)

        model = IsolationForest(
            n_estimators=self._cfg.isolation_forest_n_estimators,
            contamination=self._cfg.isolation_forest_contamination,
            max_samples=min(self._cfg.isolation_forest_max_samples, len(features)),
            random_state=42,
            n_jobs=1,
        )

        await anyio.to_thread.run_sync(model.fit, X_train)

        # Extract threshold from decision function
        scores = model.decision_function(X_train)
        threshold = float(np.percentile(scores, 5))

        logger.info(
            "anomaly-train-complete  samples=%d threshold=%.4f",
            len(features),
            threshold,
        )

        # Persist model via ModelStore
        if self._model_store is not None:
            try:
                import sklearn  # noqa: PLC0415

                meta = ModelMetadata(
                    sklearn_version=sklearn.__version__,
                    numpy_version=np.__version__,
                    trained_at=datetime.now(tz=timezone.utc).isoformat(),
                    sample_count=len(features),
                    feature_names=["consumption_mean", "consumption_std"],
                )
                self._model_store.save("anomaly_forest", model, meta)
            except Exception:  # noqa: BLE001
                logger.warning("anomaly-model-save-failed", exc_info=True)

        # Save baselines to disk
        try:
            self._save_baselines()
        except Exception:  # noqa: BLE001
            logger.warning("anomaly-baselines-save-failed", exc_info=True)

    def get_events(self, limit: int = 100) -> list[dict]:
        """Return recent anomaly events as dicts."""
        return [
            dataclasses.asdict(e)
            for e in self._events[-limit:]
        ]

    def get_battery_health(self) -> dict:
        """Return per-system efficiency and SoC band baselines."""
        health: dict[str, Any] = {}
        for name in ("huawei", "victron"):
            charge = self._charge_kwh.get(name, 0.0)
            discharge = self._discharge_kwh.get(name, 0.0)
            eff = (discharge / charge * 100.0) if charge > 0 else None
            health[name] = {
                "efficiency_pct": round(eff, 1) if eff is not None else None,
                "charge_kwh_24h": round(charge, 3),
                "discharge_kwh_24h": round(discharge, 3),
            }

        # SoC band baselines summary
        bands: dict[str, dict[str, dict[str, float]]] = {}
        for band_key, band_data in self._soc_baselines.items():
            bands[band_key] = {}
            for direction, bl in band_data.items():
                bands[band_key][direction] = {
                    "mean": round(bl.mean, 6),
                    "std": round(bl.std, 6),
                    "count": bl.count,
                }

        health["soc_bands"] = bands
        return health

    # ------------------------------------------------------------------
    # Detection methods (all use float comparisons only)
    # ------------------------------------------------------------------

    def _check_comm_loss(
        self,
        h_snap: ControllerSnapshot | None,
        v_snap: ControllerSnapshot | None,
        now: float,
    ) -> list[AnomalyEvent]:
        """Detect recurring driver timeout patterns."""
        events: list[AnomalyEvent] = []
        pairs: list[tuple[str, ControllerSnapshot | None]] = [
            ("huawei", h_snap),
            ("victron", v_snap),
        ]
        for name, snap in pairs:
            if snap is None or snap.consecutive_failures == 0:
                continue
            history = self._comm_failure_history[name]
            history.append(now)

            # Prune to lookback window
            cutoff = now - self._cfg.comm_loss_window_s
            history[:] = [t for t in history if t > cutoff]

            # Count distinct failure windows (gaps > comm_loss_gap_s)
            windows = self._count_windows(history, self._cfg.comm_loss_gap_s)
            if windows >= self._cfg.comm_loss_min_windows:
                severity = self._escalation.record(f"comm_loss:{name}", now)
                if self._cooldown.can_fire(f"comm_loss:{name}", severity, now):
                    self._cooldown.record_fire(
                        f"comm_loss:{name}", severity, now
                    )
                    events.append(
                        AnomalyEvent(
                            timestamp=datetime.now(tz=timezone.utc).isoformat(),
                            anomaly_type="comm_loss",
                            severity=severity,
                            message=(
                                f"Recurring {name} communication failures: "
                                f"{windows} episodes in last hour"
                            ),
                            value=float(windows),
                            threshold=float(self._cfg.comm_loss_min_windows),
                            system=name,
                        )
                    )
        return events

    def _check_consumption(
        self,
        h_snap: ControllerSnapshot | None,
        v_snap: ControllerSnapshot | None,
        now: float,
    ) -> list[AnomalyEvent]:
        """Detect consumption spikes relative to hourly baselines."""
        events: list[AnomalyEvent] = []

        # Derive system activity proxy
        activity = 0.0
        if h_snap is not None:
            activity += abs(h_snap.power_w)
        if v_snap is not None:
            activity += abs(v_snap.power_w)
            if v_snap.grid_power_w is not None:
                activity += abs(v_snap.grid_power_w)

        hour = datetime.now(tz=timezone.utc).hour
        bl = self._hourly_baselines[hour]
        self._total_consumption_updates += 1

        # Check deviation BEFORE updating baseline (avoid contamination)
        should_check = (
            self._total_consumption_updates >= self._cfg.minimum_consumption_hours
            and bl.count >= 10
            and bl.std >= 1.0
        )
        threshold = bl.mean + self._cfg.consumption_threshold_sigma * bl.std
        is_spike = should_check and activity > threshold

        # Now update baseline with this observation
        bl.update(activity)

        if is_spike:
            severity = self._escalation.record("consumption_spike", now)
            if self._cooldown.can_fire("consumption_spike", severity, now):
                self._cooldown.record_fire("consumption_spike", severity, now)
                events.append(
                    AnomalyEvent(
                        timestamp=datetime.now(tz=timezone.utc).isoformat(),
                        anomaly_type="consumption_spike",
                        severity=severity,
                        message=(
                            f"Consumption spike: {activity:.0f} W "
                            f"(baseline: {bl.mean:.0f} +/- {bl.std:.0f} W)"
                        ),
                        value=activity,
                        threshold=threshold,
                        system=None,
                    )
                )
        return events

    def _check_soc_rate(
        self,
        h_snap: ControllerSnapshot | None,
        v_snap: ControllerSnapshot | None,
        now: float,
    ) -> list[AnomalyEvent]:
        """Detect SoC charge/discharge rate deviations per band."""
        events: list[AnomalyEvent] = []
        pairs: list[tuple[str, ControllerSnapshot | None]] = [
            ("huawei", h_snap),
            ("victron", v_snap),
        ]

        for name, snap in pairs:
            if snap is None or snap.power_w == 0.0:
                self._last_soc[name] = snap.soc_pct if snap else None
                self._last_snap_time[name] = now if snap else None
                continue

            prev_soc = self._last_soc.get(name)
            prev_time = self._last_snap_time.get(name)
            self._last_soc[name] = snap.soc_pct
            self._last_snap_time[name] = now

            if prev_soc is None or prev_time is None:
                continue

            dt = now - prev_time
            if dt < 1.0:
                continue

            delta_soc = snap.soc_pct - prev_soc
            rate = abs(delta_soc) / dt  # %/s

            if rate < 1e-8:
                continue

            band = _soc_band(snap.soc_pct)
            if band is None:
                continue

            direction = "charge" if snap.power_w > 0 else "discharge"
            bl = self._soc_baselines[band][direction]

            # Check deviation BEFORE updating baseline (avoid contamination)
            has_baseline = bl.count >= 10
            data_age_days = (
                (now - bl.first_update) / 86400.0 if bl.count > 0 else 0.0
            )
            deviation = 0.0
            if has_baseline and bl.std > 1e-10:
                deviation = abs(rate - bl.mean) / bl.std

            # Now update the baseline with this observation
            bl.update(rate)

            if not has_baseline:
                continue
            if data_age_days < self._cfg.minimum_battery_days:
                continue
            if bl.std < 1e-10:
                continue
            if deviation > self._cfg.soc_rate_threshold_sigma:
                severity = self._escalation.record(f"soc_curve:{name}", now)
                if self._cooldown.can_fire(f"soc_curve:{name}", severity, now):
                    self._cooldown.record_fire(
                        f"soc_curve:{name}", severity, now
                    )
                    events.append(
                        AnomalyEvent(
                            timestamp=datetime.now(tz=timezone.utc).isoformat(),
                            anomaly_type="soc_curve",
                            severity=severity,
                            message=(
                                f"{name} {direction} rate anomaly in "
                                f"SoC band {band}: {rate:.6f} %/s "
                                f"(baseline: {bl.mean:.6f} +/- {bl.std:.6f})"
                            ),
                            value=rate,
                            threshold=bl.mean
                            + self._cfg.soc_rate_threshold_sigma * bl.std,
                            system=name,
                        )
                    )
        return events

    def _check_efficiency(
        self,
        h_snap: ControllerSnapshot | None,
        v_snap: ControllerSnapshot | None,
        now: float,
    ) -> list[AnomalyEvent]:
        """Track round-trip efficiency and flag degradation."""
        events: list[AnomalyEvent] = []
        pairs: list[tuple[str, ControllerSnapshot | None]] = [
            ("huawei", h_snap),
            ("victron", v_snap),
        ]

        for name, snap in pairs:
            if snap is None:
                continue

            prev_time = self._last_snap_time.get(name)
            dt_s = (now - prev_time) if prev_time is not None else 5.0
            if dt_s <= 0:
                dt_s = 5.0

            # Accumulate energy
            energy_kwh = abs(snap.power_w) * dt_s / 3_600_000.0
            if snap.power_w > 0:
                self._charge_kwh[name] += energy_kwh
            elif snap.power_w < 0:
                self._discharge_kwh[name] += energy_kwh

            # Check if 24h window has elapsed
            window_start = self._efficiency_window_start.get(name, now)
            if (now - window_start) >= 86400.0:
                charge = self._charge_kwh[name]
                discharge = self._discharge_kwh[name]
                if charge > 0.1:
                    eff_pct = (discharge / charge) * 100.0
                    if eff_pct < self._cfg.efficiency_threshold_pct:
                        severity = self._escalation.record(
                            f"efficiency:{name}", now
                        )
                        if self._cooldown.can_fire(
                            f"efficiency:{name}", severity, now
                        ):
                            self._cooldown.record_fire(
                                f"efficiency:{name}", severity, now
                            )
                            events.append(
                                AnomalyEvent(
                                    timestamp=datetime.now(
                                        tz=timezone.utc
                                    ).isoformat(),
                                    anomaly_type="efficiency",
                                    severity=severity,
                                    message=(
                                        f"{name} round-trip efficiency "
                                        f"{eff_pct:.1f}% over 24h "
                                        f"(threshold: {self._cfg.efficiency_threshold_pct}%)"
                                    ),
                                    value=eff_pct,
                                    threshold=self._cfg.efficiency_threshold_pct,
                                    system=name,
                                )
                            )

                # Reset accumulators
                self._charge_kwh[name] = 0.0
                self._discharge_kwh[name] = 0.0
                self._efficiency_window_start[name] = now

        return events

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _count_windows(timestamps: list[float], gap_s: float) -> int:
        """Count distinct failure windows (gaps > *gap_s* = new window)."""
        if not timestamps:
            return 0
        sorted_ts = sorted(timestamps)
        windows = 1
        for i in range(1, len(sorted_ts)):
            if sorted_ts[i] - sorted_ts[i - 1] > gap_s:
                windows += 1
        return windows

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_events(self) -> None:
        """Persist events to JSON (trimmed to max_events)."""
        trimmed = self._events[-self._cfg.max_events :]
        self._events = trimmed
        data = [dataclasses.asdict(e) for e in trimmed]
        self._events_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._events_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(self._events_path)

    def _load_events(self) -> list[AnomalyEvent]:
        """Load events from JSON, discarding corrupt data."""
        if not self._events_path.exists():
            return []
        try:
            raw = json.loads(self._events_path.read_text())
            if not isinstance(raw, list):
                return []
            events = [AnomalyEvent(**e) for e in raw]
            # Prune by max_events
            return events[-self._cfg.max_events :]
        except (json.JSONDecodeError, TypeError, KeyError, OSError):
            return []

    def _save_baselines(self) -> None:
        """Persist hourly and SoC band baselines to JSON."""
        data: dict[str, Any] = {
            "hourly": [
                {"mean": bl.mean, "std": bl.std, "count": bl.count}
                for bl in self._hourly_baselines
            ],
            "soc_bands": {
                band: {
                    direction: {
                        "mean": bl.mean,
                        "std": bl.std,
                        "count": bl.count,
                        "first_update": bl.first_update,
                    }
                    for direction, bl in directions.items()
                }
                for band, directions in self._soc_baselines.items()
            },
            "total_consumption_updates": self._total_consumption_updates,
        }
        self._baselines_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._baselines_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(self._baselines_path)

    def _load_baselines(self) -> None:
        """Restore baselines from JSON."""
        if not self._baselines_path.exists():
            return
        try:
            data = json.loads(self._baselines_path.read_text())
        except (json.JSONDecodeError, OSError):
            return

        # Hourly baselines
        hourly = data.get("hourly", [])
        for i, entry in enumerate(hourly):
            if i < 24 and isinstance(entry, dict):
                self._hourly_baselines[i] = HourlyBaseline(
                    mean=entry.get("mean", 0.0),
                    std=entry.get("std", 0.0),
                    count=entry.get("count", 0),
                )

        # SoC band baselines
        soc_bands = data.get("soc_bands", {})
        for band, directions in soc_bands.items():
            if band in self._soc_baselines and isinstance(directions, dict):
                for direction, vals in directions.items():
                    if (
                        direction in self._soc_baselines[band]
                        and isinstance(vals, dict)
                    ):
                        self._soc_baselines[band][direction] = SocBandBaseline(
                            mean=vals.get("mean", 0.0),
                            std=vals.get("std", 0.0),
                            count=vals.get("count", 0),
                            first_update=vals.get("first_update", 0.0),
                        )

        self._total_consumption_updates = data.get(
            "total_consumption_updates", 0
        )
