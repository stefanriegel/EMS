/**
 * useEmsState — polling fallback hook.
 *
 * Fetches /api/state and /api/devices every 5s when WebSocket is unavailable.
 * Uses AbortController for cleanup on unmount so no stale setState calls.
 *
 * Returns { pool, devices, connected } where connected=true when the last
 * fetch succeeded (used by App.tsx to decide whether to show stale-data badge).
 */
import { useState, useEffect } from "react";
import type { PoolState, DevicesPayload } from "../types";

export interface EmsPollingState {
  pool: PoolState | null;
  devices: DevicesPayload | null;
  connected: boolean;
}

const POLL_INTERVAL_MS = 5_000;

export function useEmsState(): EmsPollingState {
  const [pool, setPool] = useState<PoolState | null>(null);
  const [devices, setDevices] = useState<DevicesPayload | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let aborted = false;
    const controllers: AbortController[] = [];

    async function fetchAll() {
      const ctrl = new AbortController();
      controllers.push(ctrl);
      try {
        const [poolRes, devicesRes] = await Promise.all([
          fetch("./api/state", { signal: ctrl.signal }),
          fetch("./api/devices", { signal: ctrl.signal }),
        ]);
        if (aborted) return;

        if (poolRes.ok) {
          const poolData = await poolRes.json() as PoolState;
          if (!aborted) setPool(poolData);
        }
        if (devicesRes.ok) {
          const devData = await devicesRes.json() as DevicesPayload;
          if (!aborted) setDevices(devData);
        }
        if (!aborted) setConnected(poolRes.ok && devicesRes.ok);
      } catch (err) {
        if (aborted) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        console.warn("[useEmsState] fetch failed:", err);
        setConnected(false);
      }
    }

    void fetchAll();
    const intervalId = setInterval(() => void fetchAll(), POLL_INTERVAL_MS);

    return () => {
      aborted = true;
      clearInterval(intervalId);
      controllers.forEach((c) => c.abort());
    };
  }, []);

  return { pool, devices, connected };
}
