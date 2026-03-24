# Phase 1: Victron Modbus TCP Driver - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Build and verify the Victron Modbus TCP driver against real hardware, adapt Huawei driver to a unified interface. Both battery systems must be readable and writable through a uniform driver interface over Modbus TCP.

Requirements: DRV-01, DRV-02, DRV-03, DRV-04, DRV-05, DRV-06

</domain>

<decisions>
## Implementation Decisions

### Driver interface formalization
- **D-01:** Create a Python `Protocol` class (`BatteryDriver`) for structural typing — no ABC, no inheritance
- **D-02:** Protocol defines: `async connect()`, `async close()`, `async read_state() -> BatteryState`, `async write_setpoint(watts: float)` — plus system-specific extensions
- **D-03:** Both `HuaweiDriver` and `VictronDriver` satisfy the protocol implicitly (structural subtyping) — no changes to Huawei class signature needed
- **D-04:** Protocol lives in `backend/drivers/protocol.py` — imported by orchestrator for type hints

### Victron Modbus unit IDs
- **D-05:** Two separate configurable unit IDs: `vebus_unit_id` (default 227) for inverter registers, `system_unit_id` (default 100) for system-level registers (SoC, battery power)
- **D-06:** Env vars: `VICTRON_VEBUS_UNIT_ID` and `VICTRON_SYSTEM_UNIT_ID` — configurable because Venus OS assigns unit IDs dynamically based on connected devices
- **D-07:** Default port changes from 1883 (MQTT) to 502 (Modbus TCP)

### Migration strategy
- **D-08:** Replace MQTT implementation in-place — same `VictronDriver` class name, same public method signatures, new Modbus TCP backend
- **D-09:** Remove paho-mqtt dependency from VictronDriver entirely (paho-mqtt may remain for other uses like EVCC)
- **D-10:** `VictronSystemData` and `VictronPhaseData` dataclasses stay unchanged — only the driver internals change

### Register access pattern
- **D-11:** Use pymodbus 3.11+ `AsyncModbusTcpClient` for all Modbus TCP communication
- **D-12:** Batch consecutive registers in single `read_holding_registers()` calls to minimize round-trips
- **D-13:** System registers (unit 100): SoC, battery power, battery voltage, battery current
- **D-14:** VE.Bus registers (unit 227+): per-phase AC power (L1/L2/L3), grid power, ESS mode, AC power setpoint writes
- **D-15:** Register addresses follow Venus OS Modbus TCP register list (documented by Victron for firmware v3.20+)

### Error recovery
- **D-16:** Adopt Huawei's proven `_with_reconnect()` pattern — on `ConnectionException`, attempt one reconnect, then raise
- **D-17:** Connection health check: attempt a single register read on `connect()` to verify the link is live
- **D-18:** Stale data detection: timestamp each successful read, orchestrator treats data older than `2 * loop_interval_s` as stale

### Sign convention enforcement
- **D-19:** Victron Modbus registers use their native convention internally; conversion to canonical (positive = charge, negative = discharge) happens only in the driver's read methods
- **D-20:** Write methods accept canonical convention and convert to Victron-native before writing registers

### Claude's Discretion
- Exact register grouping boundaries for batched reads
- Internal helper method organization within the driver
- Specific pymodbus client configuration (timeout, retries)
- Test fixture structure and mock patterns for pymodbus

</decisions>

<specifics>
## Specific Ideas

- Huawei driver is the gold-standard reference — match its patterns (async context manager, `_with_reconnect()`, dataclass returns)
- Venus OS Modbus TCP register list is the authoritative source for register addresses — verify against actual firmware v3.20+ on the GX device
- The orchestrator already handles Victron connection failure gracefully (WARNING log, continues Huawei-only) — no changes to lifespan wiring needed
- Existing `VictronSystemData` already captures per-phase power, grid power, SoC — the Modbus driver just populates it differently

</specifics>

<canonical_refs>
## Canonical References

### Driver contracts
- `backend/drivers/huawei_driver.py` — Reference Modbus TCP driver implementation (457 lines), async context manager, `_with_reconnect()` pattern
- `backend/drivers/huawei_models.py` — Battery dataclass with sign convention properties (positive = charge)
- `backend/drivers/victron_driver.py` — Current MQTT driver to replace (498 lines), method signatures to preserve
- `backend/drivers/victron_models.py` — `VictronSystemData`, `VictronPhaseData` dataclasses to keep unchanged

### Configuration
- `backend/config.py` — `VictronConfig` dataclass to extend with Modbus-specific fields (`vebus_unit_id`, `system_unit_id`, port 502)

### Integration points
- `backend/orchestrator.py` — `_apply_setpoints()` calls `write_ac_power_setpoint(phase, watts)` — signature must be preserved
- `backend/main.py` — `lifespan()` wires VictronDriver with graceful degradation on connect failure
- `backend/unified_model.py` — `UnifiedPoolState.from_readings()` consumes `VictronSystemData`

### Tests
- `tests/drivers/test_victron_driver.py` — Existing MQTT tests to rewrite for Modbus; `_make_system_data()` factory to retain
- `tests/drivers/test_huawei_driver.py` — Reference test patterns and fixture style

### External documentation
- Venus OS Modbus TCP register list (Victron published docs) — authoritative register addresses for firmware v3.20+

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `HuaweiDriver._with_reconnect()` — reconnect-on-failure wrapper, directly applicable to Victron Modbus
- `VictronSystemData` / `VictronPhaseData` — dataclasses already model the right shape, just need Modbus population
- `_require_env()` helper in config.py — use for new `VICTRON_VEBUS_UNIT_ID` etc.
- `_make_system_data()` test factory — reuse for Modbus driver tests

### Established Patterns
- Async context manager (`async with driver:`) — both drivers must support this
- Sentinel values for offline state — orchestrator already creates zeroed `VictronSystemData` on failure
- Dataclass-based state snapshots — never return None, always return a populated dataclass
- Sign convention properties (`charge_power_w`, `discharge_power_w`) derived from single signed field

### Integration Points
- `Orchestrator._apply_setpoints()` calls `victron.write_ac_power_setpoint(phase, watts)` — method signature is the contract
- `lifespan()` in main.py instantiates `VictronDriver(host, port, ...)` — constructor signature changes (add unit IDs, remove discovery_timeout)
- `VictronConfig.from_env()` — add new env vars, change default port

</code_context>

<deferred>
## Deferred Ideas

- Formal ABC/Protocol enforcement via `isinstance` or `runtime_checkable` — not needed for 2 drivers, revisit if third battery added (ECO-01)
- Auto-discovery of Victron unit IDs via Modbus scan — manual config is sufficient for v1
- Venus OS firmware version detection via Modbus — too fragile, document supported versions instead

</deferred>

---

*Phase: 01-victron-modbus-driver*
*Context gathered: 2026-03-22*
