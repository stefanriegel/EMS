# Technology Stack

**Analysis Date:** 2026-03-21

## Languages

**Primary:**
- Python 3.12+ - Backend API, drivers, orchestrator, and scheduler logic (`backend/` directory)
- TypeScript 5.9.3 - Frontend dashboard and UI components (`frontend/src/`)
- React 19.2.4 - Frontend framework and component library

**Secondary:**
- Shell (bash/zsh) - Setup scripts and addon entry points (`scripts/`, `ha-addon/run.sh`)

## Runtime

**Environment:**
- FastAPI 0.x (Python async web framework)
- Uvicorn (ASGI server for FastAPI)
- Node.js/npm (frontend build and dev tools)
- Python 3.12+ (CPython)

**Package Managers:**
- `uv` (Python package manager, lockfile: `uv.lock` - 291KB)
- npm (Node.js, lockfile: `package-lock.json`)
- pip (Python packaging via setuptools in `pyproject.toml`)

## Frameworks

**Core Backend:**
- FastAPI - REST API and WebSocket server (`backend/main.py`, `backend/api.py`)
- Uvicorn[standard] - ASGI application server

**Frontend:**
- React 19.2.4 - Component-based UI framework
- Vite 8.0.1 - Frontend build tool and dev server
- wouter 3.9.0 - Client-side routing (lightweight router, no Next.js)
- TypeScript 5.9.3 - Type-safe JavaScript with JSDoc/TSDoc annotations

**Testing:**
- pytest 8+ - Backend unit and async testing (`pyproject.toml` with `asyncio_mode = "auto"`)
- pytest-anyio - Async testing with trio support
- pytest-mock - Mocking and fixture support
- Playwright 1.58.2 - Frontend E2E testing (`frontend/package.json`)

**Build & Dev:**
- Vite 8.0.1 - Frontend bundler and dev server
- ESLint 9.39.4 - JavaScript/TypeScript linting (`frontend/`)
- TypeScript compiler (tsc) - Type checking before Vite build
- setuptools 68+ - Python build backend

## Key Dependencies

**Critical Backend:**
- huawei-solar 2.5+ - Huawei LUNA2000 inverter/battery driver via Modbus TCP
- pymodbus 3.11 - Modbus protocol implementation
- paho-mqtt 2.1+ - MQTT client for Victron/HA/EVCC integration
- influxdb-client[async] 1.45+ - Time-series database client (optional, see INTEGRATIONS.md)
- httpx - Async HTTP client (EVCC, HA REST, Telegram APIs)
- fastapi - REST/WebSocket framework
- uvicorn[standard] - Production ASGI server with uvloop support

**Authentication & Security:**
- python-jose[cryptography] - JWT token generation and validation
- passlib[bcrypt] - Password hashing for admin auth
- bcrypt <4 - Bcrypt algorithm implementation

**Machine Learning & Forecasting:**
- scikit-learn 1.4 to <2 - ML models for consumption forecasting
- numpy 1.25 to <3 - Numerical computing for time-series analysis

**Frontend:**
- react 19.2.4 - UI component framework
- react-dom 19.2.4 - React DOM rendering
- wouter 3.9.0 - Lightweight routing (replaces Next.js)

## Configuration

**Environment Variables:**
All configuration is environment-driven (`backend/config.py`):

**Core Drivers:**
- `HUAWEI_HOST`, `HUAWEI_PORT` - Modbus TCP proxy address (required)
- `VICTRON_HOST`, `VICTRON_PORT` - Venus OS MQTT broker (required)

**Optional Integrations:**
- `INFLUXDB_URL`, `INFLUXDB_TOKEN` - InfluxDB time-series (optional, skipped if both empty)
- `EVCC_HOST`, `EVCC_PORT` - EVCC API (optional, default 192.168.0.10:7070)
- `EVCC_MQTT_HOST`, `EVCC_MQTT_PORT` - EVCC MQTT broker (optional)
- `HA_URL`, `HA_TOKEN` - Home Assistant REST API (optional)
- `HA_MQTT_HOST`, `HA_MQTT_PORT`, `HA_MQTT_USERNAME`, `HA_MQTT_PASSWORD` - HA MQTT
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - Telegram notifications (optional)

**Scheduler & Tariffs:**
- `SCHEDULER_RUN_HOUR` - Hour to run charge scheduler (default 23)
- `SCHEDULER_CHARGE_START_MIN`, `SCHEDULER_CHARGE_END_MIN` - Charge window bounds
- `OCTOPUS_*` - Octopus Go tariff rates (UK supply, optional)
- `MODUL3_TIMEZONE` - German grid-fee window timezone

**System Tuning:**
- `LOG_LEVEL` - Logging verbosity (INFO, DEBUG, WARNING)
- `EMS_CONFIG_PATH` - Setup config file location (default `/config/options.json` in HA)
- `HA_STAT_OUTDOOR_TEMP_ENTITY`, `HA_STAT_HEAT_PUMP_ENTITY` - HA statistics for ML forecasting

**Environment Files:**
- `.env` - Development environment (present but not read here for security)
- `.env.example` - Example configuration template
- `docker-compose.yml` - Local development with InfluxDB + EMS services

## Build & Deployment

**Frontend Build:**
```bash
# In frontend/
npm run build         # TypeScript + Vite → dist/
npm run dev          # Vite dev server with HMR
npm run lint         # ESLint type checking
npm run test:playwright # E2E tests with Playwright
```

**Backend Build:**
```bash
# Python setuptools
python -m pip install -e .           # Editable install
python -m uvicorn backend.main:app   # Development server
```

**Docker:**
- `Dockerfile` - Single-stage Python 3.12 image, serves both backend (FastAPI) and static frontend
- `docker-compose.yml` - Local dev stack: InfluxDB (2.7) + EMS service

## Platform Requirements

**Development:**
- Python 3.12+
- Node.js 18+ (for npm/Vite)
- MQTT broker (Victron Venus OS MQTT broker)
- Huawei Modbus TCP proxy / SUN2000 dongle

**Production:**
- Docker (via `docker-compose` or Kubernetes)
- Home Assistant Add-on (HA ecosystem integration)
- Optional: InfluxDB 2.x (time-series storage)
- Optional: EVCC energy management system
- Optional: Telegram Bot API access

## Key Design Patterns

**Configuration Loading:**
All configs load from `os.environ` via dataclass `.from_env()` methods in `backend/config.py`. Safe defaults allow tests to run without environment setup. Empty strings treated as missing (e.g., `INFLUXDB_URL` or `INFLUXDB_TOKEN`).

**Async/Await:**
Entire backend is async via asyncio. FastAPI uses async route handlers, httpx for async HTTP, paho-mqtt with asyncio support.

**Frontend Architecture:**
- Custom React hooks (`useEmsSocket`, `useEmsState`) for WebSocket and polling fallback
- Wouter for lightweight client-side routing (no SSR, pure SPA)
- Vite dev proxy (`localhost:5173`) forwards `/api/ws/state` to backend in dev mode

---

*Stack analysis: 2026-03-21*
