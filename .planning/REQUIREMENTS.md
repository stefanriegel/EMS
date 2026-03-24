# Requirements: EMS v2

**Defined:** 2026-03-24
**Core Value:** Both battery systems operate independently with zero oscillation to maximize PV self-consumption across the combined 94 kWh pool.

## v1.4 Requirements

Requirements for Production Deployment & Cross-Charge Prevention. Each maps to roadmap phases.

### Hardware Validation

- [x] **HWVAL-01**: EMS validates Modbus read connectivity to both batteries before attempting any writes
- [x] **HWVAL-02**: EMS performs write-back verification (write value, read back, confirm match) before trusting setpoint control
- [x] **HWVAL-03**: All write methods support a `dry_run` flag that logs intended writes without executing them
- [ ] **HWVAL-04**: EMS runs 48h read-only validation phase before enabling writes on each battery system

### Huawei Control

- [ ] **HCTL-01**: EMS switches Huawei to TOU working mode (register 47086) on startup for authoritative charge/discharge control
- [ ] **HCTL-02**: EMS restores Huawei to self-consumption mode on shutdown (idempotent, handles crash recovery)
- [ ] **HCTL-03**: EMS periodically verifies Huawei is still in TOU mode and re-applies if reverted
- [ ] **HCTL-04**: Mode transitions clamp power to zero before switching and wait for settle before resuming setpoints

### Cross-Charge Prevention

- [ ] **XCHG-01**: Coordinator detects cross-charging (opposing battery power signs + near-zero grid) within 2 control cycles
- [ ] **XCHG-02**: On detection, coordinator forces the charging battery to HOLDING role to stop energy transfer
- [ ] **XCHG-03**: Cross-charge detection uses 2-cycle debounce and 100W minimum threshold to avoid false positives
- [ ] **XCHG-04**: First detection per episode triggers Telegram alert
- [ ] **XCHG-05**: Cumulative cross-charge waste energy tracked in InfluxDB
- [ ] **XCHG-06**: Dashboard displays cross-charge status indicator

### VRM/DESS Integration

- [ ] **DESS-01**: VRM client reads battery/system diagnostics via REST API with Personal Access Token auth
- [ ] **DESS-02**: EMS reads DESS planned charge/discharge schedule from Venus OS MQTT broker
- [ ] **DESS-03**: Coordinator avoids issuing Huawei discharge during DESS Victron charge windows (and vice versa)
- [ ] **DESS-04**: VRM/DESS integration degrades gracefully when VRM credentials missing or Venus MQTT unavailable

### Production Commissioning

- [ ] **PROD-01**: Staged rollout: read-only -> single-battery writes -> dual-battery writes with documented progression criteria
- [ ] **PROD-02**: Shadow mode logs all coordinator decisions and intended writes without executing them
- [ ] **PROD-03**: Victron 45s emergency zero-write guard prevents 60s watchdog timeout from causing uncontrolled state

## Future Requirements

### Huawei TOU Schedule Programming

- **HCTL-05**: EMS programs Huawei TOU charge/discharge periods via multi-register writes
- **HCTL-06**: TOU schedule synced with tariff engine for optimal charging windows

### Full EMS Victron Control

- **VCTL-01**: EMS takes full Mode 3 setpoint control of Victron (replacing DESS)
- **VCTL-02**: Operating mode A/B comparison analysis with multi-day dataset collection

## Out of Scope

| Feature | Reason |
|---------|--------|
| VRM cloud API for schedule writes | Violates local-only constraint |
| Bidirectional DESS schedule manipulation | Creates dual-controller oscillation |
| Register 47589 remote control mode | Disables ALL Huawei internal safety — no fallback on EMS crash |
| Real-time cross-inverter AC power balancing | Different response times (Huawei ~2s, Victron ~0.5s) guarantee oscillation |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| HWVAL-01 | Phase 20 | Complete |
| HWVAL-02 | Phase 20 | Complete |
| HWVAL-03 | Phase 20 | Complete |
| HWVAL-04 | Phase 20 | Pending |
| HCTL-01 | Phase 22 | Pending |
| HCTL-02 | Phase 22 | Pending |
| HCTL-03 | Phase 22 | Pending |
| HCTL-04 | Phase 22 | Pending |
| XCHG-01 | Phase 21 | Pending |
| XCHG-02 | Phase 21 | Pending |
| XCHG-03 | Phase 21 | Pending |
| XCHG-04 | Phase 21 | Pending |
| XCHG-05 | Phase 21 | Pending |
| XCHG-06 | Phase 21 | Pending |
| DESS-01 | Phase 24 | Pending |
| DESS-02 | Phase 24 | Pending |
| DESS-03 | Phase 24 | Pending |
| DESS-04 | Phase 24 | Pending |
| PROD-01 | Phase 23 | Pending |
| PROD-02 | Phase 23 | Pending |
| PROD-03 | Phase 23 | Pending |

**Coverage:**
- v1.4 requirements: 21 total
- Mapped to phases: 21
- Unmapped: 0

---
*Requirements defined: 2026-03-24*
*Last updated: 2026-03-24 after roadmap creation*
