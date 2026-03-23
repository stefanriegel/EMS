/**
 * ForecastCard -- 3-day solar forecast bar chart.
 *
 * Shows expected solar production per day as horizontal bars,
 * plus consumption and net balance summary rows with confidence.
 * Renders a fallback message when forecast data is unavailable.
 */
import type { ForecastPayload } from "../types";

interface Props {
  forecast: ForecastPayload | null;
}

export function ForecastCard({ forecast }: Props) {
  const maxSolar = forecast
    ? Math.max(...forecast.days.map((d) => d.solar_kwh), 1)
    : 1;

  return (
    <section className="card forecast-card" data-testid="forecast-card">
      <h2 className="card-title">Solar Forecast</h2>

      {!forecast ? (
        <p className="unavailable">No forecast available</p>
      ) : (
        <>
          <div className="forecast-bars">
            {forecast.days.map((day) => {
              const label = new Date(day.date + "T12:00:00").toLocaleDateString(
                [],
                { weekday: "short" },
              );
              const widthPct = (day.solar_kwh / maxSolar) * 100;
              return (
                <div key={day.date} className="forecast-bar-row">
                  <span className="forecast-bar-label">{label}</span>
                  <div className="forecast-bar-track">
                    <div
                      className="forecast-bar-fill"
                      style={{ width: `${widthPct}%` }}
                    />
                  </div>
                  <span className="forecast-bar-value">
                    {day.solar_kwh.toFixed(1)} kWh
                  </span>
                </div>
              );
            })}
          </div>

          <div className="forecast-summary">
            {forecast.days.map((day) => {
              const label = new Date(day.date + "T12:00:00").toLocaleDateString(
                [],
                { weekday: "short" },
              );
              const isPositive = day.net_kwh >= 0;
              return (
                <div key={day.date} className="forecast-day-summary">
                  <span>{label}</span>
                  <span>
                    Load: {day.consumption_kwh.toFixed(1)} kWh
                  </span>
                  <span
                    className={
                      isPositive
                        ? "forecast-net--surplus"
                        : "forecast-net--deficit"
                    }
                  >
                    Net: {day.net_kwh.toFixed(1)} kWh
                  </span>
                  <span className="forecast-confidence">
                    {(day.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}
