"""Tests for ConsumptionForecaster -- ML-based consumption forecasting.

All tests use a real in-memory / on-disk SQLite database populated with
synthetic data.  No mocks are used for the DB or ML layer.

Test coverage:
- Cold-start: returns ConsumptionForecast(fallback_used=True) when < 14 days
- Happy path: returns ConsumptionForecast(fallback_used=False) when >= 14 days
- Sanity: today_expected_kwh is non-zero, non-NaN, in [5, 100] kWh on 30-day fixture
- Reasoning text: ML breakdown string contains "ML forecast: heat_pump="
- Entity absent: forecaster handles missing DHW entity gracefully (no raise)
- retrain_if_stale: calls train() when model is None; skips when recently trained
- HistGBR with NaN lag features (FCST-04)
- Lag features in feature matrix (FCST-02)
- Calendar features (is_weekend) (FCST-03)
- Recency weighting (FCST-06)
- Time-series cross-validation (FCST-07)
- FeaturePipeline wiring (per user decision)
- Weather client integration (FCST-01)
- Feature count mismatch model discard (FCST-04)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import math

import numpy
import pytest

from backend.config import HaStatisticsConfig
from backend.consumption_forecaster import (
    FEATURE_NAMES,
    ConsumptionForecaster,
    _build_features,
    _build_lag_features,
    _compute_ewm,
    _compute_recency_weights,
)
from backend.ha_statistics_reader import HaStatisticsReader
from backend.schedule_models import ConsumptionForecast, HourlyConsumptionForecast


# ---------------------------------------------------------------------------
# Fixture helpers (duplicated from test_ha_statistics_reader for isolation)
# ---------------------------------------------------------------------------

def _create_ha_db(path: str) -> None:
    conn = sqlite3.connect(path)
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS statistics_meta (
                id INTEGER PRIMARY KEY,
                statistic_id TEXT NOT NULL,
                source TEXT
            );
            CREATE TABLE IF NOT EXISTS statistics (
                id INTEGER PRIMARY KEY,
                metadata_id INTEGER NOT NULL,
                start DATETIME NOT NULL,
                mean REAL,
                state REAL,
                min REAL,
                max REAL
            );
        """)
    conn.close()


def _insert_entity_rows(
    path: str,
    statistic_id: str,
    rows: list[tuple[str, float]],
) -> None:
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO statistics_meta (statistic_id, source)"
            " VALUES (?, 'recorder')",
            (statistic_id,),
        )
        meta_id = conn.execute(
            "SELECT id FROM statistics_meta WHERE statistic_id = ?",
            (statistic_id,),
        ).fetchone()[0]
        conn.executemany(
            "INSERT INTO statistics (metadata_id, start, mean) VALUES (?, ?, ?)",
            [(meta_id, ts, val) for ts, val in rows],
        )
    conn.close()


def _make_hourly_rows(
    days: int,
    base_value: float = 1000.0,
    vary: bool = True,
) -> list[tuple[str, float]]:
    """Generate *days* x 24 hourly rows ending now.

    If *vary* is True, values oscillate +/- 20% with hour-of-day to give the
    model something non-trivial to learn.
    """
    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    rows = []
    for h in range(days * 24 - 1, -1, -1):
        ts = now - timedelta(hours=h)
        hour = ts.hour
        if vary:
            import math as _math
            factor = 0.8 + 0.4 * _math.sin(_math.pi * hour / 12)
        else:
            factor = 1.0
        rows.append((ts.strftime("%Y-%m-%d %H:%M:%S"), base_value * factor))
    return rows


def _build_config(
    db_path: str,
    min_training_days: int = 14,
    outdoor_entity: str = "sensor.outdoor_temp",
    hp_entity: str = "sensor.heat_pump",
    dhw_entity: str = "sensor.dhw",
) -> HaStatisticsConfig:
    return HaStatisticsConfig(
        db_path=db_path,
        min_training_days=min_training_days,
        outdoor_temp_entity=outdoor_entity,
        heat_pump_entity=hp_entity,
        dhw_entity=dhw_entity,
    )


def _populate_db(
    path: str,
    days: int,
    outdoor_temp_val: float = 10.0,
    hp_val: float = 2000.0,
    dhw_val: float | None = 500.0,
    outdoor_entity: str = "sensor.outdoor_temp",
    hp_entity: str = "sensor.heat_pump",
    dhw_entity: str = "sensor.dhw",
) -> None:
    """Populate a synthetic DB for forecaster tests."""
    _create_ha_db(path)
    _insert_entity_rows(path, outdoor_entity, _make_hourly_rows(days, outdoor_temp_val, vary=False))
    _insert_entity_rows(path, hp_entity, _make_hourly_rows(days, hp_val, vary=True))
    if dhw_val is not None:
        _insert_entity_rows(path, dhw_entity, _make_hourly_rows(days, dhw_val, vary=True))


# ---------------------------------------------------------------------------
# Tests: cold-start (insufficient data)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_query_consumption_history_cold_start_when_7_days(tmp_path):
    """Returns fallback_used=True when only 7 days of data (below 14-day threshold)."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=7)

    config = _build_config(db_path, min_training_days=14)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)

    await forecaster.train()

    result = await forecaster.query_consumption_history()
    assert isinstance(result, ConsumptionForecast)
    assert result.fallback_used is True, "Expected fallback_used=True with only 7 days"


@pytest.mark.anyio
async def test_query_consumption_history_cold_start_without_training(tmp_path):
    """Returns fallback_used=True when train() was never called."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)

    result = await forecaster.query_consumption_history()
    assert result.fallback_used is True


@pytest.mark.anyio
async def test_cold_start_logs_warning(tmp_path, caplog):
    """Cold-start path emits a WARNING log."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=5)

    config = _build_config(db_path, min_training_days=14)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    import logging
    with caplog.at_level(logging.WARNING, logger="ems.consumption_forecaster"):
        await forecaster.query_consumption_history()
    assert "cold-start" in caplog.text.lower() or "fallback" in caplog.text.lower()


# ---------------------------------------------------------------------------
# Tests: happy path (>= 14 days)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_query_consumption_history_ml_path_when_14_days(tmp_path):
    """Returns fallback_used=False when trained on >= 14 days of data."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=14)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.query_consumption_history()
    assert isinstance(result, ConsumptionForecast)
    assert result.fallback_used is False, (
        f"Expected fallback_used=False, got {result.fallback_used}. "
        f"days_of_history={result.days_of_history}"
    )


@pytest.mark.anyio
async def test_query_consumption_history_positive_kwh_30_days(tmp_path):
    """today_expected_kwh is non-zero, non-NaN, and in [5, 100] kWh on 30-day fixture."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30, hp_val=2000.0, dhw_val=500.0)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.query_consumption_history()
    kwh = result.today_expected_kwh
    assert not math.isnan(kwh), "today_expected_kwh is NaN"
    assert kwh > 0, f"today_expected_kwh must be positive, got {kwh}"
    assert 5 <= kwh <= 100, f"today_expected_kwh {kwh:.2f} outside sanity range [5, 100]"


@pytest.mark.anyio
async def test_reasoning_text_contains_ml_breakdown(tmp_path):
    """reasoning_text contains 'ML forecast: heat_pump=' after successful prediction."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()
    await forecaster.query_consumption_history()

    text = forecaster.reasoning_text
    assert "ML forecast: heat_pump=" in text, (
        f"Expected 'ML forecast: heat_pump=' in reasoning_text, got: {text!r}"
    )


@pytest.mark.anyio
async def test_query_consumption_history_days_of_history_populated(tmp_path):
    """days_of_history is set to the number of days in the training data."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=21)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.query_consumption_history()
    assert result.days_of_history > 0
    assert 18 <= result.days_of_history <= 22


# ---------------------------------------------------------------------------
# Tests: DHW entity absent
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_forecaster_handles_missing_dhw_entity(tmp_path):
    """Forecaster trains and predicts without raising when DHW entity is absent."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30, dhw_val=None)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.query_consumption_history()
    assert isinstance(result, ConsumptionForecast)
    assert result.fallback_used is False
    assert result.today_expected_kwh > 0


# ---------------------------------------------------------------------------
# Tests: retrain_if_stale
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_retrain_if_stale_trains_when_model_is_none(tmp_path):
    """retrain_if_stale calls train() when model is None."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=20)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)

    assert forecaster._heat_pump_model is None
    await forecaster.retrain_if_stale(stale_hours=24)
    assert forecaster._heat_pump_model is not None


@pytest.mark.anyio
async def test_retrain_if_stale_skips_when_recently_trained(tmp_path):
    """retrain_if_stale does NOT retrain when models are fresh."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=20)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    first_model = forecaster._heat_pump_model

    await forecaster.retrain_if_stale(stale_hours=24)
    assert forecaster._heat_pump_model is first_model


# ---------------------------------------------------------------------------
# Tests: fallback_used=True result has correct seasonal value
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cold_start_uses_seasonal_fallback_kwh(tmp_path):
    """Cold-start today_expected_kwh matches _seasonal_fallback_kwh(today)."""
    from datetime import date
    from backend.influx_reader import _seasonal_fallback_kwh

    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=5)

    config = _build_config(db_path, min_training_days=14)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.query_consumption_history()
    expected = _seasonal_fallback_kwh(date.today())
    assert result.today_expected_kwh == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Tests: observability -- INFO log on successful predict
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ml_predict_logs_info_with_breakdown(tmp_path, caplog):
    """ML predict logs an INFO message with heat_pump/dhw/base breakdown."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    import logging
    with caplog.at_level(logging.INFO, logger="ems.consumption_forecaster"):
        await forecaster.query_consumption_history()

    assert "ML forecast" in caplog.text
    assert "heat_pump" in caplog.text


# ---------------------------------------------------------------------------
# Tests: get_forecast_comparison()
# ---------------------------------------------------------------------------

def test_get_forecast_comparison_returns_none_before_prediction(tmp_path):
    """get_forecast_comparison returns None before any ML prediction has been made."""
    db_path = str(tmp_path / "ha.db")
    config = HaStatisticsConfig(
        db_path=db_path,
        min_training_days=14,
        outdoor_temp_entity="sensor.outdoor_temp",
        heat_pump_entity="sensor.heat_pump",
        dhw_entity="sensor.dhw",
    )
    reader = MagicMock()
    forecaster = ConsumptionForecaster(reader, config)
    result = forecaster.get_forecast_comparison(10.0)
    assert result is None


def test_get_forecast_comparison_after_prediction(tmp_path):
    """get_forecast_comparison returns correct dict after setting _last_prediction_kwh."""
    from datetime import date

    db_path = str(tmp_path / "ha.db")
    config = HaStatisticsConfig(
        db_path=db_path,
        min_training_days=14,
        outdoor_temp_entity="sensor.outdoor_temp",
        heat_pump_entity="sensor.heat_pump",
        dhw_entity="sensor.dhw",
    )
    reader = MagicMock()
    forecaster = ConsumptionForecaster(reader, config)
    forecaster._last_prediction_kwh = 18.2
    forecaster._last_prediction_date = date.today()

    result = forecaster.get_forecast_comparison(19.7)
    assert result is not None
    assert result["predicted_kwh"] == pytest.approx(18.2, abs=0.01)
    assert result["actual_kwh"] == pytest.approx(19.7, abs=0.01)
    assert result["error_pct"] == pytest.approx(7.6, abs=0.1)


def test_get_forecast_comparison_zero_actual_no_division(tmp_path):
    """get_forecast_comparison does not raise ZeroDivisionError when actual_kwh=0."""
    db_path = str(tmp_path / "ha.db")
    config = HaStatisticsConfig(
        db_path=db_path,
        min_training_days=14,
        outdoor_temp_entity="sensor.outdoor_temp",
        heat_pump_entity="sensor.heat_pump",
        dhw_entity="sensor.dhw",
    )
    reader = MagicMock()
    forecaster = ConsumptionForecaster(reader, config)
    forecaster._last_prediction_kwh = 5.0

    result = forecaster.get_forecast_comparison(0.0)
    assert result is not None
    assert result["predicted_kwh"] == pytest.approx(5.0)
    assert result["actual_kwh"] == pytest.approx(0.0)
    assert result["error_pct"] > 0


# ---------------------------------------------------------------------------
# Tests: executor offloading -- .fit() via anyio.to_thread.run_sync
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_train_uses_executor(tmp_path):
    """Verify that model.fit() is offloaded via anyio.to_thread.run_sync."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)

    with patch(
        "backend.consumption_forecaster.anyio.to_thread.run_sync",
        new_callable=AsyncMock,
    ) as mock_run:
        mock_run.side_effect = lambda fn, *a, **kw: fn()
        await forecaster.train()
        assert mock_run.call_count >= 2, (
            f"Expected >= 2 run_sync calls, got {mock_run.call_count}"
        )


# ---------------------------------------------------------------------------
# Tests: predict_hourly() -- 72h hourly consumption predictions
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_predict_hourly_returns_72_values_when_trained(tmp_path):
    """predict_hourly() returns HourlyConsumptionForecast with 72 hourly_kwh values."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30, hp_val=2000.0, dhw_val=500.0)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.predict_hourly()
    assert isinstance(result, HourlyConsumptionForecast)
    assert len(result.hourly_kwh) == 72
    assert result.horizon_hours == 72


@pytest.mark.anyio
async def test_predict_hourly_ml_source_when_trained(tmp_path):
    """predict_hourly() returns fallback_used=False and source='ml'."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30, hp_val=2000.0, dhw_val=500.0)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.predict_hourly()
    assert result.fallback_used is False
    assert result.source == "ml"


@pytest.mark.anyio
async def test_predict_hourly_total_equals_sum(tmp_path):
    """predict_hourly() total_kwh equals sum of hourly_kwh values."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30, hp_val=2000.0, dhw_val=500.0)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.predict_hourly()
    assert result.total_kwh == pytest.approx(sum(result.hourly_kwh), abs=0.001)


@pytest.mark.anyio
async def test_predict_hourly_cold_start_fallback(tmp_path):
    """predict_hourly() cold-start returns fallback_used=True and source='seasonal'."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=5)

    config = _build_config(db_path, min_training_days=14)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.predict_hourly()
    assert isinstance(result, HourlyConsumptionForecast)
    assert result.fallback_used is True
    assert result.source == "seasonal"
    assert len(result.hourly_kwh) == 72
    assert result.horizon_hours == 72


@pytest.mark.anyio
async def test_predict_hourly_cold_start_hour_variation(tmp_path):
    """predict_hourly() cold-start hourly values have hour-of-day variation."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=5)

    config = _build_config(db_path, min_training_days=14)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.predict_hourly()

    from datetime import datetime as dt
    now = dt.now(tz=timezone.utc)
    night_vals = []
    day_vals = []
    for h, kwh in enumerate(result.hourly_kwh):
        hour_of_day = (now + timedelta(hours=h)).hour
        if hour_of_day in (0, 1, 2, 3, 4, 5):
            night_vals.append(kwh)
        elif hour_of_day in (10, 11, 12, 13, 14, 15, 16):
            day_vals.append(kwh)

    if night_vals and day_vals:
        avg_night = sum(night_vals) / len(night_vals)
        avg_day = sum(day_vals) / len(day_vals)
        assert avg_day > avg_night, (
            f"Expected daytime average ({avg_day:.4f}) > nighttime average ({avg_night:.4f})"
        )


@pytest.mark.anyio
async def test_predict_hourly_custom_horizon(tmp_path):
    """predict_hourly(horizon_hours=48) returns 48 values instead of 72."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30, hp_val=2000.0, dhw_val=500.0)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.predict_hourly(horizon_hours=48)
    assert len(result.hourly_kwh) == 48
    assert result.horizon_hours == 48


@pytest.mark.anyio
async def test_predict_hourly_all_values_non_negative(tmp_path):
    """predict_hourly() all hourly values are non-negative (clamped to 0)."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30, hp_val=2000.0, dhw_val=500.0)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.predict_hourly()
    for i, val in enumerate(result.hourly_kwh):
        assert val >= 0, f"hourly_kwh[{i}] = {val} is negative"


@pytest.mark.anyio
async def test_existing_query_consumption_history_still_works(tmp_path):
    """Existing query_consumption_history() tests still pass (no regression)."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30, hp_val=2000.0, dhw_val=500.0)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.query_consumption_history()
    assert isinstance(result, ConsumptionForecast)
    assert result.fallback_used is False
    assert result.today_expected_kwh > 0


# ===========================================================================
# NEW TESTS: Phase 17 upgraded forecaster
# ===========================================================================


# ---------------------------------------------------------------------------
# Tests: HistGBR handles NaN lag features (FCST-04)
# ---------------------------------------------------------------------------

def test_histgbr_handles_nan():
    """HistGradientBoostingRegressor trains and predicts with NaN lag values."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    # Build feature matrix with NaN in lag columns (indices 6, 7)
    X = []
    y = []
    for i in range(100):
        row = [
            10.0,          # outdoor_temp_c
            10.0,          # ewm_temp_3d
            float(i % 7),  # day_of_week
            float(i % 24), # hour_of_day
            6.0,           # month
            1.0 if (i % 7) >= 5 else 0.0,  # is_weekend
            float("nan"),  # lag_24h
            float("nan"),  # lag_168h
        ]
        X.append(row)
        y.append(1000.0 + 100.0 * math.sin(math.pi * (i % 24) / 12))

    model = HistGradientBoostingRegressor(
        max_iter=50, max_depth=3, random_state=42
    )
    model.fit(X, y)  # Should not crash

    # Predict with NaN lag features
    pred = model.predict([X[0]])
    assert len(pred) == 1
    assert not math.isnan(pred[0]), "Prediction should not be NaN"


# ---------------------------------------------------------------------------
# Tests: lag features in feature matrix (FCST-02)
# ---------------------------------------------------------------------------

def test_lag_features_in_matrix():
    """_build_features with consumption_map produces 8-column rows with correct lag values."""
    base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    timestamps = [base + timedelta(hours=h) for h in range(48)]
    outdoor_temps = [15.0] * 48
    ewm_temps = [15.0] * 48

    # Build a consumption map with known values
    consumption_map = {}
    for ts in timestamps:
        consumption_map[ts] = 1000.0 + ts.hour * 10.0

    features = _build_features(timestamps, outdoor_temps, ewm_temps, consumption_map)

    assert len(features) == 48
    assert len(features[0]) == 8, f"Expected 8 features, got {len(features[0])}"

    # Check lag_24h at index 24 (should have data from hour 0)
    row_24 = features[24]
    assert not math.isnan(row_24[6]), "lag_24h at hour 24 should not be NaN"
    assert row_24[6] == pytest.approx(consumption_map[timestamps[0]], abs=0.1)

    # Check lag_24h at index 0 (should be NaN -- no data 24h before)
    row_0 = features[0]
    assert math.isnan(row_0[6]), "lag_24h at hour 0 should be NaN (no prior data)"

    # lag_168h should be NaN for all (only 48 hours of data)
    for row in features:
        assert math.isnan(row[7]), "lag_168h should be NaN (only 48h of data)"


# ---------------------------------------------------------------------------
# Tests: calendar features (FCST-03)
# ---------------------------------------------------------------------------

def test_calendar_features():
    """_build_features includes is_weekend=1.0 for Saturday/Sunday, 0.0 for weekday."""
    # 2025-06-14 is a Saturday, 2025-06-16 is a Monday
    saturday = datetime(2025, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
    sunday = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    monday = datetime(2025, 6, 16, 12, 0, 0, tzinfo=timezone.utc)

    timestamps = [saturday, sunday, monday]
    temps = [15.0] * 3
    ewm = [15.0] * 3

    features = _build_features(timestamps, temps, ewm)

    # is_weekend is at index 5
    assert features[0][5] == 1.0, "Saturday should have is_weekend=1.0"
    assert features[1][5] == 1.0, "Sunday should have is_weekend=1.0"
    assert features[2][5] == 0.0, "Monday should have is_weekend=0.0"


# ---------------------------------------------------------------------------
# Tests: recency weighting (FCST-06)
# ---------------------------------------------------------------------------

def test_recency_weighting():
    """_compute_recency_weights: newest ~ 1.0, 30-day-old ~ 0.5."""
    now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    # 60 days of hourly timestamps
    timestamps = [now - timedelta(hours=h) for h in range(60 * 24 - 1, -1, -1)]

    weights = _compute_recency_weights(timestamps, half_life_days=30.0)

    assert len(weights) == len(timestamps)
    # Newest weight should be ~1.0
    assert weights[-1] == pytest.approx(1.0, abs=0.01), (
        f"Newest weight should be ~1.0, got {weights[-1]}"
    )

    # Find the weight approximately 30 days back (720 hours)
    # The 30-day-old sample is at index len - 720 - 1
    idx_30d = len(timestamps) - 720 - 1
    if idx_30d >= 0:
        assert weights[idx_30d] == pytest.approx(0.5, abs=0.05), (
            f"30-day-old weight should be ~0.5, got {weights[idx_30d]}"
        )


# ---------------------------------------------------------------------------
# Tests: time-series CV (FCST-07)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_time_series_cv(tmp_path, caplog):
    """train() uses TimeSeriesSplit -- verified via CV log output."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)

    import logging
    with caplog.at_level(logging.INFO, logger="ems.consumption_forecaster"):
        await forecaster.train()

    assert "CV scores" in caplog.text, (
        "Expected 'CV scores' in log output (TimeSeriesSplit CV)"
    )
    assert "neg_MAPE" in caplog.text, (
        "Expected 'neg_MAPE' in log output"
    )


# ---------------------------------------------------------------------------
# Tests: train() uses 8 feature names in metadata (FCST-01)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_train_uses_weather_features(tmp_path):
    """After training, feature_names metadata includes all 8 feature names."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30)

    from backend.model_store import ModelStore
    store_path = str(tmp_path / "models")
    model_store = ModelStore(store_path)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(
        reader, config, model_store=model_store
    )
    await forecaster.train()

    # Load the saved model and check metadata
    result = model_store.load("heat_pump")
    assert result is not None, "heat_pump model should be saved"
    _, meta = result
    assert meta.feature_names == FEATURE_NAMES
    assert len(meta.feature_names) == 8


# ---------------------------------------------------------------------------
# Tests: FeaturePipeline wiring (per user decision)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_train_uses_feature_pipeline(tmp_path):
    """When FeaturePipeline is provided, train() calls pipeline.extract()."""
    from backend.feature_pipeline import FeatureSet

    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)

    # Build synthetic FeatureSet data (30 days)
    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    synthetic_data = []
    for h in range(30 * 24):
        ts = now - timedelta(hours=30 * 24 - 1 - h)
        synthetic_data.append((ts, 2000.0))

    temp_data = [
        (ts, 10.0) for ts, _ in synthetic_data
    ]

    feature_set = FeatureSet(
        outdoor_temp=temp_data,
        heat_pump=synthetic_data,
        dhw=[(ts, 500.0) for ts, _ in synthetic_data],
        timestamps=[ts for ts, _ in synthetic_data],
        source="ha_statistics",
    )

    mock_pipeline = AsyncMock()
    mock_pipeline.extract = AsyncMock(return_value=feature_set)

    forecaster = ConsumptionForecaster(
        reader, config, feature_pipeline=mock_pipeline
    )

    # Spy on reader to verify it's NOT called
    reader_spy = AsyncMock(wraps=reader.read_entity_hourly)
    with patch.object(reader, "read_entity_hourly", reader_spy):
        await forecaster.train()

    # Pipeline.extract() should have been called
    mock_pipeline.extract.assert_called_once_with(
        force_refresh=True, days=90
    )
    # Reader should NOT have been called (pipeline path used)
    reader_spy.assert_not_called()


@pytest.mark.anyio
async def test_train_fallback_without_pipeline(tmp_path):
    """When FeaturePipeline is None, train() falls back to direct reader calls."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)

    # No feature_pipeline provided
    forecaster = ConsumptionForecaster(reader, config)

    # Spy on reader to verify it IS called
    original_read = reader.read_entity_hourly
    reader_spy = AsyncMock(side_effect=original_read)
    with patch.object(reader, "read_entity_hourly", reader_spy):
        await forecaster.train()

    # Reader should have been called (fallback path)
    assert reader_spy.call_count >= 2, (
        f"Expected >= 2 reader calls, got {reader_spy.call_count}"
    )


# ---------------------------------------------------------------------------
# Tests: predict with weather client (FCST-01)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_predict_with_weather_client(tmp_path):
    """predict_hourly uses weather client temps when available."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30, hp_val=2000.0, dhw_val=500.0)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)

    # Mock weather client returning 5.0 C for all hours
    mock_weather = AsyncMock()
    mock_weather.get_temperature_forecast = AsyncMock(
        return_value=[5.0] * 72
    )

    forecaster = ConsumptionForecaster(
        reader, config, weather_client=mock_weather
    )
    await forecaster.train()

    result = await forecaster.predict_hourly()
    assert result.fallback_used is False

    # Verify weather client was called
    mock_weather.get_temperature_forecast.assert_called()


@pytest.mark.anyio
async def test_predict_fallback_no_weather(tmp_path):
    """predict_hourly works when weather_client is None (uses fallback temp)."""
    db_path = str(tmp_path / "ha.db")
    _populate_db(db_path, days=30, hp_val=2000.0, dhw_val=500.0)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)

    # No weather client
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()

    result = await forecaster.predict_hourly()
    assert result.fallback_used is False
    assert result.total_kwh > 0


# ---------------------------------------------------------------------------
# Tests: feature count mismatch discards model (FCST-04)
# ---------------------------------------------------------------------------

def test_feature_count_mismatch_discards_model(tmp_path):
    """_try_load_models discards model when stored feature count != 8."""
    import sklearn as _sklearn
    import numpy as _numpy
    from backend.model_store import ModelMetadata, ModelStore

    store_path = str(tmp_path / "models")
    model_store = ModelStore(store_path)

    # Save a model with old 5-feature metadata
    from sklearn.ensemble import HistGradientBoostingRegressor
    dummy_model = HistGradientBoostingRegressor(max_iter=10, random_state=42)
    # Train on minimal data so it's a valid model
    X_dummy = [[1.0, 2.0, 3.0, 4.0, 5.0]] * 20
    y_dummy = [100.0] * 20
    dummy_model.fit(X_dummy, y_dummy)

    model_store.save(
        "heat_pump",
        dummy_model,
        ModelMetadata(
            sklearn_version=_sklearn.__version__,
            numpy_version=_numpy.__version__,
            trained_at="2025-01-01T00:00:00",
            sample_count=20,
            feature_names=["outdoor_temp_c", "ewm_temp_3d", "day_of_week", "hour_of_day", "month"],
        ),
    )

    # Create forecaster with this model store
    config = HaStatisticsConfig(
        db_path=str(tmp_path / "ha.db"),
        min_training_days=14,
        outdoor_temp_entity="sensor.outdoor_temp",
        heat_pump_entity="sensor.heat_pump",
        dhw_entity="sensor.dhw",
    )
    reader = MagicMock()
    forecaster = ConsumptionForecaster(reader, config, model_store=model_store)

    # Model should have been discarded (feature count 5 != 8)
    assert forecaster._heat_pump_model is None, (
        "Model with 5 features should be discarded (expected 8)"
    )


# ---------------------------------------------------------------------------
# Tests: FEATURE_NAMES constant
# ---------------------------------------------------------------------------

def test_feature_names_constant():
    """FEATURE_NAMES has exactly 8 entries with expected names."""
    assert len(FEATURE_NAMES) == 8
    assert FEATURE_NAMES == [
        "outdoor_temp_c",
        "ewm_temp_3d",
        "day_of_week",
        "hour_of_day",
        "month",
        "is_weekend",
        "lag_24h",
        "lag_168h",
    ]
