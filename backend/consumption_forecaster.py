"""ConsumptionForecaster — ML-based household consumption forecast.

Trains three ``GradientBoostingRegressor`` models (heat pump, DHW, base load)
on Home Assistant long-term statistics and predicts next-24h consumption in
kWh, broken down by load type.

The class implements the same ``query_consumption_history()`` protocol as
``InfluxMetricsReader`` so it can be injected into the ``Scheduler`` as a
drop-in replacement without modifying any call sites.

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
  dhw on M samples, base on K samples"`` — after each retrain
- INFO ``"ConsumptionForecaster: ML forecast heat_pump=X.X dhw=X.X
  base=X.X total=Y.Y kWh (days_of_history=N)"`` — on each predict
- WARNING ``"ConsumptionForecaster: cold-start fallback (days_of_history=N
  < min_training_days=14)"`` — insufficient history
- WARNING ``"ConsumptionForecaster: entity <id> not found in HA statistics
  — skipping"`` — emitted by the reader per missing entity
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from functools import partial
from typing import TYPE_CHECKING, Optional

import anyio.to_thread
import numpy
import sklearn

from backend.influx_reader import _seasonal_fallback_kwh
from backend.schedule_models import ConsumptionForecast, HourlyConsumptionForecast

if TYPE_CHECKING:
    from backend.config import HaStatisticsConfig
    from backend.ha_statistics_reader import HaStatisticsReader
    from backend.model_store import ModelMetadata, ModelStore

logger = logging.getLogger("ems.consumption_forecaster")

# Base load placeholder (W) — replaced in S02 when a real consumption entity
# is available in HA statistics.
_BASE_LOAD_W: float = 300.0


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


def _build_features(
    timestamps: list[datetime],
    outdoor_temps: list[float],
    ewm_temps: list[float],
) -> list[list[float]]:
    """Build a feature matrix row for each hourly observation.

    Features: [outdoor_temp_c, ewm_temp_3d, day_of_week, hour_of_day, month]
    """
    rows: list[list[float]] = []
    for ts, ot, ewm in zip(timestamps, outdoor_temps, ewm_temps):
        rows.append([
            ot,
            ewm,
            float(ts.weekday()),
            float(ts.hour),
            float(ts.month),
        ])
    return rows


def _seasonal_hourly_fallback(horizon_hours: int = 72) -> HourlyConsumptionForecast:
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
    # Hour-of-day weights — sum to ~24.0 so daily total is preserved
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
    """

    def __init__(
        self,
        reader: "HaStatisticsReader",
        config: "HaStatisticsConfig",
        *,
        model_store: "ModelStore | None" = None,
    ) -> None:
        self._reader = reader
        self._config = config
        self._model_store = model_store

        self._heat_pump_model = None  # GradientBoostingRegressor or None
        self._dhw_model = None
        self._base_model = None

        self._last_trained_at: Optional[datetime] = None
        self._days_of_history: int = 0
        self._reasoning_text: str = "ML forecast: not yet trained"

        # Cached training data size for cold-start guard
        self._total_samples: int = 0

        # Last ML prediction memory — populated on ML success path only
        self._last_prediction_kwh: float | None = None
        self._last_prediction_date: date | None = None

        # Attempt to restore previously trained models from disk
        self._try_load_models()

    def _try_load_models(self) -> bool:
        """Try to load persisted models from ModelStore.

        Returns ``True`` if at least the heat_pump model was restored.
        """
        if self._model_store is None:
            return False
        try:
            hp_result = self._model_store.load("heat_pump")
            if hp_result is not None:
                self._heat_pump_model, _ = hp_result
                logger.info("Restored heat_pump model from ModelStore")

            dhw_result = self._model_store.load("dhw")
            if dhw_result is not None:
                self._dhw_model, _ = dhw_result
                logger.info("Restored dhw model from ModelStore")

            base_result = self._model_store.load("base_load")
            if base_result is not None:
                self._base_model, _ = base_result
                logger.info("Restored base_load model from ModelStore")

            return hp_result is not None
        except Exception as exc:  # noqa: BLE001
            logger.warning("ModelStore load failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Public interface — matches InfluxMetricsReader protocol
    # ------------------------------------------------------------------

    @property
    def reasoning_text(self) -> str:
        """Last per-load breakdown string from the most recent predict call."""
        return self._reasoning_text

    async def train(self) -> None:
        """Read HA statistics and retrain all three GBR models.

        Reads 90 days of data for outdoor temp, heat pump, and DHW entities.
        A model is only trained when its entity has ≥ ``min_training_days * 24``
        hourly samples.  Models for entities with insufficient data remain
        ``None`` (the forecaster falls back to seasonal constant for those).

        After training, logs per-model sample counts and train RMSE.
        """
        # Import here so the module is importable even without scikit-learn
        # installed (the cold-start fallback path never calls train()).
        try:
            from sklearn.ensemble import GradientBoostingRegressor  # noqa: PLC0415
            from sklearn.metrics import mean_squared_error  # noqa: PLC0415
        except ImportError as exc:
            logger.warning(
                "ConsumptionForecaster: scikit-learn not available — "
                "falling back to seasonal constant: %s",
                exc,
            )
            return

        min_samples = self._config.min_training_days * 24

        # ------------------------------------------------------------------
        # 1. Fetch data
        # ------------------------------------------------------------------
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
        # 2. Align timestamps — inner-join on hour-truncated UTC timestamp
        # ------------------------------------------------------------------
        def _to_map(series: list[tuple[datetime, float]]) -> dict[datetime, float]:
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
        X = _build_features(timestamps, outdoor_temps, ewm_temps)

        # ------------------------------------------------------------------
        # 4. Train heat pump model
        # ------------------------------------------------------------------
        y_hp = [hp_map[ts] for ts in timestamps]
        hp_model = GradientBoostingRegressor(
            n_estimators=100, max_depth=3, random_state=42
        )
        await anyio.to_thread.run_sync(partial(hp_model.fit, X, y_hp))
        hp_preds = hp_model.predict(X)
        hp_rmse = math.sqrt(
            mean_squared_error(y_hp, hp_preds)
        )
        self._heat_pump_model = hp_model
        hp_n = len(y_hp)

        if self._model_store is not None:
            try:
                from backend.model_store import ModelMetadata  # noqa: PLC0415
                self._model_store.save("heat_pump", hp_model, ModelMetadata(
                    sklearn_version=sklearn.__version__,
                    numpy_version=numpy.__version__,
                    trained_at=datetime.now(tz=timezone.utc).isoformat(),
                    sample_count=hp_n,
                    feature_names=["outdoor_temp_c", "ewm_temp_3d", "day_of_week", "hour_of_day", "month"],
                ))
            except Exception as exc:  # noqa: BLE001
                logger.warning("ModelStore save failed for heat_pump: %s", exc)

        # ------------------------------------------------------------------
        # 5. Train DHW model (optional — skip if entity absent)
        # ------------------------------------------------------------------
        dhw_n = 0
        dhw_rmse = float("nan")
        common_dhw = sorted(set(temp_map) & set(dhw_map))
        if len(common_dhw) >= min_samples:
            ts_dhw = common_dhw
            ot_dhw = [temp_map[ts] for ts in ts_dhw]
            ewm_dhw = _compute_ewm(ot_dhw)
            X_dhw = _build_features(ts_dhw, ot_dhw, ewm_dhw)
            y_dhw = [dhw_map[ts] for ts in ts_dhw]
            dhw_model = GradientBoostingRegressor(
                n_estimators=100, max_depth=3, random_state=42
            )
            await anyio.to_thread.run_sync(partial(dhw_model.fit, X_dhw, y_dhw))
            dhw_preds = dhw_model.predict(X_dhw)
            dhw_rmse = math.sqrt(mean_squared_error(y_dhw, dhw_preds))
            self._dhw_model = dhw_model
            dhw_n = len(y_dhw)

            if self._model_store is not None:
                try:
                    from backend.model_store import ModelMetadata  # noqa: PLC0415
                    self._model_store.save("dhw", dhw_model, ModelMetadata(
                        sklearn_version=sklearn.__version__,
                        numpy_version=numpy.__version__,
                        trained_at=datetime.now(tz=timezone.utc).isoformat(),
                        sample_count=dhw_n,
                        feature_names=["outdoor_temp_c", "ewm_temp_3d", "day_of_week", "hour_of_day", "month"],
                    ))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("ModelStore save failed for dhw: %s", exc)
        else:
            self._dhw_model = None

        # ------------------------------------------------------------------
        # 6. Train base load model (constant 300 W placeholder)
        # ------------------------------------------------------------------
        y_base = [_BASE_LOAD_W] * len(timestamps)
        base_model = GradientBoostingRegressor(
            n_estimators=100, max_depth=3, random_state=42
        )
        await anyio.to_thread.run_sync(partial(base_model.fit, X, y_base))
        base_preds = base_model.predict(X)
        base_rmse = math.sqrt(mean_squared_error(y_base, base_preds))
        self._base_model = base_model
        base_n = len(y_base)

        if self._model_store is not None:
            try:
                from backend.model_store import ModelMetadata  # noqa: PLC0415
                self._model_store.save("base_load", base_model, ModelMetadata(
                    sklearn_version=sklearn.__version__,
                    numpy_version=numpy.__version__,
                    trained_at=datetime.now(tz=timezone.utc).isoformat(),
                    sample_count=base_n,
                    feature_names=["outdoor_temp_c", "ewm_temp_3d", "day_of_week", "hour_of_day", "month"],
                ))
            except Exception as exc:  # noqa: BLE001
                logger.warning("ModelStore save failed for base_load: %s", exc)

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
            Always returns a result — never raises.
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
        # We don't have outdoor temp forecast; use a neutral 10 °C placeholder.
        # S02 / future slices can inject a real temp forecast.
        neutral_temp = 10.0
        ewm_placeholder = neutral_temp  # EWM = same neutral value for all hours

        hp_total_w = 0.0
        dhw_total_w = 0.0
        base_total_w = 0.0

        now_utc = datetime.now(tz=timezone.utc)
        for h in range(24):
            ts = now_utc + timedelta(hours=h)
            features = [[
                neutral_temp,
                ewm_placeholder,
                float(ts.weekday()),
                float(ts.hour),
                float(ts.month),
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

        # Convert W·h → kWh (each hour contributes 1 h)
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
            Always returns a result — never raises.
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

        # ML prediction path
        neutral_temp = 10.0
        ewm_placeholder = neutral_temp

        now_utc = datetime.now(tz=timezone.utc)
        hourly_kwh: list[float] = []

        for h in range(horizon_hours):
            ts = now_utc + timedelta(hours=h)
            features = [[
                neutral_temp,
                ewm_placeholder,
                float(ts.weekday()),
                float(ts.hour),
                float(ts.month),
            ]]

            hp_pred = max(0.0, float(self._heat_pump_model.predict(features)[0]))

            dhw_pred = 0.0
            if self._dhw_model is not None:
                dhw_pred = max(0.0, float(self._dhw_model.predict(features)[0]))

            if self._base_model is not None:
                base_pred = max(0.0, float(self._base_model.predict(features)[0]))
            else:
                base_pred = _BASE_LOAD_W

            hourly_kwh.append(max(0.0, (hp_pred + dhw_pred + base_pred) / 1000.0))

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

        Parameters
        ----------
        stale_hours:
            Maximum age of the trained models in hours before a retrain is
            triggered (default 24).
        """
        if self._heat_pump_model is None:
            await self.train()
            return

        if self._last_trained_at is None:
            await self.train()
            return

        age = datetime.now(tz=timezone.utc) - self._last_trained_at
        if age.total_seconds() > stale_hours * 3600:
            logger.info(
                "ConsumptionForecaster: models are %.1f h old (> %d h) — retraining",
                age.total_seconds() / 3600,
                stale_hours,
            )
            await self.train()

    def get_forecast_comparison(self, actual_kwh: float) -> dict[str, float] | None:
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
