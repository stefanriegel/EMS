# Technology Stack

**Project:** EMS v2 -- Independent Dual-Battery Energy Management
**Researched:** 2026-03-22

## Recommended Stack

### Core Framework

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Python | 3.12+ | Backend runtime | Already in use. 3.12 brings TaskGroup, ExceptionGroup, and improved asyncio performance. No reason to jump to 3.13 unless needed. |
| FastAPI | 0.135+ | REST API + WebSocket server | Already deployed (0.135.1 in lockfile). Mature async framework, native Pydantic v2 integration. Keep current. |
| uvicorn[standard] | latest | ASGI server with uvloop | Already in use. The `[standard]` extra gives uvloop on Linux/aarch64 for ~2x event loop throughput. |
| React | 19.x | Dashboard SPA | Already at 19.2.4. React 19 concurrent features useful for real-time dashboard updates. Keep current. |
| Vite | 8.x | Frontend build | Already at 8.0.1. Fast HMR, proxy config for dev. Keep current. |
| TypeScript | 5.9+ | Type-safe frontend | Already at 5.9.3. Keep current. |

### Modbus Communication

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| pymodbus | >=3.12,<4 | Victron Modbus TCP driver (NEW) + general Modbus | **Critical change.** Already a dependency (3.12.1 in lockfile) but only used transitively by huawei-solar. For the Victron Modbus TCP rewrite, use pymodbus directly via `AsyncModbusTcpClient`. pymodbus 3.x has a stable async API, automatic reconnect support, and is the de facto Python Modbus library. |
| huawei-solar | >=2.5 | Huawei SUN2000/LUNA2000 driver | Already at 2.5.0. This wraps pymodbus with Huawei-specific register definitions. Keep using it -- no reason to drop down to raw pymodbus for Huawei when huawei-solar handles register naming, value parsing, and the 64-register gap constraint. |

**Why pymodbus over alternatives:**

| Library | Verdict | Reason |
|---------|---------|--------|
| **pymodbus >=3.12** | USE THIS | Async TCP client, auto-reconnect, mature (15+ years), 3.x API is stable. Already a transitive dependency. Community standard for Python Modbus. |
| umodbus | Do not use | Synchronous only. No async support. Dead project (last release 2020). |
| pyModbusTCP | Do not use | Thin wrapper, no async, limited register types. pymodbus is strictly superior. |
| minimalmodbus | Do not use | Serial only (RS-485). Does not support Modbus TCP at all. |
| ctmodbus | Do not use | Niche, minimal community. pymodbus has 100x the adoption. |

### MQTT (Retained for HA/EVCC, NOT for Victron control)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| paho-mqtt | >=2.1,<3 | HA MQTT discovery, EVCC MQTT monitoring | At 2.1.0 in lockfile. Victron control moves to Modbus TCP, but MQTT is still needed for HA entity publishing and EVCC state monitoring. paho-mqtt 2.x has the cleaner `CallbackAPIVersion.VERSION2` API already used in the codebase. |

### Database & Metrics

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| influxdb-client[async] | >=1.45 | Time-series metrics (optional) | Already in use with graceful degradation. Per-battery metrics will need new measurement schemas but the client is the same. |
| SQLite3 (stdlib) | -- | HA statistics for ML forecasting | Already used for consumption forecasting. No change needed. |

### Authentication & Security

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| python-jose[cryptography] | current | JWT tokens | Already in use. No change. |
| passlib[bcrypt] + bcrypt<4 | current | Password hashing | Already in use. bcrypt<4 pin avoids the API-breaking change in bcrypt 4.x. |

### ML & Forecasting

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| scikit-learn | >=1.4,<2 | Consumption forecasting | Already in use. No change for the dual-battery rewrite. |
| numpy | >=1.25,<3 | Numerical computing | Already in use. numpy 2.x is compatible. |

### HTTP Client

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| httpx | latest | Async HTTP (EVCC, HA REST, Telegram) | Already in use. No change needed. |

### Testing

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| pytest | >=8 | Test runner | Already in use. |
| pytest-anyio | latest | Async test support | Already in use with `anyio_mode = "auto"`. |
| pytest-mock | latest | Mocking | Already in use. |
| Playwright | 1.58+ | E2E frontend testing | Already in use. |

### Infrastructure

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Docker | -- | Container runtime | Single-stage Python 3.12 image serves backend + static frontend. |
| uv | latest | Python package manager | Already in use (291KB lockfile). Fast installs, deterministic resolution. |
| npm | latest | Frontend package manager | Already in use. |

## New Dependencies for v2

These are additions the dual-battery rewrite requires that are NOT currently installed:

| Library | Version | Purpose | Confidence |
|---------|---------|---------|------------|
| (none) | -- | -- | -- |

**Key finding: No new pip dependencies are required.** pymodbus is already installed (transitive via huawei-solar). The Victron Modbus TCP driver will use `pymodbus.client.AsyncModbusTcpClient` directly. All coordinator/controller logic is pure Python using asyncio primitives already in the codebase.

## Victron Modbus TCP Driver -- Technical Details

### Register Map (from Victron dbus_modbustcp)

The Victron Venus OS exposes dbus values over Modbus TCP via the `dbus-modbustcp` service. Key registers for ESS external control:

**Confidence: MEDIUM** -- Based on Victron's published CCGX Modbus TCP register list and community documentation. Exact register addresses should be verified against the actual Venus OS firmware version in production.

| Register | Address | Unit ID | R/W | Type | Description |
|----------|---------|---------|-----|------|-------------|
| ESS Mode (Hub4Mode) | 2902 | 100 (settings) | R/W | uint16 | 1=ESS with Phase Compensation, 2=ESS without, 3=External Control |
| AcPowerSetpoint L1 | 37 | 246 (Hub4) | R/W | int16 | Grid setpoint phase 1 in watts. Positive=import, negative=export |
| AcPowerSetpoint L2 | 40 | 246 (Hub4) | R/W | int16 | Grid setpoint phase 2 |
| AcPowerSetpoint L3 | 41 | 246 (Hub4) | R/W | int16 | Grid setpoint phase 3 |
| MaxChargePercentage | 2701 | 100 (settings) | R/W | uint16 | Max charge current as % of max |
| MaxDischargePercentage | 2702 | 100 (settings) | R/W | uint16 | Max discharge current as % of max |
| Battery SOC | 266 | 100 (system) | R | uint16 | Battery state of charge 0-100% (scale 10) |
| Battery Power | 258 | 100 (system) | R | int16 | Battery power in watts |
| Battery Voltage | 259 | 100 (system) | R | uint16 | Battery voltage (scale 10) |
| Battery Current | 261 | 100 (system) | R | int16 | Battery current (scale 10) |
| Grid Power L1 | 820 | 30 (grid meter) | R | int16 | Grid power phase 1 |
| Grid Power L2 | 821 | 30 (grid meter) | R | int16 | Grid power phase 2 |
| Grid Power L3 | 822 | 30 (grid meter) | R | int16 | Grid power phase 3 |
| VE.Bus State | 31 | 246 (vebus) | R | uint16 | VE.Bus operating state |
| DisableCharge | 38 | 246 (Hub4) | R/W | uint16 | 0=charge allowed, 1=charge disabled |
| DisableFeedIn | 39 | 246 (Hub4) | R/W | uint16 | 0=feed-in allowed, 1=feed-in disabled |

**Unit IDs:**
- 100: `com.victronenergy.system` and `com.victronenergy.settings`
- 246: `com.victronenergy.vebus` (VE.Bus/Multi device, Hub4 ESS registers)
- 30: `com.victronenergy.grid` (grid meter)

**IMPORTANT: Unit ID mapping varies by installation.** The Venus OS Modbus TCP service assigns unit IDs dynamically based on connected devices. The values above are typical defaults. The driver MUST either:
1. Use a configurable unit ID mapping (recommended), OR
2. Query the unit ID mapping at startup

### pymodbus AsyncModbusTcpClient Usage Pattern

```python
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

class VictronModbusDriver:
    """Victron Modbus TCP driver using pymodbus AsyncModbusTcpClient."""

    def __init__(self, host: str, port: int = 502) -> None:
        self._client = AsyncModbusTcpClient(
            host=host,
            port=port,
            timeout=10,
            retries=1,
            reconnect_delay=2.0,       # auto-reconnect after 2s
            reconnect_delay_max=30.0,   # backoff cap at 30s
        )

    async def connect(self) -> None:
        await self._client.connect()

    async def close(self) -> None:
        self._client.close()

    async def read_battery_soc(self) -> float:
        result = await self._client.read_holding_registers(
            address=266, count=1, slave=100
        )
        if result.isError():
            raise ModbusException(f"Read SOC failed: {result}")
        return result.registers[0] / 10.0

    async def write_ac_power_setpoint(self, phase: int, watts: int) -> None:
        address = {1: 37, 2: 40, 3: 41}[phase]
        result = await self._client.write_register(
            address=address, value=watts, slave=246
        )
        if result.isError():
            raise ModbusException(f"Write setpoint failed: {result}")
```

### Key pymodbus 3.x Features Used

- **`AsyncModbusTcpClient`**: Native asyncio, no thread bridging needed (unlike paho-mqtt). Eliminates the `call_soon_threadsafe` complexity in the current MQTT driver.
- **Auto-reconnect**: Built-in with configurable `reconnect_delay` and `reconnect_delay_max`. No manual `_with_reconnect` wrapper needed (though the Huawei driver pattern is still a good defense-in-depth approach).
- **`slave` parameter**: Maps to Modbus unit ID. Each register read/write specifies the unit ID directly.
- **Error handling**: `result.isError()` returns True on Modbus exceptions. The `ModbusException` class covers connection and protocol errors.

## Anti-Oscillation Patterns -- No External Libraries Needed

**Confidence: HIGH** -- These are well-established control theory patterns. No specialized library is required; implement in pure Python.

The v2 coordinator needs these anti-oscillation mechanisms, all implementable without external dependencies:

### 1. Hysteresis Dead-Band (Already Partially Implemented)
The current orchestrator has hysteresis. Extend it per-battery:

```python
@dataclass
class HysteresisConfig:
    dead_band_w: float = 50.0        # Ignore power changes < 50W
    min_change_interval_s: float = 5.0  # Min seconds between setpoint changes
    soc_dead_band_pct: float = 1.0   # Ignore SoC differences < 1%
```

### 2. Soft-Start / Soft-Stop Ramp Limiter
Prevents step changes that cause oscillation:

```python
@dataclass
class RampConfig:
    max_ramp_rate_w_per_s: float = 500.0  # Max 500W/s change rate
    charge_ramp_rate_w_per_s: float = 300.0  # Slower for charging
```

**Implementation:** Each battery controller maintains `last_setpoint_w` and `last_setpoint_time`. New setpoints are clamped to `last + max_ramp * dt`.

### 3. Role-Based Priority with Non-Overlapping Zones
Prevents both batteries from chasing the same load:

```python
class BatteryRole(str, Enum):
    BASE_LOAD = "BASE_LOAD"       # Covers steady-state consumption
    PEAK_SHAVING = "PEAK_SHAVING" # Covers transient spikes
    GRID_CHARGE = "GRID_CHARGE"   # Charging from grid during cheap tariff
    IDLE = "IDLE"                  # Not dispatched
```

The coordinator assigns roles based on SoC, tariff, PV conditions. Only ONE battery responds to a given load signal at a time.

### 4. Coordinator Arbitration Loop
The coordinator runs a single control loop (2-5 second cycle) that:
1. Reads both battery states
2. Computes total demand
3. Allocates demand to batteries based on roles/SoC/capacity
4. Sends setpoints to per-battery controllers
5. Each controller applies its own ramp limiting and hysteresis

**No external control library (like `simple-pid` or `python-control`) is needed.** The system is a discrete dispatch allocator, not a continuous PID control loop. PID controllers are wrong for battery dispatch because:
- Battery power is set via discrete register writes, not continuous actuators
- The "plant" response is near-instantaneous (inverter follows setpoint within 1-2 seconds)
- Integral windup in PID causes exactly the oscillation we are trying to avoid

## Frontend Additions for Dashboard

| Library | Version | Purpose | Confidence |
|---------|---------|---------|------------|
| recharts | 2.x | Battery power/SoC time-series charts | MEDIUM -- lightweight, React-native, no D3 dependency. Alternative: use the existing WebSocket data with simple CSS bar/gauge components if charts are not needed initially. |

**Decision deferred:** The dashboard rewrite scope determines whether a charting library is needed at all. The current frontend has no charting library -- it uses custom components. If per-battery time-series visualization is required, recharts is the lightest option that integrates cleanly with React 19.

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Modbus library | pymodbus >=3.12 | umodbus, pyModbusTCP | No async support, dead/minimal projects |
| Victron control protocol | Modbus TCP | MQTT (current) | MQTT uses topic-based writes via Venus dbus-flashmq which adds latency, has no write confirmation, and requires MQTT keepalive dance. Modbus TCP gives synchronous write-ack per register. |
| Anti-oscillation | Pure Python (hysteresis + ramp + roles) | simple-pid, python-control | PID is wrong for discrete battery dispatch. Over-engineering for a system with 2 actuators and 2-5s cycle time. |
| State machine | Python Enum + match/case | transitions, pytransitions | Two controllers with 4-5 states each do not justify a state machine library. `match/case` (Python 3.10+) is cleaner and type-checkable. |
| Async framework | FastAPI + asyncio | Trio, AnyIO directly | FastAPI already uses asyncio. Switching async runtime mid-project is high risk for zero benefit. |
| Frontend charting | recharts (if needed) | Chart.js, D3, visx | Chart.js needs a React wrapper. D3 is overkill. visx is complex. recharts is purpose-built for React. |
| Package manager | uv | pip, poetry, pdm | uv is already in use, fastest resolver, deterministic lockfile. |

## Installation

```bash
# No new dependencies needed for core v2 rewrite.
# pymodbus is already installed as a transitive dependency of huawei-solar.

# Verify pymodbus is importable:
python -c "from pymodbus.client import AsyncModbusTcpClient; print('OK')"

# If adding recharts for dashboard (optional, deferred):
cd frontend && npm install recharts
```

## Configuration Changes for v2

New environment variables needed:

```bash
# Victron Modbus TCP (replaces VICTRON_HOST/VICTRON_PORT MQTT config)
VICTRON_MODBUS_HOST=192.168.0.10   # Venus OS GX device IP
VICTRON_MODBUS_PORT=502            # Modbus TCP port (default 502)

# Victron Modbus Unit IDs (installation-specific)
VICTRON_UNIT_ID_SYSTEM=100         # com.victronenergy.system
VICTRON_UNIT_ID_VEBUS=246          # com.victronenergy.vebus
VICTRON_UNIT_ID_SETTINGS=100       # com.victronenergy.settings
VICTRON_UNIT_ID_GRID=30            # com.victronenergy.grid

# Controller tuning (with safe defaults)
CONTROLLER_CYCLE_S=3               # Control loop interval (seconds)
CONTROLLER_DEAD_BAND_W=50          # Hysteresis dead-band (watts)
CONTROLLER_RAMP_RATE_W_S=500       # Max setpoint ramp rate (watts/second)
```

**Backward compatibility:** Keep `VICTRON_HOST` / `VICTRON_PORT` as aliases during transition. If `VICTRON_MODBUS_HOST` is not set, fall back to `VICTRON_HOST` with the new default port 502 (instead of 1883).

## Sources

- Codebase analysis: `pyproject.toml`, `uv.lock`, `backend/drivers/`, `backend/orchestrator.py`
- pymodbus 3.12.1 -- installed version confirmed from `uv.lock`
- huawei-solar 2.5.0 -- installed version confirmed from `uv.lock`
- FastAPI 0.135.1 -- installed version confirmed from `uv.lock`
- paho-mqtt 2.1.0 -- installed version confirmed from `uv.lock`
- Victron dbus_modbustcp register list -- from Victron's published CCGX Modbus TCP documentation (register addresses need verification against actual Venus OS firmware, flagged MEDIUM confidence)
- Anti-oscillation patterns -- standard control theory (hysteresis, rate limiting, priority dispatch), no external source needed

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| pymodbus as Modbus library | HIGH | Already a dependency, 3.x async API is stable, no viable alternative |
| Keep huawei-solar for Huawei | HIGH | Working driver exists, no reason to rewrite |
| Victron register addresses | MEDIUM | Based on published register list; unit IDs vary by installation and MUST be configurable. Verify against actual Venus OS firmware. |
| Anti-oscillation without external libs | HIGH | Standard patterns, 2 actuators with 2-5s cycle do not need PID/control-theory libraries |
| No new pip dependencies | HIGH | All required functionality exists in current dependency tree |
| recharts for dashboard | LOW | Deferred decision -- may not be needed if custom components suffice |
| paho-mqtt retained for HA/EVCC | HIGH | Only Victron control moves to Modbus; HA discovery and EVCC monitoring still need MQTT |
