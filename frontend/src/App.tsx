/**
 * App — root component for the EMS dashboard.
 *
 * Data flow:
 *   1. useEmsSocket connects to /api/ws/state (relayed via Vite proxy in dev,
 *      or served by FastAPI directly in prod).
 *   2. While WS is connected, WS data drives all three components.
 *   3. If WS has failed at least once (retryCount > 0) and is not connected,
 *      useEmsState polling fallback supplies pool + devices; tariff shows null.
 *   4. PoolOverview always receives `connected` from the WS hook so it renders
 *      the "⚠ Disconnected" banner when the WS is down.
 *
 * Routing:
 *   - /login renders the Login page; / renders the main dashboard.
 *   - If the backend returns 401, the app redirects to /login.
 */
import React, { useState, useEffect } from "react";
import { Route, Switch, useLocation } from "wouter";
import { useEmsSocket } from "./hooks/useEmsSocket";
import { useEmsState } from "./hooks/useEmsState";
import { EnergyFlowCard } from "./components/EnergyFlowCard";
import { BatteryStatus } from "./components/BatteryStatus";
import { DecisionLog } from "./components/DecisionLog";
import { DeviceDetail } from "./components/DeviceDetail";
import { TariffCard } from "./components/TariffCard";
import { OptimizationCard } from "./components/OptimizationCard";
import { EvccCard } from "./components/EvccCard";
import { LoadsCard } from "./components/LoadsCard";
import { useDecisions } from "./hooks/useDecisions";
import { useForecast } from "./hooks/useForecast";
import { ForecastCard } from "./components/ForecastCard";
import { Login } from "./pages/Login";
import type { PoolState, DevicesPayload } from "./types";

// Build WS URL dynamically from window.location so it works under both
// direct access (ws://host:8000/api/ws/state) and HA Ingress
// (wss://ha.local/api/hassio_ingress/{token}/api/ws/state).
function buildWsUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = new URL("./api/ws/state", location.href);
  wsUrl.protocol = proto;
  return wsUrl.href;
}
const WS_URL = buildWsUrl();

/**
 * FallbackConsumer — renders when WS has disconnected. Calls useEmsState()
 * and passes results up via callbacks. Kept as a child component so the hook
 * is called unconditionally within its own component scope.
 */
function FallbackConsumer({
  onPool,
  onDevices,
}: {
  onPool: (v: PoolState | null) => void;
  onDevices: (v: DevicesPayload | null) => void;
}) {
  const { pool, devices } = useEmsState();

  // Sync to parent on each render. Referential equality is not guaranteed,
  // but the parent guards against duplicates with its own state update logic.
  React.useEffect(() => {
    onPool(pool);
  }, [pool, onPool]);

  React.useEffect(() => {
    onDevices(devices);
  }, [devices, onDevices]);

  return null;
}

/**
 * DashboardLayout — the main dashboard view, shown at /.
 */
function DashboardLayout() {
  const ws = useEmsSocket(WS_URL);
  const useFallback = !ws.connected && ws.retryCount > 0;
  const decisions = useDecisions(20, 30_000);
  const forecast = useForecast(60_000);

  const [fbPool, setFbPool] = useState<PoolState | null>(null);
  const [fbDevices, setFbDevices] = useState<DevicesPayload | null>(null);

  const handleFbPool = React.useCallback((v: PoolState | null) => setFbPool(v), []);
  const handleFbDevices = React.useCallback((v: DevicesPayload | null) => setFbDevices(v), []);

  // Prefer WS data; fall back to polling data when WS is unavailable.
  const pool = ws.data?.pool ?? (useFallback ? fbPool : null);
  const devices = ws.data?.devices ?? (useFallback ? fbDevices : null);
  const tariff = ws.data?.tariff ?? null;
  const optimization = ws.data?.optimization ?? null;
  const evcc = ws.data?.evcc ?? null;
  const haMqttConnected = ws.data?.ha_mqtt_connected ?? false;
  const loads = ws.data?.loads ?? null;

  return (
    <div className="app">
      <header className="app-header">
        <h1 className="app-title">EMS Dashboard</h1>
        <span className="ws-status">
          {ws.connected ? (
            <span className="ws-badge ws-badge--connected">● Live</span>
          ) : ws.retryCount === 0 ? (
            <span className="ws-badge ws-badge--connecting">◌ Connecting…</span>
          ) : (
            <span className="ws-badge ws-badge--disconnected">
              ⚠ Disconnected (retry {ws.retryCount})
            </span>
          )}
        </span>
      </header>

      <main className="app-main">
        {/* Activate polling fallback only when WS has failed */}
        {useFallback && (
          <FallbackConsumer onPool={handleFbPool} onDevices={handleFbDevices} />
        )}

        <div className="dashboard-grid">
          <EnergyFlowCard pool={pool} devices={devices} />
          <BatteryStatus pool={pool} devices={devices} connected={ws.connected} />
          <DecisionLog decisions={decisions} />
          <OptimizationCard optimization={optimization} />
          <ForecastCard forecast={forecast} />
          <TariffCard tariff={tariff} />
          <LoadsCard loads={loads} />
          <EvccCard
            evcc={evcc}
            controlState={pool?.control_state ?? "IDLE"}
            haMqttConnected={haMqttConnected}
          />
          <DeviceDetail devices={devices} pool={pool} />
        </div>
      </main>

      <footer className="app-footer">
        <span>EMS · M004</span>
      </footer>
    </div>
  );
}

/**
 * App — SPA root. Redirects to /login on 401, otherwise renders dashboard.
 */
export default function App() {
  const [, setLocation] = useLocation();

  // On mount: check auth status. If 401, redirect to /login.
  // Silently ignore errors (no backend in preview/test environment).
  useEffect(() => {
    fetch("./api/state")
      .then((r) => {
        if (r.status === 401) {
          setLocation("/login");
        }
      })
      .catch(() => {
        // No backend available (preview/test environment)
      });
  }, [setLocation]);

  return (
    <Switch>
      <Route path="/login">
        <Login />
      </Route>
      <Route path="/">
        <DashboardLayout />
      </Route>
    </Switch>
  );
}
