/**
 * DeviceDetail -- per-device drill-down for Huawei LUNA2000 and Victron MPII.
 *
 * Shows role + setpoint + measured power prominently at the top, with
 * hardware details collapsed by default in a native <details> element.
 *
 * All null/undefined fields render as "N/A" (never blank, never "undefined").
 */
import type { DevicesPayload, PoolState } from "../types";

interface Props {
  devices: DevicesPayload | null;
  pool: PoolState | null;
}

const roleColors: Record<string, string> = {
  PRIMARY_DISCHARGE: "#f59e0b",
  SECONDARY_DISCHARGE: "#fbbf24",
  CHARGING: "#22c55e",
  HOLDING: "#3b82f6",
  GRID_CHARGE: "#06b6d4",
};

const roleLabels: Record<string, string> = {
  PRIMARY_DISCHARGE: "Primary",
  SECONDARY_DISCHARGE: "Secondary",
  CHARGING: "Charging",
  HOLDING: "Holding",
  GRID_CHARGE: "Grid Charge",
};

/** Safely format a nullable number with unit. */
function n(value: number | null | undefined, unit = "W", decimals = 0): string {
  if (value === null || value === undefined) return "N/A";
  return `${value.toFixed(decimals)} ${unit}`;
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "N/A";
  return `${value.toFixed(1)} %`;
}

interface RowProps {
  label: string;
  value: string;
}

function Row({ label, value }: RowProps) {
  return (
    <div className="detail-row">
      <span className="detail-label">{label}</span>
      <span className="detail-value">{value}</span>
    </div>
  );
}

export function DeviceDetail({ devices, pool }: Props) {
  const hw = devices?.huawei ?? null;
  const vc = devices?.victron ?? null;

  const huaweiRole = pool?.huawei_role ?? "";
  const victronRole = pool?.victron_role ?? "";
  const huaweiSetpoint = pool?.huawei_discharge_setpoint_w ?? null;
  const victronSetpoint = pool?.victron_discharge_setpoint_w ?? null;

  return (
    <section className="card device-detail">
      <h2 className="card-title">Device Detail</h2>

      {/* Huawei LUNA2000 */}
      <div className="device-section">
        <div className="device-header">
          <span
            className="avail-dot"
            style={{ background: hw?.available ? "#22c55e" : "#ef4444" }}
          />
          <h3 className="device-title">Huawei LUNA2000</h3>
          <span
            className="role-badge"
            style={{ background: roleColors[huaweiRole] ?? "#6b7280" }}
          >
            {roleLabels[huaweiRole] ?? "---"}
          </span>
        </div>

        <Row label="Setpoint" value={huaweiSetpoint !== null ? `${huaweiSetpoint} W` : "N/A"} />
        <Row label="Measured Power" value={n(hw?.total_power_w)} />

        <details className="device-collapse">
          <summary>Hardware Details</summary>
          <div className="device-collapse-content">
            <Row label="Pack 1 SoC" value={pct(hw?.pack1_soc_pct)} />
            <Row label="Pack 1 Power" value={n(hw?.pack1_power_w)} />
            <Row label="Pack 2 SoC" value={pct(hw?.pack2_soc_pct)} />
            <Row label="Pack 2 Power" value={n(hw?.pack2_power_w)} />
            <Row label="Total SoC" value={pct(hw?.total_soc_pct)} />
            <Row label="Max Charge" value={n(hw?.max_charge_w)} />
            <Row label="Max Discharge" value={n(hw?.max_discharge_w)} />
            <Row label="Master PV Power" value={n(hw?.master_pv_power_w)} />
            <Row label="Slave PV Power" value="N/A" />
          </div>
        </details>
      </div>

      {/* Victron MultiPlus-II */}
      <div className="device-section">
        <div className="device-header">
          <span
            className="avail-dot"
            style={{ background: vc?.available ? "#22c55e" : "#ef4444" }}
          />
          <h3 className="device-title">Victron MultiPlus-II</h3>
          <span
            className="role-badge"
            style={{ background: roleColors[victronRole] ?? "#6b7280" }}
          >
            {roleLabels[victronRole] ?? "---"}
          </span>
        </div>

        <Row label="Setpoint" value={victronSetpoint !== null ? `${victronSetpoint} W` : "N/A"} />
        <Row label="Measured Power" value={n(vc?.battery_power_w, "W", 0)} />

        <details className="device-collapse">
          <summary>Hardware Details</summary>
          <div className="device-collapse-content">
            <Row label="Battery SoC" value={pct(vc?.soc_pct)} />
            <Row label="L1 Power" value={n(vc?.l1_power_w)} />
            <Row label="L1 Voltage" value={n(vc?.l1_voltage_v, "V", 1)} />
            <Row label="L2 Power" value={n(vc?.l2_power_w)} />
            <Row label="L2 Voltage" value={n(vc?.l2_voltage_v, "V", 1)} />
            <Row label="L3 Power" value={n(vc?.l3_power_w)} />
            <Row label="L3 Voltage" value={n(vc?.l3_voltage_v, "V", 1)} />
            <Row label="Grid Power" value={n(vc?.grid_power_w, "W", 0)} />
            <Row label="Grid L1" value={n(vc?.grid_l1_power_w, "W", 0)} />
            <Row label="Grid L2" value={n(vc?.grid_l2_power_w, "W", 0)} />
            <Row label="Grid L3" value={n(vc?.grid_l3_power_w, "W", 0)} />
            <Row label="Total PV" value={n(vc?.pv_on_grid_w, "W", 0)} />
            <Row label="Consumption" value={n(vc?.consumption_w, "W", 0)} />
          </div>
        </details>
      </div>
    </section>
  );
}
