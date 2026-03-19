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

export type ControlState = "IDLE" | "CHARGE" | "DISCHARGE" | "HOLD";

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
// WebSocket push payload (/api/ws/state)
// ---------------------------------------------------------------------------

export interface WsPayload {
  pool: PoolState | null;
  devices: DevicesPayload;
  tariff: TariffPayload;
}
