/**
 * DecisionLog -- audit trail card showing recent dispatch decisions.
 *
 * Shows:
 *   - Last N decisions with relative timestamps and trigger badges
 *   - Expandable detail rows (native <details>) with allocation breakdown
 *   - Empty state when no decisions have been recorded yet
 */
import type { DecisionEntry, InterventionEntry } from "../types";

interface Props {
  decisions: DecisionEntry[];
  interventions?: InterventionEntry[];
}

const interventionColors: Record<string, string> = {
  min_soc_guard: "#ef4444",
  cross_charge: "#f59e0b",
  grid_charge_window: "#06b6d4",
  soc_balance: "#3b82f6",
};

function relativeTime(isoTimestamp: string): string {
  const diff = Date.now() - new Date(isoTimestamp).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const triggerColors: Record<string, string> = {
  role_change: "#f59e0b",
  allocation_shift: "#3b82f6",
  failover: "#ef4444",
  hold_signal: "#06b6d4",
  slot_start: "#22c55e",
  slot_end: "#8b5cf6",
};

const roleLabels: Record<string, string> = {
  PRIMARY_DISCHARGE: "Primary",
  SECONDARY_DISCHARGE: "Secondary",
  CHARGING: "Charging",
  HOLDING: "Holding",
  GRID_CHARGE: "Grid Charge",
};

export function DecisionLog({ decisions, interventions }: Props) {
  const hasInterventions = interventions && interventions.length > 0;

  return (
    <section className="card decision-log" data-testid="decision-log-card">
      <h2 className="card-title">Decision Log</h2>

      {/* Supervisory mode: active interventions */}
      {hasInterventions && (
        <>
          <p style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: "0.5rem" }}>
            Active Interventions
          </p>
          <div className="decision-list" style={{ marginBottom: "1rem" }}>
            {interventions!.map((entry, i) => (
              <details key={`iv-${i}`} className="decision-entry">
                <summary className="decision-summary">
                  <span className="decision-time">{relativeTime(entry.timestamp)}</span>
                  <span
                    className="decision-trigger"
                    style={{ background: interventionColors[entry.intervention_type] ?? "#6b7280" }}
                  >
                    {entry.intervention_type.replace(/_/g, " ")}
                  </span>
                  <span className="decision-reason">{entry.reason}</span>
                </summary>
                <div className="decision-detail">
                  <div className="decision-detail-row">
                    <span className="decision-detail-label">Target</span>
                    <span className="decision-detail-value">{entry.target_system}</span>
                  </div>
                  <div className="decision-detail-row">
                    <span className="decision-detail-label">Action</span>
                    <span className="decision-detail-value">{entry.action}</span>
                  </div>
                </div>
              </details>
            ))}
          </div>
        </>
      )}

      {/* Legacy mode: dispatch decisions */}
      {decisions.length === 0 && !hasInterventions ? (
        <p className="decision-log-empty">No dispatch decisions yet</p>
      ) : decisions.length > 0 ? (
        <div className="decision-list">
          {decisions.map((entry, i) => (
            <details key={i} className="decision-entry">
              <summary className="decision-summary">
                <span className="decision-time">{relativeTime(entry.timestamp)}</span>
                <span
                  className="decision-trigger"
                  style={{ background: triggerColors[entry.trigger] ?? "#6b7280" }}
                >
                  {entry.trigger.replace("_", " ")}
                </span>
                <span className="decision-reason">{entry.reasoning}</span>
              </summary>
              <div className="decision-detail">
                <div className="decision-detail-row">
                  <span className="decision-detail-label">Huawei</span>
                  <span className="decision-detail-value">
                    {roleLabels[entry.huawei_role] ?? entry.huawei_role} ({entry.huawei_allocation_w} W)
                  </span>
                </div>
                <div className="decision-detail-row">
                  <span className="decision-detail-label">Victron</span>
                  <span className="decision-detail-value">
                    {roleLabels[entry.victron_role] ?? entry.victron_role} ({entry.victron_allocation_w} W)
                  </span>
                </div>
                <div className="decision-detail-row">
                  <span className="decision-detail-label">Target</span>
                  <span className="decision-detail-value">{entry.p_target_w} W</span>
                </div>
              </div>
            </details>
          ))}
        </div>
      ) : null}
    </section>
  );
}
