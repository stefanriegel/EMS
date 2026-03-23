"""Persistent storage for trained sklearn models.

Uses joblib for model serialisation and a JSON sidecar for metadata
(sklearn version, numpy version, training timestamp, sample count,
feature names).  On load, the sklearn version is compared -- a mismatch
causes the stale artefact to be silently discarded so the caller
retrains from scratch.

Observability
-------------
* **WARNING** ``model-version-mismatch``  -- sklearn version changed,
  model discarded.
* **WARNING** ``model-load-error``        -- corrupt file or unexpected
  error, model discarded.
* **INFO**    ``model-saved``             -- model written to disk.
* **INFO**    ``model-loaded``            -- model loaded successfully.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import sklearn

logger = logging.getLogger(__name__)


@dataclass
class ModelMetadata:
    """Sidecar metadata persisted alongside a joblib model file."""

    sklearn_version: str
    numpy_version: str
    trained_at: str
    sample_count: int
    feature_names: list[str]


class ModelStore:
    """Save and load sklearn models with version-aware JSON sidecars.

    Parameters
    ----------
    model_dir:
        Filesystem directory for model artefacts.  Created if absent.
    """

    def __init__(self, model_dir: str) -> None:
        self._dir = Path(model_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ---- public API ------------------------------------------------

    def save(self, name: str, model: Any, metadata: ModelMetadata) -> None:
        """Persist *model* and *metadata* under *name*."""
        model_path = self._dir / f"{name}.joblib"
        meta_path = self._dir / f"{name}.meta.json"

        joblib.dump(model, model_path)
        meta_path.write_text(
            json.dumps(dataclasses.asdict(metadata), indent=2)
        )
        logger.info(
            "model-saved  name=%s sklearn=%s samples=%d",
            name,
            metadata.sklearn_version,
            metadata.sample_count,
        )

    def load(self, name: str) -> tuple[Any, ModelMetadata] | None:
        """Load a previously saved model, or ``None`` on any failure.

        Returns ``None`` (and deletes stale files) when:
        * the model or metadata file is missing,
        * the metadata JSON is corrupt,
        * the sklearn version recorded in metadata differs from the
          running version, or
        * joblib cannot deserialise the model file.
        """
        model_path = self._dir / f"{name}.joblib"
        meta_path = self._dir / f"{name}.meta.json"

        if not model_path.exists() or not meta_path.exists():
            return None

        try:
            raw = json.loads(meta_path.read_text())
            meta = ModelMetadata(**raw)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning(
                "model-load-error  name=%s reason=corrupt-metadata err=%s",
                name,
                exc,
            )
            self._remove(name)
            return None

        if meta.sklearn_version != sklearn.__version__:
            logger.warning(
                "model-version-mismatch  name=%s stored=%s running=%s",
                name,
                meta.sklearn_version,
                sklearn.__version__,
            )
            self._remove(name)
            return None

        try:
            model = joblib.load(model_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "model-load-error  name=%s reason=corrupt-model err=%s",
                name,
                exc,
            )
            self._remove(name)
            return None

        logger.info("model-loaded  name=%s sklearn=%s", name, meta.sklearn_version)
        return model, meta

    # ---- internal --------------------------------------------------

    def _remove(self, name: str) -> None:
        """Delete model and metadata files (ignore if already gone)."""
        (self._dir / f"{name}.joblib").unlink(missing_ok=True)
        (self._dir / f"{name}.meta.json").unlink(missing_ok=True)
