# Changelog

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
