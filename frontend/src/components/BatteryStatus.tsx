/**
 * BatteryStatus -- dual-battery status card replacing PoolOverview.
 *
 * Shows:
 *   - Combined pool SoC bar with pool status indicator (NORMAL/DEGRADED/OFFLINE)
 *   - Two side-by-side battery cards (Huawei + Victron), each with:
 *     - SoC bar, power value, role badge, setpoint, availability dot
 *   - Disconnected banner when WebSocket is not connected
 *
 * Roles are always read from pool (not devices) per backend WS contract.
 */
import type { PoolState, DevicesPayload } from "../types";

interface Props {
  pool: PoolState | null;
  devices: DevicesPayload | null;
  connected: boolean;
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

const statusColors: Record<string, string> = {
  NORMAL: "#22c55e",
  DEGRADED: "#f59e0b",
  OFFLINE: "#ef4444",
};

/** Formats a watt value as a human-readable power string. */
function formatPower(watts: number): string {
  const abs = Math.abs(watts);
  if (abs < 1000) return `${Math.round(abs)} W`;
  return `${(abs / 1000).toFixed(1)} kW`;
}

/** Returns a short power-direction annotation for the role badge.
 *  Shows actual battery activity when it differs from the EMS role label
 *  (e.g. Victron ESS self-consuming while coordinator says HOLDING). */
function powerAnnotation(powerW: number | null): string {
  if (powerW === null || Math.abs(powerW) < 10) return "";
  if (powerW < 0) return ` · ${formatPower(powerW)} out`;
  return ` · ${formatPower(powerW)} in`;
}

export function BatteryStatus({ pool, devices, connected }: Props) {
  const combinedSoc = pool?.combined_soc_pct ?? null;
  const poolStatus = pool?.pool_status ?? "OFFLINE";

  const huaweiSoc = pool?.huawei_soc_pct ?? null;
  const victronSoc = pool?.victron_soc_pct ?? null;
  const huaweiAvailable = pool?.huawei_available ?? false;
  const victronAvailable = pool?.victron_available ?? false;

  const huaweiRole = pool?.huawei_role ?? "";
  const victronRole = pool?.victron_role ?? "";

  const huaweiPower = devices?.huawei?.total_power_w ?? null;
  const victronPower = devices?.victron?.battery_power_w ?? null;

  const huaweiSetpoint = pool?.huawei_discharge_setpoint_w ?? null;
  const victronSetpoint = pool?.victron_discharge_setpoint_w ?? null;

  return (
    <section className="card battery-status" data-testid="battery-status-card">
      {!connected && (
        <div className="disconnected-banner">Warning: Disconnected -- data may be stale</div>
      )}

      {/* Pool header with combined SoC */}
      <div className="pool-header">
        <h2 className="card-title" style={{ margin: 0 }}>Battery Pool</h2>
        <span
          className="pool-status-dot"
          style={{ background: statusColors[poolStatus] ?? "#ef4444" }}
          title={`Pool: ${poolStatus}`}
        />
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem", color: "var(--text-secondary)" }}>
          {poolStatus}
        </span>
      </div>
      <p className="card-subtitle">94 kWh combined ESS</p>

      {/* Combined SoC bar */}
      <div className="soc-bar-container">
        <div className="soc-bar-label">
          <span>Combined SoC</span>
          <span className="soc-value">
            {combinedSoc !== null ? `${combinedSoc.toFixed(1)}%` : "N/A"}
          </span>
        </div>
        <div className="soc-bar-track">
          <div
            className="soc-bar-fill"
            style={{
              width: combinedSoc !== null ? `${Math.min(100, Math.max(0, combinedSoc))}%` : "0%",
            }}
          />
        </div>
      </div>

      {/* Two battery cards side-by-side */}
      <div className="battery-pair">
        {/* Huawei card */}
        <div className="battery-card battery-card--huawei" data-testid="huawei-battery">
          <div className="battery-header">
            <span
              className="avail-dot"
              style={{ background: huaweiAvailable ? "#22c55e" : "#ef4444" }}
              title={`Huawei ${huaweiAvailable ? "online" : "offline"}`}
            />
            <span className="battery-name">Huawei</span>
          </div>

          {/* SoC bar */}
          <div className="soc-bar-container" style={{ marginBottom: "0.5rem" }}>
            <div className="soc-bar-label">
              <span>SoC</span>
              <span className="soc-value">
                {huaweiSoc !== null ? `${huaweiSoc.toFixed(1)}%` : "N/A"}
              </span>
            </div>
            <div className="soc-bar-track">
              <div
                className="soc-bar-fill"
                style={{
                  width: huaweiSoc !== null ? `${Math.min(100, Math.max(0, huaweiSoc))}%` : "0%",
                  background: "linear-gradient(90deg, var(--color-huawei), #fbbf24)",
                }}
              />
            </div>
          </div>

          {/* Metrics row */}
          <div className="battery-metrics">
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "0.85rem", color: "var(--text-primary)" }}>
              {huaweiPower !== null ? formatPower(huaweiPower) : "N/A"}
            </span>
            <span
              className="role-badge"
              style={{ background: roleColors[huaweiRole] ?? "#6b7280" }}
            >
              {roleLabels[huaweiRole] ?? "---"}{powerAnnotation(huaweiPower)}
            </span>
          </div>

          {/* Setpoint */}
          <div style={{ marginTop: "0.35rem", fontSize: "0.72rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
            Setpoint: {huaweiSetpoint !== null ? `${huaweiSetpoint} W` : "N/A"}
          </div>
        </div>

        {/* Victron card */}
        <div className="battery-card battery-card--victron" data-testid="victron-battery">
          <div className="battery-header">
            <span
              className="avail-dot"
              style={{ background: victronAvailable ? "#22c55e" : "#ef4444" }}
              title={`Victron ${victronAvailable ? "online" : "offline"}`}
            />
            <span className="battery-name">Victron</span>
          </div>

          {/* SoC bar */}
          <div className="soc-bar-container" style={{ marginBottom: "0.5rem" }}>
            <div className="soc-bar-label">
              <span>SoC</span>
              <span className="soc-value">
                {victronSoc !== null ? `${victronSoc.toFixed(1)}%` : "N/A"}
              </span>
            </div>
            <div className="soc-bar-track">
              <div
                className="soc-bar-fill"
                style={{
                  width: victronSoc !== null ? `${Math.min(100, Math.max(0, victronSoc))}%` : "0%",
                  background: "linear-gradient(90deg, var(--color-victron), #a78bfa)",
                }}
              />
            </div>
          </div>

          {/* Metrics row */}
          <div className="battery-metrics">
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "0.85rem", color: "var(--text-primary)" }}>
              {victronPower !== null ? formatPower(victronPower) : "N/A"}
            </span>
            <span
              className="role-badge"
              style={{ background: roleColors[victronRole] ?? "#6b7280" }}
            >
              {roleLabels[victronRole] ?? "---"}{powerAnnotation(victronPower)}
            </span>
          </div>

          {/* Setpoint */}
          <div style={{ marginTop: "0.35rem", fontSize: "0.72rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
            Setpoint: {victronSetpoint !== null ? `${victronSetpoint} W` : "N/A"}
          </div>
        </div>
      </div>
    </section>
  );
}
