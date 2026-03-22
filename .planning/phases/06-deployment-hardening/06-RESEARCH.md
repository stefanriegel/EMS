# Phase 6: Deployment & Hardening - Research

**Researched:** 2026-03-22
**Domain:** HA Add-on packaging, Docker multi-stage builds, setup wizard, service discovery
**Confidence:** HIGH

## Summary

Phase 6 is primarily a packaging and configuration restructure -- not greenfield development. The codebase already has all the runtime components (SupervisorClient, EmsSetupConfig, setup_api.py, VictronConfig, etc.) working. The work is: (1) consolidate the Dockerfile to eliminate stale copies in `ha-addon/`, (2) update the setup wizard from Victron MQTT to Modbus TCP with unit IDs, (3) add Modul3 tariff fields to the wizard, and (4) verify Supervisor service discovery works with the v2 coordinator.

Every file that needs changing already exists and follows established patterns. The risk is low -- this is mechanical extension of existing code, not architectural invention.

**Primary recommendation:** Execute as three focused waves: (1) Dockerfile consolidation + stale file cleanup, (2) Victron Modbus TCP wizard migration + probe replacement, (3) Modul3 tariff wizard + config.yaml schema updates.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Single-source Dockerfile at repo root. Delete `ha-addon/backend/` and `ha-addon/pyproject.toml` -- no more stale copies. The Dockerfile copies from the main `backend/` directory directly.
- **D-02:** Dockerfile builds frontend from source (`npm ci && npm run build`) during image build. Delete committed `ha-addon/dist/` bundle. Multi-stage build: Node.js stage for frontend, Python stage for backend.
- **D-03:** One consolidated Dockerfile replacing both root `Dockerfile` and `ha-addon/Dockerfile`. Uses HA Add-on base image (`ghcr.io/home-assistant/*-base-python:3.12-alpine3.21`) with `build.yaml` `build_from` controlling arch. Dev `docker-compose.yml` uses the same Dockerfile.
- **D-04:** Single `pyproject.toml` at repo root (both copies are already identical). ha-addon copy removed.
- **D-05:** Setup wizard Step 2 renamed from "Victron MQTT" to "Victron Modbus TCP". Fields: `victron_host` (str), `victron_port` (int, default 502). Unit IDs (`victron_system_unit_id` default 100, `victron_battery_unit_id` default 225, `victron_vebus_unit_id` default 227) behind an "Advanced" toggle -- most Venus OS installations use standard IDs.
- **D-06:** Victron probe does a real Modbus TCP register read (system SoC at unit 100) to validate both network reachability and unit ID correctness. Falls back to TCP-only connect check with a warning if the register read fails.
- **D-07:** No automatic config migration. If a saved config has `victron_port: 1883` (old MQTT), the wizard's "Test Connection" will fail, making it obvious the port needs updating.
- **D-08:** `run.sh` exports `VICTRON_UNIT_SYSTEM`, `VICTRON_UNIT_BATTERY`, `VICTRON_UNIT_VEBUS` env vars from `options.json`. `VictronConfig.from_env()` reads from env -- consistent with all other config fields.
- **D-09:** Add Victron Modbus unit IDs to `config.yaml` schema: `victron_system_unit_id: int?`, `victron_battery_unit_id: int?`, `victron_vebus_unit_id: int?` with defaults 100/225/227.
- **D-10:** Change `victron_port` default from 1883 to 502 in `config.yaml` options block.
- **D-11:** Add optional coordinator tuning entries to `config.yaml` schema: `huawei_deadband_w: int?`, `victron_deadband_w: int?`, `ramp_rate_w_per_cycle: int?`, `min_soc_pct_huawei: int?`, `min_soc_pct_victron: int?`. Not in the wizard -- advanced users only via HA Add-on config panel.
- **D-12:** Tariff Step 5 in wizard: Octopus Go rates (existing off-peak/peak rates and time windows) plus Modul3 grid-fee windows (surplus/deficit periods with time frames).
- **D-13:** Add `config.yaml` schema entries for Modul3: `modul3_surplus_start_min: int?`, `modul3_surplus_end_min: int?`, `modul3_deficit_start_min: int?`, `modul3_deficit_end_min: int?`, `modul3_surplus_rate_eur_kwh: str?`, `modul3_deficit_rate_eur_kwh: str?`.
- **D-14:** No coordinator tuning parameters in the setup wizard.
- **D-15:** Min-SoC profiles managed by ML forecaster + scheduler, not manual configuration.
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

### Deferred Ideas (OUT OF SCOPE)
- CI/CD pipeline for HA Add-on builds
- Config migration tool (v1 to v2 auto-migration)
- Dashboard config page (runtime config editing)
- Health check endpoint for Supervisor watchdog (just verify existing works)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DEP-01 | HA Add-on as primary deployment target (aarch64 + amd64) | Consolidated multi-stage Dockerfile with `build.yaml` `build_from` controlling arch-specific base images. Existing pattern verified in `ha-addon/build.yaml`. |
| DEP-02 | Supervisor service discovery for MQTT, EVCC, InfluxDB | `SupervisorClient` already implements all three discoveries. Lifespan in `main.py` already integrates discovery results via `os.environ.setdefault()`. Verification-only work needed. |
| DEP-03 | Setup wizard updated for dual-controller config (Victron Modbus host/port/unit IDs) | Wizard Step 2 migration from MQTT to Modbus TCP, new probe endpoint, unit ID fields with Advanced toggle, Modul3 tariff fields in Step 5. |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Stack**: Python 3.12+ (FastAPI/uvicorn), React 19+ (Vite), TypeScript
- **Deployment**: Must run as HA Add-on (primary) -- Docker container on aarch64/amd64
- **Graceful degradation**: Every external dependency (InfluxDB, EVCC, HA, Telegram) must be optional
- **Naming**: snake_case.py, PascalCase.tsx, camelCase.ts
- **Testing**: pytest (backend), Playwright (frontend E2E), test_*.py, *.spec.ts
- **Git**: Conventional Commits, no AI attribution, real scopes only
- **GSD Workflow**: Use `/gsd:execute-phase` for planned phase work

## Standard Stack

No new libraries needed. This phase extends existing code using existing dependencies.

### Core (already installed)
| Library | Version | Purpose | Status |
|---------|---------|---------|--------|
| pymodbus | 3.11+ | Modbus TCP probe for Victron | Already in pyproject.toml |
| fastapi | latest | Setup API endpoints | Already installed |
| React | 19.2.4 | Setup wizard UI | Already installed |
| Vite | 8.0.1 | Frontend build in Docker | Already installed |
| Playwright | 1.58.2 | E2E tests for wizard | Already installed |

### Docker Base Images
| Image | Purpose | Source |
|-------|---------|--------|
| `ghcr.io/home-assistant/aarch64-base-python:3.12-alpine3.21` | ARM64 runtime | build.yaml |
| `ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.21` | x86_64 runtime | build.yaml |
| `node:20-alpine` (or similar) | Frontend build stage | Multi-stage Dockerfile |

## Architecture Patterns

### Consolidated Dockerfile Structure (Multi-stage)
```dockerfile
# Stage 1: Frontend build
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime (HA Add-on base)
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.21
FROM ${BUILD_FROM}
WORKDIR /app
# System deps (build-base for bcrypt, gfortran+openblas for sklearn)
RUN apk add --no-cache build-base libffi-dev gfortran openblas-dev jq
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY backend/ backend/
COPY --from=frontend-build /app/frontend/dist frontend/dist
COPY ha-addon/run.sh /run.sh
RUN chmod +x /run.sh
EXPOSE 8000
ENTRYPOINT ["/run.sh"]
```

### Config Pipeline Pattern
```
config.yaml (HA UI) --> options.json --> run.sh --> env vars --> from_env() --> dataclass
```
Every new field follows this chain. Pattern is mechanically repeatable.

### Probe Pattern (existing)
```python
# Sync blocking operation wrapped in asyncio.to_thread()
def _probe_victron_modbus(host: str, port: int, unit_id: int) -> bool:
    """Modbus TCP register read probe."""
    from pymodbus.client import ModbusTcpClient
    client = ModbusTcpClient(host, port=port, timeout=5)
    client.connect()
    try:
        result = client.read_holding_registers(843, count=1, slave=unit_id)
        if result.isError():
            raise ConnectionError(f"Register read failed: {result}")
        return True
    finally:
        client.close()
```

### ha-addon File Layout After Cleanup
```
ha-addon/
  config.yaml       # HA Add-on metadata + schema
  build.yaml        # Multi-arch base image selection
  run.sh            # Options.json --> env vars bridge
  translations/
    en.yaml          # English config labels
    de.yaml          # German config labels
  DOCS.md            # User documentation
  CHANGELOG.md       # Release notes
  icon.png           # Add-on icon
  logo.png           # Add-on logo
  .dockerignore      # Build context exclusions
```

Deleted: `ha-addon/backend/` (~27 files), `ha-addon/pyproject.toml`, `ha-addon/dist/` (~4 files), `ha-addon/Dockerfile`.

### Anti-Patterns to Avoid
- **Duplicated source code in ha-addon/**: The entire reason for this phase. Never copy backend/ into ha-addon/ again -- the Dockerfile COPY pulls from repo root.
- **Pre-built frontend bundles in git**: `ha-addon/dist/` gets stale. The multi-stage build compiles from source every time.
- **Exporting empty env vars**: The `run.sh` pattern `[ -n "$value" ] && export VAR="$value"` prevents empty strings from overriding `_require_env()` KeyError detection. New fields must follow this pattern.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Modbus TCP probe | Raw TCP socket check | pymodbus `ModbusTcpClient.read_holding_registers()` | D-06 requires register read for unit ID validation, not just TCP connect |
| Multi-arch Docker builds | Separate Dockerfiles per arch | `build.yaml` `build_from` + `ARG BUILD_FROM` | HA Add-on builder standard mechanism |
| Config schema validation | Custom JSON schema | HA config.yaml `schema:` section with type annotations | HA Supervisor validates automatically |

## Common Pitfalls

### Pitfall 1: env var name mismatch between run.sh and from_env()
**What goes wrong:** D-08 specifies `VICTRON_UNIT_SYSTEM`, `VICTRON_UNIT_BATTERY`, `VICTRON_UNIT_VEBUS` in run.sh, but `VictronConfig.from_env()` currently reads `VICTRON_SYSTEM_UNIT_ID` and `VICTRON_VEBUS_UNIT_ID`. These must match.
**Why it happens:** CONTEXT.md env var names differ from existing code.
**How to avoid:** Either update run.sh to export `VICTRON_SYSTEM_UNIT_ID` / `VICTRON_VEBUS_UNIT_ID` (matching existing code), or update `VictronConfig.from_env()` to read D-08's names. The simpler path: keep existing env var names in `from_env()` and have run.sh export those same names. The CONTEXT.md D-08 names are aspirational -- the code is the source of truth.
**Warning signs:** Victron driver starts with wrong unit IDs (all defaults), ignoring config.yaml values.

### Pitfall 2: battery_unit_id doesn't exist in VictronDriver
**What goes wrong:** D-05 and D-09 mention `victron_battery_unit_id` (default 225), but the current `VictronDriver` only uses `system_unit_id` (100) and `vebus_unit_id` (227). There is no battery-specific unit ID in the driver code.
**Why it happens:** The CONTEXT.md decision anticipated a future use case. Venus OS exposes battery registers at unit 225, but the current driver reads battery data from system registers (unit 100).
**How to avoid:** Add the field to config.yaml schema and VictronConfig for forward compatibility, but do not change the driver. The wizard can collect it, run.sh can export it, config.py can store it -- even if the driver doesn't use it yet.
**Warning signs:** N/A -- it's a schema-only addition.

### Pitfall 3: Victron Modbus probe using sync pymodbus in async context
**What goes wrong:** The existing probes use `asyncio.to_thread()` to run sync code. The pymodbus sync `ModbusTcpClient` works for probes but must not be confused with the async `AsyncModbusTcpClient` used by the driver.
**Why it happens:** Probes are one-shot, short-lived -- sync is fine.
**How to avoid:** Use `ModbusTcpClient` (sync) in the probe, wrapped in `asyncio.to_thread()`. Do NOT use `AsyncModbusTcpClient` for probes -- it adds unnecessary complexity.

### Pitfall 4: docker-compose.yml needs build context adjustment
**What goes wrong:** Current root `Dockerfile` is `python:3.12-slim` based. The consolidated Dockerfile uses HA base images. `docker-compose.yml` must still work for dev.
**Why it happens:** HA base images are Alpine-based, not Debian-based like `python:3.12-slim`.
**How to avoid:** The consolidated Dockerfile accepts `BUILD_FROM` as an ARG with a default. `docker-compose.yml` can override it for dev if needed, or simply use the Alpine-based image (which works fine for dev).

### Pitfall 5: Modul3 config.yaml field naming vs existing TariffConfig
**What goes wrong:** D-13 defines Modul3 fields as `modul3_surplus_start_min`, etc. But the existing `TariffConfig.from_env()` uses `Modul3Window` with `start_min`, `end_min`, `rate_eur_kwh`, `tier` -- a list of 4 windows (NT, ST, HT, ST). The D-13 schema simplifies to just surplus/deficit periods.
**Why it happens:** The wizard surfaces a simplified view (2 windows) vs the full 4-window model in code.
**How to avoid:** The wizard collects surplus/deficit periods and rates. Run.sh exports them as env vars. The `TariffConfig.from_env()` either reads these new env vars to override the default 4-window list, or the simplified wizard values are mapped to the existing window model during setup/config loading.

### Pitfall 6: Node.js not in HA base image
**What goes wrong:** HA base images (`*-base-python:3.12-alpine3.21`) do not include Node.js. The frontend build must happen in a separate stage.
**Why it happens:** Multi-stage build required by design.
**How to avoid:** First stage uses `node:20-alpine`, second stage uses HA base. `COPY --from=frontend-build` pulls only the built `dist/` directory.

## Code Examples

### run.sh env var export for new Victron fields
```bash
# --- Victron Modbus unit IDs (optional, defaults in VictronConfig) ---
_victron_sys_unit=$(get_option 'victron_system_unit_id')
[ -n "$_victron_sys_unit" ] && export VICTRON_SYSTEM_UNIT_ID="$_victron_sys_unit"
_victron_bat_unit=$(get_option 'victron_battery_unit_id')
[ -n "$_victron_bat_unit" ] && export VICTRON_BATTERY_UNIT_ID="$_victron_bat_unit"
_victron_vb_unit=$(get_option 'victron_vebus_unit_id')
[ -n "$_victron_vb_unit" ] && export VICTRON_VEBUS_UNIT_ID="$_victron_vb_unit"
```

### EmsSetupConfig extension for Victron Modbus
```python
@dataclass
class EmsSetupConfig:
    # --- Victron Modbus TCP (was MQTT) ---
    victron_host: str = ""
    victron_port: int = 502          # Changed from 1883
    victron_system_unit_id: int = 100
    victron_battery_unit_id: int = 225
    victron_vebus_unit_id: int = 227
    # ... existing fields ...
```

### SetupCompleteRequest extension
```python
class SetupCompleteRequest(BaseModel):
    # --- Victron Modbus TCP ---
    victron_host: str = ""
    victron_port: int = 502          # Changed from 1883
    victron_system_unit_id: int = 100
    victron_battery_unit_id: int = 225
    victron_vebus_unit_id: int = 227
    # ... existing fields ...
```

### Victron Modbus probe endpoint
```python
elif device == "victron_modbus":
    ok = await asyncio.to_thread(
        _probe_victron_modbus, body.host, body.port, body.unit_id
    )
    if not ok:
        return {"ok": False, "error": "Modbus register read failed"}
```

### config.yaml schema additions
```yaml
schema:
  # --- Victron Modbus ---
  victron_host: str
  victron_port: int
  victron_system_unit_id: int?
  victron_battery_unit_id: int?
  victron_vebus_unit_id: int?
  # --- Coordinator tuning (advanced) ---
  huawei_deadband_w: int?
  victron_deadband_w: int?
  ramp_rate_w_per_cycle: int?
  min_soc_pct_huawei: int?
  min_soc_pct_victron: int?
  # --- Modul3 tariff ---
  modul3_surplus_start_min: int?
  modul3_surplus_end_min: int?
  modul3_deficit_start_min: int?
  modul3_deficit_end_min: int?
  modul3_surplus_rate_eur_kwh: str?
  modul3_deficit_rate_eur_kwh: str?
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Victron via MQTT | Victron via Modbus TCP | Phase 1 | Wizard Step 2 must reflect this |
| Duplicate backend/ in ha-addon/ | Single source at repo root | Phase 6 (now) | Dockerfile restructure |
| Pre-built frontend dist/ | Build from source in Docker | Phase 6 (now) | Multi-stage Dockerfile |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ (backend), Playwright 1.58.2 (frontend E2E) |
| Config file | `pyproject.toml` (pytest), `frontend/playwright.config.ts` |
| Quick run command | `cd /Users/mustermann/Documents/coding/ems && python -m pytest tests/ -x -q` |
| Full suite command | `cd /Users/mustermann/Documents/coding/ems && python -m pytest tests/ -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DEP-01 | Dockerfile builds and produces working image | manual | `docker build -f Dockerfile .` | N/A (manual) |
| DEP-02 | Supervisor discovery resolves MQTT, EVCC, InfluxDB | unit | `python -m pytest tests/test_supervisor_client.py -x` | Yes |
| DEP-02 | Lifespan integrates discovery results | unit | `python -m pytest tests/test_main_lifespan.py -x` | Yes |
| DEP-03 | Setup wizard Victron Modbus probe works | unit | `python -m pytest tests/test_setup_api.py -x` | Yes |
| DEP-03 | EmsSetupConfig stores new Victron fields | unit | `python -m pytest tests/test_setup_config.py -x` | Yes |
| DEP-03 | VictronConfig reads unit ID env vars | unit | `python -m pytest tests/test_setup_config.py -x` | Yes |
| DEP-03 | Setup wizard UI shows Modbus TCP fields | E2E | `cd frontend && npx playwright test tests/setup-wizard.spec.ts` | Yes |
| DEP-03 | config.yaml schema accepts new fields | manual | HA Add-on config validation | N/A |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_setup_api.py tests/test_setup_config.py tests/test_supervisor_client.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_setup_api.py` -- needs new test for `victron_modbus` probe device (existing file, add test)
- [ ] `tests/test_setup_config.py` -- needs tests for new Victron Modbus fields + Modul3 fields (existing file, add tests)
- [ ] `frontend/tests/setup-wizard.spec.ts` -- needs tests for Modbus TCP step label, Advanced toggle, Modul3 fields (existing file, add tests)

## Open Questions

1. **D-08 env var naming conflict**
   - What we know: CONTEXT.md says `VICTRON_UNIT_SYSTEM`, existing `VictronConfig.from_env()` reads `VICTRON_SYSTEM_UNIT_ID`.
   - What's unclear: Which naming convention should win?
   - Recommendation: Keep existing `VICTRON_SYSTEM_UNIT_ID` / `VICTRON_VEBUS_UNIT_ID` naming in `from_env()` since it's already deployed. Have `run.sh` export using these same names. The CONTEXT.md intent (D-08) is satisfied either way -- the pipeline config.yaml -> run.sh -> env -> from_env() works regardless of exact name choice.

2. **battery_unit_id (225) not used by VictronDriver**
   - What we know: The driver only uses `system_unit_id` (100) and `vebus_unit_id` (227). Unit 225 is the Venus OS battery monitor unit.
   - What's unclear: Will the driver ever need battery-specific registers?
   - Recommendation: Add the field to VictronConfig for forward compatibility. Store and export it. The driver can start reading it when needed. No driver changes in this phase.

3. **Modul3 wizard field mapping to existing TariffConfig**
   - What we know: D-13 defines simplified surplus/deficit fields. The existing `TariffConfig.from_env()` creates 4 `Modul3Window` objects with NT/ST/HT tiers.
   - What's unclear: How the simplified wizard fields map to the 4-window model.
   - Recommendation: The wizard can expose two time windows (surplus period = NT tier, deficit period = HT tier) with rates. Run.sh exports them. `TariffConfig.from_env()` reads them and builds the window list accordingly. Standard-tarif (ST) windows fill the remaining time automatically.

## Sources

### Primary (HIGH confidence)
- Codebase inspection: `ha-addon/config.yaml`, `ha-addon/build.yaml`, `ha-addon/Dockerfile`, `ha-addon/run.sh`
- Codebase inspection: `backend/config.py`, `backend/setup_api.py`, `backend/setup_config.py`, `backend/supervisor_client.py`
- Codebase inspection: `backend/main.py` (lifespan with Supervisor discovery integration)
- Codebase inspection: `backend/drivers/victron_driver.py` (unit ID usage)
- Codebase inspection: `frontend/src/pages/SetupWizard.tsx` (current wizard steps)
- Codebase inspection: `frontend/tests/setup-wizard.spec.ts` (existing E2E tests)

### Secondary (MEDIUM confidence)
- HA Add-on build system: `build.yaml` `build_from` pattern is standard HA Add-on practice

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries, all existing
- Architecture: HIGH -- extending established patterns (config pipeline, probe pattern, multi-stage Docker)
- Pitfalls: HIGH -- identified from direct code inspection, not speculation

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable domain, no external dependency churn)
