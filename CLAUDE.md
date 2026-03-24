## Project

**EMS v2** — Dual-battery energy management (Huawei LUNA2000 + Victron MultiPlus-II) via Modbus TCP. Maximizes PV self-consumption across 94 kWh pool. Runs as Home Assistant Add-on.

## Commands

```bash
# Backend
pip install -e ".[dev]"                    # install with dev deps
uvicorn backend.main:app --reload --port 8000  # dev server
python -m pytest tests/ -q                  # unit tests
python -m pytest tests/ --cov=backend       # coverage

# Frontend
cd frontend && npm install                  # install deps
npm run dev                                 # dev server :5173 → proxy :8000
npm run build                               # tsc -b && vite build → dist/
npm run lint                                # eslint
npx playwright test                         # E2E tests

# Docker
docker compose up -d                        # InfluxDB + EMS + Grafana
curl http://localhost:8080/api/health        # verify
```

```bash
# Probing & Diagnostics
python scripts/probe_huawei.py              # test Huawei Modbus TCP connection
python scripts/probe_victron.py             # test Victron Modbus TCP connection
python scripts/probe_evcc_mqtt.py           # test EVCC MQTT broker
python scripts/ha_forecast_smoke.py         # smoke-test ML forecast pipeline
```

```bash
# HA Add-on Deployment
scripts/deploy_ha_addon.sh                  # build & push HA Add-on image
scripts/uat_docker.sh                       # run UAT smoke tests in Docker
```

## Architecture

**Entry**: `backend/main.py` (FastAPI lifespan) · `frontend/src/main.tsx` (React SPA)

**Drivers** (`backend/drivers/`): `huawei_driver.py` (Modbus TCP via `huawei-solar`), `victron_driver.py` (Modbus TCP via `pymodbus`) · Protocol contracts in `protocol.py` · Models: `huawei_models.py`, `victron_models.py`

**Controllers**: `huawei_controller.py`, `victron_controller.py` — wrap drivers with failure counting, safe-state, sign-convention translation · Produce `ControllerSnapshot`, consume `ControllerCommand` from `controller_model.py`

**Orchestration**: `orchestrator.py` (5s control loop) · `coordinator.py` (role dispatch) · State: `unified_model.py` (`UnifiedPoolState`, `ControlState` enum) · `controller_model.py` (`BatteryRole`, `PoolStatus`, `CoordinatorState`, `DecisionEntry`)

**Scheduling**: `scheduler.py` + `weather_scheduler.py` (3-day outlook) · `consumption_forecaster.py` (ML via scikit-learn) · `weather_client.py` (Open-Meteo cascade) · Models: `schedule_models.py` (`ChargeSlot`, `ChargeSchedule`, `DayPlan`)

**Tariff**: `tariff.py` (`CompositeTariffEngine` — Octopus Go + Modul 3) · `live_tariff.py` (`LiveOctopusTariff` — HA entity override) · `tariff_models.py` · `export_advisor.py`

**API** (`backend/api.py`): REST + WebSocket (`ws_manager.py`) · Auth: `auth.py` (JWT + bcrypt, `AuthMiddleware`) · Setup wizard: `setup_api.py`, `setup_config.py` · HA Ingress: `ingress.py`

**Integrations**: `evcc_client.py` (HTTP), `evcc_mqtt_driver.py` (MQTT) · `ha_rest_client.py` (`MultiEntityHaClient`), `ha_mqtt_client.py` · `ha_statistics_reader.py` (SQLite) · `notifier.py` (Telegram) · `supervisor_client.py` (HA Supervisor auto-discovery) · `influx_writer.py`, `influx_reader.py` (optional InfluxDB)

**Frontend** (`frontend/src/`): Components — `EnergyFlowCard.tsx`, `BatteryStatus.tsx`, `DeviceDetail.tsx`, `TariffCard.tsx`, `OptimizationCard.tsx`, `ForecastCard.tsx`, `EvccCard.tsx`, `LoadsCard.tsx`, `DecisionLog.tsx`, `PoolOverview.tsx` · Hooks — `useEmsSocket.ts` (WebSocket), `useEmsState.ts` (polling fallback), `useDecisions.ts`, `useForecast.ts` · Pages — `Login.tsx` · Types — `types.ts` · Routing — `wouter` in `App.tsx`

**Config**: `backend/config.py` (dataclass configs with `from_env()` classmethods) · `ha-addon/config.yaml` (HA Add-on options/schema) · `ha-addon/run.sh` (env mapping) · `.env.example`, `docker-compose.yml`

**Deployment**: `Dockerfile` (multi-stage: Node build → Python runtime) · `ha-addon/build.yaml` (aarch64/amd64) · `scripts/deploy_ha_addon.sh` · `grafana/` (dashboards + provisioning)

**CI/CD & Tooling**: `.github/workflows/` (GitHub Actions pipelines) · `.planning/` (project roadmap — `PROJECT.md`, `MILESTONES.md`, phase plans in `phases/`, codebase analysis in `codebase/`, research notes in `research/`) · `.artifacts/browser/` (Playwright browser binaries) · `.bg-shell/manifest.json` (background shell configuration) · `ems.egg-info/` (Python sdist metadata — `PKG-INFO`, `SOURCES.txt`) · `.pytest_cache/` (test runner cache)

## Constraints

- **Graceful degradation**: every external dep (InfluxDB, EVCC, HA, Telegram) must be optional — `None` checks, never crash
- **Safety**: each battery enters safe state independently on comm loss (3 consecutive failures → zero setpoint)
- **No cloud**: local network only, no external API dependencies for core operation
- **HA Add-on**: must run on aarch64/amd64, Docker container, `host_network: true`

## Conventions

- **Python**: `snake_case` for all file and function names, `PascalCase` for all dataclass and enum names, `from __future__ import annotations`, type hints on all signatures, `logger = logging.getLogger(__name__)`, 4-space indent, 88-char lines
- **TypeScript**: `PascalCase.tsx` for components, `camelCase.ts` for hooks and utilities, `interface` (no `I` prefix), `X | null` for nullable
- **Tests**: `tests/test_*.py` with `pytest` + `anyio`, `frontend/tests/*.spec.ts` with Playwright at 375px viewport
- **Config pattern**: dataclass with `@classmethod from_env()` reading `os.environ` via `_require_env()`
- **Error handling**: explicit exceptions (never bare `except:`), fire-and-forget for InfluxDB/Telegram, WARNING log + swallow
- **Imports**: stdlib → third-party → local (blank-line separated), `from backend.xxx import yyy` (absolute)

## Key Files

- `pyproject.toml` — Python deps, pytest config (`asyncio_mode = "auto"`)
- `frontend/package.json` — React 19, Vite 8, Playwright, wouter
- `frontend/vite.config.ts` — dev proxy `/api` → `:8000`
- `frontend/eslint.config.js` — ESLint 9 + typescript-eslint + react-hooks
- `frontend/playwright.config.ts` — baseURL `:4173`, 375×812 viewport
- `ha-addon/translations/en.yaml`, `de.yaml` — HA config UI strings
- `repository.yaml` — HA Add-on Store metadata

## GSD Workflow

Use `/gsd:quick` for small fixes, `/gsd:debug` for investigation, `/gsd:execute-phase` for planned work. Do not make direct repo edits outside GSD unless explicitly asked.

<!-- caliber:managed:pre-commit -->
## Before Committing

Run `caliber refresh` before creating git commits to keep docs in sync with code changes.
After it completes, stage any modified doc files before committing:

```bash
caliber refresh && git add CLAUDE.md .claude/ .cursor/ .github/copilot-instructions.md AGENTS.md CALIBER_LEARNINGS.md 2>/dev/null
```
<!-- /caliber:managed:pre-commit -->

<!-- caliber:managed:learnings -->
## Session Learnings

Read `CALIBER_LEARNINGS.md` for patterns and anti-patterns learned from previous sessions.
These are auto-extracted from real tool usage — treat them as project-specific rules.
<!-- /caliber:managed:learnings -->
