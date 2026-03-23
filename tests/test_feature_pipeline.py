"""Tests for backend.feature_pipeline — FeaturePipeline and FeatureSet."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import HaStatisticsConfig
from backend.feature_pipeline import FeaturePipeline, FeatureSet


def _make_config() -> HaStatisticsConfig:
    return HaStatisticsConfig(
        db_path="/tmp/fake.db",
        outdoor_temp_entity="sensor.temp",
        heat_pump_entity="sensor.hp",
        dhw_entity="sensor.dhw",
    )


def _make_series(hours: int = 24) -> list[tuple[datetime, float]]:
    """Return synthetic time series for testing."""
    return [
        (datetime(2026, 1, 1, h, tzinfo=timezone.utc), float(h))
        for h in range(hours)
    ]


@pytest.mark.anyio
async def test_extract_from_ha_only() -> None:
    """With ha_reader and no influx_reader, extract returns FeatureSet from HA."""
    ha_reader = AsyncMock()
    ha_reader.read_entity_hourly = AsyncMock(return_value=_make_series())

    pipeline = FeaturePipeline(
        ha_reader=ha_reader,
        influx_reader=None,
        config=_make_config(),
    )
    result = await pipeline.extract()

    assert result is not None
    assert isinstance(result, FeatureSet)
    assert len(result.outdoor_temp) == 24
    assert len(result.heat_pump) == 24
    assert result.source == "ha_statistics"
    # ha_reader.read_entity_hourly called 3 times (temp, hp, dhw)
    assert ha_reader.read_entity_hourly.call_count == 3


@pytest.mark.anyio
async def test_extract_caches_results() -> None:
    """Two calls within 1 hour produce the same object; ha_reader called once."""
    ha_reader = AsyncMock()
    ha_reader.read_entity_hourly = AsyncMock(return_value=_make_series())

    pipeline = FeaturePipeline(
        ha_reader=ha_reader,
        influx_reader=None,
        config=_make_config(),
    )
    first = await pipeline.extract()
    second = await pipeline.extract()

    assert first is second
    # ha_reader called only during the first extract
    assert ha_reader.read_entity_hourly.call_count == 3


@pytest.mark.anyio
async def test_cache_expired_refetches() -> None:
    """After patching cache timestamp to 2 hours ago, extract refetches."""
    ha_reader = AsyncMock()
    ha_reader.read_entity_hourly = AsyncMock(return_value=_make_series())

    pipeline = FeaturePipeline(
        ha_reader=ha_reader,
        influx_reader=None,
        config=_make_config(),
    )
    first = await pipeline.extract()
    assert ha_reader.read_entity_hourly.call_count == 3

    # Patch cache timestamp to 2 hours ago
    from datetime import timedelta

    pipeline._cache_timestamp = datetime.now(tz=timezone.utc) - timedelta(hours=2)

    second = await pipeline.extract()
    # Should have re-fetched (3 more calls = 6 total)
    assert ha_reader.read_entity_hourly.call_count == 6
    assert second is not first


@pytest.mark.anyio
async def test_force_refresh_bypasses_cache() -> None:
    """extract(force_refresh=True) calls ha_reader even when cache is valid."""
    ha_reader = AsyncMock()
    ha_reader.read_entity_hourly = AsyncMock(return_value=_make_series())

    pipeline = FeaturePipeline(
        ha_reader=ha_reader,
        influx_reader=None,
        config=_make_config(),
    )
    await pipeline.extract()
    assert ha_reader.read_entity_hourly.call_count == 3

    await pipeline.extract(force_refresh=True)
    assert ha_reader.read_entity_hourly.call_count == 6


@pytest.mark.anyio
async def test_influx_unavailable_falls_back_to_ha() -> None:
    """With influx_reader that raises, extract still returns FeatureSet from HA."""
    ha_reader = AsyncMock()
    ha_reader.read_entity_hourly = AsyncMock(return_value=_make_series())

    influx_reader = AsyncMock()
    influx_reader.query_range = AsyncMock(side_effect=RuntimeError("connection refused"))

    pipeline = FeaturePipeline(
        ha_reader=ha_reader,
        influx_reader=influx_reader,
        config=_make_config(),
    )
    result = await pipeline.extract()

    assert result is not None
    assert isinstance(result, FeatureSet)
    assert len(result.outdoor_temp) == 24
    # Source should still be ha_statistics (influx failed)
    assert result.source in ("ha_statistics", "both")


@pytest.mark.anyio
async def test_ha_reader_none_returns_none() -> None:
    """With ha_reader=None and influx_reader=None, extract returns None."""
    pipeline = FeaturePipeline(
        ha_reader=None,
        influx_reader=None,
        config=_make_config(),
    )
    result = await pipeline.extract()
    assert result is None


@pytest.mark.anyio
async def test_feature_set_has_expected_fields() -> None:
    """FeatureSet has outdoor_temp, heat_pump, dhw, timestamps fields."""
    fs = FeatureSet()
    assert hasattr(fs, "outdoor_temp")
    assert hasattr(fs, "heat_pump")
    assert hasattr(fs, "dhw")
    assert hasattr(fs, "timestamps")
    assert isinstance(fs.outdoor_temp, list)
    assert isinstance(fs.heat_pump, list)
    assert isinstance(fs.dhw, list)
    assert isinstance(fs.timestamps, list)
    assert fs.source == "ha_statistics"
