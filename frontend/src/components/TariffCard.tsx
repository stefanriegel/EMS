/**
 * TariffCard — current electricity tariff rates.
 *
 * Shows effective_rate_eur_kwh prominently, with octopus/modul3 breakdown.
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
              <span className="tariff-source">Octopus Go</span>
              <span className="tariff-rate">{rate(tariff?.octopus_rate_eur_kwh)}</span>
            </div>
            <div className="tariff-row">
              <span className="tariff-source">Modul3</span>
              <span className="tariff-rate">{rate(tariff?.modul3_rate_eur_kwh)}</span>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
