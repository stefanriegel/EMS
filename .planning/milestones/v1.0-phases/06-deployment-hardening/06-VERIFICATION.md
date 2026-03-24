---
phase: 06-deployment-hardening
verified: 2026-03-23T11:00:00Z
status: passed
score: 10/10 must-haves verified
gaps: []
human_verification:
  - test: "Multi-arch Docker build (aarch64/amd64)"
    expected: "Both manifest entries build and run correctly"
    why_human: "Requires cross-platform Docker buildx or real aarch64 hardware. Cannot test programmatically in CI."
  - test: "HA Add-on install via Supervisor"
    expected: "Add-on appears in HA store, installs, config.yaml schema is accepted by Supervisor"
    why_human: "Requires running HA Supervisor instance — not reproducible in automated tooling."
  - test: "Supervisor service discovery detects MQTT"
    expected: "With Mosquitto add-on installed, EMS auto-detects MQTT broker via SupervisorClient"
    why_human: "Requires HA Supervisor services API — external service dependency."
  - test: "Setup wizard visual appearance"
    expected: "Dark theme, properly spaced, readable labels, Advanced toggle collapses and expands correctly in browser"
    why_human: "Visual/interactive quality cannot be verified programmatically."
---

# Phase 6: Deployment & Hardening Verification Report

**Phase Goal:** Consolidated Dockerfile, HA Add-on config updates, and setup wizard migration from MQTT to Modbus TCP.
**Verified:** 2026-03-23T11:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|---------|
| 1  | A single Dockerfile at repo root builds a working image for both HA Add-on and local dev | VERIFIED | `Dockerfile` line 1: `FROM node:20-alpine AS frontend-build`, line 10: `ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.21` |
| 2  | No stale duplicate source code exists in ha-addon/ | VERIFIED | `ha-addon/backend/` NOT FOUND, `ha-addon/Dockerfile` NOT FOUND, `ha-addon/pyproject.toml` NOT FOUND, `ha-addon/dist/` NOT FOUND. Directory contains only: config.yaml, build.yaml, run.sh, translations/, DOCS.md, CHANGELOG.md, icon.png, logo.png |
| 3  | config.yaml schema includes Victron Modbus unit IDs, coordinator tuning, and Modul3 tariff fields | VERIFIED | config.yaml lines 38–41: victron_system_unit_id/battery/vebus; lines 47–51: deadband/ramp/min_soc; lines 60–66: modul3 tariff fields. Schema section mirrors all with int?/str? types |
| 4  | run.sh exports all new env vars from options.json to the backend | VERIFIED | run.sh lines 26–31: VICTRON_SYSTEM_UNIT_ID, VICTRON_BATTERY_UNIT_ID, VICTRON_VEBUS_UNIT_ID; lines 87–96: coordinator tuning vars; lines 99–110: MODUL3_* vars |
| 5  | EmsSetupConfig stores Victron Modbus fields (port 502, unit IDs) and Modul3 tariff fields | VERIFIED | setup_config.py lines 56–59: victron_port=502, unit IDs; lines 81–86: modul3 tariff fields |
| 6  | Setup API has a victron_modbus probe that performs a real Modbus TCP register read via pymodbus | VERIFIED | setup_api.py lines 111–138: `_probe_victron_modbus` reads register 843 via `ModbusTcpClient`, TCP-only fallback returns warning dict |
| 7  | SetupCompleteRequest includes all new fields and persists them via EmsSetupConfig | VERIFIED | setup_api.py lines 193–238: SetupCompleteRequest mirrors EmsSetupConfig; line 253: `EmsSetupConfig(**body.model_dump())` wires payload to persistence |
| 8  | VictronConfig.from_env() reads battery_unit_id env var for forward compatibility | VERIFIED | config.py lines 100, 123: `battery_unit_id: int = 225` and `battery_unit_id=int(os.environ.get("VICTRON_BATTERY_UNIT_ID", "225"))` |
| 9  | Step 2 wizard says "Victron Modbus TCP", port defaults to 502, has Advanced toggle with unit IDs, probe calls victron_modbus | VERIFIED | SetupWizard.tsx line 201: `"Victron Modbus TCP"`, line 63: `victron_port: "502"`, lines 207–215: `<details><summary>Advanced: Unit IDs</summary>`, lines 498–502: probe calls `victron_modbus` with unit_id |
| 10 | Step 5 has Modul3 grid-fee fields, CSS classes are defined, finish payload includes all new fields | VERIFIED | SetupWizard.tsx lines 326–335: Modul3 section with 6 fields; index.css line 852+: `.setup-wizard`, `.setup-field`, `.btn--primary`, `.probe-badge--ok`, `.probe-badge--warn`, `.setup-advanced`; handleFinish payload lines 427–446: all new fields present |

**Score:** 10/10 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `Dockerfile` | Consolidated multi-stage Dockerfile (Node.js frontend build + HA base Python runtime) | VERIFIED | Contains `FROM node:20-alpine AS frontend-build`, `ARG BUILD_FROM`, `COPY --from=frontend-build`, `ENTRYPOINT ["/run.sh"]` |
| `ha-addon/config.yaml` | HA Add-on schema with all new fields | VERIFIED | victron_system_unit_id, coordinator tuning, Modul3 fields in both options and schema sections |
| `ha-addon/run.sh` | Options.json to env var bridge with new Victron, coordinator, and Modul3 exports | VERIFIED | All 11 new env var exports present using nonempty-only pattern |
| `backend/setup_config.py` | EmsSetupConfig with Victron Modbus + Modul3 fields | VERIFIED | Contains victron_system_unit_id (line 57), modul3_surplus_start_min (line 81) |
| `backend/setup_api.py` | Modbus TCP probe endpoint and updated SetupCompleteRequest | VERIFIED | Contains `_probe_victron_modbus` (line 111), `victron_modbus` in Literal type (line 148), no `victron_mqtt` references |
| `backend/config.py` | VictronConfig with battery_unit_id | VERIFIED | battery_unit_id field (line 100) and from_env reads VICTRON_BATTERY_UNIT_ID (line 123) |
| `frontend/src/pages/SetupWizard.tsx` | Updated wizard with Modbus TCP Step 2 + Modul3 Step 5 | VERIFIED | Contains "Victron Modbus TCP" (line 201), advanced toggle (line 207), modul3 fields, correct probe endpoint |
| `frontend/src/index.css` | CSS class definitions for setup wizard | VERIFIED | `.setup-wizard` (line 852), `.setup-field` (line 925), `.btn--primary` (line 1007), `.probe-badge--ok` (line 1050), `.probe-badge--warn` (line 1060), `.setup-advanced` (line 981) |
| `frontend/tests/setup-wizard.spec.ts` | E2E tests for wizard changes | VERIFIED | Tests for "Victron Modbus TCP" title, Advanced toggle, victron_modbus probe, Modul3 fields, finish payload |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `ha-addon/config.yaml` victron_system_unit_id | `ha-addon/run.sh` | get_option names match | WIRED | run.sh line 26: `get_option 'victron_system_unit_id'` matches config.yaml option name |
| `ha-addon/run.sh` VICTRON_SYSTEM_UNIT_ID | `backend/config.py VictronConfig.from_env()` | env var names match | WIRED | run.sh exports VICTRON_SYSTEM_UNIT_ID; config.py line 122 reads `os.environ.get("VICTRON_SYSTEM_UNIT_ID", "100")` |
| `Dockerfile` | `ha-addon/build.yaml` | ARG BUILD_FROM default | WIRED | Dockerfile line 10: `ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.21` — build.yaml passes this arg per-arch |
| `backend/setup_api.py _probe_victron_modbus` | `pymodbus.client.ModbusTcpClient` | sync Modbus register read wrapped in asyncio.to_thread | WIRED | setup_api.py lines 124–138: `from pymodbus.client import ModbusTcpClient`, `read_holding_registers(843, count=1, slave=unit_id)`, wrapped via `asyncio.to_thread` (line 166) |
| `backend/setup_api.py` SetupCompleteRequest | `backend/setup_config.py` EmsSetupConfig | body.model_dump() | WIRED | setup_api.py line 253: `EmsSetupConfig(**body.model_dump())` — all fields map 1:1 |
| `SetupWizard.tsx StepVictron probe` | `backend/setup_api.py probe_device` | fetch POST to /api/setup/probe/victron_modbus | WIRED | SetupWizard.tsx line 498: `handleProbe("victron_modbus", {...})` → line 401: `fetch(\`/api/setup/probe/${device}\`)` |
| `SetupWizard.tsx handleFinish payload` | `backend/setup_api.py SetupCompleteRequest` | POST /api/setup/complete with all new fields | WIRED | SetupWizard.tsx lines 427–446: victron_system_unit_id, victron_battery_unit_id, victron_vebus_unit_id, all modul3 fields present in payload |

---

### Data-Flow Trace (Level 4)

Not applicable for this phase — no dynamic data rendering components were added. The Dockerfile, config files, and setup wizard are configuration collection artifacts, not data display components.

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Backend setup tests pass (22 tests) | `uv run python -m pytest tests/test_setup_config.py tests/test_setup_api.py -x -q` | 22 passed, 1 warning | PASS |
| setup_api.py has no victron_mqtt references | `grep -n "victron_mqtt" backend/setup_api.py \| wc -l` | 0 | PASS |
| SetupWizard.tsx has no victron_mqtt references | `grep -n "victron_mqtt" frontend/src/pages/SetupWizard.tsx \| wc -l` | 0 | PASS |
| All phase commits exist in git history | `git log --oneline a8805d4 9492c75 9b60b45 03236d9 1f18cea b7f907a` | All 6 commits verified | PASS |
| ha-addon/ contains only expected files | `ls ha-addon/` | build.yaml, CHANGELOG.md, config.yaml, DOCS.md, icon.png, logo.png, run.sh, translations/ | PASS |
| Stale ha-addon/backend deleted | `ls ha-addon/backend/` | NOT FOUND | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| DEP-01 | 06-01-PLAN.md | HA Add-on as primary deployment target (aarch64 + amd64) | SATISFIED | Consolidated Dockerfile with `ARG BUILD_FROM` for multi-arch support via build.yaml; ha-addon/ cleaned to spec. Cross-platform Docker build needs human verification. |
| DEP-02 | 06-01-PLAN.md, 06-02-PLAN.md | Supervisor service discovery for MQTT, EVCC, InfluxDB | SATISFIED | run.sh delegates MQTT/HA discovery to SupervisorClient (comment at line 40–43); EVCC/InfluxDB override fields present in run.sh; functional validation needs human verification with HA Supervisor. |
| DEP-03 | 06-02-PLAN.md, 06-03-PLAN.md | Setup wizard updated for dual-controller config (Victron Modbus host/port/unit IDs) | SATISFIED | Full stack: EmsSetupConfig, SetupCompleteRequest, VictronConfig.from_env, probe endpoint, SetupWizard.tsx — all migrated to Modbus TCP with unit IDs. |

All 3 phase requirements (DEP-01, DEP-02, DEP-03) are accounted for and satisfied. No orphaned requirements.

---

### Anti-Patterns Found

No blockers or warnings found.

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| — | — | — | — | — |

Scan notes:
- `setup_api.py`: The `EmsSetupConfig(**body.model_dump())` construction is a direct field mirror with no intermediary. Field alignment is enforced by matching dataclass fields — not a stub.
- `SetupWizard.tsx`: The `|| 0` and `|| 0.0` defaults in handleFinish for Modul3 fields are correct — empty string from DEFAULT_VALUES parses as NaN, so 0 is the legitimate fallback for optional numeric fields.
- `run.sh`: Coordinator tuning fields use the nonempty-only export pattern correctly — missing options do not override backend defaults, which is the intended behavior.

---

### Human Verification Required

#### 1. Multi-arch Docker Build

**Test:** `docker buildx build --platform linux/amd64,linux/arm64 .` in repo root
**Expected:** Both architecture manifest entries build successfully; image runs `uvicorn backend.main:app` on port 8000
**Why human:** Requires cross-platform Docker buildx or real aarch64 hardware. Cannot be tested in automated tooling without buildx setup.

#### 2. HA Add-on Install via Supervisor

**Test:** Add the repository URL in HA Add-ons → Repositories, attempt to install EMS add-on
**Expected:** Add-on appears in store, config.yaml schema is accepted by Supervisor, options form renders all new fields (victron unit IDs, coordinator tuning, Modul3)
**Why human:** Requires a running Home Assistant Supervisor instance.

#### 3. Supervisor Service Discovery

**Test:** Install EMS on HA with Mosquitto Broker add-on installed; observe whether MQTT credentials are auto-detected
**Expected:** Backend connects to MQTT without manual MQTT credentials in config.yaml
**Why human:** Requires HA Supervisor services API — external service dependency.

#### 4. Setup Wizard Visual Appearance

**Test:** `cd frontend && npm run dev`, open `http://localhost:5173/setup`
**Expected:** Dark theme, properly spaced, Step 2 shows "Victron Modbus TCP" with port 502, Advanced toggle collapses/expands 3 unit ID fields, Step 5 shows both Octopus Go and Modul3 sections
**Why human:** Visual/interactive quality cannot be verified programmatically.

---

### Gaps Summary

No gaps. All automated checks passed. Phase goal is achieved across all three plans:

- **Plan 01 (Dockerfile + HA Add-on config):** Stale files removed, multi-stage Dockerfile in place, config.yaml and run.sh extended with all 14 new fields.
- **Plan 02 (Backend setup wizard migration):** EmsSetupConfig, SetupCompleteRequest, VictronConfig all updated to Modbus TCP; victron_modbus probe implemented with real pymodbus register read and TCP-only fallback; 22 tests passing.
- **Plan 03 (Frontend wizard):** Step 2 fully migrated to Modbus TCP with Advanced toggle, Step 5 has Modul3 fields, all CSS classes defined, E2E tests cover new functionality.

Four items require human verification: multi-arch Docker build, HA Supervisor integration, MQTT service discovery, and visual wizard appearance. These are runtime integration tests that cannot be automated without dedicated infrastructure.

---

_Verified: 2026-03-23T11:00:00Z_
_Verifier: Claude (gsd-verifier)_
