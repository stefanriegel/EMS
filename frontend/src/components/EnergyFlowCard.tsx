/**
 * EnergyFlowCard — animated SVG energy flow diagram.
 *
 * Shows live energy flow between PV, Battery, Home, and Grid nodes.
 * Uses pure CSS @keyframes animation (stroke-dashoffset) — no extra deps.
 *
 * Graceful degradation: when pool === null or devices === null, renders
 * static grey nodes with "—" labels and no animation. This is the visible
 * signal that WS data has not yet arrived or the backend is unreachable.
 */
import type { PoolState, DevicesPayload } from "../types";

interface Props {
  pool: PoolState | null;
  devices: DevicesPayload | null;
}

// SVG coordinate constants
const PV = { cx: 200, cy: 60 };
const BAT = { cx: 80, cy: 200 };
const HOME = { cx: 320, cy: 200 };
const GRID = { cx: 200, cy: 300 };

// Battery SoC arc geometry: r=44, circumference = 2π*44 ≈ 276.46
const SOC_RADIUS = 44;
const SOC_CIRCUMFERENCE = 2 * Math.PI * SOC_RADIUS;

/**
 * Formats a watt value as a human-readable power string.
 * < 1000 W → "420 W", >= 1000 W → "2.4 kW"
 */
function formatPower(watts: number): string {
  const abs = Math.abs(watts);
  if (abs < 1000) return `${Math.round(abs)} W`;
  return `${(abs / 1000).toFixed(1)} kW`;
}

export function EnergyFlowCard({ pool, devices }: Props) {
  const hasData = pool !== null && devices !== null;

  // Derive display values
  const pvPower = devices?.huawei?.master_pv_power_w ?? 0;
  const combinedSoc = pool?.combined_soc_pct ?? null;
  const combinedPower = pool?.combined_power_w ?? 0;

  // Flow path activation logic (>20 W threshold avoids noise flicker)
  const pvToBatActive = hasData && pvPower > 20 && combinedPower > 20;
  const pvToHomeActive = hasData && pvPower > 20;
  const batToHomeActive = hasData && combinedPower < -20;
  const homeToGridActive = hasData && combinedPower > 20 && pvPower < 20;

  // Battery SoC arc: stroke-dashoffset encodes fill level
  const socOffset =
    combinedSoc !== null
      ? SOC_CIRCUMFERENCE * (1 - combinedSoc / 100)
      : SOC_CIRCUMFERENCE; // full offset = empty arc when no data

  // Node fill/stroke color based on data availability
  const pvColor = hasData ? "var(--color-pv)" : "var(--text-muted)";
  const batColor = hasData ? "var(--color-battery)" : "var(--text-muted)";
  const homeColor = hasData ? "var(--color-home)" : "var(--text-muted)";
  const gridColor = hasData ? "var(--color-grid)" : "var(--text-muted)";

  const pvValue = hasData ? formatPower(pvPower) : "—";
  const batValue = hasData && combinedSoc !== null ? `${Math.round(combinedSoc)}%` : "—";
  const homeValue = hasData ? formatPower(Math.abs(combinedPower)) : "—";
  const gridValue = hasData ? formatPower(Math.abs(combinedPower)) : "—";

  return (
    <section className="card energy-flow-card" data-testid="energy-flow-card">
      <div className="card-title">Energy Flow</div>
      <div className="card-subtitle">Live generation · storage · consumption</div>

      <svg
        width="100%"
        viewBox="0 0 400 360"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Energy flow diagram showing PV, battery, home, and grid"
      >
        {/* ── Flow paths (drawn beneath nodes) ─────────────────────────── */}

        {/* PV → Battery */}
        <path
          d="M 200 96 C 200 150 80 150 80 164"
          className={`flow-path${pvToBatActive ? " flow-path--active" : ""}`}
          stroke={pvToBatActive ? "var(--color-pv)" : undefined}
        />

        {/* PV → Home */}
        <path
          d="M 200 96 C 200 150 320 150 320 164"
          className={`flow-path${pvToHomeActive ? " flow-path--active" : ""}`}
          stroke={pvToHomeActive ? "var(--color-pv)" : undefined}
        />

        {/* Battery → Home */}
        <path
          d="M 116 200 L 284 200"
          className={`flow-path${batToHomeActive ? " flow-path--active" : ""}`}
          stroke={batToHomeActive ? "var(--color-battery)" : undefined}
        />

        {/* Home → Grid (or Grid → Home) */}
        <path
          d="M 320 236 C 320 270 200 270 200 264"
          className={`flow-path${homeToGridActive ? " flow-path--active" : ""}`}
          stroke={homeToGridActive ? "var(--color-grid)" : undefined}
        />

        {/* ── Battery SoC arc ───────────────────────────────────────────── */}
        {/* Background track */}
        <circle
          cx={BAT.cx}
          cy={BAT.cy}
          r={SOC_RADIUS}
          fill="none"
          stroke="rgba(251,191,36,0.12)"
          strokeWidth={4}
        />
        {/* Animated fill arc */}
        <circle
          cx={BAT.cx}
          cy={BAT.cy}
          r={SOC_RADIUS}
          className="soc-arc"
          strokeDasharray={`${SOC_CIRCUMFERENCE}`}
          strokeDashoffset={socOffset}
          strokeLinecap="round"
          transform={`rotate(-90, ${BAT.cx}, ${BAT.cy})`}
          opacity={hasData ? 1 : 0.3}
        />

        {/* ── PV node ───────────────────────────────────────────────────── */}
        <circle
          cx={PV.cx}
          cy={PV.cy}
          r={36}
          fill={pvColor}
          fillOpacity={0.15}
          stroke={pvColor}
          strokeWidth={2.5}
        />
        {/* ☀ sun icon approximation */}
        <text x={PV.cx} y={PV.cy + 5} textAnchor="middle" fontSize={20} fill={pvColor} fontFamily="serif">
          ☀
        </text>
        <text x={PV.cx} y={PV.cy - 46} className="node-value">
          {pvValue}
        </text>
        <text x={PV.cx} y={PV.cy + 52} className="node-label">
          Solar
        </text>

        {/* ── Battery node ──────────────────────────────────────────────── */}
        <circle
          cx={BAT.cx}
          cy={BAT.cy}
          r={36}
          fill={batColor}
          fillOpacity={0.15}
          stroke={batColor}
          strokeWidth={2.5}
        />
        {/* 🔋 battery icon */}
        <text x={BAT.cx} y={BAT.cy + 5} textAnchor="middle" fontSize={18} fill={batColor} fontFamily="serif">
          🔋
        </text>
        <text x={BAT.cx - 58} y={BAT.cy + 5} className="node-value" textAnchor="middle">
          {batValue}
        </text>
        <text x={BAT.cx} y={BAT.cy + 52} className="node-label">
          Battery
        </text>

        {/* ── Home node ─────────────────────────────────────────────────── */}
        <circle
          cx={HOME.cx}
          cy={HOME.cy}
          r={36}
          fill={homeColor}
          fillOpacity={0.15}
          stroke={homeColor}
          strokeWidth={2.5}
        />
        {/* 🏠 home icon */}
        <text x={HOME.cx} y={HOME.cy + 5} textAnchor="middle" fontSize={18} fill={homeColor} fontFamily="serif">
          🏠
        </text>
        <text x={HOME.cx + 56} y={HOME.cy + 5} className="node-value" textAnchor="middle">
          {homeValue}
        </text>
        <text x={HOME.cx} y={HOME.cy + 52} className="node-label">
          Home
        </text>

        {/* ── Grid node ─────────────────────────────────────────────────── */}
        <circle
          cx={GRID.cx}
          cy={GRID.cy}
          r={36}
          fill={gridColor}
          fillOpacity={0.15}
          stroke={gridColor}
          strokeWidth={2.5}
        />
        {/* ⚡ grid icon */}
        <text x={GRID.cx} y={GRID.cy + 5} textAnchor="middle" fontSize={18} fill={gridColor} fontFamily="serif">
          ⚡
        </text>
        <text x={GRID.cx} y={GRID.cy - 46} className="node-value">
          {gridValue}
        </text>
        <text x={GRID.cx} y={GRID.cy + 52} className="node-label">
          Grid
        </text>
      </svg>

      {/* Null-state indicator (shown while connecting) */}
      {!hasData && (
        <p className="energy-flow-connecting">Waiting for live data…</p>
      )}
    </section>
  );
}
