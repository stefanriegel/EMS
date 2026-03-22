# EMS v2 — Independent Dual-Battery Energy Management

## What This Is

A complete rewrite of the Energy Management System that controls two physically and logically separate battery systems (Huawei LUNA2000 via Modbus TCP and Victron MultiPlus-II via Modbus TCP) as independent units with coordinated dispatch. Each system has its own control path, setpoint logic, and failure handling. The system maximizes PV self-consumption, supports dynamic tariff optimization, and runs as a Home Assistant Add-on.

## Core Value

Both battery systems operate independently with zero oscillation — coordinated but never coupled — to maximize PV self-consumption across the combined 94 kWh pool.

## Requirements

### Validated

- ✓ Huawei LUNA2000 driver (Modbus TCP read/write) — existing
- ✓ FastAPI async backend with lifespan management — existing
- ✓ InfluxDB time-series metrics (optional, graceful degradation) — existing
- ✓ Home Assistant Add-on packaging and Supervisor integration — existing
- ✓ Tariff engine (Octopus Go + Modul3 composite) — existing
- ✓ EVCC coordination (HTTP API + MQTT monitoring) — existing
- ✓ Telegram alert notifications — existing
- ✓ HA MQTT discovery and entity publishing — existing
- ✓ ML consumption forecaster (HA SQLite statistics) — existing
- ✓ JWT-based authentication — existing
- ✓ Setup wizard for first-run configuration — existing

### Active

- [ ] Independent control paths per battery system (no unified SoC aggregation)
- [ ] Victron MultiPlus-II control via Modbus TCP (replacing MQTT)
- [ ] Dynamic role assignment (base load, peak shaving, charging) based on SoC/tariff/PV
- [ ] Anti-oscillation: hysteresis, soft-start/soft-stop per battery
- [ ] Coordinated dispatch: total charge/discharge power remains stable
- [ ] Per-system failure isolation (one system down, other unaffected)
- [ ] Nightly charge scheduler with per-battery targets
- [ ] Reworked React dashboard with per-system visibility and decision transparency
- [ ] Per-system metrics and reporting in InfluxDB
- [ ] HA Add-on as primary deployment target
- [ ] Tariff optimization with per-battery dispatch strategy
- [ ] Production-ready alerting and monitoring

### Out of Scope

- Virtual coupling / parallel battery aggregation — fundamentally opposed to the architecture
- Mobile app — web dashboard only
- Cloud connectivity — fully local operation
- Third-party battery brands — Huawei + Victron only for v1
- Victron MQTT control — replaced by Modbus TCP

## Context

**Existing codebase (v1):** The current EMS uses a unified Orchestrator that computes weighted-average SoC across both batteries and dispatches proportional setpoints. This approach causes oscillations when both systems react to the same inputs, and produces suboptimal setpoints because the systems have different characteristics (Huawei: 30 kWh, Victron: 64 kWh).

**Hardware environment:**
- Huawei SUN2000 inverter with LUNA2000 battery (30 kWh) — Modbus TCP on port 502
- Victron MultiPlus-II with Pylontech/similar (64 kWh) — Venus OS GX device
- EVCC for EV charging optimization (co-installed HA add-on)
- Home Assistant OS as the host platform

**Architecture shift:** v1 treats both systems as one pool. v2 gives each system a dedicated controller that receives instructions from a coordinator. The coordinator ensures stability (no fighting) and optimizes the combined output, but each controller makes its own setpoint decisions.

**Victron protocol change:** Switching from MQTT to Modbus TCP for more precise ESS control (direct register writes instead of MQTT topic-based commands).

## Constraints

- **Deployment**: Must run as HA Add-on (primary) — Docker container on aarch64/amd64
- **Hardware**: Huawei Modbus TCP, Victron Modbus TCP (replacing MQTT)
- **Stack**: Python 3.12+ (FastAPI/uvicorn), React 19+ (Vite), TypeScript
- **Network**: Local network only, no cloud dependencies
- **Graceful degradation**: Every external dependency (InfluxDB, EVCC, HA, Telegram) must be optional
- **Safety**: Each battery must enter safe state independently on communication loss

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Fresh rewrite over incremental refactor | Current unified orchestrator architecture is fundamentally incompatible with independent control | — Pending |
| Victron Modbus TCP instead of MQTT | More precise ESS control via direct register writes | — Pending |
| Dynamic roles instead of fixed specialization | SoC/tariff/PV conditions change throughout the day; fixed roles waste capacity | — Pending |
| Independent controllers with coordinator pattern | Prevents oscillation while allowing optimization; each system is autonomous | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-22 after initialization*
