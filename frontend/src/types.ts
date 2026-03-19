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
}

export interface OptimizationPayload {
  slots: ChargeSlotPayload[];
  reasoning: OptimizationReasoningPayload;
  computed_at: string;
  stale: boolean;
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
// WebSocket push payload (/api/ws/state)
// ---------------------------------------------------------------------------

export interface WsPayload {
  pool: PoolState | null;
  devices: DevicesPayload;
  tariff: TariffPayload;
  optimization: OptimizationPayload | null;
  evcc: EvccPayload | null;
  ha_mqtt_connected: boolean;
}
