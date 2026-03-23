/**
 * EnergyFlowCard -- 5-node animated SVG energy flow diagram.
 *
 * Shows live energy flow between PV, Huawei, Victron, Home, and Grid nodes
 * with 6 independent flow paths and per-battery SoC arcs.
 *
 * Graceful degradation: when pool === null or devices === null, renders
 * static grey nodes with "--" labels and no animation.
 */
import type { PoolState, DevicesPayload } from "../types";

interface Props {
  pool: PoolState | null;
  devices: DevicesPayload | null;
}

// SVG coordinate constants -- symmetric 5-node layout
const PV      = { cx: 200, cy: 55 };
const HUAWEI  = { cx: 70,  cy: 210 };
const HOME    = { cx: 200, cy: 200 };
const VICTRON = { cx: 330, cy: 210 };
const GRID    = { cx: 200, cy: 340 };

// SoC arc geometry: r=34, circumference = 2*pi*34 ~ 213.63
const SOC_RADIUS = 34;
const SOC_CIRCUMFERENCE = 2 * Math.PI * SOC_RADIUS;

// Node circle radius
const NODE_R = 34;

// Flow activation threshold (watts) -- avoids noise flicker
const FLOW_THRESHOLD = 20;

/**
 * Formats a watt value as a human-readable power string.
 * < 1000 W -> "420 W", >= 1000 W -> "2.4 kW"
 */
function formatPower(watts: number): string {
  const abs = Math.abs(watts);
  if (abs < 1000) return `${Math.round(abs)} W`;
  return `${(abs / 1000).toFixed(1)} kW`;
}

/** Compute stroke-dashoffset for a SoC arc (0-100%). */
function socOffset(socPct: number | null): number {
  if (socPct === null) return SOC_CIRCUMFERENCE;
  return SOC_CIRCUMFERENCE * (1 - socPct / 100);
}

export function EnergyFlowCard({ pool, devices }: Props) {
  const hasData = pool !== null && devices !== null;

  // Derive display values
  const pvPower = devices?.huawei?.master_pv_power_w ?? 0;
  const huaweiPower = devices?.huawei?.total_power_w ?? 0;
  const victronPower = devices?.victron?.battery_power_w ?? 0;
  const gridPower = devices?.victron?.grid_power_w ?? 0;
  const consumptionPower = devices?.victron?.consumption_w ?? 0;

  const huaweiSoc = pool?.huawei_soc_pct ?? null;
  const victronSoc = pool?.victron_soc_pct ?? null;
  const huaweiAvailable = pool?.huawei_available ?? false;
  const victronAvailable = pool?.victron_available ?? false;

  // Flow path activation logic (6 independent paths)
  // Positive battery power = charging, negative = discharging
  const pvToHuaweiActive = hasData && pvPower > FLOW_THRESHOLD && huaweiPower > FLOW_THRESHOLD;
  const pvToVictronActive = hasData && pvPower > FLOW_THRESHOLD && victronPower > FLOW_THRESHOLD;
  const pvToHomeActive = hasData && pvPower > FLOW_THRESHOLD;
  const huaweiToHomeActive = hasData && huaweiPower < -FLOW_THRESHOLD;
  const victronToHomeActive = hasData && victronPower < -FLOW_THRESHOLD;
  // Grid: positive = importing to home, negative = exporting from home
  const gridToHomeActive = hasData && gridPower > FLOW_THRESHOLD;
  const homeToGridActive = hasData && gridPower < -FLOW_THRESHOLD;

  // Node colors based on data availability and system status
  const pvColor = hasData ? "var(--color-pv)" : "var(--text-muted)";
  const huaweiColor = hasData && huaweiAvailable ? "var(--color-huawei)" : "var(--text-muted)";
  const victronColor = hasData && victronAvailable ? "var(--color-victron)" : "var(--text-muted)";
  const homeColor = hasData ? "var(--color-home)" : "var(--text-muted)";
  const gridColor = hasData ? "var(--color-grid)" : "var(--text-muted)";

  // Display values
  const pvValue = hasData ? formatPower(pvPower) : "--";
  const huaweiValue = hasData && huaweiSoc !== null ? `${Math.round(huaweiSoc)}%` : "--";
  const victronValue = hasData && victronSoc !== null ? `${Math.round(victronSoc)}%` : "--";
  const homeValue = hasData ? formatPower(consumptionPower || Math.abs(huaweiPower + victronPower)) : "--";
  const gridValue = hasData ? formatPower(Math.abs(gridPower)) : "--";

  // Per-battery power labels below nodes
  const huaweiPowerLabel = hasData ? formatPower(huaweiPower) : "--";
  const victronPowerLabel = hasData ? formatPower(victronPower) : "--";

  // Offline opacity
  const huaweiOpacity = hasData && huaweiAvailable ? 1 : 0.3;
  const victronOpacity = hasData && victronAvailable ? 1 : 0.3;

  return (
    <section className="card energy-flow-card" data-testid="energy-flow-card">
      <div className="card-title">Energy Flow</div>
      <div className="card-subtitle">Live generation, storage, consumption</div>

      <svg
        width="100%"
        viewBox="0 0 400 400"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Energy flow diagram showing PV, Huawei battery, Victron battery, home, and grid"
      >
        {/* -- Flow paths (drawn beneath nodes) -- */}

        {/* PV -> Huawei */}
        <path
          d={`M ${PV.cx} ${PV.cy + NODE_R + 2} C ${PV.cx - 40} ${PV.cy + 80} ${HUAWEI.cx + 40} ${HUAWEI.cy - 60} ${HUAWEI.cx} ${HUAWEI.cy - NODE_R - 2}`}
          className={`flow-path${pvToHuaweiActive ? " flow-path--active" : ""}`}
          stroke={pvToHuaweiActive ? "var(--color-pv)" : undefined}
        />

        {/* PV -> Victron */}
        <path
          d={`M ${PV.cx} ${PV.cy + NODE_R + 2} C ${PV.cx + 40} ${PV.cy + 80} ${VICTRON.cx - 40} ${VICTRON.cy - 60} ${VICTRON.cx} ${VICTRON.cy - NODE_R - 2}`}
          className={`flow-path${pvToVictronActive ? " flow-path--active" : ""}`}
          stroke={pvToVictronActive ? "var(--color-pv)" : undefined}
        />

        {/* PV -> Home */}
        <path
          d={`M ${PV.cx} ${PV.cy + NODE_R + 2} L ${HOME.cx} ${HOME.cy - NODE_R - 2}`}
          className={`flow-path${pvToHomeActive ? " flow-path--active" : ""}`}
          stroke={pvToHomeActive ? "var(--color-pv)" : undefined}
        />

        {/* Huawei -> Home */}
        <path
          d={`M ${HUAWEI.cx + NODE_R + 2} ${HUAWEI.cy} L ${HOME.cx - NODE_R - 2} ${HOME.cy}`}
          className={`flow-path${huaweiToHomeActive ? " flow-path--active" : ""}`}
          stroke={huaweiToHomeActive ? "var(--color-huawei)" : undefined}
        />

        {/* Victron -> Home */}
        <path
          d={`M ${VICTRON.cx - NODE_R - 2} ${VICTRON.cy} L ${HOME.cx + NODE_R + 2} ${HOME.cy}`}
          className={`flow-path${victronToHomeActive ? " flow-path--active" : ""}`}
          stroke={victronToHomeActive ? "var(--color-victron)" : undefined}
        />

        {/* Grid <-> Home */}
        <path
          d={`M ${HOME.cx} ${HOME.cy + NODE_R + 2} L ${GRID.cx} ${GRID.cy - NODE_R - 2}`}
          className={`flow-path${gridToHomeActive || homeToGridActive ? " flow-path--active" : ""}`}
          stroke={gridToHomeActive ? "var(--color-grid)" : homeToGridActive ? "var(--color-home)" : undefined}
        />

        {/* -- Huawei SoC arc -- */}
        <g data-testid="ef-huawei-node" opacity={huaweiOpacity}>
          {/* Background track */}
          <circle
            cx={HUAWEI.cx}
            cy={HUAWEI.cy}
            r={SOC_RADIUS}
            fill="none"
            stroke="rgba(245,158,11,0.12)"
            strokeWidth={4}
          />
          {/* Animated fill arc */}
          <circle
            cx={HUAWEI.cx}
            cy={HUAWEI.cy}
            r={SOC_RADIUS}
            className="soc-arc--huawei"
            strokeDasharray={`${SOC_CIRCUMFERENCE}`}
            strokeDashoffset={socOffset(huaweiSoc)}
            strokeLinecap="round"
            transform={`rotate(-90, ${HUAWEI.cx}, ${HUAWEI.cy})`}
          />

          {/* Node circle */}
          <circle
            cx={HUAWEI.cx}
            cy={HUAWEI.cy}
            r={NODE_R}
            fill={huaweiColor}
            fillOpacity={0.15}
            stroke={huaweiColor}
            strokeWidth={2.5}
          />
          {/* Battery icon */}
          <text x={HUAWEI.cx} y={HUAWEI.cy + 5} textAnchor="middle" fontSize={16} fill={huaweiColor} fontFamily="serif">
            🔋
          </text>

          {/* Offline indicator */}
          {hasData && !huaweiAvailable && (
            <text x={HUAWEI.cx + 22} y={HUAWEI.cy - 18} textAnchor="middle" fontSize={14} fill="var(--accent-red)" fontWeight={700}>
              x
            </text>
          )}

          {/* SoC value */}
          <text x={HUAWEI.cx} y={HUAWEI.cy - NODE_R - 12} className="node-value">
            {huaweiValue}
          </text>
          {/* Label */}
          <text x={HUAWEI.cx} y={HUAWEI.cy + NODE_R + 16} className="node-label">
            Huawei
          </text>
          {/* Power label below */}
          <text x={HUAWEI.cx} y={HUAWEI.cy + NODE_R + 32} className="node-label" fontSize={11}>
            {huaweiPowerLabel}
          </text>
        </g>

        {/* -- Victron SoC arc -- */}
        <g data-testid="ef-victron-node" opacity={victronOpacity}>
          {/* Background track */}
          <circle
            cx={VICTRON.cx}
            cy={VICTRON.cy}
            r={SOC_RADIUS}
            fill="none"
            stroke="rgba(139,92,246,0.12)"
            strokeWidth={4}
          />
          {/* Animated fill arc */}
          <circle
            cx={VICTRON.cx}
            cy={VICTRON.cy}
            r={SOC_RADIUS}
            className="soc-arc--victron"
            strokeDasharray={`${SOC_CIRCUMFERENCE}`}
            strokeDashoffset={socOffset(victronSoc)}
            strokeLinecap="round"
            transform={`rotate(-90, ${VICTRON.cx}, ${VICTRON.cy})`}
          />

          {/* Node circle */}
          <circle
            cx={VICTRON.cx}
            cy={VICTRON.cy}
            r={NODE_R}
            fill={victronColor}
            fillOpacity={0.15}
            stroke={victronColor}
            strokeWidth={2.5}
          />
          {/* Battery icon */}
          <text x={VICTRON.cx} y={VICTRON.cy + 5} textAnchor="middle" fontSize={16} fill={victronColor} fontFamily="serif">
            🔋
          </text>

          {/* Offline indicator */}
          {hasData && !victronAvailable && (
            <text x={VICTRON.cx + 22} y={VICTRON.cy - 18} textAnchor="middle" fontSize={14} fill="var(--accent-red)" fontWeight={700}>
              x
            </text>
          )}

          {/* SoC value */}
          <text x={VICTRON.cx} y={VICTRON.cy - NODE_R - 12} className="node-value">
            {victronValue}
          </text>
          {/* Label */}
          <text x={VICTRON.cx} y={VICTRON.cy + NODE_R + 16} className="node-label">
            Victron
          </text>
          {/* Power label below */}
          <text x={VICTRON.cx} y={VICTRON.cy + NODE_R + 32} className="node-label" fontSize={11}>
            {victronPowerLabel}
          </text>
        </g>

        {/* -- PV node -- */}
        <circle
          cx={PV.cx}
          cy={PV.cy}
          r={NODE_R}
          fill={pvColor}
          fillOpacity={0.15}
          stroke={pvColor}
          strokeWidth={2.5}
        />
        <text x={PV.cx} y={PV.cy + 5} textAnchor="middle" fontSize={18} fill={pvColor} fontFamily="serif">
          ☀
        </text>
        <text x={PV.cx} y={PV.cy - NODE_R - 8} className="node-value">
          {pvValue}
        </text>
        <text x={PV.cx} y={PV.cy + NODE_R + 16} className="node-label">
          Solar
        </text>

        {/* -- Home node -- */}
        <circle
          cx={HOME.cx}
          cy={HOME.cy}
          r={NODE_R}
          fill={homeColor}
          fillOpacity={0.15}
          stroke={homeColor}
          strokeWidth={2.5}
        />
        <text x={HOME.cx} y={HOME.cy + 5} textAnchor="middle" fontSize={18} fill={homeColor} fontFamily="serif">
          🏠
        </text>
        <text x={HOME.cx} y={HOME.cy - NODE_R - 8} className="node-value">
          {homeValue}
        </text>
        <text x={HOME.cx} y={HOME.cy + NODE_R + 16} className="node-label">
          Home
        </text>

        {/* -- Grid node -- */}
        <circle
          cx={GRID.cx}
          cy={GRID.cy}
          r={NODE_R}
          fill={gridColor}
          fillOpacity={0.15}
          stroke={gridColor}
          strokeWidth={2.5}
        />
        <text x={GRID.cx} y={GRID.cy + 5} textAnchor="middle" fontSize={18} fill={gridColor} fontFamily="serif">
          ⚡
        </text>
        <text x={GRID.cx} y={GRID.cy + NODE_R + 16} className="node-label">
          Grid
        </text>
        {homeToGridActive && (
          <text
            x={GRID.cx}
            y={GRID.cy + NODE_R + 32}
            textAnchor="middle"
            fontSize={11}
            fontWeight={600}
            fill="var(--accent-green)"
            data-testid="ef-export-label"
          >
            EXPORT
          </text>
        )}
        <text x={GRID.cx} y={GRID.cy - NODE_R - 8} className="node-value">
          {gridValue}
        </text>
      </svg>

      {/* Null-state indicator (shown while connecting) */}
      {!hasData && (
        <p className="energy-flow-connecting">Waiting for live data...</p>
      )}
    </section>
  );
}
