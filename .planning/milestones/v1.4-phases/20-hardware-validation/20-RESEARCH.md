# Phase 20: Hardware Validation - Research

**Researched:** 2026-03-24
**Domain:** Modbus TCP driver hardening, write-back verification, dry-run mode, read-only validation period
**Confidence:** HIGH

## Summary

Phase 20 adds four safety layers to the existing Modbus TCP drivers before any production control. The codebase already has all the read/write methods needed -- HuaweiDriver has 4 write methods and VictronDriver has 1 -- so the work is purely additive: adding a `dry_run` flag to each write method, implementing write-then-read-back verification, validating read connectivity at startup, and enforcing a configurable read-only period per battery system.

The existing code patterns are well-established. Both drivers use `_with_reconnect` wrappers, `assert self._client is not None` guards, and structured DEBUG/WARNING logging. Controllers consume drivers through `poll()` and `execute()` methods. The coordinator never calls drivers directly -- it only talks through controllers. This means the dry_run and validation-period enforcement can be layered at either the driver level (dry_run flag) or the controller/coordinator level (validation period gating).

**Primary recommendation:** Add `dry_run: bool = False` parameter to all 5 existing write methods. Implement write-back verification as a new driver method called after writes. Add a `HardwareValidationConfig` dataclass to `config.py` with `validation_period_hours` (default 48) and `dry_run` (default True). Wire validation state tracking into the lifespan/coordinator startup.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
None -- all implementation choices are at Claude's discretion (infrastructure phase).

### Claude's Discretion
All implementation choices are at Claude's discretion. Key constraints from research:
- Huawei SDongle only allows 1 Modbus TCP connection -- must decide Modbus Proxy vs sole-client
- Huawei power limits are ceilings, not setpoints -- validate actual vs commanded deviation
- All driver write methods already exist (write_battery_mode, write_ac_charging, write_max_charge_power, write_max_discharge_power for Huawei; write_ac_power_setpoint for Victron)
- dry_run flag should be added to existing write methods, not as separate methods
- Read-only validation period should be configurable via env var (default 48h)

### Deferred Ideas (OUT OF SCOPE)
None -- infrastructure phase.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| HWVAL-01 | EMS validates Modbus read connectivity to both batteries before attempting any writes | Startup connectivity check: both drivers already have `connect()` + health checks (Victron reads SoC register 843). Huawei needs equivalent read-all-registers validation. |
| HWVAL-02 | EMS performs write-back verification (write value, read back, confirm match) before trusting setpoint control | New `verify_write` pattern: write register, immediately read it back, compare. Huawei uses named registers via `huawei-solar`; Victron uses raw `read_holding_registers`. |
| HWVAL-03 | All write methods support a `dry_run` flag that logs intended writes without executing them | Add `dry_run: bool = False` to all 5 write methods. When True, log the intended write as a `DecisionEntry` with trigger="dry_run" and return without executing. |
| HWVAL-04 | EMS runs 48h read-only validation phase before enabling writes on each battery system | New `HardwareValidationConfig` dataclass with `validation_period_hours` env var. Track `first_successful_read_at` per system. Gate writes until period elapsed. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| huawei-solar | >=2.5 | Huawei SUN2000 Modbus TCP | Already installed, used by HuaweiDriver |
| pymodbus | >=3.11,<4 | Victron Modbus TCP via pymodbus | Already installed, used by VictronDriver |
| pytest | >=8 | Unit testing | Already installed, project standard |
| anyio | latest | Async test support | Already installed, project standard |

No new dependencies required. Zero new pip packages (confirmed in STATE.md decisions).

## Architecture Patterns

### Where Each Requirement Lives

```
backend/
  drivers/
    huawei_driver.py    -- HWVAL-01 (read validation), HWVAL-02 (write-back verify), HWVAL-03 (dry_run)
    victron_driver.py   -- HWVAL-01 (read validation), HWVAL-02 (write-back verify), HWVAL-03 (dry_run)
  config.py             -- HWVAL-04 (HardwareValidationConfig dataclass)
  huawei_controller.py  -- HWVAL-04 (validation period gating in execute())
  victron_controller.py -- HWVAL-04 (validation period gating in execute())
  main.py               -- HWVAL-01 (startup validation), HWVAL-04 (config wiring)
tests/
  test_huawei_controller.py -- Extended with dry_run + validation period tests
  test_victron_controller.py -- Extended with dry_run + validation period tests
  test_hardware_validation.py -- New: write-back verification, connectivity checks
```

### Pattern 1: dry_run Flag on Write Methods

**What:** Add `dry_run: bool = False` as the last parameter to each existing write method. When True, log the intended write at INFO level and return without executing.

**When to use:** Every driver write method.

**Example:**
```python
async def write_max_charge_power(self, watts: int, *, dry_run: bool = False) -> None:
    async def _do() -> None:
        assert self._client is not None, "Driver not connected"
        if dry_run:
            logger.info(
                "DRY RUN: would set storage_maximum_charging_power=%d slave_id=%d",
                watts, self.master_slave_id,
            )
            return
        logger.debug("set storage_maximum_charging_power=%d slave_id=%d", watts, self.master_slave_id)
        await self._client.set("storage_maximum_charging_power", watts, slave_id=self.master_slave_id)
    await self._with_reconnect(_do)
```

**Key design choice:** `dry_run` is keyword-only (`*`) to prevent accidental positional use. The flag is at the driver level, not the controller level, so that safe-state writes (zero setpoints on comm loss) still execute even during dry-run mode. The controller/coordinator decides when to pass `dry_run=True`.

### Pattern 2: Write-Back Verification

**What:** After writing a register value, immediately read it back and compare. Return a result indicating match/mismatch.

**When to use:** During initial hardware validation phase and optionally on every write.

**Example for Huawei:**
```python
async def verify_write_max_charge_power(self, watts: int) -> bool:
    """Write max charge power and verify by reading back."""
    await self.write_max_charge_power(watts)
    battery = await self.read_battery()
    actual = battery.max_charge_power_w
    match = actual == watts
    if not match:
        logger.warning(
            "Write-back mismatch: wrote max_charge=%d, read back=%d",
            watts, actual,
        )
    else:
        logger.info("Write-back verified: max_charge=%d matches", watts)
    return match
```

**Important for Huawei:** Power limits are ceilings, not exact setpoints. The charge_discharge_power register reflects actual power, which may differ from the commanded limit. Verification should compare the limit register, not the actual power register.

**For Victron:** Read back the Hub4 setpoint register after writing:
```python
async def verify_write_ac_power_setpoint(self, phase: int, watts: float) -> bool:
    reg = _PHASE_SETPOINT_REG[phase]
    await self.write_ac_power_setpoint(phase, watts)
    result = await self._client.read_holding_registers(address=reg, count=1, slave=self._vebus_unit_id)
    read_back = _signed16(result.registers[0])
    expected = int(watts)
    match = read_back == expected
    if not match:
        logger.warning("Write-back mismatch L%d: wrote %d, read %d", phase, expected, read_back)
    return match
```

### Pattern 3: Read-Only Validation Period

**What:** Track the first successful read timestamp per battery system. Block all writes (pass `dry_run=True`) until the configured validation period has elapsed.

**Where:** Controller level (HuaweiController.execute, VictronController.execute), because the coordinator should not need to know about validation periods.

**Example:**
```python
class HuaweiController:
    def __init__(self, ..., validation_config: HardwareValidationConfig | None = None) -> None:
        ...
        self._validation_config = validation_config
        self._first_read_at: float | None = None

    async def poll(self) -> ControllerSnapshot:
        ...
        # Track first successful read
        if self._first_read_at is None and snap.available:
            self._first_read_at = time.time()
            logger.info("Huawei: first successful read at %.0f — validation period started", self._first_read_at)
        return snap

    def _in_validation_period(self) -> bool:
        if self._validation_config is None:
            return False
        if self._first_read_at is None:
            return True  # haven't read successfully yet
        elapsed_hours = (time.time() - self._first_read_at) / 3600.0
        return elapsed_hours < self._validation_config.validation_period_hours

    async def execute(self, cmd: ControllerCommand) -> None:
        dry_run = self._in_validation_period()
        if dry_run:
            logger.info("Huawei: validation period active (%.1fh remaining) — dry_run=True",
                        self._remaining_hours())
        # Pass dry_run to all driver write calls
        ...
```

### Pattern 4: Startup Connectivity Validation (HWVAL-01)

**What:** Before the coordinator starts its control loop, perform a full read cycle on both batteries to verify Modbus connectivity.

**Where:** In the lifespan (`main.py`), after `connect()` and before `coordinator.start()`.

**Example:**
```python
# After huawei.connect() and victron.connect():
# Validate Huawei reads
try:
    master = await huawei.read_master()
    battery = await huawei.read_battery()
    slave = await huawei.read_slave()
    logger.info("Huawei validation: all registers read successfully (SoC=%.1f%%)", battery.total_soc_pct)
except Exception as exc:
    logger.error("Huawei validation FAILED: %s", exc)
    # Continue in degraded mode or raise

# Validate Victron reads
try:
    state = await victron.read_system_state()
    logger.info("Victron validation: all registers read successfully (SoC=%.1f%%)", state.battery_soc_pct)
except Exception as exc:
    logger.error("Victron validation FAILED: %s", exc)
```

### Anti-Patterns to Avoid

- **Skipping safe-state writes during dry_run:** Safe-state writes (zero setpoint on comm loss) MUST always execute, even during dry_run/validation period. Only coordinator-initiated control writes should be gated.
- **Verifying actual power vs commanded limit for Huawei:** Huawei power limits are ceilings. Actual power depends on load/PV. Only verify the limit registers, not the actual power registers.
- **Persisting validation state to disk:** First-read timestamp should be in-memory only. On restart, the 48h period restarts. This is safer -- you re-validate after every restart.
- **Adding dry_run at the coordinator level only:** The coordinator calls `controller.execute()`, not driver methods directly. dry_run must propagate down to the driver write calls.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Modbus TCP communication | Custom socket code | huawei-solar + pymodbus | Already used, battle-tested, handles reconnect |
| Register address mapping | Raw register numbers for Huawei | huawei-solar named registers | Library handles register grouping, data types, scaling |
| Config env-var parsing | Custom parsing | Existing `from_env()` classmethod pattern | Consistent with 12+ existing config dataclasses |

## Common Pitfalls

### Pitfall 1: Huawei SDongle Single Connection Limit
**What goes wrong:** Huawei SDongle (SUN2000 built-in dongle) only accepts 1 Modbus TCP connection at a time. If another client (Home Assistant, SolarAssistant) is connected, EMS connection fails.
**Why it happens:** Hardware limitation of the SDongle.
**How to avoid:** Use a Modbus TCP proxy (e.g., mbusd) that multiplexes the single upstream connection to multiple downstream clients. The driver already connects via configurable host:port, so pointing it at a proxy requires zero code changes.
**Warning signs:** `ConnectionException` on `connect()` despite correct IP/port.

### Pitfall 2: Huawei Write Register Names vs Read Register Names
**What goes wrong:** The huawei-solar library uses different register names for reading vs writing the same logical value. For example, `storage_maximum_charge_power` (read, reg 37046) vs `storage_maximum_charging_power` (write, reg 47075).
**Why it happens:** Huawei's register map has separate read-only and read-write register ranges.
**How to avoid:** For write-back verification on Huawei, read the read-only register (37046) after writing the read-write register (47075). The library abstracts this, but the names differ. Current code already uses the correct write names.
**Warning signs:** `huawei_solar` raises `ReadOnlyRegister` error if you try to `set()` a read-only register name.

### Pitfall 3: Victron Register Scale Factors
**What goes wrong:** Writing a raw integer but reading back a scaled value, or vice versa. Hub4 setpoint registers are scale-1 (no scaling), but battery registers have scale-10.
**Why it happens:** Victron registers use inconsistent scale factors across register groups.
**How to avoid:** The existing `write_ac_power_setpoint` already handles the int16/uint16 conversion correctly. For write-back verification, read the same register and apply the same `_signed16()` conversion.
**Warning signs:** Read-back values off by factor of 10.

### Pitfall 4: Validation Period vs Safe-State Conflict
**What goes wrong:** During the 48h read-only period, if comm loss triggers safe-state (3 consecutive failures), the safe-state zero-write must still execute. If dry_run blocks it, the battery could continue discharging uncontrolled.
**Why it happens:** Over-enthusiastic dry_run gating that blocks ALL writes including safety writes.
**How to avoid:** Only gate coordinator-initiated `execute()` calls. The `_handle_failure()` safe-state writes in both controllers must bypass dry_run. Implement this by having the controllers pass `dry_run` only in `execute()`, not in `_handle_failure()`.
**Warning signs:** Battery continues discharging during comm loss while in validation period.

### Pitfall 5: time.time() vs time.monotonic()
**What goes wrong:** Using `time.monotonic()` for the validation period timer. Monotonic time resets across reboots and has no relation to wall-clock time.
**Why it happens:** Existing driver code uses `time.monotonic()` for poll timestamps (correct for short-duration staleness checks), but the 48h validation period needs wall-clock time.
**How to avoid:** Use `time.time()` for the validation period first-read timestamp. The 48h period is long enough that NTP adjustments are irrelevant.
**Warning signs:** Validation period appears shorter/longer than expected after system sleep.

## Code Examples

### HardwareValidationConfig Dataclass
```python
@dataclass
class HardwareValidationConfig:
    """Configuration for the hardware validation phase.

    Controls dry-run mode and the read-only validation period that must
    elapse before the EMS enables write operations on each battery system.

    Environment variables:
        ``EMS_VALIDATION_PERIOD_HOURS`` -- hours before writes enabled (default 48).
        ``EMS_DRY_RUN``                -- force dry-run mode (default "false").
    """

    validation_period_hours: float = 48.0
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> "HardwareValidationConfig":
        return cls(
            validation_period_hours=float(
                os.environ.get("EMS_VALIDATION_PERIOD_HOURS", "48")
            ),
            dry_run=os.environ.get("EMS_DRY_RUN", "false").lower() == "true",
        )
```

### Victron Write-Back Verification
```python
async def validate_connectivity(self) -> bool:
    """Perform a full read cycle to validate Modbus TCP connectivity.

    Returns True if all expected registers are readable, False otherwise.
    """
    try:
        state = await self.read_system_state()
        logger.info(
            "Victron connectivity validated: SoC=%.1f%% power=%.0fW grid=%.0fW",
            state.battery_soc_pct,
            state.battery_power_w,
            state.grid_power_w,
        )
        return True
    except Exception as exc:
        logger.error("Victron connectivity validation failed: %s", exc)
        return False
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8 + anyio |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `python -m pytest tests/test_hardware_validation.py tests/test_huawei_controller.py tests/test_victron_controller.py -q` |
| Full suite command | `python -m pytest tests/ -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| HWVAL-01 | Startup reads all registers without error | unit | `python -m pytest tests/test_hardware_validation.py::TestConnectivityValidation -x` | Wave 0 |
| HWVAL-02 | Write-then-read-back matches or logs mismatch | unit | `python -m pytest tests/test_hardware_validation.py::TestWriteBackVerification -x` | Wave 0 |
| HWVAL-03 | dry_run logs but does not execute writes | unit | `python -m pytest tests/test_huawei_controller.py::TestDryRun tests/test_victron_controller.py::TestDryRun -x` | Wave 0 |
| HWVAL-04 | Writes blocked during validation period | unit | `python -m pytest tests/test_huawei_controller.py::TestValidationPeriod tests/test_victron_controller.py::TestValidationPeriod -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_hardware_validation.py tests/test_huawei_controller.py tests/test_victron_controller.py -q`
- **Per wave merge:** `python -m pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_hardware_validation.py` -- covers HWVAL-01 (connectivity), HWVAL-02 (write-back verify)
- [ ] New test classes in `tests/test_huawei_controller.py` -- covers HWVAL-03 + HWVAL-04 for Huawei
- [ ] New test classes in `tests/test_victron_controller.py` -- covers HWVAL-03 + HWVAL-04 for Victron

## Project Constraints (from CLAUDE.md)

- **Graceful degradation:** every external dep must be optional -- None checks, never crash. Validation failures should log and continue in degraded mode, not crash.
- **Safety:** each battery enters safe state independently on comm loss (3 consecutive failures -> zero setpoint). Safe-state writes MUST NOT be blocked by dry_run.
- **Python conventions:** snake_case, PascalCase for dataclasses/enums, `from __future__ import annotations`, type hints, `logger = logging.getLogger(__name__)`, 4-space indent, 88-char lines.
- **Config pattern:** dataclass with `@classmethod from_env()` reading `os.environ` via `_require_env()` or `os.environ.get()`.
- **Error handling:** explicit exceptions, never bare `except:`.
- **Tests:** `tests/test_*.py` with `pytest` + `anyio`, `@pytest.mark.anyio` for async tests.
- **Imports:** stdlib, third-party, local (blank-line separated), absolute imports.
- **Before committing:** Run `caliber refresh` then stage doc files.

## Sources

### Primary (HIGH confidence)
- `backend/drivers/huawei_driver.py` -- all 4 Huawei write methods, register names, reconnect pattern
- `backend/drivers/victron_driver.py` -- Victron write_ac_power_setpoint, Hub4 register addresses, _signed16 helper
- `backend/config.py` -- existing config dataclass pattern (12+ examples)
- `backend/huawei_controller.py` -- execute() pattern, safe-state in _handle_failure()
- `backend/victron_controller.py` -- execute() pattern, ESS mode guard, safe-state
- `backend/main.py` -- lifespan wiring, driver connect/disconnect sequence
- `backend/controller_model.py` -- DecisionEntry for dry_run audit logging
- `tests/test_huawei_controller.py` -- existing test patterns with AsyncMock

### Secondary (MEDIUM confidence)
- CONTEXT.md notes on SDongle single-connection limitation
- CONTEXT.md notes on Huawei power limits being ceilings

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- zero new deps, all existing libraries
- Architecture: HIGH -- patterns directly derived from existing codebase
- Pitfalls: HIGH -- derived from existing driver code and CONTEXT.md hardware notes

**Research date:** 2026-03-24
**Valid until:** 2026-04-24 (stable -- no external dependency changes)
