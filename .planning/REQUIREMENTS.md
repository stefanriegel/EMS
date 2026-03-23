# Requirements: EMS v1.2

**Defined:** 2026-03-23
**Core Value:** Both battery systems operate independently with zero oscillation to maximize PV self-consumption across the combined 94 kWh pool.

## v1.2 Requirements

Requirements for Home Assistant Best Practice Alignment milestone.

### Config & Setup

- [ ] **CFG-01**: Setup wizard code is removed (backend routes, frontend pages, setup_config.py)
- [ ] **CFG-02**: Add-on options page is the sole configuration surface — no ems_config.json layer
- [ ] **CFG-03**: Frontend `/setup` route removed; direct access shows dashboard or Add-on config redirect

### MQTT Discovery

- [ ] **DISC-01**: All discovery payloads include `origin` metadata (name, sw version)
- [ ] **DISC-02**: Availability topic with LWT — entities show "unavailable" when EMS goes offline
- [ ] **DISC-03**: `expire_after: 120` on all sensor entities as stale-data safety net
- [ ] **DISC-04**: `has_entity_name: True` with shortened entity names (no device name duplication)
- [ ] **DISC-05**: `entity_category` tagging — diagnostic for status/online, config for tunable parameters
- [ ] **DISC-06**: `device_class` and `state_class` audit — all applicable entities have correct classes
- [ ] **DISC-07**: `configuration_url` in device info pointing to EMS dashboard
- [ ] **DISC-08**: `huawei_online` and `victron_online` moved from sensor to binary_sensor with device_class connectivity
- [ ] **DISC-09**: `grid_charge_active` and `export_active` published as binary_sensor with device_class running
- [ ] **DISC-10**: Three HA devices — EMS Huawei, EMS Victron, EMS System — with entities grouped by physical device
- [ ] **DISC-11**: Platform migration cleanup — empty retained payloads to old sensor topics before new binary_sensor publication
- [ ] **DISC-12**: Existing `unique_id` values preserved — no breaking changes for existing HA dashboards/automations
- [ ] **DISC-13**: Add-on `translations/en.yaml` with human-readable names and descriptions for all config options

### Controllable Entities

- [ ] **CTRL-01**: MQTT subscribe infrastructure — EMS listens on command topics for bidirectional control
- [ ] **CTRL-02**: Number entity for Huawei min-SoC (10-100%, step 5, slider mode)
- [ ] **CTRL-03**: Number entity for Victron min-SoC (10-100%, step 5, slider mode)
- [ ] **CTRL-04**: Number entity for Huawei dead-band (50-1000W, step 50, box mode)
- [ ] **CTRL-05**: Number entity for Victron dead-band (50-500W, step 50, box mode)
- [ ] **CTRL-06**: Number entity for ramp rate (100-2000W, step 100, box mode)
- [ ] **CTRL-07**: Select entity for control mode (AUTO, HOLD, GRID_CHARGE, DISCHARGE_LOCKED)
- [ ] **CTRL-08**: Button entity for Force Grid Charge with auto-timeout
- [ ] **CTRL-09**: Button entity for Reset to Auto
- [ ] **CTRL-10**: State echo — after processing a command, publish updated state on state_topic
- [ ] **CTRL-11**: Defensive paho threading — wrap subscribe in try/except, periodic health check for silent thread crash

### Ingress

- [ ] **INGR-01**: `ingress: true` and `ingress_port` in Add-on config.yaml with panel_icon and panel_title
- [ ] **INGR-02**: ASGI IngressMiddleware reading X-Ingress-Path header and setting root_path
- [ ] **INGR-03**: Frontend Vite `base: './'` for relative asset paths
- [ ] **INGR-04**: Dynamic WebSocket URL construction from window.location (works with both direct and Ingress access)
- [ ] **INGR-05**: Auth bypass for Ingress requests — detect X-Ingress-Path header, skip JWT validation
- [ ] **INGR-06**: Dashboard accessible in HA sidebar and via direct port simultaneously

## Future Requirements

### Deferred to v2+

- **DIAG-01**: Diagnostic sensors (uptime, cycle duration, MQTT message count)
- **CUST-01**: Custom HA integration (Python component) for native HA services
- **TRIG-01**: MQTT device triggers for state change events

## Out of Scope

| Feature | Reason |
|---------|--------|
| Custom HA integration (Python) | MQTT discovery provides 90% of the value at 5% of the complexity |
| MQTT device triggers | Wrong semantic model for continuous-state EMS |
| Climate entity | Wrong platform for battery management |
| 50+ granular entities (one per register) | Entity sprawl harms usability |
| Retained state messages | HA docs recommend against it; use expire_after instead |
| Config migration from wizard | No existing users to migrate — clean break |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| CFG-01 | TBD | Pending |
| CFG-02 | TBD | Pending |
| CFG-03 | TBD | Pending |
| DISC-01 | TBD | Pending |
| DISC-02 | TBD | Pending |
| DISC-03 | TBD | Pending |
| DISC-04 | TBD | Pending |
| DISC-05 | TBD | Pending |
| DISC-06 | TBD | Pending |
| DISC-07 | TBD | Pending |
| DISC-08 | TBD | Pending |
| DISC-09 | TBD | Pending |
| DISC-10 | TBD | Pending |
| DISC-11 | TBD | Pending |
| DISC-12 | TBD | Pending |
| DISC-13 | TBD | Pending |
| CTRL-01 | TBD | Pending |
| CTRL-02 | TBD | Pending |
| CTRL-03 | TBD | Pending |
| CTRL-04 | TBD | Pending |
| CTRL-05 | TBD | Pending |
| CTRL-06 | TBD | Pending |
| CTRL-07 | TBD | Pending |
| CTRL-08 | TBD | Pending |
| CTRL-09 | TBD | Pending |
| CTRL-10 | TBD | Pending |
| CTRL-11 | TBD | Pending |
| INGR-01 | TBD | Pending |
| INGR-02 | TBD | Pending |
| INGR-03 | TBD | Pending |
| INGR-04 | TBD | Pending |
| INGR-05 | TBD | Pending |
| INGR-06 | TBD | Pending |

**Coverage:**
- v1.2 requirements: 33 total
- Mapped to phases: 0
- Unmapped: 33

---
*Requirements defined: 2026-03-23*
*Last updated: 2026-03-23 after initial definition*
