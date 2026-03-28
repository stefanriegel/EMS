/**
 * PoolOverview — combined 94 kWh battery pool status.
 *
 * Shows:
 *   - Combined SoC as a CSS-only percentage bar
 *   - Combined power (positive = charging, negative = discharging)
 *   - Control state badge (IDLE / CHARGE / DISCHARGE / HOLD)
 *   - Driver availability dots (green / red)
 *   - "⚠ Disconnected" banner when connected=false
 */
import type { PoolState } from "../types";

interface Props {
  pool: PoolState | null;
  connected: boolean;
}

const stateColors: Record<string, string> = {
  IDLE: "#6b7280",
  CHARGE: "#22c55e",
  DISCHARGE: "#f59e0b",
  HOLD: "#3b82f6",
  GRID_CHARGE: "#06b6d4",
};

function fmt(value: number | null | undefined, unit: string, decimals = 1): string {
  if (value === null || value === undefined) return "N/A";
  return `${value.toFixed(decimals)} ${unit}`;
}

export function PoolOverview({ pool, connected }: Props) {
  const soc = pool?.combined_soc_pct ?? null;
  const power = pool?.combined_power_w ?? null;
  const state = pool?.control_state ?? "IDLE";
  const stateColor = stateColors[state] ?? "#6b7280";

  // Detect supervisory mode
  const isSupervisory = pool && "control_mode" in pool && (pool as unknown as Record<string, string>)["control_mode"] === "supervisory";
  const controlModeLabel = isSupervisory ? "Supervisory" : state;

  return (
    <section className="card pool-overview">
      {!connected && (
        <div className="disconnected-banner">⚠ Disconnected — data may be stale</div>
      )}

      <h2 className="card-title">Pool Overview</h2>
      <p className="card-subtitle">94 kWh combined ESS</p>

      {/* SoC bar */}
      <div className="soc-bar-container">
        <div className="soc-bar-label">
          <span>SoC</span>
          <span className="soc-value">{soc !== null ? `${soc.toFixed(1)}%` : "N/A"}</span>
        </div>
        <div className="soc-bar-track">
          <div
            className="soc-bar-fill"
            style={{ width: soc !== null ? `${Math.min(100, Math.max(0, soc))}%` : "0%" }}
          />
        </div>
      </div>

      {/* Power + state */}
      <div className="pool-metrics">
        <div className="metric">
          <span className="metric-label">Power</span>
          <span className="metric-value">{fmt(power, "W", 0)}</span>
        </div>
        <div className="metric">
          <span className="metric-label">Mode</span>
          <span
            className="control-badge"
            style={{ background: isSupervisory ? "#8b5cf6" : stateColor }}
          >
            {controlModeLabel}
          </span>
        </div>
      </div>

      {/* Individual SoC */}
      <div className="pool-metrics" style={{ marginTop: "0.5rem" }}>
        <div className="metric">
          <span className="metric-label">Huawei SoC</span>
          <span className="metric-value">{fmt(pool?.huawei_soc_pct ?? null, "%")}</span>
        </div>
        <div className="metric">
          <span className="metric-label">Victron SoC</span>
          <span className="metric-value">{fmt(pool?.victron_soc_pct ?? null, "%")}</span>
        </div>
      </div>

      {/* Availability dots */}
      <div className="availability-row">
        <span className="avail-label">Drivers</span>
        <span
          className="avail-dot"
          style={{ background: pool?.huawei_available ? "#22c55e" : "#ef4444" }}
          title={`Huawei ${pool?.huawei_available ? "online" : "offline"}`}
        />
        <span className="avail-name">Huawei</span>
        <span
          className="avail-dot"
          style={{ background: pool?.victron_available ? "#22c55e" : "#ef4444" }}
          title={`Victron ${pool?.victron_available ? "online" : "offline"}`}
        />
        <span className="avail-name">Victron</span>
      </div>
    </section>
  );
}
