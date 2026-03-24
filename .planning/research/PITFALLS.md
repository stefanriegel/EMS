# Pitfalls Research

**Domain:** Dual-battery EMS production deployment, cross-charge prevention, VRM/DESS integration
**Researched:** 2026-03-24
**Confidence:** HIGH (based on codebase analysis, hardware documentation, community reports)

## Critical Pitfalls

### Pitfall 1: Huawei Modbus Single-Connection Exclusion

**What goes wrong:**
The Huawei SUN2000 Smart Dongle only supports ONE concurrent Modbus TCP connection. If the EMS opens a persistent connection, the existing `wlcrs/huawei_solar` Home Assistant integration loses its connection and goes unavailable. Conversely, if HA is connected, the EMS gets timeouts and broken pipe errors. This is not a software bug -- the dongle firmware rejects the second TCP client.

**Why it happens:**
The Modbus TCP proxy on the Smart Dongle is single-server only. Most developers assume TCP allows multiple clients (it does at the socket level), but the dongle's firmware handles one transaction stream at a time and drops competing connections.

**How to avoid:**
1. Use a Modbus Proxy (e.g., the HACS Modbus Proxy add-on) to multiplex. Both HA and EMS connect to the proxy; it serializes access to the dongle.
2. Increase timeouts to 10+ seconds when going through a proxy -- latency doubles.
3. Alternatively, disable the HA integration entirely and let the EMS be the sole Modbus client, publishing entities via MQTT discovery instead (the EMS already does this with 17 entities).
4. Never hold a persistent connection open. Connect, read/write, disconnect -- or at minimum use short poll intervals with connection pooling.

**Warning signs:**
- `ConnectionException` or `BrokenPipeError` appearing sporadically in logs
- Huawei entities in HA going `unavailable` when EMS starts
- Zero values returned from Modbus reads (proxy returning stale/corrupt data)

**Phase to address:**
Phase 1 (Hardware Validation) -- must decide the connection strategy before any real hardware work. This is a blocking architectural decision.

---

### Pitfall 2: Huawei "Takeover" Is Not True Setpoint Control

**What goes wrong:**
The current EMS controls Huawei via `write_max_discharge_power` and `write_max_charge_power` -- these are LIMITS, not setpoints. The Huawei internal EMS still decides what to do within those limits. Setting `write_max_discharge_power(2000)` does not mean "discharge at 2000W" -- it means "discharge at whatever the internal EMS decides, up to 2000W." This leads to:
- The battery not discharging when expected (internal EMS sees no load)
- Unpredictable charge rates during grid-charge windows
- Inability to hold battery at a specific power level

The current `HuaweiController.execute()` maps `PRIMARY_DISCHARGE` to `write_max_discharge_power(abs(watts))`, which means the coordinator thinks it allocated 2000W to Huawei, but Huawei might deliver 0W, 500W, or 2000W depending on its own internal logic.

**Why it happens:**
Huawei's `MAXIMISE_SELF_CONSUMPTION` mode (register 47086 = 2) runs its own PV-first algorithm internally. The Modbus registers exposed are configuration limits, not real-time setpoints like Victron's Hub4 AC power setpoint. There is no Huawei equivalent of Victron's Mode 3 external control.

**How to avoid:**
1. Accept this limitation and design around it: use `TOU` mode (register 47086 = 5) with custom charge/discharge time windows to force specific behaviors.
2. Track the delta between commanded and actual power -- if Huawei consistently under-delivers, allocate the gap to Victron.
3. The coordinator's headroom-weighted allocation must treat Huawei's allocation as a ceiling, not a commitment. Victron (with true setpoint control) should be the "slack absorber."
4. Consider a hybrid operating model: Huawei runs its own internal EMS for self-consumption, and the custom EMS only takes over for grid-charge windows and export management via working mode switches.

**Warning signs:**
- Coordinator logs show Huawei allocated X watts but power meter reads 0 or much less
- Grid import during periods where the coordinator thought Huawei was discharging
- Battery SoC not moving despite active discharge command

**Phase to address:**
Phase 1 (Hardware Validation) -- must probe real hardware to understand actual control granularity before building coordinator logic around assumptions.

---

### Pitfall 3: Cross-Charging Between AC-Coupled Batteries

**What goes wrong:**
When Victron discharges (exports AC) and Huawei is in charge mode, the Huawei system can absorb Victron's AC output -- effectively shuffling energy from one battery to the other through two DC-AC-DC conversions at ~85% round-trip efficiency. The system loses 15% of the energy for zero net benefit. In the worst case, both batteries cross-charge each other in alternating cycles, burning energy continuously.

**Why it happens:**
Both battery systems share the same AC bus (household wiring). Neither system knows about the other -- they each see grid power flow and react independently. The Huawei internal EMS might charge from "PV" that is actually Victron discharge power, because from its perspective, power flowing in the AC bus looks identical regardless of source.

**How to avoid:**
1. Never simultaneously discharge one battery while the other charges unless there is a verified household load consuming the discharged power.
2. The coordinator must use a cross-charge detection algorithm: if `huawei_charging_w > 0` AND `victron_discharging_w > 0` (or vice versa), and `grid_power_w` is near zero (no net import/export), cross-charging is occurring.
3. Detection threshold: `min(abs(charge_power), abs(discharge_power)) > 100W` while `abs(grid_power) < 200W` for 2+ consecutive cycles = cross-charge confirmed.
4. Response: immediately set the charging battery to HOLDING. The discharging battery should serve the load alone.
5. Log cross-charge events with energy-loss calculation for monitoring.

**Warning signs:**
- Both batteries active (one charging, one discharging) while grid power is near zero
- Pool SoC flat or declining despite both batteries showing non-zero power
- Anomaly detector efficiency domain should flag round-trip efficiency < 90%

**Phase to address:**
Phase 2 or 3 (Cross-Charge Prevention) -- this is the core feature of the milestone. Build detection first, prevention second, and monitoring third.

---

### Pitfall 4: Victron DESS and Custom EMS Fighting for Control

**What goes wrong:**
If DESS (Dynamic ESS) is active on VRM, it writes its own schedule to `com.victronenergy.settings /Settings/DynamicESS/Schedule` on the Venus GX device. This schedule contains desired SoC, grid feed-in permission, and timing. Simultaneously, the custom EMS writes Hub4 AC power setpoints via Modbus. The two systems fight -- DESS changes the operating mode or SoC target, the EMS writes a contradicting setpoint, the Victron oscillates between passthru and discharge mode every few seconds.

Community reports confirm: "ESS control loop unstable, constant mode switching (external control / passthru)" is a known failure mode when two controllers compete.

**Why it happens:**
DESS runs on the VRM cloud and pushes schedules to the GX device via VRM API. The GX device's ESS implementation applies those schedules. Meanwhile, Modbus Mode 3 setpoints also arrive. There is no arbitration -- whoever writes last wins, until the other writes again.

**How to avoid:**
1. **Decide ownership clearly.** Either DESS controls Victron OR the custom EMS controls Victron. Not both.
2. If using DESS: set the custom EMS to READ-ONLY for Victron (poll state, do not write setpoints). Control only Huawei.
3. If using custom EMS: fully disable DESS by setting `/Settings/DynamicEss/Mode` to `0` on the Venus GX. Verify it stays disabled after VRM firmware updates.
4. If a hybrid approach is wanted (DESS for Victron, custom EMS for Huawei): read DESS schedule via VRM API to inform Huawei coordination, but never write Victron setpoints.

**Warning signs:**
- Victron ESS mode flipping between 2 and 3 in logs
- Setpoints being overwritten within seconds of writing
- Battery oscillating between charge/discharge rapidly (sub-minute cycles)
- Venus GX VRM dashboard showing "External Control" and "Optimized" alternating

**Phase to address:**
Phase 1 (Operating Mode Investigation) -- this must be settled before any Victron control code runs on real hardware.

---

### Pitfall 5: Victron Hub4 60-Second Setpoint Watchdog Timeout

**What goes wrong:**
Victron ESS Mode 3 requires the AcPowerSetpoint register to be written at least once every 60 seconds. If the EMS misses a write (crash, network timeout, slow cycle), the Victron reverts to its internal logic -- which may mean full discharge, full charge, or passthru depending on configuration. With a 5-second control loop, this seems safe, but:
- 12 consecutive failures (12 x 5s = 60s) triggers the watchdog
- Network hiccups during HA add-on restarts or Docker container recreation
- The EMS safe-state logic waits 3 failures before zeroing (15s), but the watchdog is at 60s -- the gap between 15s and 60s is a gray zone where stale setpoints persist

**Why it happens:**
The Victron watchdog is a safety feature. It prevents a crashed external controller from leaving the battery in an unsafe state. But it also means any temporary disruption causes uncontrolled behavior for up to 60 seconds.

**How to avoid:**
1. The existing 3-failure safe-state (15s) is correct -- it writes zero setpoints well before the 60s watchdog expires. Keep this.
2. Add a "last successful write" timestamp. If `now - last_write > 45s`, escalate to emergency: write zero setpoints immediately, regardless of failure count.
3. On EMS startup, immediately write zero setpoints before starting the control loop -- don't assume the previous session left clean state.
4. Log the 60s watchdog explicitly: if the EMS cannot write for 50+ seconds, send a Telegram alert.

**Warning signs:**
- `consecutive_failures >= 9` in VictronController (approaching 60s watchdog)
- Victron power behavior changing without EMS command
- Grid power spikes coinciding with EMS restart timestamps

**Phase to address:**
Phase 2 or 3 (Production Hardening) -- the current safe-state logic is close but needs the secondary 45s timeout guard.

---

### Pitfall 6: Working Mode Switches Causing Transient Power Spikes

**What goes wrong:**
Switching Huawei between working modes (e.g., `MAXIMISE_SELF_CONSUMPTION` to `TOU`) via register 47086 causes a transient period where the battery may briefly discharge or charge at maximum rate before settling into the new mode. The FusionSolar TOU configuration defines charge/discharge windows -- if the current time falls into an "active charge" window during the mode switch, the battery immediately starts charging at full rate.

**Why it happens:**
The Huawei firmware applies the new working mode atomically but the TOU schedule lookup happens after the mode switch. During the transition, the internal state machine may pass through intermediate states. If TOU windows are pre-configured in FusionSolar and the mode switch lands during an active window, the battery acts on the pre-configured schedule immediately.

**How to avoid:**
1. Set TOU time windows via Modbus before switching the working mode -- never switch mode blindly assuming neutral defaults.
2. Write `write_max_discharge_power(0)` and `write_max_charge_power(0)` before switching modes to clamp any transient.
3. After switching, wait 2-3 seconds and re-read battery power to confirm the new mode is stable before resuming normal control.
4. Never switch working modes during periods of high PV production -- the transient could cause grid export spikes.

**Warning signs:**
- Brief power spikes (>3kW) appearing in InfluxDB exactly when mode switches are logged
- Grid export/import spikes at mode transition timestamps
- Battery SoC moving faster than expected during the first 10s after a mode switch

**Phase to address:**
Phase 1 (Hardware Validation) -- test mode switching behavior on real hardware to characterize transient duration and magnitude.

---

### Pitfall 7: Simulated-to-Real Gap -- Code That Passes Tests But Fails on Hardware

**What goes wrong:**
The EMS has ~18,300 LOC of tests across 1,509 test cases, but all use mocked drivers. The real hardware introduces timing, latency, firmware quirks, and electrical behavior that mocks cannot reproduce:
- Modbus TCP over WiFi (Smart Dongle) has 50-200ms latency vs. instant mock responses
- Huawei returns `None` for registers that should have values (firmware version differences)
- Victron register scaling factors differ between Venus OS versions
- Real power readings oscillate 5-10% around setpoints due to inverter PID loops
- pymodbus connection recovery behaves differently under real network conditions

**Why it happens:**
This is the classic simulation-to-production gap. Every mock has implicit assumptions about response format, timing, and error modes. 72% of BESS incidents occur during commissioning or the first two operational years -- the integration and deployment phase is where failures concentrate.

**How to avoid:**
1. First production deployment should be READ-ONLY: poll both batteries, log all data, write zero setpoints. Run for 48+ hours to validate data formats.
2. Second phase: enable writes on ONE battery system only (Victron, because it has true setpoint control and a hardware watchdog). Monitor for 48+ hours.
3. Third phase: enable Huawei writes. Monitor for 48+ hours.
4. Build a "hardware validation" mode that compares live register values against expected ranges and flags anomalies.
5. Every driver method should have a `dry_run` flag that logs the intended write without executing it.

**Warning signs:**
- Any `KeyError`, `TypeError`, or `None` appearing in driver read paths during the first live run
- Actual SoC values outside 0-100% range (firmware quirks)
- Register read returning `0` for all values (wrong slave ID or unit ID)

**Phase to address:**
Phase 1 (Hardware Validation) -- the entire first phase should be about establishing ground truth from real hardware.

---

### Pitfall 8: VRM API Rate Limiting and Stale Data

**What goes wrong:**
The VRM API has undocumented rate limits. Polling DESS schedules too frequently (every 5s to match the control loop) will result in HTTP 429 responses or temporary IP blocks. Additionally, DESS schedule updates are not real-time -- next-day prices may not be available until late afternoon, and the schedule VRM returns via API may lag behind what the GX device actually uses.

Community reports confirm: "VRM-DESS API not updating with next day price and schedules" is a recurring issue. Reading schedules via VRM API may return different values than what the GX device received directly.

**Why it happens:**
VRM is a cloud service designed for monitoring dashboards, not real-time control loops. The API caches aggressively, and schedule updates propagate on their own timeline. The local GX device receives updates via MQTT from VRM, which may differ from what the HTTP API returns.

**How to avoid:**
1. Poll VRM API at most once every 5-15 minutes for DESS schedule reads. Cache locally.
2. Never use VRM API data for real-time control decisions -- only for schedule awareness and coordination.
3. Implement exponential backoff on 429 responses. Start at 60s, max at 15 minutes.
4. If VRM API is unavailable, fall back to local-only operation (the "no cloud dependencies" constraint already demands this).
5. Store a VRM API token with proper refresh logic -- tokens expire and the refresh flow has its own edge cases.

**Warning signs:**
- HTTP 429 responses in VRM client logs
- DESS schedule showing yesterday's data (stale cache)
- Schedule mismatch between VRM API response and actual Victron behavior

**Phase to address:**
Phase 2 (VRM Integration) -- design the client with aggressive caching and graceful degradation from the start.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Treating Huawei discharge limit as a setpoint | Simpler coordinator logic | Coordinator overestimates Huawei contribution, Victron under-compensates | Never in production -- must track actual vs commanded |
| Leaving DESS partially enabled | Less config changes | DESS and EMS fight intermittently, hard to reproduce | Never -- must be fully enabled OR fully disabled |
| Skipping read-only validation phase | Ship faster | First write command may have wrong sign, wrong scale, wrong register | Never -- real money (battery degradation) at stake |
| Hardcoding Victron unit IDs (100, 227) | Works on this specific Venus GX | Fails on different Venus GX firmware or device topology | MVP only -- must be configurable (already in add-on options) |
| Not logging cross-charge events | Less code | Cannot quantify energy losses, cannot prove prevention works | Never -- cross-charge detection is the milestone's core deliverable |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Huawei Modbus TCP | Opening a persistent connection alongside HA integration | Use Modbus Proxy or be the sole client; disable HA integration and publish via MQTT discovery |
| Victron Modbus TCP | Assuming unit ID 227 for VE.Bus | Probe or configure -- unit IDs vary by Venus GX model and connected device count |
| VRM API | Polling every control cycle (5s) | Poll every 5-15 minutes; cache locally; never block control loop on API response |
| DESS Schedule | Reading via VRM API and assuming it matches GX state | Read from GX device directly via Modbus/MQTT if possible; VRM API may be stale |
| EVCC + EMS + Huawei | Three systems sharing one Modbus connection | Modbus Proxy is mandatory when EVCC also reads Huawei; configure timeouts >= 10s |
| Huawei working mode | Switching mode without clamping power first | Always write zero charge/discharge limits before mode switch; wait for confirmation |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Synchronous VRM API calls in control loop | 5s loop stretches to 8-10s; setpoints lag | Move VRM polling to separate async task with its own interval | Immediately on first VRM timeout |
| Huawei Modbus via WiFi dongle under load | Modbus timeouts increase with household WiFi congestion | Use wired Ethernet to Smart Dongle if possible; increase timeout to 10s | Evening peak hours when streaming/gaming competes |
| Cross-charge detection with tight thresholds | False positives during normal PV transitions (clouds) | Use 2-3 cycle debounce (10-15s) and minimum power threshold (100W) | PV ramp events in partly cloudy conditions |
| Writing all Victron phase setpoints sequentially | L1 gets new setpoint 200ms before L3; brief phase imbalance | Write all 3 phases in rapid succession; consider batch register write | Always present but usually negligible |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| VRM API token in plaintext config | Token grants full control of Victron installation via VRM portal | Store in HA secrets or environment variable; never commit to git |
| Modbus TCP with no authentication | Anyone on LAN can write setpoints to either battery system | Accept this (Modbus has no auth); restrict via network segmentation or VLAN |
| Unclamped setpoint values | Writing 30000W to a 5000W inverter could trigger firmware protection faults | Clamp all write values to hardware maximums before sending; read max values from registers |
| No write confirmation | Writing a setpoint and assuming it took effect | Read back the register after writing; compare expected vs actual |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Silent cross-charging with no dashboard indicator | User sees both batteries active, thinks system is working, unaware of 15% energy loss | Show cross-charge warning badge on dashboard; log cumulative energy loss |
| DESS/EMS conflict with no user-facing explanation | User sees erratic battery behavior, blames EMS, when DESS is still active | Show DESS status on dashboard; warn if DESS is detected active |
| Mode switch during user observation | User watches dashboard, sees power spike during mode transition, panics | Show "mode transition" state in UI; explain transient behavior |
| Hardware validation requires manual steps | User must SSH into HA to run probe scripts | Expose hardware validation as a button in the add-on config or a dedicated setup step |

## "Looks Done But Isn't" Checklist

- [ ] **Huawei control:** Working in tests with mocks does NOT mean it works on hardware -- verify with `scripts/probe_huawei.py` on real inverter, confirm register reads return expected types
- [ ] **Cross-charge detection:** Algorithm detects synthetic cross-charge in tests -- verify it detects real cross-charge with actual power meter data (real power oscillates, synthetic data is clean)
- [ ] **DESS disabled:** Set `/Settings/DynamicEss/Mode` to `0` once -- verify it stays disabled after Venus OS firmware updates (some updates re-enable defaults)
- [ ] **Modbus Proxy stability:** Proxy works for 5 minutes during testing -- verify it survives 48+ hours without dropped connections or memory leaks
- [ ] **VRM API token refresh:** Token works for initial call -- verify refresh flow handles token expiry after 24-48 hours
- [ ] **Safe state on EMS crash:** EMS writes zero before shutdown -- verify the Victron 60s watchdog actually reverts to safe behavior (not just passthru)
- [ ] **Sign conventions on real hardware:** Tests use mocked positive/negative values -- verify actual register values match expected sign convention on THIS firmware version
- [ ] **TOU schedule persistence:** TOU windows written via Modbus survive inverter reboot -- verify with power cycle test

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Cross-charging detected | LOW | Immediately set both batteries to HOLDING; log event; resume after 30s with discharge-only mode |
| DESS/EMS conflict causing oscillation | LOW | Set DESS mode to 0 via GX device; restart EMS; verify stable |
| Huawei Modbus connection conflict with HA | MEDIUM | Stop HA integration; configure Modbus Proxy; reconfigure both clients; restart |
| Wrong sign convention on real hardware | MEDIUM | Switch to read-only mode immediately; analyze register values; fix sign mapping; redeploy |
| Setpoint written to wrong register (wrong unit ID) | HIGH | Stop EMS immediately; check battery status via vendor app; may need manual firmware reset if protection tripped |
| Working mode switch causes power spike triggering grid relay | HIGH | Grid relay trips; wait for manual reset; investigate spike magnitude; add pre-switch clamping |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Huawei single-connection exclusion | Phase 1 (Hardware Validation) | HA integration and EMS both connected simultaneously for 1 hour without errors |
| Huawei limit-not-setpoint confusion | Phase 1 (Hardware Validation) | Command 2000W discharge, measure actual power at meter, document deviation |
| Cross-charging between batteries | Phase 2/3 (Cross-Charge Prevention) | Intentionally create cross-charge scenario, verify detection fires within 15s, verify prevention stops it |
| DESS/EMS dual control conflict | Phase 1 (Operating Mode Decision) | Run EMS control loop for 1 hour with DESS disabled; verify no mode oscillation in Venus GX logs |
| Victron 60s setpoint watchdog | Phase 2 (Production Hardening) | Kill EMS process; verify Victron reverts to safe state within 60s; verify no uncontrolled discharge |
| Working mode transient spikes | Phase 1 (Hardware Validation) | Switch modes while monitoring grid meter; measure spike magnitude and duration |
| Simulated-to-real gap | Phase 1 (Hardware Validation) | 48-hour read-only run with all data logged; compare against mock assumptions |
| VRM API rate limiting | Phase 2 (VRM Integration) | Poll VRM API at 5-minute intervals for 24 hours; confirm no 429 responses; verify data freshness |

## Sources

- [Huawei Solar HA Integration -- single connection limitation](https://github.com/wlcrs/huawei_solar)
- [Modbus Proxy stability with Huawei + EVCC](https://github.com/wlcrs/huawei_solar/discussions/699)
- [Victron ESS Mode 2 and 3 documentation -- 60s setpoint timeout](https://www.victronenergy.com/live/ess:ess_mode_2_and_3)
- [Victron ESS control loop instability with external control](https://community.victronenergy.com/t/ess-control-loop-unstable-constant-mode-switching-external-control-passthru/55252)
- [Victron Dynamic ESS GitHub repository](https://github.com/victronenergy/dynamic-ess)
- [VRM DESS API schedule inconsistencies](https://community.victronenergy.com/t/is-vrm-dess-api-not-updating-with-next-day-price-and-schedules/37361)
- [VRM DESS API bug reports](https://community.victronenergy.com/t/bug-in-vrm-api-dess/43034)
- [Huawei Modbus register definitions -- working mode 47086](https://support.huawei.com/enterprise/de/doc/EDOC1100387581)
- [Victron AC coupling and Factor 1.0 rule](https://www.victronenergy.com/live/ac_coupling:start)
- [BESS failure incident analysis -- 72% in first 2 years](https://www.utilitydive.com/news/cells-and-modules-not-responsible-for-most-battery-energy-storage-system-fa/716732/)
- [EVCC Huawei Modbus TCP discussion](https://github.com/evcc-io/evcc/discussions/1928)

---
*Pitfalls research for: EMS v2 production deployment, cross-charge prevention, VRM/DESS integration*
*Researched: 2026-03-24*
