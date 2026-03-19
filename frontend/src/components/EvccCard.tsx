/**
 * EvccCard — EV charging control (EVCC) status panel.
 *
 * Shows battery mode, discharge-lock indicator, loadpoint mode,
 * charge power, vehicle SoC, and connection / charging status.
 * Reads from the `evcc` key of the WebSocket push payload.
 */
import type { EvccPayload } from "../types";

interface Props {
  evcc: EvccPayload | null;
  controlState: string;
  /** True when the HA MQTT broker connection is live */
  haMqttConnected?: boolean;
}

const BATTERY_MODE_LABELS: Record<string, string> = {
  normal: "Normal",
  hold: "Hold",
  boost: "Boost",
};

const BATTERY_MODE_COLORS: Record<string, string> = {
  normal: "#22c55e",
  hold: "#f59e0b",
  boost: "#06b6d4",
};

function fmt(value: number | null | undefined, unit: string, decimals = 1): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(decimals)} ${unit}`;
}

export function EvccCard({ evcc, controlState, haMqttConnected }: Props) {
  const isLocked = controlState === "DISCHARGE_LOCKED";

  const rawMode = evcc?.battery_mode ?? "normal";
  const modeName = BATTERY_MODE_LABELS[rawMode] ?? rawMode;
  const modeColor = BATTERY_MODE_COLORS[rawMode] ?? "#6b7280";

  const lpMode = evcc?.loadpoint_mode ?? "—";
  const power = evcc?.charge_power_w ?? 0;
  const vehicleSoc = evcc?.vehicle_soc_pct ?? null;
  const charging = evcc?.charging ?? false;
  const connected = evcc?.connected ?? false;

  return (
    <section className="card evcc-card">
      <h2 className="card-title">EVCC</h2>
      <p className="card-subtitle">EV Charging Control</p>

      {isLocked && (
        <div className="evcc-lock-badge">
          🔒 Discharge Locked
        </div>
      )}

      {/* Battery mode row */}
      <div className="evcc-mode-row">
        <span className="evcc-mode-label">Battery mode</span>
        <span
          className="control-badge"
          style={{ background: modeColor }}
        >
          {modeName}
        </span>
      </div>

      {/* Metric grid */}
      <div className="evcc-metrics">
        <div className="evcc-metric">
          <span className="metric-label">Loadpoint</span>
          <span className="metric-value evcc-lp-mode">{lpMode}</span>
        </div>
        <div className="evcc-metric">
          <span className="metric-label">Charge power</span>
          <span className="metric-value">{fmt(power, "W", 0)}</span>
        </div>
        <div className="evcc-metric">
          <span className="metric-label">Vehicle SoC</span>
          <span className="metric-value">
            {vehicleSoc !== null ? `${vehicleSoc.toFixed(1)} %` : "—"}
          </span>
        </div>
      </div>

      {/* Status row */}
      <div className="availability-row" style={{ marginTop: "0.75rem" }}>
        <span className="avail-label">EV</span>
        <span
          className="avail-dot"
          style={{ background: connected ? "#22c55e" : "#6b7280" }}
          title={connected ? "EV connected" : "No EV connected"}
        />
        <span className="avail-name">{connected ? "Connected" : "Disconnected"}</span>
        <span
          className="avail-dot"
          style={{ background: charging ? "#22c55e" : "#6b7280" }}
          title={charging ? "Charging" : "Not charging"}
        />
        <span className="avail-name">{charging ? "Charging" : "Idle"}</span>
      </div>

      {/* HA MQTT indicator */}
      {haMqttConnected !== undefined && (
        <div className="availability-row">
          <span className="avail-label">HA MQTT</span>
          <span
            className="avail-dot"
            style={{ background: haMqttConnected ? "#22c55e" : "#ef4444" }}
            title={haMqttConnected ? "HA MQTT connected" : "HA MQTT disconnected"}
          />
          <span className="avail-name">{haMqttConnected ? "Connected" : "Offline"}</span>
        </div>
      )}
    </section>
  );
}
