/**
 * TariffCard — current electricity tariff rates.
 *
 * Shows effective_rate_eur_kwh prominently, with optional breakdown.
 * Hides the Modul3 / grid-fee row when the value is 0 or null (EVCC source
 * provides a fully-inclusive price, so Modul3 is always 0).
 * Renders "Tariff unavailable" when tariff data is null or all rates are null.
 */
import type { TariffPayload } from "../types";

interface Props {
  tariff: TariffPayload | null;
}

function rate(value: number | null | undefined): string {
  if (value === null || value === undefined) return "N/A";
  return `€${value.toFixed(4)}/kWh`;
}

export function TariffCard({ tariff }: Props) {
  const unavailable =
    !tariff ||
    (tariff.effective_rate_eur_kwh === null &&
      tariff.octopus_rate_eur_kwh === null &&
      tariff.modul3_rate_eur_kwh === null);

  return (
    <section className="card tariff-card">
      <h2 className="card-title">Current Tariff</h2>

      {tariff?.source === "evcc" && (
        <span
          data-testid="tariff-source-badge"
          className="badge badge--live"
          style={{ color: "var(--color-pv)", borderColor: "var(--color-pv)" }}
        >
          EVCC ⚡
        </span>
      )}
      {tariff?.source === "live" && (
        <span
          data-testid="tariff-source-badge"
          className="badge badge--live"
          style={{ color: "var(--color-pv)", borderColor: "var(--color-pv)" }}
        >
          Live ⚡
        </span>
      )}
      {tariff?.source === "hardcoded" && (
        <span
          data-testid="tariff-source-badge"
          className="badge badge--hardcoded"
          style={{ color: "var(--text-muted)", borderColor: "var(--text-muted)" }}
        >
          Hardcoded
        </span>
      )}

      {unavailable ? (
        <p className="tariff-unavailable">Tariff unavailable</p>
      ) : (
        <>
          <div className="tariff-effective">
            <span className="tariff-effective-label">Effective Rate</span>
            <span className="tariff-effective-value">
              {rate(tariff?.effective_rate_eur_kwh)}
            </span>
          </div>
          <div className="tariff-breakdown">
            <div className="tariff-row">
              <span className="tariff-source">Supply Rate</span>
              <span className="tariff-rate">{rate(tariff?.octopus_rate_eur_kwh)}</span>
            </div>
            {tariff?.modul3_rate_eur_kwh != null && tariff.modul3_rate_eur_kwh > 0 && (
              <div className="tariff-row">
                <span className="tariff-source">Grid Fee (§14a)</span>
                <span className="tariff-rate">{rate(tariff.modul3_rate_eur_kwh)}</span>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}
