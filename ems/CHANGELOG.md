# Changelog

## 1.8.0

- InfluxDB v1 support: replaced v2 client with native line protocol writes via httpx
- Works with the HA community InfluxDB add-on (v1.8.x) out of the box
- Config: influxdb_database/username/password replace influxdb_token/org/bucket
- Auto-discovery sets URL automatically, just configure database name and credentials

## 1.9.0

- EMMA (Huawei Smart Energy Controller) integration via Modbus TCP
- True household consumption calculated from EMMA load + Victron discharge
- EMMA data logged to InfluxDB (ems_emma measurement)
- EMMA feed-in limit corrected to 10kW/100%
- Huawei deadband reduced to 200W for better PV response
- Shadow mode disabled, commissioning advanced to DUAL_BATTERY

## 1.8.x

- InfluxDB v1 support (line protocol writes)
- EVCC grid prices as primary tariff source
- Auto-calculated hausverbrauch from steuerbare + base
- Mode manager skips health checks during shadow mode

## 1.7.0

- Commissioning control panel: force-advance stages, toggle shadow mode from the dashboard
- InfluxDB auto-discovery for community add-on (direct add-on lookup fallback)
- Dead code cleanup: removed unused entity defaults and imports

## 1.6.0

- Dashboard UX rework: 3-column desktop layout, compact energy flow card, logical card ordering
- Extended Huawei registers: internal temperature, grid frequency, phase voltages, daily/total yield, battery stats
- Slave inverter PV power now read and summed with master for total PV display

## 1.5.1

- Fix: expose PV input power from both master and slave inverters to API and dashboard
- Dashboard Solar node now shows total PV from both inverters

## 1.5.0

- Configurable HA entity IDs via add-on options — no more hardcoded sensor names
- All 8 multi-entity REST sensors (heat pump, COP, Vorlauf/Ruecklauf, consumption) now individually configurable
- Empty entity = disabled, eliminates 404 log spam for non-existent sensors
- Fix: HA Ingress double-slash path normalisation (//api/state returning 404)
- Full EN/DE translations for all new configuration options

## 0.3.0

- ML consumption forecaster with temperature-correlated features
- Multi-entity HA reader (8 concurrent sensors)
- Live Octopus tariff from HA entity with Modul 3 overlay
- EVopt-compatible /api/v1/plan endpoint
- Dashboard: LoadsCard multi-entity, TariffCard source badge, OptimizationCard forecast comparison
- JWT secret auto-generation and persistence
- Supervisor InfluxDB auto-discovery
- GitHub Actions CI/CD for GHCR image builds

## 0.2.0

- Per-phase Victron grid dispatch with dead-band
- Phase imbalance detection and alerting
- Grafana dashboards and InfluxDB provisioning
- Huawei slave inverter polling
- PV surplus pre-charge mode
- HA Add-on production deployment

## 0.1.0

- Initial release
- Huawei SUN2000 + Victron MultiPlus II orchestration
- EVCC coordination
- Setup wizard and web dashboard
- InfluxDB metrics logging
- Telegram alerts
