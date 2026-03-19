/**
 * DeviceDetail — per-device drill-down for Huawei LUNA2000 and Victron MPII.
 *
 * All null/undefined fields render as "N/A" (never blank, never "undefined").
 * slave_pv_power_w is always shown as "N/A" — backend never provides it for M001.
 */
import type { DevicesPayload } from "../types";

interface Props {
  devices: DevicesPayload | null;
}

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

export function DeviceDetail({ devices }: Props) {
  const hw = devices?.huawei ?? null;
  const vc = devices?.victron ?? null;

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
        </div>
        <Row label="Pack 1 SoC" value={pct(hw?.pack1_soc_pct)} />
        <Row label="Pack 1 Power" value={n(hw?.pack1_power_w)} />
        <Row label="Pack 2 SoC" value={pct(hw?.pack2_soc_pct)} />
        <Row label="Pack 2 Power" value={n(hw?.pack2_power_w)} />
        <Row label="Total SoC" value={pct(hw?.total_soc_pct)} />
        <Row label="Total Power" value={n(hw?.total_power_w)} />
        <Row label="Max Charge" value={n(hw?.max_charge_w)} />
        <Row label="Max Discharge" value={n(hw?.max_discharge_w)} />
        <Row label="Master PV Power" value={n(hw?.master_pv_power_w)} />
        <Row label="Slave PV Power" value="N/A" />
      </div>

      {/* Victron MultiPlus-II */}
      <div className="device-section">
        <div className="device-header">
          <span
            className="avail-dot"
            style={{ background: vc?.available ? "#22c55e" : "#ef4444" }}
          />
          <h3 className="device-title">Victron MultiPlus-II</h3>
        </div>
        <Row label="Battery SoC" value={pct(vc?.soc_pct)} />
        <Row label="Battery Power" value={n(vc?.battery_power_w, "W", 0)} />
        <Row label="L1 Power" value={n(vc?.l1_power_w)} />
        <Row label="L1 Voltage" value={n(vc?.l1_voltage_v, "V", 1)} />
        <Row label="L2 Power" value={n(vc?.l2_power_w)} />
        <Row label="L2 Voltage" value={n(vc?.l2_voltage_v, "V", 1)} />
        <Row label="L3 Power" value={n(vc?.l3_power_w)} />
        <Row label="L3 Voltage" value={n(vc?.l3_voltage_v, "V", 1)} />
      </div>
    </section>
  );
}
