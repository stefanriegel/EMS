# Roadmap: EMS v1.4 Production Deployment & Cross-Charge Prevention

## Overview

This milestone transitions EMS v2 from a tested, packaged system into live production control of both battery systems (Huawei LUNA2000 + Victron MultiPlus-II). The journey starts with establishing hardware ground truth through read-only validation, builds the cross-charge safety net as pure coordinator logic (no hardware dependencies), enables Huawei mode takeover for authoritative control, commissions incrementally with staged rollout, and adds DESS schedule awareness for hybrid operating mode coordination. Every phase delivers a coherent capability that can be verified before proceeding to the next.

## Phases

**Phase Numbering:**
- Integer phases (20, 21, 22, ...): Planned milestone work
- Decimal phases (20.1, 20.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 20: Hardware Validation** - Validate Modbus connectivity, write-back verification, and dry-run infrastructure on real hardware (completed 2026-03-24)
- [x] **Phase 21: Cross-Charge Detection and Prevention** - Detect and prevent battery-to-battery energy transfer through the AC bus (completed 2026-03-24)
- [ ] **Phase 22: Huawei Mode Manager** - Take authoritative TOU mode control of Huawei for real setpoint command
- [ ] **Phase 23: Production Commissioning** - Staged rollout from read-only to dual-battery writes with safety guards
- [ ] **Phase 24: VRM/DESS Integration** - Read DESS schedule and VRM diagnostics for hybrid operating mode coordination

## Phase Details

### Phase 20: Hardware Validation
**Goal**: EMS validates real hardware connectivity and write safety before any production control
**Depends on**: Nothing (first phase of v1.4)
**Requirements**: HWVAL-01, HWVAL-02, HWVAL-03, HWVAL-04
**Success Criteria** (what must be TRUE):
  1. EMS connects to both batteries via Modbus TCP and reads all expected registers without error
  2. EMS performs write-then-read-back verification and reports match/mismatch before trusting any setpoint
  3. All driver write methods accept a dry_run flag that logs intended writes to the decision log without executing them
  4. EMS enforces a configurable read-only validation period (default 48h) per battery before enabling writes
**Plans:** 2/2 plans complete
Plans:
- [x] 20-01-PLAN.md — Driver dry_run flag, connectivity validation, and write-back verification
- [x] 20-02-PLAN.md — HardwareValidationConfig, controller validation period gating, startup wiring

### Phase 21: Cross-Charge Detection and Prevention
**Goal**: Coordinator detects and stops battery-to-battery energy transfer in real time
**Depends on**: Nothing (pure coordinator logic, can proceed in parallel with Phase 20)
**Requirements**: XCHG-01, XCHG-02, XCHG-03, XCHG-04, XCHG-05, XCHG-06
**Success Criteria** (what must be TRUE):
  1. Coordinator detects cross-charging (opposing battery power signs with near-zero grid) within 2 control cycles (10 seconds)
  2. On detection, the charging battery is forced to HOLDING role and the cross-charge episode is logged as a DecisionEntry
  3. False positives are avoided via 2-cycle debounce and 100W minimum threshold
  4. First detection per episode sends a Telegram alert and cumulative waste energy is tracked in InfluxDB
  5. Dashboard displays a cross-charge status indicator showing current state and historical episodes
**Plans:** 3/3 plans complete
Plans:
- [x] 21-01-PLAN.md — CrossChargeDetector module, CoordinatorState extension, TDD tests
- [x] 21-02-PLAN.md — Coordinator wiring, InfluxDB metrics, Telegram alerting, API health
- [x] 21-03-PLAN.md — Frontend cross-charge badge and waste stats dashboard
**UI hint**: yes

### Phase 22: Huawei Mode Manager
**Goal**: EMS takes authoritative control of Huawei by managing TOU working mode lifecycle
**Depends on**: Phase 20
**Requirements**: HCTL-01, HCTL-02, HCTL-03, HCTL-04
**Success Criteria** (what must be TRUE):
  1. EMS switches Huawei to TOU working mode on startup and Huawei accepts external charge/discharge setpoints
  2. EMS restores Huawei to self-consumption mode on shutdown, even after a crash (idempotent recovery)
  3. EMS detects if Huawei reverts from TOU mode and re-applies the mode automatically
  4. Mode transitions clamp power to zero before switching and wait for settle before resuming setpoints
**Plans**: TBD

### Phase 23: Production Commissioning
**Goal**: Both batteries operate under live EMS control with staged rollout and safety guards
**Depends on**: Phase 20, Phase 21, Phase 22
**Requirements**: PROD-01, PROD-02, PROD-03
**Success Criteria** (what must be TRUE):
  1. EMS follows documented staged rollout: read-only then single-battery writes then dual-battery writes, with clear progression criteria at each gate
  2. Shadow mode logs all coordinator decisions and intended writes without executing them, verifiable via the decision log
  3. Victron 45-second emergency zero-write guard fires before the 60-second watchdog timeout, preventing uncontrolled discharge
**Plans**: TBD

### Phase 24: VRM/DESS Integration
**Goal**: EMS reads DESS schedule and VRM diagnostics to coordinate with Victron's autonomous operation
**Depends on**: Phase 20
**Requirements**: DESS-01, DESS-02, DESS-03, DESS-04
**Success Criteria** (what must be TRUE):
  1. VRM client reads battery and system diagnostics via REST API with Personal Access Token authentication
  2. EMS reads the DESS planned charge/discharge schedule from Venus OS MQTT broker
  3. Coordinator avoids issuing Huawei discharge during DESS Victron charge windows (and vice versa)
  4. VRM/DESS integration degrades gracefully when VRM credentials are missing or Venus MQTT is unavailable
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 20 -> 21 -> 22 -> 23 -> 24

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 20. Hardware Validation | 2/2 | Complete    | 2026-03-24 |
| 21. Cross-Charge Detection and Prevention | 3/3 | Complete    | 2026-03-24 |
| 22. Huawei Mode Manager | 0/0 | Not started | - |
| 23. Production Commissioning | 0/0 | Not started | - |
| 24. VRM/DESS Integration | 0/0 | Not started | - |
