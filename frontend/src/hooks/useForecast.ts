/**
 * useForecast -- polling hook for the /api/optimization/forecast endpoint.
 *
 * Fetches multi-day solar forecast at a configurable interval.
 * Uses AbortController for cleanup on unmount (same pattern as useDecisions).
 * Returns null when the backend is unavailable or forecast not yet computed.
 */
import { useState, useEffect } from "react";
import type { ForecastPayload } from "../types";

export function useForecast(intervalMs = 60_000): ForecastPayload | null {
  const [forecast, setForecast] = useState<ForecastPayload | null>(null);

  useEffect(() => {
    let aborted = false;
    const controllers: AbortController[] = [];

    async function fetchForecast() {
      const ctrl = new AbortController();
      controllers.push(ctrl);
      try {
        const res = await fetch("/api/optimization/forecast", { signal: ctrl.signal });
        if (aborted) return;
        if (res.ok) {
          const data = (await res.json()) as ForecastPayload;
          if (!aborted) setForecast(data);
        }
      } catch (err) {
        if (aborted) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        // Silently fail -- forecast is non-critical
      }
    }

    void fetchForecast();
    const intervalId = setInterval(() => void fetchForecast(), intervalMs);

    return () => {
      aborted = true;
      clearInterval(intervalId);
      controllers.forEach((c) => c.abort());
    };
  }, [intervalMs]);

  return forecast;
}
