"""Tests for ConsumptionForecaster — ML-based consumption forecasting.

All tests use a real in-memory / on-disk SQLite database populated with
synthetic data.  No mocks are used for the DB or ML layer.

Test coverage:
- Cold-start: returns ConsumptionForecast(fallback_used=True) when < 14 days
- Happy path: returns ConsumptionForecast(fallback_used=False) when >= 14 days
- Sanity: today_expected_kwh is non-zero, non-NaN, in [5, 100] kWh on 30-day fixture
- Reasoning text: ML breakdown string contains "ML forecast: heat_pump="
- Entity absent: forecaster handles missing DHW entity gracefully (no raise)
- retrain_if_stale: calls train() when model is None; skips when recently trained
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import math

import pytest

from backend.config import HaStatisticsConfig
from backend.consumption_forecaster import ConsumptionForecaster
from backend.ha_statistics_reader import HaStatisticsReader
from backend.schedule_models import ConsumptionForecast


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
    """Generate *days* × 24 hourly rows ending now.

    If *vary* is True, values oscillate ± 20 % with hour-of-day to give the
    GBR something non-trivial to learn.
    """
    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    rows = []
    for h in range(days * 24 - 1, -1, -1):
        ts = now - timedelta(hours=h)
        hour = ts.hour
        if vary:
            # Sinusoidal variation peaking at noon
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

    # train() will find < 14 days → models stay None
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
    # Do NOT call train()

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
# Tests: happy path (≥ 14 days)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_query_consumption_history_ml_path_when_14_days(tmp_path):
    """Returns fallback_used=False when trained on ≥ 14 days of data."""
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
    # 2000 W heat pump + 500 W DHW + 300 W base → ~67 kWh/day theoretical max
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
    # Should be approximately 21 days (boundary rows may add ±1)
    assert 18 <= result.days_of_history <= 22


# ---------------------------------------------------------------------------
# Tests: DHW entity absent
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_forecaster_handles_missing_dhw_entity(tmp_path):
    """Forecaster trains and predicts without raising when DHW entity is absent."""
    db_path = str(tmp_path / "ha.db")
    # No DHW rows inserted
    _populate_db(db_path, days=30, dhw_val=None)

    config = _build_config(db_path)
    reader = HaStatisticsReader(db_path)
    forecaster = ConsumptionForecaster(reader, config)
    await forecaster.train()  # should not raise

    result = await forecaster.query_consumption_history()
    # Should still work — DHW contribution is 0, others contribute
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

    # Immediately retrain_if_stale with 24h threshold — should be a no-op
    await forecaster.retrain_if_stale(stale_hours=24)
    # The model object should be the same (not replaced)
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
# Tests: observability — INFO log on successful predict
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
    # INFO message should contain heat_pump and total kWh
    assert "heat_pump" in caplog.text


# ---------------------------------------------------------------------------
# Tests: get_forecast_comparison() — S03 T01
# ---------------------------------------------------------------------------

def test_get_forecast_comparison_returns_none_before_prediction(tmp_path):
    """get_forecast_comparison returns None before any ML prediction has been made."""
    from unittest.mock import MagicMock
    from backend.config import HaStatisticsConfig

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
    # No prediction has been made yet
    result = forecaster.get_forecast_comparison(10.0)
    assert result is None


def test_get_forecast_comparison_after_prediction(tmp_path):
    """get_forecast_comparison returns correct dict after setting _last_prediction_kwh."""
    from datetime import date
    from unittest.mock import MagicMock
    from backend.config import HaStatisticsConfig

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
    # Manually inject a prediction (as would happen after successful ML path)
    forecaster._last_prediction_kwh = 18.2
    forecaster._last_prediction_date = date.today()

    result = forecaster.get_forecast_comparison(19.7)
    assert result is not None
    assert result["predicted_kwh"] == pytest.approx(18.2, abs=0.01)
    assert result["actual_kwh"] == pytest.approx(19.7, abs=0.01)
    # error_pct = abs(18.2 - 19.7) / 19.7 * 100 = 7.61...%
    assert result["error_pct"] == pytest.approx(7.6, abs=0.1)


def test_get_forecast_comparison_zero_actual_no_division(tmp_path):
    """get_forecast_comparison does not raise ZeroDivisionError when actual_kwh=0."""
    from unittest.mock import MagicMock
    from backend.config import HaStatisticsConfig

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

    # Should not raise — divides by max(0.0, 0.001) = 0.001
    result = forecaster.get_forecast_comparison(0.0)
    assert result is not None
    assert result["predicted_kwh"] == pytest.approx(5.0)
    assert result["actual_kwh"] == pytest.approx(0.0)
    # error_pct = abs(5.0 - 0.0) / 0.001 * 100 = 500000 % — just ensure no raise
    assert result["error_pct"] > 0


# ---------------------------------------------------------------------------
# Tests: executor offloading — .fit() via anyio.to_thread.run_sync
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
        # Make run_sync actually call the function so training completes
        mock_run.side_effect = lambda fn, *a, **kw: fn()
        await forecaster.train()
        # At least 2 calls: heat_pump.fit and base.fit (dhw may or may not have data)
        assert mock_run.call_count >= 2, (
            f"Expected >= 2 run_sync calls, got {mock_run.call_count}"
        )
