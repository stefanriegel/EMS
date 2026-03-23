# Roadmap: EMS v2

## Milestones

- ✅ **v1.0 Independent Dual-Battery EMS** - Phases 1-6 (shipped 2026-03-23)
- ✅ **v1.1 Advanced Optimization** - Phases 7-11 (shipped 2026-03-23)
- 🚧 **v1.2 Home Assistant Best Practice Alignment** - Phases 12-15 (in progress)

## Phases

<details>
<summary>v1.0 Independent Dual-Battery EMS (Phases 1-6) - SHIPPED 2026-03-23</summary>

See MILESTONES.md for details.

</details>

<details>
<summary>v1.1 Advanced Optimization (Phases 7-11) - SHIPPED 2026-03-23</summary>

See MILESTONES.md for details.

</details>

### v1.2 Home Assistant Best Practice Alignment

**Milestone Goal:** Make EMS a first-class HA citizen -- proper entity model, controllable via services, accessible via Ingress, and runtime-tunable through HA entities.

- [ ] **Phase 12: Wizard Removal** - Remove setup wizard; Add-on options page becomes sole config surface
- [ ] **Phase 13: MQTT Discovery Overhaul** - Availability, origin metadata, binary sensors, entity naming, and translations
- [ ] **Phase 14: Controllable Entities** - Number, Select, and Button entities with MQTT subscribe infrastructure
- [ ] **Phase 15: HA Ingress Support** - Dashboard accessible in HA sidebar with proper path and auth handling

## Phase Details

### Phase 12: Wizard Removal
**Goal**: Add-on options page is the sole configuration surface with zero wizard code remaining
**Depends on**: Phase 11 (v1.1 complete)
**Requirements**: CFG-01, CFG-02, CFG-03
**Success Criteria** (what must be TRUE):
  1. EMS starts without any setup wizard routes or config layers -- `setup_api.py`, `setup_config.py`, and `SetupWizard.tsx` do not exist
  2. Navigating to `/setup` in the browser shows the dashboard (or redirects), not a wizard page
  3. All runtime configuration is read from Add-on options (`options.json`) with no `ems_config.json` fallback layer
**Plans**: 2 plans
Plans:
- [ ] 12-01-PLAN.md — Backend wizard removal (delete setup_api.py, setup_config.py, clean main.py lifespan)
- [x] 12-02-PLAN.md — Frontend wizard removal (delete SetupWizard.tsx, clean App.tsx routing)

### Phase 13: MQTT Discovery Overhaul
**Goal**: All HA entities follow best practices -- availability, origin metadata, proper naming, correct platforms, and entity categories
**Depends on**: Phase 12
**Requirements**: DISC-01, DISC-02, DISC-03, DISC-04, DISC-05, DISC-06, DISC-07, DISC-08, DISC-09, DISC-10, DISC-11, DISC-12, DISC-13
**Success Criteria** (what must be TRUE):
  1. When EMS goes offline, all entities show "unavailable" in HA (not stale values)
  2. HA device page shows three devices (EMS Huawei, EMS Victron, EMS System) with entities grouped by physical device, and each device links to the EMS dashboard via configuration_url
  3. Entity names in HA are clean (no device name duplication) with diagnostic and config entities properly categorized
  4. `huawei_online`, `victron_online`, `grid_charge_active`, and `export_active` appear as binary sensors (not sensors) with correct device classes
  5. Existing `unique_id` values are preserved -- no HA dashboards or automations break on upgrade
**Plans**: TBD

### Phase 14: Controllable Entities
**Goal**: Users can control EMS parameters and modes directly from HA UI, automations, and scripts
**Depends on**: Phase 13
**Requirements**: CTRL-01, CTRL-02, CTRL-03, CTRL-04, CTRL-05, CTRL-06, CTRL-07, CTRL-08, CTRL-09, CTRL-10, CTRL-11
**Success Criteria** (what must be TRUE):
  1. User can adjust Huawei and Victron min-SoC via HA number sliders and the new value takes effect on the next control cycle
  2. User can switch control mode (AUTO, HOLD, GRID_CHARGE, DISCHARGE_LOCKED) via an HA select entity and see the mode reflected in EMS state
  3. User can press "Force Grid Charge" and "Reset to Auto" button entities in HA and EMS responds within one control cycle
  4. After any command is processed, the entity's state topic reflects the updated value (state echo)
  5. MQTT subscribe infrastructure survives paho reconnects without silent thread crash
**Plans**: TBD

### Phase 15: HA Ingress Support
**Goal**: Dashboard is accessible from the HA sidebar without separate port or URL, while direct access continues to work
**Depends on**: Phase 12
**Requirements**: INGR-01, INGR-02, INGR-03, INGR-04, INGR-05, INGR-06
**Success Criteria** (what must be TRUE):
  1. Clicking the EMS entry in the HA sidebar opens the dashboard with all assets loading correctly
  2. WebSocket state updates work through both Ingress and direct port access simultaneously
  3. Ingress requests bypass JWT authentication (trusted by HA session), while direct port access still requires login
  4. All frontend routes, API calls, and static assets work with relative paths under both access methods
**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 12 -> 13 -> 14 -> 15

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 12. Wizard Removal | v1.2 | 1/2 | In Progress|  |
| 13. MQTT Discovery Overhaul | v1.2 | 0/TBD | Not started | - |
| 14. Controllable Entities | v1.2 | 0/TBD | Not started | - |
| 15. HA Ingress Support | v1.2 | 0/TBD | Not started | - |
