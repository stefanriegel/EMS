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
import type { OptimizationPayload, DayPlanPayload, PoolState } from "../types";

interface Props {
  optimization: OptimizationPayload | null;
  pool?: PoolState | null;
}

function localTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function OptimizationCard({ optimization, pool }: Props) {
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

          {optimization.reasoning.tomorrow_solar_kwh > 0 && (
            <div data-testid="opt-solar-forecast" className="opt-solar-forecast">
              ☀️ Tomorrow: {optimization.reasoning.tomorrow_solar_kwh.toFixed(1)} kWh
            </div>
          )}

          {optimization.reasoning.evopt_status && (
            <span data-testid="opt-evopt-badge" className="opt-evopt-badge">
              {optimization.reasoning.evopt_status === 'Optimal' ? 'EVopt ✓' : optimization.reasoning.evopt_status}
            </span>
          )}

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

          {optimization.day_plans && optimization.day_plans.length > 0 && (
            <details className="opt-day-plans">
              <summary>Multi-Day Outlook</summary>
              {optimization.day_plans.map((dp: DayPlanPayload) => {
                const dayLabel =
                  dp.day_index === 0
                    ? "Today"
                    : dp.day_index === 1
                    ? "Tomorrow"
                    : new Date(dp.date + "T12:00:00").toLocaleDateString([], {
                        weekday: "short",
                      });
                const isPositive = dp.net_kwh >= 0;
                return (
                  <div key={dp.date} className="opt-dayplan-row">
                    <span className="opt-dayplan-label">{dayLabel}</span>
                    <span className="opt-dayplan-metric">
                      {dp.solar_kwh.toFixed(1)} kWh
                    </span>
                    <span className="opt-dayplan-metric">
                      {dp.consumption_kwh.toFixed(1)} kWh
                    </span>
                    <span
                      className={`opt-dayplan-metric ${
                        isPositive
                          ? "forecast-net--surplus"
                          : "forecast-net--deficit"
                      }`}
                    >
                      {dp.net_kwh.toFixed(1)} kWh
                    </span>
                    {dp.charge_target_kwh > 0 && (
                      <span className="opt-dayplan-metric">
                        {dp.charge_target_kwh.toFixed(1)} kWh
                      </span>
                    )}
                    {dp.advisory && (
                      <span className="opt-dayplan-advisory">Advisory</span>
                    )}
                    <span className="forecast-confidence">
                      {(dp.confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                );
              })}
            </details>
          )}
        </>
      )}

      {pool && pool.cross_charge_episode_count > 0 && (
        <div data-testid="cross-charge-history" className="opt-cross-charge-history">
          <div className="opt-cross-charge-title">
            Cross-Charge History
          </div>
          <div className="opt-cross-charge-stats">
            <span>Episodes: {pool.cross_charge_episode_count}</span>
            <span>Waste: {(pool.cross_charge_waste_wh / 1000).toFixed(2)} kWh</span>
          </div>
        </div>
      )}
    </section>
  );
}
