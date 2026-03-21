# External Integrations

**Analysis Date:** 2026-03-21

## APIs & External Services

**Device Drivers (Required):**
- **Huawei LUNA2000 Inverter/Battery** - Modbus TCP proxy connection
  - SDK/Client: `huawei-solar` package + `pymodbus`
  - Config: `HUAWEI_HOST`, `HUAWEI_PORT` (default 502)
  - Driver: `backend/drivers/huawei_driver.py`
  - Models: `backend/drivers/huawei_models.py`

- **Victron Multiplus II / Venus OS** - MQTT broker on Victron system
  - SDK/Client: `paho-mqtt`
  - Config: `VICTRON_HOST`, `VICTRON_PORT` (default 1883)
  - Driver: `backend/drivers/victron_driver.py`
  - Models: `backend/drivers/victron_models.py`

**Energy Management (Optional):**
- **EVCC** - EV charging optimization and solar forecasting
  - HTTP API: `GET /api/state` for optimization results, solar forecast, grid prices
  - SDK/Client: `httpx` (async HTTP), custom `backend/evcc_client.py`
  - Config: `EVCC_HOST` (default 192.168.0.10), `EVCC_PORT` (default 7070)
  - MQTT (optional): `EVCC_MQTT_HOST`, `EVCC_MQTT_PORT`
  - Models: `backend/evcc_models.py`, `backend/evcc_mqtt_driver.py`

**Smart Home Hub (Optional):**
- **Home Assistant** - REST API polling + MQTT discovery + SQLite statistics
  - REST API: `GET /api/states/<entity_id>` for sensor polling
  - MQTT: Publish EMS telemetry via discovery topics
  - SQLite: Direct read access to `home-assistant_v2.db` for historical stats
  - SDK/Client: `httpx` (REST), `paho-mqtt` (MQTT), SQLite3 (stdlib)
  - Config:
    - `HA_URL` - Base URL (e.g., `http://homeassistant.local:8123`)
    - `HA_TOKEN` - Long-lived access token
    - `HA_MQTT_HOST`, `HA_MQTT_PORT`, `HA_MQTT_USERNAME`, `HA_MQTT_PASSWORD`
    - `HA_HEAT_PUMP_ENTITY_ID` - Specific entity for heat pump power
    - `HA_DB_PATH` - Path to SQLite database (default `/config/home-assistant_v2.db`)
    - `HA_STAT_OUTDOOR_TEMP_ENTITY`, `HA_STAT_HEAT_PUMP_ENTITY`, `HA_STAT_DHW_ENTITY`
  - Clients:
    - `backend/ha_rest_client.py` - Async polling of sensors
    - `backend/ha_mqtt_client.py` - Publish discovery + state updates
    - `backend/ha_statistics_reader.py` - SQLite direct access for ML training
  - Supervisor API: `backend/supervisor_client.py` (internal HA communication)

**Alerts & Notifications (Optional):**
- **Telegram Bot API** - Send alerts via Telegram
  - API: `POST https://api.telegram.org/bot{token}/sendMessage`
  - SDK/Client: `httpx`
  - Config: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
  - Notifier: `backend/notifier.py` with per-category cooldown (default 5 min)

## Data Storage

**Databases:**
- **InfluxDB 2.x** (Optional, can be disabled)
  - Connection: `INFLUXDB_URL` (default http://localhost:8086), `INFLUXDB_TOKEN`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET`
  - Client: `influxdb-client[async]` (async InfluxDBClientAsync)
  - Enabled: Only when `INFLUXDB_URL` or `INFLUXDB_TOKEN` is explicitly set
  - Writer: `backend/influx_writer.py` (fire-and-forget writes, never crashes orchestrator)
  - Reader: `backend/influx_reader.py` (historical data retrieval)
  - Measurements:
    - `ems_system` - Battery pool state (SoC, power, setpoints, control state)
    - `ems_tariff` - Electricity rates (effective, Octopus Go, Modul3 grid fees)
  - Local dev: `docker-compose.yml` spins up InfluxDB 2.7 on port 8086

- **SQLite (Home Assistant)**
  - Direct read-only access to HA statistics database
  - Path: `HA_DB_PATH` (default `/config/home-assistant_v2.db`)
  - Usage: Historical consumption + temperature data for ML consumption forecasting
  - Client: Python `sqlite3` (stdlib)

**File Storage:**
- Local filesystem only (no cloud storage)
- Docker volume: `influxdb-data:/var/lib/influxdb2` (InfluxDB persistence)
- Config file: `EMS_CONFIG_PATH` (default `/config/options.json` in HA add-on context)

**Caching:**
- In-memory state in `Orchestrator` and hook caches (no Redis/Memcached)
- WebSocket broadcast via `backend/ws_manager.py` (single connection pool per client)

## Authentication & Identity

**Auth Provider:**
- **Custom JWT-based**
  - Implementation: `backend/auth.py` with `AdminConfig`, `AuthMiddleware`
  - Token generation: `python-jose[cryptography]` with configurable secret
  - Password hashing: `passlib[bcrypt]`
  - Admin login: `/setup/login` endpoint (setup-only, before system config)
  - Middleware: `AuthMiddleware` validates JWT on protected endpoints

**External API Keys:**
- `HA_TOKEN` - Home Assistant long-lived access token
- `INFLUXDB_TOKEN` - InfluxDB authentication token (never logged)
- `TELEGRAM_BOT_TOKEN` - Telegram Bot token from BotFather
- All tokens sourced from environment (no hardcoding)

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry, LogRocket, or external error service)
- All errors logged locally to stdout/stderr via Python `logging` module

**Logs:**
- Python `logging` module, configured via `LOG_LEVEL` env var (default INFO)
- Log format: `%(asctime)s %(levelname)s %(name)s %(message)s`
- Module loggers: `ems.evcc`, `backend.notifier`, etc. (accessible via grep)
- Key patterns:
  - `"influx write failed"` - InfluxDB write failures (non-fatal, log-only)
  - `"HA REST poll failed"` - Home Assistant sensor polling failures
  - `"Telegram alert sent: [category]"` - Successful Telegram notifications
  - `"evcc get_state failed"` - EVCC API unreachability

**Metrics Collection:**
- InfluxDB time-series storage (optional)
- Nightly scheduler loop can trigger ML consumption forecasting
- No Prometheus, no StatsD

## CI/CD & Deployment

**Hosting:**
- Docker containers (primary: EMS FastAPI + static frontend)
- Home Assistant Add-on (HA ecosystem, custom supervisor client)
- Local development: `docker-compose` with InfluxDB + EMS

**CI Pipeline:**
- GitHub Actions (`.github/` directory)
- No detected external CI service (only repository structure)

**Deployment Targets:**
- Docker Hub (if published)
- Home Assistant Community Store (add-on distribution)

## Webhooks & Callbacks

**Incoming (None):**
- No external services trigger the EMS
- All integration is pull-based (polling, connections initiated by EMS)

**Outgoing (None):**
- Telegram is alert-only, no callback expected
- HA MQTT publish is fire-and-forget
- No HTTP callbacks to external services

## Environment Configuration

**Required env vars for operation:**
- `HUAWEI_HOST` - IP/hostname of Modbus TCP proxy (required, no default)
- `VICTRON_HOST` - IP/hostname of Venus OS MQTT (required, no default)

**Critical optional env vars:**
- `INFLUXDB_URL` or `INFLUXDB_TOKEN` - Either triggers InfluxDB integration (otherwise disabled)
- `HA_URL` + `HA_TOKEN` - Both required for Home Assistant integration
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` - Both required for Telegram notifications

**Secrets location:**
- Environment variables only (no `.secrets/` folder, no config files with embedded tokens)
- `.env` file in development (not committed to git per `.gitignore`)
- Home Assistant add-on: Secrets loaded from `/config/options.json` (HA supervisor integration)

**Safe defaults (tests run without any environment setup):**
- `EVCC_HOST=192.168.0.10`, `EVCC_PORT=7070`
- `HA_MQTT_HOST=192.168.0.10`, `HA_MQTT_PORT=1883`
- `INFLUXDB_URL=http://localhost:8086`, `INFLUXDB_ORG=ems`, `INFLUXDB_BUCKET=ems`
- `OCTOPUS_OFF_PEAK_START_MIN=30`, `OCTOPUS_OFF_PEAK_END_MIN=330` (UK 00:30–05:30)
- `LOG_LEVEL=INFO`

## Home Assistant Add-on Integration

**Supervisor Communication:**
- `backend/supervisor_client.py` - Queries HA supervisor API for:
  - MQTT broker info (host, port, auth)
  - EVCC service details (API host/port)
  - InfluxDB add-on config
- Enables zero-config setup in HA environment

**Add-on Discovery:**
- Automatic detection of co-installed HA add-ons (EVCC, InfluxDB)
- Falls back to environment variable defaults if not detected

## Modbus & MQTT Protocol Details

**Modbus TCP (Huawei):**
- Protocol version: Modbus TCP
- Unit IDs: Master (default 0), Slave (default 2)
- Timeout: 10 seconds per request
- Implemented in `backend/drivers/huawei_driver.py`

**MQTT (Victron, HA, EVCC optional):**
- Broker protocol: MQTT 3.1.1
- Victron topics: Discovery via `N/<portalId>/system/0/SerialNumber` keep-alive
- HA discovery: `homeassistant/<domain>/<device_id>/<object_id>/config`
- QoS: Default (typically 0 or 1, depends on client settings)

---

*Integration audit: 2026-03-21*
