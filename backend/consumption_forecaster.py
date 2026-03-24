"""ConsumptionForecaster — ML-based household consumption forecast.

Trains three ``HistGradientBoostingRegressor`` models (heat pump, DHW, base
load) on Home Assistant long-term statistics and predicts next-24h consumption
in kWh, broken down by load type.

The class implements the same ``query_consumption_history()`` protocol as
``InfluxMetricsReader`` so it can be injected into the ``Scheduler`` as a
drop-in replacement without modifying any call sites.

Feature set (8 columns)
-----------------------
``outdoor_temp_c``, ``ewm_temp_3d``, ``day_of_week``, ``hour_of_day``,
``month``, ``is_weekend``, ``lag_24h``, ``lag_168h``.

Cold-start / failure degradation
---------------------------------
When the HA database has fewer than ``min_training_days`` of data, or when
all three models are untrained (e.g. on first startup), the forecaster falls
back to the same ``_seasonal_fallback_kwh`` constant used by the Influx-based
reader and sets ``fallback_used=True`` in the returned ``ConsumptionForecast``.

Observability
-------------
- Logger name: ``ems.consumption_forecaster``
- INFO ``"ConsumptionForecaster: trained heat_pump model on N samples,
  dhw on M samples, base on K samples"`` -- after each retrain
- INFO ``"ConsumptionForecaster: ML forecast heat_pump=X.X dhw=X.X
  base=X.X total=Y.Y kWh (days_of_history=N)"`` -- on each predict
- INFO ``"CV scores (neg_MAPE): mean=...% std=...%"`` -- per-model CV
- WARNING ``"ConsumptionForecaster: cold-start fallback ..."`` -- insufficient
  history
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import anyio.to_thread
import numpy
import sklearn

from backend.influx_reader import _seasonal_fallback_kwh
from backend.schedule_models import ConsumptionForecast, HourlyConsumptionForecast

if TYPE_CHECKING:
    from backend.config import HaStatisticsConfig
    from backend.feature_pipeline import FeaturePipeline
    from backend.ha_statistics_reader import HaStatisticsReader
    from backend.model_store import ModelMetadata, ModelStore
    from backend.weather_client import OpenMeteoClient

logger = logging.getLogger("ems.consumption_forecaster")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Base load placeholder (W) -- replaced in S02 when a real consumption entity
# is available in HA statistics.
_BASE_LOAD_W: float = 300.0

FEATURE_NAMES: list[str] = [
    "outdoor_temp_c",
    "ewm_temp_3d",
    "day_of_week",
    "hour_of_day",
    "month",
    "is_weekend",
    "lag_24h",
    "lag_168h",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _compute_ewm(values: list[float], span_days: int = 3) -> list[float]:
    """Compute a simple exponentially weighted mean over hourly observations.

    Parameters
    ----------
    values:
        Hourly values in chronological order.
    span_days:
        Equivalent EWM span in days (converted to an alpha so that the
        effective window is ``span_days * 24`` hours).

    Returns
    -------
    list[float]
        EWM-smoothed values, same length as *values*.
    """
    if not values:
        return []
    alpha = 2.0 / (span_days * 24 + 1)
    ewm: list[float] = []
    prev = values[0]
    for v in values:
        prev = alpha * v + (1.0 - alpha) * prev
        ewm.append(prev)
    return ewm


def _build_lag_features(
    timestamps: list[datetime],
    consumption_map: dict[datetime, float],
) -> tuple[list[float], list[float]]:
    """Build 24h and 168h (1-week) lag features from a consumption map.

    Parameters
    ----------
    timestamps:
        Sorted list of hourly timestamps to compute lag features for.
    consumption_map:
        Mapping from hour-truncated UTC datetime to consumption value.

    Returns
    -------
    tuple[list[float], list[float]]
        ``(lag_24h, lag_168h)`` lists aligned with *timestamps*.  Uses
        ``float("nan")`` when the lagged timestamp is not available in the
        map (HistGradientBoostingRegressor handles NaN natively).
    """
    lag_24h: list[float] = []
    lag_168h: list[float] = []
    for ts in timestamps:
        ts_24 = ts - timedelta(hours=24)
        ts_168 = ts - timedelta(hours=168)
        lag_24h.append(consumption_map.get(ts_24, float("nan")))
        lag_168h.append(consumption_map.get(ts_168, float("nan")))
    return lag_24h, lag_168h


def _compute_recency_weights(
    timestamps: list[datetime],
    half_life_days: float = 30.0,
) -> numpy.ndarray:
    """Compute exponential decay recency weights for training samples.

    Parameters
    ----------
    timestamps:
        Sorted hourly timestamps (oldest first).
    half_life_days:
        Number of days after which a sample's weight drops to 0.5.

    Returns
    -------
    numpy.ndarray
        Array of weights (newest sample ~ 1.0, 30-day-old sample ~ 0.5).
    """
    if not timestamps:
        return numpy.array([], dtype=numpy.float64)
    newest = timestamps[-1]
    decay = math.log(2) / (half_life_days * 24)
    weights = numpy.array(
        [
            math.exp(-decay * (newest - ts).total_seconds() / 3600)
            for ts in timestamps
        ],
        dtype=numpy.float64,
    )
    return weights


def _build_features(
    timestamps: list[datetime],
    outdoor_temps: list[float],
    ewm_temps: list[float],
    consumption_map: dict[datetime, float] | None = None,
) -> list[list[float]]:
    """Build a feature matrix row for each hourly observation.

    Features (8 columns):
    ``[outdoor_temp_c, ewm_temp_3d, day_of_week, hour_of_day, month,
    is_weekend, lag_24h, lag_168h]``

    Parameters
    ----------
    timestamps:
        Sorted hourly timestamps.
    outdoor_temps:
        Outdoor temperature values aligned with *timestamps*.
    ewm_temps:
        EWM-smoothed temperature values aligned with *timestamps*.
    consumption_map:
        Optional mapping from hour-truncated UTC datetime to consumption
        value for computing lag features.  When ``None``, lag columns are
        filled with ``float("nan")``.
    """
    if consumption_map is not None:
        lag_24h, lag_168h = _build_lag_features(timestamps, consumption_map)
    else:
        lag_24h = [float("nan")] * len(timestamps)
        lag_168h = [float("nan")] * len(timestamps)

    rows: list[list[float]] = []
    for i, (ts, ot, ewm) in enumerate(
        zip(timestamps, outdoor_temps, ewm_temps)
    ):
        rows.append([
            ot,
            ewm,
            float(ts.weekday()),
            float(ts.hour),
            float(ts.month),
            1.0 if ts.weekday() >= 5 else 0.0,
            lag_24h[i],
            lag_168h[i],
        ])
    return rows


def _compute_daily_mape(
    predicted_hourly: list[float],
    actual_hourly: list[float],
) -> float | None:
    """Compute Mean Absolute Percentage Error for daily hourly pairs.

    Parameters
    ----------
    predicted_hourly:
        Predicted hourly consumption values.
    actual_hourly:
        Actual hourly consumption values.

    Returns
    -------
    float or None
        MAPE percentage (e.g. 12.5 means 12.5%), or ``None`` if fewer than
        12 valid pairs remain after filtering near-zero actuals.
    """
    if len(predicted_hourly) == 0 or len(actual_hourly) == 0:
        return None

    errors: list[float] = []
    for pred, actual in zip(predicted_hourly, actual_hourly):
        if actual < 0.1:
            continue  # skip near-zero to avoid MAPE explosion
        errors.append(abs(pred - actual) / actual * 100.0)

    if len(errors) < 12:
        return None

    return round(sum(errors) / len(errors), 1)


def _save_mape_history(
    mape_path: Path,
    date_str: str,
    mape_value: float,
    max_days: int = 30,
) -> None:
    """Append a MAPE entry to the history JSON file.

    Parameters
    ----------
    mape_path:
        Path to the JSON file storing MAPE history.
    date_str:
        ISO date string (YYYY-MM-DD) for the entry.
    mape_value:
        MAPE percentage value.
    max_days:
        Maximum number of entries to keep (oldest trimmed first).
    """
    history = _load_mape_history(mape_path)
    history.append({"date": date_str, "mape": mape_value})
    # Keep only the last max_days entries
    if len(history) > max_days:
        history = history[-max_days:]
    mape_path.parent.mkdir(parents=True, exist_ok=True)
    mape_path.write_text(json.dumps(history, indent=2))


def _load_mape_history(mape_path: Path) -> list[dict]:
    """Load MAPE history from a JSON file.

    Parameters
    ----------
    mape_path:
        Path to the JSON file.

    Returns
    -------
    list[dict]
        List of ``{"date": str, "mape": float}`` entries, or empty list on
        any error.
    """
    try:
        raw = mape_path.read_text()
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        return []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _seasonal_hourly_fallback(
    horizon_hours: int = 72,
) -> HourlyConsumptionForecast:
    """Return a seasonal hourly consumption forecast with hour-of-day variation.

    Uses ``_seasonal_fallback_kwh()`` for the daily total and distributes it
    across hours using realistic household weighting: low at night (0-5, 23),
    moderate morning/evening (6-9, 17-22), higher midday (10-16).

    Parameters
    ----------
    horizon_hours:
        Number of hours to predict (default 72).

    Returns
    -------
    HourlyConsumptionForecast
        With ``source="seasonal"`` and ``fallback_used=True``.
    """
    # Hour-of-day weights -- sum to ~24.0 so daily total is preserved
    _HOUR_WEIGHTS: dict[int, float] = {}
    for h in range(24):
        if h in (0, 1, 2, 3, 4, 5, 23):
            _HOUR_WEIGHTS[h] = 0.6
        elif h in (6, 7, 8, 9, 17, 18, 19, 20, 21, 22):
            _HOUR_WEIGHTS[h] = 1.2
        else:  # 10-16
            _HOUR_WEIGHTS[h] = 1.4

    weight_sum = sum(_HOUR_WEIGHTS.values())  # normalisation denominator

    now_utc = datetime.now(tz=timezone.utc)
    today = date.today()
    daily_kwh = _seasonal_fallback_kwh(today)

    hourly_kwh: list[float] = []
    for h in range(horizon_hours):
        future_hour = (now_utc + timedelta(hours=h)).hour
        weight = _HOUR_WEIGHTS[future_hour]
        hourly_kwh.append(max(0.0, daily_kwh * weight / weight_sum))

    return HourlyConsumptionForecast(
        hourly_kwh=hourly_kwh,
        total_kwh=sum(hourly_kwh),
        horizon_hours=horizon_hours,
        source="seasonal",
        fallback_used=True,
    )


# ---------------------------------------------------------------------------
# ConsumptionForecaster
# ---------------------------------------------------------------------------


class ConsumptionForecaster:
    """ML-based household consumption forecaster backed by HA statistics.

    Parameters
    ----------
    reader:
        :class:`~backend.ha_statistics_reader.HaStatisticsReader` instance
        providing async hourly timeseries data from the HA SQLite DB.
    config:
        :class:`~backend.config.HaStatisticsConfig` with entity IDs and
        training thresholds.
    model_store:
        Optional :class:`~backend.model_store.ModelStore` for persisting
        trained models to disk.
    feature_pipeline:
        Optional :class:`~backend.feature_pipeline.FeaturePipeline` for
        centralised raw data extraction.  When provided, replaces inline
        ``self._reader.read_entity_hourly()`` calls in ``train()``.
    weather_client:
        Optional :class:`~backend.weather_client.OpenMeteoClient` for
        fetching real temperature forecasts in ``predict_hourly()``.
    """

    def __init__(
        self,
        reader: "HaStatisticsReader",
        config: "HaStatisticsConfig",
        *,
        model_store: "ModelStore | None" = None,
        feature_pipeline: "FeaturePipeline | None" = None,
        weather_client: "OpenMeteoClient | None" = None,
    ) -> None:
        self._reader = reader
        self._config = config
        self._model_store = model_store
        self._feature_pipeline = feature_pipeline
        self._weather_client = weather_client

        self._heat_pump_model = None  # HistGradientBoostingRegressor or None
        self._dhw_model = None
        self._base_model = None

        self._last_trained_at: Optional[datetime] = None
        self._days_of_history: int = 0
        self._reasoning_text: str = "ML forecast: not yet trained"

        # Cached training data size for cold-start guard
        self._total_samples: int = 0

        # Last ML prediction memory -- populated on ML success path only
        self._last_prediction_kwh: float | None = None
        self._last_prediction_date: date | None = None

        # Last known outdoor temp from training data (fallback for predict)
        self._last_outdoor_temp: float = 10.0

        # MAPE tracking
        self._mape_path: Path | None = None
        if model_store is not None:
            try:
                self._mape_path = Path(model_store._dir) / "mape_history.json"
            except Exception:  # noqa: BLE001
                self._mape_path = None
        self._last_hourly_predictions: list[float] | None = None

        # Attempt to restore previously trained models from disk
        self._try_load_models()

    def _try_load_models(self) -> bool:
        """Try to load persisted models from ModelStore.

        Returns ``True`` if at least the heat_pump model was restored.
        Discards models that were trained with a different feature count
        (e.g. old 5-feature models before the upgrade to 8 features).
        """
        if self._model_store is None:
            return False
        try:
            hp_result = self._model_store.load("heat_pump")
            if hp_result is not None:
                model, meta = hp_result
                if len(meta.feature_names) != len(FEATURE_NAMES):
                    logger.warning(
                        "Discarding heat_pump model: feature count %d"
                        " != expected %d",
                        len(meta.feature_names),
                        len(FEATURE_NAMES),
                    )
                    hp_result = None
                else:
                    self._heat_pump_model = model
                    logger.info(
                        "Restored heat_pump model from ModelStore"
                    )

            dhw_result = self._model_store.load("dhw")
            if dhw_result is not None:
                model, meta = dhw_result
                if len(meta.feature_names) != len(FEATURE_NAMES):
                    logger.warning(
                        "Discarding dhw model: feature count %d"
                        " != expected %d",
                        len(meta.feature_names),
                        len(FEATURE_NAMES),
                    )
                    dhw_result = None
                else:
                    self._dhw_model = model
                    logger.info("Restored dhw model from ModelStore")

            base_result = self._model_store.load("base_load")
            if base_result is not None:
                model, meta = base_result
                if len(meta.feature_names) != len(FEATURE_NAMES):
                    logger.warning(
                        "Discarding base_load model: feature count %d"
                        " != expected %d",
                        len(meta.feature_names),
                        len(FEATURE_NAMES),
                    )
                    base_result = None
                else:
                    self._base_model = model
                    logger.info(
                        "Restored base_load model from ModelStore"
                    )

            return hp_result is not None
        except Exception as exc:  # noqa: BLE001
            logger.warning("ModelStore load failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Public interface -- matches InfluxMetricsReader protocol
    # ------------------------------------------------------------------

    @property
    def reasoning_text(self) -> str:
        """Last per-load breakdown string from the most recent predict call."""
        return self._reasoning_text

    async def train(self) -> None:
        """Read data and retrain all three HistGBR models.

        Uses FeaturePipeline for raw data extraction when available,
        otherwise falls back to direct reader access.  Applies recency
        weighting and time-series cross-validation before final fit.
        """
        try:
            from sklearn.ensemble import (  # noqa: PLC0415
                HistGradientBoostingRegressor,
            )
            from sklearn.metrics import mean_squared_error  # noqa: PLC0415
            from sklearn.model_selection import (  # noqa: PLC0415
                TimeSeriesSplit,
                cross_val_score,
            )
        except ImportError as exc:
            logger.warning(
                "ConsumptionForecaster: scikit-learn not available -- "
                "falling back to seasonal constant: %s",
                exc,
            )
            return

        min_samples = self._config.min_training_days * 24

        # ------------------------------------------------------------------
        # 1. Fetch data (FeaturePipeline or direct reader fallback)
        # ------------------------------------------------------------------
        if self._feature_pipeline is not None:
            feature_set = await self._feature_pipeline.extract(
                force_refresh=True, days=90
            )
            if feature_set is None:
                logger.warning(
                    "FeaturePipeline returned None -- no data sources"
                )
                return
            temp_data = feature_set.outdoor_temp
            hp_data = feature_set.heat_pump
            dhw_data = feature_set.dhw
        else:
            temp_data = await self._reader.read_entity_hourly(
                self._config.outdoor_temp_entity, days=90
            )
            hp_data = await self._reader.read_entity_hourly(
                self._config.heat_pump_entity, days=90
            )
            dhw_data = await self._reader.read_entity_hourly(
                self._config.dhw_entity, days=90
            )

        # ------------------------------------------------------------------
        # 2. Align timestamps -- inner-join on hour-truncated UTC timestamp
        # ------------------------------------------------------------------
        def _to_map(
            series: list[tuple[datetime, float]],
        ) -> dict[datetime, float]:
            """Map each row to its hour-truncated UTC timestamp."""
            result: dict[datetime, float] = {}
            for ts, val in series:
                key = ts.replace(minute=0, second=0, microsecond=0)
                result[key] = val
            return result

        temp_map = _to_map(temp_data)
        hp_map = _to_map(hp_data)
        dhw_map = _to_map(dhw_data)

        # Common timestamps where we have at least outdoor_temp + heat_pump
        common_ts = sorted(set(temp_map) & set(hp_map))

        self._total_samples = len(common_ts)
        days_seen = len({ts.date() for ts in common_ts})
        self._days_of_history = days_seen

        if len(common_ts) < min_samples:
            logger.warning(
                "ConsumptionForecaster: cold-start fallback"
                " (days_of_history=%d < min_training_days=%d)",
                days_seen,
                self._config.min_training_days,
            )
            return

        # ------------------------------------------------------------------
        # 3. Build feature matrix on common timestamps
        # ------------------------------------------------------------------
        timestamps = common_ts
        outdoor_temps = [temp_map[ts] for ts in timestamps]
        ewm_temps = _compute_ewm(outdoor_temps)

        # Store last outdoor temp for predict fallback
        if outdoor_temps:
            self._last_outdoor_temp = outdoor_temps[-1]

        # Consumption map for lag features (heat pump as proxy)
        consumption_map = hp_map

        X = _build_features(
            timestamps, outdoor_temps, ewm_temps, consumption_map
        )

        # Recency weights
        weights = _compute_recency_weights(timestamps)

        # ------------------------------------------------------------------
        # 4. Train heat pump model
        # ------------------------------------------------------------------
        y_hp = [hp_map[ts] for ts in timestamps]

        def _cv_and_fit_hp():
            hp_model = HistGradientBoostingRegressor(
                max_iter=100,
                max_depth=3,
                random_state=42,
                early_stopping=True,
                n_iter_no_change=10,
                validation_fraction=0.1,
            )
            scores = cross_val_score(
                hp_model,
                X,
                y_hp,
                cv=TimeSeriesSplit(n_splits=5),
                scoring="neg_mean_absolute_percentage_error",
                params={"sample_weight": weights},
            )
            logger.info(
                "CV scores heat_pump (neg_MAPE): mean=%.1f%% std=%.1f%%",
                -scores.mean() * 100,
                scores.std() * 100,
            )
            hp_model.fit(X, y_hp, sample_weight=weights)
            return hp_model

        hp_model = await anyio.to_thread.run_sync(_cv_and_fit_hp)
        hp_preds = hp_model.predict(X)
        hp_rmse = math.sqrt(mean_squared_error(y_hp, hp_preds))
        self._heat_pump_model = hp_model
        hp_n = len(y_hp)

        if self._model_store is not None:
            try:
                from backend.model_store import ModelMetadata  # noqa: PLC0415

                self._model_store.save(
                    "heat_pump",
                    hp_model,
                    ModelMetadata(
                        sklearn_version=sklearn.__version__,
                        numpy_version=numpy.__version__,
                        trained_at=datetime.now(
                            tz=timezone.utc
                        ).isoformat(),
                        sample_count=hp_n,
                        feature_names=FEATURE_NAMES,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ModelStore save failed for heat_pump: %s", exc
                )

        # ------------------------------------------------------------------
        # 5. Train DHW model (optional -- skip if entity absent)
        # ------------------------------------------------------------------
        dhw_n = 0
        dhw_rmse = float("nan")
        common_dhw = sorted(set(temp_map) & set(dhw_map))
        if len(common_dhw) >= min_samples:
            ts_dhw = common_dhw
            ot_dhw = [temp_map[ts] for ts in ts_dhw]
            ewm_dhw = _compute_ewm(ot_dhw)
            X_dhw = _build_features(
                ts_dhw, ot_dhw, ewm_dhw, consumption_map
            )
            y_dhw = [dhw_map[ts] for ts in ts_dhw]
            dhw_weights = _compute_recency_weights(ts_dhw)

            def _cv_and_fit_dhw():
                dhw_model = HistGradientBoostingRegressor(
                    max_iter=100,
                    max_depth=3,
                    random_state=42,
                    early_stopping=True,
                    n_iter_no_change=10,
                    validation_fraction=0.1,
                )
                scores = cross_val_score(
                    dhw_model,
                    X_dhw,
                    y_dhw,
                    cv=TimeSeriesSplit(n_splits=5),
                    scoring="neg_mean_absolute_percentage_error",
                    params={"sample_weight": dhw_weights},
                )
                logger.info(
                    "CV scores dhw (neg_MAPE): mean=%.1f%% std=%.1f%%",
                    -scores.mean() * 100,
                    scores.std() * 100,
                )
                dhw_model.fit(X_dhw, y_dhw, sample_weight=dhw_weights)
                return dhw_model

            dhw_model = await anyio.to_thread.run_sync(_cv_and_fit_dhw)
            dhw_preds = dhw_model.predict(X_dhw)
            dhw_rmse = math.sqrt(mean_squared_error(y_dhw, dhw_preds))
            self._dhw_model = dhw_model
            dhw_n = len(y_dhw)

            if self._model_store is not None:
                try:
                    from backend.model_store import (  # noqa: PLC0415
                        ModelMetadata,
                    )

                    self._model_store.save(
                        "dhw",
                        dhw_model,
                        ModelMetadata(
                            sklearn_version=sklearn.__version__,
                            numpy_version=numpy.__version__,
                            trained_at=datetime.now(
                                tz=timezone.utc
                            ).isoformat(),
                            sample_count=dhw_n,
                            feature_names=FEATURE_NAMES,
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "ModelStore save failed for dhw: %s", exc
                    )
        else:
            self._dhw_model = None

        # ------------------------------------------------------------------
        # 6. Train base load model (constant 300 W placeholder)
        # ------------------------------------------------------------------
        y_base = [_BASE_LOAD_W] * len(timestamps)

        def _cv_and_fit_base():
            base_model = HistGradientBoostingRegressor(
                max_iter=100,
                max_depth=3,
                random_state=42,
                early_stopping=True,
                n_iter_no_change=10,
                validation_fraction=0.1,
            )
            scores = cross_val_score(
                base_model,
                X,
                y_base,
                cv=TimeSeriesSplit(n_splits=5),
                scoring="neg_mean_absolute_percentage_error",
                params={"sample_weight": weights},
            )
            logger.info(
                "CV scores base_load (neg_MAPE): mean=%.1f%% std=%.1f%%",
                -scores.mean() * 100,
                scores.std() * 100,
            )
            base_model.fit(X, y_base, sample_weight=weights)
            return base_model

        base_model = await anyio.to_thread.run_sync(_cv_and_fit_base)
        base_preds = base_model.predict(X)
        base_rmse = math.sqrt(mean_squared_error(y_base, base_preds))
        self._base_model = base_model
        base_n = len(y_base)

        if self._model_store is not None:
            try:
                from backend.model_store import ModelMetadata  # noqa: PLC0415

                self._model_store.save(
                    "base_load",
                    base_model,
                    ModelMetadata(
                        sklearn_version=sklearn.__version__,
                        numpy_version=numpy.__version__,
                        trained_at=datetime.now(
                            tz=timezone.utc
                        ).isoformat(),
                        sample_count=base_n,
                        feature_names=FEATURE_NAMES,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ModelStore save failed for base_load: %s", exc
                )

        self._last_trained_at = datetime.now(tz=timezone.utc)

        logger.info(
            "ConsumptionForecaster: trained heat_pump model on %d samples"
            " (RMSE=%.1f W), dhw on %d samples (RMSE=%.1f W),"
            " base on %d samples (RMSE=%.1f W)",
            hp_n,
            hp_rmse,
            dhw_n,
            dhw_rmse,
            base_n,
            base_rmse,
        )

    async def query_consumption_history(self) -> ConsumptionForecast:
        """Return a ``ConsumptionForecast`` using the trained ML models.

        When models are untrained or insufficient data exists, falls back to
        ``_seasonal_fallback_kwh`` with ``fallback_used=True``.

        Returns
        -------
        ConsumptionForecast
            Always returns a result -- never raises.
        """
        today = date.today()
        min_samples = self._config.min_training_days * 24

        # Cold-start guard
        if (
            self._heat_pump_model is None
            or self._total_samples < min_samples
        ):
            logger.warning(
                "ConsumptionForecaster: cold-start fallback"
                " (days_of_history=%d < min_training_days=%d)",
                self._days_of_history,
                self._config.min_training_days,
            )
            self._reasoning_text = "ML forecast: fallback_used=True"
            return ConsumptionForecast(
                kwh_by_weekday={},
                today_expected_kwh=_seasonal_fallback_kwh(today),
                days_of_history=self._days_of_history,
                fallback_used=True,
            )

        # ------------------------------------------------------------------
        # Predict next 24 hours
        # ------------------------------------------------------------------
        # Use real temperature forecast if available
        temps = await self._get_temperature_forecast(24)
        ewm_temps = _compute_ewm(temps)

        hp_total_w = 0.0
        dhw_total_w = 0.0
        base_total_w = 0.0

        now_utc = datetime.now(tz=timezone.utc)
        for h in range(24):
            ts = now_utc + timedelta(hours=h)
            features = [[
                temps[h],
                ewm_temps[h],
                float(ts.weekday()),
                float(ts.hour),
                float(ts.month),
                1.0 if ts.weekday() >= 5 else 0.0,
                float("nan"),  # lag_24h not available for future
                float("nan"),  # lag_168h not available for future
            ]]
            hp_pred = float(self._heat_pump_model.predict(features)[0])
            hp_total_w += max(0.0, hp_pred)

            if self._dhw_model is not None:
                dhw_pred = float(self._dhw_model.predict(features)[0])
                dhw_total_w += max(0.0, dhw_pred)

            if self._base_model is not None:
                base_pred = float(self._base_model.predict(features)[0])
                base_total_w += max(0.0, base_pred)
            else:
                base_total_w += _BASE_LOAD_W

        # Convert Wh -> kWh (each hour contributes 1 h)
        hp_kwh = hp_total_w / 1000.0
        dhw_kwh = dhw_total_w / 1000.0
        base_kwh = base_total_w / 1000.0
        total_kwh = hp_kwh + dhw_kwh + base_kwh

        self._reasoning_text = (
            f"ML forecast: heat_pump={hp_kwh:.1f} kWh,"
            f" dhw={dhw_kwh:.1f} kWh,"
            f" base={base_kwh:.1f} kWh"
        )

        logger.info(
            "ConsumptionForecaster: ML forecast heat_pump=%.1f dhw=%.1f"
            " base=%.1f total=%.1f kWh (days_of_history=%d)",
            hp_kwh,
            dhw_kwh,
            base_kwh,
            total_kwh,
            self._days_of_history,
        )

        # Remember this prediction for get_forecast_comparison()
        self._last_prediction_kwh = total_kwh
        self._last_prediction_date = datetime.now(tz=timezone.utc).date()

        return ConsumptionForecast(
            kwh_by_weekday={},
            today_expected_kwh=total_kwh,
            days_of_history=self._days_of_history,
            fallback_used=False,
        )

    async def predict_hourly(
        self, horizon_hours: int = 72
    ) -> HourlyConsumptionForecast:
        """Predict per-hour consumption for the next *horizon_hours* hours.

        Uses the trained ML models (heat pump, DHW, base load) when available,
        otherwise falls back to ``_seasonal_hourly_fallback()``.

        Parameters
        ----------
        horizon_hours:
            Number of hours to predict (default 72 for 3-day horizon).

        Returns
        -------
        HourlyConsumptionForecast
            Always returns a result -- never raises.
        """
        min_samples = self._config.min_training_days * 24

        # Cold-start guard
        if (
            self._heat_pump_model is None
            or self._total_samples < min_samples
        ):
            logger.warning(
                "ConsumptionForecaster: predict_hourly cold-start fallback"
                " (days_of_history=%d < min_training_days=%d)",
                self._days_of_history,
                self._config.min_training_days,
            )
            return _seasonal_hourly_fallback(horizon_hours)

        # ML prediction path -- get real temperature forecast
        temps = await self._get_temperature_forecast(horizon_hours)
        ewm_temps = _compute_ewm(temps)

        now_utc = datetime.now(tz=timezone.utc)
        hourly_kwh: list[float] = []

        for h in range(horizon_hours):
            ts = now_utc + timedelta(hours=h)
            features = [[
                temps[h],
                ewm_temps[h],
                float(ts.weekday()),
                float(ts.hour),
                float(ts.month),
                1.0 if ts.weekday() >= 5 else 0.0,
                float("nan"),  # lag_24h not available for future
                float("nan"),  # lag_168h not available for future
            ]]

            hp_pred = max(
                0.0, float(self._heat_pump_model.predict(features)[0])
            )

            dhw_pred = 0.0
            if self._dhw_model is not None:
                dhw_pred = max(
                    0.0, float(self._dhw_model.predict(features)[0])
                )

            if self._base_model is not None:
                base_pred = max(
                    0.0, float(self._base_model.predict(features)[0])
                )
            else:
                base_pred = _BASE_LOAD_W

            hourly_kwh.append(
                max(0.0, (hp_pred + dhw_pred + base_pred) / 1000.0)
            )

        # Store first 24 hours for daily MAPE comparison
        self._last_hourly_predictions = list(hourly_kwh[:24])

        total_kwh = sum(hourly_kwh)

        logger.info(
            "ConsumptionForecaster: predict_hourly total=%.1f kWh"
            " horizon=%d hours (days_of_history=%d)",
            total_kwh,
            horizon_hours,
            self._days_of_history,
        )

        return HourlyConsumptionForecast(
            hourly_kwh=hourly_kwh,
            total_kwh=total_kwh,
            horizon_hours=horizon_hours,
            source="ml",
            fallback_used=False,
        )

    async def retrain_if_stale(self, stale_hours: int = 24) -> None:
        """Retrain models if they are ``None`` or older than *stale_hours*.

        Before retraining, computes MAPE for yesterday's predictions vs actual
        hourly consumption if predictions are available.

        Parameters
        ----------
        stale_hours:
            Maximum age of the trained models in hours before a retrain is
            triggered (default 24).
        """
        # Compute MAPE before retraining (fire-and-forget on error)
        if (
            self._last_hourly_predictions is not None
            and self._mape_path is not None
        ):
            try:
                actual_data = await self._reader.read_entity_hourly(
                    self._config.heat_pump_entity, days=2
                )
                if actual_data:
                    # Extract yesterday's 24 hours
                    yesterday = (
                        datetime.now(tz=timezone.utc) - timedelta(days=1)
                    ).date()
                    actual_yesterday = [
                        val
                        for ts, val in actual_data
                        if ts.date() == yesterday
                    ]
                    if len(actual_yesterday) >= 12:
                        mape_value = _compute_daily_mape(
                            self._last_hourly_predictions[
                                : len(actual_yesterday)
                            ],
                            actual_yesterday,
                        )
                        if mape_value is not None:
                            date_str = yesterday.isoformat()
                            _save_mape_history(
                                self._mape_path, date_str, mape_value
                            )
                            logger.info(
                                "Daily MAPE: %.1f%% (date=%s)",
                                mape_value,
                                date_str,
                            )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MAPE computation failed (non-fatal): %s", exc
                )
            self._last_hourly_predictions = None

        if self._heat_pump_model is None:
            await self.train()
            return

        if self._last_trained_at is None:
            await self.train()
            return

        age = datetime.now(tz=timezone.utc) - self._last_trained_at
        if age.total_seconds() > stale_hours * 3600:
            logger.info(
                "ConsumptionForecaster: models are %.1f h old"
                " (> %d h) -- retraining",
                age.total_seconds() / 3600,
                stale_hours,
            )
            await self.train()

    def get_forecast_comparison(
        self, actual_kwh: float
    ) -> dict[str, float] | None:
        """Compare the last ML prediction to an actual consumption value.

        Returns ``None`` if no ML prediction has been made yet (cold-start or
        fallback-only path).

        Parameters
        ----------
        actual_kwh:
            Measured / InfluxDB-derived actual consumption in kWh.

        Returns
        -------
        dict or None
            ``{"predicted_kwh": float, "actual_kwh": float, "error_pct": float}``
            when a prediction is available, else ``None``.
        """
        if self._last_prediction_kwh is None:
            return None
        error_pct = (
            abs(self._last_prediction_kwh - actual_kwh)
            / max(actual_kwh, 0.001)
            * 100
        )
        return {
            "predicted_kwh": round(self._last_prediction_kwh, 2),
            "actual_kwh": round(actual_kwh, 2),
            "error_pct": round(error_pct, 1),
        }

    def get_ml_status(self) -> dict:
        """Return ML model status, training info, and MAPE history.

        Returns
        -------
        dict
            Contains ``models`` (per-model info), ``mape`` (history and
            current value), ``days_of_history``, and ``min_training_days``.
        """
        models: dict[str, dict] = {}
        model_names = [
            ("heat_pump", self._heat_pump_model),
            ("dhw", self._dhw_model),
            ("base_load", self._base_model),
        ]
        for name, model in model_names:
            info: dict = {
                "trained": model is not None,
                "last_trained_at": None,
                "sample_count": 0,
                "feature_names": FEATURE_NAMES,
                "sklearn_version": sklearn.__version__,
            }
            if (
                model is not None
                and self._model_store is not None
            ):
                try:
                    result = self._model_store.load(name)
                    if result is not None:
                        _, meta = result
                        info["last_trained_at"] = meta.trained_at
                        info["sample_count"] = meta.sample_count
                        info["sklearn_version"] = meta.sklearn_version
                except Exception:  # noqa: BLE001
                    pass
            elif (
                model is not None
                and self._last_trained_at is not None
            ):
                info["last_trained_at"] = self._last_trained_at.isoformat()
                info["sample_count"] = self._total_samples
            models[name] = info

        # MAPE history
        mape_history: list[dict] = []
        current_mape: float | None = None
        if self._mape_path is not None:
            mape_history = _load_mape_history(self._mape_path)
            if mape_history:
                current_mape = mape_history[-1].get("mape")

        return {
            "models": models,
            "mape": {
                "current": current_mape,
                "history": mape_history,
                "days_tracked": len(mape_history),
            },
            "days_of_history": self._days_of_history,
            "min_training_days": self._config.min_training_days,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_temperature_forecast(
        self, hours: int
    ) -> list[float]:
        """Get temperature forecast, falling back gracefully.

        Tries weather client first, then falls back to last known outdoor
        temp from training, then to 10.0 C as final fallback.
        """
        if self._weather_client is not None:
            try:
                forecast = (
                    await self._weather_client.get_temperature_forecast(
                        hours=hours
                    )
                )
                if forecast is not None and len(forecast) >= hours:
                    return forecast[:hours]
                if forecast is not None and len(forecast) > 0:
                    # Pad with last value if shorter than needed
                    padded = list(forecast)
                    while len(padded) < hours:
                        padded.append(padded[-1])
                    return padded
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Temperature forecast failed, using fallback: %s",
                    exc,
                )

        # Fallback: constant temperature
        fallback_temp = self._last_outdoor_temp
        return [fallback_temp] * hours
