/**
 * OptimizationCard — tonight's charge schedule from the optimiser.
 *
 * Shows:
 *   - Stale badge when schedule.stale is true
 *   - Reasoning text and cost estimate
 *   - Per-slot rows: battery, local time window, target SoC %, grid power
 *
 * Renders "No schedule available" when optimization is null (scheduler not
 * yet started, or no active schedule computed).
 */
import type { OptimizationPayload } from "../types";

interface Props {
  optimization: OptimizationPayload | null;
}

function localTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function OptimizationCard({ optimization }: Props) {
  return (
    <section className="card optimization-card">
      <h2 className="card-title">Tonight's Schedule</h2>

      {!optimization ? (
        <p className="unavailable">No schedule available</p>
      ) : (
        <>
          {optimization.stale && (
            <div className="opt-stale-badge">Schedule may be outdated</div>
          )}

          <p className="opt-reasoning">{optimization.reasoning.text}</p>

          <p className="opt-cost">
            Est. cost: €{optimization.reasoning.cost_estimate_eur.toFixed(2)}
            <span className="opt-cost-detail">
              &nbsp;·&nbsp;{optimization.reasoning.charge_energy_kwh.toFixed(1)} kWh
            </span>
          </p>

          <div className="opt-slots">
            {optimization.slots.length === 0 ? (
              <p className="opt-no-slots">No charge windows scheduled</p>
            ) : (
              optimization.slots.map((slot, i) => (
                <div key={i} className="opt-slot-row">
                  <span className="opt-slot-battery">{slot.battery}</span>
                  <span className="opt-slot-window">
                    {localTime(slot.start_utc)}–{localTime(slot.end_utc)}
                  </span>
                  <span className="opt-slot-target">{slot.target_soc_pct.toFixed(0)}%</span>
                  <span className="opt-slot-power">{slot.grid_charge_power_w} W</span>
                </div>
              ))
            )}
          </div>

          {optimization.slots.length > 0 && (
            <div className="opt-timeline" data-testid="opt-timeline">
              <div className="opt-timeline-axis">
                {[0, 4, 8, 12, 16, 20, 24].map((h) => (
                  <span
                    key={h}
                    className="opt-timeline-hour"
                    style={{ left: `${(h / 24) * 100}%` }}
                  >
                    {String(h % 24).padStart(2, "0")}
                  </span>
                ))}
              </div>
              <div className="opt-timeline-bar">
                {optimization.slots.map((slot, i) => {
                  const startH =
                    new Date(slot.start_utc).getHours() +
                    new Date(slot.start_utc).getMinutes() / 60;
                  const endH =
                    new Date(slot.end_utc).getHours() +
                    new Date(slot.end_utc).getMinutes() / 60;
                  const left = (startH / 24) * 100;
                  const width = ((endH - startH) / 24) * 100;
                  const isHuawei = slot.battery.toLowerCase().includes("huawei");
                  const color = isHuawei ? "var(--color-huawei)" : "var(--color-victron)";
                  const row = isHuawei ? 0 : 1;
                  return (
                    <div
                      key={i}
                      className="opt-timeline-slot"
                      style={{
                        left: `${left}%`,
                        width: `${Math.max(width, 2)}%`,
                        background: color,
                        top: `${row * 50}%`,
                        height: "50%",
                      }}
                      title={`${slot.battery} ${localTime(slot.start_utc)}-${localTime(slot.end_utc)} ${slot.target_soc_pct}%`}
                    />
                  );
                })}
              </div>
            </div>
          )}

          {optimization.forecast_comparison != null && (
            <div className="opt-forecast-comparison" data-testid="opt-forecast-comparison">
              <span className="opt-fc-label">Heat Pump Forecast</span>
              <span className="opt-fc-values">
                Predicted {optimization.forecast_comparison.predicted_kwh.toFixed(1)} kWh
                {" · "}
                Actual {optimization.forecast_comparison.actual_kwh.toFixed(1)} kWh
              </span>
              <span
                data-testid="opt-forecast-error-badge"
                className={`opt-fc-badge opt-fc-badge--${
                  optimization.forecast_comparison.error_pct < 10
                    ? "green"
                    : optimization.forecast_comparison.error_pct < 20
                    ? "amber"
                    : "red"
                }`}
              >
                Error {optimization.forecast_comparison.error_pct.toFixed(1)}%
              </span>
            </div>
          )}
        </>
      )}
    </section>
  );
}
