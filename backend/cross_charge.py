"""Cross-charge detection and mitigation for dual-battery systems.

Detects battery-to-battery energy transfer (one discharging while the other
charges with near-zero grid flow) and forces the charging battery to HOLDING
to prevent wasteful DC-AC-DC round-trip losses.

Detection uses a 2-cycle debounce with configurable power and grid thresholds.
Episodes track cumulative waste energy and reset after a configurable cooldown.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from backend.controller_model import (
    BatteryRole,
    ControllerCommand,
    ControllerSnapshot,
)

logger = logging.getLogger(__name__)


@dataclass
class CrossChargeState:
    """Result of a single cross-charge detection check."""

    detected: bool = False
    source_system: str | None = None
    """System that is discharging ('huawei' or 'victron')."""

    sink_system: str | None = None
    """System that is charging ('huawei' or 'victron')."""

    source_power_w: float = 0.0
    sink_power_w: float = 0.0
    net_grid_power_w: float = 0.0
    consecutive_cycles: int = 0


@dataclass
class CrossChargeEpisode:
    """Tracks a single cross-charge episode for alerting and metrics."""

    start_time: float = 0.0
    """``time.monotonic()`` value when this episode started."""

    end_time: float | None = None
    cumulative_waste_wh: float = 0.0
    cycle_count: int = 0


class CrossChargeDetector:
    """Detects and mitigates battery-to-battery energy transfer.

    Parameters
    ----------
    threshold_w:
        Minimum absolute power per battery to consider cross-charge (default 100W).
    grid_threshold_w:
        Maximum absolute grid power for cross-charge detection (default 200W).
        Higher grid flow indicates household load, not cross-charge.
    min_cycles:
        Number of consecutive detection cycles before triggering (default 2).
    cycle_duration_s:
        Duration of one control cycle in seconds (default 5.0).
    episode_reset_s:
        Seconds of continuous non-detection before resetting an episode (default 300).
    """

    def __init__(
        self,
        threshold_w: float = 100.0,
        grid_threshold_w: float = 200.0,
        min_cycles: int = 2,
        cycle_duration_s: float = 5.0,
        episode_reset_s: float = 300.0,
    ) -> None:
        self._threshold_w = threshold_w
        self._grid_threshold_w = grid_threshold_w
        self._min_cycles = min_cycles
        self._cycle_duration_s = cycle_duration_s
        self._episode_reset_s = episode_reset_s

        # Detection state
        self._consecutive_count: int = 0
        self._last_clear_time: float = time.monotonic()

        # Episode tracking
        self._active_episode: CrossChargeEpisode | None = None
        self._total_episodes: int = 0
        self._total_waste_wh: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        h_snap: ControllerSnapshot,
        v_snap: ControllerSnapshot,
    ) -> CrossChargeState:
        """Check current snapshots for cross-charge condition.

        Returns a ``CrossChargeState`` describing the detection result.
        Also manages episode lifecycle (start, waste accumulation, reset).
        """
        grid_w = self._get_grid_power(v_snap)
        if grid_w is None:
            self._consecutive_count = 0
            self._maybe_reset_episode()
            return CrossChargeState()

        h_power = h_snap.power_w
        v_power = v_snap.power_w

        # Cross-charge condition: both above threshold, opposing signs, grid near zero
        cross = (
            abs(h_power) > self._threshold_w
            and abs(v_power) > self._threshold_w
            and (h_power > 0) != (v_power > 0)
            and abs(grid_w) < self._grid_threshold_w
        )

        if cross:
            self._consecutive_count += 1
        else:
            self._consecutive_count = 0
            # Check episode reset BEFORE updating _last_clear_time so that
            # elapsed time is measured from the previous clear, not this one.
            self._maybe_reset_episode()
            self._last_clear_time = time.monotonic()

        detected = self._consecutive_count >= self._min_cycles

        # Identify source (discharging, power < 0) and sink (charging, power > 0)
        source: str | None = None
        sink: str | None = None
        source_w = 0.0
        sink_w = 0.0

        if detected:
            if h_power < 0:
                source, sink = "huawei", "victron"
                source_w, sink_w = abs(h_power), abs(v_power)
            else:
                source, sink = "victron", "huawei"
                source_w, sink_w = abs(v_power), abs(h_power)

            # Episode management
            self._update_episode(source_w, sink_w)

        return CrossChargeState(
            detected=detected,
            source_system=source,
            sink_system=sink,
            source_power_w=source_w,
            sink_power_w=sink_w,
            net_grid_power_w=grid_w,
            consecutive_cycles=self._consecutive_count,
        )

    def mitigate(
        self,
        state: CrossChargeState,
        h_cmd: ControllerCommand,
        v_cmd: ControllerCommand,
    ) -> tuple[ControllerCommand, ControllerCommand]:
        """Force the charging (sink) battery to HOLDING.

        Returns new ``ControllerCommand`` instances — does not mutate inputs.
        """
        if state.sink_system == "huawei":
            h_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        elif state.sink_system == "victron":
            v_cmd = ControllerCommand(role=BatteryRole.HOLDING, target_watts=0.0)
        return h_cmd, v_cmd

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        """True when cross-charge is currently detected."""
        return self._active_episode is not None and self._consecutive_count >= self._min_cycles

    @property
    def total_episodes(self) -> int:
        """Total number of completed cross-charge episodes."""
        return self._total_episodes

    @property
    def total_waste_wh(self) -> float:
        """Total cumulative waste energy in Wh across all episodes."""
        return self._total_waste_wh

    @property
    def current_episode(self) -> CrossChargeEpisode | None:
        """Active episode, or None if no cross-charge in progress."""
        return self._active_episode

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_grid_power(self, v_snap: ControllerSnapshot) -> float | None:
        """Resolve grid power from Victron snapshot.

        Prefers sum of L1+L2+L3 for 3-phase accuracy, falls back to
        ``grid_power_w``. Returns ``None`` if no grid data available.
        """
        if (
            v_snap.grid_l1_power_w is not None
            and v_snap.grid_l2_power_w is not None
            and v_snap.grid_l3_power_w is not None
        ):
            return (
                v_snap.grid_l1_power_w
                + v_snap.grid_l2_power_w
                + v_snap.grid_l3_power_w
            )
        return v_snap.grid_power_w

    def _update_episode(self, source_w: float, sink_w: float) -> None:
        """Start or update the active episode with waste accumulation."""
        if self._active_episode is None:
            self._active_episode = CrossChargeEpisode(
                start_time=time.monotonic(),
            )
            logger.warning(
                "Cross-charge episode started: source=%.0fW, sink=%.0fW",
                source_w,
                sink_w,
            )

        waste_wh = min(source_w, sink_w) * self._cycle_duration_s / 3600.0
        self._active_episode.cumulative_waste_wh += waste_wh
        self._active_episode.cycle_count += 1

    def _maybe_reset_episode(self) -> None:
        """Reset episode if cooldown period has elapsed since last clear."""
        if self._active_episode is None:
            return

        elapsed = time.monotonic() - self._last_clear_time
        if elapsed >= self._episode_reset_s:
            self._active_episode.end_time = time.monotonic()
            self._total_waste_wh += self._active_episode.cumulative_waste_wh
            self._total_episodes += 1
            logger.info(
                "Cross-charge episode ended: waste=%.2fWh, cycles=%d",
                self._active_episode.cumulative_waste_wh,
                self._active_episode.cycle_count,
            )
            self._active_episode = None
