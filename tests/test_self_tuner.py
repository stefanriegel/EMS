"""Unit tests for the SelfTuner adaptive parameter tuning engine.

Covers all 8 TUNE requirements plus _apply_params coordinator injection.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.self_tuner import SelfTuner, TuningParams, TuningState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_forecaster(
    mape: float | None = 10.0,
    days: int = 90,
    hourly: list[float] | None = None,
) -> SimpleNamespace:
    """Return a mock forecaster with configurable ml_status and predictions."""
    status = {"mape": {"current": mape}, "days_of_history": days}
    f = SimpleNamespace()
    f.get_ml_status = lambda: status
    if hourly is None:
        # Default: moderate consumption, peak at hours 17-20
        hourly = [0.3] * 17 + [1.5, 1.8, 1.6, 1.2] + [0.4] * 3
    f.predict_hourly = AsyncMock(return_value=hourly)
    return f


def _make_coordinator() -> SimpleNamespace:
    """Return a mock coordinator with tunable runtime fields."""
    ns = SimpleNamespace()
    ns._huawei_deadband_w = 300
    ns._victron_deadband_w = 150
    ns._huawei_ramp_w_per_cycle = 2000
    ns._victron_ramp_w_per_cycle = 1000
    ns._sys_config = SimpleNamespace(
        huawei_min_soc_profile=None,
        victron_min_soc_profile=None,
    )
    return ns


def _tuner_with_stats(
    tmp_path,
    transitions_per_hour: int = 8,
    spikes_per_hour: int = 1,
    hours: int = 168,
    mode: str = "live",
    shadow_days: int = 0,
) -> SelfTuner:
    """Create a SelfTuner pre-loaded with synthetic hourly stats."""
    state_path = str(tmp_path / "tuning_state.json")
    tuner = SelfTuner(state_path=state_path)
    tuner._state.mode = mode
    tuner._state.shadow_days = shadow_days
    # Pre-populate base_params and current_params
    defaults = TuningParams()
    base = {
        "huawei_deadband_w": defaults.huawei_deadband_w,
        "victron_deadband_w": defaults.victron_deadband_w,
        "ramp_rate_w": defaults.ramp_rate_w,
    }
    tuner._state.base_params = dict(base)
    tuner._state.current_params = dict(base)
    tuner._state.previous_params = dict(base)
    # Fill hourly stats
    tuner._hourly_stats = [
        {
            "transitions": transitions_per_hour,
            "grid_spikes": spikes_per_hour,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        for _ in range(hours)
    ]
    return tuner


# ---------------------------------------------------------------------------
# TUNE-01: Oscillation counting
# ---------------------------------------------------------------------------

class TestTransitionCounting:
    """TUNE-01: record_cycle() counts state transitions per hour."""

    def test_transition_counting(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "state.json"))  # type: ignore[arg-type]
        # Simulate 720 cycles (1 hour) with transitions every 120 cycles
        for i in range(720):
            status = "CHARGING" if (i // 120) % 2 == 0 else "DISCHARGING"
            tuner.record_cycle(pool_status=status, grid_power_w=0.0)

        # After 720 cycles, hourly stats should have rolled over
        assert len(tuner._hourly_stats) == 1
        # 6 blocks of 120 cycles alternating = 5 transitions
        assert tuner._hourly_stats[0]["transitions"] == 5

    def test_no_transitions_same_status(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "state.json"))  # type: ignore[arg-type]
        for _ in range(720):
            tuner.record_cycle(pool_status="HOLDING", grid_power_w=0.0)
        assert len(tuner._hourly_stats) == 1
        assert tuner._hourly_stats[0]["transitions"] == 0

    def test_grid_spike_only_on_transition(self, tmp_path: object) -> None:
        """Grid spike counted only when coincident with a state transition."""
        tuner = SelfTuner(state_path=str(tmp_path / "state.json"))  # type: ignore[arg-type]
        # High grid power but no transition -- should NOT count as spike
        for _ in range(10):
            tuner.record_cycle(pool_status="DISCHARGING", grid_power_w=800.0)
        assert tuner._hourly_grid_spikes == 0

        # Now a transition with high grid power -- SHOULD count
        tuner.record_cycle(pool_status="CHARGING", grid_power_w=800.0)
        assert tuner._hourly_grid_spikes == 1

    def test_hourly_rollover_caps_at_168(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "state.json"))  # type: ignore[arg-type]
        # Manually add 170 hourly entries
        tuner._hourly_stats = [
            {"transitions": 1, "grid_spikes": 0, "timestamp": "t"}
        ] * 170
        # Trigger a rollover
        tuner._cycle_count = 720
        tuner.record_cycle(pool_status="X", grid_power_w=0.0)
        assert len(tuner._hourly_stats) <= 168


# ---------------------------------------------------------------------------
# TUNE-02: Dead-band tuning
# ---------------------------------------------------------------------------

class TestDeadbandTuning:
    """TUNE-02: nightly_tune() adjusts dead-band based on oscillation rate."""

    @pytest.mark.anyio
    async def test_deadband_tuning_increase(self, tmp_path: object) -> None:
        """High oscillation rate (>6/hr) increases dead-band."""
        tuner = _tuner_with_stats(tmp_path, transitions_per_hour=10)  # type: ignore[arg-type]
        forecaster = _make_forecaster()
        await tuner.nightly_tune(forecaster)
        # Dead-band should have increased
        assert tuner._state.current_params["huawei_deadband_w"] > 300
        assert tuner._state.current_params["victron_deadband_w"] > 150

    @pytest.mark.anyio
    async def test_deadband_tuning_decrease(self, tmp_path: object) -> None:
        """Low oscillation rate (<2/hr) decreases dead-band."""
        tuner = _tuner_with_stats(tmp_path, transitions_per_hour=1)  # type: ignore[arg-type]
        forecaster = _make_forecaster()
        await tuner.nightly_tune(forecaster)
        assert tuner._state.current_params["huawei_deadband_w"] < 300
        assert tuner._state.current_params["victron_deadband_w"] < 150


# ---------------------------------------------------------------------------
# TUNE-03: Ramp rate tuning
# ---------------------------------------------------------------------------

class TestRampRateTuning:
    """TUNE-03: nightly_tune() adjusts ramp rate based on grid spikes."""

    @pytest.mark.anyio
    async def test_ramp_rate_tuning_increase(self, tmp_path: object) -> None:
        """High spike count (>3/day avg) increases ramp rate."""
        # 1 spike per hour = 24 per day >> 3
        tuner = _tuner_with_stats(tmp_path, transitions_per_hour=4, spikes_per_hour=1)  # type: ignore[arg-type]
        # Start below clamp max so increase is observable
        tuner._state.current_params["ramp_rate_w"] = 1500
        tuner._state.base_params["ramp_rate_w"] = 1500
        forecaster = _make_forecaster()
        await tuner.nightly_tune(forecaster)
        assert tuner._state.current_params["ramp_rate_w"] > 1500

    @pytest.mark.anyio
    async def test_ramp_rate_tuning_decrease(self, tmp_path: object) -> None:
        """Zero spikes for 7 days decreases ramp rate."""
        tuner = _tuner_with_stats(tmp_path, transitions_per_hour=4, spikes_per_hour=0)  # type: ignore[arg-type]
        forecaster = _make_forecaster()
        await tuner.nightly_tune(forecaster)
        assert tuner._state.current_params["ramp_rate_w"] < 2000


# ---------------------------------------------------------------------------
# TUNE-04: Min-SoC profile tuning
# ---------------------------------------------------------------------------

class TestMinSocProfile:
    """TUNE-04: nightly_tune() generates min-SoC profile from consumption forecast."""

    @pytest.mark.anyio
    async def test_min_soc_profile(self, tmp_path: object) -> None:
        """Profile has higher min-SoC before peak consumption hours."""
        # Hours 17-20 have high consumption (1.5-1.8 kWh), rest low (0.3-0.4)
        tuner = _tuner_with_stats(tmp_path, transitions_per_hour=4)  # type: ignore[arg-type]
        forecaster = _make_forecaster()
        await tuner.nightly_tune(forecaster)

        profile = tuner._state.current_params.get("huawei_min_soc_profile")
        assert profile is not None
        assert len(profile) == 6  # 4-hour blocks

        # The 16-20 block should have higher min-SoC than the 0-4 block
        block_16_20 = next(p for p in profile if p["start_hour"] == 16)
        block_0_4 = next(p for p in profile if p["start_hour"] == 0)
        assert block_16_20["min_soc_pct"] > block_0_4["min_soc_pct"]


# ---------------------------------------------------------------------------
# TUNE-05: Shadow mode
# ---------------------------------------------------------------------------

class TestShadowMode:
    """TUNE-05: Shadow mode logs recommendations without applying."""

    @pytest.mark.anyio
    async def test_shadow_mode(self, tmp_path: object) -> None:
        """Shadow mode appends to shadow_log without modifying current_params."""
        tuner = _tuner_with_stats(
            tmp_path, transitions_per_hour=10, mode="shadow",  # type: ignore[arg-type]
        )
        original = dict(tuner._state.current_params)
        forecaster = _make_forecaster()
        await tuner.nightly_tune(forecaster)

        # current_params unchanged in shadow mode
        assert tuner._state.current_params["huawei_deadband_w"] == original["huawei_deadband_w"]
        # shadow_log should have an entry
        assert len(tuner._state.shadow_log) == 1
        assert tuner._state.shadow_days == 1

    @pytest.mark.anyio
    async def test_shadow_auto_promotion(self, tmp_path: object) -> None:
        """After 14 shadow days, mode auto-promotes to live."""
        tuner = _tuner_with_stats(
            tmp_path, transitions_per_hour=4,  # type: ignore[arg-type]
            mode="shadow", shadow_days=13,
        )
        forecaster = _make_forecaster()
        await tuner.nightly_tune(forecaster)

        assert tuner._state.shadow_days == 14
        assert tuner._state.mode == "live"

    def test_shadow_day_counter_persists(self, tmp_path: object) -> None:
        """Shadow day counter survives re-instantiation (restart resilience)."""
        state_path = str(tmp_path / "state.json")  # type: ignore[union-attr]
        tuner = SelfTuner(state_path=state_path)
        tuner._state.shadow_days = 7
        tuner._state.shadow_start_date = "2026-03-01"
        tuner._save_state()

        # Re-instantiate
        tuner2 = SelfTuner(state_path=state_path)
        assert tuner2._state.shadow_days == 7
        assert tuner2._state.shadow_start_date == "2026-03-01"


# ---------------------------------------------------------------------------
# TUNE-06: Bounded changes
# ---------------------------------------------------------------------------

class TestBoundedAdjustment:
    """TUNE-06: _bounded_adjust() caps change to 10% of base with clamp."""

    def test_bounded_adjustment(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        # 10% of base 300 = 30, so recommending +100 should cap at +30
        result = tuner._bounded_adjust("huawei_deadband_w", 300, 300, 400)
        assert result == 330

    def test_bounded_adjustment_decrease(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        # 10% of base 300 = 30, recommending -100 should cap at -30
        result = tuner._bounded_adjust("huawei_deadband_w", 300, 300, 200)
        assert result == 270

    def test_bounded_adjustment_clamp_range(self, tmp_path: object) -> None:
        """Result stays within clamp range even when recommended far outside."""
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        # Current at 60, base 300, recommend 0 -> delta = -60, capped at -30 -> 30
        # but clamp min is 50, so result should be 50
        result = tuner._bounded_adjust("huawei_deadband_w", 60, 300, 0)
        assert result == 50

    @pytest.mark.parametrize(
        "param,current,base,recommended,expected_min,expected_max",
        [
            ("huawei_deadband_w", 300, 300, 2000, 300, 330),
            ("victron_deadband_w", 150, 150, 0, 135, 150),
            ("ramp_rate_w", 2000, 2000, 3000, 2000, 2000),  # already at max
            ("ramp_rate_w", 100, 2000, 0, 100, 100),  # at clamp min
        ],
    )
    def test_bounded_adjustment_parametrized(
        self,
        tmp_path: object,
        param: str,
        current: float,
        base: float,
        recommended: float,
        expected_min: float,
        expected_max: float,
    ) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        result = tuner._bounded_adjust(param, current, base, recommended)
        assert expected_min <= result <= expected_max

    def test_uses_base_not_current(self, tmp_path: object) -> None:
        """10% bound uses base value, not current value (pitfall #5)."""
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        # Current=100, base=300, recommend=200 -> delta=100, cap=10%*300=30
        result = tuner._bounded_adjust("huawei_deadband_w", 100, 300, 200)
        assert result == 130  # 100 + 30, not 100 + 10


# ---------------------------------------------------------------------------
# TUNE-07: Automatic rollback
# ---------------------------------------------------------------------------

class TestAutomaticRollback:
    """TUNE-07: _check_rollback() reverts params when oscillation spikes."""

    def test_automatic_rollback(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        tuner._state.last_oscillation_rate = 5.0
        # 25% increase -> > 20% threshold
        assert tuner._check_rollback(6.25) is True

    def test_no_rollback_within_threshold(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        tuner._state.last_oscillation_rate = 5.0
        # 10% increase -> < 20% threshold
        assert tuner._check_rollback(5.5) is False

    def test_no_rollback_when_no_previous(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        tuner._state.last_oscillation_rate = None
        assert tuner._check_rollback(10.0) is False

    @pytest.mark.anyio
    async def test_rollback_restores_previous_params(self, tmp_path: object) -> None:
        """nightly_tune restores previous_params on rollback."""
        tuner = _tuner_with_stats(tmp_path, transitions_per_hour=10)  # type: ignore[arg-type]
        # Set previous params to specific values
        tuner._state.previous_params = {
            "huawei_deadband_w": 250,
            "victron_deadband_w": 120,
            "ramp_rate_w": 1800,
        }
        # Set last oscillation rate low so current rate triggers rollback
        tuner._state.last_oscillation_rate = 3.0
        # Current avg is 10 trans/hr -> more than 20% increase from 3.0

        forecaster = _make_forecaster()
        await tuner.nightly_tune(forecaster)

        # Should have rolled back to previous
        assert tuner._state.current_params["huawei_deadband_w"] == 250
        assert tuner._state.current_params["victron_deadband_w"] == 120
        assert tuner._state.current_params["ramp_rate_w"] == 1800


# ---------------------------------------------------------------------------
# TUNE-08: Activation gate
# ---------------------------------------------------------------------------

class TestActivationGate:
    """TUNE-08: _check_activation_gate() validates preconditions."""

    def test_activation_gate(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        forecaster = _make_forecaster(mape=10.0, days=90)
        assert tuner._check_activation_gate(forecaster) is True

    def test_activation_gate_high_mape(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        forecaster = _make_forecaster(mape=30.0, days=90)
        assert tuner._check_activation_gate(forecaster) is False

    def test_activation_gate_low_days(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        forecaster = _make_forecaster(mape=10.0, days=30)
        assert tuner._check_activation_gate(forecaster) is False

    def test_activation_gate_no_forecaster(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        assert tuner._check_activation_gate(None) is False

    def test_activation_gate_mape_none(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        forecaster = _make_forecaster(mape=None, days=90)
        assert tuner._check_activation_gate(forecaster) is False

    def test_activation_gate_boundary_mape_25(self, tmp_path: object) -> None:
        """MAPE == 25 should fail (need strictly < 25)."""
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        forecaster = _make_forecaster(mape=25.0, days=90)
        assert tuner._check_activation_gate(forecaster) is False

    def test_activation_gate_boundary_days_60(self, tmp_path: object) -> None:
        """60 days should pass (need >= 60)."""
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        forecaster = _make_forecaster(mape=10.0, days=60)
        assert tuner._check_activation_gate(forecaster) is True

    @pytest.mark.anyio
    async def test_nightly_tune_skips_when_gate_fails(self, tmp_path: object) -> None:
        """nightly_tune() returns early when activation gate fails."""
        tuner = _tuner_with_stats(tmp_path, transitions_per_hour=10)  # type: ignore[arg-type]
        original = dict(tuner._state.current_params)
        forecaster = _make_forecaster(mape=50.0, days=10)
        await tuner.nightly_tune(forecaster)
        # Params should be unchanged
        assert tuner._state.current_params == original


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

class TestStatePersistence:
    """State persistence round-trips correctly."""

    def test_save_load_roundtrip(self, tmp_path: object) -> None:
        state_path = str(tmp_path / "state.json")  # type: ignore[union-attr]
        tuner = SelfTuner(state_path=state_path)
        tuner._state.mode = "live"
        tuner._state.shadow_days = 14
        tuner._state.current_params = {"huawei_deadband_w": 350}
        tuner._state.ha_overrides = {"huawei_deadband_w": "2026-03-24"}
        tuner._save_state()

        tuner2 = SelfTuner(state_path=state_path)
        assert tuner2._state.mode == "live"
        assert tuner2._state.shadow_days == 14
        assert tuner2._state.current_params == {"huawei_deadband_w": 350}
        assert tuner2._state.ha_overrides == {"huawei_deadband_w": "2026-03-24"}


# ---------------------------------------------------------------------------
# HA command override tracking
# ---------------------------------------------------------------------------

class TestHaOverrides:
    """HA command override tracking skips tuning for overridden params."""

    @pytest.mark.anyio
    async def test_ha_override_skips_param(self, tmp_path: object) -> None:
        tuner = _tuner_with_stats(tmp_path, transitions_per_hour=10)  # type: ignore[arg-type]
        tuner.mark_ha_override("huawei_deadband_w")
        original_huawei = tuner._state.current_params["huawei_deadband_w"]
        forecaster = _make_forecaster()
        await tuner.nightly_tune(forecaster)
        # huawei deadband should be unchanged (HA overridden)
        assert tuner._state.current_params["huawei_deadband_w"] == original_huawei
        # victron deadband should have changed (not overridden)
        assert tuner._state.current_params["victron_deadband_w"] != 150

    @pytest.mark.anyio
    async def test_ha_overrides_cleared_after_nightly(self, tmp_path: object) -> None:
        tuner = _tuner_with_stats(tmp_path, transitions_per_hour=4)  # type: ignore[arg-type]
        tuner.mark_ha_override("ramp_rate_w")
        forecaster = _make_forecaster()
        await tuner.nightly_tune(forecaster)
        assert len(tuner._state.ha_overrides) == 0


# ---------------------------------------------------------------------------
# _apply_params: Coordinator parameter injection
# ---------------------------------------------------------------------------

class TestApplyParams:
    """_apply_params() pushes tuned parameters to coordinator runtime fields."""

    def test_apply_params_live_mode(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        tuner._state.mode = "live"
        tuner._state.current_params = {
            "huawei_deadband_w": 350,
            "victron_deadband_w": 180,
            "ramp_rate_w": 1800,
        }
        coord = _make_coordinator()
        tuner.set_coordinator(coord)
        tuner._apply_params()

        assert coord._huawei_deadband_w == 350
        assert coord._victron_deadband_w == 180
        assert coord._huawei_ramp_w_per_cycle == 1800
        assert coord._victron_ramp_w_per_cycle == 1800

    def test_apply_params_shadow_noop(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        tuner._state.mode = "shadow"
        tuner._state.current_params = {
            "huawei_deadband_w": 350,
            "victron_deadband_w": 180,
            "ramp_rate_w": 1800,
        }
        coord = _make_coordinator()
        tuner.set_coordinator(coord)
        tuner._apply_params()

        # Coordinator should be unchanged
        assert coord._huawei_deadband_w == 300
        assert coord._victron_deadband_w == 150

    def test_apply_params_no_coordinator(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        tuner._state.mode = "live"
        tuner._state.current_params = {
            "huawei_deadband_w": 350,
            "victron_deadband_w": 180,
            "ramp_rate_w": 1800,
        }
        # No coordinator set -- should not raise
        tuner._apply_params()

    def test_apply_params_min_soc_profile(self, tmp_path: object) -> None:
        """_apply_params propagates min-SoC profiles to coordinator."""
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        tuner._state.mode = "live"
        profile = [
            {"start_hour": 0, "end_hour": 4, "min_soc_pct": 10.0},
            {"start_hour": 16, "end_hour": 20, "min_soc_pct": 20.0},
        ]
        tuner._state.current_params = {
            "huawei_deadband_w": 300,
            "victron_deadband_w": 150,
            "ramp_rate_w": 2000,
            "huawei_min_soc_profile": profile,
            "victron_min_soc_profile": profile,
        }
        coord = _make_coordinator()
        tuner.set_coordinator(coord)
        tuner._apply_params()

        assert coord._sys_config.huawei_min_soc_profile is not None
        assert len(coord._sys_config.huawei_min_soc_profile) == 2

    @pytest.mark.anyio
    async def test_nightly_tune_live_applies_to_coordinator(
        self, tmp_path: object
    ) -> None:
        """Full integration: nightly_tune in live mode updates coordinator fields."""
        tuner = _tuner_with_stats(tmp_path, transitions_per_hour=10)  # type: ignore[arg-type]
        coord = _make_coordinator()
        tuner.set_coordinator(coord)
        forecaster = _make_forecaster()
        await tuner.nightly_tune(forecaster)

        # Deadband should have increased (oscillation > 6)
        assert coord._huawei_deadband_w > 300

    @pytest.mark.anyio
    async def test_rollback_applies_to_coordinator(self, tmp_path: object) -> None:
        """Rollback via nightly_tune also reverts coordinator fields."""
        tuner = _tuner_with_stats(tmp_path, transitions_per_hour=10)  # type: ignore[arg-type]
        tuner._state.previous_params = {
            "huawei_deadband_w": 250,
            "victron_deadband_w": 120,
            "ramp_rate_w": 1800,
        }
        tuner._state.last_oscillation_rate = 3.0
        coord = _make_coordinator()
        tuner.set_coordinator(coord)

        forecaster = _make_forecaster()
        await tuner.nightly_tune(forecaster)

        # Should have rolled back to previous values on coordinator
        assert coord._huawei_deadband_w == 250
        assert coord._victron_deadband_w == 120


# ---------------------------------------------------------------------------
# get_tuning_status
# ---------------------------------------------------------------------------

class TestTuningStatus:
    """get_tuning_status() returns the expected shape."""

    def test_get_tuning_status(self, tmp_path: object) -> None:
        tuner = SelfTuner(state_path=str(tmp_path / "s.json"))  # type: ignore[arg-type]
        status = tuner.get_tuning_status()
        assert "mode" in status
        assert "shadow_days" in status
        assert "current_params" in status
        assert "activation_gate" in status
