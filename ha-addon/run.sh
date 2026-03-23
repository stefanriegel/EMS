#!/usr/bin/env bash
set -e

# Read options from /data/options.json (written by HA Supervisor).
# Optional fields (schema type "str?") may be absent — // "" converts null/absent
# to empty string so backend env vars are always set (empty = disabled).
get_option() {
    jq -r --arg key "$1" '.[$key] // ""' /data/options.json
}

# --- Required hardware endpoints ---
# Only export if non-empty — the backend's config.py treats missing vars as
# "unconfigured" and enters degraded/setup-only mode.  Exporting "" would
# set the key in os.environ and bypass the KeyError-based detection.
_huawei_host=$(get_option 'huawei_host')
[ -n "$_huawei_host" ] && export HUAWEI_HOST="$_huawei_host"
export HUAWEI_PORT=$(get_option 'huawei_port')
export HUAWEI_MASTER_SLAVE_ID=$(get_option 'huawei_master_unit_id')
export HUAWEI_SLAVE_SLAVE_ID=$(get_option 'huawei_slave_unit_id')

_victron_host=$(get_option 'victron_host')
[ -n "$_victron_host" ] && export VICTRON_HOST="$_victron_host"
export VICTRON_PORT=$(get_option 'victron_port')

# --- Victron Modbus unit IDs (optional, defaults in VictronConfig) ---
_victron_sys_unit=$(get_option 'victron_system_unit_id')
[ -n "$_victron_sys_unit" ] && export VICTRON_SYSTEM_UNIT_ID="$_victron_sys_unit"
_victron_bat_unit=$(get_option 'victron_battery_unit_id')
[ -n "$_victron_bat_unit" ] && export VICTRON_BATTERY_UNIT_ID="$_victron_bat_unit"
_victron_vb_unit=$(get_option 'victron_vebus_unit_id')
[ -n "$_victron_vb_unit" ] && export VICTRON_VEBUS_UNIT_ID="$_victron_vb_unit"

_influxdb_url=$(get_option 'influxdb_url')
[ -n "$_influxdb_url" ] && export INFLUXDB_URL="$_influxdb_url"
_influxdb_token=$(get_option 'influxdb_token')
[ -n "$_influxdb_token" ] && export INFLUXDB_TOKEN="$_influxdb_token"
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
_tg_token=$(get_option 'telegram_bot_token')
[ -n "$_tg_token" ] && export TELEGRAM_BOT_TOKEN="$_tg_token"
_tg_chat=$(get_option 'telegram_chat_id')
[ -n "$_tg_chat" ] && export TELEGRAM_CHAT_ID="$_tg_chat"

# --- Optional: EMS web UI authentication ---
_admin_hash=$(get_option 'admin_password_hash')
[ -n "$_admin_hash" ] && export ADMIN_PASSWORD_HASH="$_admin_hash"
# JWT_SECRET is generated automatically on first startup and persisted to
# /config/.jwt_secret — no operator action required.

# --- Coordinator tuning (advanced, optional) ---
_hw_db=$(get_option 'huawei_deadband_w')
[ -n "$_hw_db" ] && export HUAWEI_DEADBAND_W="$_hw_db"
_vic_db=$(get_option 'victron_deadband_w')
[ -n "$_vic_db" ] && export VICTRON_DEADBAND_W="$_vic_db"
_ramp=$(get_option 'ramp_rate_w_per_cycle')
[ -n "$_ramp" ] && export RAMP_RATE_W_PER_CYCLE="$_ramp"
_min_hw=$(get_option 'min_soc_pct_huawei')
[ -n "$_min_hw" ] && export MIN_SOC_PCT_HUAWEI="$_min_hw"
_min_vic=$(get_option 'min_soc_pct_victron')
[ -n "$_min_vic" ] && export MIN_SOC_PCT_VICTRON="$_min_vic"

# --- Modul3 grid-fee tariff (optional) ---
_m3_ss=$(get_option 'modul3_surplus_start_min')
[ -n "$_m3_ss" ] && export MODUL3_SURPLUS_START_MIN="$_m3_ss"
_m3_se=$(get_option 'modul3_surplus_end_min')
[ -n "$_m3_se" ] && export MODUL3_SURPLUS_END_MIN="$_m3_se"
_m3_ds=$(get_option 'modul3_deficit_start_min')
[ -n "$_m3_ds" ] && export MODUL3_DEFICIT_START_MIN="$_m3_ds"
_m3_de=$(get_option 'modul3_deficit_end_min')
[ -n "$_m3_de" ] && export MODUL3_DEFICIT_END_MIN="$_m3_de"
_m3_sr=$(get_option 'modul3_surplus_rate_eur_kwh')
[ -n "$_m3_sr" ] && export MODUL3_SURPLUS_RATE_EUR_KWH="$_m3_sr"
_m3_dr=$(get_option 'modul3_deficit_rate_eur_kwh')
[ -n "$_m3_dr" ] && export MODUL3_DEFICIT_RATE_EUR_KWH="$_m3_dr"

# --- Feed-in tariff ---
_feed_in=$(get_option 'feed_in_rate_eur_kwh')
[ -n "$_feed_in" ] && export FEED_IN_RATE_EUR_KWH="$_feed_in"

# --- Seasonal strategy ---
_winter_months=$(get_option 'winter_months')
[ -n "$_winter_months" ] && export WINTER_MONTHS="$_winter_months"
_winter_boost=$(get_option 'winter_min_soc_boost_pct')
[ -n "$_winter_boost" ] && export WINTER_MIN_SOC_BOOST_PCT="$_winter_boost"

# --- Logging ---
export LOG_LEVEL=$(get_option 'log_level')

# EMS wizard config persisted to the HA config volume
export EMS_CONFIG_PATH="/config/ems_config.json"

exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
