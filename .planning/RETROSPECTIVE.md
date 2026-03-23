# Retrospective

## Milestone: v1.0 — Independent Dual-Battery EMS

**Shipped:** 2026-03-23
**Phases:** 6 | **Plans:** 16 | **Tasks:** 29
**Timeline:** 5 days (2026-03-19 to 2026-03-23)

### What Was Built
- Victron Modbus TCP driver with batched reads, int16 sign handling, configurable unit IDs
- Two-tier protocol hierarchy (LifecycleDriver + BatteryDriver) with structural conformance tests
- Per-battery controllers (HuaweiController, VictronController) with independent state machines and failure isolation
- Coordinator with SoC-based role assignment, hysteresis, ramp limiting, 2-cycle debounce
- SoC-headroom-weighted PV surplus distribution and solar-aware grid charge reduction
- Time-of-day min-SoC profiles per battery system
- Per-system InfluxDB metrics, 17-entity HA MQTT discovery, decision ring buffer
- React dashboard with dual-battery SoC arcs, 5-node energy flow SVG, decision log, tariff timeline
- Consolidated multi-stage Dockerfile, HA Add-on config extension, setup wizard Modbus TCP migration

### What Worked
- TDD approach on controllers and coordinator — caught edge cases early, prevented integration issues
- Wave-based parallel execution — plans 01 and 02 of each phase ran concurrently without conflicts
- Protocol conformance tests (Phase 1) — validated driver interfaces before building on top of them
- Phase dependency ordering — coordinator built on proven drivers, integrations built on proven coordinator
- Clear requirement traceability — 30/30 requirements mapped and validated

### What Was Inefficient
- Merge conflicts between parallel worktree agents (backend/config.py in Phase 6) — could pre-partition files
- Phase 1 gap closure (01-03) for main.py instantiation fix — should have been caught in 01-01 verification
- Some CSS classes defined but not applied until E2E tests revealed gaps

### Patterns Established
- Independent controller + coordinator pattern for multi-battery dispatch
- Fire-and-forget integration writes (log failures, never block control loop)
- Native HTML details/summary for progressive disclosure (no JS state)
- REST polling hooks with AbortController for non-critical data
- Nonempty-only env var export pattern in run.sh

### Key Lessons
- Per-system hysteresis values differ significantly (Huawei 300-500W vs Victron 100-200W) — always make configurable
- SoC headroom weighting is strictly better than 50/50 or priority-based PV distribution
- Modbus TCP register addresses need firmware-version-specific validation — design for configurability
- Decision transparency (structured WHY logging) is valuable for debugging dispatch behavior

## Cross-Milestone Trends

| Metric | v1.0 |
|--------|------|
| Phases | 6 |
| Plans | 16 |
| Tasks | 29 |
| Duration (days) | 5 |
| Tests at completion | 1,211 |
| Backend LOC | ~11,200 |
| Frontend LOC | ~2,600 |
