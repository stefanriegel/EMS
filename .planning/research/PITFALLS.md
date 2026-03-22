# Domain Pitfalls

**Domain:** Dual-battery EMS with independent Modbus TCP dispatch (Huawei LUNA2000 + Victron MultiPlus-II)
**Researched:** 2026-03-22

## Critical Pitfalls

Mistakes that cause hardware damage, energy loss, or require architectural rewrites.

### Pitfall 1: Victron ESS Mode State Machine Conflicts

**What goes wrong:** The Victron MultiPlus-II ESS controller has its own internal control logic that fights external setpoints. When Hub4Mode is set to 3 (external control), the Venus OS still enforces BatteryLife limits, minimum SoC cutoffs, and absorption/float voltage thresholds. External AcPowerSetpoint writes are silently clamped or ignored when the internal state machine disagrees --- the inverter simply does not follow the setpoint, with no error returned via MQTT or Modbus.

**Why it happens:** Venus OS treats Hub4Mode=3 as "external control suggestions" not "absolute commands." The VE.Bus firmware has hard safety limits that cannot be overridden via software. The current v1 codebase already guards against ESS mode not being 2 or 3 (orchestrator.py:756), but does not detect when a valid ESS mode still clamps the setpoint due to BatteryLife or absorption state.

**Consequences:** The EMS writes a discharge setpoint of 3000W, but the Victron only delivers 500W because BatteryLife has engaged its minimum SoC protection. The EMS thinks it dispatched 3000W total, leading to grid import the system intended to cover. The Huawei system is under-dispatched because the coordinator assumed Victron would cover its share. Over time, SoC imbalance grows.

**Prevention:**
1. Always read back the actual battery power after writing a setpoint (compare `battery_power_w` to the written setpoint on the next cycle). If delta exceeds 500W for 3+ cycles, flag the Victron as "clamped" and reassign its share to Huawei.
2. Read `MinimumSocLimit` from Venus OS settings and enforce it in the EMS coordinator, never relying on the inverter to silently enforce it.
3. Monitor VE.Bus state transitions (State register): values 3=Bulk, 4=Absorption, 5=Float indicate the charger is active and will override discharge setpoints.
4. After switching from GRID_CHARGE back to normal dispatch, wait at least 2 control cycles before trusting Victron setpoint adherence --- the VE.Bus state machine takes 5-10 seconds to transition.

**Detection:** Log `abs(written_setpoint - actual_power) / written_setpoint` as a metric. Alert when this ratio exceeds 50% for 30+ seconds.

**Phase:** Must be addressed in the Victron Modbus TCP driver phase. The driver must expose setpoint adherence as a first-class concept.

---

### Pitfall 2: Cross-System Oscillation from Shared Grid Meter

**What goes wrong:** Both battery systems observe the same grid meter reading. If both react simultaneously to a grid import of 2000W, each tries to discharge 2000W, producing 4000W total --- overshooting into grid export. Next cycle, both see grid export and reduce output, causing grid import again. The system oscillates at the control loop frequency (every 5 seconds) with increasing amplitude.

**Why it happens:** The v1 codebase uses a single `P_target` derived from `victron.grid_power_w` (orchestrator.py:608) and splits it proportionally. This works because one orchestrator computes one total. In v2 with independent controllers, if both controllers read the same grid meter and independently compute their response, they will double-count the demand.

**Consequences:** Continuous power oscillation (potentially hundreds of watts swinging every 5 seconds), accelerated battery cycling, grid instability notifications from the utility, and inverter thermal stress from rapid power reversals.

**Prevention:**
1. The coordinator must be the single entity that reads the grid meter and assigns per-system budgets. Individual controllers must NEVER independently react to grid meter readings.
2. Implement a "budget allocation" pattern: coordinator reads grid power, subtracts each system's current actual output, computes the residual, and allocates incremental adjustments (not absolute targets).
3. Use staggered setpoint writes: write Huawei first, wait 1-2 seconds for the grid meter to reflect the change, then compute and write Victron. This breaks the simultaneous-reaction loop.
4. Apply exponential moving average (EMA) smoothing on grid meter readings before computing setpoints. A 3-cycle EMA (alpha=0.4) dampens transient spikes without introducing excessive lag.

**Detection:** Monitor grid power standard deviation over 60-second windows. Healthy operation: stdev < 200W. Oscillation: stdev > 500W with regular periodicity.

**Phase:** Core coordinator design phase. This is the most architecturally critical decision --- get it wrong and the entire independent-control model fails.

---

### Pitfall 3: Huawei Modbus TCP Single-Connection Locking

**What goes wrong:** The Huawei SUN2000 inverter accepts only one Modbus TCP connection at a time on port 502. If the EMS holds the connection and another client (Home Assistant integration, SolarEdge monitor, or a second EMS instance) attempts to connect, one of two things happens: (a) the existing connection is dropped, causing the EMS to lose control, or (b) the new connection is rejected, breaking the other tool.

**Why it happens:** Huawei's Modbus TCP implementation is a simple serial-to-TCP bridge with a single-client lock. The `huawei-solar` library uses `AsyncHuaweiSolar` which holds a persistent connection. The v1 reconnect logic (`_with_reconnect` in huawei_driver.py:169) retries once on `ConnectionException`, but if another client has seized the port, reconnection will also fail.

**Consequences:** Intermittent connection drops every few minutes, failed setpoint writes leaving the battery in its last commanded state (which may be wrong for current conditions), and complete loss of Huawei control if another integration takes priority.

**Prevention:**
1. Ensure the HA Huawei Solar integration is DISABLED or configured as read-only (polling only, no write) when the EMS is running. Document this as a hard requirement.
2. Add exponential backoff to reconnection (not just one retry): 1s, 2s, 4s, 8s, max 30s. The current single-retry in `_with_reconnect` is insufficient for contested connections.
3. Implement a connection health heartbeat: if 3 consecutive read attempts fail, log ERROR and enter safe mode for the Huawei system (set discharge limit to 0 before dropping the connection).
4. Consider using the Modbus TCP proxy pattern: run a local Modbus proxy (e.g., `mbpoll` or custom asyncio proxy) that multiplexes the single upstream connection to multiple downstream clients.

**Detection:** Track connection drop frequency. More than 2 drops per hour indicates connection contention.

**Phase:** Huawei driver rewrite phase. Connection management must be robust before building independent control on top.

---

### Pitfall 4: Stale Setpoints on Communication Loss (Fail-Unsafe)

**What goes wrong:** When the EMS loses communication with an inverter, the last-written setpoint remains active on the inverter. If the last setpoint was "discharge 5000W" and communication is lost, the inverter continues discharging at 5000W indefinitely until its own BMS low-voltage cutoff triggers. This can deep-discharge the battery below safe levels.

**Why it happens:** Neither Huawei nor Victron Modbus TCP have a "watchdog timeout" that automatically reverts setpoints when the controlling client disconnects. Huawei's `StorageWorkingModesC` setting persists across connections. Victron's Hub4 AcPowerSetpoint persists until explicitly overwritten or the system reboots.

**Consequences:** Deep discharge below BMS protection thresholds (Huawei cells can be damaged below 2.5V/cell), unexpected grid export during high-tariff periods, and no visibility into the problem until the battery hits hard cutoff.

**Prevention:**
1. Implement a "dead man's switch" pattern: before starting the control loop, record the current time. If the control loop misses 3 consecutive cycles (15 seconds at 5s interval), a separate watchdog task writes safe setpoints (0W discharge) to both systems.
2. On the Victron side, use the `DisableCharge` and `DisableFeedIn` registers as safety latches. Set `DisableFeedIn=1` whenever the EMS is in HOLD state, and only clear it when actively dispatching.
3. On graceful shutdown (`Orchestrator.stop()`), the current code already writes safe setpoints (orchestrator.py:220). Extend this to also reset Huawei's working mode to the BMS-default value, not just zero the discharge limit.
4. Add a Telegram/HA notification when communication loss exceeds 60 seconds. The current `max_offline_s` timeout (config.py:173) triggers HOLD state but does not actively write zero setpoints to the hardware --- it just stops computing new ones.

**Detection:** The `_huawei_last_seen` and `_victron_last_seen` timestamps already exist. Add a metric: `time_since_last_successful_write` per system.

**Phase:** Safety layer phase --- must be implemented before any independent control goes live. This is a safety-critical feature, not a nice-to-have.

---

### Pitfall 5: Victron MQTT-to-Modbus Migration --- Protocol Semantic Differences

**What goes wrong:** The v2 design switches Victron control from MQTT to Modbus TCP. The MQTT interface (`W/{portalId}/vebus/{instanceId}/Hub4/L{N}/AcPowerSetpoint`) and the Modbus TCP register interface (register 37 on the com.victronenergy.vebus service) have different semantics: MQTT values are JSON-wrapped floats with no type validation, while Modbus registers are 16-bit signed integers with implicit scaling factors. A setpoint of 3456.7W via MQTT works fine; the same value via Modbus TCP must be written as a scaled int16 (scale factor 1, so 3457) and negative values use two's complement.

**Why it happens:** The Victron Modbus TCP register list (published by Victron) uses different unit IDs, register addresses, and scaling factors than the MQTT topic paths. The mapping is not 1:1. Specifically, `AcPowerSetpoint` via Modbus TCP is register 37 on unit ID 246 (VE.Bus system), while via MQTT it is a per-phase topic. Modbus TCP offers a single combined setpoint register, not per-phase control (unless using individual VE.Bus unit registers which are undocumented for external control).

**Consequences:** Loss of per-phase balancing capability (the v1 MQTT approach writes per-phase setpoints that zero out per-phase grid import). If Modbus TCP only exposes a single combined setpoint, phase imbalance on a 3-phase system leads to neutral current and potentially breaker trips on heavily loaded phases.

**Prevention:**
1. Before committing to the Modbus TCP migration, verify on the actual Venus OS installation whether per-phase AcPowerSetpoint registers exist in the Modbus TCP mapping. Check `/opt/victronenergy/dbus-modbus-client/` on the Venus OS device for the register map.
2. If per-phase Modbus TCP control is not available, keep MQTT for Victron setpoint writes and use Modbus TCP only for reads. This hybrid approach preserves the per-phase balancing that v1 already implements successfully.
3. If going full Modbus TCP, implement a phase-balancing algorithm at the inverter's AC output that distributes the single combined setpoint based on measured per-phase grid power. This is less precise than direct per-phase control.
4. Test with Venus OS >= 3.21 which added negative AcPowerSetpoint support. Older firmware silently clamps negative values to zero.

**Detection:** After writing a combined setpoint via Modbus TCP, read back per-phase power. If phase imbalance exceeds 1000W, the single-setpoint approach is insufficient.

**Phase:** Victron driver phase --- this is a go/no-go decision that must be resolved BEFORE writing the new driver. Wrong choice here means rewriting the driver again.

## Moderate Pitfalls

### Pitfall 6: Hysteresis Dead-Band Mismatch Between Systems

**What goes wrong:** A single 200W hysteresis threshold (config.py:158) is applied uniformly. Huawei Modbus TCP has ~500ms round-trip latency; Victron MQTT has <100ms. A 200W dead-band prevents Huawei oscillation but allows Victron micro-oscillation. Conversely, a dead-band sized for Victron (e.g., 50W) causes Huawei to thrash.

**Why it happens:** The v1 codebase already identified this in CONCERNS.md as a known issue ("Hysteresis Dead-Band Not Configurable Per System"). The per-phase 20W dead-band for Victron (orchestrator.py:766) partially addresses this, but the combined hysteresis check (orchestrator.py:791) still gates both systems together.

**Prevention:**
1. In v2, each independent controller must have its own hysteresis configuration: `huawei_hysteresis_w` (200-300W) and `victron_hysteresis_w` (50-100W).
2. Add ramp-rate limiting per system: Huawei changes should be limited to 500W/cycle max, Victron to 1000W/cycle. This prevents large step changes that overshoot.
3. Use derivative dampening: if the setpoint change direction reversed in the last 2 cycles, double the dead-band temporarily (anti-hunting logic).

**Phase:** Independent controller design phase.

---

### Pitfall 7: Coordinator-Controller Race Condition in Async Architecture

**What goes wrong:** With independent controllers running as separate async tasks, the coordinator reads grid meter data, computes budgets, and sends them to controllers. But between the coordinator reading the grid meter and the controller applying the setpoint, the grid meter value has changed. The controller applies a stale budget, producing incorrect output.

**Why it happens:** In an async architecture with a 5-second control loop, there is inherent latency between observation and action. With two independent controllers potentially writing setpoints at different times within the same cycle, the effective control delay doubles.

**Prevention:**
1. Use a single-writer pattern: the coordinator computes budgets AND writes setpoints in a single atomic cycle. Controllers are responsible for hardware abstraction (read/write methods) but not for deciding what to write.
2. If controllers must be independent tasks, use a versioned state object: the coordinator publishes a budget with a monotonic sequence number. The controller checks that its budget is not stale (sequence number matches the latest grid meter reading) before applying it.
3. Use `asyncio.gather` with a timeout to poll both systems in parallel, then compute and apply setpoints sequentially within the same event loop iteration. This is what v1 already does (orchestrator.py:485-533) and it works. Do not break this into separate tasks without solving the consistency problem.

**Phase:** Core architecture phase --- the coordinator/controller boundary design.

---

### Pitfall 8: Grid Charge Slot Handoff Between Systems

**What goes wrong:** During a scheduled cheap-tariff grid charge window, both batteries charge simultaneously. When the window ends, both systems try to resume normal dispatch at the same instant. The combined discharge spike (both systems going from +5000W charge to -3000W discharge) creates a transient power reversal of 16000W+ that can trip the grid relay or cause the Victron to enter fault state.

**Why it happens:** The v1 `_cleanup_grid_charge` method (orchestrator.py:869) zeros setpoints on slot exit. But zeroing both systems simultaneously means the house load, which was being served by the grid during the charge window, must instantly switch to battery supply. If the house load is 3000W and both systems were charging at 5000W each, the net swing is 13000W.

**Prevention:**
1. Implement a soft-exit ramp: on charge slot end, reduce charge power by 25% per cycle over 4 cycles (20 seconds), then hold at 0 for 2 cycles, then resume normal dispatch. This limits the ramp rate to ~2500W/cycle.
2. Stagger the exit: stop Huawei charging first (it has slower Modbus response), wait 2 cycles, then stop Victron. This distributes the transient over 10 seconds.
3. During the ramp-down, temporarily increase the hysteresis dead-band to 500W to prevent oscillation during the transition.

**Phase:** Scheduler/coordinator integration phase.

---

### Pitfall 9: HA Add-on Memory Constraints with Dual Control Loops

**What goes wrong:** The HA Add-on runs on devices ranging from Raspberry Pi 4 (4GB RAM) to Intel NUCs (16GB+). With v2 adding a second independent control loop, ML forecaster, two Modbus TCP connections, MQTT client, WebSocket server, and InfluxDB writer, memory usage can exceed available resources on constrained devices.

**Why it happens:** The current Dockerfile (ha-addon/Dockerfile) installs scipy/sklearn (~100MB resident) plus two async connection pools. On a Pi 4 running HAOS, the EMS Add-on competes with HA Core, Mosquitto, Z-Wave, and other add-ons for ~2.5GB of usable RAM.

**Prevention:**
1. Profile memory usage with both control loops active on a Pi 4. Target: <256MB RSS for the EMS process.
2. Make sklearn truly optional at the container level (don't install it unless `ha_ml_min_days > 0`). This saves ~100MB on constrained devices.
3. Use connection pooling with limits: maximum 1 Modbus TCP connection per system, maximum 1 MQTT connection. No connection retry storms that spawn multiple simultaneous connections.
4. Add a `/api/health` memory metric that reports RSS. Alert when RSS exceeds 200MB.
5. Consider using `uvloop` instead of the default asyncio event loop for lower per-task memory overhead.

**Phase:** HA Add-on packaging phase --- performance testing must happen on target hardware.

---

### Pitfall 10: Inconsistent Sign Conventions Across Systems

**What goes wrong:** Huawei and Victron use opposite sign conventions for power. Huawei: positive = discharge power limit (how much the battery CAN discharge). Victron: negative AcPowerSetpoint = export/discharge, positive = import/charge. The v1 codebase documents this (orchestrator.py docstring lines 12-15) but the convention is implicit, scattered across the codebase, and easy to get backwards when writing new code.

**Why it happens:** Each manufacturer defines their own Modbus register semantics. There is no industry standard for battery power sign convention. The current code handles this correctly through convention and comments, but during a rewrite, a sign error in one controller will cause a system to charge when it should discharge (or vice versa).

**Prevention:**
1. Define an explicit `PowerDirection` enum or type alias in the shared model layer: `DISCHARGE = -1, CHARGE = +1, IDLE = 0`. All internal EMS logic uses this convention. Conversion to/from hardware-specific signs happens ONLY in the driver layer.
2. Add unit tests that verify: "when the coordinator says DISCHARGE 3000W, the Huawei driver writes +3000 to the discharge limit register, and the Victron driver writes -1000 per phase to AcPowerSetpoint."
3. Document the sign convention in a single canonical location (not scattered in docstrings). Include a truth table:

| EMS Intent | Huawei Register | Victron AcPowerSetpoint |
|------------|----------------|------------------------|
| Discharge 3000W | write_max_discharge_power(3000) | write_ac_power_setpoint(N, -1000) |
| Charge 3000W | write_max_charge_power(3000) + ac_charging(True) | write_ac_power_setpoint(N, +1000) |
| Hold | write_max_discharge_power(0) | write_ac_power_setpoint(N, 0) |

**Phase:** Shared model/driver interface phase --- must be locked down before implementing controllers.

## Minor Pitfalls

### Pitfall 11: Modbus TCP Connection Timeout vs. Register Read Timeout

**What goes wrong:** The Huawei driver uses a single `timeout_s=10.0` (config.py:46) for both TCP connection establishment and individual register reads. On a congested network, TCP connection takes 2 seconds, leaving only 8 seconds for the register read. If the read takes 9 seconds, it times out despite being a valid (slow) response.

**Prevention:** Separate connection timeout (3s) from read timeout (10s). The `AsyncHuaweiSolar` library supports both. Use `connect_timeout` for the TCP handshake and `request_timeout` for Modbus transactions.

**Phase:** Driver rewrite phase.

---

### Pitfall 12: Victron Keepalive Drift Under Load

**What goes wrong:** The Victron MQTT keepalive is sent every 30 seconds (victron_driver.py:350). If the event loop is blocked by a long Huawei Modbus read (up to 10 seconds), the keepalive may be delayed by 10+ seconds. If two consecutive keepalives are delayed, Venus OS stops publishing telemetry updates, causing stale data detection and the Victron system being marked offline.

**Prevention:** Run the keepalive as a dedicated `asyncio.Task` that is not blocked by the control loop. Use `asyncio.create_task` (already done) but ensure the control loop does not hold the event loop with synchronous blocking calls. The current Huawei driver uses `await` properly, but verify that `AsyncHuaweiSolar.get_multiple()` does not internally block.

**Phase:** Driver refactoring phase.

---

### Pitfall 13: DST Transition in Charge Windows

**What goes wrong:** Already documented in CONCERNS.md (scheduler.py:120-150). Charge windows are defined in minutes-from-midnight without DST adjustment. On spring-forward, the charge window effectively shifts 1 hour later, potentially missing the cheap tariff period entirely.

**Prevention:** Store charge windows as local time ranges with explicit timezone (e.g., "00:30-05:00 Europe/Berlin"). Convert to UTC at runtime using `zoneinfo`. Test with `time_machine` or `freezegun` across DST boundaries.

**Phase:** Scheduler phase.

---

### Pitfall 14: InfluxDB Write Backpressure During Dual-System Metrics

**What goes wrong:** With two independent control loops writing metrics, the InfluxDB write rate doubles (from 1 write/5s to 2 writes/5s per system, plus coordinator metrics). The current fire-and-forget writer (influx_writer.py) spawns a thread per write via the influxdb-client async wrapper. Under sustained load, thread count grows.

**Prevention:** Batch metrics from both systems into a single InfluxDB write per control cycle. Use the InfluxDB line protocol batch API (write multiple points in one HTTP request). Limit the thread pool to 4 workers maximum.

**Phase:** Metrics/observability phase.

---

### Pitfall 15: Assertions as Precondition Checks in Driver Code

**What goes wrong:** Already documented in CONCERNS.md. The drivers use `assert self._client is not None` which is compiled out with `python -O`. In production, if the assertion is optimized away, a `NoneType has no attribute 'get_multiple'` error occurs instead of the clear "Driver not connected" message.

**Prevention:** Replace all driver `assert` statements with `if ... raise RuntimeError(...)` before the rewrite. This is a prerequisite for reliable independent controllers --- each controller must get clear error messages when its driver is in a bad state.

**Phase:** Pre-rewrite cleanup --- should be addressed in current codebase before the v2 work begins.

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Victron Modbus TCP driver | Per-phase control may not be available via Modbus TCP (#5) | Verify register map on actual hardware BEFORE writing the driver. Keep MQTT as fallback. |
| Independent controller design | Cross-system oscillation from shared grid meter (#2) | Single-writer coordinator pattern. Never let controllers independently react to grid readings. |
| Coordinator architecture | Race conditions between budget computation and setpoint application (#7) | Atomic read-compute-write cycle. No separate async tasks for budget and setpoint. |
| Safety layer | Stale setpoints on communication loss (#4) | Dead man's switch watchdog task. Write safe setpoints actively, don't just stop computing. |
| Grid charge scheduling | Power reversal transient on slot exit (#8) | Soft-exit ramp over 4 cycles. Stagger system transitions. |
| HA Add-on packaging | Memory constraints on Pi 4 (#9) | Profile on target hardware. Make sklearn optional at container level. |
| Shared model layer | Sign convention confusion (#10) | Canonical PowerDirection type. Conversion only in drivers. Truth table in docs. |
| Anti-oscillation tuning | Single hysteresis threshold insufficient (#6) | Per-system hysteresis + ramp-rate limiting + derivative dampening. |
| ESS mode management | Victron internal state machine overrides external setpoints (#1) | Read back actual power. Monitor VE.Bus state. Detect setpoint clamping. |

## Confidence Assessment

| Pitfall | Confidence | Basis |
|---------|-----------|-------|
| #1 ESS Mode Conflicts | HIGH | Observed in v1 codebase (orchestrator.py:756 guard), documented Victron behavior |
| #2 Cross-System Oscillation | HIGH | Fundamental control theory; directly caused by the architectural change from unified to independent |
| #3 Huawei Single Connection | HIGH | Documented Huawei limitation, observed in field (reconnect logic exists for this reason) |
| #4 Stale Setpoints | HIGH | Observed in v1 codebase (safe setpoint logic in stop()), applies to all Modbus TCP control |
| #5 MQTT-to-Modbus Semantics | MEDIUM | Based on Victron published register maps; per-phase availability needs hardware verification |
| #6 Hysteresis Mismatch | HIGH | Already identified in CONCERNS.md, directly measured timing differences |
| #7 Coordinator Race Condition | MEDIUM | Standard async architecture concern; severity depends on implementation choice |
| #8 Grid Charge Handoff | MEDIUM | Extrapolated from v1 cleanup logic; power reversal magnitude is calculated |
| #9 HA Add-on Memory | MEDIUM | Based on Dockerfile analysis and typical Pi 4 constraints; needs profiling to confirm |
| #10 Sign Conventions | HIGH | Directly observed in codebase --- two different conventions already in use |

## Sources

- Codebase analysis: `backend/orchestrator.py`, `backend/drivers/victron_driver.py`, `backend/drivers/huawei_driver.py`, `backend/unified_model.py`, `backend/config.py`
- Known issues: `.planning/codebase/CONCERNS.md`
- Project requirements: `.planning/PROJECT.md`
- HA Add-on config: `ha-addon/config.yaml`, `ha-addon/Dockerfile`
- Victron Venus OS MQTT/Modbus documentation (training data, MEDIUM confidence for register-level details)
- Huawei SUN2000 Modbus TCP behavior (training data + codebase evidence from `huawei-solar` library usage)
