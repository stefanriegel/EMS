<!-- GSD:project-start source:PROJECT.md -->
## Project

**EMS v2 — Independent Dual-Battery Energy Management**

A complete rewrite of the Energy Management System that controls two physically and logically separate battery systems (Huawei LUNA2000 via Modbus TCP and Victron MultiPlus-II via Modbus TCP) as independent units with coordinated dispatch. Each system has its own control path, setpoint logic, and failure handling. The system maximizes PV self-consumption, supports dynamic tariff optimization, and runs as a Home Assistant Add-on.

**Core Value:** Both battery systems operate independently with zero oscillation — coordinated but never coupled — to maximize PV self-consumption across the combined 94 kWh pool.

### Constraints

- **Deployment**: Must run as HA Add-on (primary) — Docker container on aarch64/amd64
- **Hardware**: Huawei Modbus TCP, Victron Modbus TCP (replacing MQTT)
- **Stack**: Python 3.12+ (FastAPI/uvicorn), React 19+ (Vite), TypeScript
- **Network**: Local network only, no cloud dependencies
- **Graceful degradation**: Every external dependency (InfluxDB, EVCC, HA, Telegram) must be optional
- **Safety**: Each battery must enter safe state independently on communication loss
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.12+ - Backend API, drivers, orchestrator, and scheduler logic (`backend/` directory)
- TypeScript 5.9.3 - Frontend dashboard and UI components (`frontend/src/`)
- React 19.2.4 - Frontend framework and component library
- Shell (bash/zsh) - Setup scripts and addon entry points (`scripts/`, `ha-addon/run.sh`)
## Runtime
- FastAPI 0.x (Python async web framework)
- Uvicorn (ASGI server for FastAPI)
- Node.js/npm (frontend build and dev tools)
- Python 3.12+ (CPython)
- `uv` (Python package manager, lockfile: `uv.lock` - 291KB)
- npm (Node.js, lockfile: `package-lock.json`)
- pip (Python packaging via setuptools in `pyproject.toml`)
## Frameworks
- FastAPI - REST API and WebSocket server (`backend/main.py`, `backend/api.py`)
- Uvicorn[standard] - ASGI application server
- React 19.2.4 - Component-based UI framework
- Vite 8.0.1 - Frontend build tool and dev server
- wouter 3.9.0 - Client-side routing (lightweight router, no Next.js)
- TypeScript 5.9.3 - Type-safe JavaScript with JSDoc/TSDoc annotations
- pytest 8+ - Backend unit and async testing (`pyproject.toml` with `asyncio_mode = "auto"`)
- pytest-anyio - Async testing with trio support
- pytest-mock - Mocking and fixture support
- Playwright 1.58.2 - Frontend E2E testing (`frontend/package.json`)
- Vite 8.0.1 - Frontend bundler and dev server
- ESLint 9.39.4 - JavaScript/TypeScript linting (`frontend/`)
- TypeScript compiler (tsc) - Type checking before Vite build
- setuptools 68+ - Python build backend
## Key Dependencies
- huawei-solar 2.5+ - Huawei LUNA2000 inverter/battery driver via Modbus TCP
- pymodbus 3.11 - Modbus protocol implementation
- paho-mqtt 2.1+ - MQTT client for Victron/HA/EVCC integration
- influxdb-client[async] 1.45+ - Time-series database client (optional, see INTEGRATIONS.md)
- httpx - Async HTTP client (EVCC, HA REST, Telegram APIs)
- fastapi - REST/WebSocket framework
- uvicorn[standard] - Production ASGI server with uvloop support
- python-jose[cryptography] - JWT token generation and validation
- passlib[bcrypt] - Password hashing for admin auth
- bcrypt <4 - Bcrypt algorithm implementation
- scikit-learn 1.4 to <2 - ML models for consumption forecasting
- numpy 1.25 to <3 - Numerical computing for time-series analysis
- react 19.2.4 - UI component framework
- react-dom 19.2.4 - React DOM rendering
- wouter 3.9.0 - Lightweight routing (replaces Next.js)
## Configuration
- `HUAWEI_HOST`, `HUAWEI_PORT` - Modbus TCP proxy address (required)
- `VICTRON_HOST`, `VICTRON_PORT` - Venus OS MQTT broker (required)
- `INFLUXDB_URL`, `INFLUXDB_TOKEN` - InfluxDB time-series (optional, skipped if both empty)
- `EVCC_HOST`, `EVCC_PORT` - EVCC API (optional, default 192.168.0.10:7070)
- `EVCC_MQTT_HOST`, `EVCC_MQTT_PORT` - EVCC MQTT broker (optional)
- `HA_URL`, `HA_TOKEN` - Home Assistant REST API (optional)
- `HA_MQTT_HOST`, `HA_MQTT_PORT`, `HA_MQTT_USERNAME`, `HA_MQTT_PASSWORD` - HA MQTT
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - Telegram notifications (optional)
- `SCHEDULER_RUN_HOUR` - Hour to run charge scheduler (default 23)
- `SCHEDULER_CHARGE_START_MIN`, `SCHEDULER_CHARGE_END_MIN` - Charge window bounds
- `OCTOPUS_*` - Octopus Go tariff rates (UK supply, optional)
- `MODUL3_TIMEZONE` - German grid-fee window timezone
- `LOG_LEVEL` - Logging verbosity (INFO, DEBUG, WARNING)
- `EMS_CONFIG_PATH` - Setup config file location (default `/config/options.json` in HA)
- `HA_STAT_OUTDOOR_TEMP_ENTITY`, `HA_STAT_HEAT_PUMP_ENTITY` - HA statistics for ML forecasting
- `.env` - Development environment (present but not read here for security)
- `.env.example` - Example configuration template
- `docker-compose.yml` - Local development with InfluxDB + EMS services
## Build & Deployment
# In frontend/
# Python setuptools
- `Dockerfile` - Single-stage Python 3.12 image, serves both backend (FastAPI) and static frontend
- `docker-compose.yml` - Local dev stack: InfluxDB (2.7) + EMS service
## Platform Requirements
- Python 3.12+
- Node.js 18+ (for npm/Vite)
- MQTT broker (Victron Venus OS MQTT broker)
- Huawei Modbus TCP proxy / SUN2000 dongle
- Docker (via `docker-compose` or Kubernetes)
- Home Assistant Add-on (HA ecosystem integration)
- Optional: InfluxDB 2.x (time-series storage)
- Optional: EVCC energy management system
- Optional: Telegram Bot API access
## Key Design Patterns
- Custom React hooks (`useEmsSocket`, `useEmsState`) for WebSocket and polling fallback
- Wouter for lightweight client-side routing (no SSR, pure SPA)
- Vite dev proxy (`localhost:5173`) forwards `/api/ws/state` to backend in dev mode
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
### Files
- `snake_case.py` — all lowercase with underscores
- Examples: `auth.py`, `config.py`, `unified_model.py`, `ha_statistics_reader.py`
- Test files: `test_*.py` (e.g., `test_auth.py`, `test_unified_model.py`)
- `PascalCase.tsx` for components — e.g., `App.tsx`, `PoolOverview.tsx`, `EnergyFlowCard.tsx`
- `camelCase.ts` for utilities/hooks — e.g., `useEmsSocket.ts`, `useEmsState.ts`, `types.ts`
- Test files: `*.spec.ts` (e.g., `energy-flow.spec.ts`, `login.spec.ts`)
### Functions
- `snake_case` for all functions, async or sync
- Examples: `ensure_jwt_secret()`, `create_token()`, `check_password()`, `_require_env()`
- Private/internal: leading underscore, e.g., `_require_env()`, `_make_state()`
- Factory/builder pattern: `from_env()` classmethod on dataclasses
- `camelCase` for functions
- Examples: `connect()`, `setData()`, `handleFbPool()`, `get_orchestrator()`
- React hooks: `useXxx()` naming convention
- Event handlers: `handleXxx()` or `onXxx` for callbacks
### Variables
- `snake_case` for all module-level and local variables
- Constants: `UPPER_SNAKE_CASE` (rare in codebase, mostly dataclass fields)
- Type hints: Always present for function parameters and returns
- Examples: `retryCountRef`, `unmountedRef`, `config_dir`, `charge_slots`
- `camelCase` for all variables and refs
- Refs: `xxxRef` suffix convention
- State setters: `setXxx` convention (React pattern)
- Optional fields: `xxx | null` or `xxx | undefined`
### Types
- Enum names: `PascalCase` (class name) with `UPPER_SNAKE_CASE` members
- Dataclass names: `PascalCase`
- Type annotations: Use `from __future__ import annotations` at module top for forward references
- Union types: `X | Y` syntax (Python 3.10+)
- Interface names: `PascalCase`, no `I` prefix
- Type aliases: `PascalCase`
- Nullable: `X | null` (explicit null, not `undefined` for model types)
## Code Style
### Formatting
- Line length: 88 characters (implicit; no enforced config found, but code follows it)
- Indentation: 4 spaces
- Imports organized in groups (standard library, third-party, local) separated by blank lines
- ESLint config: `frontend/eslint.config.js`
- Rules: recommended, React hooks, React refresh
- No Prettier config found; formatting is code-style only
- Import order: React/external first, then relative imports
### Linting
- No explicit linter config in `pyproject.toml`; code follows PEP 8 conventions
- Docstring style: NumPy-style docstrings (Google-compatible)
- ESLint enabled: `frontend/eslint.config.js` with `@eslint/js`, `typescript-eslint`
- Run: `npm run lint` in frontend directory
- Config extends: `js.configs.recommended`, `tseslint.configs.recommended`, `reactHooks.configs.flat.recommended`
- No auto-fixing; developers fix manually before commit
## Import Organization
### Order
### Path Aliases
- No path aliases configured; all imports are relative
- Pattern: `./` for siblings, `../` for parent directories
- No aliases; always use `from backend.xxx import yyy` for absolute imports
## Error Handling
### Patterns
- Explicit exception catching, never bare `except:`
- Return `None` or a falsy value rather than raising when error is handled gracefully
- Use `logging` for non-fatal issues (warnings, debug info)
- Try-catch for JSON parsing and async operations
- Graceful degradation: Components render null or fallback UI on error
### Logging
- Module-level logger: `logger = logging.getLogger(__name__)`
- Log levels: `logger.debug()`, `logger.info()`, `logger.warning()`
- Always include relevant context in log messages
- Console logging for observability and debugging
- Pattern: `console.log("[HookName] message")` with timestamp context
## Comments
### When to Comment
- **Why, not what**: Comments explain *intent* and *non-obvious decisions*, not what the code does
- Module docstrings: Always present, describe purpose and public API
- Function docstrings: Always present for public functions, rarely for private helpers
- Inline comments: Used sparingly for complex logic or surprising behavior
### JSDoc/TSDoc
- React component JSDoc: Block comment above component function describing inputs and behavior
- NumPy/Google style with sections: Parameters, Returns, Raises
## Function Design
### Size
- Prefer smaller functions (< 30 lines) focused on a single responsibility
- Complex logic broken into helper functions with descriptive names
- Examples:
### Parameters
- **Python:** Use dataclass configs for large parameter sets
- **TypeScript:** Destructured object parameters for component props
### Return Values
- Explicit return types on all functions
- Dataclass instances for complex returns: `UnifiedPoolState`, `AdminConfig`
- `None` for optional returns: `EvccConfig | None`, `ChargeSchedule | None`
- Boolean for predicates: `check_password() -> bool`, `verify_token() -> bool`
- Explicit types for components and hooks
- Hooks return objects with state and setters
- Components implicitly return JSX or JSX | null
## Module Design
### Exports
- No barrel files; each module exports its main class/function
- Example structure:
- No barrel files; imports are always direct
- Hooks exported directly from hook files
### Organization Pattern
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- **Hardware Polling:** Concurrent async drivers read Huawei (Modbus TCP) and Victron (MQTT) state on every control cycle
- **Unified Orchestration:** A single `Orchestrator` computes SoC-balanced setpoints and dispatches them to both systems
- **Web API & UI:** FastAPI HTTP endpoints expose orchestrator state; a React frontend consumes updates via WebSocket or polling fallback
- **Async-first:** All I/O operations are async (FastAPI, paho-mqtt, httpx, InfluxDB)
- **Graceful degradation:** One driver failure keeps the other running; both offline transitions to HOLD state
- **Decoupled dependencies:** Drivers, tariff engines, and schedulers are injected into Orchestrator; tests mock them easily
- **Lifespan-managed:** FastAPI's lifespan context manager wires up all services at startup and tears them down at shutdown
## Layers
- Purpose: Translate low-level hardware protocols to typed Python dataclasses
- Location: `backend/drivers/`
- Contains: `HuaweiDriver` (Modbus TCP), `VictronDriver` (MQTT), model dataclasses
- Depends on: External hardware, `huawei-solar` library, `paho-mqtt`
- Used by: Orchestrator, tests
- Purpose: Poll drivers, compute unified SoC, compute setpoints, apply debounce/hysteresis, handle failures
- Location: `backend/orchestrator.py`
- Contains: `Orchestrator` class (main control loop)
- Depends on: Both drivers, tariff engine, scheduler, notifier, metrics writer
- Used by: API layer, FastAPI lifespan
- Purpose: Compute nightly charge schedules based on EVCC state, tariff rates, and consumption forecasts
- Location: `backend/scheduler.py`, `backend/consumption_forecaster.py`
- Contains: `Scheduler`, `ConsumptionForecaster` (optional ML models)
- Depends on: EVCC client, tariff engine, InfluxDB metrics reader, Home Assistant statistics
- Used by: Orchestrator (for GRID_CHARGE slot detection), API
- Purpose: Provide electricity pricing at any instant and full-day slot schedules
- Location: `backend/tariff.py`, `backend/live_tariff.py`
- Contains: `CompositeTariffEngine` (multiple provider backends), `LiveOctopusTariff` (live price override)
- Depends on: Tariff configuration, optional Home Assistant REST client
- Used by: Scheduler, Orchestrator, API
- Purpose: Load and persist runtime config from environment, wizard, or Supervisor service discovery
- Location: `backend/config.py`, `backend/setup_config.py`, `backend/setup_api.py`
- Contains: Dataclass configs for all subsystems, setup wizard routes
- Depends on: Environment variables, persistent JSON file
- Used by: All layers during lifespan
- Purpose: Expose orchestrator state, config, tariff data, and charge schedule via REST and real-time updates
- Location: `backend/api.py`, `backend/ws_manager.py`
- Contains: FastAPI routes, WebSocket connection manager
- Depends on: Orchestrator, scheduler, tariff engine, dependency injection
- Used by: Frontend, external consumers
- Purpose: Time-series metrics, charge schedules, and historical consumption data
- Location: `backend/influx_writer.py`, `backend/influx_reader.py`
- Contains: Async InfluxDB client wrappers
- Depends on: InfluxDB instance (optional)
- Used by: Orchestrator (metrics), Scheduler (consumption history)
- Purpose: Connect to EVCC, Home Assistant, Telegram for alerts and coordination
- Location: `backend/evcc_client.py`, `backend/evcc_mqtt_driver.py`, `backend/ha_rest_client.py`, `backend/notifier.py`
- Contains: HTTP clients and MQTT drivers for downstream systems
- Depends on: External services (optional)
- Used by: Orchestrator, Scheduler, API
- Purpose: Real-time dashboard displaying pool state, tariff, device details, and system health
- Location: `frontend/src/`
- Contains: Components, hooks, pages
- Depends on: WebSocket and HTTP APIs
- Used by: End users, Home Assistant Lovelace embedding
## Data Flow
- Orchestrator state is the single source of truth; API is read-only for state
- Config changes via `POST /api/config` update `SystemConfig` and are applied on next cycle
- Persistent config lives in `ems_config.json` (wizard) or `JWT_SECRET` file
- Setup status (`setup_complete` flag) gates access to `/setup` vs `/` routes
## Key Abstractions
- Purpose: Snapshot of the combined battery pool state
- Examples: `backend/unified_model.py`
- Pattern: Dataclass with computed properties (weighted SoC, total power, min/max limits)
- Produced by: Orchestrator on every control cycle
- Consumed by: API, WebSocket, tests
- Purpose: Enumeration of orchestrator operating modes
- Pattern: String-backed enum for JSON serialization
- Values: IDLE, DISCHARGE, CHARGE, HOLD, GRID_CHARGE, DISCHARGE_LOCKED
- Used by: State machine logic, API responses, frontend rendering
- Purpose: Common shape for Huawei and Victron drivers
- Pattern: Both drivers implement `async connect()`, `async close()`, async read methods, write methods
- Sentinel values: Zeroed dataclasses returned when drivers are offline
- Graceful fallback: Orchestrator continues with partial data if one driver fails
- Purpose: Type-safe configuration from environment variables
- Examples: `HuaweiConfig`, `VictronConfig`, `SystemConfig`, `OrchestratorConfig`, etc.
- Pattern: Dataclass with `@classmethod from_env()` that reads `os.environ` and uses `_require_env()` for validation
- Separation: Hardware configs are read on startup; system/orchestrator configs can be updated via API
- Purpose: Next-day charging plan with time-based slot windows
- Location: `backend/schedule_models.py`
- Contains: List of `ChargeSlot` (start/end times, target SoC, reasoning)
- Pattern: Immutable dataclass produced nightly, readable via `/api/optimization/schedule`
## Entry Points
- Location: `backend/main.py`
- Triggers: `uvicorn backend.main:app` or Docker ENTRYPOINT
- Responsibilities: Lifespan wiring, driver connections, orchestrator startup, API/SPA mounting
- Location: `frontend/src/main.tsx`
- Triggers: Browser load of `/` or `/setup`
- Responsibilities: Route selection (setup wizard vs. dashboard), WebSocket connection, UI rendering
- Location: `backend/main.py` (`_nightly_scheduler_loop`)
- Triggers: 04:00 local time (configurable via `SCHEDULER_RUN_HOUR`)
- Responsibilities: Compute next-day charge schedule, retrain ML models, record metrics
- Location: `backend/setup_api.py`
- Triggers: `GET /setup/status` or `POST /setup/save-config`
- Responsibilities: First-run config collection, persistent config storage, setup completion
## Error Handling
- **Driver Offline:** If one driver fails to connect or stalls, the other continues. Both offline for > `max_offline_s` transitions pool to HOLD.
- **Config Missing:** If required env vars are absent, lifespan raises `KeyError`, caught as setup-only mode. API returns 503 for state endpoints until config is provided.
- **API Errors:** Validation errors return 422 (Pydantic default); missing dependencies (e.g., no scheduler) return 503.
- **EVCC Unreachable:** Scheduler uses stale schedule or fallback consumption estimate; metrics are still written.
- **InfluxDB Disabled:** Metrics writer and reader are `None`; orchestrator continues; scheduler can fall back to HA SQLite.
- **Telegram Optional:** If token/chat_id not set, notifier is `None`; orchestrator logs warnings instead of sending alerts.
## Cross-Cutting Concerns
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
