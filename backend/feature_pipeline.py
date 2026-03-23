"""FeaturePipeline — centralised feature extraction for ML models.

Reads training features from HA statistics (primary) and optionally InfluxDB
(supplementary), caching results for 1 hour so multiple models in the nightly
batch share a single I/O round.

Each series is a list of ``(datetime_utc, value)`` tuples sorted ascending by
time.  The pipeline reads outdoor temperature, heat pump power, and DHW power
entities.

Observability
-------------
- Logger name: ``ems.feature_pipeline``
- WARNING ``"no data sources configured — returning None"`` — both readers are
  ``None``.
- WARNING ``"HA read failed for <entity>: <exc>"`` — single entity read error;
  the pipeline continues with an empty series for that entity.
- WARNING ``"InfluxDB augmentation failed: <exc>"`` — InfluxDB unavailable;
  features are built from HA data alone.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.config import HaStatisticsConfig
    from backend.ha_statistics_reader import HaStatisticsReader
    from backend.influx_reader import InfluxMetricsReader

logger = logging.getLogger("ems.feature_pipeline")


@dataclass
class FeatureSet:
    """Training features extracted from data sources.

    Each series is a list of (datetime_utc, value) tuples sorted by time.
    """

    outdoor_temp: list[tuple[datetime, float]] = field(default_factory=list)
    heat_pump: list[tuple[datetime, float]] = field(default_factory=list)
    dhw: list[tuple[datetime, float]] = field(default_factory=list)
    timestamps: list[datetime] = field(default_factory=list)
    source: str = "ha_statistics"  # "ha_statistics", "influx", "both"


class FeaturePipeline:
    """Centralised feature extraction with 1-hour cache.

    Parameters
    ----------
    ha_reader:
        Optional ``HaStatisticsReader`` for HA SQLite statistics.
    influx_reader:
        Optional ``InfluxMetricsReader`` for InfluxDB time-series.
    config:
        ``HaStatisticsConfig`` with entity IDs and training parameters.
    """

    def __init__(
        self,
        ha_reader: HaStatisticsReader | None,
        influx_reader: InfluxMetricsReader | None,
        config: HaStatisticsConfig,
    ) -> None:
        self._ha_reader = ha_reader
        self._influx_reader = influx_reader
        self._config = config
        self._cache: FeatureSet | None = None
        self._cache_timestamp: datetime | None = None
        self._cache_ttl_s: float = 3600.0  # 1 hour

    async def extract(
        self,
        *,
        force_refresh: bool = False,
        days: int = 90,
    ) -> FeatureSet | None:
        """Extract training features, returning cached result if still valid.

        Parameters
        ----------
        force_refresh:
            Bypass cache and re-read from data sources.
        days:
            Rolling window in calendar days for HA statistics queries.

        Returns
        -------
        FeatureSet | None
            Extracted features, or ``None`` when no data sources are available.
        """
        # Check cache
        if (
            not force_refresh
            and self._cache is not None
            and self._cache_timestamp is not None
        ):
            age_s = (
                datetime.now(tz=timezone.utc) - self._cache_timestamp
            ).total_seconds()
            if age_s < self._cache_ttl_s:
                return self._cache

        # No data sources at all
        if self._ha_reader is None and self._influx_reader is None:
            logger.warning("no data sources configured — returning None")
            return None

        # Read from HA statistics
        outdoor_temp: list[tuple[datetime, float]] = []
        heat_pump: list[tuple[datetime, float]] = []
        dhw: list[tuple[datetime, float]] = []

        if self._ha_reader is not None:
            outdoor_temp = await self._read_entity(
                self._config.outdoor_temp_entity, days
            )
            heat_pump = await self._read_entity(
                self._config.heat_pump_entity, days
            )
            if self._config.dhw_entity:
                dhw = await self._read_entity(
                    self._config.dhw_entity, days
                )

        source = "ha_statistics"

        # Try to augment with InfluxDB data
        if self._influx_reader is not None:
            try:
                start = f"-{days}d"
                _influx_data = await self._influx_reader.query_range(
                    "ems_system", start, "now()"
                )
                if _influx_data:
                    source = "both"
            except Exception as exc:  # noqa: BLE001
                logger.warning("InfluxDB augmentation failed: %s", exc)

        # Build timestamps from all series
        all_times: set[datetime] = set()
        for series in (outdoor_temp, heat_pump, dhw):
            for ts, _ in series:
                all_times.add(ts)
        timestamps = sorted(all_times)

        feature_set = FeatureSet(
            outdoor_temp=outdoor_temp,
            heat_pump=heat_pump,
            dhw=dhw,
            timestamps=timestamps,
            source=source,
        )

        # Update cache
        self._cache = feature_set
        self._cache_timestamp = datetime.now(tz=timezone.utc)

        return feature_set

    def invalidate_cache(self) -> None:
        """Clear cached features, forcing a fresh read on next extract()."""
        self._cache = None
        self._cache_timestamp = None

    async def _read_entity(
        self,
        entity_id: str,
        days: int,
    ) -> list[tuple[datetime, float]]:
        """Read a single entity from HA statistics, returning [] on error."""
        assert self._ha_reader is not None  # caller guards this
        try:
            return await self._ha_reader.read_entity_hourly(
                entity_id, days=days
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("HA read failed for %s: %s", entity_id, exc)
            return []
