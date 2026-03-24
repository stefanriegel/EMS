"""Tests for the production commissioning state machine.

Covers CommissioningStage progression, shadow mode write blocking,
JSON persistence, CommissioningConfig.from_env(), and progression status.
"""
from __future__ import annotations

import json
import time

import pytest

from backend.commissioning import (
    CommissioningManager,
    CommissioningStage,
    CommissioningState,
)
from backend.config import CommissioningConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config(tmp_path):
    """Return a CommissioningConfig using a temp state file."""
    return CommissioningConfig(
        enabled=True,
        shadow_mode=False,
        state_file_path=str(tmp_path / "commissioning.json"),
        read_only_min_hours=24.0,
        single_battery_min_hours=24.0,
    )


@pytest.fixture()
def shadow_config(tmp_path):
    """Config with shadow mode enabled."""
    return CommissioningConfig(
        enabled=True,
        shadow_mode=True,
        state_file_path=str(tmp_path / "commissioning.json"),
        read_only_min_hours=24.0,
        single_battery_min_hours=24.0,
    )


@pytest.fixture()
def manager(config):
    """Return a freshly initialised CommissioningManager."""
    mgr = CommissioningManager(config)
    mgr.load_or_init()
    return mgr


# ---------------------------------------------------------------------------
# Stage progression
# ---------------------------------------------------------------------------

def test_initial_stage_is_read_only(manager):
    """New manager with no persisted state starts at READ_ONLY."""
    assert manager.stage == CommissioningStage.READ_ONLY


def test_stage_progression_read_only_to_single(config, monkeypatch):
    """advance() moves from READ_ONLY to SINGLE_BATTERY after min hours met."""
    mgr = CommissioningManager(config)
    mgr.load_or_init()
    # Pretend 25 hours have elapsed since stage entry
    fake_now = mgr.state.stage_entered_at + 25 * 3600
    monkeypatch.setattr(time, "time", lambda: fake_now)
    assert mgr.advance() is True
    assert mgr.stage == CommissioningStage.SINGLE_BATTERY


def test_stage_progression_single_to_dual(config, monkeypatch):
    """advance() moves from SINGLE_BATTERY to DUAL_BATTERY after min hours met."""
    mgr = CommissioningManager(config)
    mgr.load_or_init()
    # First advance to SINGLE_BATTERY
    t0 = mgr.state.stage_entered_at
    monkeypatch.setattr(time, "time", lambda: t0 + 25 * 3600)
    mgr.advance()
    # Now advance to DUAL_BATTERY
    t1 = mgr.state.stage_entered_at
    monkeypatch.setattr(time, "time", lambda: t1 + 25 * 3600)
    assert mgr.advance() is True
    assert mgr.stage == CommissioningStage.DUAL_BATTERY


def test_advance_blocked_when_criteria_not_met(manager, monkeypatch):
    """advance() returns False and stays at current stage when time criteria not met."""
    # Only 1 hour has passed (need 24)
    fake_now = manager.state.stage_entered_at + 1 * 3600
    monkeypatch.setattr(time, "time", lambda: fake_now)
    assert manager.advance() is False
    assert manager.stage == CommissioningStage.READ_ONLY


def test_no_advance_past_dual(config, monkeypatch):
    """advance() at DUAL_BATTERY is a no-op."""
    mgr = CommissioningManager(config)
    mgr.load_or_init()
    # Advance to DUAL_BATTERY
    t = mgr.state.stage_entered_at
    monkeypatch.setattr(time, "time", lambda: t + 25 * 3600)
    mgr.advance()
    t = mgr.state.stage_entered_at
    monkeypatch.setattr(time, "time", lambda: t + 25 * 3600)
    mgr.advance()
    assert mgr.stage == CommissioningStage.DUAL_BATTERY
    # Try advancing again
    t = mgr.state.stage_entered_at
    monkeypatch.setattr(time, "time", lambda: t + 100 * 3600)
    assert mgr.advance() is False
    assert mgr.stage == CommissioningStage.DUAL_BATTERY


# ---------------------------------------------------------------------------
# Write gating per stage
# ---------------------------------------------------------------------------

def test_can_write_victron_per_stage(config, monkeypatch):
    """READ_ONLY=False, SINGLE_BATTERY=True, DUAL_BATTERY=True (shadow_mode=False)."""
    mgr = CommissioningManager(config)
    mgr.load_or_init()
    # READ_ONLY
    assert mgr.state.can_write_victron() is False
    # SINGLE_BATTERY
    t = mgr.state.stage_entered_at
    monkeypatch.setattr(time, "time", lambda: t + 25 * 3600)
    mgr.advance()
    assert mgr.state.can_write_victron() is True
    # DUAL_BATTERY
    t = mgr.state.stage_entered_at
    monkeypatch.setattr(time, "time", lambda: t + 25 * 3600)
    mgr.advance()
    assert mgr.state.can_write_victron() is True


def test_can_write_huawei_per_stage(config, monkeypatch):
    """READ_ONLY=False, SINGLE_BATTERY=False, DUAL_BATTERY=True (shadow_mode=False)."""
    mgr = CommissioningManager(config)
    mgr.load_or_init()
    # READ_ONLY
    assert mgr.state.can_write_huawei() is False
    # SINGLE_BATTERY
    t = mgr.state.stage_entered_at
    monkeypatch.setattr(time, "time", lambda: t + 25 * 3600)
    mgr.advance()
    assert mgr.state.can_write_huawei() is False
    # DUAL_BATTERY
    t = mgr.state.stage_entered_at
    monkeypatch.setattr(time, "time", lambda: t + 25 * 3600)
    mgr.advance()
    assert mgr.state.can_write_huawei() is True


# ---------------------------------------------------------------------------
# Shadow mode
# ---------------------------------------------------------------------------

def test_shadow_mode_blocks_all_writes(shadow_config):
    """can_write_victron()=False and can_write_huawei()=False when shadow_mode=True."""
    mgr = CommissioningManager(shadow_config)
    mgr.load_or_init()
    # Even at READ_ONLY, shadow should block
    assert mgr.state.can_write_victron() is False
    assert mgr.state.can_write_huawei() is False
    # Shadow mode is set on state
    assert mgr.shadow_mode is True


# ---------------------------------------------------------------------------
# JSON persistence
# ---------------------------------------------------------------------------

def test_state_persistence_save_load(config, monkeypatch):
    """Save state, create new manager, load_or_init() restores same stage and shadow_mode."""
    mgr = CommissioningManager(config)
    mgr.load_or_init()
    # Advance to SINGLE_BATTERY
    t = mgr.state.stage_entered_at
    monkeypatch.setattr(time, "time", lambda: t + 25 * 3600)
    mgr.advance()
    assert mgr.stage == CommissioningStage.SINGLE_BATTERY

    # Create a new manager and load
    mgr2 = CommissioningManager(config)
    mgr2.load_or_init()
    assert mgr2.stage == CommissioningStage.SINGLE_BATTERY
    assert mgr2.shadow_mode is False


def test_state_persistence_missing_file(config):
    """load_or_init() with no file starts at READ_ONLY."""
    mgr = CommissioningManager(config)
    mgr.load_or_init()
    assert mgr.stage == CommissioningStage.READ_ONLY


# ---------------------------------------------------------------------------
# CommissioningConfig.from_env()
# ---------------------------------------------------------------------------

def test_config_from_env(monkeypatch):
    """CommissioningConfig.from_env() reads all expected env vars with correct defaults."""
    # Clear any existing env vars
    for var in (
        "EMS_COMMISSIONING_ENABLED",
        "EMS_SHADOW_MODE",
        "EMS_COMMISSIONING_STATE_PATH",
        "EMS_READ_ONLY_MIN_HOURS",
        "EMS_SINGLE_BATTERY_MIN_HOURS",
    ):
        monkeypatch.delenv(var, raising=False)

    cfg = CommissioningConfig.from_env()
    assert cfg.enabled is True
    assert cfg.shadow_mode is True
    assert cfg.state_file_path == "/config/ems_commissioning.json"
    assert cfg.read_only_min_hours == 24.0
    assert cfg.single_battery_min_hours == 24.0

    # Override all
    monkeypatch.setenv("EMS_COMMISSIONING_ENABLED", "false")
    monkeypatch.setenv("EMS_SHADOW_MODE", "false")
    monkeypatch.setenv("EMS_COMMISSIONING_STATE_PATH", "/tmp/test.json")
    monkeypatch.setenv("EMS_READ_ONLY_MIN_HOURS", "48")
    monkeypatch.setenv("EMS_SINGLE_BATTERY_MIN_HOURS", "72")
    cfg = CommissioningConfig.from_env()
    assert cfg.enabled is False
    assert cfg.shadow_mode is False
    assert cfg.state_file_path == "/tmp/test.json"
    assert cfg.read_only_min_hours == 48.0
    assert cfg.single_battery_min_hours == 72.0


# ---------------------------------------------------------------------------
# Progression status
# ---------------------------------------------------------------------------

def test_get_progression_status(config, monkeypatch):
    """Returns dict with time_in_stage_hours, min_hours_required, can_advance."""
    mgr = CommissioningManager(config)
    mgr.load_or_init()
    # 12 hours in
    fake_now = mgr.state.stage_entered_at + 12 * 3600
    monkeypatch.setattr(time, "time", lambda: fake_now)
    status = mgr.get_progression_status()
    assert abs(status["time_in_stage_hours"] - 12.0) < 0.01
    assert status["min_hours_required"] == 24.0
    assert status["can_advance"] is False

    # 25 hours in
    fake_now = mgr.state.stage_entered_at + 25 * 3600
    monkeypatch.setattr(time, "time", lambda: fake_now)
    status = mgr.get_progression_status()
    assert abs(status["time_in_stage_hours"] - 25.0) < 0.01
    assert status["can_advance"] is True
