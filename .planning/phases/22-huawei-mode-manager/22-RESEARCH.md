# Phase 22: Huawei Mode Manager - Research

**Researched:** 2026-03-24
**Domain:** Huawei LUNA2000 Modbus TCP working mode lifecycle management
**Confidence:** HIGH

## Summary

Phase 22 introduces a `HuaweiModeManager` state machine that takes authoritative control of the Huawei inverter's storage working mode. On EMS startup, it switches the inverter from the default `MAXIMISE_SELF_CONSUMPTION` (mode 2) to `TIME_OF_USE_LUNA2000` (mode 5) via register 47086. This gives EMS full control over charge/discharge power limits, which is required for coordinated dual-battery operation. On shutdown, the mode is restored to self-consumption so the Huawei system operates safely if EMS is not running.

The implementation builds on existing primitives: `HuaweiDriver.write_battery_mode()` already writes register `storage_working_mode_settings`, `write_max_charge_power(0)` and `write_max_discharge_power(0)` provide power clamping, and `HuaweiBatteryData.working_mode` (register 37006) provides read-back verification. The mode manager must be wired into the FastAPI lifespan (startup/shutdown) and the coordinator's control loop (periodic health checks). The key safety concern is that mode transitions must clamp power to zero before switching and wait for the inverter to settle, preventing transient power spikes.

**Primary recommendation:** Implement `HuaweiModeManager` as a standalone class in `backend/huawei_mode_manager.py`, injected into `HuaweiController` via `set_mode_manager()`, with a `ModeManagerConfig` dataclass in `backend/config.py`. Expose the current working mode as an HA MQTT sensor entity.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
None explicitly locked -- all implementation choices are at Claude's discretion (infrastructure phase).

### Claude's Discretion
All implementation choices are at Claude's discretion -- pure infrastructure phase. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

Key constraints from research:
- Use existing `HuaweiDriver.write_battery_mode(StorageWorkingModesC.TIME_OF_USE_LUNA2000)` for TOU mode
- Use existing `write_max_charge_power(0)` and `write_max_discharge_power(0)` for power clamping before mode switch
- Mode transitions: clamp -> wait 1 cycle (5s) -> switch -> wait settle (5s) -> resume setpoints
- Mode health check: read current working mode periodically, re-apply if reverted
- Shutdown: restore to `MAXIMISE_SELF_CONSUMPTION`, must be idempotent and handle crash recovery
- Safe-state writes must bypass mode manager checks
- Follow existing injection pattern: `set_mode_manager()` on HuaweiController or Coordinator
- Expose current Huawei working mode via HA MQTT entity

### Deferred Ideas (OUT OF SCOPE)
None -- infrastructure phase.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| HCTL-01 | EMS switches Huawei to TOU working mode (register 47086) on startup for authoritative charge/discharge control | `HuaweiDriver.write_battery_mode(StorageWorkingModesC.TIME_OF_USE_LUNA2000)` already exists; wire into lifespan startup via mode manager |
| HCTL-02 | EMS restores Huawei to self-consumption mode on shutdown (idempotent, handles crash recovery) | `write_battery_mode(MAXIMISE_SELF_CONSUMPTION)` for shutdown; `working_mode` field from `read_battery()` for crash recovery detection at next startup |
| HCTL-03 | EMS periodically verifies Huawei is still in TOU mode and re-applies if reverted | `HuaweiBatteryData.working_mode` (register 37006) is already read every poll cycle; compare against expected mode and re-apply if mismatched |
| HCTL-04 | Mode transitions clamp power to zero before switching and wait for settle before resuming setpoints | Use `write_max_charge_power(0)` + `write_max_discharge_power(0)` before mode write; configurable settle delay (default 5s) before resuming |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| huawei-solar | >= 2.5 | Modbus TCP driver + `StorageWorkingModesC` enum | Already installed; provides `AsyncHuaweiSolar.set()` for register writes |
| pymodbus | (transitive) | Underlying Modbus TCP transport | Dependency of huawei-solar |
| FastAPI | (existing) | Lifespan hooks for startup/shutdown | Already the application framework |
| paho-mqtt | (existing) | HA MQTT entity publishing | Already used by `ha_mqtt_client.py` |

### Supporting
No new dependencies. Everything needed exists in the project.

**Installation:**
No new packages required.

## Architecture Patterns

### Recommended Project Structure
```
backend/
  huawei_mode_manager.py   # NEW: HuaweiModeManager state machine
  config.py                # MODIFY: add ModeManagerConfig
  huawei_controller.py     # MODIFY: add mode manager awareness
  coordinator.py           # MODIFY: wire mode manager, expose mode in state
  main.py                  # MODIFY: create mode manager at startup, restore on shutdown
  ha_mqtt_client.py        # MODIFY: add huawei_working_mode sensor entity
  controller_model.py      # MODIFY: add huawei_working_mode field to CoordinatorState
tests/
  test_huawei_mode_manager.py  # NEW: mode manager unit tests
```

### Pattern 1: State Machine for Mode Lifecycle

**What:** `HuaweiModeManager` tracks the current mode state and manages transitions with power clamping and settle delays.

**When to use:** Any time the Huawei working mode needs to change (startup, shutdown, health check recovery).

**State machine states:**
- `IDLE` -- mode manager not yet activated
- `CLAMPING` -- power clamped to zero, waiting before mode switch
- `SWITCHING` -- mode write issued, waiting for settle
- `ACTIVE` -- TOU mode confirmed, normal operation
- `RESTORING` -- switching back to self-consumption (shutdown)
- `FAILED` -- mode switch failed after retries

**Example:**
```python
from __future__ import annotations

import enum
import logging
import time

from backend.drivers.huawei_driver import HuaweiDriver, StorageWorkingModesC

logger = logging.getLogger(__name__)


class ModeState(enum.Enum):
    IDLE = "idle"
    CLAMPING = "clamping"
    SWITCHING = "switching"
    ACTIVE = "active"
    RESTORING = "restoring"
    FAILED = "failed"


class HuaweiModeManager:
    def __init__(
        self,
        driver: HuaweiDriver,
        settle_delay_s: float = 5.0,
        health_check_interval_s: float = 60.0,
    ) -> None:
        self._driver = driver
        self._settle_delay_s = settle_delay_s
        self._health_check_interval_s = health_check_interval_s
        self._state = ModeState.IDLE
        self._last_health_check: float = 0.0
        self._target_mode = StorageWorkingModesC.TIME_OF_USE_LUNA2000

    @property
    def state(self) -> ModeState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state == ModeState.ACTIVE

    async def activate(self) -> None:
        """Switch to TOU mode with power clamping."""
        # Clamp power to zero
        await self._driver.write_max_charge_power(0)
        await self._driver.write_max_discharge_power(0)
        self._state = ModeState.CLAMPING
        # ... wait settle, then switch mode

    async def check_health(self, current_working_mode: int | None) -> None:
        """Verify mode hasn't reverted; re-apply if needed."""
        ...

    async def restore(self) -> None:
        """Restore to MAXIMISE_SELF_CONSUMPTION on shutdown."""
        ...
```

### Pattern 2: Optional Injection (Established Project Pattern)

**What:** Mode manager is injected into HuaweiController via `set_mode_manager()`, with `None` guards everywhere.

**When to use:** All optional integrations in this project follow this pattern.

**Example:**
```python
# In HuaweiController
def set_mode_manager(self, manager: HuaweiModeManager) -> None:
    self._mode_manager = manager

# In poll() or execute() — check before use
if self._mode_manager is not None:
    await self._mode_manager.check_health(battery.working_mode)
```

### Pattern 3: Config Dataclass with from_env()

**What:** `ModeManagerConfig` follows the established config pattern.

**Example:**
```python
@dataclass
class ModeManagerConfig:
    enabled: bool = True
    settle_delay_s: float = 5.0
    health_check_interval_s: float = 60.0

    @classmethod
    def from_env(cls) -> "ModeManagerConfig":
        return cls(
            enabled=os.environ.get("EMS_MODE_MANAGER_ENABLED", "true").lower() == "true",
            settle_delay_s=float(os.environ.get("EMS_MODE_SETTLE_DELAY_S", "5.0")),
            health_check_interval_s=float(
                os.environ.get("EMS_MODE_HEALTH_CHECK_S", "60.0")
            ),
        )
```

### Anti-Patterns to Avoid
- **Calling driver directly from coordinator:** The coordinator NEVER calls driver methods directly (CTRL-02 constraint). Mode manager owns the driver calls for mode switching.
- **Blocking the control loop during mode transitions:** Use the state machine approach -- each call to `check_health()` or `activate()` does one step, not the full multi-second transition. The 5s control loop naturally provides the timing.
- **Forgetting to clamp before switching:** Mode transitions without zero power first can cause transient spikes. The state machine enforces: CLAMPING -> SWITCHING -> ACTIVE.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Working mode register writes | Raw Modbus register writes | `HuaweiDriver.write_battery_mode()` | Already handles reconnect, dry_run, logging |
| Working mode read-back | Separate register read | `HuaweiBatteryData.working_mode` from `read_battery()` | Already read every poll cycle for free |
| Power clamping | Custom zero-write logic | `write_max_charge_power(0)` + `write_max_discharge_power(0)` | Existing methods with reconnect and dry_run |
| HA entity publishing | Custom MQTT publish | `ha_mqtt_client.py` entity definitions | Established discovery + state pattern |

**Key insight:** All the Modbus primitives already exist. This phase is purely about orchestrating them in the correct sequence with proper state tracking.

## Common Pitfalls

### Pitfall 1: Race Between Mode Manager and Controller Execute
**What goes wrong:** The mode manager clamps power to zero for a mode switch, then the controller's `execute()` immediately writes non-zero power limits before the settle delay completes.
**Why it happens:** The control loop runs every 5s and `execute()` writes power limits independently of mode manager state.
**How to avoid:** Mode manager exposes an `is_transitioning` property. `HuaweiController.execute()` checks this and skips power writes (or forces zero) while transitioning. Safe-state writes bypass this check.
**Warning signs:** Transient power spikes during mode transitions visible in InfluxDB.

### Pitfall 2: Infinite Re-Apply Loop
**What goes wrong:** Mode health check detects a mismatch, re-applies TOU mode, but the read-back register (37006) lags behind the write register (47086), causing immediate re-detection.
**Why it happens:** Register 37006 (`storage_unit_1_working_mode_b`) is a read-only status register that may update asynchronously from the write register 47086 (`storage_working_mode_settings`).
**How to avoid:** After a mode re-apply, set a cooldown timer (e.g., 30s) before the next health check. Track consecutive re-apply attempts and stop after N failures.
**Warning signs:** Rapid repeated mode write log entries.

### Pitfall 3: Crash Recovery State Confusion
**What goes wrong:** EMS crashes after switching to TOU mode. On restart, the mode manager doesn't know the inverter is already in TOU mode and performs an unnecessary transition (with power clamping).
**Why it happens:** Mode manager starts in `IDLE` state and has no persistent state.
**How to avoid:** At startup, read `working_mode` from `read_battery()` first. If already `TIME_OF_USE_LUNA2000` (value 5), skip directly to `ACTIVE` state without clamping. This is the crash recovery path.
**Warning signs:** Unnecessary power interruptions on EMS restart.

### Pitfall 4: Safe-State Writes Blocked by Mode Manager
**What goes wrong:** Battery enters safe state (3 consecutive failures), but the mode manager's `is_transitioning` flag blocks the safe-state zero-power write.
**Why it happens:** Safe-state writes go through `_handle_failure()` which writes `write_max_discharge_power(0)` directly on the driver. If mode manager check is added upstream of this, it could block it.
**How to avoid:** The CONTEXT.md explicitly states: "Safe-state writes must bypass mode manager checks." Ensure `_handle_failure()` remains a direct driver call that never consults the mode manager.
**Warning signs:** Battery not entering safe state during comm loss.

### Pitfall 5: Shutdown Restore Fails Silently
**What goes wrong:** EMS shutdown calls `restore()` but the Modbus connection is already closed or the inverter is unresponsive.
**Why it happens:** Shutdown ordering -- if `huawei.close()` runs before `mode_manager.restore()`.
**How to avoid:** Mode manager restore must happen BEFORE driver close in the lifespan shutdown sequence. Make `restore()` idempotent with exception handling -- log WARNING but don't crash.
**Warning signs:** Inverter left in TOU mode after EMS stops.

## Code Examples

### StorageWorkingModesC Enum Values (verified)
```python
# From huawei_solar.register_values (verified on this machine):
# ADAPTIVE = 0
# FIXED_CHARGE_DISCHARGE = 1
# MAXIMISE_SELF_CONSUMPTION = 2  <-- restore target
# TIME_OF_USE_LG = 3
# FULLY_FED_TO_GRID = 4
# TIME_OF_USE_LUNA2000 = 5       <-- EMS operating mode
```

### Existing Write Method (from huawei_driver.py)
```python
# Source: backend/drivers/huawei_driver.py line 361
async def write_battery_mode(
    self, mode: StorageWorkingModesC, *, dry_run: bool = False
) -> None:
    # Uses self._client.set("storage_working_mode_settings", mode, slave_id=...)
    # Wraps in _with_reconnect for automatic retry on ConnectionException
```

### Existing Working Mode Read (from read_battery)
```python
# Source: backend/drivers/huawei_driver.py line 325
# Register 37006 (storage_unit_1_working_mode_b) read in every poll cycle
working_mode=p1.get("storage_unit_1_working_mode_b")
# Returns int value matching StorageWorkingModesC enum
```

### HA MQTT Entity Pattern (from ha_mqtt_client.py)
```python
# Source: backend/ha_mqtt_client.py line 130
# Add to SENSOR_ENTITIES list:
EntityDefinition(
    "huawei_working_mode", "Working Mode", "sensor",
    None, "enum", None, "diagnostic",
    "huawei_working_mode", "huawei",
)
```

### Lifespan Integration Pattern (from main.py)
```python
# Source: backend/main.py startup section (~line 552-570)
# Create mode manager after HuaweiController, before coordinator.start():
mode_manager = HuaweiModeManager(huawei, mode_cfg)
huawei_ctrl.set_mode_manager(mode_manager)
await mode_manager.activate()  # switches to TOU mode

# Source: backend/main.py shutdown section (~line 727-753)
# Restore mode BEFORE closing drivers:
if mode_manager is not None:
    await mode_manager.restore()
await huawei.close()  # driver close comes after
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Manual TOU mode setting via FusionSolar app | EMS automated mode management | This phase | EMS takes authoritative control, no manual intervention needed |
| `get_working_mode()` returns None on Coordinator | Mode manager tracks and exposes actual mode | This phase | HA entity shows real working mode, /health endpoint returns actual value |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + anyio (asyncio_mode = "auto") |
| Config file | `pyproject.toml` |
| Quick run command | `python -m pytest tests/test_huawei_mode_manager.py -q` |
| Full suite command | `python -m pytest tests/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| HCTL-01 | Activate switches to TOU mode via driver | unit | `python -m pytest tests/test_huawei_mode_manager.py::test_activate_writes_tou_mode -x` | Wave 0 |
| HCTL-01 | Clamps power before mode switch | unit | `python -m pytest tests/test_huawei_mode_manager.py::test_activate_clamps_power_first -x` | Wave 0 |
| HCTL-02 | Restore writes MAXIMISE_SELF_CONSUMPTION | unit | `python -m pytest tests/test_huawei_mode_manager.py::test_restore_writes_self_consumption -x` | Wave 0 |
| HCTL-02 | Restore is idempotent (no error if already in self-consumption) | unit | `python -m pytest tests/test_huawei_mode_manager.py::test_restore_idempotent -x` | Wave 0 |
| HCTL-02 | Crash recovery: startup reads mode, skips transition if already TOU | unit | `python -m pytest tests/test_huawei_mode_manager.py::test_crash_recovery_skips_clamping -x` | Wave 0 |
| HCTL-03 | Health check detects mode reversion and re-applies | unit | `python -m pytest tests/test_huawei_mode_manager.py::test_health_check_reapplies_on_revert -x` | Wave 0 |
| HCTL-03 | Health check respects cooldown after re-apply | unit | `python -m pytest tests/test_huawei_mode_manager.py::test_health_check_cooldown -x` | Wave 0 |
| HCTL-04 | Execute blocked during mode transition | unit | `python -m pytest tests/test_huawei_mode_manager.py::test_execute_blocked_during_transition -x` | Wave 0 |
| HCTL-04 | Safe-state writes bypass mode transition check | unit | `python -m pytest tests/test_huawei_mode_manager.py::test_safe_state_bypasses_transition -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_huawei_mode_manager.py -q`
- **Per wave merge:** `python -m pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_huawei_mode_manager.py` -- covers HCTL-01 through HCTL-04
- No framework install needed (pytest already configured)
- No conftest changes needed (existing mock patterns in `test_huawei_controller.py` are directly reusable)

## Sources

### Primary (HIGH confidence)
- `huawei_solar.register_values.StorageWorkingModesC` -- verified locally: `TIME_OF_USE_LUNA2000=5`, `MAXIMISE_SELF_CONSUMPTION=2`
- `backend/drivers/huawei_driver.py` -- `write_battery_mode()` method at line 361, `read_battery()` returning `working_mode` at line 325
- `backend/huawei_controller.py` -- controller pattern with `poll()`, `execute()`, `_handle_failure()`
- `backend/main.py` -- lifespan startup/shutdown pattern, injection wiring
- `backend/ha_mqtt_client.py` -- entity definition pattern, `SENSOR_ENTITIES` list
- `backend/config.py` -- `from_env()` classmethod config pattern
- `backend/coordinator.py` -- `set_*()` injection methods, `_build_controllable_extra_fields()`

### Secondary (MEDIUM confidence)
- Huawei register documentation: 47086 = `storage_working_mode_settings` (write), 37006 = `storage_unit_1_working_mode_b` (read) -- confirmed via huawei-solar library register names

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already installed and in use, enum values verified locally
- Architecture: HIGH -- follows established patterns visible throughout the codebase
- Pitfalls: HIGH -- derived from direct code analysis of race conditions and shutdown ordering

**Research date:** 2026-03-24
**Valid until:** 2026-04-24 (stable domain -- Modbus registers and huawei-solar API are mature)
