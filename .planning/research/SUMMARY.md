# Research Summary: EMS v2 -- Independent Dual-Battery Control

**Domain:** Residential energy management system with dual-battery dispatch
**Researched:** 2026-03-22
**Overall confidence:** MEDIUM-HIGH

## Executive Summary

The EMS v2 rewrite from a unified orchestrator to independent per-battery controllers with coordinated dispatch requires surprisingly few technology changes. The existing stack (Python 3.12, FastAPI, pymodbus, React 19) already contains every dependency needed. The primary work is architectural, not technological.

The single most important change is replacing the Victron MQTT driver with a Modbus TCP driver using pymodbus's `AsyncModbusTcpClient`. This eliminates the threading complexity of paho-mqtt callbacks (the current `call_soon_threadsafe` bridge), gives synchronous write acknowledgment (vs MQTT fire-and-forget), and aligns both battery systems on the same protocol. pymodbus 3.12.1 is already installed as a transitive dependency of huawei-solar.

The anti-oscillation system does not require external control libraries. PID controllers are explicitly wrong for this use case -- battery inverters respond to discrete register writes within 1-2 seconds, making integral windup the enemy. Instead, the system needs: (1) per-battery hysteresis dead-bands, (2) ramp-rate limiters on setpoint changes, (3) role-based priority dispatch so both batteries never chase the same load signal, and (4) a coordinator that arbitrates total demand allocation.

The frontend dashboard needs per-battery visibility but the technology stack (React 19, Vite 8, TypeScript 5.9) is already current. A charting library (recharts) may be needed for time-series visualization but that decision can be deferred.

## Key Findings

**Stack:** No new pip dependencies required. pymodbus (already installed) provides the Victron Modbus TCP client. All anti-oscillation logic is pure Python.

**Architecture:** Replace unified orchestrator with Coordinator + 2 BatteryControllers. Each controller owns its driver, setpoint logic, and failure handling. The coordinator allocates demand but never directly writes to hardware.

**Critical pitfall:** Victron Modbus TCP unit IDs vary by installation. The driver MUST use configurable unit IDs, not hardcoded values. Testing against the actual Venus OS firmware version is essential before going live.

## Implications for Roadmap

Based on research, suggested phase structure:

1. **Victron Modbus TCP Driver** -- Build the new driver first, test against real hardware
   - Addresses: Protocol switch from MQTT to Modbus TCP
   - Avoids: Deploying coordinator logic before the foundation is solid
   - This is the highest-risk component (hardware-dependent, register addresses need verification)

2. **Per-Battery Controller + Coordinator Pattern** -- Core architecture rewrite
   - Addresses: Independent control paths, anti-oscillation, role-based dispatch
   - Avoids: Big-bang rewrite by keeping the old orchestrator running until controllers are proven
   - Implement hysteresis, ramp limiting, role assignment here

3. **Dashboard Rewrite** -- Per-battery visibility
   - Addresses: Per-system metrics, decision transparency, role visualization
   - Avoids: Building UI before the data model is stable

4. **Integration and Hardening** -- InfluxDB per-battery metrics, nightly scheduler, failure isolation
   - Addresses: Per-system metrics, alerting, scheduled charging with per-battery targets
   - Avoids: Optimizing before core dispatch works

**Phase ordering rationale:**
- Phase 1 must come first because the Victron Modbus driver is the foundation for everything else and has the highest hardware verification risk
- Phase 2 depends on Phase 1 (needs working Victron Modbus driver)
- Phase 3 can partially overlap with Phase 2 once the data model is stable
- Phase 4 is polish/hardening that depends on Phase 2 being functional

**Research flags for phases:**
- Phase 1: Needs hardware verification -- Victron Modbus register addresses and unit IDs must be confirmed against the actual Venus OS GX device
- Phase 2: Standard patterns, unlikely to need additional research
- Phase 3: Standard React patterns, unlikely to need research
- Phase 4: Standard InfluxDB patterns, unlikely to need research

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | No new dependencies, all versions confirmed from lockfile |
| Features | MEDIUM-HIGH | Feature set is well-defined in PROJECT.md; anti-oscillation patterns are standard |
| Architecture | MEDIUM-HIGH | Coordinator pattern is well-established; Victron register mapping needs verification |
| Pitfalls | MEDIUM | Victron Modbus specifics need hardware testing; anti-oscillation tuning is empirical |

## Gaps to Address

- Victron Venus OS Modbus TCP register addresses need verification against actual firmware (Venus OS v3.20+). WebSearch was unavailable during research; register list is based on training data knowledge of the CCGX register spreadsheet.
- Exact unit ID assignments for the specific Victron installation need probing at startup or manual configuration
- Ramp rate and dead-band tuning values (50W dead-band, 500W/s ramp) are starting points -- will need empirical tuning against real hardware
- recharts decision deferred until dashboard scope is clearer
