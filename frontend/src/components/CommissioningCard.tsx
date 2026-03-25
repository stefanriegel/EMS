/**
 * CommissioningCard -- staged rollout controls and shadow mode toggle.
 *
 * Fetches commissioning status from GET /api/health and provides:
 *   - Current stage display with progression timer
 *   - Force Advance button (bypasses time requirement)
 *   - Shadow mode on/off toggle
 */
import { useState, useEffect, useCallback } from "react";

interface CommissioningStatus {
  stage: string;
  shadow_mode: boolean;
  stage_entered_at: string;
  progression: {
    time_in_stage_hours: number;
    min_hours_required: number;
    can_advance: boolean;
  };
}

const stageLabels: Record<string, string> = {
  READ_ONLY: "Read Only",
  SINGLE_BATTERY: "Single Battery",
  DUAL_BATTERY: "Dual Battery",
};

const stageColors: Record<string, string> = {
  READ_ONLY: "#ef4444",
  SINGLE_BATTERY: "#f59e0b",
  DUAL_BATTERY: "#22c55e",
};

export function CommissioningCard() {
  const [status, setStatus] = useState<CommissioningStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(() => {
    fetch("./api/health")
      .then((r) => r.json())
      .then((data) => {
        if (data.commissioning) {
          setStatus(data.commissioning);
          setError(null);
        } else {
          setStatus(null);
        }
      })
      .catch(() => {
        setError("Failed to fetch status");
      });
  }, []);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 15_000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  const handleForceAdvance = async () => {
    setLoading(true);
    try {
      const res = await fetch("./api/commissioning/force-advance", { method: "POST" });
      if (res.ok) {
        fetchStatus();
      } else {
        const body = await res.json();
        setError(body.error ?? "Force advance failed");
      }
    } catch {
      setError("Network error");
    } finally {
      setLoading(false);
    }
  };

  const handleToggleShadow = async () => {
    if (!status) return;
    setLoading(true);
    try {
      const res = await fetch("./api/override/shadow-mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !status.shadow_mode }),
      });
      if (res.ok) {
        fetchStatus();
      } else {
        const body = await res.json();
        setError(body.error ?? "Toggle failed");
      }
    } catch {
      setError("Network error");
    } finally {
      setLoading(false);
    }
  };

  if (status === null && error === null) {
    return null; // Commissioning not configured -- hide card
  }

  if (status === null) {
    return (
      <section className="card commissioning-card">
        <h2 className="card-title">Commissioning</h2>
        <p style={{ color: "#ef4444" }}>{error}</p>
      </section>
    );
  }

  const isFinalStage = status.stage === "DUAL_BATTERY";
  const prog = status.progression;

  return (
    <section className="card commissioning-card">
      <h2 className="card-title">Commissioning</h2>

      {error && <p style={{ color: "#ef4444", fontSize: "0.85rem", margin: "0 0 0.5rem" }}>{error}</p>}

      {/* Stage display */}
      <div className="pool-metrics">
        <div className="metric">
          <span className="metric-label">Stage</span>
          <span
            className="control-badge"
            style={{ background: stageColors[status.stage] ?? "#6b7280" }}
          >
            {stageLabels[status.stage] ?? status.stage}
          </span>
        </div>
        <div className="metric">
          <span className="metric-label">Shadow Mode</span>
          <span
            className="control-badge"
            style={{ background: status.shadow_mode ? "#f59e0b" : "#22c55e" }}
          >
            {status.shadow_mode ? "ON" : "OFF"}
          </span>
        </div>
      </div>

      {/* Progression timer */}
      {!isFinalStage && (
        <div style={{ fontSize: "0.85rem", color: "#9ca3af", margin: "0.5rem 0" }}>
          Time in stage: {prog.time_in_stage_hours.toFixed(1)}h / {prog.min_hours_required}h required
          {prog.can_advance && " (ready to advance)"}
        </div>
      )}

      {/* Controls */}
      <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.75rem", flexWrap: "wrap" }}>
        {!isFinalStage && (
          <button
            onClick={handleForceAdvance}
            disabled={loading}
            style={{
              padding: "0.4rem 0.8rem",
              borderRadius: "0.375rem",
              border: "1px solid #f59e0b",
              background: "transparent",
              color: "#f59e0b",
              cursor: loading ? "wait" : "pointer",
              fontSize: "0.85rem",
            }}
          >
            {loading ? "..." : "Force Advance"}
          </button>
        )}

        <button
          onClick={handleToggleShadow}
          disabled={loading}
          style={{
            padding: "0.4rem 0.8rem",
            borderRadius: "0.375rem",
            border: `1px solid ${status.shadow_mode ? "#22c55e" : "#ef4444"}`,
            background: "transparent",
            color: status.shadow_mode ? "#22c55e" : "#ef4444",
            cursor: loading ? "wait" : "pointer",
            fontSize: "0.85rem",
          }}
        >
          {status.shadow_mode ? "Disable Shadow Mode" : "Enable Shadow Mode"}
        </button>
      </div>
    </section>
  );
}
