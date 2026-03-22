# Phase 6: Deployment & Hardening - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

The dual-battery EMS runs as a production HA Add-on with automated service discovery and guided first-run setup. The ha-addon packaging is restructured to use the main codebase directly (no stale copies), the setup wizard is updated for the Victron Modbus TCP protocol, and service discovery covers MQTT, EVCC, and InfluxDB.

Requirements: DEP-01, DEP-02, DEP-03

</domain>

<decisions>
## Implementation Decisions

### ha-addon build restructure (DEP-01)
- **D-01:** Single-source Dockerfile at repo root. Delete `ha-addon/backend/` and `ha-addon/pyproject.toml` — no more stale copies. The Dockerfile copies from the main `backend/` directory directly.
- **D-02:** Dockerfile builds frontend from source (`npm ci && npm run build`) during image build. Delete committed `ha-addon/dist/` bundle. Multi-stage build: Node.js stage for frontend, Python stage for backend.
- **D-03:** One consolidated Dockerfile replacing both root `Dockerfile` and `ha-addon/Dockerfile`. Uses HA Add-on base image (`ghcr.io/home-assistant/*-base-python:3.12-alpine3.21`) with `build.yaml` `build_from` controlling arch. Dev `docker-compose.yml` uses the same Dockerfile.
- **D-04:** Single `pyproject.toml` at repo root (both copies are already identical). ha-addon copy removed.

### Victron protocol migration in wizard (DEP-03)
- **D-05:** Setup wizard Step 2 renamed from "Victron MQTT" to "Victron Modbus TCP". Fields: `victron_host` (str), `victron_port` (int, default 502). Unit IDs (`victron_system_unit_id` default 100, `victron_battery_unit_id` default 225, `victron_vebus_unit_id` default 227) behind an "Advanced" toggle — most Venus OS installations use standard IDs.
- **D-06:** Victron probe does a real Modbus TCP register read (system SoC at unit 100) to validate both network reachability and unit ID correctness. Falls back to TCP-only connect check with a warning if the register read fails.
- **D-07:** No automatic config migration. If a saved config has `victron_port: 1883` (old MQTT), the wizard's "Test Connection" will fail, making it obvious the port needs updating. The label change from "MQTT" to "Modbus TCP" signals the protocol shift.
- **D-08:** `run.sh` exports `VICTRON_UNIT_SYSTEM`, `VICTRON_UNIT_BATTERY`, `VICTRON_UNIT_VEBUS` env vars from `options.json`. `VictronConfig.from_env()` reads from env — consistent with all other config fields.

### config.yaml schema updates (DEP-01, DEP-03)
- **D-09:** Add Victron Modbus unit IDs to `config.yaml` schema: `victron_system_unit_id: int?`, `victron_battery_unit_id: int?`, `victron_vebus_unit_id: int?` with defaults 100/225/227.
- **D-10:** Change `victron_port` default from 1883 to 502 in `config.yaml` options block.
- **D-11:** Add optional coordinator tuning entries to `config.yaml` schema: `huawei_deadband_w: int?`, `victron_deadband_w: int?`, `ramp_rate_w_per_cycle: int?`, `min_soc_pct_huawei: int?`, `min_soc_pct_victron: int?`. Not in the wizard — advanced users only via HA Add-on config panel.

### Tariff configuration (DEP-03)
- **D-12:** Tariff Step 5 in wizard: Octopus Go rates (existing off-peak/peak rates and time windows) plus Modul3 grid-fee windows (surplus/deficit periods with time frames). Both tariff providers already exist in the `CompositeTariffEngine` — the wizard surfaces their configuration.
- **D-13:** Add `config.yaml` schema entries for Modul3: `modul3_surplus_start_min: int?`, `modul3_surplus_end_min: int?`, `modul3_deficit_start_min: int?`, `modul3_deficit_end_min: int?`, `modul3_surplus_rate_eur_kwh: str?`, `modul3_deficit_rate_eur_kwh: str?`.

### Coordinator config approach
- **D-14:** No coordinator tuning parameters in the setup wizard. Wizard stays simple: hardware endpoints, credentials, tariff config, SoC limits. Tuning goes in `config.yaml` for advanced users.
- **D-15:** Min-SoC profiles managed by ML forecaster + scheduler, not manual configuration. The system computes optimal targets based on predicted consumption and solar forecast. Manual override available only via `config.yaml`.

### Service discovery (DEP-02)
- **D-16:** `SupervisorClient` already discovers MQTT, EVCC, and InfluxDB via Supervisor API. Phase 6 verifies this works with the v2 coordinator architecture and updates `run.sh` to use discovered values when config.yaml fields are empty.
- **D-17:** Victron Modbus TCP is NOT discoverable via Supervisor (hardware endpoint, not an HA service). The user must provide host/port in the wizard or config.yaml.

### Claude's Discretion
- Multi-stage Dockerfile layer ordering and caching optimization
- Exact `config.yaml` option ordering and grouping
- Setup wizard step numbering after restructure
- `run.sh` env var export order for new Victron fields
- Probe timeout values for Modbus TCP test
- Frontend build stage Node.js version selection
- Translation file updates (en.yaml, de.yaml) for new config fields

</decisions>

<specifics>
## Specific Ideas

- The restructure should result in deleting the entire `ha-addon/backend/` directory and `ha-addon/dist/` — a significant cleanup. Only HA-specific files remain in `ha-addon/`: config.yaml, build.yaml, run.sh, translations, icons, DOCS.md, CHANGELOG.md, .dockerignore.
- The Victron Modbus probe should feel like the existing Huawei probe — same "Test Connection" button, same inline result badge, but testing Modbus TCP instead of raw TCP socket.
- Modul3 tariff config should mirror the Octopus Go pattern: start/end times in minutes-from-midnight plus rate values.
- The consolidated Dockerfile needs a Node.js stage for `npm run build` — Alpine has `nodejs` and `npm` packages available. Keep the final image clean (no Node.js in runtime).

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### HA Add-on packaging (primary modification targets)
- `ha-addon/config.yaml` — Add-on metadata, schema, options. Update Victron fields, add unit IDs, add coordinator tuning, add Modul3 tariff fields.
- `ha-addon/build.yaml` — Multi-arch base image selection (aarch64/amd64).
- `ha-addon/Dockerfile` — Replace with consolidated Dockerfile at repo root. Multi-stage build.
- `ha-addon/run.sh` — Options.json → env vars bridge. Add Victron unit ID exports, Modul3 tariff exports.
- `ha-addon/translations/en.yaml` — English config labels/descriptions for new fields.
- `ha-addon/translations/de.yaml` — German config labels/descriptions for new fields.

### Root Dockerfile (consolidation target)
- `Dockerfile` — Current dev Dockerfile. Replace with consolidated version.
- `docker-compose.yml` — Dev compose. Update to use consolidated Dockerfile.
- `pyproject.toml` — Single dependency source (keep at root, delete ha-addon copy).

### Setup wizard (protocol migration)
- `backend/setup_api.py` — Probe endpoints. Replace MQTT probe with Modbus TCP probe for Victron.
- `backend/setup_config.py` — `EmsSetupConfig` dataclass. Add Victron unit IDs, Modul3 fields. Change victron_port default to 502.
- `frontend/src/pages/SetupWizard.tsx` — 6-step wizard UI. Update Step 2 (Victron MQTT → Modbus TCP + unit IDs), Step 5 (add Modul3 tariff fields).

### Service discovery (verification)
- `backend/supervisor_client.py` — SupervisorClient with MQTT, EVCC, InfluxDB discovery. Verify integration with v2 coordinator.
- `backend/main.py` — Lifespan wiring. Verify Supervisor discovery feeds into coordinator initialization.

### Configuration
- `backend/config.py` — All config dataclasses with `from_env()`. Verify VictronConfig reads new unit ID env vars. Add Modul3 config fields if not present.
- `backend/drivers/victron_driver.py` — VictronDriver and VictronConfig. Source of truth for unit ID field names and defaults.

### Tariff engine
- `backend/tariff.py` — `CompositeTariffEngine` with Octopus Go + Modul3 providers. Verify Modul3 config is read from env vars.
- `backend/live_tariff.py` — Live Octopus tariff override from HA entity.

### Stale files to delete
- `ha-addon/backend/` — Entire directory (stale v1 copy). ~30 files.
- `ha-addon/pyproject.toml` — Duplicate of root pyproject.toml.
- `ha-addon/dist/` — Pre-built frontend bundle (replaced by build-from-source).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **SupervisorClient** — Already discovers MQTT, EVCC, InfluxDB. Just needs verification with v2 coordinator, not rewriting.
- **EmsSetupConfig** — Flat dataclass with atomic save/load. Extend with new fields, pattern is established.
- **Setup wizard probe pattern** — `POST /api/setup/probe/{device}` with inline result badge. Reuse for Modbus TCP probe.
- **run.sh env var pattern** — `get_option()` + conditional export. Mechanical to extend for new fields.
- **config.yaml schema pattern** — Type annotations with `str?`/`int?` for optional fields. Mechanical to extend.

### Established Patterns
- **Optional-only-if-nonempty exports** — `run.sh` pattern: `[ -n "$value" ] && export VAR="$value"`. Prevents empty strings from overriding defaults.
- **Probe design** — Short-lived connectivity test wrapped in `asyncio.to_thread()`. No persistent state. Returns `{"ok": bool, "error": str?}`.
- **Config fallback chain** — config.yaml options → run.sh env vars → `from_env()` dataclasses → hardcoded defaults. Phase 6 adds coordinator tuning to this chain.

### Integration Points
- **Dockerfile → backend/ + frontend/** — COPY paths change from ha-addon-local to repo-root.
- **config.yaml → run.sh → env vars → from_env()** — Full pipeline from HA UI to Python config.
- **SupervisorClient → main.py lifespan** — Discovery results injected during startup.
- **SetupWizard.tsx → /api/setup/probe → /api/setup/complete** — Frontend wizard to backend persistence.

</code_context>

<deferred>
## Deferred Ideas

- **CI/CD pipeline for HA Add-on builds** — Automated builds on tag push, multi-arch image publishing to GHCR. Not needed for v1 — manual builds sufficient.
- **Config migration tool** — Automatic v1→v2 config migration (detecting old MQTT port, converting fields). Complexity not justified for a small user base.
- **Dashboard config page** — Runtime config editing from the dashboard UI (not just the HA Add-on panel or first-run wizard). v2 feature.
- **Health check endpoint for Supervisor watchdog** — config.yaml already has `watchdog: http://[HOST]:[PORT:8000]/api/health`. Verify it works but no new development needed.

</deferred>

---

*Phase: 06-deployment-hardening*
*Context gathered: 2026-03-22*
