# Feature Landscape

**Domain:** Production deployment, VRM/DESS integration, cross-charge prevention for dual-battery EMS
**Researched:** 2026-03-24
**Milestone:** v1.4 Production Deployment & Cross-Charge Prevention

## Table Stakes

Features required for the system to run safely in production with both batteries under coordinated control. Missing any of these means the EMS cannot be trusted with real hardware and real money.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Huawei working mode takeover | The Huawei SUN2000 has its own internal EMS (self-consumption optimization). The EMS must switch the inverter to a mode where external setpoints are obeyed, not internally overridden. Without this, `write_max_charge_power` / `write_max_discharge_power` are advisory at best. | MEDIUM | Register `storage_working_mode_settings` (47086) via `huawei-solar` library. Current driver already has `write_battery_mode(StorageWorkingModesC)`. The mode `MAXIMISE_SELF_CONSUMPTION` is the default internal mode. For EMS control, need to switch to `TIME_OF_USE` (value 5) which makes the inverter follow externally-configured charge/discharge windows, or explore register 47589 (inverter control level) for full remote takeover. The `huawei-solar` library already exposes `StorageWorkingModesC` enum. **Critical**: must restore original mode on EMS shutdown. |
| Cross-charge detection | When one battery discharges while the other charges, energy flows through the AC bus from Battery A to Battery B -- a pure loss (2x conversion = ~15-20% wasted). This is the defining failure mode of AC-coupled dual-battery systems. The coordinator must detect and prevent it. | HIGH | Not a hardware problem -- this is a coordinator logic problem. Both controllers operate via the coordinator, so the coordinator has full visibility. Detection: check if one system has a DISCHARGE role while the other has a CHARGING role simultaneously, and grid power is near zero (energy is circulating, not flowing to/from grid). Prevention: the coordinator must never assign contradictory roles unless the energy flow direction justifies it (e.g., grid import covers both charging battery and household load). |
| Cross-charge prevention in coordinator | The coordinator's `_assign_discharge_roles` and `_allocate` methods must enforce that discharge and charge commands are mutually exclusive unless grid power flow confirms real demand exists. | HIGH | Must modify the coordinator's control loop to add a cross-charge guard. At minimum: if `h_cmd.role` is discharge and `v_cmd.role` is charge (or vice versa), verify that measured grid import exceeds the charge power. If not, force the charging battery to HOLDING. Depends on reliable grid power measurement (available from Victron `grid_power_w`). |
| Session health validation on startup | Before sending any setpoints to real hardware, confirm Modbus connectivity, correct unit IDs, and that the inverter responds to register reads. A misconfigured unit ID could write to the wrong device. | LOW | Both drivers already have `connect()` with health checks. Need to extend startup to verify write capability: do a read-back after the first write to confirm the register was accepted. Especially important for Huawei where the SDongle2 proxy can silently drop writes. |
| Safe shutdown / mode restoration | When EMS stops (crash, update, user action), batteries must return to a safe autonomous state. Huawei must be restored to its original working mode (self-consumption). Victron Hub4 setpoints should be zeroed. | MEDIUM | Add `async def shutdown()` to both controllers. HuaweiController: restore `StorageWorkingModesC.MAXIMISE_SELF_CONSUMPTION`. VictronController: write zero setpoints to all phases. Register with FastAPI lifespan `shutdown` hook. Must be idempotent (safe to call multiple times). |
| Production deployment on HA | The system must actually run on the target HA instance controlling real hardware. All configuration via Add-on options, no manual SSH required. | MEDIUM | The HA Add-on packaging (Phase 6, Phase 12) is done. This is about operational validation: correct `host_network: true`, Modbus TCP reachable from container, correct unit IDs, config options for Huawei master/slave IDs and Victron system/VEBus IDs. |

## Differentiators

Features that add significant value but the system can ship without them initially. These separate this EMS from a simple "set and forget" battery controller.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| VRM API DESS schedule reading | Read Victron's Dynamic ESS (DESS) schedule to understand what charge/discharge windows DESS has planned. This enables a hybrid mode: let DESS manage Victron while EMS controls Huawei, with awareness of DESS plans for coordinated optimization. | MEDIUM | DESS writes schedules to Venus OS D-Bus: `/Settings/DynamicESS/Schedule/[0-3]/[Soc,AllowGridFeedIn,Start,Duration,Restrictions,Strategy]`. Each slot has: target SoC (%), start (unix timestamp), duration (seconds, typically 3600), restrictions (0=none, 1=no-export, 2=no-import), strategy (0=follow-SOC, 1=minimize-grid). **Two access paths**: (1) Local: read via Modbus TCP from Venus OS -- these settings may not be exposed via standard Modbus register list, would need custom D-Bus bridge or `dbus-mqtt` topic subscription. (2) Remote: VRM Dynamic ESS API at `vrm-dynamic-ess-api.victronenergy.com` with `X-Authorization: Token <access-token>`, endpoint `GET /` returns schedule with battery config params. The API is designed for *writing* schedules (it calculates optimal schedules given prices), not just reading. **Recommendation**: prefer local Venus OS access via MQTT (`/Settings/DynamicEss/Schedule/...` topics via dbus-mqtt) over VRM cloud API to maintain local-only constraint. |
| Dual-scheduler coordination | Run the internal EMS scheduler alongside DESS. Internal scheduler handles Huawei grid-charge windows; DESS handles Victron. Coordinator merges both schedules for cross-charge prevention. | HIGH | The existing `Scheduler` and `WeatherScheduler` produce `ChargeSchedule` / `DayPlan` for the internal system. A new `DessScheduleReader` would produce an equivalent schedule for Victron's planned behavior. The coordinator would then have two schedule inputs and must ensure they don't conflict (both batteries charging from grid simultaneously may exceed grid import limits; one charging while other discharges = cross-charge). |
| Cross-charge energy waste metric | Track and expose how much energy was lost to cross-charging (historical). Even with prevention, transient cross-charge will occur during role transitions. Measuring it proves the prevention works. | LOW | Calculate `cross_charge_waste_wh` per cycle: `min(abs(charge_power), abs(discharge_power)) * cycle_seconds * (1 - efficiency)` when both batteries have opposing power signs. Log to InfluxDB, expose via API. Target: <1% of daily throughput. |
| Huawei TOU schedule programming | Instead of continuous max-charge/max-discharge power limits, program Huawei's native TOU mode with specific charge/discharge windows. This gives the Huawei internal EMS a schedule to follow even if the external EMS loses connectivity. | HIGH | Requires writing TOU charge/discharge periods via Modbus registers. The `huawei-solar` library supports `storage_working_mode_settings` = TOU, plus additional registers for charge periods (47200-47249 range for up to 14 time-of-use periods). Each period has: start hour, start minute, end hour, end minute, charge/discharge flag, electricity price. Complex register writes with validation. **Risk**: if Huawei firmware rejects malformed TOU entries, the battery may revert to default behavior unpredictably. |
| Operating mode investigation | Systematically test and document two operating strategies: (A) Full EMS control of both batteries via Modbus, (B) DESS controls Victron + EMS controls Huawei. Determine which delivers better optimization. | MEDIUM | Not a code feature but a research/validation task. Requires running both modes for several days each, comparing: total grid import, self-consumption ratio, cross-charge losses, system stability. Results inform whether VRM/DESS integration is needed at all. |
| Victron Hub4 override coordination | When DESS is active, it writes to Hub4 overrides (`/Overrides/ForceCharge`, `/Overrides/MaxDischargePower`, `/Overrides/Setpoint`). If EMS also writes Hub4 setpoints, there will be contention. Need to either disable DESS and let EMS control directly, or read DESS state and only control Huawei. | MEDIUM | Three strategies: (1) Disable DESS (`/Settings/DynamicEss/Mode = 0`) and let EMS control both batteries via Hub4 directly -- simplest. (2) Leave DESS active, read its schedule, and only write Huawei setpoints -- safest for Victron side. (3) Override DESS when EMS disagrees -- most complex, risk of oscillation between two controllers. **Recommendation**: Start with strategy (2), fall back to (1) if cross-charge prevention requires direct Victron control. |

## Anti-Features

Features to explicitly NOT build. These look tempting but create more problems than they solve.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Direct VRM cloud API for schedule writes | Violates the local-only constraint. Creates cloud dependency for core operation. VRM API has authentication tokens that expire, rate limits, and connectivity requirements. | Read DESS schedule locally via Venus OS D-Bus/MQTT. If VRM data is needed for forecasts, treat it as optional enhancement (graceful degradation). |
| Bidirectional DESS schedule manipulation | Writing custom schedules to DESS from EMS creates a dual-controller fight. DESS recalculates every few minutes and would overwrite EMS changes. Two optimizers fighting over the same battery = oscillation. | Choose one controller per battery: either DESS manages Victron or EMS does, never both simultaneously. |
| Huawei register 47589 remote control mode | Register 47589 (inverter control level) sets the inverter to full remote control modes (0=local, 1-5=remote). Mode 1 ("remote charge/self-discharge") and mode 5 ("three-party scheduling") give maximum control but disable ALL internal safety and optimization. If EMS crashes, Huawei does nothing -- no self-consumption, no self-protection. | Use `storage_working_mode_settings` (47086) to switch to TOU mode, which still allows Huawei's internal EMS to handle safety while following external time-based instructions. Or use max-charge/max-discharge power limits (current approach) which constrain but don't override the internal EMS. |
| Real-time grid power balancing across both inverters | Trying to coordinate instantaneous AC power output from both inverters to match household load creates a control theory nightmare. Two independent actuators with different response times (Huawei ~2s, Victron ~0.5s) and different measurement points = guaranteed oscillation. | Continue the current role-based approach: one battery is PRIMARY_DISCHARGE at a time. The other is HOLDING or SECONDARY. Only one actuator targets grid power; the other has a fixed setpoint. |
| Automatic DESS disable/enable toggling | Automatically disabling DESS when EMS wants direct Victron control and re-enabling it afterward creates race conditions and confusing states. User won't know who is controlling what. | Pick one strategy at configuration time and stick with it. Expose as an Add-on option: "Victron control: EMS-direct / DESS-managed". |

## Feature Dependencies

```
Session Health Validation
    |
    v
Huawei Working Mode Takeover --> Safe Shutdown / Mode Restoration
    |
    v
Cross-Charge Detection --> Cross-Charge Prevention in Coordinator
    |                            |
    v                            v
Cross-Charge Waste Metric    Production Deployment on HA
                                 |
                                 v
                          Operating Mode Investigation
                                 |
                            +---------+
                            |         |
                            v         v
               (if Mode A)           (if Mode B)
        Full EMS Control        VRM DESS Schedule Reading
                                     |
                                     v
                              Dual-Scheduler Coordination
                                     |
                                     v
                              Hub4 Override Coordination
```

**Key dependency chain**: Cross-charge prevention must be in place *before* production deployment. Without it, the system could waste 15-20% of battery throughput on circular energy flows.

**Decision gate**: Operating mode investigation determines whether VRM/DESS integration features are needed. If full EMS control (Mode A) works well, the DESS integration branch can be deferred entirely.

## MVP Recommendation

### Phase 1: Safe Production Control (must have)

1. **Session health validation** -- already partially done, extend with write-back verification
2. **Huawei working mode takeover** -- switch to TOU or constrained self-consumption on startup
3. **Safe shutdown / mode restoration** -- lifespan hook to restore autonomous operation
4. **Cross-charge detection** -- add to coordinator's per-cycle decision logic
5. **Cross-charge prevention** -- enforce mutual exclusion of opposing roles unless grid flow justifies it

### Phase 2: Production Deployment

6. **Production deployment on HA** -- validate full stack on real hardware, tune parameters

### Phase 3: Operating Mode Decision (investigation)

7. **Operating mode investigation** -- run both strategies, measure outcomes, decide on DESS path

### Defer

- **VRM DESS schedule reading**: Only if Mode B (DESS + EMS hybrid) wins the operating mode investigation
- **Dual-scheduler coordination**: Depends on DESS schedule reading
- **Huawei TOU schedule programming**: Complex, fragile, and the current max-power-limit approach works. Only pursue if field testing reveals the Huawei internal EMS fights the power limits excessively
- **Cross-charge waste metric**: Nice to have for validation but not blocking

## Complexity Assessment

| Feature | Estimated Effort | Risk | Confidence |
|---------|-----------------|------|------------|
| Session health validation | 1-2 days | LOW | HIGH -- extending existing patterns |
| Huawei working mode takeover | 2-3 days | MEDIUM | MEDIUM -- need real hardware testing, firmware behavior varies |
| Safe shutdown | 1-2 days | LOW | HIGH -- standard lifespan hook pattern |
| Cross-charge detection | 2-3 days | LOW | HIGH -- pure coordinator logic, no new hardware interaction |
| Cross-charge prevention | 3-5 days | MEDIUM | MEDIUM -- needs careful edge case handling, testing with realistic power profiles |
| Production deployment | 3-5 days | HIGH | LOW -- real hardware always surprises, unit ID probing, network issues |
| Operating mode investigation | 5-7 days | MEDIUM | LOW -- requires running system for multiple days, weather-dependent |
| VRM DESS schedule reading | 3-5 days | MEDIUM | MEDIUM -- D-Bus/MQTT path reading is well-documented but untested |
| Dual-scheduler coordination | 5-7 days | HIGH | LOW -- novel architecture, no reference implementations |

## Sources

- [Victron Dynamic ESS GitHub](https://github.com/victronenergy/dynamic-ess) -- DESS schedule structure, D-Bus paths
- [Victron Dynamic ESS Manual](https://www.victronenergy.com/live/drafts:dynamic_ess) -- SOC targeting approach, override mechanism
- [VRM API Documentation](https://vrm-api-docs.victronenergy.com/) -- Authentication, diagnostics endpoint
- [VRM Dynamic ESS API](https://vrm-dynamic-ess-api.victronenergy.com/docs) -- Schedule endpoint with SOC targets and battery config
- [Huawei SUN2000 Modbus Interface Definitions](https://support.huawei.com/enterprise/de/doc/EDOC1100387581) -- Register 47589 (control level), 47086 (working mode)
- [Huawei TOU Mode Documentation](https://support.huawei.com/enterprise/en/doc/EDOC1100186676/e8d2e6db/tou-time-of-use-mode) -- TOU charge period configuration
- [Victron GX Modbus-TCP Manual](https://www.victronenergy.com/live/ccgx:modbustcp_faq) -- Hub4 control registers
- [Victron Venus OS D-Bus Wiki](https://github.com/victronenergy/venus/wiki/dbus) -- D-Bus service paths for settings
- [Victron Community: DESS Modbus Registers](https://community.victronenergy.com/t/dess-modbus-registers/2154) -- Community discussion on reading DESS state via Modbus
- [Victron Community: Multiplus II and Huawei AC-Coupling](https://community.victronenergy.com/t/multiplus-ii-and-huawei-ac-coupling/25221) -- Real-world AC coupling experience
- Existing codebase: `backend/drivers/huawei_driver.py`, `backend/coordinator.py`, `backend/huawei_controller.py`
