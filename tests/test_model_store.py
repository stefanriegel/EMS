"""Tests for backend.model_store — ModelStore with joblib + JSON sidecar."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from sklearn.ensemble import GradientBoostingRegressor

from backend.model_store import ModelMetadata, ModelStore


def _train_model() -> GradientBoostingRegressor:
    """Return a tiny fitted GBR for testing."""
    model = GradientBoostingRegressor(n_estimators=5, max_depth=2)
    model.fit([[1], [2], [3]], [1, 2, 3])
    return model


def _make_metadata() -> ModelMetadata:
    import sklearn

    return ModelMetadata(
        sklearn_version=sklearn.__version__,
        numpy_version=np.__version__,
        trained_at="2026-01-01T00:00:00Z",
        sample_count=3,
        feature_names=["x"],
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_save_and_load(tmp_path: Path) -> None:
    """Round-trip: save a model, load it back, predictions match."""
    store = ModelStore(str(tmp_path))
    model = _train_model()
    meta = _make_metadata()

    store.save("consumption", model, meta)
    result = store.load("consumption")

    assert result is not None
    loaded_model, loaded_meta = result
    X = [[1.5], [2.5]]
    np.testing.assert_array_equal(model.predict(X), loaded_model.predict(X))
    assert loaded_meta.sample_count == 3
    assert loaded_meta.feature_names == ["x"]


def test_load_returns_none_when_no_files(tmp_path: Path) -> None:
    """Load non-existent model returns None."""
    store = ModelStore(str(tmp_path))
    assert store.load("nonexistent") is None


def test_version_mismatch_discards_model(tmp_path: Path) -> None:
    """When sklearn version differs, load returns None and files deleted."""
    store = ModelStore(str(tmp_path))
    model = _train_model()
    meta = _make_metadata()
    store.save("consumption", model, meta)

    with patch("backend.model_store.sklearn.__version__", "0.0.0"):
        result = store.load("consumption")

    assert result is None
    assert not (tmp_path / "consumption.joblib").exists()
    assert not (tmp_path / "consumption.meta.json").exists()


def test_corrupt_metadata_discards_model(tmp_path: Path) -> None:
    """Corrupt metadata JSON causes load to return None."""
    store = ModelStore(str(tmp_path))
    model = _train_model()
    meta = _make_metadata()
    store.save("consumption", model, meta)

    (tmp_path / "consumption.meta.json").write_text("NOT VALID JSON {{{")
    assert store.load("consumption") is None


def test_corrupt_model_file_discards_model(tmp_path: Path) -> None:
    """Corrupt joblib file causes load to return None."""
    store = ModelStore(str(tmp_path))
    model = _train_model()
    meta = _make_metadata()
    store.save("consumption", model, meta)

    (tmp_path / "consumption.joblib").write_bytes(b"\x00garbage\xff")
    assert store.load("consumption") is None


def test_metadata_fields(tmp_path: Path) -> None:
    """Saved .meta.json contains all required keys."""
    store = ModelStore(str(tmp_path))
    model = _train_model()
    meta = _make_metadata()
    store.save("consumption", model, meta)

    raw = json.loads((tmp_path / "consumption.meta.json").read_text())
    assert set(raw.keys()) >= {
        "sklearn_version",
        "numpy_version",
        "trained_at",
        "sample_count",
        "feature_names",
    }
    assert isinstance(raw["feature_names"], list)


def test_model_dir_created_on_init(tmp_path: Path) -> None:
    """ModelStore creates directory if it does not exist."""
    new_dir = tmp_path / "nested" / "models"
    assert not new_dir.exists()
    ModelStore(str(new_dir))
    assert new_dir.is_dir()


def test_config_from_env() -> None:
    """ModelStoreConfig.from_env() reads EMS_MODEL_DIR with default."""
    from backend.config import ModelStoreConfig

    # Default
    with patch.dict("os.environ", {}, clear=False):
        cfg = ModelStoreConfig.from_env()
        assert cfg.model_dir == "/config/ems_models"
        assert cfg.enabled is True

    # Custom
    with patch.dict("os.environ", {"EMS_MODEL_DIR": "/tmp/custom_models"}):
        cfg = ModelStoreConfig.from_env()
        assert cfg.model_dir == "/tmp/custom_models"
        assert cfg.enabled is True
