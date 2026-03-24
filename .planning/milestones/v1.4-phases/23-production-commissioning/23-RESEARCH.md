# Phase 23: Production Commissioning - Research

**Researched:** 2026-03-24
**Domain:** Staged rollout state machine, shadow mode, Victron watchdog guard
**Confidence:** HIGH

## Summary

Phase 23 adds three production safety mechanisms to the EMS: (1) a commissioning state machine that gates write access through READ_ONLY, SINGLE_BATTERY, and DUAL_BATTERY stages, (2) a shadow mode that logs coordinator decisions without executing hardware writes, and (3) a Victron 45-second periodic zero-write guard that prevents the Venus OS Hub4 60-second watchdog from timing out.

All three features build directly on established patterns in the codebase. The commissioning state machine follows the HuaweiModeManager state-machine pattern (enum states, `from_env()` config). Shadow mode intercepts the coordinator's execute calls at a single point. The Victron watchdog guard runs as an `asyncio.create_task()` background loop, identical to how the nightly scheduler and intraday replan loops work.

**Primary recommendation:** Implement a `CommissioningManager` class with JSON file persistence, wire it into the coordinator via `set_commissioning_manager()`, and have it gate all `controller.execute()` calls based on the current stage. The 45-second guard is a standalone background task in the Victron controller.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
None explicitly locked -- all implementation choices are at Claude's discretion.

### Claude's Discretion
All implementation choices are at Claude's discretion -- infrastructure phase. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

Key constraints:
- Staged rollout: READ_ONLY -> SINGLE_BATTERY (Victron only, since Huawei mode manager handles its own writes) -> DUAL_BATTERY
- Each stage transition requires documented criteria (configurable via env vars)
- Shadow mode: coordinator computes decisions and logs them but does NOT call controller.execute()
- Shadow mode must be configurable via env var (default: True for safety)
- Victron 45s guard: periodic zero-write to keep the 60s watchdog from firing during normal operation
- Guard runs as a background task in the coordinator, independent of the 5s control loop
- All commissioning state exposed via /api/health and HA MQTT
- Commissioning stages persist across restarts (file-based state in /config/ems_commissioning.json)

### Deferred Ideas (OUT OF SCOPE)
None -- infrastructure phase.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PROD-01 | Staged rollout: read-only -> single-battery writes -> dual-battery writes with documented progression criteria | CommissioningManager state machine with CommissioningStage enum, JSON persistence, env-var criteria thresholds |
| PROD-02 | Shadow mode logs all coordinator decisions and intended writes without executing them | Shadow mode flag on CommissioningManager; coordinator checks before calling controller.execute(); uses existing DecisionEntry with trigger="shadow_mode" |
| PROD-03 | Victron 45s emergency zero-write guard prevents 60s watchdog timeout from causing uncontrolled state | Background asyncio task in VictronController writing 0W to all 3 phases every 45 seconds; independent of 5s control loop |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Graceful degradation**: every external dep must be optional -- None checks, never crash
- **Safety**: each battery enters safe state independently on comm loss (3 consecutive failures -> zero setpoint)
- **Python conventions**: snake_case files/functions, PascalCase dataclasses/enums, `from __future__ import annotations`, type hints on all signatures
- **Config pattern**: dataclass with `@classmethod from_env()` reading `os.environ`
- **Error handling**: explicit exceptions (never bare `except:`), fire-and-forget for integrations, WARNING log + swallow
- **Tests**: `tests/test_*.py` with `pytest` + `anyio`, `asyncio_mode = "auto"`
- **Before committing**: run `caliber refresh`

## Architecture Patterns

### Recommended Project Structure

No new files beyond what's listed below. All code goes into existing `backend/` directory:

```
backend/
  commissioning.py       # NEW: CommissioningManager + CommissioningStage enum
  config.py              # MODIFIED: add CommissioningConfig dataclass
  coordinator.py         # MODIFIED: shadow mode guard, commissioning state in CoordinatorState
  victron_controller.py  # MODIFIED: 45s watchdog guard background task
  api.py                 # MODIFIED: commissioning section in /api/health
  main.py                # MODIFIED: wire CommissioningManager in lifespan
  controller_model.py    # MODIFIED: add commissioning fields to CoordinatorState
tests/
  test_commissioning.py  # NEW: commissioning state machine + shadow mode tests
  test_victron_watchdog_guard.py  # NEW: 45s guard tests
```

### Pattern 1: CommissioningManager State Machine

**What:** A state machine managing the READ_ONLY -> SINGLE_BATTERY -> DUAL_BATTERY progression with JSON persistence.

**When to use:** At startup and every control cycle to determine whether writes are allowed.

**Design:**

```python
from __future__ import annotations

import enum
import json
import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class CommissioningStage(str, enum.Enum):
    READ_ONLY = "READ_ONLY"
    SINGLE_BATTERY = "SINGLE_BATTERY"
    DUAL_BATTERY = "DUAL_BATTERY"


@dataclass
class CommissioningState:
    stage: CommissioningStage
    shadow_mode: bool
    stage_entered_at: float  # time.time() epoch
    read_only_min_hours: float
    single_battery_min_hours: float

    def can_write_victron(self) -> bool:
        return self.stage in (
            CommissioningStage.SINGLE_BATTERY,
            CommissioningStage.DUAL_BATTERY,
        ) and not self.shadow_mode

    def can_write_huawei(self) -> bool:
        return (
            self.stage == CommissioningStage.DUAL_BATTERY
            and not self.shadow_mode
        )
```

**Key insight:** The `can_write_victron()` / `can_write_huawei()` methods provide the gate that the coordinator checks before calling `controller.execute()`. The SINGLE_BATTERY stage enables Victron writes only (since Huawei mode manager handles its own writes -- the context says "Victron only, since Huawei mode manager handles its own writes").

Wait -- re-reading the CONTEXT.md: "SINGLE_BATTERY (Victron only, since Huawei mode manager handles its own writes)". This means during SINGLE_BATTERY stage, Victron gets write access first because Huawei's mode manager already controls Huawei TOU writes independently. So:

- READ_ONLY: neither battery gets coordinator writes
- SINGLE_BATTERY: Victron coordinator writes enabled, Huawei coordinator writes still blocked (Huawei mode manager runs independently)
- DUAL_BATTERY: both get coordinator writes

### Pattern 2: Shadow Mode Integration in Coordinator

**What:** Before calling `controller.execute()`, check commissioning manager. If shadow mode is active, log the decision but skip the execute call.

**When to use:** In every code path in `_run_cycle()` that calls `self._huawei_ctrl.execute()` or `self._victron_ctrl.execute()`.

**Design approach:**

The coordinator's `_run_cycle()` method has 6+ code paths that call `controller.execute()` (EVCC hold, HA mode override, grid charge, grid charge cleanup, charge routing, discharge path). Rather than adding shadow-mode checks to each path, introduce a private method that wraps all execute calls:

```python
async def _execute_commands(
    self, h_cmd: ControllerCommand, v_cmd: ControllerCommand
) -> None:
    """Execute commands with commissioning gate and shadow mode."""
    cm = self._commissioning_manager
    if cm is not None and cm.state.shadow_mode:
        # Log shadow decision
        self._decisions.append(DecisionEntry(
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            trigger="shadow_mode",
            huawei_role=h_cmd.role.value,
            victron_role=v_cmd.role.value,
            p_target_w=0.0,
            huawei_allocation_w=h_cmd.target_watts,
            victron_allocation_w=v_cmd.target_watts,
            pool_status=self._state.pool_status if self._state else "NORMAL",
            reasoning="Shadow mode: decisions logged, writes suppressed",
        ))
        return

    # Stage-gated execution
    if cm is None or cm.state.can_write_huawei():
        await self._huawei_ctrl.execute(h_cmd)
    else:
        logger.debug("Commissioning: Huawei write blocked (stage=%s)", cm.state.stage.value)

    if cm is None or cm.state.can_write_victron():
        await self._victron_ctrl.execute(v_cmd)
    else:
        logger.debug("Commissioning: Victron write blocked (stage=%s)", cm.state.stage.value)
```

Then refactor all `_run_cycle()` paths to call `self._execute_commands(h_cmd, v_cmd)` instead of directly calling `self._huawei_ctrl.execute()` and `self._victron_ctrl.execute()`.

### Pattern 3: Victron 45-Second Watchdog Guard

**What:** A background asyncio task that writes 0W to all 3 Victron phases every 45 seconds, ensuring the Venus OS Hub4 60-second watchdog never fires.

**Why 45 seconds:** Venus OS Hub4 has a built-in watchdog timer. If no setpoint write is received within 60 seconds, Venus OS reverts to its default ESS behavior (typically Mode 1 or Mode 2), which could cause uncontrolled battery discharge. The 5-second control loop normally keeps this timer refreshed, but if the control loop stalls (heavy computation, InfluxDB timeout, etc.), the 45-second guard provides a safety net.

**Where:** In `VictronController`, started as a background task via the coordinator or main.py lifespan.

```python
async def _watchdog_guard_loop(self) -> None:
    """Periodic zero-write to prevent Venus OS 60s watchdog timeout."""
    while True:
        await asyncio.sleep(45)
        try:
            for phase in (1, 2, 3):
                await self._driver.write_ac_power_setpoint(phase, 0.0)
            logger.debug("Victron watchdog guard: 0W written to all phases")
        except Exception as exc:
            logger.warning("Victron watchdog guard write failed: %s", exc)
```

**Critical detail:** The guard writes 0W (zero setpoint = no discharge/charge = safe). This is not a real control action -- it is purely a keepalive for the watchdog timer. If the control loop is running normally (every 5s), the guard write at 45s is redundant and harmless.

**The guard must NOT run during dry_run/validation period** since it would be an actual hardware write. Check `_in_validation_period()` before writing.

### Pattern 4: JSON File Persistence

**What:** Commissioning state persists to `/config/ems_commissioning.json` so stage survives restarts.

**Follows:** The `ModelStore` pattern from Phase 16, but simpler -- just a single JSON file.

```python
def _save_state(self) -> None:
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
    os.replace(tmp, path)  # atomic on POSIX

def _load_state(self) -> CommissioningState | None:
    path = self._config.state_file_path
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        data = json.load(f)
    return CommissioningState(
        stage=CommissioningStage(data["stage"]),
        shadow_mode=data.get("shadow_mode", True),
        stage_entered_at=data.get("stage_entered_at", time.time()),
        read_only_min_hours=self._config.read_only_min_hours,
        single_battery_min_hours=self._config.single_battery_min_hours,
    )
```

### Anti-Patterns to Avoid

- **Modifying each execute() call site individually**: There are 6+ places in `_run_cycle()` that call execute. Extracting a common `_execute_commands()` avoids N-way duplication of the shadow/commissioning check.
- **Coupling watchdog guard to control loop timing**: The guard MUST be independent. If it runs inside `_run_cycle()`, a stalled cycle defeats the purpose.
- **Auto-advancing stages without explicit criteria**: Stage progression must require documented, measurable criteria (time-in-stage + health checks), never auto-advance.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Atomic JSON writes | Manual file.write() | os.replace() with tmp file | Crash during write corrupts state file |
| Background task management | threading.Thread | asyncio.create_task() | Project uses asyncio throughout |
| Enum serialization | Custom to_json/from_json | str mixin on enum (existing pattern) | BatteryRole, PoolStatus already use this |

## Common Pitfalls

### Pitfall 1: Watchdog Guard Running During Validation Period

**What goes wrong:** The 45s guard writes 0W to Victron while the system is supposed to be in read-only validation mode, violating the dry-run contract.
**Why it happens:** Guard task started unconditionally at startup.
**How to avoid:** Guard checks `_in_validation_period()` before writing. If in validation, log but skip.
**Warning signs:** Test that asserts no writes during dry_run fails.

### Pitfall 2: Shadow Mode Not Covering All Execute Paths

**What goes wrong:** Shadow mode blocks writes in the normal discharge path but not in EVCC hold or grid charge paths, allowing unintended writes.
**Why it happens:** Shadow mode check added to some paths but not all.
**How to avoid:** Extract `_execute_commands()` method and replace ALL direct `controller.execute()` calls in `_run_cycle()`.
**Warning signs:** Grep for `execute(` in coordinator.py should find only the centralized method + safe-state writes.

### Pitfall 3: Commissioning State File Path Not Configurable

**What goes wrong:** Hard-coded path fails in Docker vs. local dev environments.
**Why it happens:** Forgetting the `from_env()` pattern.
**How to avoid:** `CommissioningConfig` dataclass with `state_file_path` defaulting to `/config/ems_commissioning.json` but overridable via `EMS_COMMISSIONING_STATE_PATH`.

### Pitfall 4: Stage Transition Criteria Not Time-Zone Aware

**What goes wrong:** Time-based criteria (e.g., "48 hours in READ_ONLY") use wall-clock time which can jump on NTP sync or DST changes.
**Why it happens:** Using `datetime.now()` instead of monotonic time or epoch time.
**How to avoid:** Use `time.time()` (epoch) for stage entry timestamp -- same pattern as `HardwareValidationConfig` which uses `time.time()` for `_first_read_at`.

### Pitfall 5: Victron Guard Conflicting with Active Setpoints

**What goes wrong:** The 45s guard writes 0W while the control loop just wrote -2000W, creating a brief power flicker.
**Why it happens:** Guard and control loop are asynchronous.
**How to avoid:** The guard only needs to fire when the control loop has NOT written recently. Track `_last_write_time` in VictronController; if < 45s ago, skip guard write. Alternatively, accept that the next control loop cycle (5s later) will reassert the correct setpoint. Since the guard fires every 45s and the control loop fires every 5s, there is at most one 5s window of 0W before the control loop corrects it. For safety, this is acceptable -- a brief 0W is far better than a watchdog timeout.

**Recommended approach:** Keep it simple -- always write 0W at 45s. The control loop overwrites within 5s. Complexity of tracking last-write-time is not worth it for a safety guard.

## Code Examples

### CommissioningConfig Dataclass

```python
@dataclass
class CommissioningConfig:
    """Configuration for the production commissioning state machine.

    Environment variables:
        ``EMS_COMMISSIONING_ENABLED``       -- enable/disable (default "true").
        ``EMS_SHADOW_MODE``                 -- shadow mode (default "true").
        ``EMS_COMMISSIONING_STATE_PATH``    -- state file path (default "/config/ems_commissioning.json").
        ``EMS_READ_ONLY_MIN_HOURS``         -- min hours in READ_ONLY (default 24).
        ``EMS_SINGLE_BATTERY_MIN_HOURS``    -- min hours in SINGLE_BATTERY (default 24).
    """

    enabled: bool = True
    shadow_mode: bool = True
    state_file_path: str = "/config/ems_commissioning.json"
    read_only_min_hours: float = 24.0
    single_battery_min_hours: float = 24.0

    @classmethod
    def from_env(cls) -> "CommissioningConfig":
        return cls(
            enabled=os.environ.get("EMS_COMMISSIONING_ENABLED", "true").lower() == "true",
            shadow_mode=os.environ.get("EMS_SHADOW_MODE", "true").lower() == "true",
            state_file_path=os.environ.get(
                "EMS_COMMISSIONING_STATE_PATH", "/config/ems_commissioning.json"
            ),
            read_only_min_hours=float(os.environ.get("EMS_READ_ONLY_MIN_HOURS", "24")),
            single_battery_min_hours=float(
                os.environ.get("EMS_SINGLE_BATTERY_MIN_HOURS", "24")
            ),
        )
```

### Health Endpoint Extension

```python
# In api.py get_health():
commissioning_mgr = getattr(request.app.state, "commissioning_manager", None)
return {
    # ... existing fields ...
    "commissioning": {
        "stage": commissioning_mgr.stage.value if commissioning_mgr else "DUAL_BATTERY",
        "shadow_mode": commissioning_mgr.shadow_mode if commissioning_mgr else False,
        "stage_entered_at": commissioning_mgr.stage_entered_at_iso if commissioning_mgr else None,
        "progression_criteria": commissioning_mgr.get_progression_status() if commissioning_mgr else None,
    } if commissioning_mgr else None,
}
```

### CoordinatorState Extension

```python
# Add to CoordinatorState dataclass:
commissioning_stage: str = "DUAL_BATTERY"
"""Current commissioning stage (READ_ONLY, SINGLE_BATTERY, DUAL_BATTERY)."""

commissioning_shadow_mode: bool = False
"""True when shadow mode is active (decisions logged, writes suppressed)."""
```

### Wiring in main.py Lifespan

```python
# After coordinator creation, before coordinator.start():
from backend.commissioning import CommissioningManager
from backend.config import CommissioningConfig

commissioning_cfg = CommissioningConfig.from_env()
commissioning_mgr = CommissioningManager(commissioning_cfg)
commissioning_mgr.load_or_init()
coordinator.set_commissioning_manager(commissioning_mgr)
app.state.commissioning_manager = commissioning_mgr
logger.info(
    "Commissioning manager: stage=%s shadow=%s",
    commissioning_mgr.stage.value,
    commissioning_mgr.shadow_mode,
)
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + anyio (auto async mode) |
| Config file | `pyproject.toml` ([tool.pytest.ini_options]) |
| Quick run command | `python -m pytest tests/test_commissioning.py tests/test_victron_watchdog_guard.py -x -q` |
| Full suite command | `python -m pytest tests/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PROD-01 | Staged rollout progression READ_ONLY -> SINGLE_BATTERY -> DUAL_BATTERY | unit | `python -m pytest tests/test_commissioning.py::test_stage_progression -x` | Wave 0 |
| PROD-01 | Stage persists across restarts via JSON file | unit | `python -m pytest tests/test_commissioning.py::test_state_persistence -x` | Wave 0 |
| PROD-01 | Stage transition blocked when criteria not met | unit | `python -m pytest tests/test_commissioning.py::test_transition_blocked -x` | Wave 0 |
| PROD-02 | Shadow mode logs decisions without executing | unit | `python -m pytest tests/test_commissioning.py::test_shadow_mode_no_writes -x` | Wave 0 |
| PROD-02 | Shadow decisions appear in decision log with trigger="shadow_mode" | unit | `python -m pytest tests/test_commissioning.py::test_shadow_decision_log -x` | Wave 0 |
| PROD-03 | 45s guard writes 0W to all 3 phases | unit | `python -m pytest tests/test_victron_watchdog_guard.py::test_guard_fires -x` | Wave 0 |
| PROD-03 | Guard skips write during validation period | unit | `python -m pytest tests/test_victron_watchdog_guard.py::test_guard_skip_validation -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_commissioning.py tests/test_victron_watchdog_guard.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_commissioning.py` -- covers PROD-01, PROD-02
- [ ] `tests/test_victron_watchdog_guard.py` -- covers PROD-03

## Open Questions

1. **Watchdog guard interaction with safe-state writes**
   - What we know: Safe-state writes (3 consecutive failures -> zero setpoint) bypass validation period. The watchdog guard also writes zero.
   - What's unclear: Should the guard defer to safe-state writes, or are they independently fine since both write 0W?
   - Recommendation: Both independently write 0W -- they are idempotent. No coordination needed.

2. **Stage advancement: manual vs. automatic**
   - What we know: CONTEXT says "documented progression criteria (configurable via env vars)".
   - What's unclear: Should the system auto-advance when criteria are met, or require manual advancement (env var change + restart)?
   - Recommendation: Auto-check criteria each cycle, but log "ready to advance" rather than auto-advancing. Provide an API endpoint or env-var override to trigger advancement. This is the safest approach for production commissioning.

## Sources

### Primary (HIGH confidence)
- Codebase analysis: `backend/coordinator.py`, `backend/victron_controller.py`, `backend/config.py`, `backend/huawei_mode_manager.py`, `backend/main.py`, `backend/controller_model.py`, `backend/api.py`
- CONTEXT.md: Phase 23 implementation decisions and constraints

### Secondary (MEDIUM confidence)
- Venus OS Hub4 60-second watchdog timeout: established Victron community knowledge. The Hub4 control mechanism reverts to default behavior if no ESS setpoint write is received within 60 seconds. The EMS already has an ESS mode guard (ess_mode < 2 check in VictronController.execute()), confirming the project is aware of Venus OS Hub4 behavior.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - pure Python, no new dependencies
- Architecture: HIGH - follows exact patterns from Phase 20 (HardwareValidationConfig), Phase 22 (HuaweiModeManager state machine), and existing coordinator wiring
- Pitfalls: HIGH - derived from direct analysis of coordinator's 6+ execute paths and existing validation/dry-run patterns

**Research date:** 2026-03-24
**Valid until:** 2026-04-24 (stable -- internal architecture, no external deps)
