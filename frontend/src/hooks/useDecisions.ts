/**
 * useDecisions -- polling hook for the /api/decisions endpoint.
 *
 * Fetches the last N decision entries at a configurable interval.
 * Uses AbortController for cleanup on unmount (same pattern as useEmsState).
 * Returns an empty array when the backend is unavailable.
 */
import { useState, useEffect } from "react";
import type { DecisionEntry } from "../types";

export function useDecisions(limit = 20, intervalMs = 30_000): DecisionEntry[] {
  const [decisions, setDecisions] = useState<DecisionEntry[]>([]);

  useEffect(() => {
    let aborted = false;
    const controllers: AbortController[] = [];

    async function fetchDecisions() {
      const ctrl = new AbortController();
      controllers.push(ctrl);
      try {
        const res = await fetch(`/api/decisions?limit=${limit}`, { signal: ctrl.signal });
        if (aborted) return;
        if (res.ok) {
          const data = (await res.json()) as DecisionEntry[];
          if (!aborted) setDecisions(data);
        }
      } catch (err) {
        if (aborted) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        // Silently fail -- decision log is non-critical
      }
    }

    void fetchDecisions();
    const intervalId = setInterval(() => void fetchDecisions(), intervalMs);

    return () => {
      aborted = true;
      clearInterval(intervalId);
      controllers.forEach((c) => c.abort());
    };
  }, [limit, intervalMs]);

  return decisions;
}
