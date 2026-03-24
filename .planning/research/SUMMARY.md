# Project Research Summary

**Project:** EMS v1.4 — Production Deployment and Cross-Charge Prevention
**Domain:** Dual-battery energy management production commissioning (Huawei LUNA2000 + Victron MultiPlus-II)
**Researched:** 2026-03-24
**Confidence:** MEDIUM (high on implementation patterns; medium on real-hardware behavior)

## Executive Summary

This milestone (v1.4) transitions EMS v2 from a tested, HA-Add-on-packaged system into a live production deployment controlling two real battery systems — a 30 kWh Huawei LUNA2000 and a 64 kWh Victron MultiPlus-II — across a 94 kWh AC-coupled pool. The critical insight from all four research streams is that **zero new dependencies are required**. The existing stack (huawei-solar, pymodbus, httpx, FastAPI) already contains every primitive needed. Three new modules must be built — `VrmClient`, `CrossChargeDetector`, `HuaweiModeManager` — but all fit cleanly into the established optional-injection pattern that every other integration already uses.

The central architectural recommendation is the **hybrid operating mode**: DESS manages Victron (it has sophisticated hourly pricing optimization already running), while the EMS takes full control of Huawei via TOU working mode (register 47086 = 5). The EMS reads the DESS schedule from the Venus OS MQTT broker to understand Victron's planned behavior and avoid issuing contradicting setpoints. This approach reduces cross-charge risk, eliminates the dual-controller fight, and defers the significantly more complex "full EMS control of both batteries" architecture to a later operating mode investigation phase.

The defining risk for this milestone is the simulation-to-real hardware gap. 72% of BESS incidents occur in the first two operational years during commissioning. The Huawei SDongle only supports one concurrent Modbus TCP connection, Huawei power limits are ceilings (not setpoints), mode switches cause transient power spikes, and the Victron 60-second setpoint watchdog creates a failure window. Every one of these pitfalls is documented and avoidable — but only through a deliberate phased rollout that starts read-only and introduces writes incrementally on each battery system before enabling full coordinator control.

## Key Findings

### Recommended Stack

The entire v1.4 feature set requires **no new pip dependencies**. This is the key finding from STACK.md. VRM API integration uses `httpx` (already installed) via a thin custom `VrmClient` wrapper — the two existing PyPI packages (`victron-vrm`, `vrmapi`) are explicitly rejected as immature or synchronous. Huawei remote control uses registers already supported by the installed `huawei-solar >= 2.5` library; three new driver methods (`write_forcible_mode`, `write_forcible_charge_power`, `write_forcible_discharge_power`) follow the identical pattern of the existing five write methods. Cross-charge detection is pure coordinator arithmetic requiring no external calls.

**Core technologies:**
- `huawei-solar >= 2.5` (existing): Huawei Modbus TCP read/write — already has every needed register including `StorageWorkingModesC.TIME_OF_USE_LUNA2000` and `StorageForcibleChargeDischarge` enums, verified from installed library
- `pymodbus >= 3.11, < 4` (existing): Victron Modbus TCP via Venus OS — fully operational, no changes needed
- `httpx` (existing): VRM REST API client — async-native, fits existing patterns; custom wrapper preferred over third-party clients
- `FastAPI` (existing): new `/api/vrm/*` and `/api/cross-charge/*` endpoints follow existing REST patterns

**Control strategy for Huawei:** Start with forcible charge/discharge (Option B) rather than TOU period writes. Forcible mode requires three new write methods vs. complex multi-register TOU struct writes that need careful field testing. Migrate to TOU if forcible proves insufficient.

**DESS schedule reading:** Use Venus OS MQTT (`N/{portalId}/settings/0/Settings/DynamicEss/Schedule/#`) rather than VRM REST API. MQTT provides the authoritative schedule from the GX device itself; VRM API may return stale data and adds a cloud dependency for core scheduling logic.

### Expected Features

**Must have (table stakes) — blocking for production:**
- **Huawei working mode takeover** — without this, `write_max_charge_power` / `write_max_discharge_power` are advisories the Huawei internal EMS ignores; switch to `TIME_OF_USE_LUNA2000` (register 47086 = 5) on EMS startup
- **Cross-charge detection** — defining failure mode of AC-coupled dual-battery systems; 15-20% round-trip energy loss; coordinator must detect within 2 cycles (10s) and mitigate automatically
- **Cross-charge prevention in coordinator** — role exclusion guard: never assign opposing discharge/charge roles unless grid power flow confirms real demand exists to justify it
- **Session health validation** — extend existing `connect()` health checks with write-back verification; mandatory before any setpoint is written to real hardware
- **Safe shutdown / mode restoration** — FastAPI lifespan shutdown hook; restore Huawei to `MAXIMISE_SELF_CONSUMPTION`, zero all Victron setpoints; must be idempotent

**Must have — production deployment:**
- **Incremental commissioning protocol** — 48-hour read-only run, then single-battery writes, then dual-battery; validates hardware assumptions before risking real control
- **Modbus connection strategy decision** — Modbus Proxy or sole-client architecture for Huawei; this is a blocking architectural decision required before any hardware work

**Should have (differentiators):**
- **VRM DESS schedule reading** — via Venus OS MQTT; coordinator awareness of DESS's planned charge/discharge windows avoids fighting Victron
- **Dual-scheduler coordination** — merge internal schedule (Huawei) and DESS schedule (Victron) at coordinator level
- **Hub4 override coordination** — clear ownership strategy: DESS controls Victron OR EMS does, never both; exposed as Add-on configuration option
- **Cross-charge waste metric** — track cumulative energy lost to cross-charging in InfluxDB; proves prevention is working

**Defer to v2+:**
- Huawei TOU schedule programming (multi-register struct writes, firmware behavior varies)
- Dual-scheduler full optimization (requires operating mode investigation to determine if needed)
- Operating mode A/B comparison analysis (multi-day dataset collection)

**Anti-features (explicitly avoid):**
- VRM cloud API for schedule writes — violates local-only constraint
- Bidirectional DESS schedule manipulation — creates dual-controller oscillation
- Register 47589 remote control mode — disables ALL Huawei internal safety mechanisms, no fallback on EMS crash
- Real-time cross-inverter AC power balancing — two actuators with different response times (Huawei ~2s, Victron ~0.5s) guarantees oscillation

### Architecture Approach

All four new components follow the existing optional-injection pattern (`set_*()` methods, `None` guards, fire-and-forget). The `CrossChargeDetector` acts as an inline command interceptor inside `Coordinator._run_cycle()` — the same pattern already used by EVCC hold and mode overrides (lines 573, 604 of coordinator.py). DESS schedule data arrives via asynchronous MQTT and is cached for synchronous consumption during the 5-second control loop, matching the `EvccMqttDriver.evcc_battery_mode` pattern exactly. No new component is required for the system to start; all degrade gracefully when absent.

**Major components:**
1. `CrossChargeDetector` (`backend/cross_charge.py`, NEW) — detects battery-to-battery energy transfer via AC bus; 2-cycle debounce, 100W threshold; mitigation forces charging battery to HOLDING; zero external dependencies
2. `HuaweiModeManager` (`backend/huawei_mode.py`, NEW) — state machine for TOU mode takeover on startup and release to self-consumption on shutdown; uses only existing HuaweiDriver write methods; 5-second settle delay after mode transitions
3. `VrmClient` (`backend/vrm_client.py`, NEW) — async httpx wrapper for VRM diagnostics polling (5-minute interval); Personal Access Token auth; cached result, never blocks the control loop
4. Venus MQTT DESS subscription (extend existing MQTT infrastructure) — subscribes to DESS schedule D-Bus paths on Venus OS MQTT broker; updates coordinator's schedule cache asynchronously

**Modified components:** `Coordinator` (cross-charge guard + DESS-aware allocation + 3 new injection methods + `CoordinatorState` extension), `HuaweiController` (mode manager wiring at startup/shutdown), `controller_model.py` (new trigger values: `cross_charge_prevention`, `ems_mode_transition`, `dess_coordination`), `config.py` (new `VrmConfig` and `CrossChargeConfig` dataclasses), `backend/main.py` (wire new components in lifespan).

**Unchanged components:** `VictronDriver`, `HuaweiDriver`, `Scheduler`, `WeatherScheduler`, `ExportAdvisor`, frontend (minimal — cross-charge status surfaced via existing `CoordinatorState` WebSocket).

**Key data flow addition:** After computing commands but before executing them, the coordinator's `_run_cycle()` runs the cross-charge guard. If cross-charge is detected, commands are modified in place and a `DecisionEntry` is logged. This is a zero-latency safety net with no external dependencies.

### Critical Pitfalls

1. **Huawei Modbus single-connection exclusion** — The SDongle firmware rejects a second TCP client entirely. If EMS connects, the HA `wlcrs/huawei_solar` integration goes unavailable and returns errors. Two choices: (a) Modbus Proxy (serializes connections, increases timeout to 10s) or (b) disable the HA integration and let EMS be sole client (already publishes 17 entities via MQTT discovery). This is a blocking architectural decision required before Phase 1.

2. **Huawei power limits are ceilings, not setpoints** — `write_max_discharge_power(2000)` means "up to 2000W as Huawei's internal EMS decides" — not "discharge at exactly 2000W." The coordinator must treat Huawei's allocation as a ceiling, not a commitment. Victron (with true Mode 3 setpoint control) must be the slack absorber. Validate actual vs. commanded deviation on real hardware before tuning any allocation logic.

3. **Cross-charging through the AC bus** — 15-20% round-trip energy loss; occurs silently when one battery discharges while the other charges with no household load to justify it. Detection: `abs(charge_power) > 100W` and `abs(discharge_power) > 100W` while `abs(grid_power) < 200W` for 2+ consecutive cycles. Response: immediately force the charging battery to HOLDING. This is the milestone's core deliverable.

4. **DESS/EMS dual-controller conflict** — If DESS is active on VRM and EMS simultaneously writes Victron Hub4 setpoints, both controllers write to the same registers and Victron oscillates between modes (community-confirmed failure pattern: "constant mode switching external-control/passthru"). Prevention: clear ownership at configuration time. Recommended: EMS controls Huawei, DESS controls Victron, EMS reads DESS schedule but never writes Victron setpoints.

5. **Simulation-to-real hardware gap** — All 1,509 test cases use mocked drivers. Real hardware introduces WiFi latency (50-200ms from SDongle), `None` returns from unexpected registers, firmware version differences in register scaling, real power oscillation (5-10% around setpoints), and pymodbus recovery behavior under real network conditions. Mitigation: mandatory 48-hour read-only phase before any writes; single-battery enables before dual-battery; `dry_run` flag on all write methods.

6. **Working mode switch transient power spikes** — Switching Huawei between working modes via register 47086 causes a transient period where the battery may briefly charge or discharge at maximum rate before settling. Prevention: clamp power limits to zero before switching, wait one control cycle (5s) to confirm mode is active, then resume normal setpoints. Never switch during high PV production periods.

## Implications for Roadmap

Based on research, the dependency chain mandates this order: (1) establish hardware ground truth and make blocking architectural decisions, (2) build the cross-charge safety net as pure code (no hardware dependencies), (3) enable Huawei mode control on validated hardware, (4) commission incrementally, (5) add DESS coordination if the hybrid operating mode is chosen.

### Phase 1: Hardware Validation and Operating Mode Decision

**Rationale:** All further work depends on establishing ground truth from real hardware and resolving three blocking decisions: (a) Modbus single vs. Proxy connection strategy for Huawei, (b) DESS enabled/disabled for Victron, (c) whether Huawei power limits give sufficient actual control granularity. These cannot be answered without a live system. Running read-only first establishes correct register types, sign conventions, and scale factors before any write path is built.
**Delivers:** Validated register reads from both batteries, confirmed sign conventions, documented actual vs. commanded power deviation profile, operating mode decision (DESS hybrid vs. full EMS control), Modbus connection architecture decided
**Addresses:** Session health validation (table stake), simulated-to-real gap (pitfall 7), mode-switch transient characterization
**Avoids:** Pitfalls 1 (Modbus exclusion), 2 (limit-not-setpoint), 4 (DESS conflict), 6 (mode-switch spikes), 7 (sim gap)
**Research flag:** Requires real hardware access; cannot be unit-tested or mocked. Use `scripts/probe_huawei.py` and `scripts/probe_victron.py`.

### Phase 2: Cross-Charge Detection and Prevention

**Rationale:** Must be in place before any production control of both batteries. Cross-charge prevention is the core deliverable of this milestone. Crucially, it is pure Python logic with zero hardware dependencies — can be built and unit-tested entirely with mock snapshots while Phase 1 hardware validation runs. No reason to block this on hardware access.
**Delivers:** `CrossChargeDetector` module, inline guard in `Coordinator._run_cycle()`, `CrossChargeState` dataclass, Telegram alerts on first detection per episode, `DecisionEntry` trigger type, cross-charge waste metric in InfluxDB
**Addresses:** Cross-charge detection and prevention (must-have table stakes), cross-charge waste metric (differentiator)
**Uses:** Existing `ControllerSnapshot`, `ControllerCommand`, `coordinator.py` injection pattern
**Avoids:** Pitfall 3 (cross-charging); false positives during PV ramp events (2-cycle debounce and 100W minimum threshold are mandatory, not optional)
**Research flag:** Standard patterns — no additional research needed. Well-understood coordinator injection model with clear precedents in the codebase.

### Phase 3: Huawei Mode Manager

**Rationale:** After Phase 1 confirms TOU mode behavior on real hardware and Phase 2 cross-charge guard is in place as a safety net, the EMS can take authoritative control of Huawei. The mode manager code is straightforward (uses only existing driver write methods), but requires hardware validation to confirm TOU mode accepts external power limits as expected and to set the correct settle delay for mode transitions.
**Delivers:** `HuaweiModeManager` state machine, startup TOU takeover with 5s settle delay, shutdown mode restoration, periodic mode health check (confirm Huawei hasn't reverted), HA MQTT entity exposing current Huawei working mode
**Uses:** Existing `HuaweiDriver.write_battery_mode()`, `write_ac_charging()`, `write_max_charge_power()`, `write_max_discharge_power()`
**Avoids:** Pitfall 6 (mode-switch transients — clamp power before switching, wait for confirmation); Pitfall 2 (limit-not-setpoint — track actual vs. commanded deviation)
**Research flag:** Code is standard; field validation of TOU mode settling time and setpoint acceptance on this specific firmware version is the unknown. Phase 1 must characterize this before implementation.

### Phase 4: Incremental Production Commissioning

**Rationale:** Enable actual battery writes in the documented sequence: 48-hour read-only (Phase 1 handles this), then Victron-only writes, then Huawei writes, then dual-battery coordination with cross-charge prevention active. This is primarily an operational commissioning phase, not a code phase. The main work is monitoring setup, alerting validation, and gradual rollout with documented fallback procedures.
**Delivers:** Live EMS controlling both batteries under safe, validated conditions; Modbus Proxy configured and stable over 48+ hours; shadow mode (log decisions without executing) confirmed working; Victron 60s watchdog secondary guard (45s emergency zero-write) in place
**Addresses:** Production deployment on HA (must-have table stake)
**Avoids:** Pitfall 5 (Victron 60s watchdog — add 45s emergency zero-write guard as belt-and-suspenders); Pitfall 7 (sim-to-real gap via staged single-battery then dual-battery rollout)
**Research flag:** No code research needed. Operational validation is the work. Staged rollout steps are well-defined from research.

### Phase 5: VRM/DESS Integration (conditional)

**Rationale:** Only needed if Phase 1 operating mode investigation selects the DESS hybrid model (EMS controls Huawei, DESS controls Victron) AND Phase 4 field data shows cross-charge risk that DESS schedule awareness would reduce. If full EMS control (Mode A) is chosen in Phase 1, this phase is skipped entirely. This gate prevents building a feature that may be unnecessary.
**Delivers:** `VrmClient` for diagnostics, Venus OS MQTT DESS schedule subscription, `DessSchedule` data model, DESS-aware coordinator allocation (Victron HOLDING during DESS windows), API endpoint for DESS schedule status, dashboard indicator for DESS activity
**Uses:** `httpx` (existing), Venus OS MQTT broker (local network, same infrastructure as EVCC MQTT), Personal Access Token auth
**Avoids:** Pitfall 4 (DESS/EMS conflict — EMS reads DESS but never writes Victron setpoints); Pitfall 8 (VRM rate limiting — 5-minute poll interval, aggressive caching, exponential backoff on 429)
**Research flag:** Venus OS MQTT topic paths for DESS schedule need field validation on real Venus OS. VRM DESS API response schema is inferred from community sources, not confirmed. Plan for schema discovery work at the start of this phase.

### Phase Ordering Rationale

- Phase 1 must be first because the Modbus connection strategy and operating mode decision gate all subsequent code decisions
- Phase 2 (cross-charge detection) can proceed in parallel with Phase 1 hardware work using mocks; it is architecturally independent and must complete before Phase 4 live dual-battery writes
- Phase 3 (Huawei mode manager) depends on Phase 1 confirming TOU mode behavior; cannot be validated correctly without hardware data from Phase 1
- Phase 4 (commissioning) depends on Phases 1-3 all being complete; it is the integration point
- Phase 5 (VRM/DESS) is conditional on Phase 1 operating mode decision; may be skipped entirely

### Research Flags

Phases needing field validation or deeper investigation during execution:
- **Phase 1:** Real hardware validation is the entire deliverable; Huawei TOU mode settling behavior, actual vs. commanded power deviation, and Modbus single-connection exclusion timing all need live hardware probing with `scripts/probe_huawei.py`
- **Phase 3:** TOU mode settling time and transient spike magnitude are hardware-specific; cannot be set correctly until Phase 1 characterizes them on this specific firmware version
- **Phase 5:** Venus OS MQTT topic structure for DESS schedule paths needs field validation; VRM DESS API schema needs confirmation against a live VRM installation with DESS enabled

Phases with standard patterns (skip additional research):
- **Phase 2:** Cross-charge detection is pure coordinator logic following existing injection patterns; fully unit-testable with mocks; implementation can start immediately
- **Phase 4:** Commissioning protocol is operational, not code research; the staged rollout steps are well-defined from pitfalls research

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Zero new dependencies confirmed from installed library inspection; all write methods verified against huawei-solar source code; enum values confirmed from local install |
| Features | HIGH | Feature set well-defined from codebase analysis, hardware documentation, and community incident reports; cross-charge detection algorithm is deterministic; anti-features clearly motivated |
| Architecture | MEDIUM | Component interfaces are clear and follow proven patterns; DESS schedule MQTT path structure needs field validation on real Venus OS; VRM DESS API response schema is inferred |
| Pitfalls | HIGH | Based on direct codebase analysis, hardware vendor documentation (official Huawei Modbus register docs), and verified community incident reports; all pitfalls have documented mitigations |

**Overall confidence:** MEDIUM

### Gaps to Address

- **Huawei TOU mode setpoint acceptance:** Does the inverter treat `write_max_charge_power()` / `write_max_discharge_power()` as true setpoints when in TOU mode, or still as ceilings? Validate in Phase 1 on real hardware before designing coordinator allocation around the answer.
- **Venus OS MQTT DESS topic schema:** D-Bus paths documented in dynamic-ess GitHub, but exact MQTT topic structure and JSON payload format need validation against a running Venus OS instance before building the subscription handler.
- **Mode-switch transient duration and magnitude:** Pitfall 6 warns of power spikes during working mode transitions. Phase 1 must characterize spike duration on this specific Huawei firmware to set the correct pre-switch clamping and post-switch settle wait time.
- **Modbus Proxy 48-hour stability:** Community reports document memory leak and connection-drop behavior in some proxy implementations. Phase 1 must validate chosen proxy solution over 48+ hours before trusting it for production.
- **VRM DESS API schema:** DESS schedule endpoint response field names are inferred from community sources and the dynamic-ess repo. Needs confirmation against a live VRM installation with DESS enabled before building the `DessSchedule` data model.

## Sources

### Primary (HIGH confidence)
- Installed `huawei-solar` library — `StorageWorkingModesC`, `StorageForcibleChargeDischarge` enums verified from local installation on this system
- Existing codebase (`coordinator.py`, `huawei_driver.py`, `victron_driver.py`, `controller_model.py`) — integration patterns, existing write methods, EVCC injection precedent
- [Huawei SUN2000 Modbus Interface Definitions](https://support.huawei.com/enterprise/de/doc/EDOC1100387581) — register 47086 (working mode), 47087 (AC charging), 47589 (control level)
- [Victron ESS Mode 2 and 3 documentation](https://www.victronenergy.com/live/ess:ess_mode_2_and_3) — 60-second setpoint watchdog, Hub4 control registers, Mode 3 external control behavior

### Secondary (MEDIUM confidence)
- [VRM API Documentation](https://vrm-api-docs.victronenergy.com/) — endpoint reference, Personal Access Token auth method
- [Dynamic ESS GitHub](https://github.com/victronenergy/dynamic-ess) — DESS D-Bus paths, schedule structure, D-Bus to MQTT mapping
- [Victron dbus-mqtt](https://github.com/victronenergy/dbus-mqtt) — Venus OS MQTT bridge topic format
- [Huawei Solar HA Integration](https://github.com/wlcrs/huawei_solar) — single-connection limitation documentation; Modbus Proxy discussions
- [Victron Community: ESS control loop instability](https://community.victronenergy.com/t/ess-control-loop-unstable-constant-mode-switching-external-control-passthru/55252) — DESS/EMS conflict confirmed as known failure pattern

### Tertiary (LOW confidence — needs field validation)
- [VRM Dynamic ESS API](https://vrm-dynamic-ess-api.victronenergy.com/docs) — DESS schedule endpoint; schema inferred, not confirmed against live system
- [VRM DESS API schedule inconsistencies](https://community.victronenergy.com/t/is-vrm-dess-api-not-updating-with-next-day-price-and-schedules/37361) — stale data behavior documented in community
- [Node-RED VRM API + DESS](https://communityarchive.victronenergy.com/articles/293324/node-red-vrm-api-and-dynamic-ess-1.html) — endpoint usage examples, DESS API shape hints
- BESS failure incident analysis — "72% in first 2 years" statistic from [Utility Dive](https://www.utilitydive.com/news/cells-and-modules-not-responsible-for-most-battery-energy-storage-system-fa/716732/)

---
*Research completed: 2026-03-24*
*Ready for roadmap: yes*
