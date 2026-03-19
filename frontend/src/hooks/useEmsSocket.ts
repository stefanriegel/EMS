/**
 * useEmsSocket — WebSocket hook with exponential-backoff auto-reconnect.
 *
 * Connects to `url` on mount, parses incoming JSON frames as WsPayload,
 * and automatically reconnects on close/error using:
 *   delay = Math.min(1000 * 2^retryCount, 30_000) ms
 *
 * Observability:
 *   - console.log on connect, disconnect, and each reconnect attempt with
 *     timestamp and retryCount so browser DevTools show connection lifecycle.
 *   - Returns { connected: false, retryCount: N } on disconnect so callers
 *     can render a "⚠ Disconnected" indicator.
 */
import { useState, useEffect, useRef, useCallback } from "react";
import type { WsPayload } from "../types";

export interface EmsSocketState {
  data: WsPayload | null;
  connected: boolean;
  retryCount: number;
}

export function useEmsSocket(url: string): EmsSocketState {
  const [data, setData] = useState<WsPayload | null>(null);
  const [connected, setConnected] = useState(false);
  const [retryCount, setRetryCount] = useState(0);

  // Stable refs so the reconnect timer closure always has current values
  const retryCountRef = useRef(0);
  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  const connect = useCallback(() => {
    if (unmountedRef.current) return;

    const ts = new Date().toISOString();
    const attempt = retryCountRef.current;
    if (attempt > 0) {
      console.log(`[useEmsSocket] reconnect attempt ${attempt} at ${ts}`);
    } else {
      console.log(`[useEmsSocket] connecting to ${url} at ${ts}`);
    }

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (unmountedRef.current) {
        ws.close();
        return;
      }
      console.log(`[useEmsSocket] connected at ${new Date().toISOString()}`);
      retryCountRef.current = 0;
      setRetryCount(0);
      setConnected(true);
    };

    ws.onmessage = (event: MessageEvent) => {
      if (unmountedRef.current) return;
      try {
        const parsed = JSON.parse(event.data as string) as WsPayload;
        setData(parsed);
      } catch (err) {
        console.warn("[useEmsSocket] failed to parse message:", err);
      }
    };

    ws.onclose = () => {
      if (unmountedRef.current) return;
      const now = new Date().toISOString();
      console.log(`[useEmsSocket] disconnected at ${now}; retryCount=${retryCountRef.current}`);
      setConnected(false);

      const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30_000);
      retryCountRef.current += 1;
      setRetryCount(retryCountRef.current);

      console.log(`[useEmsSocket] scheduling reconnect in ${delay}ms (attempt ${retryCountRef.current})`);
      timerRef.current = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      // onerror is always followed by onclose; close handles the reconnect.
      console.warn(`[useEmsSocket] socket error at ${new Date().toISOString()}`);
    };
  }, [url]); // url is stable (passed from App top-level)

  useEffect(() => {
    unmountedRef.current = false;
    retryCountRef.current = 0;
    connect();

    return () => {
      unmountedRef.current = true;
      // Cancel any pending reconnect timer
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      // Close existing socket without triggering the reconnect path
      if (wsRef.current !== null) {
        wsRef.current.onclose = null;
        wsRef.current.onerror = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  return { data, connected, retryCount };
}
