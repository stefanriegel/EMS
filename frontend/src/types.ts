/**
 * TypeScript types mirroring the backend JSON schemas.
 *
 * Sources:
 *   - backend/unified_model.py  → PoolState
 *   - backend/api.py GET /api/devices → DevicesPayload
 *   - backend/api.py WebSocket /api/ws/state → WsPayload
 */

// ---------------------------------------------------------------------------
// Pool state (UnifiedPoolState dataclass)
// ---------------------------------------------------------------------------

export type ControlState = "IDLE" | "CHARGE" | "DISCHARGE" | "HOLD" | "GRID_CHARGE" | "DISCHARGE_LOCKED";

export interface PoolState {
  combined_soc_pct: number;
  huawei_soc_pct: number;
  victron_soc_pct: number;
  huawei_available: boolean;
  victron_available: boolean;
  control_state: ControlState;
  huawei_discharge_setpoint_w: number;
  victron_discharge_setpoint_w: number;
  combined_power_w: number;
  huawei_charge_headroom_w: number;
  victron_charge_headroom_w: number;
  timestamp: number;
  grid_charge_slot_active: boolean;
  evcc_battery_mode: string;
  huawei_role: string;
  victron_role: string;
  pool_status: string;
  huawei_effective_min_soc_pct: number;
  victron_effective_min_soc_pct: number;
  cross_charge_active: boolean;
  cross_charge_waste_wh: number;
  cross_charge_episode_count: number;
}

// ---------------------------------------------------------------------------
// Decision entry (from /api/decisions)
// ---------------------------------------------------------------------------

export interface DecisionEntry {
  timestamp: string;
  trigger: string;
  huawei_role: string;
  victron_role: string;
  p_target_w: number;
  huawei_allocation_w: number;
  victron_allocation_w: number;
  pool_status: string;
  reasoning: string;
}

// ---------------------------------------------------------------------------
// Device snapshot (GET /api/devices)
// ---------------------------------------------------------------------------

export interface HuaweiSnapshot {
  available: boolean;
  pack1_soc_pct: number;
  pack1_power_w: number;
  pack2_soc_pct: number | null;
  pack2_power_w: number | null;
  total_soc_pct: number;
  total_power_w: number;
  max_charge_w: number;
  max_discharge_w: number;
  master_pv_power_w: number | null;
  slave_pv_power_w: null; // always null in M001
}

export interface VictronSnapshot {
  available: boolean;
  soc_pct: number;
  battery_power_w: number;
  l1_power_w: number;
  l2_power_w: number;
  l3_power_w: number;
  l1_voltage_v: number;
  l2_voltage_v: number;
  l3_voltage_v: number;
  // System-level Venus OS totals
  grid_power_w: number | null;       // positive = importing, negative = exporting
  grid_l1_power_w: number | null;    // per-phase grid import/export at L1
  grid_l2_power_w: number | null;
  grid_l3_power_w: number | null;
  consumption_w: number | null;      // total house load across all phases
  pv_on_grid_w: number | null;       // total AC-coupled PV across all inverters
}

export interface DevicesPayload {
  huawei: HuaweiSnapshot;
  victron: VictronSnapshot;
}

// ---------------------------------------------------------------------------
// Tariff snapshot (embedded in WS payload)
// ---------------------------------------------------------------------------

export interface TariffPayload {
  effective_rate_eur_kwh: number | null;
  octopus_rate_eur_kwh: number | null;
  modul3_rate_eur_kwh: number | null;
  source?: "evcc" | "live" | "hardcoded";
}

// ---------------------------------------------------------------------------
// Optimization / charge schedule snapshot (embedded in WS payload)
// ---------------------------------------------------------------------------

export interface ChargeSlotPayload {
  battery: string;
  target_soc_pct: number;
  start_utc: string;
  end_utc: string;
  grid_charge_power_w: number;
}

export interface OptimizationReasoningPayload {
  text: string;
  tomorrow_solar_kwh: number;
  expected_consumption_kwh: number;
  charge_energy_kwh: number;
  cost_estimate_eur: number;
  evopt_status?: string;
}

export interface OptimizationPayload {
  slots: ChargeSlotPayload[];
  reasoning: OptimizationReasoningPayload;
  computed_at: string;
  stale: boolean;
  forecast_comparison?: { predicted_kwh: number; actual_kwh: number; error_pct: number } | null;
  day_plans?: DayPlanPayload[] | null;
}

// ---------------------------------------------------------------------------
// Multi-day forecast (GET /api/optimization/forecast)
// ---------------------------------------------------------------------------

export interface ForecastDayPayload {
  date: string;
  day_index: number;
  solar_kwh: number;
  consumption_kwh: number;
  net_kwh: number;
  confidence: number;
  charge_target_kwh: number;
  advisory: boolean;
}

export interface ForecastPayload {
  days: ForecastDayPayload[];
}

// ---------------------------------------------------------------------------
// Day plan in schedule response (extension of OptimizationPayload)
// ---------------------------------------------------------------------------

export interface DayPlanPayload {
  date: string;
  day_index: number;
  solar_kwh: number;
  consumption_kwh: number;
  net_kwh: number;
  confidence: number;
  charge_target_kwh: number;
  advisory: boolean;
  slots: ChargeSlotPayload[];
}

// ---------------------------------------------------------------------------
// EVCC snapshot (embedded in WS payload)
// ---------------------------------------------------------------------------

export interface EvccPayload {
  battery_mode: string;
  loadpoint_mode: string;
  charge_power_w: number;
  vehicle_soc_pct: number | null;
  charging: boolean;
  connected: boolean;
}

// ---------------------------------------------------------------------------
// Loads snapshot (embedded in WS payload — sourced from HA REST API)
// ---------------------------------------------------------------------------

export interface LoadsPayload {
  heat_pump_power_w: number | null;
  available: boolean;
  cop?: number | null;
  outdoor_temp_c?: number | null;
  flow_temp_c?: number | null;
  return_temp_c?: number | null;
  hausverbrauch_w?: number | null;
  steuerbare_w?: number | null;
  base_w?: number | null;
}

// ---------------------------------------------------------------------------
// WebSocket push payload (/api/ws/state)
// ---------------------------------------------------------------------------

export interface WsPayload {
  pool: PoolState | null;
  devices: DevicesPayload;
  tariff: TariffPayload;
  optimization: OptimizationPayload | null;
  evcc: EvccPayload | null;
  ha_mqtt_connected: boolean;
  loads: LoadsPayload | null;
}
