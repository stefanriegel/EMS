# EMS — Energy Management System

Orchestrates battery dispatch for a **Huawei SUN2000** solar inverter and **Victron MultiPlus II** inverter/charger. Reads Huawei state over Modbus TCP, reads and writes Victron state over MQTT (Venus OS), co-ordinates with EVCC for EV charging, logs time-series data to InfluxDB, and exposes a React dashboard on port 8000.

**System requirements:**

| Component | Minimum |
|-----------|---------|
| Docker Engine | 24+ (with Compose v2) |
| RAM | 512 MB free |
| Python | 3.12+ (development only) |
| Node.js | 18+ (frontend development only) |

---

## Overview

EMS runs as a single FastAPI backend process that:

- Polls the Huawei SUN2000 via Modbus TCP every 5 s and reads battery SoC, pack voltages, and active power
- Reads Victron MultiPlus II state from Venus OS MQTT and writes AC power setpoints for per-phase grid feed-in/import control
- Runs an orchestrator loop that computes charge/discharge setpoints based on configurable tariff windows and SoC limits
- Publishes device telemetry to InfluxDB for Grafana dashboards
- Sends Telegram alerts on sustained grid overload or fault conditions
- Provides a React-based web UI for live monitoring, a first-run setup wizard, and runtime settings

---

## Quick Start (Docker Compose)

### 1 — Clone and configure

```bash
git clone <repo-url> ems
cd ems
cp .env.example .env
```

Open `.env` and set the two required values:

```ini
HUAWEI_HOST=192.168.1.185   # IP of Huawei Modbus proxy (SDongle / SolarmanV5 bridge)
VICTRON_HOST=192.168.1.150  # IP of Victron Cerbo GX (Venus OS MQTT)
```

All other values have working defaults for a first run.

### 2 — Start the stack

```bash
docker compose up -d
```

Services start in dependency order: InfluxDB first (health-checked), then EMS and Grafana.

### 3 — Complete the setup wizard

Open **http://localhost:8080** in your browser. On first run, EMS redirects to the setup wizard. Fill in:

- Huawei Modbus connection details (host/port, slave ID)
- Victron MQTT connection details
- Tariff schedule (off-peak window, peak/off-peak rates)
- Battery SoC limits for Huawei and Victron
- EVCC, Home Assistant, and Telegram settings (all optional)

Click **Save** on the final step. Settings are written to `/config/ems_config.json` inside the named Docker volume and survive container restarts.

### 4 — Verify

```bash
curl -s http://localhost:8080/api/health
# → {"status":"ok"}

curl -s http://localhost:8080/api/state | python3 -m json.tool
```

Grafana dashboards are available at **http://localhost:3001** (default password: `admin`).

---

## Configuration

### Environment variables

All variables are read by the `ems` container at startup. Set them in `.env` (Docker Compose picks this up automatically).

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `HUAWEI_HOST` | `192.168.0.10` | **Yes** | IP of Huawei Modbus TCP proxy |
| `VICTRON_HOST` | `192.168.0.20` | **Yes** | IP of Victron Cerbo GX (Venus OS) |
| `INFLUXDB_TOKEN` | `ems-dev-token-change-me` | Yes | InfluxDB API token — must match `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN` |
| `INFLUXDB_ORG` | `ems` | No | InfluxDB organisation name |
| `INFLUXDB_BUCKET` | `ems` | No | InfluxDB bucket name |
| `INFLUXDB_URL` | `http://influxdb:8086` | No | Set automatically by docker-compose; override for external InfluxDB |
| `HUAWEI_MASTER_SLAVE_ID` | `0` | No | Modbus slave ID for the master inverter |
| `LOG_LEVEL` | `INFO` | No | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `ADMIN_PASSWORD_HASH` | _(unset)_ | No | bcrypt hash of admin password — leave unset to disable auth |
| `JWT_SECRET` | _(unset)_ | No | Secret for JWT signing — required when `ADMIN_PASSWORD_HASH` is set |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | No | Grafana web UI admin password |

> **Production note:** Change `INFLUXDB_TOKEN`, `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN`, and `GRAFANA_ADMIN_PASSWORD` before exposing the stack on your network.

### Enabling authentication

By default, the EMS UI is accessible without a password. To enable login protection:

**Step 1 — Generate a bcrypt hash of your password:**

```bash
# Using htpasswd (Apache utils):
htpasswd -bnBC 12 "" yourpassword | tr -d ':\n'

# Or using Python (passlib must be installed):
python3 -c "from passlib.hash import bcrypt; print(bcrypt.hash('yourpassword', rounds=12))"
```

**Step 2 — Generate a JWT secret:**

```bash
openssl rand -hex 32
```

**Step 3 — Add both to `.env`:**

```ini
ADMIN_PASSWORD_HASH=$2y$12$...   # output from step 1
JWT_SECRET=abc123...             # output from step 2
```

**Step 4 — Restart the EMS container:**

```bash
docker compose restart ems
```

The login page will appear on next browser access.

---

## Home Assistant Add-on

### Install

1. In Home Assistant, go to **Settings → Add-ons → Add-on Store**.
2. Click the three-dot menu (⋮) → **Repositories**.
3. Add the repository URL for this project (the URL of the repository root, which contains `repository.yaml`).
4. Find **EMS – Energy Management System** in the store and click **Install**.
5. After installation, go to the add-on's **Configuration** tab and fill in the required fields (see table below).
6. Click **Save**, then **Start**.

### Configuration options

| Option | Required | Description |
|--------|----------|-------------|
| `huawei_host` | **Yes** | IP address of the Huawei Modbus TCP proxy |
| `victron_host` | **Yes** | IP address of the Victron Cerbo GX |
| `influxdb_url` | **Yes** | URL of your InfluxDB instance (e.g. `http://192.168.1.x:8086`) |
| `influxdb_token` | **Yes** | InfluxDB API token |
| `huawei_port` | No (502) | Modbus TCP port |
| `victron_port` | No (1883) | Venus OS MQTT port |
| `influxdb_org` | No (ems) | InfluxDB organisation |
| `influxdb_bucket` | No (ems) | InfluxDB bucket |
| `evcc_host` | No | EVCC HTTP host |
| `evcc_port` | No (7070) | EVCC HTTP port |
| `evcc_mqtt_host` | No | EVCC MQTT broker host |
| `evcc_mqtt_port` | No (1883) | EVCC MQTT broker port |
| `ha_url` | No | Home Assistant base URL (for REST sensor reads) |
| `ha_token` | No | Long-lived HA access token |
| `ha_heat_pump_entity_id` | No | HA entity ID for heat-pump power sensor |
| `ha_mqtt_host` | No | HA Mosquitto MQTT broker host |
| `ha_mqtt_port` | No (1883) | HA MQTT broker port |
| `ha_mqtt_username` | No | HA MQTT username |
| `ha_mqtt_password` | No | HA MQTT password |
| `telegram_bot_token` | No | Telegram bot token for alerts |
| `telegram_chat_id` | No | Telegram chat ID for alerts |
| `admin_password_hash` | No | bcrypt hash of admin password (see Authentication above) |
| `jwt_secret` | No | JWT signing secret (required when `admin_password_hash` is set) |
| `log_level` | No (INFO) | Log level |

The add-on uses the Home Assistant `/config` directory (mapped read-write) to persist `ems_config.json`.

### Network note

The add-on defaults to bridge networking. If your Huawei Modbus proxy or Victron Cerbo GX is on a LAN subnet that is unreachable from the HA OS bridge network (e.g. hardware on `192.168.1.x` and HA on a separate VLAN), enable host networking:

1. In `ha-addon/config.yaml`, change `host_network: false` to `host_network: true`.
2. Rebuild and reinstall the add-on.

With host networking, the add-on shares the Home Assistant host's network interface and can reach all LAN addresses directly.

---

## Lovelace Dashboard Card

Add a **Webpage card** to any Lovelace dashboard to embed the EMS UI inside Home Assistant:

1. Go to the Lovelace dashboard where you want to add the card.
2. Click **Edit dashboard** → **Add card** → search for **Webpage**.
3. Paste the following YAML:

```yaml
type: webpage
url: http://<ha-host>:8000
title: Energy Management
aspect_ratio: "16:9"
```

Replace `<ha-host>` with your Home Assistant host IP or hostname.

> **Note:** The EMS UI is served over plain HTTP. If your browser enforces HTTPS for embedded frames (mixed-content policy), load the Lovelace dashboard over HTTP as well, or use a reverse proxy to serve EMS over HTTPS.

Alternatively, use a standard **IFrame card** (identical effect):

```yaml
type: iframe
url: http://<ha-host>:8000
aspect_ratio: "16:9"
```

---

## Architecture

```
┌──────────────┐   Modbus TCP    ┌──────────────────────┐
│ Huawei       │◄───────────────►│                      │
│ SUN2000      │                 │  EMS Backend         │
└──────────────┘                 │  (FastAPI / Python)  │
                                 │                      │
┌──────────────┐   MQTT (Venus)  │  Orchestrator loop   │
│ Victron      │◄───────────────►│  (5 s cycle)         │
│ Cerbo GX     │                 │                      │
└──────────────┘                 │  Port 8000           │
                                 └──────┬───────────────┘
┌──────────────┐   HTTP / MQTT          │ InfluxDB line
│ EVCC         │◄───────────────►       ▼ protocol
└──────────────┘            ┌──────────────────┐
                            │ InfluxDB 2.7     │
┌──────────────┐            │ Port 8086        │
│ Home Asst.   │◄──REST/MQTT│                  │
└──────────────┘            └────────┬─────────┘
                                     │
                            ┌────────▼─────────┐
                            │ Grafana 10        │
                            │ Port 3001         │
                            └──────────────────┘
```

| Component | Technology | Port |
|-----------|-----------|------|
| EMS backend + UI | Python / FastAPI + React | 8080 (Docker) / 8000 (direct) |
| InfluxDB | Time-series database | 8086 |
| Grafana | Dashboard visualisation | 3001 |
| Victron read | Venus OS MQTT (`N/` topics) | 1883 on Cerbo GX |
| Victron write | Venus OS MQTT (`W/` topics) | 1883 on Cerbo GX (direct, not via HA broker) |
| Huawei | Modbus TCP | 502 on SDongle proxy |

> **Important — Victron write path:** AC power setpoints must be written directly to the Cerbo GX (`W/` topics). Writing setpoints to the Home Assistant Mosquitto broker (bridge) is silently accepted but does **not** reach Venus OS.

---

## Development

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Run with auto-reload:
uvicorn backend.main:app --reload --port 8000
```

The backend expects hardware to be reachable. Start without hardware — it logs warnings but continues in degraded mode.

### Frontend

```bash
cd frontend
npm install
npm run dev          # dev server at http://localhost:5173 with proxy to :8000
npm run build        # production build → frontend/dist/
```

### Running tests

```bash
# Unit + integration tests:
.venv/bin/python -m pytest tests/ -q

# With coverage:
.venv/bin/python -m pytest tests/ --cov=backend --cov-report=term-missing

# Playwright end-to-end tests:
cd frontend
npx playwright install --with-deps chromium
npx playwright test
```

### Building the Docker image

```bash
docker context use desktop-linux
docker build -t ems:local .
```

### Health and diagnostics

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Returns `{"status":"ok"}` when the backend is running |
| `GET /api/state` | Live orchestrator state snapshot |
| `GET /api/devices` | Per-device availability and last-read values |
| `GET /api/setup/status` | Whether the setup wizard has been completed |

Check container logs:

```bash
docker compose logs ems -f
docker compose logs ems | grep -E "ERROR|WARNING|Failed"
```

Verify config persistence after a restart:

```bash
docker compose restart ems
docker compose exec ems cat /config/ems_config.json
```
