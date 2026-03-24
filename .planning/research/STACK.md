# Stack Research: Production Deployment & Cross-Charge Prevention (v1.4)

**Domain:** VRM API integration, Huawei remote control modes, cross-charge detection
**Researched:** 2026-03-24
**Confidence:** HIGH (VRM API well-documented, huawei-solar already installed, cross-charge is pure coordinator logic)

## Recommended Stack

### Core Technologies (ALREADY INSTALLED -- NO CHANGES)

| Technology | Version | Purpose | Status |
|------------|---------|---------|--------|
| huawei-solar | >=2.5 | Huawei Modbus TCP read/write | Already installed. Already supports `StorageWorkingModesC`, `StorageForcibleChargeDischarge`, TOU period registers, max charge/discharge power writes. |
| pymodbus | >=3.11,<4 | Victron Modbus TCP via Venus OS | Already installed. Victron driver fully functional. |
| httpx | (already dep) | VRM API HTTP client | Already installed. Async-native, used by other integrations. Use for VRM API calls. |
| FastAPI | (already dep) | REST API endpoints | Already installed. New `/api/vrm/*` and `/api/cross-charge/*` endpoints. |

### New Dependencies: NONE

**The entire v1.4 feature set requires zero new pip dependencies.** This is the key finding.

**Rationale:**
- VRM API is a standard REST API -- `httpx` (already installed) handles it perfectly
- Huawei remote control uses registers already supported by `huawei-solar` >= 2.5
- Cross-charge detection is pure Python logic in the coordinator
- No new client libraries, no new protocols, no new runtimes

## VRM API Integration Stack

### Authentication

The VRM API supports two auth methods. Use **Personal Access Tokens** because:
- No login/refresh flow needed (token is long-lived)
- Simpler config: one `VRM_ACCESS_TOKEN` env var
- Header: `X-Authorization: Token <personal-access-token>`
- Created at: `https://vrm.victronenergy.com/access-tokens`

Do NOT use username/password login (requires token refresh, adds state management complexity).

### Key Endpoints

| Endpoint | Method | Purpose | EMS Usage |
|----------|--------|---------|-----------|
| `GET /v2/installations/{id}/dynamic-ess` | GET | Read current DESS schedule | Read Victron's planned charge/discharge schedule per hour |
| `GET /v2/installations/{id}/diagnostics` | GET | Live system state | Validate Victron state matches Modbus readings |
| `GET /v2/installations/{id}/widgets/BatterySummary` | GET | Battery SoC/power | Cross-validate with Modbus SoC |
| `GET /v2/installations/{id}/stats` | GET | Historical stats | Consumption pattern validation |

### Client Architecture

Build a thin `VrmClient` class wrapping `httpx.AsyncClient`:

```python
class VrmClient:
    """Async VRM API client using Personal Access Token auth."""

    BASE_URL = "https://vrmapi.victronenergy.com/v2"

    def __init__(self, access_token: str, site_id: int) -> None:
        self._token = access_token
        self._site_id = site_id
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"X-Authorization": f"Token {access_token}"},
            timeout=15.0,
        )

    async def get_dess_schedule(self) -> DessSchedule: ...
    async def get_dess_config(self) -> DessConfig: ...
    async def get_battery_summary(self) -> BatterySummary: ...
```

**Do NOT use `victron-vrm` (PyPI) or `vrmapi` (official).** Reasons:
- `victron-vrm` (v0.1.11): Third-party, immature (0.x), adds pydantic dependency we don't use elsewhere
- `vrmapi` (official): Synchronous `requests`-based, unmaintained, no async support
- The VRM API is trivially simple REST -- 3 endpoints, one auth header. A ~100-line wrapper with `httpx` is more maintainable than taking on either dependency.

### DESS Schedule Data Model

Based on VRM API documentation and community sources:

```python
@dataclass
class DessScheduleEntry:
    start: int           # Unix timestamp
    duration: int        # Seconds
    target_soc: float    # 0-100%
    allow_grid_feedin: bool
    restrictions: int    # 0=none, 1=no battery export, 2=no grid import
    strategy: int        # 0=target SOC, 1=minimize grid
```

### Rate Limits & Reliability

- VRM API has no published rate limits, but community guidance says "don't inject too often" -- configuration changes trigger recalculation taking up to 10 minutes
- For read-only schedule polling: 5-minute interval is safe and sufficient
- VRM is a cloud API -- this violates the "no cloud for core" constraint. Design as **optional overlay**: coordinator must function without VRM data. VRM provides advisory schedule data only.
- Graceful degradation: if VRM unreachable, log WARNING, continue with local-only control

**Confidence: MEDIUM** -- VRM API endpoint paths for DESS are inferred from community sources; exact response schemas need field validation against a live installation.

## Huawei Remote Control Mode Stack

### Already Available in huawei-solar

The `huawei-solar` library (already at >=2.5) provides everything needed for remote control:

| Register | huawei-solar Name | Purpose | Already Used? |
|----------|-------------------|---------|---------------|
| `storage_working_mode_settings` | `STORAGE_WORKING_MODE_SETTINGS` | Set operating mode (0-5) | YES -- `write_battery_mode()` exists |
| `storage_forcible_charge_discharge_setting_mode` | `STORAGE_FORCIBLE_CHARGE_DISCHARGE_SETTING_MODE` | Force charge/discharge/stop | NO -- new |
| `forcible_charge_discharge_write` | `STORAGE_FORCIBLE_CHARGE_DISCHARGE_WRITE` | Trigger forcible mode | NO -- new |
| `storage_forcible_charge_power` | `STORAGE_FORCIBLE_CHARGE_POWER` | Set forced charge rate | NO -- new |
| `storage_forcible_discharge_power` | `STORAGE_FORCIBLE_DISCHARGE_POWER` | Set forced discharge rate | NO -- new |
| `storage_maximum_charging_power` | `STORAGE_MAXIMUM_CHARGING_POWER` | Max charge limit | YES -- `write_max_charge_power()` exists |
| `storage_maximum_discharging_power` | `STORAGE_MAXIMUM_DISCHARGING_POWER` | Max discharge limit | YES -- `write_max_discharge_power()` exists |
| `storage_charge_from_grid_function` | `STORAGE_CHARGE_FROM_GRID_FUNCTION` | Allow/block grid charging | YES -- `write_ac_charging()` exists |
| `storage_huawei_luna2000_time_of_use_...` | `STORAGE_HUAWEI_LUNA2000_TIME_OF_USE_CHARGING_AND_DISCHARGING_PERIODS` | TOU period config | NO -- new |
| `storage_excess_pv_energy_use_in_tou` | `STORAGE_EXCESS_PV_ENERGY_USE_IN_TOU` | PV surplus handling in TOU | NO -- new |

### StorageWorkingModesC Enum (verified from installed library)

| Mode | Value | Description | EMS Usage |
|------|-------|-------------|-----------|
| `ADAPTIVE` | 0 | Huawei internal AI control | Not useful -- EMS replaces this |
| `FIXED_CHARGE_DISCHARGE` | 1 | Constant charge/discharge | Not useful for dynamic control |
| `MAXIMISE_SELF_CONSUMPTION` | 2 | PV self-consumption priority | **Current default** -- Huawei handles its own optimization |
| `TIME_OF_USE_LG` | 3 | TOU for LG batteries | N/A (LUNA2000 system) |
| `FULLY_FED_TO_GRID` | 4 | All power to grid | Useful for export mode |
| `TIME_OF_USE_LUNA2000` | 5 | TOU for LUNA2000 batteries | **Key mode for EMS remote control** |

### StorageForcibleChargeDischarge Enum (verified)

| Mode | Value | Description |
|------|-------|-------------|
| `STOP` | 0 | Cancel forced operation |
| `CHARGE` | 1 | Force battery charge |
| `DISCHARGE` | 2 | Force battery discharge |

### Control Strategy Options

Two viable approaches for EMS-controlled Huawei:

**Option A: TIME_OF_USE_LUNA2000 mode (Recommended)**
- Set mode to `TIME_OF_USE_LUNA2000` (5)
- Write TOU periods via `STORAGE_HUAWEI_LUNA2000_TIME_OF_USE_CHARGING_AND_DISCHARGING_PERIODS`
- Huawei follows the schedule; EMS updates it dynamically
- Advantage: Huawei's internal BMS still manages cell balancing, thermal limits
- Risk: TOU period register format needs field validation (complex multi-register write)

**Option B: Forcible charge/discharge**
- Keep mode at `MAXIMISE_SELF_CONSUMPTION` (2)
- Override with `STORAGE_FORCIBLE_CHARGE_DISCHARGE_SETTING_MODE` for grid charge windows
- Use `STORAGE_FORCIBLE_CHARGE_POWER` / `STORAGE_FORCIBLE_DISCHARGE_POWER` for rate control
- Advantage: Simpler -- no TOU period parsing
- Risk: Forcible mode has a timeout (needs periodic refresh); less granular than TOU

**Recommendation: Start with Option B (forcible), migrate to Option A (TOU) if needed.**
Option B is simpler, requires fewer untested register writes, and the existing driver already has the charge/discharge power write methods. Three new write methods are needed for forcible mode; TOU periods require complex multi-register struct writes that need careful field testing.

### New Driver Methods Needed

Add to `HuaweiDriver`:

```python
async def write_forcible_mode(self, mode: StorageForcibleChargeDischarge) -> None: ...
async def write_forcible_charge_power(self, watts: int) -> None: ...
async def write_forcible_discharge_power(self, watts: int) -> None: ...
```

Pattern: identical to existing `write_max_charge_power()` -- single `self._client.set()` call with reconnect wrapper.

**Confidence: HIGH** -- enum values verified from installed library, write pattern proven by existing methods.

## Cross-Charge Detection & Prevention

### What Is Cross-Charging?

Cross-charging occurs when one battery system charges while the other discharges, and the discharge energy flows through the grid meter to charge the other battery. This wastes energy through conversion losses (typically 5-10% round-trip per battery) and may incur grid usage charges.

In this setup:
- Huawei discharging (feeding AC bus) while Victron is charging (absorbing from AC bus) = cross-charge
- Victron discharging while Huawei is charging = cross-charge
- Both discharging = fine (both serving load)
- Both charging = fine (both absorbing PV surplus)

### Detection: Pure Coordinator Logic

No new libraries needed. Cross-charge detection uses data already available in `ControllerSnapshot`:

```python
def detect_cross_charge(
    huawei: ControllerSnapshot,
    victron: ControllerSnapshot,
) -> bool:
    """True if one battery charges while the other discharges."""
    h_charging = huawei.battery_power_w > DEAD_BAND_W
    h_discharging = huawei.battery_power_w < -DEAD_BAND_W
    v_charging = victron.battery_power_w > DEAD_BAND_W
    v_discharging = victron.battery_power_w < -DEAD_BAND_W
    return (h_charging and v_discharging) or (h_discharging and v_charging)
```

### Prevention Strategy

Prevention lives in the coordinator's role assignment logic:

1. **Role conflict check**: Before assigning roles, verify no conflicting charge/discharge assignments
2. **Priority-based resolution**: When cross-charge would occur, the battery with higher SoC should discharge, lower SoC should hold (not charge from grid during discharge windows)
3. **Dead-band tolerance**: Small power flows (<50W) are noise from grid meter lag -- ignore
4. **Reactive correction**: If cross-charge detected post-dispatch, immediately set the lower-priority battery to HOLDING

### Metrics for Cross-Charge

Track in InfluxDB (existing `influx_writer.py`):
- `ems_cross_charge_detected` (boolean per cycle)
- `ems_cross_charge_energy_wh` (cumulative wasted energy)
- `ems_cross_charge_corrections` (count of reactive corrections)

No new dependencies -- uses existing InfluxDB write path.

**Confidence: HIGH** -- purely algorithmic, no external dependencies, fits cleanly into existing coordinator pattern.

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| VRM API client | Custom httpx wrapper | `victron-vrm` (PyPI) | 0.x maturity, adds pydantic dep, we need 3 endpoints max |
| VRM API client | Custom httpx wrapper | `vrmapi` (official) | Sync-only (`requests`), unmaintained, no async |
| Huawei control | Forcible charge/discharge | TOU period writes | TOU requires complex multi-register struct; start simple |
| Cross-charge detect | Coordinator logic | Separate service/daemon | Over-engineering; detection is 5 lines in the 5s loop |
| DESS schedule read | VRM REST API | Venus OS D-Bus direct | D-Bus requires local access to Venus OS; VRM API works remotely |

## Installation

```bash
# No new dependencies needed. Verify existing:
pip install -e ".[dev]"
```

## Integration Points with Existing Code

| New Feature | Integrates With | How |
|-------------|----------------|-----|
| VRM client | `backend/integrations/` | New `vrm_client.py`, same pattern as `evcc_client.py` |
| VRM config | `backend/config.py` | New `VrmConfig` dataclass with `from_env()` |
| DESS schedule | `backend/coordinator.py` | Advisory input to role assignment (optional) |
| Huawei forcible mode | `backend/drivers/huawei_driver.py` | 3 new write methods, same pattern |
| Huawei forcible mode | `backend/huawei_controller.py` | New `execute_forcible()` path in command execution |
| Cross-charge detection | `backend/coordinator.py` | New check in dispatch loop, after role assignment |
| Cross-charge metrics | `backend/influx_writer.py` | New measurement `ems_cross_charge` |
| HA Add-on config | `ha-addon/config.yaml` | New options: `vrm_access_token`, `vrm_site_id` |
| HA MQTT entities | `backend/ha_mqtt_client.py` | New sensors: cross-charge status, VRM connection |

## Configuration Additions

```yaml
# ha-addon/config.yaml additions
vrm_access_token: ""     # Personal Access Token from VRM portal (optional)
vrm_site_id: 0           # VRM installation ID (optional)
huawei_control_mode: "self_consumption"  # "self_consumption" | "ems_forcible" | "ems_tou"
```

All three are optional -- system must function without VRM and without Huawei remote control (keeping current `MAXIMISE_SELF_CONSUMPTION` mode).

## Sources

- [VRM API Documentation](https://vrm-api-docs.victronenergy.com/) -- Official endpoint reference
- [VRM API v2 Overview](https://docs.victronenergy.com/vrmapi/overview.html) -- Redirects to above
- [Dynamic ESS GitHub](https://github.com/victronenergy/dynamic-ess) -- DESS implementation, schedule format
- [Dynamic ESS Manual](https://www.victronenergy.com/live/drafts:dynamic_ess) -- DESS architecture and D-Bus paths
- [Node-RED VRM API + DESS](https://communityarchive.victronenergy.com/articles/293324/node-red-vrm-api-and-dynamic-ess-1.html) -- Endpoint usage examples
- [victron-vrm PyPI](https://pypi.org/project/victron-vrm/) -- Evaluated, not recommended
- [huawei-solar-lib GitHub](https://github.com/wlcrs/huawei-solar-lib) -- Source for register definitions
- [Huawei Modbus Interface Definitions](https://support.huawei.com/enterprise/de/doc/EDOC1100387581) -- Official register docs
- Installed `huawei-solar` library -- `StorageWorkingModesC` and `StorageForcibleChargeDischarge` enums verified locally
