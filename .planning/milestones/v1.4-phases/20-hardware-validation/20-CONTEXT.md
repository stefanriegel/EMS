# Phase 20: Hardware Validation - Context

**Gathered:** 2026-03-24
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

EMS validates real hardware connectivity and write safety before any production control. Adds write-back verification, dry-run flag on all write methods, and configurable read-only validation period per battery system.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — pure infrastructure phase. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

Key constraints from research:
- Huawei SDongle only allows 1 Modbus TCP connection — must decide Modbus Proxy vs sole-client
- Huawei power limits are ceilings, not setpoints — validate actual vs commanded deviation
- All driver write methods already exist (write_battery_mode, write_ac_charging, write_max_charge_power, write_max_discharge_power for Huawei; write_ac_power_setpoint for Victron)
- dry_run flag should be added to existing write methods, not as separate methods
- Read-only validation period should be configurable via env var (default 48h)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `HuaweiDriver` (backend/drivers/huawei_driver.py) — 4 write methods using `_with_reconnect` pattern
- `VictronDriver` (backend/drivers/victron_driver.py) — `write_ac_power_setpoint` using pymodbus
- `scripts/probe_huawei.py`, `scripts/probe_victron.py` — existing diagnostic scripts
- Config pattern: dataclass with `from_env()` classmethod

### Established Patterns
- All write methods use `_with_reconnect` wrapper for auto-retry
- `assert self._client is not None` guard at top of each write
- Structured logging: DEBUG for operations, WARNING for failures
- `LifecycleDriver` and `BatteryDriver` protocol contracts

### Integration Points
- `backend/main.py` lifespan — where drivers are constructed and connected
- `HuaweiController` / `VictronController` — consume driver write methods
- `backend/config.py` — where new config dataclass should go

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. Refer to ROADMAP phase description and success criteria.

</specifics>

<deferred>
## Deferred Ideas

None — infrastructure phase.

</deferred>
