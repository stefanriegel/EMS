/**
 * SetupWizard — 6-step guided setup wizard for first-run configuration.
 *
 * Steps:
 *   1. Modbus (Huawei inverter host/port)
 *   2. Victron Modbus TCP (host/port + unit IDs behind Advanced toggle)
 *   3. EVCC (host/port + EVCC MQTT host/port)
 *   4. HA REST (URL, token, heat pump entity ID)
 *   5. Tariff (Octopus off-peak/peak rates and windows + Modul3 grid-fee windows)
 *   6. SoC limits (min/max per system)
 *
 * Steps 1–4 each have a "Test Connection" button that POSTs to
 * /api/setup/probe/{device} and displays an inline result badge.
 * Step 6 has a "Finish Setup" button that POSTs /api/setup/complete
 * and redirects to / on success.
 */
import { useState } from "react";
import { useLocation } from "wouter";

// ---- Types ------------------------------------------------------------------

interface FormValues {
  // Step 1: Modbus
  huawei_host: string;
  huawei_port: string;
  // Step 2: Victron Modbus TCP
  victron_host: string;
  victron_port: string;
  victron_system_unit_id: string;
  victron_battery_unit_id: string;
  victron_vebus_unit_id: string;
  // Step 3: EVCC
  evcc_host: string;
  evcc_port: string;
  evcc_mqtt_host: string;
  evcc_mqtt_port: string;
  // Step 4: HA REST
  ha_url: string;
  ha_token: string;
  ha_heat_pump_entity_id: string;
  // Step 5: Tariff
  octopus_off_peak_start_min: string;
  octopus_off_peak_end_min: string;
  octopus_off_peak_rate_eur_kwh: string;
  octopus_peak_rate_eur_kwh: string;
  modul3_surplus_start_min: string;
  modul3_surplus_end_min: string;
  modul3_deficit_start_min: string;
  modul3_deficit_end_min: string;
  modul3_surplus_rate_eur_kwh: string;
  modul3_deficit_rate_eur_kwh: string;
  // Feed-in tariff
  feed_in_rate_eur_kwh: string;
  // Seasonal strategy
  winter_months: string;
  winter_min_soc_boost_pct: string;
  // Step 6: SoC limits
  huawei_min_soc_pct: string;
  huawei_max_soc_pct: string;
  victron_min_soc_pct: string;
  victron_max_soc_pct: string;
}

const DEFAULT_VALUES: FormValues = {
  huawei_host: "",
  huawei_port: "6607",
  victron_host: "",
  victron_port: "502",
  victron_system_unit_id: "100",
  victron_battery_unit_id: "225",
  victron_vebus_unit_id: "227",
  evcc_host: "",
  evcc_port: "7070",
  evcc_mqtt_host: "",
  evcc_mqtt_port: "1883",
  ha_url: "http://homeassistant.local:8123",
  ha_token: "",
  ha_heat_pump_entity_id: "sensor.heat_pump_power_w",
  octopus_off_peak_start_min: "90",
  octopus_off_peak_end_min: "270",
  octopus_off_peak_rate_eur_kwh: "0.08",
  octopus_peak_rate_eur_kwh: "0.28",
  modul3_surplus_start_min: "",
  modul3_surplus_end_min: "",
  modul3_deficit_start_min: "",
  modul3_deficit_end_min: "",
  modul3_surplus_rate_eur_kwh: "",
  modul3_deficit_rate_eur_kwh: "",
  feed_in_rate_eur_kwh: "",
  winter_months: "11,12,1,2",
  winter_min_soc_boost_pct: "10",
  huawei_min_soc_pct: "10",
  huawei_max_soc_pct: "95",
  victron_min_soc_pct: "15",
  victron_max_soc_pct: "95",
};

interface ProbeResult {
  ok: boolean;
  error?: string;
  warning?: string;
}

// ---- Helpers ----------------------------------------------------------------

function Field({
  label,
  name,
  value,
  onChange,
  type = "text",
  placeholder,
}: {
  label: string;
  name: keyof FormValues;
  value: string;
  onChange: (name: keyof FormValues, value: string) => void;
  type?: string;
  placeholder?: string;
}) {
  return (
    <div className="setup-field">
      <label htmlFor={name} className="setup-label">
        {label}
      </label>
      <input
        id={name}
        name={name}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(name, e.target.value)}
        className="setup-input"
        autoComplete="off"
        spellCheck={false}
      />
    </div>
  );
}

function ProbeBadge({ result }: { result: ProbeResult | null }) {
  if (result === null) return null;
  if (result.ok && result.warning) {
    return <span className="probe-badge probe-badge--warn">! {result.warning}</span>;
  }
  if (result.ok) {
    return <span className="probe-badge probe-badge--ok">&#10003; Connection OK</span>;
  }
  return (
    <span className="probe-badge probe-badge--fail">
      &#10007; Failed{result.error ? `: ${result.error}` : ""}
    </span>
  );
}

// ---- Step components --------------------------------------------------------

function StepModbus({
  values,
  onChange,
  onProbe,
  probeResult,
  probing,
}: {
  values: FormValues;
  onChange: (name: keyof FormValues, value: string) => void;
  onProbe: () => void;
  probeResult: ProbeResult | null;
  probing: boolean;
}) {
  return (
    <div className="setup-step">
      <h2 className="setup-step-title">Modbus — Huawei Inverter</h2>
      <p className="setup-step-desc">
        Enter the hostname or IP address and TCP port of the Huawei SUN2000 Modbus proxy.
      </p>
      <Field label="Host" name="huawei_host" value={values.huawei_host} onChange={onChange} placeholder="192.168.0.100" />
      <Field label="Port" name="huawei_port" value={values.huawei_port} onChange={onChange} placeholder="6607" />
      <div className="setup-probe-row">
        <button
          className="btn btn--secondary"
          onClick={onProbe}
          disabled={probing || !values.huawei_host}
          type="button"
        >
          {probing ? "Testing…" : "Test Connection"}
        </button>
        <ProbeBadge result={probeResult} />
      </div>
    </div>
  );
}

function StepVictron({
  values,
  onChange,
  onProbe,
  probeResult,
  probing,
}: {
  values: FormValues;
  onChange: (name: keyof FormValues, value: string) => void;
  onProbe: () => void;
  probeResult: ProbeResult | null;
  probing: boolean;
}) {
  return (
    <div className="setup-step">
      <h2 className="setup-step-title">Victron Modbus TCP</h2>
      <p className="setup-step-desc">
        Enter the hostname or IP address of the Venus OS GX device and the Modbus TCP port.
      </p>
      <Field label="Host" name="victron_host" value={values.victron_host} onChange={onChange} placeholder="192.168.0.101" />
      <Field label="Port" name="victron_port" value={values.victron_port} onChange={onChange} placeholder="502" />
      <details className="setup-advanced">
        <summary>Advanced: Unit IDs</summary>
        <p className="setup-step-desc">
          Most Venus OS installations use the default unit IDs. Only change these if your system uses non-standard addressing.
        </p>
        <Field label="System Unit ID" name="victron_system_unit_id" value={values.victron_system_unit_id} onChange={onChange} placeholder="100" />
        <Field label="Battery Unit ID" name="victron_battery_unit_id" value={values.victron_battery_unit_id} onChange={onChange} placeholder="225" />
        <Field label="VE.Bus Unit ID" name="victron_vebus_unit_id" value={values.victron_vebus_unit_id} onChange={onChange} placeholder="227" />
      </details>
      <div className="setup-probe-row">
        <button
          className="btn btn--secondary"
          onClick={onProbe}
          disabled={probing || !values.victron_host}
          type="button"
        >
          {probing ? "Testing…" : "Test Connection"}
        </button>
        <ProbeBadge result={probeResult} />
      </div>
    </div>
  );
}

function StepEvcc({
  values,
  onChange,
  onProbe,
  probeResult,
  probing,
}: {
  values: FormValues;
  onChange: (name: keyof FormValues, value: string) => void;
  onProbe: () => void;
  probeResult: ProbeResult | null;
  probing: boolean;
}) {
  return (
    <div className="setup-step">
      <h2 className="setup-step-title">EVCC</h2>
      <p className="setup-step-desc">
        EVCC controls EV charging. Enter its HTTP API address and MQTT broker address.
      </p>
      <Field label="EVCC Host" name="evcc_host" value={values.evcc_host} onChange={onChange} placeholder="192.168.0.102" />
      <Field label="EVCC Port" name="evcc_port" value={values.evcc_port} onChange={onChange} placeholder="7070" />
      <Field label="EVCC MQTT Host" name="evcc_mqtt_host" value={values.evcc_mqtt_host} onChange={onChange} placeholder="192.168.0.102" />
      <Field label="EVCC MQTT Port" name="evcc_mqtt_port" value={values.evcc_mqtt_port} onChange={onChange} placeholder="1883" />
      <div className="setup-probe-row">
        <button
          className="btn btn--secondary"
          onClick={onProbe}
          disabled={probing || !values.evcc_host}
          type="button"
        >
          {probing ? "Testing…" : "Test Connection"}
        </button>
        <ProbeBadge result={probeResult} />
      </div>
    </div>
  );
}

function StepHaRest({
  values,
  onChange,
  onProbe,
  probeResult,
  probing,
}: {
  values: FormValues;
  onChange: (name: keyof FormValues, value: string) => void;
  onProbe: () => void;
  probeResult: ProbeResult | null;
  probing: boolean;
}) {
  return (
    <div className="setup-step">
      <h2 className="setup-step-title">Home Assistant REST</h2>
      <p className="setup-step-desc">
        The EMS reads heat pump power from a Home Assistant sensor via the REST API.
      </p>
      <Field label="HA URL" name="ha_url" value={values.ha_url} onChange={onChange} placeholder="http://homeassistant.local:8123" />
      <Field label="Long-lived Access Token" name="ha_token" value={values.ha_token} onChange={onChange} type="password" placeholder="eyJ…" />
      <Field label="Heat Pump Entity ID" name="ha_heat_pump_entity_id" value={values.ha_heat_pump_entity_id} onChange={onChange} placeholder="sensor.heat_pump_power_w" />
      <div className="setup-probe-row">
        <button
          className="btn btn--secondary"
          onClick={onProbe}
          disabled={probing || !values.ha_url || !values.ha_token}
          type="button"
        >
          {probing ? "Testing…" : "Test Connection"}
        </button>
        <ProbeBadge result={probeResult} />
      </div>
    </div>
  );
}

function StepTariff({
  values,
  onChange,
}: {
  values: FormValues;
  onChange: (name: keyof FormValues, value: string) => void;
}) {
  return (
    <div className="setup-step">
      <h2 className="setup-step-title">Tariff Settings</h2>
      <p className="setup-step-desc">
        Define off-peak and peak electricity rates for smart charging decisions.
        Times are in minutes from midnight (e.g. 90 = 01:30, 270 = 04:30).
      </p>
      <h3 className="setup-step-title" style={{ fontSize: '16px' }}>Octopus Go Rates</h3>
      <Field label="Off-peak Start (minutes from midnight)" name="octopus_off_peak_start_min" value={values.octopus_off_peak_start_min} onChange={onChange} placeholder="90" />
      <Field label="Off-peak End (minutes from midnight)" name="octopus_off_peak_end_min" value={values.octopus_off_peak_end_min} onChange={onChange} placeholder="270" />
      <Field label="Off-peak Rate (€/kWh)" name="octopus_off_peak_rate_eur_kwh" value={values.octopus_off_peak_rate_eur_kwh} onChange={onChange} placeholder="0.08" />
      <Field label="Peak Rate (€/kWh)" name="octopus_peak_rate_eur_kwh" value={values.octopus_peak_rate_eur_kwh} onChange={onChange} placeholder="0.28" />
      <hr style={{ borderColor: 'var(--bg-card-border)', margin: '16px 0' }} />
      <h3 className="setup-step-title" style={{ fontSize: '16px', marginTop: '16px' }}>Modul3 Grid-Fee Windows</h3>
      <p className="setup-step-desc">
        Define surplus and deficit time windows with their grid-fee rates for the Modul3 tariff provider.
      </p>
      <Field label="Surplus Start (min from midnight)" name="modul3_surplus_start_min" value={values.modul3_surplus_start_min} onChange={onChange} placeholder="0" />
      <Field label="Surplus End (min from midnight)" name="modul3_surplus_end_min" value={values.modul3_surplus_end_min} onChange={onChange} placeholder="0" />
      <Field label="Surplus Rate (EUR/kWh)" name="modul3_surplus_rate_eur_kwh" value={values.modul3_surplus_rate_eur_kwh} onChange={onChange} placeholder="0.0" />
      <Field label="Deficit Start (min from midnight)" name="modul3_deficit_start_min" value={values.modul3_deficit_start_min} onChange={onChange} placeholder="0" />
      <Field label="Deficit End (min from midnight)" name="modul3_deficit_end_min" value={values.modul3_deficit_end_min} onChange={onChange} placeholder="0" />
      <Field label="Deficit Rate (EUR/kWh)" name="modul3_deficit_rate_eur_kwh" value={values.modul3_deficit_rate_eur_kwh} onChange={onChange} placeholder="0.0" />
      <hr style={{ borderColor: 'var(--bg-card-border)', margin: '16px 0' }} />
      <h3 className="setup-step-title" style={{ fontSize: '16px', marginTop: '16px' }}>Feed-in Tariff</h3>
      <Field label="Feed-in Rate (EUR/kWh)" name="feed_in_rate_eur_kwh" value={values.feed_in_rate_eur_kwh} onChange={onChange} placeholder="0.074" />
      <hr style={{ borderColor: 'var(--bg-card-border)', margin: '16px 0' }} />
      <h3 className="setup-step-title" style={{ fontSize: '16px', marginTop: '16px' }}>Seasonal Strategy</h3>
      <Field label="Winter Months (comma-separated)" name="winter_months" value={values.winter_months} onChange={onChange} placeholder="11,12,1,2" />
      <Field label="Winter Min-SoC Boost (%)" name="winter_min_soc_boost_pct" value={values.winter_min_soc_boost_pct} onChange={onChange} placeholder="10" />
    </div>
  );
}

function StepSocLimits({
  values,
  onChange,
  onFinish,
  finishing,
  finishError,
}: {
  values: FormValues;
  onChange: (name: keyof FormValues, value: string) => void;
  onFinish: () => void;
  finishing: boolean;
  finishError: string | null;
}) {
  return (
    <div className="setup-step">
      <h2 className="setup-step-title">Battery SoC Limits</h2>
      <p className="setup-step-desc">
        Set minimum and maximum state-of-charge limits for each battery system.
        These protect battery longevity and reserve capacity for emergencies.
      </p>
      <Field label="Huawei Min SoC (%)" name="huawei_min_soc_pct" value={values.huawei_min_soc_pct} onChange={onChange} placeholder="10" />
      <Field label="Huawei Max SoC (%)" name="huawei_max_soc_pct" value={values.huawei_max_soc_pct} onChange={onChange} placeholder="95" />
      <Field label="Victron Min SoC (%)" name="victron_min_soc_pct" value={values.victron_min_soc_pct} onChange={onChange} placeholder="15" />
      <Field label="Victron Max SoC (%)" name="victron_max_soc_pct" value={values.victron_max_soc_pct} onChange={onChange} placeholder="95" />
      {finishError && (
        <p className="setup-error">{finishError}</p>
      )}
      <button
        className="btn btn--primary"
        onClick={onFinish}
        disabled={finishing}
        type="button"
        data-testid="finish-setup-btn"
      >
        {finishing ? "Saving…" : "Finish Setup"}
      </button>
    </div>
  );
}

// ---- Main component ---------------------------------------------------------

const TOTAL_STEPS = 6;

export function SetupWizard() {
  const [, setLocation] = useLocation();
  const [step, setStep] = useState(1);
  const [values, setValues] = useState<FormValues>(DEFAULT_VALUES);
  const [probeResults, setProbeResults] = useState<Record<number, ProbeResult | null>>({});
  const [probing, setProbing] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [finishError, setFinishError] = useState<string | null>(null);

  function handleChange(name: keyof FormValues, value: string) {
    setValues((prev) => ({ ...prev, [name]: value }));
  }

  async function handleProbe(device: string, body: Record<string, string | number>) {
    setProbing(true);
    setProbeResults((prev) => ({ ...prev, [step]: null }));
    try {
      const resp = await fetch(`/api/setup/probe/${device}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data: ProbeResult = await resp.json();
      setProbeResults((prev) => ({ ...prev, [step]: data }));
    } catch (err) {
      setProbeResults((prev) => ({
        ...prev,
        [step]: { ok: false, error: String(err) },
      }));
    } finally {
      setProbing(false);
    }
  }

  async function handleFinish() {
    setFinishing(true);
    setFinishError(null);
    try {
      const payload = {
        huawei_host: values.huawei_host,
        huawei_port: parseInt(values.huawei_port, 10) || 6607,
        victron_host: values.victron_host,
        victron_port: parseInt(values.victron_port, 10) || 502,
        victron_system_unit_id: parseInt(values.victron_system_unit_id, 10) || 100,
        victron_battery_unit_id: parseInt(values.victron_battery_unit_id, 10) || 225,
        victron_vebus_unit_id: parseInt(values.victron_vebus_unit_id, 10) || 227,
        evcc_host: values.evcc_host,
        evcc_port: parseInt(values.evcc_port, 10) || 7070,
        evcc_mqtt_host: values.evcc_mqtt_host,
        evcc_mqtt_port: parseInt(values.evcc_mqtt_port, 10) || 1883,
        ha_url: values.ha_url,
        ha_token: values.ha_token,
        ha_heat_pump_entity_id: values.ha_heat_pump_entity_id,
        octopus_off_peak_start_min: parseInt(values.octopus_off_peak_start_min, 10) || 90,
        octopus_off_peak_end_min: parseInt(values.octopus_off_peak_end_min, 10) || 270,
        octopus_off_peak_rate_eur_kwh: parseFloat(values.octopus_off_peak_rate_eur_kwh) || 0.08,
        octopus_peak_rate_eur_kwh: parseFloat(values.octopus_peak_rate_eur_kwh) || 0.28,
        modul3_surplus_start_min: parseInt(values.modul3_surplus_start_min, 10) || 0,
        modul3_surplus_end_min: parseInt(values.modul3_surplus_end_min, 10) || 0,
        modul3_deficit_start_min: parseInt(values.modul3_deficit_start_min, 10) || 0,
        modul3_deficit_end_min: parseInt(values.modul3_deficit_end_min, 10) || 0,
        modul3_surplus_rate_eur_kwh: parseFloat(values.modul3_surplus_rate_eur_kwh) || 0.0,
        modul3_deficit_rate_eur_kwh: parseFloat(values.modul3_deficit_rate_eur_kwh) || 0.0,
        feed_in_rate_eur_kwh: parseFloat(values.feed_in_rate_eur_kwh) || 0.074,
        winter_months: values.winter_months || "11,12,1,2",
        winter_min_soc_boost_pct: parseInt(values.winter_min_soc_boost_pct, 10) || 10,
        huawei_min_soc_pct: parseInt(values.huawei_min_soc_pct, 10) || 10,
        huawei_max_soc_pct: parseInt(values.huawei_max_soc_pct, 10) || 95,
        victron_min_soc_pct: parseInt(values.victron_min_soc_pct, 10) || 15,
        victron_max_soc_pct: parseInt(values.victron_max_soc_pct, 10) || 95,
      };
      const resp = await fetch("/api/setup/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const text = await resp.text();
        setFinishError(`Server error (${resp.status}): ${text}`);
        return;
      }
      const data = await resp.json();
      if (data.ok) {
        setLocation("/");
      } else {
        setFinishError(data.error ?? "Setup failed — unknown error.");
      }
    } catch (err) {
      setFinishError(`Request failed: ${String(err)}`);
    } finally {
      setFinishing(false);
    }
  }

  function renderStep() {
    switch (step) {
      case 1:
        return (
          <StepModbus
            values={values}
            onChange={handleChange}
            onProbe={() =>
              handleProbe("modbus", {
                host: values.huawei_host,
                port: parseInt(values.huawei_port, 10) || 6607,
              })
            }
            probeResult={probeResults[1] ?? null}
            probing={probing}
          />
        );
      case 2:
        return (
          <StepVictron
            values={values}
            onChange={handleChange}
            onProbe={() =>
              handleProbe("victron_modbus", {
                host: values.victron_host,
                port: parseInt(values.victron_port, 10) || 502,
                unit_id: parseInt(values.victron_system_unit_id, 10) || 100,
              })
            }
            probeResult={probeResults[2] ?? null}
            probing={probing}
          />
        );
      case 3:
        return (
          <StepEvcc
            values={values}
            onChange={handleChange}
            onProbe={() =>
              handleProbe("evcc", {
                host: values.evcc_host,
                port: parseInt(values.evcc_port, 10) || 7070,
              })
            }
            probeResult={probeResults[3] ?? null}
            probing={probing}
          />
        );
      case 4:
        return (
          <StepHaRest
            values={values}
            onChange={handleChange}
            onProbe={() =>
              handleProbe("ha_rest", {
                ha_url: values.ha_url,
                ha_token: values.ha_token,
                ha_heat_pump_entity_id: values.ha_heat_pump_entity_id,
              })
            }
            probeResult={probeResults[4] ?? null}
            probing={probing}
          />
        );
      case 5:
        return <StepTariff values={values} onChange={handleChange} />;
      case 6:
        return (
          <StepSocLimits
            values={values}
            onChange={handleChange}
            onFinish={handleFinish}
            finishing={finishing}
            finishError={finishError}
          />
        );
      default:
        return null;
    }
  }

  return (
    <div data-testid="setup-wizard" className="setup-wizard">
      <header className="setup-header">
        <h1 className="setup-title">EMS Setup</h1>
        <span data-testid="step-indicator" className="setup-step-indicator">
          Step {step} of {TOTAL_STEPS}
        </span>
      </header>

      <div className="setup-progress">
        {Array.from({ length: TOTAL_STEPS }, (_, i) => (
          <div
            key={i}
            className={`setup-progress-dot${i + 1 === step ? " setup-progress-dot--active" : i + 1 < step ? " setup-progress-dot--done" : ""}`}
          />
        ))}
      </div>

      <div className="card setup-card">{renderStep()}</div>

      <div className="setup-nav">
        {step > 1 && (
          <button
            className="btn btn--ghost"
            onClick={() => setStep((s) => s - 1)}
            type="button"
          >
            ← Back
          </button>
        )}
        {step < TOTAL_STEPS && (
          <button
            className="btn btn--primary setup-nav-next"
            onClick={() => setStep((s) => s + 1)}
            type="button"
            data-testid="next-step-btn"
          >
            Next →
          </button>
        )}
      </div>
    </div>
  );
}
