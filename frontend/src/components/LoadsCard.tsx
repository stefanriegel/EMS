/**
 * LoadsCard — displays heat pump power sourced from the HA REST API.
 *
 * Follows the S01 consumer visual language: `.card` container with
 * `var(--color-home)` accent and availability badge.
 *
 * Null-state: when `loads` is null (HA client absent or unconfigured),
 * the card still renders — it shows "—" and a grey "Unavailable" badge.
 * The card is never hidden from the layout.
 */
import type { LoadsPayload } from "../types";

interface Props {
  loads: LoadsPayload | null;
}

export function LoadsCard({ loads }: Props) {
  const powerValue =
    loads?.heat_pump_power_w != null ? `${loads.heat_pump_power_w} W` : "—";

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

      <div className="loads-row">
        <span className="loads-label">Heat Pump</span>
        <span className="loads-value">{powerValue}</span>
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
    </div>
  );
}
