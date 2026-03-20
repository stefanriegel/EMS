#!/usr/bin/with-contenv bashio

# ---------------------------------------------------------------------------
# Required hardware endpoints (no default — user must configure these in HA)
# ---------------------------------------------------------------------------
export HUAWEI_HOST=$(bashio::config 'huawei_host')
export VICTRON_HOST=$(bashio::config 'victron_host')
export INFLUXDB_URL=$(bashio::config 'influxdb_url')
export INFLUXDB_TOKEN=$(bashio::config 'influxdb_token')

# ---------------------------------------------------------------------------
# Optional port overrides and service addresses
# ---------------------------------------------------------------------------
export HUAWEI_PORT=$(bashio::config 'huawei_port' '502')
export VICTRON_PORT=$(bashio::config 'victron_port' '1883')
export INFLUXDB_ORG=$(bashio::config 'influxdb_org' 'ems')
export INFLUXDB_BUCKET=$(bashio::config 'influxdb_bucket' 'ems')

export EVCC_HOST=$(bashio::config 'evcc_host' '192.168.0.10')
export EVCC_PORT=$(bashio::config 'evcc_port' '7070')
export EVCC_MQTT_HOST=$(bashio::config 'evcc_mqtt_host' '192.168.0.10')
export EVCC_MQTT_PORT=$(bashio::config 'evcc_mqtt_port' '1883')

# ---------------------------------------------------------------------------
# Home Assistant REST API (optional; empty = disabled)
# ---------------------------------------------------------------------------
export HA_URL=$(bashio::config 'ha_url' '')
export HA_TOKEN=$(bashio::config 'ha_token' '')
export HA_HEAT_PUMP_ENTITY_ID=$(bashio::config 'ha_heat_pump_entity_id' '')

# ---------------------------------------------------------------------------
# Home Assistant MQTT (optional)
# ---------------------------------------------------------------------------
export HA_MQTT_HOST=$(bashio::config 'ha_mqtt_host' '192.168.0.10')
export HA_MQTT_PORT=$(bashio::config 'ha_mqtt_port' '1883')
export HA_MQTT_USERNAME=$(bashio::config 'ha_mqtt_username' '')
export HA_MQTT_PASSWORD=$(bashio::config 'ha_mqtt_password' '')

# ---------------------------------------------------------------------------
# Telegram alerts (optional; empty token/chat_id = disabled)
# ---------------------------------------------------------------------------
export TELEGRAM_BOT_TOKEN=$(bashio::config 'telegram_bot_token' '')
export TELEGRAM_CHAT_ID=$(bashio::config 'telegram_chat_id' '')

# ---------------------------------------------------------------------------
# Auth (opt-in; leave admin_password_hash blank to disable)
# ---------------------------------------------------------------------------
export ADMIN_PASSWORD_HASH=$(bashio::config 'admin_password_hash' '')
export JWT_SECRET=$(bashio::config 'jwt_secret' '')

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
export LOG_LEVEL=$(bashio::config 'log_level' 'INFO')

# ---------------------------------------------------------------------------
# Fixed paths — not user-configurable
# /config is mounted read-write via map: ["config:rw"] in config.yaml
# ---------------------------------------------------------------------------
export EMS_CONFIG_PATH="/config/ems_config.json"

exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
