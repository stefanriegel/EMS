"""Production commissioning state machine.

Manages the staged rollout of write access to battery controllers:

- **READ_ONLY** — both batteries are read-only, no setpoints written.
- **SINGLE_BATTERY** — Victron writes enabled, Huawei writes still blocked.
- **DUAL_BATTERY** — both batteries receive coordinator writes.

Each stage requires a configurable minimum time before advancement.
Shadow mode suppresses all writes regardless of stage, logging decisions
without executing them.

State persists to a JSON file so stage survives container restarts.
"""
from __future__ import annotations

import enum
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CommissioningStage(str, enum.Enum):
    """Commissioning rollout stage."""

    READ_ONLY = "READ_ONLY"
    SINGLE_BATTERY = "SINGLE_BATTERY"
    DUAL_BATTERY = "DUAL_BATTERY"


# Ordered progression path
_STAGE_ORDER = [
    CommissioningStage.READ_ONLY,
    CommissioningStage.SINGLE_BATTERY,
    CommissioningStage.DUAL_BATTERY,
]


@dataclass
class CommissioningState:
    """Snapshot of the current commissioning state."""

    stage: CommissioningStage
    shadow_mode: bool
    stage_entered_at: float  # time.time() epoch
    read_only_min_hours: float
    single_battery_min_hours: float

    def can_write_victron(self) -> bool:
        """Return True when Victron coordinator writes are allowed."""
        if self.shadow_mode:
            return False
        return self.stage in (
            CommissioningStage.SINGLE_BATTERY,
            CommissioningStage.DUAL_BATTERY,
        )

    def can_write_huawei(self) -> bool:
        """Return True when Huawei coordinator writes are allowed."""
        if self.shadow_mode:
            return False
        return self.stage == CommissioningStage.DUAL_BATTERY


class CommissioningManager:
    """Manages staged rollout progression and persistence.

    Parameters
    ----------
    config:
        A :class:`~backend.config.CommissioningConfig` instance providing
        paths, timing thresholds, and the shadow mode flag.
    """

    def __init__(self, config) -> None:
        from backend.config import CommissioningConfig  # noqa: PLC0415

        self._config: CommissioningConfig = config
        self._state: CommissioningState | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_or_init(self) -> None:
        """Load persisted state or initialise at READ_ONLY."""
        loaded = self._load_state()
        if loaded is not None:
            self._state = loaded
            logger.info(
                "Commissioning state loaded: stage=%s shadow=%s",
                self._state.stage.value,
                self._state.shadow_mode,
            )
        else:
            self._state = CommissioningState(
                stage=CommissioningStage.READ_ONLY,
                shadow_mode=self._config.shadow_mode,
                stage_entered_at=time.time(),
                read_only_min_hours=self._config.read_only_min_hours,
                single_battery_min_hours=self._config.single_battery_min_hours,
            )
            self._save_state()
            logger.info("Commissioning state initialised at READ_ONLY")

    def advance(self) -> bool:
        """Attempt to advance to the next commissioning stage.

        Returns ``True`` if the stage was advanced, ``False`` if criteria
        were not met or the manager is already at DUAL_BATTERY.
        """
        assert self._state is not None, "Call load_or_init() first"

        idx = _STAGE_ORDER.index(self._state.stage)
        if idx >= len(_STAGE_ORDER) - 1:
            # Already at final stage
            return False

        min_hours = self._min_hours_for_current_stage()
        elapsed_hours = (time.time() - self._state.stage_entered_at) / 3600.0

        if elapsed_hours < min_hours:
            logger.debug(
                "Commissioning advance blocked: %.1fh / %.1fh required",
                elapsed_hours,
                min_hours,
            )
            return False

        next_stage = _STAGE_ORDER[idx + 1]
        logger.info(
            "Commissioning advancing: %s -> %s (after %.1fh)",
            self._state.stage.value,
            next_stage.value,
            elapsed_hours,
        )
        self._state.stage = next_stage
        self._state.stage_entered_at = time.time()
        self._save_state()
        return True

    @property
    def stage(self) -> CommissioningStage:
        """Current commissioning stage."""
        assert self._state is not None
        return self._state.stage

    @property
    def shadow_mode(self) -> bool:
        """Whether shadow mode is active."""
        assert self._state is not None
        return self._state.shadow_mode

    @shadow_mode.setter
    def shadow_mode(self, value: bool) -> None:
        """Set shadow mode and persist."""
        assert self._state is not None
        self._state.shadow_mode = value
        self._save_state()

    @property
    def state(self) -> CommissioningState:
        """Full state snapshot."""
        assert self._state is not None
        return self._state

    @property
    def stage_entered_at_iso(self) -> str:
        """ISO 8601 UTC timestamp of when the current stage was entered."""
        assert self._state is not None
        return datetime.fromtimestamp(
            self._state.stage_entered_at, tz=timezone.utc
        ).isoformat()

    def force_advance(self) -> bool:
        """Advance to the next commissioning stage, bypassing time requirement.

        Returns ``True`` if the stage was advanced, ``False`` if already at
        the final stage (DUAL_BATTERY).
        """
        assert self._state is not None, "Call load_or_init() first"

        idx = _STAGE_ORDER.index(self._state.stage)
        if idx >= len(_STAGE_ORDER) - 1:
            return False

        next_stage = _STAGE_ORDER[idx + 1]
        logger.info(
            "Commissioning force-advancing: %s -> %s",
            self._state.stage.value,
            next_stage.value,
        )
        self._state.stage = next_stage
        self._state.stage_entered_at = time.time()
        self._save_state()
        return True

    def get_progression_status(self) -> dict:
        """Return progression status for API display.

        Returns a dict with ``time_in_stage_hours``, ``min_hours_required``,
        and ``can_advance``.
        """
        assert self._state is not None
        elapsed = (time.time() - self._state.stage_entered_at) / 3600.0
        min_hours = self._min_hours_for_current_stage()
        return {
            "time_in_stage_hours": round(elapsed, 2),
            "min_hours_required": min_hours,
            "can_advance": elapsed >= min_hours
            and _STAGE_ORDER.index(self._state.stage) < len(_STAGE_ORDER) - 1,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Persist state to JSON file (atomic via os.replace)."""
        assert self._state is not None
        data = {
            "stage": self._state.stage.value,
            "shadow_mode": self._state.shadow_mode,
            "stage_entered_at": self._state.stage_entered_at,
        }
        path = self._config.state_file_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)

    def _load_state(self) -> CommissioningState | None:
        """Load state from JSON file, or return None if missing."""
        path = self._config.state_file_path
        if not os.path.isfile(path):
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            return CommissioningState(
                stage=CommissioningStage(data["stage"]),
                shadow_mode=data.get("shadow_mode", True),
                stage_entered_at=data.get("stage_entered_at", time.time()),
                read_only_min_hours=self._config.read_only_min_hours,
                single_battery_min_hours=self._config.single_battery_min_hours,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Failed to load commissioning state: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _min_hours_for_current_stage(self) -> float:
        """Return the minimum hours required before advancing from the current stage."""
        assert self._state is not None
        if self._state.stage == CommissioningStage.READ_ONLY:
            return self._state.read_only_min_hours
        if self._state.stage == CommissioningStage.SINGLE_BATTERY:
            return self._state.single_battery_min_hours
        return float("inf")  # DUAL_BATTERY has no next stage
