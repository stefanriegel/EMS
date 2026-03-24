# Architecture Patterns

**Domain:** Production deployment, VRM integration, cross-charge prevention for dual-battery EMS
**Researched:** 2026-03-24

## Current Architecture Overview

The existing system follows a strict layered architecture:

```
                    Coordinator (5s loop)
                   /                    \
    HuaweiController              VictronController
          |                              |
    HuaweiDriver                   VictronDriver
    (huawei-solar)                  (pymodbus)
```

**Key contracts:**
- Controllers expose `poll() -> ControllerSnapshot` and `execute(ControllerCommand)`
- Coordinator never touches drivers directly
- Optional integrations injected via `set_*()` methods (scheduler, notifier, EVCC, HA MQTT, etc.)
- All integrations follow graceful degradation -- `None` checks, fire-and-forget

## Recommended Architecture for v1.4

### New Components

Four new components are needed, each fitting cleanly into the existing injection pattern.

#### 1. VRM API Client (`backend/vrm_client.py`)

**What:** Async HTTP client to the Victron VRM portal API for reading DESS schedules and system diagnostics.

**Interface:**
```python
class VrmClient:
    """Read-only VRM API client for DESS schedule visibility."""

    def __init__(self, config: VrmConfig) -> None: ...
    async def connect(self) -> None: ...  # authenticate, get installation ID
    async def close(self) -> None: ...
    async def get_dess_schedule(self) -> DessSchedule | None: ...
    async def get_system_diagnostics(self) -> dict | None: ...
```

**Data model:**
```python
@dataclass
class DessScheduleEntry:
    start_unix: int
    duration_s: int
    target_soc_pct: float
    allow_grid_feedin: bool
    restrictions: int      # 0=none, 1=no battery export, 2=no battery import
    strategy: int           # 0=follow SOC, 1=maximize battery

@dataclass
class DessSchedule:
    entries: list[DessScheduleEntry]
    mode: str               # "auto", "off"
    battery_capacity_kwh: float
    system_efficiency_pct: float
    fetched_at: float       # time.monotonic()
```

**Authentication:** Use the `victron-vrm` PyPI package (v0.1.11, async httpx-based, supports access tokens). It handles token management and refresh. Alternatively, direct httpx calls to `vrmapi.victronenergy.com/v2/` endpoints -- the API is simple REST.

**Key endpoints:**
- `POST /v2/auth/login` -- get JWT token (or use Personal Access Token)
- `GET /v2/users/{id}/installations` -- list sites
- `GET /v2/installations/{id}/diagnostics` -- current system state
- DESS schedule data: NOT directly available via VRM REST API as a structured endpoint. The schedule lives on the Venus OS dbus at `/Settings/DynamicEss/Schedule/*/*`. Two options to read it:
  1. **VRM diagnostics endpoint** -- may include DESS-related data in the diagnostics payload (needs field validation)
  2. **Venus OS MQTT** -- subscribe to `N/{portalId}/settings/0/Settings/DynamicEss/Schedule/*` topics via the Venus OS MQTT broker (dbus-flashmq). This is the authoritative source.

**Recommendation:** Use Venus OS MQTT for DESS schedule reading (option 2). The MQTT broker on Venus OS exposes all dbus paths. Since the EMS already has MQTT infrastructure for HA and EVCC, adding a Venus MQTT subscription is the natural path. The VRM REST API is useful for diagnostics and cross-validation but does NOT expose DESS schedules as a first-class endpoint.

**Confidence:** MEDIUM -- DESS schedule dbus paths are documented in the dynamic-ess GitHub repo, but the exact MQTT topic structure needs field validation on real Venus OS.

#### 2. Cross-Charge Detector (`backend/cross_charge.py`)

**What:** Detects and prevents the scenario where one battery charges the other through the AC bus -- Huawei discharging while Victron charges (or vice versa), with no actual household load to justify it.

**Interface:**
```python
@dataclass
class CrossChargeState:
    detected: bool
    source_system: str | None      # "huawei" or "victron"
    sink_system: str | None
    source_power_w: float
    sink_power_w: float
    net_grid_power_w: float
    duration_cycles: int           # consecutive cycles detected

class CrossChargeDetector:
    """Detects battery-to-battery energy transfer via AC bus."""

    def __init__(self, threshold_w: float = 100.0, min_cycles: int = 2) -> None: ...

    def check(
        self,
        h_snap: ControllerSnapshot,
        v_snap: ControllerSnapshot,
        h_cmd: ControllerCommand,
        v_cmd: ControllerCommand,
    ) -> CrossChargeState: ...

    def mitigate(
        self,
        state: CrossChargeState,
        h_cmd: ControllerCommand,
        v_cmd: ControllerCommand,
    ) -> tuple[ControllerCommand, ControllerCommand]: ...
```

**Detection logic:**
1. One battery has negative power (discharging) while the other has positive power (charging)
2. Grid power is near zero or negative (no grid import = load isn't justifying the discharge)
3. The charging power approximately matches the discharging power (energy is transferring between batteries)
4. Threshold: ignore if either power < 100W (noise/standby)
5. Debounce: require 2+ consecutive cycles to avoid transient false positives

**Mitigation strategy:**
- Force the charging battery to HOLDING (zero setpoint)
- Let the discharging battery continue serving load
- Log a DecisionEntry with trigger="cross_charge_prevention"
- Send Telegram alert on first detection per episode

**Where it fits:** Called inside `Coordinator._run_cycle()` after commands are computed but BEFORE they're executed. This is a guard -- it intercepts and modifies commands.

#### 3. Huawei Mode Manager (`backend/huawei_mode.py`)

**What:** Manages the Huawei inverter's storage working mode for remote EMS takeover. The Huawei SUN2000 has an internal EMS that runs when in "Maximum Self-Consumption" mode (register 47086 = 2). For the external EMS to control the battery, it must switch to "TOU" mode (register 47086 = 5) and set appropriate charge/discharge power limits.

**Interface:**
```python
class HuaweiModeManager:
    """Manages Huawei working mode transitions for EMS takeover."""

    def __init__(self, driver: HuaweiDriver) -> None: ...

    async def ensure_ems_control(self) -> bool: ...
    async def release_to_internal(self) -> bool: ...
    async def read_current_mode(self) -> StorageWorkingModesC | None: ...
    async def is_ems_controlling(self) -> bool: ...
```

**Mode transition flow:**
1. On EMS startup: read current mode. If `MAXIMISE_SELF_CONSUMPTION` (2), switch to `TIME_OF_USE` (5) and enable AC charging (register 47087).
2. During operation: EMS controls via `write_max_charge_power()` and `write_max_discharge_power()`.
3. On EMS shutdown: optionally revert to `MAXIMISE_SELF_CONSUMPTION` (configurable -- user may want EMS to stay in control across restarts).
4. On comm failure: the Huawei internal EMS continues to manage the battery in TOU mode -- safe fallback.

**Existing driver support:** The HuaweiDriver already has `write_battery_mode()`, `write_ac_charging()`, `write_max_charge_power()`, and `write_max_discharge_power()`. No new driver methods needed. This module adds the state-machine logic around when and how to call them.

**Confidence:** MEDIUM -- The register addresses are documented in the Huawei Modbus Interface Definitions (V5). The `huawei-solar` library wraps these registers. Field validation needed to confirm TOU mode behavior with external setpoints.

#### 4. DESS Schedule Reader (within VRM/MQTT client)

Rather than a separate component, DESS schedule reading is a method on an extended Venus MQTT subscription. The existing VictronDriver connects to Venus OS via Modbus TCP. DESS schedules are on dbus paths NOT exposed via Modbus TCP registers. Two approaches:

**Option A: Venus OS MQTT (recommended)**
- Subscribe to `N/{portalId}/settings/0/Settings/DynamicEss/Schedule/#` topics
- Parse JSON payloads into `DessScheduleEntry` dataclasses
- Integrate into a `VenusMqttClient` or extend `EvccMqttDriver` pattern

**Option B: VRM REST API polling**
- Poll the VRM diagnostics endpoint every 5-15 minutes
- Extract DESS-related fields from the diagnostics blob
- Lower fidelity -- diagnostics may not include full schedule detail

**Recommendation:** Option A. The Venus OS MQTT broker is on the local network (same host as Modbus TCP), latency is sub-second, and it exposes the authoritative schedule data.

### Modified Components

#### Coordinator (`backend/coordinator.py`)

Modifications needed:

1. **Cross-charge guard in `_run_cycle()`:**
   ```python
   # After computing h_cmd, v_cmd but before executing:
   if self._cross_charge_detector is not None:
       xc_state = self._cross_charge_detector.check(h_snap, v_snap, h_cmd, v_cmd)
       if xc_state.detected:
           h_cmd, v_cmd = self._cross_charge_detector.mitigate(xc_state, h_cmd, v_cmd)
           self._log_cross_charge_decision(xc_state)
   ```

2. **New injection method:**
   ```python
   def set_cross_charge_detector(self, detector: CrossChargeDetector) -> None: ...
   def set_vrm_client(self, client: VrmClient) -> None: ...
   def set_huawei_mode_manager(self, manager: HuaweiModeManager) -> None: ...
   ```

3. **DESS-aware scheduling:** If a DESS schedule is active, the coordinator can factor Victron's planned charge/discharge into its allocation decisions. This avoids fighting with DESS -- if DESS plans to charge Victron at hour X, the coordinator should let it and focus Huawei on other tasks.

4. **CoordinatorState extension:**
   - Add `cross_charge_detected: bool = False`
   - Add `huawei_ems_mode: str = "unknown"` (self-consumption / tou / unknown)
   - Add `dess_schedule_active: bool = False`

#### HuaweiController (`backend/huawei_controller.py`)

Minimal changes:
- On startup, call `HuaweiModeManager.ensure_ems_control()` before entering the control loop
- On shutdown, optionally call `release_to_internal()`
- The controller's `execute()` method already calls `write_max_discharge_power()` and `write_max_charge_power()` -- no change needed for TOU mode control

#### DecisionEntry (`backend/controller_model.py`)

Add new trigger values:
- `"cross_charge_prevention"` -- when cross-charge is detected and mitigated
- `"ems_mode_transition"` -- when Huawei mode is changed
- `"dess_coordination"` -- when DESS schedule influences allocation

#### Config (`backend/config.py`)

New config dataclass:
```python
@dataclass
class VrmConfig:
    token: str
    installation_id: int | None = None  # auto-detect if None
    poll_interval_s: int = 300           # 5 min for REST API

    @classmethod
    def from_env(cls) -> "VrmConfig": ...

@dataclass
class CrossChargeConfig:
    threshold_w: float = 100.0
    min_cycles: int = 2
    enabled: bool = True
```

#### Main lifespan (`backend/main.py`)

Wire new components in the same pattern as existing optional integrations:
```python
# In lifespan, after coordinator creation:
try:
    vrm_cfg = VrmConfig.from_env()
    vrm_client = VrmClient(vrm_cfg)
    await vrm_client.connect()
    coordinator.set_vrm_client(vrm_client)
except KeyError:
    logger.info("VRM_TOKEN not set — VRM integration disabled")

cross_charge = CrossChargeDetector(
    threshold_w=float(os.environ.get("CROSS_CHARGE_THRESHOLD_W", "100")),
)
coordinator.set_cross_charge_detector(cross_charge)

mode_mgr = HuaweiModeManager(huawei_driver)
coordinator.set_huawei_mode_manager(mode_mgr)
```

### Components NOT Modified

- **VictronDriver** -- no changes needed. Modbus TCP reads/writes unchanged.
- **HuaweiDriver** -- no changes needed. Already has all required write methods.
- **Scheduler / WeatherScheduler** -- no changes. DESS coordination is at the coordinator level, not the scheduling level.
- **ExportAdvisor** -- no changes. Export decisions remain independent.
- **Frontend** -- minimal changes. Cross-charge status can be surfaced via existing CoordinatorState WebSocket.
- **InfluxDB writer** -- add cross_charge event metrics, but structure unchanged.

## Data Flow

### Normal Operation (no DESS)

```
                  Coordinator._run_cycle()
                         |
              poll() both controllers
                         |
              compute p_target from grid
                         |
              assign roles + allocate watts
                         |
         cross_charge_detector.check(h_snap, v_snap, h_cmd, v_cmd)
                         |
                 [if detected: mitigate]
                         |
              execute() both controllers
                         |
              build_state + write_integrations
```

### DESS-Aware Operation

```
     VenusMqttClient                    Coordinator._run_cycle()
           |                                     |
     subscribe DESS topics              poll() both controllers
           |                                     |
     update DessSchedule cache          check DESS schedule for current hour
           |                                     |
           +---- DessSchedule --------->  if DESS charging Victron:
                                            Victron role = HOLDING (let DESS drive)
                                            Huawei gets full allocation
                                                 |
                                         cross_charge_detector.check()
                                                 |
                                         execute() both controllers
```

### Huawei Mode Transition

```
     EMS startup
         |
     HuaweiModeManager.ensure_ems_control()
         |
     read_current_mode() → MAXIMISE_SELF_CONSUMPTION
         |
     write_battery_mode(TIME_OF_USE)
     write_ac_charging(True)
         |
     Coordinator loop starts
         |
     HuaweiController.execute() writes max_charge/discharge_power
     (Huawei internal EMS in TOU mode follows these limits)
         |
     EMS shutdown (optional)
         |
     HuaweiModeManager.release_to_internal()
         |
     write_battery_mode(MAXIMISE_SELF_CONSUMPTION)
```

## Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| VrmClient | VRM API auth + diagnostics polling | Coordinator (via set_vrm_client) |
| VenusMqttClient | DESS schedule subscription from Venus OS MQTT | Coordinator (schedule cache) |
| CrossChargeDetector | Detect + mitigate battery-to-battery transfer | Coordinator (inline in _run_cycle) |
| HuaweiModeManager | Working mode state machine (self-consumption <-> TOU) | HuaweiDriver (via existing write methods) |
| Coordinator | Orchestrates all -- cross-charge guard + DESS awareness | All controllers, all integrations |

## Patterns to Follow

### Pattern 1: Optional Integration Injection

**What:** Every new component follows the `set_*()` injection pattern already used by scheduler, EVCC, notifier, HA MQTT, anomaly detector, and self-tuner.

**When:** Always. No new component should be required for the system to start.

**Example:**
```python
# In Coordinator.__init__:
self._cross_charge_detector: CrossChargeDetector | None = None
self._vrm_client = None
self._huawei_mode_manager: HuaweiModeManager | None = None

# Injection:
def set_cross_charge_detector(self, detector: CrossChargeDetector) -> None:
    self._cross_charge_detector = detector

# Usage (always guarded):
if self._cross_charge_detector is not None:
    state = self._cross_charge_detector.check(...)
```

### Pattern 2: Inline Guard (Cross-Charge)

**What:** The cross-charge detector acts as a command interceptor -- it sits between command computation and execution, modifying commands if a dangerous condition is detected.

**When:** Any safety-critical check that must run every cycle.

**Why this pattern:** The coordinator already has this pattern for EVCC hold (line 573) and mode override (line 604). Cross-charge prevention is the same concept -- a guard that short-circuits or modifies the normal dispatch path.

### Pattern 3: Cached External Data (DESS Schedule)

**What:** DESS schedule data arrives asynchronously (via MQTT) and is cached for coordinator consumption. The coordinator reads the cache synchronously during each 5s cycle -- never blocks on an external call.

**When:** Any data source that updates on a different cadence than the 5s control loop.

**Example:** Same pattern as `EvccMqttDriver.evcc_battery_mode` -- updated via MQTT callback, read synchronously by coordinator.

## Anti-Patterns to Avoid

### Anti-Pattern 1: Blocking on VRM API in Control Loop

**What:** Calling the VRM REST API synchronously during the 5s control cycle.
**Why bad:** VRM API latency is 200-500ms. This would add jitter to the control loop and risk timeout cascading.
**Instead:** Poll VRM API on a separate async task (every 5 minutes). Cache results. Coordinator reads the cache.

### Anti-Pattern 2: Fighting DESS

**What:** The EMS writing conflicting setpoints to Victron while DESS is actively controlling it.
**Why bad:** DESS writes Hub4 AC power setpoints to the same VE.Bus registers the EMS writes to. If both write simultaneously, the battery oscillates between conflicting commands.
**Instead:** When DESS is active and has a schedule entry for the current hour, the coordinator should set Victron to HOLDING and let DESS drive. Only override DESS when the user explicitly requests it via HA mode override.

### Anti-Pattern 3: Assuming Huawei TOU Mode Is Instant

**What:** Switching mode and immediately writing setpoints in the same cycle.
**Why bad:** Mode transition on the Huawei inverter may take 1-2 seconds to settle. Writing setpoints before the mode is active can result in the setpoint being ignored.
**Instead:** Write mode, wait one control cycle (5s), then start writing setpoints. The HuaweiModeManager should track transition state.

## Suggested Build Order

Based on dependency analysis:

### Phase 1: Cross-Charge Detection (no external dependencies)
1. `CrossChargeDetector` class with `check()` and `mitigate()` methods
2. `CrossChargeState` dataclass
3. Integration into `Coordinator._run_cycle()` as inline guard
4. Unit tests with mock snapshots and commands
5. DecisionEntry trigger type + Telegram alert

**Rationale:** Purely internal logic. No external APIs, no hardware changes. Can be tested entirely with mocks. Addresses the most critical safety concern.

### Phase 2: Huawei Mode Manager (requires field validation)
1. `HuaweiModeManager` state machine
2. Integration into lifespan (startup/shutdown)
3. Mode health check in coordinator (periodic read of current mode)
4. HA MQTT entity for current Huawei mode visibility
5. Field test: switch mode on real hardware, verify setpoint acceptance

**Rationale:** Uses existing HuaweiDriver methods. The code is straightforward, but needs real hardware to validate that TOU mode accepts external setpoints as expected.

### Phase 3: VRM/DESS Integration (requires VRM account + Venus OS access)
1. `VrmClient` with auth and diagnostics
2. Venus MQTT subscription for DESS schedule paths
3. `DessSchedule` data model
4. DESS-aware coordinator logic (Victron HOLDING during DESS windows)
5. API endpoint to expose DESS schedule status
6. Dashboard indicator for DESS activity

**Rationale:** Requires VRM credentials and a Venus OS MQTT broker. The coordinator changes depend on understanding DESS schedule structure, which needs field validation. Build after Phase 2 so the Huawei side is stable.

### Phase 4: Production Integration
1. Combined testing with both batteries under EMS control
2. Monitoring and alerting setup
3. Gradual rollout: shadow mode (log decisions, don't execute) before live control
4. Fallback procedures documented

**Rationale:** Only after individual features are validated can they be combined for production use.

## Scalability Considerations

| Concern | Current (dev) | Production | Notes |
|---------|--------------|------------|-------|
| Control loop latency | <50ms | <100ms target | Cross-charge check adds ~0.1ms (pure math) |
| MQTT connections | HA + EVCC | HA + EVCC + Venus | Third MQTT connection; same pattern |
| VRM API rate limits | N/A | 1 req/5min | VRM has no documented rate limit but conservative polling is wise |
| Mode transitions | N/A | 1-2s settling | One extra cycle delay on startup |

## Operating Mode Decision Matrix

The milestone mentions investigating "full EMS control vs Victron DESS + EMS-controlled Huawei." This is the critical architectural decision:

| Mode | EMS Controls | DESS Controls | Cross-Charge Risk | Complexity |
|------|-------------|---------------|-------------------|------------|
| **Full EMS** | Huawei + Victron | Nothing | High (must prevent) | High |
| **Hybrid (recommended)** | Huawei only | Victron via DESS | Low (DESS handles Victron) | Medium |
| **Minimal EMS** | Neither | Victron via DESS, Huawei internal | None | Low (but no optimization) |

**Recommendation:** Start with **Hybrid mode**. Let DESS manage Victron (it has sophisticated hourly pricing optimization), while the EMS takes full control of Huawei via TOU mode. The EMS reads the DESS schedule to understand what Victron is doing and avoids conflicting commands. Cross-charge detection still runs as a safety net.

This approach:
- Reduces cross-charge risk (only EMS writes to Huawei, only DESS writes to Victron)
- Leverages DESS's existing pricing optimization for Victron
- Gives the EMS full authority over Huawei (30 kWh, faster to control)
- Preserves the option to move to Full EMS later by simply disabling DESS

## Sources

- [Victron VRM API Python Client](https://github.com/victronenergy/vrm-api-python-client) -- official reference
- [victron-vrm PyPI package](https://pypi.org/project/victron-vrm/) -- v0.1.11, async httpx-based
- [VRM API Documentation](https://vrm-api-docs.victronenergy.com/) -- REST endpoints
- [Dynamic ESS GitHub](https://github.com/victronenergy/dynamic-ess) -- DESS dbus paths and schedule structure
- [Victron dbus-mqtt](https://github.com/victronenergy/dbus-mqtt) -- Venus OS MQTT bridge for dbus paths
- [Huawei SUN2000 Modbus Interface Definitions](https://support.huawei.com/enterprise/de/doc/EDOC1100387581) -- register 47086 (working mode), 47087 (AC charging)
- [Victron Modbus TCP Register List](https://github.com/victronenergy/dbus_modbustcp/blob/master/CCGX-Modbus-TCP-register-list.xlsx) -- standard register addresses
- [Venus OS v3.70 Release](https://www.victronenergy.com/blog/2026/02/25/introducing-venus-os-3-70/) -- latest DESS fixes
- Existing codebase: `coordinator.py`, `huawei_driver.py`, `victron_driver.py`, `controller_model.py`
