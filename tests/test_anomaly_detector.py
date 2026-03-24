"""Unit tests for the AnomalyDetector module.

Tests cover all three detection domains (communication loss, consumption
spikes, battery health drift), tiered alert escalation, cooldown tracking,
JSON persistence, nightly IsolationForest training, and per-cycle
float-only checks.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.anomaly_detector import (
    AnomalyDetector,
    AnomalyEvent,
    HourlyBaseline,
    SocBandBaseline,
    _CooldownTracker,
    _EscalationTracker,
)
from backend.config import AnomalyDetectorConfig
from backend.controller_model import BatteryRole, ControllerSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snap(
    *,
    soc_pct: float = 50.0,
    power_w: float = 0.0,
    available: bool = True,
    consecutive_failures: int = 0,
    grid_power_w: float | None = None,
    ts: float | None = None,
) -> ControllerSnapshot:
    """Build a ControllerSnapshot with sensible defaults."""
    return ControllerSnapshot(
        soc_pct=soc_pct,
        power_w=power_w,
        available=available,
        role=BatteryRole.HOLDING,
        consecutive_failures=consecutive_failures,
        timestamp=ts or time.monotonic(),
        grid_power_w=grid_power_w,
    )


def _make_detector(tmp_path: Path, **overrides) -> AnomalyDetector:
    """Build an AnomalyDetector writing to *tmp_path*."""
    cfg = AnomalyDetectorConfig(
        events_path=str(tmp_path / "events.json"),
        baselines_path=str(tmp_path / "baselines.json"),
        model_dir=str(tmp_path),
        **overrides,
    )
    return AnomalyDetector(cfg, model_store=None)


# ---------------------------------------------------------------------------
# Communication loss detection
# ---------------------------------------------------------------------------


class TestCommLoss:
    """ANOM-01: Communication loss pattern detection."""

    def test_comm_loss_pattern(self, tmp_path: Path) -> None:
        """3+ failure windows in 1 hour triggers comm_loss event."""
        det = _make_detector(
            tmp_path,
            comm_loss_window_s=3600.0,
            comm_loss_min_windows=3,
            comm_loss_gap_s=30.0,
        )
        now = time.monotonic()
        events: list[AnomalyEvent] = []

        # Simulate 3 distinct failure windows separated by > 30s gaps
        for i in range(3):
            h = _snap(consecutive_failures=2, ts=now + i * 60)
            v = _snap(ts=now + i * 60)
            # Inject monotonic time so windows are separated
            det._now_mono = lambda _t=now + i * 60: _t  # type: ignore[attr-defined]
            evts = det.check_cycle(h, v)
            events.extend(evts)

        comm_events = [e for e in events if e.anomaly_type == "comm_loss"]
        assert len(comm_events) >= 1
        assert comm_events[0].system == "huawei"

    def test_comm_loss_no_false_positive(self, tmp_path: Path) -> None:
        """Single failure window does not trigger comm_loss."""
        det = _make_detector(tmp_path)
        h = _snap(consecutive_failures=1)
        v = _snap()
        events = det.check_cycle(h, v)
        comm_events = [e for e in events if e.anomaly_type == "comm_loss"]
        assert len(comm_events) == 0


# ---------------------------------------------------------------------------
# Consumption spike detection
# ---------------------------------------------------------------------------


class TestConsumption:
    """ANOM-02: Consumption spike detection."""

    def test_consumption_spike(self, tmp_path: Path) -> None:
        """After establishing baseline, large value triggers spike event."""
        det = _make_detector(
            tmp_path,
            minimum_consumption_hours=10,
            consumption_threshold_sigma=3.0,
        )
        now = time.monotonic()

        # Seed the current-hour baseline with slightly varying values
        hour = datetime.now(tz=timezone.utc).hour
        bl = det._hourly_baselines[hour]
        for i in range(50):
            bl.update(1000.0 + (i % 5) * 20.0)  # ~1000-1080W baseline
        det._total_consumption_updates = 200  # well past minimum

        # Now inject a massive spike (10x baseline)
        t = now + 1000
        h = _snap(power_w=-5000.0, ts=t)
        v = _snap(power_w=-3000.0, grid_power_w=2000.0, ts=t)
        det._now_mono = lambda: t  # type: ignore[attr-defined]
        events = det.check_cycle(h, v)
        spike_events = [e for e in events if e.anomaly_type == "consumption_spike"]
        assert len(spike_events) >= 1

    def test_consumption_no_alert_cold_start(self, tmp_path: Path) -> None:
        """With fewer than minimum_consumption_hours updates, no consumption events."""
        det = _make_detector(tmp_path, minimum_consumption_hours=168)
        h = _snap(power_w=-5000.0)
        v = _snap(power_w=-3000.0, grid_power_w=2000.0)
        events = det.check_cycle(h, v)
        consumption_events = [e for e in events if e.anomaly_type == "consumption_spike"]
        assert len(consumption_events) == 0


# ---------------------------------------------------------------------------
# Alert escalation
# ---------------------------------------------------------------------------


class TestEscalation:
    """ANOM-03: Alert severity escalation."""

    def test_alert_escalation(self) -> None:
        """First occurrence returns warning, third within 24h returns alert."""
        tracker = _EscalationTracker()
        now = time.monotonic()

        sev1 = tracker.record("comm_loss", now)
        assert sev1 == "warning"

        sev2 = tracker.record("comm_loss", now + 10)
        assert sev2 == "warning"

        sev3 = tracker.record("comm_loss", now + 20)
        assert sev3 == "alert"

    def test_alert_cooldown_reset(self) -> None:
        """After 24h window expires, counter resets."""
        tracker = _EscalationTracker()
        now = time.monotonic()

        tracker.record("comm_loss", now)
        tracker.record("comm_loss", now + 10)
        tracker.record("comm_loss", now + 20)

        # 25 hours later, counter should be reset
        sev = tracker.record("comm_loss", now + 90000)
        assert sev == "warning"  # back to warning (count = 1 in new window)


# ---------------------------------------------------------------------------
# SoC curve anomaly detection
# ---------------------------------------------------------------------------


class TestSocCurve:
    """ANOM-04: SoC curve anomaly detection."""

    def test_soc_curve_anomaly(self, tmp_path: Path) -> None:
        """After 14 days of baseline, deviating charge rate triggers event."""
        det = _make_detector(tmp_path, minimum_battery_days=0)
        now = time.monotonic()

        # Directly seed the 50-80 charge baseline with known values
        bl = det._soc_baselines["50-80"]["charge"]
        bl.mean = 0.001  # very low normal rate
        bl.std = 0.0001
        bl.count = 50
        bl.first_update = now - 86400 * 15  # 15 days ago

        # Set up previous state so rate can be computed
        det._last_soc["huawei"] = 55.0
        det._last_snap_time["huawei"] = now - 300  # 5 min ago

        # Inject anomalous charge: SoC jumps from 55 to 60 in 300s
        # rate = 5/300 = 0.0167 %/s, baseline = 0.001, deviation huge
        det._now_mono = lambda: now  # type: ignore[attr-defined]
        h_anom = _snap(soc_pct=60.0, power_w=3000.0, ts=now)
        events = det.check_cycle(h_anom, _snap(ts=now))
        soc_events = [e for e in events if e.anomaly_type == "soc_curve"]
        assert len(soc_events) >= 1

    def test_soc_no_alert_before_14_days(self, tmp_path: Path) -> None:
        """Before minimum_battery_days, no SoC anomalies."""
        det = _make_detector(tmp_path, minimum_battery_days=14)
        h = _snap(soc_pct=50.0, power_w=5000.0)
        v = _snap()
        events = det.check_cycle(h, v)
        soc_events = [e for e in events if e.anomaly_type == "soc_curve"]
        assert len(soc_events) == 0


# ---------------------------------------------------------------------------
# Efficiency tracking
# ---------------------------------------------------------------------------


class TestEfficiency:
    """ANOM-05: Round-trip efficiency tracking."""

    def test_efficiency_tracking(self, tmp_path: Path) -> None:
        """Efficiency below 85% over 24h window triggers event."""
        det = _make_detector(
            tmp_path,
            efficiency_threshold_pct=85.0,
            minimum_battery_days=0,
        )
        now = time.monotonic()

        # Charge phase: accumulate 10 kWh of charging
        for i in range(100):
            t = now + i * 5
            h = _snap(power_w=2000.0, ts=t)  # 2kW charging
            v = _snap(ts=t)
            det._now_mono = lambda _t=t: _t  # type: ignore[attr-defined]
            det.check_cycle(h, v)

        # Discharge phase: accumulate only 5 kWh (50% efficiency, below 85%)
        base_t = now + 500
        for i in range(100):
            t = base_t + i * 5
            h = _snap(power_w=-1000.0, ts=t)  # 1kW discharging
            v = _snap(ts=t)
            det._now_mono = lambda _t=t: _t  # type: ignore[attr-defined]
            det.check_cycle(h, v)

        # Force 24h window expiry to trigger efficiency check
        t_end = now + 86401
        det._now_mono = lambda: t_end  # type: ignore[attr-defined]
        det._efficiency_window_start["huawei"] = now  # window started at now
        events = det.check_cycle(_snap(ts=t_end), _snap(ts=t_end))
        eff_events = [e for e in events if e.anomaly_type == "efficiency"]
        assert len(eff_events) >= 1

    def test_efficiency_24h_window_reset(self, tmp_path: Path) -> None:
        """After 24h, accumulators reset."""
        det = _make_detector(tmp_path, minimum_battery_days=0)
        now = time.monotonic()

        det._charge_kwh["huawei"] = 10.0
        det._discharge_kwh["huawei"] = 9.0
        det._efficiency_window_start["huawei"] = now - 86401

        det._now_mono = lambda: now  # type: ignore[attr-defined]
        det.check_cycle(_snap(ts=now), _snap(ts=now))

        # After check, accumulators should be reset
        assert det._charge_kwh["huawei"] < 10.0  # Reset happened


# ---------------------------------------------------------------------------
# Nightly training
# ---------------------------------------------------------------------------


class TestNightlyTrain:
    """ANOM-06: Nightly IsolationForest training."""

    @pytest.mark.anyio
    async def test_nightly_train(self, tmp_path: Path) -> None:
        """nightly_train() fits IsolationForest and saves via ModelStore."""
        mock_store = MagicMock()
        mock_store.save = MagicMock()

        cfg = AnomalyDetectorConfig(
            events_path=str(tmp_path / "events.json"),
            baselines_path=str(tmp_path / "baselines.json"),
            model_dir=str(tmp_path),
        )
        det = AnomalyDetector(cfg, model_store=mock_store)

        # Seed hourly baselines directly (bypasses check_cycle hour limitation)
        for i in range(24):
            det._hourly_baselines[i] = HourlyBaseline(
                mean=500.0 + i * 10, std=50.0, count=20
            )

        await det.nightly_train()

        # Verify ModelStore.save was called
        assert mock_store.save.called


# ---------------------------------------------------------------------------
# No sklearn in check_cycle
# ---------------------------------------------------------------------------


class TestNoSklearn:
    """ANOM-07: check_cycle uses only float comparisons."""

    def test_check_cycle_no_sklearn(self) -> None:
        """Verify check_cycle does not reference sklearn."""
        source = inspect.getsource(AnomalyDetector.check_cycle)
        assert "sklearn" not in source
        assert "IsolationForest" not in source

        # Also check the private detection methods called by check_cycle
        for method_name in [
            "_check_comm_loss",
            "_check_consumption",
            "_check_soc_rate",
            "_check_efficiency",
        ]:
            method = getattr(AnomalyDetector, method_name)
            src = inspect.getsource(method)
            assert "sklearn" not in src, f"{method_name} references sklearn"


# ---------------------------------------------------------------------------
# Event persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    """Event save/load and retention."""

    def test_event_persistence(self, tmp_path: Path) -> None:
        """Events saved to JSON and restored on construction."""
        det = _make_detector(tmp_path)
        # Manually add an event
        event = AnomalyEvent(
            timestamp="2026-01-01T00:00:00+00:00",
            anomaly_type="comm_loss",
            severity="warning",
            message="test",
            value=3.0,
            threshold=3.0,
            system="huawei",
        )
        det._events.append(event)
        det._save_events()

        # Construct a new detector reading from the same path
        det2 = _make_detector(tmp_path)
        assert len(det2._events) == 1
        assert det2._events[0].anomaly_type == "comm_loss"

    def test_event_retention(self, tmp_path: Path) -> None:
        """More than max_events events are trimmed."""
        det = _make_detector(tmp_path, max_events=500)
        for i in range(600):
            det._events.append(
                AnomalyEvent(
                    timestamp=f"2026-01-01T{i:06d}",
                    anomaly_type="test",
                    severity="info",
                    message=f"event {i}",
                    value=0.0,
                    threshold=0.0,
                    system=None,
                )
            )
        det._save_events()

        det2 = _make_detector(tmp_path, max_events=500)
        assert len(det2._events) <= 500


# ---------------------------------------------------------------------------
# Battery health API
# ---------------------------------------------------------------------------


class TestBatteryHealth:
    """get_battery_health() returns structured dict."""

    def test_get_battery_health(self, tmp_path: Path) -> None:
        """Returns dict with per-system efficiency and SoC band baselines."""
        det = _make_detector(tmp_path)
        det._charge_kwh["huawei"] = 10.0
        det._discharge_kwh["huawei"] = 9.0

        health = det.get_battery_health()
        assert isinstance(health, dict)
        assert "huawei" in health or "efficiency" in health or "soc_bands" in health


# ---------------------------------------------------------------------------
# HourlyBaseline and SocBandBaseline unit tests
# ---------------------------------------------------------------------------


class TestBaselines:
    """Unit tests for baseline dataclasses."""

    def test_hourly_baseline_update(self) -> None:
        """EMA update produces sensible mean and std."""
        bl = HourlyBaseline()
        for v in [10.0, 12.0, 11.0, 10.5, 11.5]:
            bl.update(v)
        assert bl.count == 5
        assert bl.mean > 0
        assert bl.std >= 0

    def test_soc_band_baseline(self) -> None:
        """SocBandBaseline tracks rate updates."""
        bl = SocBandBaseline()
        for v in [0.01, 0.012, 0.011]:
            bl.update(v)
        assert bl.count == 3
        assert bl.mean > 0
