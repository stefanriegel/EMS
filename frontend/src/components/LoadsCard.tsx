/**
 * LoadsCard — displays heat pump and home energy loads sourced from HA REST API.
 *
 * Follows the S01 consumer visual language: `.card` container with
 * `var(--color-home)` accent and availability badge.
 *
 * Null-state: when `loads` is null (HA client absent or unconfigured),
 * the card still renders — it shows "—" and a grey "Unavailable" badge.
 * The card is never hidden from the layout.
 *
 * Groups:
 *   - Heat Pump:   power (W), COP
 *   - Temperatures: outdoor, Vorlauf (flow), Rücklauf (return)
 *   - Consumption:  Hausverbrauch, Steuerbare, Base
 */
import type { LoadsPayload } from "../types";

interface Props {
  loads: LoadsPayload | null;
}

function fmt_power(val: number | null | undefined): string {
  return val != null ? `${val} W` : "—";
}

function fmt_temp(val: number | null | undefined): string {
  return val != null ? `${val.toFixed(1)} °C` : "—";
}

function fmt_cop(val: number | null | undefined): string {
  return val != null ? val.toFixed(2) : "—";
}

export function LoadsCard({ loads }: Props) {
  const isAvailable = loads?.available === true;

  return (
    <div className="card loads-card" data-testid="loads-card">
      <div
        className="card-title"
        style={{ color: "var(--color-home)" }}
      >
        ⚡ Loads
      </div>
      <div className="card-subtitle">Heat pump · sourced from HA REST API</div>

      <div className="loads-availability">
        {isAvailable ? (
          <span
            className="badge badge--available"
            style={{ color: "var(--color-pv)", borderColor: "var(--color-pv)" }}
          >
            Available
          </span>
        ) : (
          <span
            className="badge badge--unavailable"
            style={{ color: "var(--text-muted)", borderColor: "var(--text-muted)" }}
          >
            Unavailable
          </span>
        )}
      </div>

      {/* ── Heat Pump ──────────────────────────────────────────────── */}
      <div className="loads-section-label">Heat Pump</div>
      <div className="loads-row">
        <span className="loads-label">Power</span>
        <span className="loads-value" data-testid="loads-heat-pump">
          {fmt_power(loads?.heat_pump_power_w)}
        </span>
      </div>
      <div className="loads-row">
        <span className="loads-label">COP</span>
        <span className="loads-value" data-testid="loads-cop">
          {fmt_cop(loads?.cop)}
        </span>
      </div>

      {/* ── Temperatures ───────────────────────────────────────────── */}
      <div className="loads-section-label">Temperatures</div>
      <div className="loads-row">
        <span className="loads-label">Outdoor</span>
        <span className="loads-value" data-testid="loads-outdoor-temp">
          {fmt_temp(loads?.outdoor_temp_c)}
        </span>
      </div>
      <div className="loads-row">
        <span className="loads-label">Vorlauf</span>
        <span className="loads-value" data-testid="loads-flow-temp">
          {fmt_temp(loads?.flow_temp_c)}
        </span>
      </div>
      <div className="loads-row">
        <span className="loads-label">Rücklauf</span>
        <span className="loads-value" data-testid="loads-return-temp">
          {fmt_temp(loads?.return_temp_c)}
        </span>
      </div>

      {/* ── Consumption ────────────────────────────────────────────── */}
      <div className="loads-section-label">Consumption</div>
      <div className="loads-row">
        <span className="loads-label">Hausverbrauch</span>
        <span className="loads-value" data-testid="loads-hausverbrauch">
          {fmt_power(loads?.hausverbrauch_w)}
        </span>
      </div>
      <div className="loads-row">
        <span className="loads-label">Steuerbare</span>
        <span className="loads-value" data-testid="loads-steuerbare">
          {fmt_power(loads?.steuerbare_w)}
        </span>
      </div>
      <div className="loads-row">
        <span className="loads-label">Base</span>
        <span className="loads-value" data-testid="loads-base">
          {fmt_power(loads?.base_w)}
        </span>
      </div>
    </div>
  );
}
