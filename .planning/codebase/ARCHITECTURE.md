# Architecture

**Analysis Date:** 2026-03-21

## Pattern Overview

**Overall:** Event-driven orchestrator with pluggable hardware drivers and async control loops.

The EMS follows a **layered, driver-centric architecture** with three core responsibilities:
- **Hardware Polling:** Concurrent async drivers read Huawei (Modbus TCP) and Victron (MQTT) state on every control cycle
- **Unified Orchestration:** A single `Orchestrator` computes SoC-balanced setpoints and dispatches them to both systems
- **Web API & UI:** FastAPI HTTP endpoints expose orchestrator state; a React frontend consumes updates via WebSocket or polling fallback

**Key Characteristics:**
- **Async-first:** All I/O operations are async (FastAPI, paho-mqtt, httpx, InfluxDB)
- **Graceful degradation:** One driver failure keeps the other running; both offline transitions to HOLD state
- **Decoupled dependencies:** Drivers, tariff engines, and schedulers are injected into Orchestrator; tests mock them easily
- **Lifespan-managed:** FastAPI's lifespan context manager wires up all services at startup and tears them down at shutdown

## Layers

**Hardware Drivers Layer:**
- Purpose: Translate low-level hardware protocols to typed Python dataclasses
- Location: `backend/drivers/`
- Contains: `HuaweiDriver` (Modbus TCP), `VictronDriver` (MQTT), model dataclasses
- Depends on: External hardware, `huawei-solar` library, `paho-mqtt`
- Used by: Orchestrator, tests

**Orchestration & Control Loop:**
- Purpose: Poll drivers, compute unified SoC, compute setpoints, apply debounce/hysteresis, handle failures
- Location: `backend/orchestrator.py`
- Contains: `Orchestrator` class (main control loop)
- Depends on: Both drivers, tariff engine, scheduler, notifier, metrics writer
- Used by: API layer, FastAPI lifespan

**Scheduling & Optimization:**
- Purpose: Compute nightly charge schedules based on EVCC state, tariff rates, and consumption forecasts
- Location: `backend/scheduler.py`, `backend/consumption_forecaster.py`
- Contains: `Scheduler`, `ConsumptionForecaster` (optional ML models)
- Depends on: EVCC client, tariff engine, InfluxDB metrics reader, Home Assistant statistics
- Used by: Orchestrator (for GRID_CHARGE slot detection), API

**Tariff Engine:**
- Purpose: Provide electricity pricing at any instant and full-day slot schedules
- Location: `backend/tariff.py`, `backend/live_tariff.py`
- Contains: `CompositeTariffEngine` (multiple provider backends), `LiveOctopusTariff` (live price override)
- Depends on: Tariff configuration, optional Home Assistant REST client
- Used by: Scheduler, Orchestrator, API

**Configuration & Setup:**
- Purpose: Load and persist runtime config from environment, wizard, or Supervisor service discovery
- Location: `backend/config.py`, `backend/setup_config.py`, `backend/setup_api.py`
- Contains: Dataclass configs for all subsystems, setup wizard routes
- Depends on: Environment variables, persistent JSON file
- Used by: All layers during lifespan

**HTTP API & WebSocket:**
- Purpose: Expose orchestrator state, config, tariff data, and charge schedule via REST and real-time updates
- Location: `backend/api.py`, `backend/ws_manager.py`
- Contains: FastAPI routes, WebSocket connection manager
- Depends on: Orchestrator, scheduler, tariff engine, dependency injection
- Used by: Frontend, external consumers

**Data Persistence:**
- Purpose: Time-series metrics, charge schedules, and historical consumption data
- Location: `backend/influx_writer.py`, `backend/influx_reader.py`
- Contains: Async InfluxDB client wrappers
- Depends on: InfluxDB instance (optional)
- Used by: Orchestrator (metrics), Scheduler (consumption history)

**External Integrations:**
- Purpose: Connect to EVCC, Home Assistant, Telegram for alerts and coordination
- Location: `backend/evcc_client.py`, `backend/evcc_mqtt_driver.py`, `backend/ha_rest_client.py`, `backend/notifier.py`
- Contains: HTTP clients and MQTT drivers for downstream systems
- Depends on: External services (optional)
- Used by: Orchestrator, Scheduler, API

**React Frontend:**
- Purpose: Real-time dashboard displaying pool state, tariff, device details, and system health
- Location: `frontend/src/`
- Contains: Components, hooks, pages
- Depends on: WebSocket and HTTP APIs
- Used by: End users, Home Assistant Lovelace embedding

## Data Flow

**Startup Sequence:**

1. FastAPI lifespan manager loads environment and wizard config via `os.environ.setdefault()`
2. Supervisor service discovery (HA add-on only) resolves MQTT brokers and InfluxDB URLs
3. Both drivers are instantiated and connected asynchronously
4. Tariff engine, scheduler, EVCC client, and InfluxDB clients are created
5. Nightly scheduler task is spawned as an asyncio task
6. Orchestrator is instantiated, started, and stored on `app.state.orchestrator`
7. API layer is fully operational; frontend can start polling/connecting

**Control Loop (5 s cycle):**

1. Orchestrator calls `huawei.read_battery()` and `victron.read_system_state()` concurrently
2. Both results (or sentinels if offline) are used to compute `UnifiedPoolState`
3. Weighted-average SoC is calculated: `(huawei_soc * 30 + victron_soc * 64) / 94`
4. Available capacity above `min_soc_pct` is split proportionally across both systems
5. Hysteresis filter (dead-band) suppresses writes if Δ watts < threshold
6. Debounce state machine requires `debounce_cycles` consecutive identical states before committing
7. Setpoints are written to both drivers; state machine transitions occur
8. Metrics are written to InfluxDB asynchronously (fire-and-forget)
9. WebSocket clients receive updated `UnifiedPoolState` via the broadcast manager

**Nightly Scheduler (e.g., 04:00 local time):**

1. Scheduler fetches EVCC state (current SoC, grid power limits)
2. Consumption forecaster is retrained on HA SQLite stats (if available)
3. Cheapest tariff window is selected for the next day
4. Per-battery charge targets are computed based on load forecast and available solar
5. Resulting `ChargeSchedule` is written to InfluxDB and stored on `Scheduler.active_schedule`
6. Forecast comparison from yesterday's accuracy is recorded

**WebSocket/API Updates:**

1. Frontend connects to `ws://localhost:8000/api/ws/state`
2. Broadcast manager queues every orchestrator state change
3. WS clients receive JSON payloads containing pool, devices, and tariff at ~1 Hz
4. If WS closes, frontend falls back to polling `GET /api/state` every 2 s
5. Exponential backoff reconnect (up to 30 s delay) on WS close

**State Management:**

- Orchestrator state is the single source of truth; API is read-only for state
- Config changes via `POST /api/config` update `SystemConfig` and are applied on next cycle
- Persistent config lives in `ems_config.json` (wizard) or `JWT_SECRET` file
- Setup status (`setup_complete` flag) gates access to `/setup` vs `/` routes

## Key Abstractions

**UnifiedPoolState:**
- Purpose: Snapshot of the combined battery pool state
- Examples: `backend/unified_model.py`
- Pattern: Dataclass with computed properties (weighted SoC, total power, min/max limits)
- Produced by: Orchestrator on every control cycle
- Consumed by: API, WebSocket, tests

**ControlState (Enum):**
- Purpose: Enumeration of orchestrator operating modes
- Pattern: String-backed enum for JSON serialization
- Values: IDLE, DISCHARGE, CHARGE, HOLD, GRID_CHARGE, DISCHARGE_LOCKED
- Used by: State machine logic, API responses, frontend rendering

**Driver Interface (Protocol):**
- Purpose: Common shape for Huawei and Victron drivers
- Pattern: Both drivers implement `async connect()`, `async close()`, async read methods, write methods
- Sentinel values: Zeroed dataclasses returned when drivers are offline
- Graceful fallback: Orchestrator continues with partial data if one driver fails

**Dataclass Configs:**
- Purpose: Type-safe configuration from environment variables
- Examples: `HuaweiConfig`, `VictronConfig`, `SystemConfig`, `OrchestratorConfig`, etc.
- Pattern: Dataclass with `@classmethod from_env()` that reads `os.environ` and uses `_require_env()` for validation
- Separation: Hardware configs are read on startup; system/orchestrator configs can be updated via API

**ChargeSchedule:**
- Purpose: Next-day charging plan with time-based slot windows
- Location: `backend/schedule_models.py`
- Contains: List of `ChargeSlot` (start/end times, target SoC, reasoning)
- Pattern: Immutable dataclass produced nightly, readable via `/api/optimization/schedule`

## Entry Points

**FastAPI Application:**
- Location: `backend/main.py`
- Triggers: `uvicorn backend.main:app` or Docker ENTRYPOINT
- Responsibilities: Lifespan wiring, driver connections, orchestrator startup, API/SPA mounting

**React Application:**
- Location: `frontend/src/main.tsx`
- Triggers: Browser load of `/` or `/setup`
- Responsibilities: Route selection (setup wizard vs. dashboard), WebSocket connection, UI rendering

**Nightly Scheduler Loop:**
- Location: `backend/main.py` (`_nightly_scheduler_loop`)
- Triggers: 04:00 local time (configurable via `SCHEDULER_RUN_HOUR`)
- Responsibilities: Compute next-day charge schedule, retrain ML models, record metrics

**Setup Wizard API:**
- Location: `backend/setup_api.py`
- Triggers: `GET /setup/status` or `POST /setup/save-config`
- Responsibilities: First-run config collection, persistent config storage, setup completion

## Error Handling

**Strategy:** Fail-soft with logging and graceful degradation.

**Patterns:**

- **Driver Offline:** If one driver fails to connect or stalls, the other continues. Both offline for > `max_offline_s` transitions pool to HOLD.
- **Config Missing:** If required env vars are absent, lifespan raises `KeyError`, caught as setup-only mode. API returns 503 for state endpoints until config is provided.
- **API Errors:** Validation errors return 422 (Pydantic default); missing dependencies (e.g., no scheduler) return 503.
- **EVCC Unreachable:** Scheduler uses stale schedule or fallback consumption estimate; metrics are still written.
- **InfluxDB Disabled:** Metrics writer and reader are `None`; orchestrator continues; scheduler can fall back to HA SQLite.
- **Telegram Optional:** If token/chat_id not set, notifier is `None`; orchestrator logs warnings instead of sending alerts.

**Observability:** All errors and state transitions are logged at INFO/WARNING level with timestamp and context. Metrics include per-component uptime and error counts in InfluxDB.

## Cross-Cutting Concerns

**Logging:** Python `logging` module with module-level loggers (`backend.orchestrator`, `backend.drivers.huawei_driver`, etc.). Root logger configured by `LOG_LEVEL` env var (default INFO).

**Validation:** Pydantic models for API request/response bodies; dataclass configs validated in `from_env()` via `_require_env()` KeyError checks.

**Authentication:** Optional bcrypt + JWT via `AuthMiddleware` (enabled if `ADMIN_PASSWORD_HASH` is set); applies to all endpoints except `/api/health` and `/setup`.

**Observability/Metrics:** Time-series data written to InfluxDB asynchronously; includes pool state, per-device telemetry, charge schedules, and forecasts.

**MQTT Broker Connections:** Victron driver uses `paho-mqtt`; EVCC/HA MQTT use separate brokers (configurable, may be the same Mosquitto instance in HA).

**Async Runtime:** FastAPI runs on asyncio; driver I/O and HTTP calls are async; paho-mqtt callbacks cross the thread boundary via `loop.call_soon_threadsafe()`.

---

*Architecture analysis: 2026-03-21*
