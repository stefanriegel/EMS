"""Unit tests for HealthLogger, HealthSnapshot, _ml_metrics, _sched_metrics,
and InfluxMetricsWriter.write_health().

Coverage:
  - HealthLogger.should_log(): interval gating
  - HealthLogger.capture(): all fields populated correctly with None objects
  - HealthLogger.capture(): ML fields extracted from a mock forecaster
  - HealthLogger.capture(): scheduler fields extracted from a mock scheduler
  - HealthLogger.capture(): anomaly flags fire at correct thresholds
  - HealthLogger.get_recent(): ring-buffer truncation
  - _ml_metrics(): handles None, untrained, trained with/without mape
  - _sched_metrics(): handles None, no active_schedule, full schedule
  - InfluxMetricsWriter.write_health(): line protocol contains expected fields/tags
  - InfluxMetricsWriter.write_health(): optional float fields omitted when None
  - InfluxMetricsWriter.write_health(): exception is swallowed (fire-and-forget)

K002: use @pytest.mark.anyio (not asyncio_mode=auto).
K007: anyio_mode = "auto" makes each async test run twice (asyncio + trio).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.health_logger import (
    HealthLogger,
    _ml_metrics,
    _sched_metrics,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _capture_defaults(hl: HealthLogger, **overrides):
    """Call hl.capture() with safe defaults; caller can override any kwarg."""
    kwargs = dict(
        h_soc=80.0,
        v_soc=55.0,
        h_power=-500.0,
        v_power=-1000.0,
        h_max_discharge_w=5000.0,
        v_max_discharge_w=3000.0,
        pv_power=2000.0,
        grid_power=100.0,
        true_consumption=1800.0,
        v_l1_w=50.0,
        v_l2_w=25.0,
        v_l3_w=25.0,
        control_state="IDLE",
        pool_status="NORMAL",
        h_role="HOLDING",
        v_role="HOLDING",
        h_setpoint_w=0.0,
        v_setpoint_w=0.0,
        cross_charge_active=False,
        cross_charge_waste=0.0,
        cross_charge_episodes=0,
        shadow_mode=False,
        commissioning_stage="production",
        huawei_available=True,
        victron_available=True,
        emma_available=False,
        influx_available=True,
        ha_mqtt_available=True,
        evcc_available=False,
        telegram_available=True,
        forecaster=None,
        scheduler=None,
    )
    kwargs.update(overrides)
    return hl.capture(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# HealthLogger.should_log
# ─────────────────────────────────────────────────────────────────────────────

class TestShouldLog:
    def test_first_call_is_true(self):
        hl = HealthLogger()
        assert hl.should_log() is True

    def test_false_immediately_after_capture(self):
        hl = HealthLogger()
        _capture_defaults(hl)
        assert hl.should_log() is False

    def test_true_after_interval_elapsed(self, monkeypatch):
        hl = HealthLogger()
        _capture_defaults(hl)
        # Simulate 301 seconds of monotonic time passing
        monkeypatch.setattr(
            "backend.health_logger.time.monotonic",
            lambda: hl._last_log_time + 301,
        )
        assert hl.should_log() is True


# ─────────────────────────────────────────────────────────────────────────────
# HealthLogger.capture — basic field correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestCapture:
    def test_combined_soc_is_average(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, h_soc=80.0, v_soc=60.0)
        assert snap.combined_soc_pct == pytest.approx(70.0)

    def test_soc_imbalance_is_absolute_difference(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, h_soc=80.0, v_soc=55.0)
        assert snap.soc_imbalance_pct == pytest.approx(25.0)

    def test_energy_flow_fields_passed_through(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, pv_power=3000.0, grid_power=-200.0, true_consumption=2500.0)
        assert snap.pv_power_w == pytest.approx(3000.0)
        assert snap.grid_power_w == pytest.approx(-200.0)
        assert snap.true_consumption_w == pytest.approx(2500.0)

    def test_phase_powers_passed_through(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, v_l1_w=100.0, v_l2_w=200.0, v_l3_w=300.0)
        assert snap.victron_grid_l1_w == pytest.approx(100.0)
        assert snap.victron_grid_l2_w == pytest.approx(200.0)
        assert snap.victron_grid_l3_w == pytest.approx(300.0)

    def test_control_state_and_roles(self):
        hl = HealthLogger()
        snap = _capture_defaults(
            hl,
            control_state="DISCHARGE",
            pool_status="NORMAL",
            h_role="DISCHARGING",
            v_role="HOLDING",
            h_setpoint_w=2000.0,
            v_setpoint_w=1500.0,
        )
        assert snap.control_state == "DISCHARGE"
        assert snap.pool_status == "NORMAL"
        assert snap.huawei_role == "DISCHARGING"
        assert snap.victron_role == "HOLDING"
        assert snap.huawei_setpoint_w == pytest.approx(2000.0)
        assert snap.victron_setpoint_w == pytest.approx(1500.0)

    def test_availability_flags(self):
        hl = HealthLogger()
        snap = _capture_defaults(
            hl,
            huawei_available=False,
            victron_available=True,
            emma_available=True,
            influx_available=True,
            ha_mqtt_available=False,
            evcc_available=True,
            telegram_available=False,
        )
        assert snap.huawei_available is False
        assert snap.victron_available is True
        assert snap.emma_available is True
        assert snap.ha_mqtt_available is False
        assert snap.evcc_available is True
        assert snap.telegram_available is False

    def test_timestamp_is_utc(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl)
        assert snap.timestamp.tzinfo is not None
        assert snap.timestamp.tzinfo == timezone.utc

    def test_ml_none_when_no_forecaster(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, forecaster=None)
        assert snap.ml_trained is False
        assert snap.ml_days_of_history == 0
        assert snap.ml_total_samples == 0
        assert snap.ml_last_prediction_kwh is None
        assert snap.ml_last_trained_age_h is None
        assert snap.ml_last_mape_pct is None

    def test_sched_none_when_no_scheduler(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, scheduler=None)
        assert snap.sched_has_schedule is False
        assert snap.sched_slot_count == 0
        assert snap.sched_solar_forecast_kwh == pytest.approx(0.0)
        assert snap.sched_consumption_forecast_kwh == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Anomaly flags
# ─────────────────────────────────────────────────────────────────────────────

class TestAnomalyFlags:
    def test_flag_soc_imbalance_above_threshold(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, h_soc=90.0, v_soc=55.0)  # 35% gap
        assert snap.flag_soc_imbalance is True

    def test_flag_soc_imbalance_below_threshold(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, h_soc=80.0, v_soc=60.0)  # 20% gap
        assert snap.flag_soc_imbalance is False

    def test_flag_cross_charge_above_threshold(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, cross_charge_waste=150.0)
        assert snap.flag_cross_charge is True

    def test_flag_cross_charge_below_threshold(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, cross_charge_waste=50.0)
        assert snap.flag_cross_charge is False

    def test_flag_system_degraded_when_huawei_offline(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, huawei_available=False, victron_available=True)
        assert snap.flag_system_degraded is True

    def test_flag_system_not_degraded_when_both_online(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, huawei_available=True, victron_available=True)
        assert snap.flag_system_degraded is False

    def test_flag_ml_stale_when_no_forecaster(self):
        hl = HealthLogger()
        snap = _capture_defaults(hl, forecaster=None)
        assert snap.flag_ml_stale is True

    def test_flag_ml_stale_when_trained_recently(self):
        hl = HealthLogger()
        fc = SimpleNamespace(
            _heat_pump_model=object(),  # non-None → trained
            _dhw_model=None,
            _base_model=None,
            _last_trained_at=datetime.now(tz=timezone.utc),
            _days_of_history=14,
            _total_samples=336,
            _last_prediction_kwh=18.5,
            _mape_path=None,
        )
        snap = _capture_defaults(hl, forecaster=fc)
        assert snap.ml_trained is True
        assert snap.flag_ml_stale is False

    def test_flag_ml_stale_when_trained_over_25h_ago(self):
        hl = HealthLogger()
        from datetime import timedelta
        fc = SimpleNamespace(
            _heat_pump_model=object(),
            _dhw_model=None,
            _base_model=None,
            _last_trained_at=datetime.now(tz=timezone.utc) - timedelta(hours=26),
            _days_of_history=14,
            _total_samples=336,
            _last_prediction_kwh=18.5,
            _mape_path=None,
        )
        snap = _capture_defaults(hl, forecaster=fc)
        assert snap.flag_ml_stale is True

    def test_flag_sched_stale_propagates(self):
        hl = HealthLogger()
        reasoning = SimpleNamespace(tomorrow_solar_kwh=10.0, expected_consumption_kwh=20.0)
        schedule = SimpleNamespace(stale=True, slots=[], reasoning=reasoning, target_soc_pct=90.0)
        sched = SimpleNamespace(active_schedule=schedule)
        snap = _capture_defaults(hl, scheduler=sched)
        assert snap.sched_stale is True
        assert snap.flag_sched_stale is True


# ─────────────────────────────────────────────────────────────────────────────
# Ring buffer
# ─────────────────────────────────────────────────────────────────────────────

class TestRingBuffer:
    def test_get_recent_returns_last_n(self):
        hl = HealthLogger()
        # Force rapid successive captures by resetting _last_log_time each time
        for _ in range(5):
            hl._last_log_time = 0.0
            _capture_defaults(hl)
        recent = hl.get_recent(3)
        assert len(recent) == 3

    def test_buffer_truncates_at_max(self, monkeypatch):
        hl = HealthLogger()
        hl._max_snapshots = 5
        for i in range(7):
            hl._last_log_time = 0.0
            _capture_defaults(hl, h_soc=float(i))
        assert len(hl._snapshots) == 5
        # Most recent value should be the last captured
        assert hl._snapshots[-1].huawei_soc_pct == pytest.approx(6.0)


# ─────────────────────────────────────────────────────────────────────────────
# _ml_metrics helper
# ─────────────────────────────────────────────────────────────────────────────

class TestMlMetrics:
    def test_none_forecaster_returns_defaults(self):
        r = _ml_metrics(None)
        assert r["trained"] is False
        assert r["days_of_history"] == 0
        assert r["total_samples"] == 0
        assert r["last_prediction_kwh"] is None
        assert r["last_trained_age_h"] is None
        assert r["last_mape_pct"] is None

    def test_untrained_models_returns_trained_false(self):
        fc = SimpleNamespace(
            _heat_pump_model=None, _dhw_model=None, _base_model=None,
            _last_trained_at=None, _days_of_history=0, _total_samples=0,
            _last_prediction_kwh=None, _mape_path=None,
        )
        r = _ml_metrics(fc)
        assert r["trained"] is False

    def test_one_model_trained_returns_true(self):
        fc = SimpleNamespace(
            _heat_pump_model=object(),  # non-None
            _dhw_model=None, _base_model=None,
            _last_trained_at=datetime.now(tz=timezone.utc),
            _days_of_history=10, _total_samples=240,
            _last_prediction_kwh=15.0, _mape_path=None,
        )
        r = _ml_metrics(fc)
        assert r["trained"] is True
        assert r["days_of_history"] == 10
        assert r["total_samples"] == 240
        assert r["last_prediction_kwh"] == pytest.approx(15.0)

    def test_last_trained_age_computed_correctly(self):
        from datetime import timedelta
        fc = SimpleNamespace(
            _heat_pump_model=object(),
            _dhw_model=None, _base_model=None,
            _last_trained_at=datetime.now(tz=timezone.utc) - timedelta(hours=3),
            _days_of_history=5, _total_samples=120,
            _last_prediction_kwh=None, _mape_path=None,
        )
        r = _ml_metrics(fc)
        assert r["last_trained_age_h"] == pytest.approx(3.0, abs=0.1)

    def test_mape_read_from_path(self, tmp_path):
        mape_file = tmp_path / "mape_history.json"
        mape_file.write_text(json.dumps([
            {"date": "2026-01-01", "mape": 12.5},
            {"date": "2026-01-02", "mape": 9.8},
        ]))
        fc = SimpleNamespace(
            _heat_pump_model=object(),
            _dhw_model=None, _base_model=None,
            _last_trained_at=datetime.now(tz=timezone.utc),
            _days_of_history=7, _total_samples=168,
            _last_prediction_kwh=20.0,
            _mape_path=mape_file,
        )
        r = _ml_metrics(fc)
        assert r["last_mape_pct"] == pytest.approx(9.8)

    def test_mape_none_when_file_missing(self, tmp_path):
        fc = SimpleNamespace(
            _heat_pump_model=object(),
            _dhw_model=None, _base_model=None,
            _last_trained_at=datetime.now(tz=timezone.utc),
            _days_of_history=7, _total_samples=168,
            _last_prediction_kwh=20.0,
            _mape_path=tmp_path / "nonexistent.json",
        )
        r = _ml_metrics(fc)
        assert r["last_mape_pct"] is None

    def test_mape_none_when_empty_history(self, tmp_path):
        mape_file = tmp_path / "mape_history.json"
        mape_file.write_text("[]")
        fc = SimpleNamespace(
            _heat_pump_model=object(),
            _dhw_model=None, _base_model=None,
            _last_trained_at=datetime.now(tz=timezone.utc),
            _days_of_history=7, _total_samples=168,
            _last_prediction_kwh=None,
            _mape_path=mape_file,
        )
        r = _ml_metrics(fc)
        assert r["last_mape_pct"] is None


# ─────────────────────────────────────────────────────────────────────────────
# _sched_metrics helper
# ─────────────────────────────────────────────────────────────────────────────

class TestSchedMetrics:
    def test_none_scheduler_returns_defaults(self):
        r = _sched_metrics(None)
        assert r["has_schedule"] is False
        assert r["slot_count"] == 0
        assert r["stale"] is False

    def test_scheduler_with_no_active_schedule(self):
        sched = SimpleNamespace(active_schedule=None)
        r = _sched_metrics(sched)
        assert r["has_schedule"] is False

    def test_weather_scheduler_wraps_inner_scheduler(self):
        reasoning = SimpleNamespace(tomorrow_solar_kwh=8.0, expected_consumption_kwh=22.0)
        schedule = SimpleNamespace(stale=False, slots=["a", "b"], reasoning=reasoning)
        inner = SimpleNamespace(active_schedule=schedule)
        # WeatherScheduler pattern: has _scheduler attribute pointing to inner Scheduler
        weather_sched = SimpleNamespace(_scheduler=inner)
        r = _sched_metrics(weather_sched)
        assert r["has_schedule"] is True
        assert r["slot_count"] == 2
        assert r["stale"] is False
        assert r["solar_forecast_kwh"] == pytest.approx(8.0)
        assert r["consumption_forecast_kwh"] == pytest.approx(22.0)

    def test_stale_schedule_reported(self):
        reasoning = SimpleNamespace(tomorrow_solar_kwh=0.0, expected_consumption_kwh=15.0)
        schedule = SimpleNamespace(stale=True, slots=[], reasoning=reasoning)
        sched = SimpleNamespace(active_schedule=schedule)
        r = _sched_metrics(sched)
        assert r["stale"] is True

    def test_slot_count(self):
        reasoning = SimpleNamespace(tomorrow_solar_kwh=5.0, expected_consumption_kwh=18.0)
        schedule = SimpleNamespace(stale=False, slots=list(range(4)), reasoning=reasoning)
        sched = SimpleNamespace(active_schedule=schedule)
        r = _sched_metrics(sched)
        assert r["slot_count"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# InfluxMetricsWriter.write_health — line protocol correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestWriteHealth:
    def _make_writer(self):
        """Return an InfluxMetricsWriter with a mocked httpx client."""
        from backend.influx_writer import InfluxMetricsWriter
        writer = InfluxMetricsWriter(
            url="http://localhost:8086",
            database="ems",
            username="",
            password="",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_post = AsyncMock(return_value=mock_response)
        writer._http = MagicMock()
        writer._http.post = mock_post
        writer._http.aclose = AsyncMock()
        return writer

    def _body(self, writer) -> str:
        """Extract the posted line protocol body as a string."""
        _, kwargs = writer._http.post.call_args
        body = kwargs["content"]
        return body if isinstance(body, str) else body.decode()

    def _make_snap(self, **overrides):
        hl = HealthLogger()
        return _capture_defaults(hl, **overrides)

    @pytest.mark.anyio
    async def test_measurement_name_in_line(self):
        writer = self._make_writer()
        snap = self._make_snap()
        await writer.write_health(snap)
        assert self._body(writer).startswith("ems_health,")

    @pytest.mark.anyio
    async def test_control_state_tag_present(self):
        writer = self._make_writer()
        snap = self._make_snap(control_state="DISCHARGE")
        await writer.write_health(snap)
        assert "control_state=DISCHARGE" in self._body(writer)

    @pytest.mark.anyio
    async def test_combined_soc_field_present(self):
        writer = self._make_writer()
        snap = self._make_snap(h_soc=80.0, v_soc=60.0)
        await writer.write_health(snap)
        assert "combined_soc_pct=70.0" in self._body(writer)

    @pytest.mark.anyio
    async def test_ml_trained_bool_field(self):
        writer = self._make_writer()
        snap = self._make_snap(forecaster=None)
        await writer.write_health(snap)
        assert "ml_trained=false" in self._body(writer)

    @pytest.mark.anyio
    async def test_avail_fields_present(self):
        writer = self._make_writer()
        snap = self._make_snap(huawei_available=False, victron_available=True)
        await writer.write_health(snap)
        body = self._body(writer)
        assert "avail_huawei=false" in body
        assert "avail_victron=true" in body

    @pytest.mark.anyio
    async def test_optional_ml_fields_omitted_when_none(self):
        writer = self._make_writer()
        snap = self._make_snap(forecaster=None)
        await writer.write_health(snap)
        body = self._body(writer)
        assert "ml_last_prediction_kwh" not in body
        assert "ml_last_trained_age_h" not in body
        assert "ml_last_mape_pct" not in body

    @pytest.mark.anyio
    async def test_optional_ml_fields_written_when_present(self):
        from datetime import timedelta
        fc = SimpleNamespace(
            _heat_pump_model=object(),
            _dhw_model=None, _base_model=None,
            _last_trained_at=datetime.now(tz=timezone.utc) - timedelta(hours=2),
            _days_of_history=14, _total_samples=336,
            _last_prediction_kwh=21.3,
            _mape_path=None,
        )
        writer = self._make_writer()
        snap = self._make_snap(forecaster=fc)
        await writer.write_health(snap)
        body = self._body(writer)
        assert "ml_last_prediction_kwh=21.3" in body
        assert "ml_last_trained_age_h=" in body

    @pytest.mark.anyio
    async def test_sched_fields_present(self):
        reasoning = SimpleNamespace(tomorrow_solar_kwh=7.5, expected_consumption_kwh=19.0)
        schedule = SimpleNamespace(stale=False, slots=["a", "b", "c"], reasoning=reasoning)
        sched = SimpleNamespace(active_schedule=schedule)
        writer = self._make_writer()
        snap = self._make_snap(scheduler=sched)
        await writer.write_health(snap)
        body = self._body(writer)
        assert "sched_has_schedule=true" in body
        assert "sched_slot_count=3i" in body
        assert "sched_solar_forecast_kwh=7.5" in body

    @pytest.mark.anyio
    async def test_exception_swallowed(self):
        writer = self._make_writer()
        writer._http.post = AsyncMock(side_effect=RuntimeError("network down"))
        snap = self._make_snap()
        # Must not raise
        await writer.write_health(snap)

    @pytest.mark.anyio
    async def test_flag_fields_present(self):
        writer = self._make_writer()
        # Trigger soc imbalance flag (>30%)
        snap = self._make_snap(h_soc=90.0, v_soc=55.0)
        await writer.write_health(snap)
        body = self._body(writer)
        assert "flag_soc_imbalance=true" in body
        assert "flag_system_degraded=" in body
        assert "flag_ml_stale=" in body
