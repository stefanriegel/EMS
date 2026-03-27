# Supervisory EMS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 5s setpoint control loop with a supervisory observation + intervention model that lets native battery controllers run autonomously.

**Architecture:** A `Supervisor` class observes both battery systems at a configurable interval (default 5s) and evaluates 4 priority-ordered interventions (Min-SoC Guard, Cross-Charge Prevention, Grid Charge Window, SoC Balancing). It writes to batteries only when an intervention triggers. The existing coordinator is preserved behind a `control_mode` feature flag for instant rollback.

**Tech Stack:** Python 3.12+, FastAPI, pymodbus, pytest + anyio, React 19 + TypeScript

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `backend/supervisor_model.py` | `BatteryState` enum (AUTONOMOUS/HELD/GRID_CHARGING), `Observation` dataclass, `InterventionRecord` dataclass, `SupervisorState` dataclass |
| `backend/interventions.py` | Four intervention functions: `check_min_soc`, `check_cross_charge`, `check_grid_charge`, `check_soc_balance`. Each takes an `Observation` and returns `InterventionAction | None` |
| `backend/supervisor.py` | `Supervisor` class: observation timer, intervention engine, controller writes, state management |
| `tests/test_supervisor_model.py` | Tests for supervisor data models |
| `tests/test_interventions.py` | Tests for each intervention rule in isolation |
| `tests/test_supervisor.py` | Tests for the supervisor loop, priority ordering, controller integration |

### Modified Files

| File | Change |
|------|--------|
| `backend/config.py` | Add `SupervisoryConfig` dataclass with `from_env()` |
| `backend/orchestrator.py` | Branch on `control_mode`: create `Supervisor` or `Coordinator` |
| `backend/api.py` | Add `GET /api/interventions`, update `GET /api/state` for supervisor mode |
| `backend/influx_writer.py` | Add `write_observation()` and `write_intervention()` methods |
| `backend/ws_manager.py` | No structural change — broadcast format updated via api.py |
| `ems/config.yaml` | Add `control_mode`, `observation_interval_s`, `soc_balance_threshold`, `soc_balance_hysteresis`, `min_soc_hysteresis` |
| `ems/run.sh` | Map new config options to env vars |
| `frontend/src/types.ts` | Add `InterventionEntry` interface, `BatteryState` type |
| `frontend/src/components/DecisionLog.tsx` | Render intervention log when in supervisory mode |
| `frontend/src/components/BatteryStatus.tsx` | Map 3 battery states to colors/labels |
| `frontend/src/components/PoolOverview.tsx` | Update control state display |

---

### Task 1: Supervisor Data Models

**Files:**
- Create: `backend/supervisor_model.py`
- Create: `tests/test_supervisor_model.py`

- [ ] **Step 1: Write tests for BatteryState enum and Observation dataclass**

```python
# tests/test_supervisor_model.py
from __future__ import annotations

from backend.supervisor_model import (
    BatteryState,
    Observation,
    InterventionAction,
    InterventionRecord,
    SupervisorState,
)


class TestBatteryState:
    def test_values(self) -> None:
        assert BatteryState.AUTONOMOUS == "AUTONOMOUS"
        assert BatteryState.HELD == "HELD"
        assert BatteryState.GRID_CHARGING == "GRID_CHARGING"

    def test_is_str_enum(self) -> None:
        assert isinstance(BatteryState.AUTONOMOUS, str)


class TestObservation:
    def test_pool_soc(self) -> None:
        obs = Observation(
            huawei_soc_pct=50.0,
            victron_soc_pct=50.0,
            huawei_power_w=-1000.0,
            victron_power_w=-2000.0,
            pv_power_w=3000.0,
            emma_load_power_w=4000.0,
            victron_consumption_w=1000.0,
            huawei_available=True,
            victron_available=True,
            timestamp=1000.0,
        )
        # pool_soc = (50*30 + 50*64) / 94 = 50.0
        assert obs.pool_soc == 50.0

    def test_pool_soc_weighted(self) -> None:
        obs = Observation(
            huawei_soc_pct=100.0,
            victron_soc_pct=0.0,
            huawei_power_w=0.0,
            victron_power_w=0.0,
            pv_power_w=0.0,
            emma_load_power_w=0.0,
            victron_consumption_w=0.0,
            huawei_available=True,
            victron_available=True,
            timestamp=1000.0,
        )
        # pool_soc = (100*30 + 0*64) / 94 ≈ 31.91
        assert abs(obs.pool_soc - 31.91) < 0.1

    def test_soc_delta(self) -> None:
        obs = Observation(
            huawei_soc_pct=80.0,
            victron_soc_pct=60.0,
            huawei_power_w=0.0,
            victron_power_w=0.0,
            pv_power_w=0.0,
            emma_load_power_w=0.0,
            victron_consumption_w=0.0,
            huawei_available=True,
            victron_available=True,
            timestamp=1000.0,
        )
        assert obs.soc_delta == 20.0

    def test_true_consumption(self) -> None:
        obs = Observation(
            huawei_soc_pct=50.0,
            victron_soc_pct=50.0,
            huawei_power_w=0.0,
            victron_power_w=0.0,
            pv_power_w=0.0,
            emma_load_power_w=3000.0,
            victron_consumption_w=1500.0,
            huawei_available=True,
            victron_available=True,
            timestamp=1000.0,
        )
        assert obs.true_consumption_w == 4500.0


class TestInterventionAction:
    def test_hold_action(self) -> None:
        action = InterventionAction(
            target_system="huawei",
            target_state=BatteryState.HELD,
        )
        assert action.target_system == "huawei"
        assert action.target_state == BatteryState.HELD
        assert action.max_discharge_power_w is None
        assert action.target_soc_pct is None

    def test_throttle_action(self) -> None:
        action = InterventionAction(
            target_system="huawei",
            target_state=BatteryState.AUTONOMOUS,
            max_discharge_power_w=2500,
        )
        assert action.max_discharge_power_w == 2500


class TestInterventionRecord:
    def test_fields(self) -> None:
        rec = InterventionRecord(
            timestamp="2026-03-28T10:00:00Z",
            intervention_type="min_soc_guard",
            target_system="victron",
            action=BatteryState.HELD,
            reason="Victron SoC 8% below min_soc 10%",
        )
        assert rec.intervention_type == "min_soc_guard"
        assert rec.target_system == "victron"


class TestSupervisorState:
    def test_fields(self) -> None:
        state = SupervisorState(
            pool_soc_pct=50.0,
            huawei_soc_pct=50.0,
            victron_soc_pct=50.0,
            soc_delta=0.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            huawei_available=True,
            victron_available=True,
            true_consumption_w=5000.0,
            pv_power_w=3000.0,
            active_interventions=[],
            timestamp=1000.0,
        )
        assert state.huawei_state == BatteryState.AUTONOMOUS
        assert state.active_interventions == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_supervisor_model.py -v`
Expected: `ModuleNotFoundError: No module named 'backend.supervisor_model'`

- [ ] **Step 3: Implement supervisor_model.py**

```python
# backend/supervisor_model.py
from __future__ import annotations

import enum
from dataclasses import dataclass, field

HUAWEI_CAPACITY_KWH = 30.0
VICTRON_CAPACITY_KWH = 64.0
TOTAL_CAPACITY_KWH = HUAWEI_CAPACITY_KWH + VICTRON_CAPACITY_KWH


class BatteryState(str, enum.Enum):
    AUTONOMOUS = "AUTONOMOUS"
    HELD = "HELD"
    GRID_CHARGING = "GRID_CHARGING"


@dataclass
class Observation:
    huawei_soc_pct: float
    victron_soc_pct: float
    huawei_power_w: float
    victron_power_w: float
    pv_power_w: float
    emma_load_power_w: float
    victron_consumption_w: float
    huawei_available: bool
    victron_available: bool
    timestamp: float

    @property
    def pool_soc(self) -> float:
        return (
            self.huawei_soc_pct * HUAWEI_CAPACITY_KWH
            + self.victron_soc_pct * VICTRON_CAPACITY_KWH
        ) / TOTAL_CAPACITY_KWH

    @property
    def soc_delta(self) -> float:
        return abs(self.huawei_soc_pct - self.victron_soc_pct)

    @property
    def true_consumption_w(self) -> float:
        return self.emma_load_power_w + self.victron_consumption_w


@dataclass
class InterventionAction:
    target_system: str  # "huawei" or "victron"
    target_state: BatteryState
    max_discharge_power_w: int | None = None
    target_soc_pct: float | None = None
    charge_power_w: int | None = None


@dataclass
class InterventionRecord:
    timestamp: str
    intervention_type: str
    target_system: str
    action: BatteryState
    reason: str


@dataclass
class SupervisorState:
    pool_soc_pct: float
    huawei_soc_pct: float
    victron_soc_pct: float
    soc_delta: float
    huawei_state: BatteryState
    victron_state: BatteryState
    huawei_available: bool
    victron_available: bool
    true_consumption_w: float
    pv_power_w: float
    active_interventions: list[InterventionRecord] = field(default_factory=list)
    timestamp: float = 0.0
    grid_charge_slot_active: bool = False
    control_mode: str = "supervisory"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_supervisor_model.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/supervisor_model.py tests/test_supervisor_model.py
git commit -m "feat: add supervisor data models (BatteryState, Observation, SupervisorState)"
```

---

### Task 2: Supervisory Config

**Files:**
- Modify: `backend/config.py`
- Create: `tests/test_supervisory_config.py`

- [ ] **Step 1: Write tests for SupervisoryConfig**

```python
# tests/test_supervisory_config.py
from __future__ import annotations

import os
from unittest.mock import patch

from backend.config import SupervisoryConfig


class TestSupervisoryConfig:
    def test_defaults(self) -> None:
        cfg = SupervisoryConfig()
        assert cfg.control_mode == "supervisory"
        assert cfg.observation_interval_s == 5.0
        assert cfg.soc_balance_threshold_pct == 10.0
        assert cfg.soc_balance_hysteresis_pct == 5.0
        assert cfg.min_soc_pct == 10.0
        assert cfg.min_soc_hysteresis_pct == 5.0

    def test_from_env(self) -> None:
        env = {
            "EMS_CONTROL_MODE": "legacy",
            "EMS_OBSERVATION_INTERVAL_S": "10",
            "EMS_SOC_BALANCE_THRESHOLD_PCT": "15",
            "EMS_SOC_BALANCE_HYSTERESIS_PCT": "7",
            "EMS_MIN_SOC_PCT": "12",
            "EMS_MIN_SOC_HYSTERESIS_PCT": "3",
        }
        with patch.dict(os.environ, env):
            cfg = SupervisoryConfig.from_env()
        assert cfg.control_mode == "legacy"
        assert cfg.observation_interval_s == 10.0
        assert cfg.soc_balance_threshold_pct == 15.0
        assert cfg.soc_balance_hysteresis_pct == 7.0
        assert cfg.min_soc_pct == 12.0
        assert cfg.min_soc_hysteresis_pct == 3.0

    def test_from_env_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            cfg = SupervisoryConfig.from_env()
        assert cfg.control_mode == "supervisory"
        assert cfg.observation_interval_s == 5.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_supervisory_config.py -v`
Expected: `ImportError: cannot import name 'SupervisoryConfig'`

- [ ] **Step 3: Add SupervisoryConfig to config.py**

Add at the end of `backend/config.py` (after existing config dataclasses):

```python
@dataclass
class SupervisoryConfig:
    control_mode: str = "supervisory"
    observation_interval_s: float = 5.0
    soc_balance_threshold_pct: float = 10.0
    soc_balance_hysteresis_pct: float = 5.0
    min_soc_pct: float = 10.0
    min_soc_hysteresis_pct: float = 5.0

    @classmethod
    def from_env(cls) -> SupervisoryConfig:
        return cls(
            control_mode=os.environ.get("EMS_CONTROL_MODE", "supervisory"),
            observation_interval_s=float(
                os.environ.get("EMS_OBSERVATION_INTERVAL_S", "5")
            ),
            soc_balance_threshold_pct=float(
                os.environ.get("EMS_SOC_BALANCE_THRESHOLD_PCT", "10")
            ),
            soc_balance_hysteresis_pct=float(
                os.environ.get("EMS_SOC_BALANCE_HYSTERESIS_PCT", "5")
            ),
            min_soc_pct=float(os.environ.get("EMS_MIN_SOC_PCT", "10")),
            min_soc_hysteresis_pct=float(
                os.environ.get("EMS_MIN_SOC_HYSTERESIS_PCT", "5")
            ),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_supervisory_config.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/config.py tests/test_supervisory_config.py
git commit -m "feat: add SupervisoryConfig with control_mode and intervention thresholds"
```

---

### Task 3: Min-SoC Guard Intervention

**Files:**
- Create: `backend/interventions.py`
- Create: `tests/test_interventions.py`

- [ ] **Step 1: Write tests for min_soc_guard**

```python
# tests/test_interventions.py
from __future__ import annotations

from backend.interventions import check_min_soc
from backend.supervisor_model import BatteryState, Observation


def _obs(
    huawei_soc: float = 50.0,
    victron_soc: float = 50.0,
    **kwargs,
) -> Observation:
    defaults = dict(
        huawei_soc_pct=huawei_soc,
        victron_soc_pct=victron_soc,
        huawei_power_w=0.0,
        victron_power_w=0.0,
        pv_power_w=0.0,
        emma_load_power_w=0.0,
        victron_consumption_w=0.0,
        huawei_available=True,
        victron_available=True,
        timestamp=1000.0,
    )
    defaults.update(kwargs)
    return Observation(**defaults)


class TestMinSocGuard:
    def test_no_action_when_both_above_min(self) -> None:
        actions = check_min_soc(
            _obs(huawei_soc=50, victron_soc=50),
            min_soc_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
        )
        assert actions == []

    def test_hold_huawei_below_min(self) -> None:
        actions = check_min_soc(
            _obs(huawei_soc=8, victron_soc=50),
            min_soc_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
        )
        assert len(actions) == 1
        assert actions[0].target_system == "huawei"
        assert actions[0].target_state == BatteryState.HELD

    def test_hold_victron_below_min(self) -> None:
        actions = check_min_soc(
            _obs(huawei_soc=50, victron_soc=5),
            min_soc_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
        )
        assert len(actions) == 1
        assert actions[0].target_system == "victron"
        assert actions[0].target_state == BatteryState.HELD

    def test_hold_both_below_min(self) -> None:
        actions = check_min_soc(
            _obs(huawei_soc=5, victron_soc=5),
            min_soc_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
        )
        assert len(actions) == 2

    def test_release_requires_hysteresis(self) -> None:
        """Already HELD at 12% — must reach 15% (min 10 + hysteresis 5) to release."""
        actions = check_min_soc(
            _obs(huawei_soc=12, victron_soc=50),
            min_soc_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.HELD,
            victron_state=BatteryState.AUTONOMOUS,
        )
        # Still below release threshold (15%), keep held
        assert len(actions) == 1
        assert actions[0].target_system == "huawei"
        assert actions[0].target_state == BatteryState.HELD

    def test_release_above_hysteresis(self) -> None:
        """HELD at 16% with min=10, hysteresis=5 → release threshold is 15%, so release."""
        actions = check_min_soc(
            _obs(huawei_soc=16, victron_soc=50),
            min_soc_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.HELD,
            victron_state=BatteryState.AUTONOMOUS,
        )
        assert actions == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_interventions.py::TestMinSocGuard -v`
Expected: `ModuleNotFoundError: No module named 'backend.interventions'`

- [ ] **Step 3: Implement check_min_soc**

```python
# backend/interventions.py
from __future__ import annotations

from backend.supervisor_model import BatteryState, InterventionAction, Observation


def check_min_soc(
    obs: Observation,
    min_soc_pct: float,
    hysteresis_pct: float,
    huawei_state: BatteryState,
    victron_state: BatteryState,
) -> list[InterventionAction]:
    """Hold batteries below min SoC. Release only above min + hysteresis."""
    release_threshold = min_soc_pct + hysteresis_pct
    actions: list[InterventionAction] = []

    for system, soc, current_state in [
        ("huawei", obs.huawei_soc_pct, huawei_state),
        ("victron", obs.victron_soc_pct, victron_state),
    ]:
        if soc < min_soc_pct:
            actions.append(
                InterventionAction(
                    target_system=system, target_state=BatteryState.HELD
                )
            )
        elif current_state == BatteryState.HELD and soc < release_threshold:
            # Keep held until hysteresis clears
            actions.append(
                InterventionAction(
                    target_system=system, target_state=BatteryState.HELD
                )
            )
        # else: no action (autonomous or above release threshold)

    return actions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_interventions.py::TestMinSocGuard -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/interventions.py tests/test_interventions.py
git commit -m "feat: add min-SoC guard intervention"
```

---

### Task 4: Cross-Charge Prevention Intervention

**Files:**
- Modify: `backend/interventions.py`
- Modify: `tests/test_interventions.py`

- [ ] **Step 1: Write tests for check_cross_charge**

Add to `tests/test_interventions.py`:

```python
from backend.interventions import check_cross_charge


class TestCrossChargePrevention:
    def test_no_action_both_discharging(self) -> None:
        """Both batteries discharging — no cross-charge."""
        actions = check_cross_charge(
            _obs(huawei_power=-1000, victron_power=-2000, pv_power=0),
            consecutive_clear_count=0,
        )
        assert actions == []

    def test_no_action_charging_with_pv(self) -> None:
        """One charging but PV is above 100W — solar charge, not grid cross-charge."""
        actions = check_cross_charge(
            _obs(huawei_power=-1000, victron_power=500, pv_power=2000),
            consecutive_clear_count=0,
        )
        assert actions == []

    def test_detect_victron_charging_from_grid(self) -> None:
        """Huawei discharging, Victron charging, PV < 100W → hold Victron."""
        obs = _obs(huawei_power=-1000, victron_power=500, pv_power=50)
        actions = check_cross_charge(obs, consecutive_clear_count=0)
        assert len(actions) == 1
        assert actions[0].target_system == "victron"
        assert actions[0].target_state == BatteryState.HELD

    def test_detect_huawei_charging_from_grid(self) -> None:
        """Victron discharging, Huawei charging, PV < 100W → hold Huawei."""
        obs = _obs(huawei_power=500, victron_power=-1000, pv_power=50)
        actions = check_cross_charge(obs, consecutive_clear_count=0)
        assert len(actions) == 1
        assert actions[0].target_system == "huawei"
        assert actions[0].target_state == BatteryState.HELD

    def test_no_action_both_charging(self) -> None:
        """Both batteries charging — not a cross-charge scenario."""
        actions = check_cross_charge(
            _obs(huawei_power=500, victron_power=500, pv_power=0),
            consecutive_clear_count=0,
        )
        assert actions == []
```

Note: update the `_obs` helper to accept `huawei_power` and `victron_power` kwargs:

```python
def _obs(
    huawei_soc: float = 50.0,
    victron_soc: float = 50.0,
    huawei_power: float = 0.0,
    victron_power: float = 0.0,
    pv_power: float = 0.0,
    **kwargs,
) -> Observation:
    defaults = dict(
        huawei_soc_pct=huawei_soc,
        victron_soc_pct=victron_soc,
        huawei_power_w=huawei_power,
        victron_power_w=victron_power,
        pv_power_w=pv_power,
        emma_load_power_w=0.0,
        victron_consumption_w=0.0,
        huawei_available=True,
        victron_available=True,
        timestamp=1000.0,
    )
    defaults.update(kwargs)
    return Observation(**defaults)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_interventions.py::TestCrossChargePrevention -v`
Expected: `ImportError: cannot import name 'check_cross_charge'`

- [ ] **Step 3: Implement check_cross_charge**

Add to `backend/interventions.py`:

```python
PV_THRESHOLD_W = 100.0


def check_cross_charge(
    obs: Observation,
    consecutive_clear_count: int,
) -> list[InterventionAction]:
    """Detect one battery discharging while the other charges from grid.

    Cross-charge is detected when:
    - Battery powers have opposite signs (one charging, one discharging)
    - PV power is below 100W (charge is from grid, not solar)
    """
    h_discharging = obs.huawei_power_w < 0
    v_discharging = obs.victron_power_w < 0
    h_charging = obs.huawei_power_w > 0
    v_charging = obs.victron_power_w > 0
    low_pv = obs.pv_power_w < PV_THRESHOLD_W

    if h_discharging and v_charging and low_pv:
        return [
            InterventionAction(
                target_system="victron", target_state=BatteryState.HELD
            )
        ]
    if v_discharging and h_charging and low_pv:
        return [
            InterventionAction(
                target_system="huawei", target_state=BatteryState.HELD
            )
        ]
    return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_interventions.py::TestCrossChargePrevention -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/interventions.py tests/test_interventions.py
git commit -m "feat: add cross-charge prevention intervention"
```

---

### Task 5: Grid Charge Window Intervention

**Files:**
- Modify: `backend/interventions.py`
- Modify: `tests/test_interventions.py`

- [ ] **Step 1: Write tests for check_grid_charge**

Add to `tests/test_interventions.py`:

```python
from backend.interventions import check_grid_charge
from backend.schedule_models import ChargeSlot


def _make_slot(
    battery: str = "huawei",
    target_soc_pct: float = 80.0,
    grid_charge_power_w: int = 3000,
) -> ChargeSlot:
    from datetime import datetime, timezone

    return ChargeSlot(
        battery=battery,
        target_soc_pct=target_soc_pct,
        start_utc=datetime(2026, 3, 28, 1, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 3, 28, 5, 0, tzinfo=timezone.utc),
        grid_charge_power_w=grid_charge_power_w,
    )


class TestGridChargeWindow:
    def test_no_action_without_active_slot(self) -> None:
        actions = check_grid_charge(active_slot=None)
        assert actions == []

    def test_grid_charge_huawei(self) -> None:
        slot = _make_slot(battery="huawei", target_soc_pct=80, grid_charge_power_w=3000)
        actions = check_grid_charge(active_slot=slot)
        assert len(actions) == 1
        assert actions[0].target_system == "huawei"
        assert actions[0].target_state == BatteryState.GRID_CHARGING
        assert actions[0].target_soc_pct == 80.0
        assert actions[0].charge_power_w == 3000

    def test_grid_charge_victron(self) -> None:
        slot = _make_slot(battery="victron", target_soc_pct=90, grid_charge_power_w=5000)
        actions = check_grid_charge(active_slot=slot)
        assert len(actions) == 1
        assert actions[0].target_system == "victron"
        assert actions[0].target_state == BatteryState.GRID_CHARGING
        assert actions[0].target_soc_pct == 90.0
        assert actions[0].charge_power_w == 5000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_interventions.py::TestGridChargeWindow -v`
Expected: `ImportError: cannot import name 'check_grid_charge'`

- [ ] **Step 3: Implement check_grid_charge**

Add to `backend/interventions.py`:

```python
from backend.schedule_models import ChargeSlot


def check_grid_charge(
    active_slot: ChargeSlot | None,
) -> list[InterventionAction]:
    """Switch battery to grid-charge mode during cheap tariff window."""
    if active_slot is None:
        return []
    return [
        InterventionAction(
            target_system=active_slot.battery,
            target_state=BatteryState.GRID_CHARGING,
            target_soc_pct=active_slot.target_soc_pct,
            charge_power_w=active_slot.grid_charge_power_w,
        )
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_interventions.py::TestGridChargeWindow -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/interventions.py tests/test_interventions.py
git commit -m "feat: add grid charge window intervention"
```

---

### Task 6: SoC Balancing Intervention

**Files:**
- Modify: `backend/interventions.py`
- Modify: `tests/test_interventions.py`

- [ ] **Step 1: Write tests for check_soc_balance**

Add to `tests/test_interventions.py`:

```python
from backend.interventions import check_soc_balance


class TestSocBalancing:
    def test_no_action_within_threshold(self) -> None:
        actions = check_soc_balance(
            _obs(huawei_soc=55, victron_soc=50),
            threshold_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            balancing_active=False,
        )
        assert actions == []

    def test_throttle_huawei_when_higher(self) -> None:
        actions = check_soc_balance(
            _obs(huawei_soc=80, victron_soc=60),
            threshold_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            balancing_active=False,
        )
        assert len(actions) == 1
        assert actions[0].target_system == "huawei"
        assert actions[0].target_state == BatteryState.AUTONOMOUS
        assert actions[0].max_discharge_power_w is not None

    def test_throttle_victron_when_higher(self) -> None:
        actions = check_soc_balance(
            _obs(huawei_soc=40, victron_soc=65),
            threshold_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            balancing_active=False,
        )
        assert len(actions) == 1
        assert actions[0].target_system == "victron"
        assert actions[0].target_state == BatteryState.AUTONOMOUS
        assert actions[0].target_soc_pct is not None

    def test_release_requires_hysteresis(self) -> None:
        """Balancing active, delta=7, threshold=10, hysteresis=5 → release at <5, so keep."""
        actions = check_soc_balance(
            _obs(huawei_soc=57, victron_soc=50),
            threshold_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            balancing_active=True,
        )
        assert len(actions) == 1  # Still throttling

    def test_release_below_hysteresis(self) -> None:
        """Balancing active, delta=3, threshold=10, hysteresis=5 → release at <5, so release."""
        actions = check_soc_balance(
            _obs(huawei_soc=53, victron_soc=50),
            threshold_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            balancing_active=True,
        )
        assert actions == []

    def test_skip_if_held(self) -> None:
        """Don't balance a battery that's already HELD by a higher-priority intervention."""
        actions = check_soc_balance(
            _obs(huawei_soc=80, victron_soc=60),
            threshold_pct=10.0,
            hysteresis_pct=5.0,
            huawei_state=BatteryState.HELD,
            victron_state=BatteryState.AUTONOMOUS,
            balancing_active=False,
        )
        assert actions == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_interventions.py::TestSocBalancing -v`
Expected: `ImportError: cannot import name 'check_soc_balance'`

- [ ] **Step 3: Implement check_soc_balance**

Add to `backend/interventions.py`:

```python
HUAWEI_RATED_DISCHARGE_W = 5000
VICTRON_THROTTLE_SOC_OFFSET = 10.0


def check_soc_balance(
    obs: Observation,
    threshold_pct: float,
    hysteresis_pct: float,
    huawei_state: BatteryState,
    victron_state: BatteryState,
    balancing_active: bool,
) -> list[InterventionAction]:
    """Throttle the higher-SoC battery when delta exceeds threshold.

    Huawei: reduce max_discharge_power to 50% of rated.
    Victron: raise ESS min-SoC floor to current SoC minus offset.
    """
    delta = obs.soc_delta
    release_threshold = threshold_pct - hysteresis_pct

    if balancing_active and delta < release_threshold:
        return []
    if not balancing_active and delta <= threshold_pct:
        return []

    # Determine which system is higher
    if obs.huawei_soc_pct > obs.victron_soc_pct:
        if huawei_state == BatteryState.HELD:
            return []
        return [
            InterventionAction(
                target_system="huawei",
                target_state=BatteryState.AUTONOMOUS,
                max_discharge_power_w=HUAWEI_RATED_DISCHARGE_W // 2,
            )
        ]
    else:
        if victron_state == BatteryState.HELD:
            return []
        floor_soc = max(0.0, obs.victron_soc_pct - VICTRON_THROTTLE_SOC_OFFSET)
        return [
            InterventionAction(
                target_system="victron",
                target_state=BatteryState.AUTONOMOUS,
                target_soc_pct=floor_soc,
            )
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_interventions.py::TestSocBalancing -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/interventions.py tests/test_interventions.py
git commit -m "feat: add SoC balancing intervention"
```

---

### Task 7: Intervention Engine (Priority-Ordered Evaluation)

**Files:**
- Modify: `backend/interventions.py`
- Modify: `tests/test_interventions.py`

- [ ] **Step 1: Write tests for evaluate_interventions**

Add to `tests/test_interventions.py`:

```python
from backend.interventions import evaluate_interventions


class TestEvaluateInterventions:
    def test_no_interventions_normal_state(self) -> None:
        """Normal operation: all SoCs fine, no cross-charge, no tariff slot."""
        result = evaluate_interventions(
            obs=_obs(huawei_soc=50, victron_soc=50),
            min_soc_pct=10.0,
            min_soc_hysteresis_pct=5.0,
            soc_balance_threshold_pct=10.0,
            soc_balance_hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            active_slot=None,
            cross_charge_clear_count=0,
            balancing_active=False,
        )
        assert result.huawei_state == BatteryState.AUTONOMOUS
        assert result.victron_state == BatteryState.AUTONOMOUS
        assert result.actions == []

    def test_min_soc_overrides_soc_balance(self) -> None:
        """Min-SoC guard (priority 1) holds Huawei even though SoC balance (priority 4) would throttle Victron."""
        result = evaluate_interventions(
            obs=_obs(huawei_soc=5, victron_soc=30),
            min_soc_pct=10.0,
            min_soc_hysteresis_pct=5.0,
            soc_balance_threshold_pct=10.0,
            soc_balance_hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            active_slot=None,
            cross_charge_clear_count=0,
            balancing_active=False,
        )
        assert result.huawei_state == BatteryState.HELD
        # Victron should still get balanced (delta=25 > threshold=10)
        assert result.victron_state == BatteryState.AUTONOMOUS

    def test_grid_charge_applied(self) -> None:
        slot = _make_slot(battery="huawei", target_soc_pct=80, grid_charge_power_w=3000)
        result = evaluate_interventions(
            obs=_obs(huawei_soc=50, victron_soc=50),
            min_soc_pct=10.0,
            min_soc_hysteresis_pct=5.0,
            soc_balance_threshold_pct=10.0,
            soc_balance_hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            active_slot=slot,
            cross_charge_clear_count=0,
            balancing_active=False,
        )
        assert result.huawei_state == BatteryState.GRID_CHARGING
        assert result.victron_state == BatteryState.AUTONOMOUS

    def test_cross_charge_holds_charging_system(self) -> None:
        result = evaluate_interventions(
            obs=_obs(huawei_soc=50, victron_soc=50, huawei_power=-1000, victron_power=500, pv_power=0),
            min_soc_pct=10.0,
            min_soc_hysteresis_pct=5.0,
            soc_balance_threshold_pct=10.0,
            soc_balance_hysteresis_pct=5.0,
            huawei_state=BatteryState.AUTONOMOUS,
            victron_state=BatteryState.AUTONOMOUS,
            active_slot=None,
            cross_charge_clear_count=0,
            balancing_active=False,
        )
        assert result.victron_state == BatteryState.HELD
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_interventions.py::TestEvaluateInterventions -v`
Expected: `ImportError: cannot import name 'evaluate_interventions'`

- [ ] **Step 3: Implement evaluate_interventions**

Add to `backend/interventions.py`:

```python
from dataclasses import dataclass, field


@dataclass
class InterventionResult:
    huawei_state: BatteryState
    victron_state: BatteryState
    actions: list[InterventionAction] = field(default_factory=list)
    huawei_max_discharge_w: int | None = None
    victron_target_soc_pct: float | None = None
    huawei_charge_power_w: int | None = None
    huawei_charge_target_soc_pct: float | None = None
    victron_charge_power_w: int | None = None
    victron_charge_target_soc_pct: float | None = None
    balancing_active: bool = False


def evaluate_interventions(
    obs: Observation,
    min_soc_pct: float,
    min_soc_hysteresis_pct: float,
    soc_balance_threshold_pct: float,
    soc_balance_hysteresis_pct: float,
    huawei_state: BatteryState,
    victron_state: BatteryState,
    active_slot: ChargeSlot | None,
    cross_charge_clear_count: int,
    balancing_active: bool,
) -> InterventionResult:
    """Evaluate all interventions in priority order, return resolved states."""
    h_state = BatteryState.AUTONOMOUS
    v_state = BatteryState.AUTONOMOUS
    all_actions: list[InterventionAction] = []
    result = InterventionResult(huawei_state=h_state, victron_state=v_state)

    # Priority 1: Min-SoC Guard
    for action in check_min_soc(obs, min_soc_pct, min_soc_hysteresis_pct, huawei_state, victron_state):
        if action.target_system == "huawei":
            h_state = action.target_state
        else:
            v_state = action.target_state
        all_actions.append(action)

    # Priority 2: Cross-Charge Prevention (only for systems not already held)
    if h_state != BatteryState.HELD and v_state != BatteryState.HELD:
        for action in check_cross_charge(obs, cross_charge_clear_count):
            if action.target_system == "huawei" and h_state == BatteryState.AUTONOMOUS:
                h_state = action.target_state
                all_actions.append(action)
            elif action.target_system == "victron" and v_state == BatteryState.AUTONOMOUS:
                v_state = action.target_state
                all_actions.append(action)

    # Priority 3: Grid Charge Window (only for systems not held)
    for action in check_grid_charge(active_slot):
        if action.target_system == "huawei" and h_state == BatteryState.AUTONOMOUS:
            h_state = action.target_state
            result.huawei_charge_power_w = action.charge_power_w
            result.huawei_charge_target_soc_pct = action.target_soc_pct
            all_actions.append(action)
        elif action.target_system == "victron" and v_state == BatteryState.AUTONOMOUS:
            v_state = action.target_state
            result.victron_charge_power_w = action.charge_power_w
            result.victron_charge_target_soc_pct = action.target_soc_pct
            all_actions.append(action)

    # Priority 4: SoC Balancing (only for autonomous systems)
    balance_actions = check_soc_balance(
        obs, soc_balance_threshold_pct, soc_balance_hysteresis_pct,
        h_state, v_state, balancing_active,
    )
    for action in balance_actions:
        if action.target_system == "huawei" and h_state == BatteryState.AUTONOMOUS:
            result.huawei_max_discharge_w = action.max_discharge_power_w
            all_actions.append(action)
        elif action.target_system == "victron" and v_state == BatteryState.AUTONOMOUS:
            result.victron_target_soc_pct = action.target_soc_pct
            all_actions.append(action)
    result.balancing_active = len(balance_actions) > 0

    result.huawei_state = h_state
    result.victron_state = v_state
    result.actions = all_actions
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_interventions.py -v`
Expected: All 23 tests PASS (6 min-soc + 5 cross-charge + 3 grid-charge + 6 soc-balance + 4 evaluate = ~24)

- [ ] **Step 5: Commit**

```bash
git add backend/interventions.py tests/test_interventions.py
git commit -m "feat: add intervention engine with priority-ordered evaluation"
```

---

### Task 8: Supervisor Class

**Files:**
- Create: `backend/supervisor.py`
- Create: `tests/test_supervisor.py`

- [ ] **Step 1: Write tests for Supervisor**

```python
# tests/test_supervisor.py
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.config import SupervisoryConfig, OrchestratorConfig, SystemConfig
from backend.controller_model import BatteryRole, ControllerSnapshot
from backend.supervisor import Supervisor
from backend.supervisor_model import BatteryState


def _snap(
    soc: float = 50.0,
    power: float = 0.0,
    available: bool = True,
    pv_power: float = 0.0,
    grid_power: float | None = None,
    consumption: float | None = None,
    load_power: float | None = None,
) -> ControllerSnapshot:
    return ControllerSnapshot(
        soc_pct=soc,
        power_w=power,
        available=available,
        role=BatteryRole.HOLDING,
        consecutive_failures=0,
        timestamp=1000.0,
        pv_input_power_w=int(pv_power) if pv_power else None,
        grid_power_w=grid_power,
        consumption_w=consumption,
        master_active_power_w=load_power,
    )


def _make_supervisor(
    supervisory_config: SupervisoryConfig | None = None,
) -> tuple[Supervisor, AsyncMock, AsyncMock]:
    h_ctrl = AsyncMock()
    v_ctrl = AsyncMock()
    h_ctrl.poll = AsyncMock(return_value=_snap(soc=50, power=-1000, pv_power=3000, load_power=4000))
    v_ctrl.poll = AsyncMock(return_value=_snap(soc=50, power=-2000, consumption=1500))

    sup = Supervisor(
        huawei_ctrl=h_ctrl,
        victron_ctrl=v_ctrl,
        supervisory_config=supervisory_config or SupervisoryConfig(),
        orch_config=OrchestratorConfig(),
        sys_config=SystemConfig(),
    )
    return sup, h_ctrl, v_ctrl


class TestSupervisorObserve:
    @pytest.mark.anyio
    async def test_observe_reads_both_controllers(self) -> None:
        sup, h_ctrl, v_ctrl = _make_supervisor()
        obs = await sup._observe()
        h_ctrl.poll.assert_awaited_once()
        v_ctrl.poll.assert_awaited_once()
        assert obs.huawei_soc_pct == 50.0
        assert obs.victron_soc_pct == 50.0

    @pytest.mark.anyio
    async def test_observe_extracts_pv_power(self) -> None:
        sup, _, _ = _make_supervisor()
        obs = await sup._observe()
        assert obs.pv_power_w == 3000.0

    @pytest.mark.anyio
    async def test_observe_handles_unavailable_huawei(self) -> None:
        sup, h_ctrl, _ = _make_supervisor()
        h_ctrl.poll = AsyncMock(return_value=_snap(available=False, soc=0))
        obs = await sup._observe()
        assert obs.huawei_available is False


class TestSupervisorCycle:
    @pytest.mark.anyio
    async def test_normal_state_no_writes(self) -> None:
        """When both batteries are healthy and balanced, no writes should happen."""
        sup, h_ctrl, v_ctrl = _make_supervisor()
        await sup._run_cycle()
        h_ctrl.execute.assert_not_awaited()
        v_ctrl.execute.assert_not_awaited()

    @pytest.mark.anyio
    async def test_min_soc_holds_battery(self) -> None:
        """When Huawei SoC drops below min, supervisor should write hold command."""
        sup, h_ctrl, v_ctrl = _make_supervisor()
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=5, power=-1000, pv_power=0, load_power=2000))
        await sup._run_cycle()
        h_ctrl.execute.assert_awaited_once()
        cmd = h_ctrl.execute.call_args[0][0]
        assert cmd.role == BatteryRole.HOLDING
        assert cmd.target_watts == 0

    @pytest.mark.anyio
    async def test_state_reflects_intervention(self) -> None:
        sup, h_ctrl, _ = _make_supervisor()
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=5, power=-1000, pv_power=0, load_power=2000))
        await sup._run_cycle()
        state = sup.get_state()
        assert state is not None
        assert state.huawei_state == BatteryState.HELD

    @pytest.mark.anyio
    async def test_cross_charge_holds_charging_system(self) -> None:
        sup, h_ctrl, v_ctrl = _make_supervisor()
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=50, power=-1000, pv_power=50, load_power=2000))
        v_ctrl.poll = AsyncMock(return_value=_snap(soc=50, power=500, consumption=1000))
        await sup._run_cycle()
        v_ctrl.execute.assert_awaited_once()
        cmd = v_ctrl.execute.call_args[0][0]
        assert cmd.role == BatteryRole.HOLDING

    @pytest.mark.anyio
    async def test_get_interventions_returns_history(self) -> None:
        sup, h_ctrl, _ = _make_supervisor()
        h_ctrl.poll = AsyncMock(return_value=_snap(soc=5, power=-1000, pv_power=0, load_power=2000))
        await sup._run_cycle()
        interventions = sup.get_interventions(limit=10)
        assert len(interventions) >= 1
        assert interventions[0]["intervention_type"] == "min_soc_guard"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_supervisor.py -v`
Expected: `ModuleNotFoundError: No module named 'backend.supervisor'`

- [ ] **Step 3: Implement Supervisor class**

```python
# backend/supervisor.py
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections import deque
from datetime import datetime, timezone

from backend.config import OrchestratorConfig, SupervisoryConfig, SystemConfig
from backend.controller_model import BatteryRole, ControllerCommand, ControllerSnapshot
from backend.interventions import InterventionResult, evaluate_interventions
from backend.supervisor_model import (
    BatteryState,
    InterventionRecord,
    Observation,
    SupervisorState,
)

logger = logging.getLogger(__name__)

_MAX_INTERVENTION_HISTORY = 100


class Supervisor:
    """Supervisory EMS: observe batteries, intervene only on trigger."""

    def __init__(
        self,
        huawei_ctrl,
        victron_ctrl,
        supervisory_config: SupervisoryConfig,
        orch_config: OrchestratorConfig,
        sys_config: SystemConfig,
        writer=None,
    ) -> None:
        self._h_ctrl = huawei_ctrl
        self._v_ctrl = victron_ctrl
        self._sup_config = supervisory_config
        self._orch_config = orch_config
        self._sys_config = sys_config
        self._writer = writer

        self._state: SupervisorState | None = None
        self._huawei_state = BatteryState.AUTONOMOUS
        self._victron_state = BatteryState.AUTONOMOUS
        self._interventions: deque[InterventionRecord] = deque(maxlen=_MAX_INTERVENTION_HISTORY)
        self._cross_charge_clear_count = 0
        self._balancing_active = False
        self._task: asyncio.Task | None = None
        self._scheduler = None
        self._last_error: str | None = None

    # --- Injected services ---

    def set_scheduler(self, scheduler) -> None:
        self._scheduler = scheduler

    def set_notifier(self, notifier) -> None:
        self._notifier = notifier

    def set_ha_mqtt_client(self, client) -> None:
        self._ha_mqtt = client

    # --- Lifecycle ---

    async def start(self) -> None:
        logger.info("Supervisor starting (interval=%.1fs)", self._sup_config.observation_interval_s)
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Supervisor stopped")

    # --- Public accessors ---

    def get_state(self) -> SupervisorState | None:
        return self._state

    def get_interventions(self, limit: int = 20) -> list[dict]:
        items = list(self._interventions)[-limit:]
        return [dataclasses.asdict(r) for r in items]

    def get_last_error(self) -> str | None:
        return self._last_error

    # --- Internal loop ---

    async def _loop(self) -> None:
        while True:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Supervisor cycle failed")
                self._last_error = "cycle_exception"
            await asyncio.sleep(self._sup_config.observation_interval_s)

    async def _run_cycle(self) -> None:
        obs = await self._observe()

        # Get active charge slot from scheduler
        active_slot = None
        if self._scheduler is not None:
            active_slot = self._scheduler.active_charge_slot()

        # Evaluate interventions
        result = evaluate_interventions(
            obs=obs,
            min_soc_pct=self._sup_config.min_soc_pct,
            min_soc_hysteresis_pct=self._sup_config.min_soc_hysteresis_pct,
            soc_balance_threshold_pct=self._sup_config.soc_balance_threshold_pct,
            soc_balance_hysteresis_pct=self._sup_config.soc_balance_hysteresis_pct,
            huawei_state=self._huawei_state,
            victron_state=self._victron_state,
            active_slot=active_slot,
            cross_charge_clear_count=self._cross_charge_clear_count,
            balancing_active=self._balancing_active,
        )

        # Apply state changes
        await self._apply_result(result, obs)

        # Update internal state
        self._huawei_state = result.huawei_state
        self._victron_state = result.victron_state
        self._balancing_active = result.balancing_active

        # Track cross-charge debounce
        if not any(a.target_state == BatteryState.HELD for a in result.actions
                   if a.target_system in ("huawei", "victron")):
            self._cross_charge_clear_count += 1
        else:
            self._cross_charge_clear_count = 0

        # Record interventions
        now = datetime.now(tz=timezone.utc).isoformat()
        for action in result.actions:
            record = InterventionRecord(
                timestamp=now,
                intervention_type=self._classify_action(action, result),
                target_system=action.target_system,
                action=action.target_state,
                reason=self._describe_action(action, obs),
            )
            self._interventions.append(record)

        # Build supervisor state
        self._state = SupervisorState(
            pool_soc_pct=obs.pool_soc,
            huawei_soc_pct=obs.huawei_soc_pct,
            victron_soc_pct=obs.victron_soc_pct,
            soc_delta=obs.soc_delta,
            huawei_state=result.huawei_state,
            victron_state=result.victron_state,
            huawei_available=obs.huawei_available,
            victron_available=obs.victron_available,
            true_consumption_w=obs.true_consumption_w,
            pv_power_w=obs.pv_power_w,
            active_interventions=list(self._interventions)[-5:],
            timestamp=time.monotonic(),
            grid_charge_slot_active=active_slot is not None,
        )
        self._last_error = None

    async def _observe(self) -> Observation:
        h_snap = await self._h_ctrl.poll()
        v_snap = await self._v_ctrl.poll()
        return Observation(
            huawei_soc_pct=h_snap.soc_pct,
            victron_soc_pct=v_snap.soc_pct,
            huawei_power_w=h_snap.power_w,
            victron_power_w=v_snap.power_w,
            pv_power_w=float(h_snap.pv_input_power_w or 0),
            emma_load_power_w=float(h_snap.master_active_power_w or 0),
            victron_consumption_w=float(v_snap.consumption_w or 0),
            huawei_available=h_snap.available,
            victron_available=v_snap.available,
            timestamp=time.monotonic(),
        )

    async def _apply_result(
        self, result: InterventionResult, obs: Observation
    ) -> None:
        """Write commands to controllers only when state changes."""
        if result.huawei_state != self._huawei_state or result.huawei_max_discharge_w is not None:
            cmd = self._build_huawei_command(result)
            await self._h_ctrl.execute(cmd)

        if result.victron_state != self._victron_state or result.victron_target_soc_pct is not None:
            cmd = self._build_victron_command(result)
            await self._v_ctrl.execute(cmd)

    def _build_huawei_command(self, result: InterventionResult) -> ControllerCommand:
        if result.huawei_state == BatteryState.HELD:
            return ControllerCommand(role=BatteryRole.HOLDING, target_watts=0)
        if result.huawei_state == BatteryState.GRID_CHARGING:
            return ControllerCommand(
                role=BatteryRole.GRID_CHARGE,
                target_watts=float(result.huawei_charge_power_w or 3000),
            )
        if result.huawei_max_discharge_w is not None:
            return ControllerCommand(
                role=BatteryRole.PRIMARY_DISCHARGE,
                target_watts=float(-result.huawei_max_discharge_w),
            )
        return ControllerCommand(role=BatteryRole.HOLDING, target_watts=0)

    def _build_victron_command(self, result: InterventionResult) -> ControllerCommand:
        if result.victron_state == BatteryState.HELD:
            return ControllerCommand(role=BatteryRole.HOLDING, target_watts=0)
        if result.victron_state == BatteryState.GRID_CHARGING:
            return ControllerCommand(
                role=BatteryRole.GRID_CHARGE,
                target_watts=float(result.victron_charge_power_w or 5000),
            )
        if result.victron_target_soc_pct is not None:
            # Victron ESS: we set min-SoC floor, not a power setpoint
            return ControllerCommand(
                role=BatteryRole.PRIMARY_DISCHARGE,
                target_watts=0,  # ESS handles its own power
            )
        return ControllerCommand(role=BatteryRole.HOLDING, target_watts=0)

    def _classify_action(self, action, result: InterventionResult) -> str:
        if action.target_state == BatteryState.GRID_CHARGING:
            return "grid_charge_window"
        if action.target_state == BatteryState.HELD:
            return "min_soc_guard" if action.max_discharge_power_w is None else "cross_charge"
        if action.max_discharge_power_w is not None or action.target_soc_pct is not None:
            return "soc_balance"
        return "unknown"

    def _describe_action(self, action, obs: Observation) -> str:
        if action.target_state == BatteryState.HELD:
            return f"{action.target_system} held: SoC={getattr(obs, f'{action.target_system}_soc_pct', '?')}%"
        if action.target_state == BatteryState.GRID_CHARGING:
            return f"{action.target_system} grid charging: target={action.target_soc_pct}%"
        if action.max_discharge_power_w is not None:
            return f"{action.target_system} throttled to {action.max_discharge_power_w}W (SoC balance)"
        return f"{action.target_system} → {action.target_state}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_supervisor.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/supervisor.py tests/test_supervisor.py
git commit -m "feat: add Supervisor class with observation loop and intervention engine"
```

---

### Task 9: Add-on Config and Env Mapping

**Files:**
- Modify: `ems/config.yaml`
- Modify: `ems/run.sh`

- [ ] **Step 1: Read current config.yaml**

Run: `cat ems/config.yaml` to see current structure and find where to add new options.

- [ ] **Step 2: Add new config options to ems/config.yaml**

Add under the `schema` section, in the "Coordinator tuning" area:

```yaml
control_mode:
  name: Control Mode
  description: >-
    "supervisory" lets native battery controllers run autonomously with EMS guardrails.
    "legacy" uses the original setpoint control loop.
  default: supervisory
  type: list
  options:
    - supervisory
    - legacy
observation_interval_s:
  name: Observation Interval (seconds)
  description: How often the EMS reads battery state. Lower = faster detection, higher = less Modbus traffic.
  default: 5
  type: integer
  range:
    min: 2
    max: 60
soc_balance_threshold:
  name: SoC Balance Threshold (%)
  description: SoC difference between batteries that triggers balancing.
  default: 10
  type: integer
  range:
    min: 5
    max: 30
soc_balance_hysteresis:
  name: SoC Balance Hysteresis (%)
  description: SoC delta must drop below threshold minus this value to release balancing.
  default: 5
  type: integer
  range:
    min: 2
    max: 15
min_soc_hysteresis:
  name: Min SoC Hysteresis (%)
  description: SoC must recover above min + this value to resume discharge.
  default: 5
  type: integer
  range:
    min: 2
    max: 15
```

- [ ] **Step 3: Add env mappings to ems/run.sh**

Add after the existing env mappings:

```bash
export EMS_CONTROL_MODE="$(bashio::config 'control_mode' 'supervisory')"
export EMS_OBSERVATION_INTERVAL_S="$(bashio::config 'observation_interval_s' '5')"
export EMS_SOC_BALANCE_THRESHOLD_PCT="$(bashio::config 'soc_balance_threshold' '10')"
export EMS_SOC_BALANCE_HYSTERESIS_PCT="$(bashio::config 'soc_balance_hysteresis' '5')"
export EMS_MIN_SOC_HYSTERESIS_PCT="$(bashio::config 'min_soc_hysteresis' '5')"
```

- [ ] **Step 4: Commit**

```bash
git add ems/config.yaml ems/run.sh
git commit -m "feat(addon): add supervisory control mode config options"
```

---

### Task 10: Orchestrator Branching

**Files:**
- Modify: `backend/orchestrator.py`
- Modify: `backend/main.py` (if coordinator is created there)

- [ ] **Step 1: Read orchestrator.py and main.py to find where Coordinator is created**

Run: `grep -n "Coordinator\|coordinator" backend/main.py backend/orchestrator.py | head -40`

Identify the exact location where the Coordinator is instantiated and started.

- [ ] **Step 2: Write test for control_mode branching**

Add to existing `tests/test_orchestrator.py` or create `tests/test_control_mode.py`:

```python
# tests/test_control_mode.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from backend.config import SupervisoryConfig


class TestControlModeBranching:
    def test_supervisory_config_from_env_defaults_to_supervisory(self) -> None:
        cfg = SupervisoryConfig()
        assert cfg.control_mode == "supervisory"

    def test_legacy_mode_creates_coordinator(self) -> None:
        cfg = SupervisoryConfig(control_mode="legacy")
        assert cfg.control_mode == "legacy"

    def test_supervisory_mode_value(self) -> None:
        cfg = SupervisoryConfig(control_mode="supervisory")
        assert cfg.control_mode == "supervisory"
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_control_mode.py -v`
Expected: All PASS

- [ ] **Step 4: Modify main.py to branch on control_mode**

Find the location in `backend/main.py` where `Coordinator` is created. Add branching:

```python
from backend.config import SupervisoryConfig
from backend.supervisor import Supervisor

supervisory_config = SupervisoryConfig.from_env()

if supervisory_config.control_mode == "supervisory":
    controller = Supervisor(
        huawei_ctrl=h_ctrl,
        victron_ctrl=v_ctrl,
        supervisory_config=supervisory_config,
        orch_config=orch_config,
        sys_config=sys_config,
        writer=writer,
    )
else:
    controller = Coordinator(
        huawei_ctrl=h_ctrl,
        victron_ctrl=v_ctrl,
        sys_config=sys_config,
        orch_config=orch_config,
        writer=writer,
        tariff_engine=tariff_engine,
    )
```

The exact edit depends on the current structure of `main.py`. Read the file first, then apply the minimal change needed to add the branch.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_control_mode.py
git commit -m "feat: add control_mode branching — supervisory or legacy coordinator"
```

---

### Task 11: API Updates

**Files:**
- Modify: `backend/api.py`

- [ ] **Step 1: Read current api.py to find decisions endpoint and state endpoint**

Run: `grep -n "decisions\|state\|interventions" backend/api.py | head -20`

- [ ] **Step 2: Add /api/interventions endpoint**

Add to `backend/api.py`:

```python
@app.get("/api/interventions")
async def get_interventions(limit: int = 20):
    """Return recent intervention records (supervisory mode only)."""
    if hasattr(app.state, "supervisor") and app.state.supervisor is not None:
        return app.state.supervisor.get_interventions(limit=min(limit, 100))
    return []
```

- [ ] **Step 3: Update /api/state to handle both modes**

Modify the existing `/api/state` handler to check for supervisor mode:

```python
@app.get("/api/state")
async def get_state():
    if hasattr(app.state, "supervisor") and app.state.supervisor is not None:
        state = app.state.supervisor.get_state()
        if state is None:
            raise HTTPException(status_code=503, detail="Supervisor not ready")
        return dataclasses.asdict(state)
    # ... existing coordinator path ...
```

- [ ] **Step 4: Run existing API tests to verify no regression**

Run: `python -m pytest tests/test_api.py -v --tb=short`
Expected: All existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api.py
git commit -m "feat(api): add /api/interventions endpoint, update /api/state for supervisor mode"
```

---

### Task 12: InfluxDB Integration

**Files:**
- Modify: `backend/influx_writer.py`

- [ ] **Step 1: Read influx_writer.py to understand write pattern**

Run: `grep -n "def write\|def _write\|measurement" backend/influx_writer.py | head -20`

- [ ] **Step 2: Add write_observation and write_intervention methods**

Add to the `InfluxMetricsWriter` class:

```python
def write_observation(self, state: "SupervisorState") -> None:
    """Write observation snapshot (every cycle in supervisory mode)."""
    if not self._enabled:
        return
    point = {
        "measurement": "ems_observation",
        "tags": {
            "huawei_state": state.huawei_state,
            "victron_state": state.victron_state,
        },
        "fields": {
            "pool_soc_pct": state.pool_soc_pct,
            "huawei_soc_pct": state.huawei_soc_pct,
            "victron_soc_pct": state.victron_soc_pct,
            "soc_delta": state.soc_delta,
            "true_consumption_w": state.true_consumption_w,
            "pv_power_w": state.pv_power_w,
        },
        "time": datetime.now(tz=timezone.utc),
    }
    self._write_point(point)

def write_intervention(self, record: "InterventionRecord") -> None:
    """Write intervention event (only when triggered)."""
    if not self._enabled:
        return
    point = {
        "measurement": "ems_intervention",
        "tags": {
            "intervention_type": record.intervention_type,
            "target_system": record.target_system,
            "action": str(record.action),
        },
        "fields": {
            "reason": record.reason,
        },
        "time": datetime.now(tz=timezone.utc),
    }
    self._write_point(point)
```

- [ ] **Step 3: Wire InfluxDB writes into Supervisor._run_cycle**

Add at the end of `Supervisor._run_cycle()` in `backend/supervisor.py`:

```python
# Write to InfluxDB
if self._writer and self._state:
    self._writer.write_observation(self._state)
    for action in result.actions:
        record = InterventionRecord(
            timestamp=now,
            intervention_type=self._classify_action(action, result),
            target_system=action.target_system,
            action=action.target_state,
            reason=self._describe_action(action, obs),
        )
        self._writer.write_intervention(record)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_supervisor.py tests/test_influx_writer.py -v --tb=short`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/influx_writer.py backend/supervisor.py
git commit -m "feat(influx): add observation and intervention measurements for supervisor mode"
```

---

### Task 13: Frontend Type Updates

**Files:**
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Read current types.ts**

Run: Review `frontend/src/types.ts` to find `DecisionEntry` and `PoolState` interfaces.

- [ ] **Step 2: Add InterventionEntry interface and BatteryState type**

Add to `frontend/src/types.ts`:

```typescript
export type BatteryState = "AUTONOMOUS" | "HELD" | "GRID_CHARGING";

export interface InterventionEntry {
  timestamp: string;
  intervention_type: "min_soc_guard" | "cross_charge" | "grid_charge_window" | "soc_balance";
  target_system: "huawei" | "victron";
  action: BatteryState;
  reason: string;
}

export interface SupervisorState {
  pool_soc_pct: number;
  huawei_soc_pct: number;
  victron_soc_pct: number;
  soc_delta: number;
  huawei_state: BatteryState;
  victron_state: BatteryState;
  huawei_available: boolean;
  victron_available: boolean;
  true_consumption_w: number;
  pv_power_w: number;
  active_interventions: InterventionEntry[];
  timestamp: number;
  grid_charge_slot_active: boolean;
  control_mode: "supervisory" | "legacy";
}
```

- [ ] **Step 3: Commit**

```bash
cd frontend && git add src/types.ts && cd ..
git commit -m "feat(frontend): add InterventionEntry, BatteryState, SupervisorState types"
```

---

### Task 14: Frontend Component Updates

**Files:**
- Modify: `frontend/src/components/BatteryStatus.tsx`
- Modify: `frontend/src/components/DecisionLog.tsx`
- Modify: `frontend/src/components/PoolOverview.tsx`

- [ ] **Step 1: Read all three component files**

Read `BatteryStatus.tsx`, `DecisionLog.tsx`, and `PoolOverview.tsx` to understand the current rendering.

- [ ] **Step 2: Update BatteryStatus.tsx role colors**

Add new state colors alongside existing role colors:

```typescript
const batteryStateColors: Record<string, string> = {
  AUTONOMOUS: "#22c55e",    // green — running freely
  HELD: "#f59e0b",          // amber — held by intervention
  GRID_CHARGING: "#06b6d4", // cyan — charging from grid
};

const batteryStateLabels: Record<string, string> = {
  AUTONOMOUS: "Autonomous",
  HELD: "Held",
  GRID_CHARGING: "Grid Charging",
};
```

In the render section, detect supervisory mode from the pool state and use the new colors:

```typescript
// If pool has huawei_state/victron_state fields, use supervisory display
const isSupervisory = pool && "huawei_state" in pool;
const huaweiLabel = isSupervisory
  ? batteryStateLabels[pool.huawei_state] || pool.huawei_state
  : roleLabels[pool?.huawei_role] || pool?.huawei_role;
```

- [ ] **Step 3: Update DecisionLog.tsx to show interventions**

Add intervention rendering alongside existing decision rendering:

```typescript
import { InterventionEntry } from "../types";

const interventionColors: Record<string, string> = {
  min_soc_guard: "#ef4444",     // red — safety
  cross_charge: "#f59e0b",      // amber — efficiency
  grid_charge_window: "#06b6d4", // cyan — tariff
  soc_balance: "#3b82f6",       // blue — pool health
};

// In the component, check if data contains interventions or decisions:
// If interventions array exists, render intervention timeline
// Otherwise, render legacy decision log
```

- [ ] **Step 4: Update PoolOverview.tsx state display**

Update the control state display to handle supervisory states. If `control_mode === "supervisory"`, show "Supervisory" as the mode indicator with a green dot.

- [ ] **Step 5: Run frontend lint**

Run: `cd frontend && npm run lint`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/BatteryStatus.tsx frontend/src/components/DecisionLog.tsx frontend/src/components/PoolOverview.tsx
git commit -m "feat(frontend): update components for supervisory mode — state badges, intervention log"
```

---

### Task 15: WebSocket Broadcast Update

**Files:**
- Modify: `backend/api.py` (WebSocket handler)

- [ ] **Step 1: Read the WebSocket broadcast section of api.py**

Find the `/api/ws/state` handler and the payload construction.

- [ ] **Step 2: Update WS payload to include supervisor state**

In the WebSocket broadcast, detect supervisor mode and include the supervisor state:

```python
# In the WS broadcast payload construction:
if hasattr(app.state, "supervisor") and app.state.supervisor is not None:
    sup_state = app.state.supervisor.get_state()
    payload["pool"] = dataclasses.asdict(sup_state) if sup_state else None
    payload["interventions"] = app.state.supervisor.get_interventions(limit=5)
```

- [ ] **Step 3: Run backend tests**

Run: `python -m pytest tests/ -q --tb=short`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add backend/api.py
git commit -m "feat(ws): include supervisor state and interventions in WebSocket broadcast"
```

---

### Task 16: HA Add-on Translation Strings

**Files:**
- Modify: `ems/translations/en.yaml`
- Modify: `ems/translations/de.yaml`

- [ ] **Step 1: Read current translation files**

Run: Review both translation files to see the existing structure.

- [ ] **Step 2: Add English translations**

Add under the `configuration` section of `ems/translations/en.yaml`:

```yaml
control_mode:
  name: Control Mode
  description: >-
    "supervisory" lets native battery controllers run autonomously with EMS guardrails.
    "legacy" uses the original setpoint control loop.
observation_interval_s:
  name: Observation Interval (seconds)
  description: How often the EMS reads battery state.
soc_balance_threshold:
  name: SoC Balance Threshold (%)
  description: SoC difference between batteries that triggers balancing.
soc_balance_hysteresis:
  name: SoC Balance Hysteresis (%)
  description: SoC delta must drop this far below threshold to release balancing.
min_soc_hysteresis:
  name: Min SoC Hysteresis (%)
  description: SoC must recover above min + this value to resume discharge.
```

- [ ] **Step 3: Add German translations**

Add under the `configuration` section of `ems/translations/de.yaml`:

```yaml
control_mode:
  name: Steuerungsmodus
  description: >-
    "supervisory" lässt die nativen Batterie-Controller autonom laufen, das EMS setzt nur Leitplanken.
    "legacy" nutzt die ursprüngliche Sollwert-Regelschleife.
observation_interval_s:
  name: Beobachtungsintervall (Sekunden)
  description: Wie oft das EMS den Batteriestatus liest.
soc_balance_threshold:
  name: SoC-Ausgleichsschwelle (%)
  description: SoC-Differenz zwischen Batterien, ab der Ausgleich ausgelöst wird.
soc_balance_hysteresis:
  name: SoC-Ausgleichs-Hysterese (%)
  description: SoC-Delta muss so weit unter den Schwellwert fallen, um den Ausgleich aufzuheben.
min_soc_hysteresis:
  name: Min-SoC-Hysterese (%)
  description: SoC muss über Min + diesen Wert steigen, um die Entladung fortzusetzen.
```

- [ ] **Step 4: Commit**

```bash
git add ems/translations/en.yaml ems/translations/de.yaml
git commit -m "feat(addon): add translation strings for supervisory config options"
```

---

### Task 17: Integration Test — Full Cycle

**Files:**
- Create: `tests/test_supervisor_integration.py`

- [ ] **Step 1: Write integration test for a full supervisor cycle**

```python
# tests/test_supervisor_integration.py
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.config import SupervisoryConfig, OrchestratorConfig, SystemConfig
from backend.controller_model import BatteryRole, ControllerSnapshot
from backend.supervisor import Supervisor
from backend.supervisor_model import BatteryState


def _snap(soc: float = 50.0, power: float = 0.0, **kwargs) -> ControllerSnapshot:
    defaults = dict(
        soc_pct=soc,
        power_w=power,
        available=True,
        role=BatteryRole.HOLDING,
        consecutive_failures=0,
        timestamp=1000.0,
        pv_input_power_w=kwargs.get("pv", 0),
        grid_power_w=kwargs.get("grid", None),
        consumption_w=kwargs.get("consumption", 0),
        master_active_power_w=kwargs.get("load", 0),
    )
    return ControllerSnapshot(**defaults)


class TestSupervisorIntegration:
    @pytest.mark.anyio
    async def test_normal_operation_no_writes(self) -> None:
        """Both batteries healthy, balanced SoC, no tariff slot → no controller writes."""
        h = AsyncMock()
        v = AsyncMock()
        h.poll = AsyncMock(return_value=_snap(soc=50, power=-1000, pv=3000, load=4000))
        v.poll = AsyncMock(return_value=_snap(soc=50, power=-2000, consumption=1500))

        sup = Supervisor(
            huawei_ctrl=h, victron_ctrl=v,
            supervisory_config=SupervisoryConfig(),
            orch_config=OrchestratorConfig(),
            sys_config=SystemConfig(),
        )
        await sup._run_cycle()

        h.execute.assert_not_awaited()
        v.execute.assert_not_awaited()
        state = sup.get_state()
        assert state.huawei_state == BatteryState.AUTONOMOUS
        assert state.victron_state == BatteryState.AUTONOMOUS

    @pytest.mark.anyio
    async def test_min_soc_then_recovery(self) -> None:
        """Huawei drops below min → held. Recovers above min+hysteresis → released."""
        h = AsyncMock()
        v = AsyncMock()
        v.poll = AsyncMock(return_value=_snap(soc=50, consumption=1000))

        sup = Supervisor(
            huawei_ctrl=h, victron_ctrl=v,
            supervisory_config=SupervisoryConfig(min_soc_pct=10.0, min_soc_hysteresis_pct=5.0),
            orch_config=OrchestratorConfig(),
            sys_config=SystemConfig(),
        )

        # Cycle 1: SoC=5% → hold
        h.poll = AsyncMock(return_value=_snap(soc=5, power=-500, pv=0, load=1000))
        await sup._run_cycle()
        assert sup.get_state().huawei_state == BatteryState.HELD

        # Cycle 2: SoC=12% → still held (below 10+5=15)
        h.poll = AsyncMock(return_value=_snap(soc=12, power=0, pv=0, load=1000))
        h.execute.reset_mock()
        await sup._run_cycle()
        assert sup.get_state().huawei_state == BatteryState.HELD

        # Cycle 3: SoC=16% → released (above 15)
        h.poll = AsyncMock(return_value=_snap(soc=16, power=0, pv=3000, load=1000))
        h.execute.reset_mock()
        await sup._run_cycle()
        assert sup.get_state().huawei_state == BatteryState.AUTONOMOUS

    @pytest.mark.anyio
    async def test_cross_charge_detected_and_cleared(self) -> None:
        """Cross-charge detected → victron held. PV returns → released after debounce."""
        h = AsyncMock()
        v = AsyncMock()

        sup = Supervisor(
            huawei_ctrl=h, victron_ctrl=v,
            supervisory_config=SupervisoryConfig(),
            orch_config=OrchestratorConfig(),
            sys_config=SystemConfig(),
        )

        # Cycle 1: Huawei discharging, Victron charging, no PV → cross-charge
        h.poll = AsyncMock(return_value=_snap(soc=50, power=-1000, pv=50, load=2000))
        v.poll = AsyncMock(return_value=_snap(soc=50, power=500, consumption=1000))
        await sup._run_cycle()
        assert sup.get_state().victron_state == BatteryState.HELD

    @pytest.mark.anyio
    async def test_intervention_history_populated(self) -> None:
        """Interventions are recorded in history."""
        h = AsyncMock()
        v = AsyncMock()
        h.poll = AsyncMock(return_value=_snap(soc=5, power=-500, pv=0, load=1000))
        v.poll = AsyncMock(return_value=_snap(soc=50, consumption=1000))

        sup = Supervisor(
            huawei_ctrl=h, victron_ctrl=v,
            supervisory_config=SupervisoryConfig(),
            orch_config=OrchestratorConfig(),
            sys_config=SystemConfig(),
        )
        await sup._run_cycle()

        history = sup.get_interventions(limit=10)
        assert len(history) >= 1
        assert history[0]["target_system"] == "huawei"
```

- [ ] **Step 2: Run integration tests**

Run: `python -m pytest tests/test_supervisor_integration.py -v`
Expected: All 4 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_supervisor_integration.py
git commit -m "test: add supervisor integration tests — full cycle, recovery, cross-charge"
```

---

### Task 18: Run Full Test Suite

**Files:** None (verification only)

- [ ] **Step 1: Run complete backend test suite**

Run: `python -m pytest tests/ -q --tb=short`
Expected: All tests PASS including existing coordinator tests (unchanged)

- [ ] **Step 2: Run frontend lint**

Run: `cd frontend && npm run lint`
Expected: No errors

- [ ] **Step 3: Run frontend build**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 4: Commit any final fixes if needed**

If any tests fail, fix the issues and commit:
```bash
git commit -m "fix: resolve test failures from supervisory integration"
```
