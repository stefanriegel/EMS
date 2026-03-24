# Phase 1: Victron Modbus TCP Driver - Research

**Researched:** 2026-03-22
**Domain:** Victron Venus OS Modbus TCP + pymodbus async client + driver protocol abstraction
**Confidence:** HIGH

## Summary

This phase replaces the existing MQTT-based Victron driver with a Modbus TCP implementation while preserving the public API surface (`read_system_state()`, `write_ac_power_setpoint()`, etc.) and the `VictronSystemData`/`VictronPhaseData` dataclasses. The Victron Venus OS GX device exposes all relevant registers over Modbus TCP on port 502, organized by unit ID (100 for system-level aggregates, 227+ for VE.Bus inverter registers). pymodbus 3.11+ provides `AsyncModbusTcpClient` with automatic reconnection and the `device_id` parameter for unit addressing.

The core challenge is mapping MQTT topic paths to Modbus register addresses with correct data types, scale factors, and sign conventions. The register list is well-documented by Victron (CCGX-Modbus-TCP-register-list.xlsx) and verified against multiple community implementations. The existing Huawei driver provides a proven pattern for `_with_reconnect()`, async context manager, and dataclass-based returns that the Victron driver must mirror.

**Primary recommendation:** Implement a drop-in replacement of `VictronDriver` using `pymodbus.client.AsyncModbusTcpClient`, batching consecutive registers in single `read_holding_registers()` calls, and create a `BatteryDriver` Protocol class in `backend/drivers/protocol.py` for structural typing.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D-01: Create a Python `Protocol` class (`BatteryDriver`) for structural typing -- no ABC, no inheritance
- D-02: Protocol defines: `async connect()`, `async close()`, `async read_state() -> BatteryState`, `async write_setpoint(watts: float)` -- plus system-specific extensions
- D-03: Both `HuaweiDriver` and `VictronDriver` satisfy the protocol implicitly (structural subtyping) -- no changes to Huawei class signature needed
- D-04: Protocol lives in `backend/drivers/protocol.py` -- imported by orchestrator for type hints
- D-05: Two separate configurable unit IDs: `vebus_unit_id` (default 227) for inverter registers, `system_unit_id` (default 100) for system-level registers (SoC, battery power)
- D-06: Env vars: `VICTRON_VEBUS_UNIT_ID` and `VICTRON_SYSTEM_UNIT_ID` -- configurable because Venus OS assigns unit IDs dynamically based on connected devices
- D-07: Default port changes from 1883 (MQTT) to 502 (Modbus TCP)
- D-08: Replace MQTT implementation in-place -- same `VictronDriver` class name, same public method signatures, new Modbus TCP backend
- D-09: Remove paho-mqtt dependency from VictronDriver entirely (paho-mqtt may remain for other uses like EVCC)
- D-10: `VictronSystemData` and `VictronPhaseData` dataclasses stay unchanged -- only the driver internals change
- D-11: Use pymodbus 3.11+ `AsyncModbusTcpClient` for all Modbus TCP communication
- D-12: Batch consecutive registers in single `read_holding_registers()` calls to minimize round-trips
- D-13: System registers (unit 100): SoC, battery power, battery voltage, battery current
- D-14: VE.Bus registers (unit 227+): per-phase AC power (L1/L2/L3), grid power, ESS mode, AC power setpoint writes
- D-15: Register addresses follow Venus OS Modbus TCP register list (documented by Victron for firmware v3.20+)
- D-16: Adopt Huawei's proven `_with_reconnect()` pattern -- on `ConnectionException`, attempt one reconnect, then raise
- D-17: Connection health check: attempt a single register read on `connect()` to verify the link is live
- D-18: Stale data detection: timestamp each successful read, orchestrator treats data older than `2 * loop_interval_s` as stale
- D-19: Victron Modbus registers use their native convention internally; conversion to canonical (positive = charge, negative = discharge) happens only in the driver's read methods
- D-20: Write methods accept canonical convention and convert to Victron-native before writing registers

### Claude's Discretion
- Exact register grouping boundaries for batched reads
- Internal helper method organization within the driver
- Specific pymodbus client configuration (timeout, retries)
- Test fixture structure and mock patterns for pymodbus

### Deferred Ideas (OUT OF SCOPE)
- Formal ABC/Protocol enforcement via `isinstance` or `runtime_checkable` -- not needed for 2 drivers, revisit if third battery added (ECO-01)
- Auto-discovery of Victron unit IDs via Modbus scan -- manual config is sufficient for v1
- Venus OS firmware version detection via Modbus -- too fragile, document supported versions instead
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DRV-01 | Victron MultiPlus-II controlled via Modbus TCP (replacing MQTT) | Full register map documented; pymodbus AsyncModbusTcpClient verified; drop-in replacement strategy confirmed |
| DRV-02 | Victron Modbus TCP driver reads system state (SoC, per-phase power, grid power, ESS mode) | Register addresses verified: system unit 100 (SoC=843, power=842, voltage=840, current=841, grid=820-822), vebus unit 227 (AC output=23-25, state=31, mode=33) |
| DRV-03 | Victron Modbus TCP driver writes ESS setpoints (total and per-phase AC power) | Write registers verified: vebus unit 227, L1=37, L2=40, L3=41 (int16, scale 1, watts); DisableCharge=38, DisableFeedIn=39 |
| DRV-04 | Victron Modbus unit IDs configurable (not hardcoded) | `VICTRON_VEBUS_UNIT_ID` (default 227) and `VICTRON_SYSTEM_UNIT_ID` (default 100) env vars; VictronConfig dataclass extended |
| DRV-05 | Huawei driver retained from v1, adapted to work with per-battery controller interface | BatteryDriver Protocol class (structural typing) in `backend/drivers/protocol.py`; Huawei already satisfies it implicitly |
| DRV-06 | Canonical sign convention: positive = charge, negative = discharge, conversion only in drivers | Victron system registers already use positive=charge for battery_power_w; vebus AC output needs scale factor application; setpoint writes are int16 in watts |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pymodbus | 3.12.1 (latest stable, pinned >=3.11,<4 in pyproject.toml) | Modbus TCP async client | Already in project deps; industry standard Python Modbus library; AsyncModbusTcpClient with auto-reconnect |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | 8+ | Unit testing | Already configured with anyio backend |
| pytest-anyio | (existing) | Async test support | For testing async Modbus read/write operations |
| pytest-mock | (existing) | Mocking | For mocking AsyncModbusTcpClient responses |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| pymodbus | pyModbusTCP | pyModbusTCP is simpler but lacks async support; pymodbus is already a project dependency |

**Installation:**
No new packages needed -- pymodbus >=3.11 is already in `pyproject.toml` dependencies.

**Version note:** pymodbus 3.10.0 renamed the `slave=` parameter to `device_id=` across all client methods. Since the project pins `>=3.11`, use `device_id=` exclusively. The older `slave=` parameter will not work.

## Architecture Patterns

### Recommended Project Structure
```
backend/drivers/
  __init__.py
  protocol.py          # NEW: BatteryDriver Protocol class
  huawei_driver.py     # Unchanged (already satisfies protocol)
  huawei_models.py     # Unchanged
  victron_driver.py    # REWRITTEN: Modbus TCP replacing MQTT
  victron_models.py    # Unchanged
```

### Pattern 1: Victron Modbus Register Map (Constants)
**What:** Define all register addresses, unit IDs, data types, and scale factors as module-level constants at the top of victron_driver.py.
**When to use:** Always -- register addresses are the contract with the Venus OS firmware.
**Example:**
```python
# Source: Victron CCGX-Modbus-TCP-register-list.xlsx + attributes.csv
# https://github.com/victronenergy/dbus_modbustcp/blob/master/attributes.csv

# --- System registers (unit_id=100) ---
_SYS_REG_BATTERY_VOLTAGE = 840    # uint16, scale 10 (divide by 10 for volts)
_SYS_REG_BATTERY_CURRENT = 841    # int16,  scale 10 (divide by 10 for amps)
_SYS_REG_BATTERY_POWER   = 842    # int16,  scale 1  (watts, positive=charging)
_SYS_REG_BATTERY_SOC     = 843    # uint16, scale 1  (percent 0-100)
_SYS_REG_GRID_L1_POWER   = 820    # int16,  scale 1  (watts)
_SYS_REG_GRID_L2_POWER   = 821    # int16,  scale 1  (watts)
_SYS_REG_GRID_L3_POWER   = 822    # int16,  scale 1  (watts)

# --- VE.Bus registers (unit_id=227 default) ---
_VB_REG_AC_OUT_L1_P = 23  # int16, scale 0.1 (VA, divide raw by 10 -> 0.1 factor)
_VB_REG_AC_OUT_L2_P = 24  # int16, scale 0.1
_VB_REG_AC_OUT_L3_P = 25  # int16, scale 0.1
_VB_REG_AC_OUT_L1_I = 18  # int16, scale 10  (divide by 10 for amps)
_VB_REG_AC_OUT_L2_I = 19  # int16, scale 10
_VB_REG_AC_OUT_L3_I = 20  # int16, scale 10
_VB_REG_AC_OUT_L1_V = 15  # uint16, scale 10 (divide by 10 for volts)
_VB_REG_AC_OUT_L2_V = 16  # uint16, scale 10
_VB_REG_AC_OUT_L3_V = 17  # uint16, scale 10
_VB_REG_STATE        = 31  # uint16, scale 1
_VB_REG_MODE         = 33  # uint16, scale 1

# --- VE.Bus Hub4 writable registers ---
_VB_REG_HUB4_L1_SETPOINT    = 37  # int16, scale 1, W (writable)
_VB_REG_HUB4_L2_SETPOINT    = 40  # int16, scale 1, W (writable)
_VB_REG_HUB4_L3_SETPOINT    = 41  # int16, scale 1, W (writable)
_VB_REG_HUB4_DISABLE_CHARGE = 38  # uint16, 0=allowed, 1=disabled (writable)
_VB_REG_HUB4_DISABLE_FEEDIN = 39  # uint16, 0=allowed, 1=disabled (writable)
```

### Pattern 2: Batched Register Reads
**What:** Read consecutive registers in a single `read_holding_registers()` call to minimize TCP round-trips.
**When to use:** For groups of registers with addresses close together (e.g., 840-843 for system battery data, 15-25 for vebus AC output).
**Example:**
```python
# Batch system battery registers (840-843, 4 consecutive)
result = await self._client.read_holding_registers(
    address=840, count=4, device_id=self._system_unit_id
)
if result.isError():
    raise ConnectionError(f"System register read failed: {result}")
regs = result.registers  # [voltage_raw, current_raw, power_raw, soc_raw]
battery_voltage_v = regs[0] / 10.0   # uint16, scale 10
battery_current_a = _signed16(regs[1]) / 10.0  # int16, scale 10
battery_power_w   = _signed16(regs[2])  # int16, scale 1 (already watts)
battery_soc_pct   = float(regs[3])      # uint16, scale 1

# Grid power registers (820-822, 3 consecutive)
result = await self._client.read_holding_registers(
    address=820, count=3, device_id=self._system_unit_id
)
grid_regs = result.registers
grid_l1_w = _signed16(grid_regs[0])
grid_l2_w = _signed16(grid_regs[1])
grid_l3_w = _signed16(grid_regs[2])
```

### Pattern 3: Signed 16-bit Conversion Helper
**What:** pymodbus returns unsigned 16-bit values; int16 registers need manual sign conversion.
**When to use:** For every register documented as `int16` in the Victron register list.
**Example:**
```python
def _signed16(value: int) -> int:
    """Convert unsigned 16-bit register value to signed int16."""
    return value - 0x10000 if value >= 0x8000 else value
```

### Pattern 4: _with_reconnect() Pattern (from Huawei driver)
**What:** Wrap every Modbus operation in a try/reconnect/retry helper.
**When to use:** All read and write operations.
**Example:**
```python
from pymodbus.exceptions import ModbusException, ConnectionException

async def _with_reconnect(self, coro_factory):
    """Execute coro_factory() and retry once on connection failure."""
    try:
        return await coro_factory()
    except (ModbusException, ConnectionException) as exc:
        logger.warning(
            "Modbus connection lost to %s:%d (%s) -- reconnecting",
            self.host, self.port, type(exc).__name__,
        )
        self._client.close()
        await self._client.connect()
        return await coro_factory()
```

### Pattern 5: BatteryDriver Protocol
**What:** Structural typing protocol for driver abstraction.
**When to use:** Import in orchestrator for type hints; both drivers satisfy implicitly.
**Example:**
```python
# backend/drivers/protocol.py
from __future__ import annotations
from typing import Protocol, runtime_checkable

@runtime_checkable  # optional, only needed if isinstance checks desired later
class BatteryDriver(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def read_state(self) -> object: ...
    async def write_setpoint(self, watts: float) -> None: ...
    async def __aenter__(self) -> "BatteryDriver": ...
    async def __aexit__(self, *args) -> None: ...
```

Note: Per D-01, do NOT use `@runtime_checkable` unless there is a concrete need. Plain `Protocol` is sufficient for type-checker-only structural subtyping.

### Anti-Patterns to Avoid
- **Reading registers one at a time:** Each `read_holding_registers()` is a TCP round-trip. Batch consecutive registers.
- **Using `slave=` parameter:** pymodbus 3.11+ renamed it to `device_id=`. Using `slave=` will cause a TypeError.
- **Assuming unsigned for all registers:** Many Victron registers are signed int16 (battery current, battery power, grid power, AC output power). Always apply `_signed16()` for int16 types.
- **Forgetting scale factors:** AC output power registers (23-25) have scale factor 0.1 (raw value / 10), voltage registers (15-17) have scale 10 (raw / 10), current registers (18-20) have scale 10 (raw / 10).
- **Mixing unit IDs:** System registers (SoC, grid power) use unit 100; VE.Bus registers (AC output, setpoints) use unit 227. Mixing them up returns garbage data with no error.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Modbus TCP framing | Raw TCP socket + manual PDU assembly | pymodbus AsyncModbusTcpClient | Handles framing, timeouts, retries, reconnection automatically |
| Signed int16 conversion | Ad-hoc bitwise operations scattered throughout | Single `_signed16()` helper at module level | Consistent, tested, single point of truth |
| Connection management | Manual socket lifecycle | pymodbus auto-reconnect + `_with_reconnect()` wrapper | pymodbus handles TCP reconnection internally; wrapper handles Modbus-level errors |

## Common Pitfalls

### Pitfall 1: Scale Factor Confusion
**What goes wrong:** Register values appear wildly wrong (e.g., battery voltage reads as 4820 instead of 48.2V).
**Why it happens:** The Victron register list documents scale factors inconsistently. Some say "scale 10" meaning "multiply raw by 10" (for register storage), but the actual usage is "divide raw by 10" to get real values. The attributes.csv column header says "scalefactor" but the semantic is: `real_value = raw_register_value / scalefactor` for values > 1, and `real_value = raw_register_value * scalefactor` for scalefactor < 1.
**How to avoid:** For each register, verify against the MQTT driver's existing behavior. The MQTT path values arrive pre-scaled; Modbus values are raw integers that need scaling.
**Warning signs:** Values that are 10x or 100x too large or too small.

### Pitfall 2: Victron Battery Power Sign Convention
**What goes wrong:** Battery appears to charge when it's actually discharging.
**Why it happens:** The existing `VictronSystemData` uses positive = charging, negative = discharging. Victron's Modbus register 842 (Dc/Battery/Power) already uses positive = charging. So NO sign flip is needed for battery_power_w from the system unit. However, the ESS setpoint registers (37, 40, 41) use positive = import from grid (which charges battery), negative = export to grid (which discharges battery). This matches the existing MQTT convention, so setpoint writes also need no sign flip.
**How to avoid:** Document the convention for each register explicitly. The canonical convention (DRV-06: positive = charge) matches Victron's native convention for both reads and writes.
**Warning signs:** `charge_power_w` and `discharge_power_w` properties return wrong values.

### Pitfall 3: pymodbus `device_id` vs `slave` Parameter
**What goes wrong:** `TypeError: unexpected keyword argument 'slave'` at runtime.
**Why it happens:** pymodbus 3.10.0 renamed `slave=` to `device_id=` across all client methods. The project pins `>=3.11`.
**How to avoid:** Always use `device_id=` in all pymodbus calls. Never use `slave=`.
**Warning signs:** Code works with pymodbus 3.8 but fails after upgrade.

### Pitfall 4: Non-Consecutive Register Gaps
**What goes wrong:** Attempting to read registers 15-25 (11 registers) in a single batch, but register 21-22 may not exist or may be different data.
**Why it happens:** Not all register addresses in a range are defined. Reading undefined registers may return 0 or cause an error depending on Venus OS firmware version.
**How to avoid:** Only batch registers that are documented and consecutive. For the vebus AC output:
- Voltage: 15, 16, 17 (3 consecutive) -- safe to batch
- Current: 18, 19, 20 (3 consecutive) -- safe to batch
- Power: 23, 24, 25 (3 consecutive) -- safe to batch
- But 15-25 as a single batch includes undefined registers 21, 22 -- risky.
**Warning signs:** Intermittent errors on some Venus OS firmware versions.

### Pitfall 5: ESS Mode Register Location
**What goes wrong:** Trying to read/write Hub4Mode via vebus unit ID.
**Why it happens:** In MQTT, Hub4Mode is at `settings/0/Settings/CGwacs/Hub4Mode`. In Modbus TCP, the settings service (`com.victronenergy.settings`) has its own unit ID (typically 0 or 100 depending on firmware). The CCGX register list maps Hub4Mode to a specific register under the settings service, not the vebus service.
**How to avoid:** For ESS mode reads, use the system-level register (unit 100). For enabling ESS external control (mode 3), write once during `connect()` using the vebus Mode register (33) or the settings service. Verify against the actual GX device during integration testing.
**Warning signs:** ESS mode reads return None or unexpected values.

## Code Examples

### Complete read_system_state() Implementation Pattern
```python
# Source: Victron CCGX-Modbus-TCP-register-list + attributes.csv
# https://github.com/victronenergy/dbus_modbustcp/blob/master/attributes.csv

async def read_system_state(self) -> VictronSystemData:
    """Read full system state from Venus OS via Modbus TCP."""

    async def _do() -> VictronSystemData:
        # --- System unit: battery registers 840-843 (4 consecutive) ---
        bat_result = await self._client.read_holding_registers(
            address=840, count=4, device_id=self._system_unit_id
        )
        if bat_result.isError():
            raise ConnectionError(f"Battery register read failed: {bat_result}")
        br = bat_result.registers
        battery_voltage_v = br[0] / 10.0     # reg 840, uint16, /10
        battery_current_a = _signed16(br[1]) / 10.0  # reg 841, int16, /10
        battery_power_w = float(_signed16(br[2]))     # reg 842, int16, W
        battery_soc_pct = float(br[3])                # reg 843, uint16, %

        # --- System unit: grid power 820-822 (3 consecutive) ---
        grid_result = await self._client.read_holding_registers(
            address=820, count=3, device_id=self._system_unit_id
        )
        if grid_result.isError():
            raise ConnectionError(f"Grid register read failed: {grid_result}")
        gr = grid_result.registers
        grid_l1_w = float(_signed16(gr[0]))
        grid_l2_w = float(_signed16(gr[1]))
        grid_l3_w = float(_signed16(gr[2]))

        # --- VE.Bus unit: AC output voltage 15-17 (3 consecutive) ---
        volt_result = await self._client.read_holding_registers(
            address=15, count=3, device_id=self._vebus_unit_id
        )
        vr = volt_result.registers
        l1_voltage = vr[0] / 10.0  # uint16, /10
        l2_voltage = vr[1] / 10.0
        l3_voltage = vr[2] / 10.0

        # --- VE.Bus unit: AC output current 18-20 (3 consecutive) ---
        cur_result = await self._client.read_holding_registers(
            address=18, count=3, device_id=self._vebus_unit_id
        )
        cr = cur_result.registers
        l1_current = _signed16(cr[0]) / 10.0  # int16, /10
        l2_current = _signed16(cr[1]) / 10.0
        l3_current = _signed16(cr[2]) / 10.0

        # --- VE.Bus unit: AC output power 23-25 (3 consecutive) ---
        pwr_result = await self._client.read_holding_registers(
            address=23, count=3, device_id=self._vebus_unit_id
        )
        pr = pwr_result.registers
        l1_power = _signed16(pr[0]) * 0.1  # int16, scale 0.1 (raw * 0.1)
        l2_power = _signed16(pr[1]) * 0.1
        l3_power = _signed16(pr[2]) * 0.1

        # --- VE.Bus unit: state (31) and mode (33) ---
        state_result = await self._client.read_holding_registers(
            address=31, count=1, device_id=self._vebus_unit_id
        )
        mode_result = await self._client.read_holding_registers(
            address=33, count=1, device_id=self._vebus_unit_id
        )
        vebus_state = state_result.registers[0] if not state_result.isError() else None
        vebus_mode = mode_result.registers[0] if not mode_result.isError() else None

        return VictronSystemData(
            battery_soc_pct=battery_soc_pct,
            battery_power_w=battery_power_w,
            battery_current_a=battery_current_a,
            battery_voltage_v=battery_voltage_v,
            l1=VictronPhaseData(
                power_w=l1_power, current_a=l1_current,
                voltage_v=l1_voltage, setpoint_w=None,
            ),
            l2=VictronPhaseData(
                power_w=l2_power, current_a=l2_current,
                voltage_v=l2_voltage, setpoint_w=None,
            ),
            l3=VictronPhaseData(
                power_w=l3_power, current_a=l3_current,
                voltage_v=l3_voltage, setpoint_w=None,
            ),
            ess_mode=None,  # May need separate settings read
            system_state=None,
            vebus_state=vebus_state,
            grid_power_w=grid_l1_w + grid_l2_w + grid_l3_w,
            grid_l1_power_w=grid_l1_w,
            grid_l2_power_w=grid_l2_w,
            grid_l3_power_w=grid_l3_w,
            consumption_w=None,  # Not directly available via simple register read
            pv_on_grid_w=None,
            timestamp=time.monotonic(),
        )

    return await self._with_reconnect(_do)
```

### Write Setpoint Pattern
```python
async def write_ac_power_setpoint(self, phase: int, watts: float) -> None:
    """Write per-phase AcPowerSetpoint via Modbus TCP.

    Positive watts = import from grid (charge).
    Negative watts = export to grid (discharge).
    """
    reg_map = {1: 37, 2: 40, 3: 41}  # L1, L2, L3
    register = reg_map[phase]
    # int16: clamp to -32768..32767, convert to unsigned for Modbus
    value = int(max(-32768, min(32767, watts)))

    async def _do() -> None:
        result = await self._client.write_register(
            address=register, value=value & 0xFFFF,
            device_id=self._vebus_unit_id,
        )
        if result.isError():
            raise ConnectionError(f"Setpoint write failed for L{phase}: {result}")
        logger.debug("Modbus tx: reg=%d value=%d (L%d setpoint)", register, value, phase)

    await self._with_reconnect(_do)
```

### pymodbus Client Setup Pattern
```python
from pymodbus.client import AsyncModbusTcpClient

class VictronDriver:
    def __init__(
        self,
        host: str,
        port: int = 502,
        timeout_s: float = 5.0,
        system_unit_id: int = 100,
        vebus_unit_id: int = 227,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self._system_unit_id = system_unit_id
        self._vebus_unit_id = vebus_unit_id
        self._client = AsyncModbusTcpClient(
            host=host,
            port=port,
            timeout=timeout_s,
            retries=1,        # We handle retries via _with_reconnect
        )

    async def connect(self) -> None:
        connected = await self._client.connect()
        if not connected:
            raise ConnectionError(
                f"Failed to connect to Victron Modbus at {self.host}:{self.port}"
            )
        # Health check: read battery SoC to verify link is live (D-17)
        result = await self._client.read_holding_registers(
            address=843, count=1, device_id=self._system_unit_id
        )
        if result.isError():
            raise ConnectionError(
                f"Health check failed: cannot read SoC register: {result}"
            )
        logger.info(
            "Victron Modbus connected: %s:%d (system_id=%d, vebus_id=%d, SoC=%.0f%%)",
            self.host, self.port, self._system_unit_id, self._vebus_unit_id,
            float(result.registers[0]),
        )

    async def close(self) -> None:
        self._client.close()
        logger.debug("Victron Modbus disconnected from %s:%d", self.host, self.port)
```

### Mock Pattern for Tests
```python
from unittest.mock import AsyncMock, MagicMock, PropertyMock
from pymodbus.pdu import ModbusResponse

def _mock_register_response(registers: list[int]) -> MagicMock:
    """Create a mock Modbus response with given register values."""
    resp = MagicMock()
    resp.isError.return_value = False
    resp.registers = registers
    return resp

def _mock_error_response() -> MagicMock:
    resp = MagicMock()
    resp.isError.return_value = True
    resp.registers = []
    return resp

@pytest.fixture
def mock_modbus_client():
    client = AsyncMock()
    client.connect = AsyncMock(return_value=True)
    client.close = MagicMock()
    client.read_holding_registers = AsyncMock()
    client.write_register = AsyncMock()
    return client
```

## Victron Modbus TCP Register Reference

### System Unit (unit_id=100, com.victronenergy.system)

| Register | Path | Type | Scale | Unit | Access | Notes |
|----------|------|------|-------|------|--------|-------|
| 840 | /Dc/Battery/Voltage | uint16 | 10 | V | R | Divide by 10 |
| 841 | /Dc/Battery/Current | int16 | 10 | A | R | Divide by 10, positive=charging |
| 842 | /Dc/Battery/Power | int16 | 1 | W | R | Positive=charging |
| 843 | /Dc/Battery/Soc | uint16 | 1 | % | R | 0-100 |
| 820 | /Ac/Grid/L1/Power | int16 | 1 | W | R | Positive=importing |
| 821 | /Ac/Grid/L2/Power | int16 | 1 | W | R | Positive=importing |
| 822 | /Ac/Grid/L3/Power | int16 | 1 | W | R | Positive=importing |

### VE.Bus Unit (unit_id=227 default, com.victronenergy.vebus)

| Register | Path | Type | Scale | Unit | Access | Notes |
|----------|------|------|-------|------|--------|-------|
| 15 | /Ac/Out/L1/V | uint16 | 10 | V | R | Divide by 10 |
| 16 | /Ac/Out/L2/V | uint16 | 10 | V | R | Divide by 10 |
| 17 | /Ac/Out/L3/V | uint16 | 10 | V | R | Divide by 10 |
| 18 | /Ac/Out/L1/I | int16 | 10 | A | R | Divide by 10 |
| 19 | /Ac/Out/L2/I | int16 | 10 | A | R | Divide by 10 |
| 20 | /Ac/Out/L3/I | int16 | 10 | A | R | Divide by 10 |
| 23 | /Ac/Out/L1/P | int16 | 0.1 | VA | R | Multiply by 0.1 |
| 24 | /Ac/Out/L2/P | int16 | 0.1 | VA | R | Multiply by 0.1 |
| 25 | /Ac/Out/L3/P | int16 | 0.1 | VA | R | Multiply by 0.1 |
| 31 | /State | uint16 | 1 | - | R | VE.Bus state |
| 33 | /Mode | uint16 | 1 | - | W | VE.Bus mode |
| 37 | /Hub4/L1/AcPowerSetpoint | int16 | 1 | W | W | Positive=charge, negative=discharge |
| 38 | /Hub4/DisableCharge | uint16 | 1 | - | W | 0=allowed, 1=disabled |
| 39 | /Hub4/DisableFeedIn | uint16 | 1 | - | W | 0=allowed, 1=disabled |
| 40 | /Hub4/L2/AcPowerSetpoint | int16 | 1 | W | W | Same as L1 |
| 41 | /Hub4/L3/AcPowerSetpoint | int16 | 1 | W | W | Same as L1 |

### Scale Factor Interpretation
The Victron register list uses "scalefactor" with an inconsistent semantic:
- **scalefactor = 10** means: `real_value = raw / 10` (e.g., voltage 4820 -> 482.0V; current 52 -> 5.2A)
- **scalefactor = 0.1** means: `real_value = raw * 0.1` (e.g., power 15000 -> 1500.0 VA)
- **scalefactor = 1** means: `real_value = raw` (no conversion needed)

### Sign Convention Summary
| Register Group | Victron Native | Canonical (DRV-06) | Conversion Needed |
|----------------|----------------|---------------------|-------------------|
| Battery power (842) | positive=charging | positive=charge | None |
| Battery current (841) | positive=charging | positive=charge | None |
| Grid power (820-822) | positive=importing | positive=importing | None (grid, not battery) |
| AC output power (23-25) | sign follows current | report as-is | None |
| AcPowerSetpoint (37,40,41) | positive=charge/import | positive=charge | None |

Key finding: Victron's native Modbus sign convention already matches the project's canonical convention (positive = charge). No sign flips needed in either read or write methods.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| paho-mqtt MQTT driver | pymodbus Modbus TCP driver | This phase | Eliminates MQTT dependency for Victron; direct register access; deterministic reads instead of subscription-based |
| `slave=` parameter in pymodbus | `device_id=` parameter | pymodbus 3.10.0 | Must use `device_id=` with pinned >=3.11 |
| VictronDriver with discovery_timeout_s | VictronDriver with unit IDs | This phase | No more MQTT discovery step; unit IDs configured at startup |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-anyio |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `python -m pytest tests/drivers/test_victron_driver.py -x` |
| Full suite command | `python -m pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DRV-01 | Victron driver uses Modbus TCP client, not MQTT | unit | `python -m pytest tests/drivers/test_victron_driver.py -x -k "modbus"` | Rewrite needed |
| DRV-02 | read_system_state returns correct VictronSystemData from Modbus registers | unit | `python -m pytest tests/drivers/test_victron_driver.py -x -k "read_system"` | Rewrite needed |
| DRV-03 | write_ac_power_setpoint writes correct register with correct value | unit | `python -m pytest tests/drivers/test_victron_driver.py -x -k "write"` | Rewrite needed |
| DRV-04 | Unit IDs are configurable via constructor and VictronConfig.from_env() | unit | `python -m pytest tests/drivers/test_victron_driver.py -x -k "unit_id or config"` | Partial (config tests exist) |
| DRV-05 | Both drivers satisfy BatteryDriver Protocol | unit | `python -m pytest tests/drivers/test_protocol.py -x` | New file needed |
| DRV-06 | Sign convention: positive=charge in read_system_state output | unit | `python -m pytest tests/drivers/test_victron_driver.py -x -k "sign"` | Rewrite needed |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/drivers/test_victron_driver.py -x`
- **Per wave merge:** `python -m pytest tests/ -x`
- **Phase gate:** Full suite green before verify

### Wave 0 Gaps
- [ ] `tests/drivers/test_victron_driver.py` -- existing MQTT tests must be rewritten for Modbus mocks
- [ ] `tests/drivers/test_protocol.py` -- new file for BatteryDriver Protocol conformance tests
- [ ] Config tests in `tests/drivers/test_victron_driver.py::TestVictronConfig` -- need updates for new fields (vebus_unit_id, system_unit_id, port default 502)

## Open Questions

1. **AC output power registers: VA or W?**
   - What we know: Registers 23-25 are documented as "VA" (volt-amperes) not watts. The existing MQTT driver reads `/Ac/Out/L1/P` which also appears to be VA.
   - What's unclear: Whether the existing `VictronPhaseData.power_w` field should actually be VA, or whether Venus OS returns true watts here for resistive loads.
   - Recommendation: Keep as-is (report the register value as `power_w`). The existing MQTT driver does the same. For pure resistive loads VA equals W.

2. **ESS mode (Hub4Mode) register address via Modbus**
   - What we know: In MQTT, Hub4Mode is at `settings/0/Settings/CGwacs/Hub4Mode`. The Modbus settings service unit ID is not consistently documented across sources.
   - What's unclear: Exact register address for Hub4Mode in the settings service.
   - Recommendation: For the initial implementation, set ESS mode via the vebus Mode register (33, write mode=3 for external control) during `connect()`. Read ESS mode from the same register. If a separate settings register is needed, verify against the actual GX device.

3. **Consumption and PV-on-grid registers**
   - What we know: The MQTT driver reads `system/0/Ac/Consumption` and `system/0/Ac/PvOnGrid` for consumption_w and pv_on_grid_w. These may map to system registers 817+ but exact addresses need verification.
   - What's unclear: Whether these register addresses are stable across Venus OS versions.
   - Recommendation: Set `consumption_w` and `pv_on_grid_w` to `None` initially. The orchestrator handles None values gracefully. Add these registers in a follow-up once verified against real hardware.

## Sources

### Primary (HIGH confidence)
- [Victron dbus_modbustcp attributes.csv](https://github.com/victronenergy/dbus_modbustcp/blob/master/attributes.csv) -- authoritative register definitions, addresses, types, scale factors
- [Victron GX Modbus-TCP Manual](https://www.victronenergy.com/live/ccgx:modbustcp_faq) -- official setup and usage documentation
- [Victron CCGX-Modbus-TCP-register-list-3.70.xlsx](https://www.victronenergy.com/upload/documents/CCGX-Modbus-TCP-register-list-3.70.xlsx) -- latest official register spreadsheet
- [PyModbus 3.12.0 Client Documentation](https://pymodbus.readthedocs.io/en/stable/source/client.html) -- AsyncModbusTcpClient API reference
- [PyModbus Changelog](https://pymodbus.readthedocs.io/en/stable/source/changelog.html) -- slave->device_id rename in 3.10.0

### Secondary (MEDIUM confidence)
- [Home Assistant Victron Modbus TCP community post](https://community.home-assistant.io/t/home-assistant-and-victron-gx-multiplus-ii-managing-your-battery-using-modbus-tcp/724762) -- practical register usage verified against real hardware
- [victron-system-monitor ModbusRegister.php](https://github.com/rbritton/victron-system-monitor/blob/master/app/ModbusRegister.php) -- community implementation confirming register addresses and data types
- [Victron Community: ESS Mode Modbus TCP](https://community.victronenergy.com/t/ess-mode-modbus-tcp-confusion/5317) -- Hub4Mode register clarification

### Tertiary (LOW confidence)
- Scale factor interpretation for registers 23-25 (AC output power, 0.1 scale) -- documented in attributes.csv but the VA vs W distinction needs hardware verification

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- pymodbus already in project deps, version verified, API documented
- Architecture: HIGH -- register addresses verified against official Victron sources + community implementations; existing driver patterns provide clear template
- Pitfalls: HIGH -- multiple sources confirm int16 handling, scale factors, parameter rename; real-world community implementations validate register groupings

**Research date:** 2026-03-22
**Valid until:** 2026-06-22 (stable domain -- Modbus protocol and Victron register list change slowly)
