# Supervisory EMS Architecture

**Date:** 2026-03-28
**Status:** Approved
**Scope:** Replace the 5s control loop with a supervisory observation + intervention model

## Problem

The EMS runs a 5s control loop that computes setpoints and writes them to both battery systems every cycle. Both the Huawei SUN2000 (via EMMA) and the Victron MultiPlus-II already have their own sub-second self-consumption controllers with independent grid meters at the same connection point.

The EMS is overriding two fast autonomous controllers with a slow polling loop. This causes:

- **Sluggish load tracking** — 5s poll + 2-cycle debounce + ramp limits = 15-25s reaction time
- **Ping-pong oscillation** — both native controllers react to each other's output via their separate meters, and the EMS ramp/hysteresis layers add further delay
- **Unnecessary complexity** — 1,400 LOC coordinator computing per-cycle setpoints for something the native controllers already do sub-second

## Solution

Stop overriding the native controllers. Let them run autonomously for real-time load tracking. The EMS becomes a **supervisory layer** that observes the pool state and intervenes only when the native controllers can't coordinate on their own.

### Core Principle

**Intervene rarely, observe always.**

The EMS writes to the batteries only when a specific trigger condition fires. Between triggers, it does nothing — the native controllers handle sub-second self-consumption independently.

### Failure Mode

If the EMS crashes, both systems continue operating independently in self-consumption mode. No safe-state, no 0W setpoint — just normal operation without pool-level coordination. This is a reliability improvement over the current design where EMS crash → both batteries go to 0W.

## Architecture

```
  Huawei SUN2000               Victron MultiPlus-II
  (native self-consumption)     (ESS Assistant, sub-second)
         |                              |
         |  own grid meter              |  own grid meter
         |                              |
         +----------+     +------------+
                    |     |
              Supervisory EMS
              (observe 5s, intervene on trigger)

  Observations:          Interventions (writes):
  - SoC (both)           - SoC Balancing (soft limits)
  - Battery power        - Cross-Charge Prevention (HOLD)
  - PV power             - Grid Charge Windows (mode switch)
  - Load power           - Min-SoC Guard (HOLD)
```

## Observation Layer

The EMS reads state from both systems at a configurable interval (default 5s).

### Data Sources

| Source | Registers / Endpoints | Interval | Purpose |
|--------|----------------------|----------|---------|
| EMMA (Modbus TCP, 192.168.0.10:502, unit_id=0) | SoC (30368), battery power (30360), PV power (30354), load power (30356) | 5s (configurable) | Huawei state |
| Victron (Modbus TCP, Venus OS) | SoC, battery power, grid power, consumption | 5s (configurable) | Victron state |
| EVCC (HTTP) | Tariff schedule, EV charging state | 60s | Grid-charge windows |

### Derived Metrics

| Metric | Formula | Purpose |
|--------|---------|---------|
| `pool_soc` | `(huawei_soc * 30 + victron_soc * 64) / 94` | Weighted pool-level SoC |
| `soc_delta` | `abs(huawei_soc - victron_soc)` | Trigger for SoC balancing |
| `cross_charge_detected` | One battery discharging while other charges from non-PV source | Trigger for cross-charge prevention |
| `true_consumption` | `emma_load_power + victron_consumption` | Accurate house load (dual-meter formula) |

### What We Stop Reading

- Per-phase power from Victron (ESS Assistant handles phase balancing)
- Huawei working_mode polling
- Per-cycle grid P_target computation

### Configuration

The observation interval is configurable via the HA add-on config page:

```yaml
observation_interval_s:
  name: Observation Interval (seconds)
  description: How often the EMS reads battery state. Lower = faster cross-charge detection, higher = less Modbus traffic.
  default: 5
  type: integer
  range:
    min: 2
    max: 60
```

## Intervention Engine

Four independent interventions, evaluated after each observation. Each is a stateless condition → action rule.

### Priority Order

If multiple interventions trigger simultaneously:

1. **Min-SoC Guard** (safety)
2. **Cross-Charge Prevention** (efficiency)
3. **Grid Charge Window** (tariff optimization)
4. **SoC Balancing** (pool health)

Higher-priority interventions override lower-priority ones. If Min-SoC Guard HOLDs a system, SoC Balancing does not override it.

### Intervention 1: SoC Balancing

**Trigger:** `soc_delta > soc_balance_threshold` (default 10%, configurable)

**Action:** Throttle the higher-SoC system's discharge so the lower-SoC system catches up. The mechanism differs per system because of their different control APIs:
- If Huawei SoC is higher → reduce Huawei `max_discharge_power` to 50% of rated capacity (Huawei accepts a watt ceiling via Modbus)
- If Victron SoC is higher → raise Victron ESS minimum SoC floor to slow its discharge (Victron ESS Assistant respects a min-SoC floor and will stop discharging when SoC reaches it)

**Release:** `soc_delta < soc_balance_threshold - soc_balance_hysteresis` (default hysteresis 5%)

**Effect:** The native controllers still handle real-time load tracking, but the higher-SoC system contributes less, so SoCs converge over minutes/hours.

### Intervention 2: Cross-Charge Prevention

**Trigger:** One battery is discharging while the other is charging from a non-PV source. Detection: battery_power signs are opposite AND total PV power (EMMA register 30354) is below 100W, meaning the charge is coming from grid, not solar.

**Action:** HOLD the system that is charging (set discharge/charge to 0W)

**Release:** Condition clears for 2 consecutive observations (10s debounce at 5s interval)

**Reuses:** Existing `cross_charge.py` detection logic, but triggered by observation rather than per-cycle evaluation.

### Intervention 3: Grid Charge Windows

**Trigger:** Cheap tariff window starts (from EVCC tariff schedule, threshold from scheduler)

**Action:**
- Switch both systems to grid-charge mode with target SoCs from the scheduler
- Huawei: set charge target via TOU configuration
- Victron: set grid-charge enable + target SoC via Modbus
- Per-battery targets based on capacity ratio: Huawei 32% (30/94), Victron 68% (64/94)

**Release:** Tariff window ends → restore autonomous self-consumption mode

### Intervention 4: Min-SoC Guard

**Trigger:** Either battery SoC drops below `min_soc` (default 10%, configurable)

**Action:** HOLD that system (stop discharge, set max_discharge_power to 0W)

**Release:** SoC recovers above `min_soc + min_soc_hysteresis` (default hysteresis 5%)

## Battery States

Replace the current 6-role model with 3 simple states:

| State | Meaning | Native Controller |
|-------|---------|-------------------|
| `AUTONOMOUS` | Running freely in self-consumption mode | Active, no EMS override |
| `HELD` | Discharge disabled by EMS intervention | Paused (0W setpoint) |
| `GRID_CHARGING` | Charging from grid during cheap tariff window | Charging at EMS-specified rate |

## Codebase Changes

### Removed / Replaced

| Module | Current LOC | Change |
|--------|-------------|--------|
| `coordinator.py` | ~1,400 | Replace with ~300 LOC supervisory loop + intervention engine |
| Role model (6 roles) | — | Replace with 3 states: AUTONOMOUS, HELD, GRID_CHARGING |
| Ramp limiting | — | Remove — native controllers handle ramps |
| Hysteresis dead-bands (W-level) | — | Remove — only SoC-level hysteresis for triggers |
| P_target computation | — | Remove — we don't compute setpoints |
| Per-phase Victron setpoints | — | Remove — ESS Assistant handles phase balancing |

### Simplified

| Module | Change |
|--------|--------|
| `huawei_controller.py` | Only writes max_discharge_power (ceiling) and charge targets |
| `victron_controller.py` | Only writes ESS min-SoC and charge enable — no per-phase setpoints |
| `orchestrator.py` | Start observation timer, register interventions |
| `unified_model.py` | Fewer states, simpler PoolStatus |
| `controller_model.py` | 3 states instead of 6 roles, remove BatteryRole enum complexity |

### Unchanged

| Module | Reason |
|--------|--------|
| `scheduler.py` / `weather_scheduler.py` | Still plans grid-charge windows |
| `consumption_forecaster.py` | Still feeds the scheduler |
| `tariff.py` / `evcc_client.py` | Still provides tariff data |
| `influx_writer.py` / `influx_reader.py` | Still logs metrics |
| `notifier.py` / `telegram.py` | Still alerts on issues |
| `api.py` / frontend components | Updated but not removed |
| `auth.py` / `ingress.py` | Unchanged |

### Net Impact

~1,100 LOC removed from coordinator, replaced by ~300 LOC. System gets simpler and faster.

## Frontend Changes

### Updated Components

| Component | Change |
|-----------|--------|
| Role badges | 3 states (Autonomous / Held / Grid Charging) instead of 6 roles |
| Decision log | Replaced by intervention log — only entries when EMS acts |
| Pool overview | Focus on SoC convergence and total pool capacity |

### New UI Elements

- **Intervention history** — timeline of when the EMS intervened and why
- **Native controller status** — indicator that each system is healthy and running autonomously
- **SoC delta indicator** — visual showing balance between the two batteries

### Unchanged Components

- Battery SoC gauges
- Energy flow visualization
- Tariff card
- Forecast card
- EVCC card

## Observability (InfluxDB)

| Measurement | Frequency | Content |
|-------------|-----------|---------|
| `ems_observation` | Every observation cycle (5s) | SoC, power, PV for both systems |
| `ems_intervention` | Only when triggered | Intervention type, target system, action, reason |

This reduces InfluxDB write volume compared to the current per-cycle decision logging.

## Migration Plan

### Phase 1: Measure Baseline

- Measure actual Modbus round-trip times to EMMA and Victron
- Run 24h in READ_ONLY mode, observe native controller behavior without EMS writes
- Quantify the ping-pong effect between the two native controllers

### Phase 2: Build Supervisory Loop

- Implement observation + intervention engine alongside existing coordinator
- Feature flag in add-on config: `control_mode: "supervisory"` vs `"legacy"`
- Both modes share observation data for side-by-side comparison

### Phase 3: Shadow Mode

- Supervisory engine runs and logs what it would do, but does not write
- Compare intervention decisions against legacy coordinator writes
- Validate trigger timing and correctness

### Phase 4: Live Cutover

- Enable supervisory writes, disable legacy coordinator
- 48h monitoring period (reuse commissioning validation concept)
- Instant rollback: switch `control_mode` back to `"legacy"` in add-on config

## Configuration (Add-on Config Page)

New and modified options:

```yaml
control_mode:
  name: Control Mode
  description: "supervisory" lets native controllers run autonomously with EMS guardrails. "legacy" uses the original 5s setpoint loop.
  default: supervisory
  type: list
  options:
    - supervisory
    - legacy

observation_interval_s:
  name: Observation Interval (seconds)
  default: 5
  type: integer
  range:
    min: 2
    max: 60

soc_balance_threshold:
  name: SoC Balance Threshold (%)
  description: SoC difference between batteries that triggers balancing intervention
  default: 10
  type: integer
  range:
    min: 5
    max: 30

soc_balance_hysteresis:
  name: SoC Balance Hysteresis (%)
  description: SoC delta must drop below threshold minus this value to release balancing
  default: 5
  type: integer
  range:
    min: 2
    max: 15

min_soc:
  name: Minimum SoC (%)
  description: Battery discharge stops below this SoC
  default: 10
  type: integer
  range:
    min: 5
    max: 50

min_soc_hysteresis:
  name: Min SoC Hysteresis (%)
  description: SoC must recover above min_soc + this value to resume discharge
  default: 5
  type: integer
  range:
    min: 2
    max: 15
```

## Testing Strategy

- **Unit tests:** Each intervention tested independently with mock observations
- **Integration tests:** Observation loop with simulated Modbus responses
- **Shadow mode validation:** Side-by-side comparison with legacy coordinator
- **Live validation:** 48h monitored period with instant rollback capability
- **Regression:** Existing scheduler/forecaster/tariff tests unchanged
