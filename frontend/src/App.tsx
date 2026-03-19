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
 */
import React, { useState } from "react";
import { useEmsSocket } from "./hooks/useEmsSocket";
import { useEmsState } from "./hooks/useEmsState";
import { PoolOverview } from "./components/PoolOverview";
import { DeviceDetail } from "./components/DeviceDetail";
import { TariffCard } from "./components/TariffCard";
import { OptimizationCard } from "./components/OptimizationCard";
import { EvccCard } from "./components/EvccCard";
import type { PoolState, DevicesPayload } from "./types";

// In production, location.host resolves to the FastAPI server address.
// In development, Vite proxy forwards /api/ws/state → ws://localhost:8000/api/ws/state.
const WS_URL = `ws://${location.host}/api/ws/state`;

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

export default function App() {
  const ws = useEmsSocket(WS_URL);
  const useFallback = !ws.connected && ws.retryCount > 0;

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
          <PoolOverview pool={pool ?? null} connected={ws.connected} />
          <DeviceDetail devices={devices} />
          <TariffCard tariff={tariff} />
          <OptimizationCard optimization={optimization} />
          <EvccCard
            evcc={evcc}
            controlState={pool?.control_state ?? "IDLE"}
            haMqttConnected={haMqttConnected}
          />
        </div>
      </main>

      <footer className="app-footer">
        <span>EMS · M004</span>
      </footer>
    </div>
  );
}
