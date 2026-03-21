#!/usr/bin/env bash
set -e

# Read options from /data/options.json (written by HA Supervisor).
# Optional fields (schema type "str?") may be absent — // "" converts null/absent
# to empty string so backend env vars are always set (empty = disabled).
get_option() {
    jq -r --arg key "$1" '.[$key] // ""' /data/options.json
}

# --- Required hardware endpoints ---
export HUAWEI_HOST=$(get_option 'huawei_host')
export HUAWEI_PORT=$(get_option 'huawei_port')
export HUAWEI_MASTER_SLAVE_ID=$(get_option 'huawei_master_unit_id')
export HUAWEI_SLAVE_SLAVE_ID=$(get_option 'huawei_slave_unit_id')

export VICTRON_HOST=$(get_option 'victron_host')
export VICTRON_PORT=$(get_option 'victron_port')

export INFLUXDB_URL=$(get_option 'influxdb_url')
export INFLUXDB_TOKEN=$(get_option 'influxdb_token')
export INFLUXDB_ORG=$(get_option 'influxdb_org')
export INFLUXDB_BUCKET=$(get_option 'influxdb_bucket')

# --- Auto-discovered by Supervisor (MQTT, HA REST, EVCC add-on) ---
# SUPERVISOR_TOKEN is injected automatically by the HA Supervisor — no export needed.
# The EMS backend calls GET http://supervisor/services/mqtt for MQTT credentials
# and http://supervisor/core/api for HA REST access.
#
# Override below only if you need to point at a different broker / EVCC instance:
_evcc_host=$(get_option 'evcc_host')
_evcc_port=$(get_option 'evcc_port')
[ -n "$_evcc_host" ] && export EVCC_HOST="$_evcc_host"
[ -n "$_evcc_port" ] && export EVCC_PORT="$_evcc_port"

_ha_heat=$(get_option 'ha_heat_pump_entity_id')
[ -n "$_ha_heat" ] && export HA_HEAT_PUMP_ENTITY_ID="$_ha_heat"

# --- Optional: ML consumption forecaster ---
_ha_db=$(get_option 'ha_db_path')
[ -n "$_ha_db" ] && export HA_DB_PATH="$_ha_db"

_ha_stat_outdoor=$(get_option 'ha_statistics_entity_outdoor_temp')
[ -n "$_ha_stat_outdoor" ] && export HA_STAT_OUTDOOR_TEMP_ENTITY="$_ha_stat_outdoor"

_ha_stat_hp=$(get_option 'ha_statistics_entity_heat_pump')
[ -n "$_ha_stat_hp" ] && export HA_STAT_HEAT_PUMP_ENTITY="$_ha_stat_hp"

_ha_stat_dhw=$(get_option 'ha_statistics_entity_dhw')
[ -n "$_ha_stat_dhw" ] && export HA_STAT_DHW_ENTITY="$_ha_stat_dhw"

_ha_ml_min=$(get_option 'ha_ml_min_days')
[ -n "$_ha_ml_min" ] && export HA_ML_MIN_DAYS="$_ha_ml_min"

# --- Optional: Live Octopus tariff from HA entity ---
_ha_octopus=$(get_option 'ha_octopus_entity_id')
[ -n "$_ha_octopus" ] && export HA_OCTOPUS_ENTITY_ID="$_ha_octopus"

# --- Optional: Telegram alerts ---
export TELEGRAM_BOT_TOKEN=$(get_option 'telegram_bot_token')
export TELEGRAM_CHAT_ID=$(get_option 'telegram_chat_id')

# --- Optional: EMS web UI authentication ---
export ADMIN_PASSWORD_HASH=$(get_option 'admin_password_hash')
# JWT_SECRET is generated automatically on first startup and persisted to
# /config/.jwt_secret — no operator action required.

# --- Logging ---
export LOG_LEVEL=$(get_option 'log_level')

# EMS wizard config persisted to the HA config volume
export EMS_CONFIG_PATH="/config/ems_config.json"

exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
