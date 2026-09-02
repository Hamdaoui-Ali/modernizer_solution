"use client";

import type { RepairAttemptSummary } from "../../../lib/contracts";

function attemptStatusLabel(status: string): string {
  return status.replace(/_/g, " ").toUpperCase();
}

function statusColorClass(status: string): string {
  if (status === "approved_applied") return "status-pass";
  if (status === "approve_failed" || status === "exhausted") return "status-fail";
  return "";
}

export function RepairAttemptTimeline({
  attempts,
}: {
  attempts: RepairAttemptSummary[];
}) {
  if (attempts.length === 0) {
    return (
      <div className="attempt-timeline-empty" data-testid="attempt-timeline-empty">
        <p className="meta">No repair attempts yet.</p>
      </div>
    );
  }

  return (
    <div className="attempt-timeline" data-testid="attempt-timeline">
      <strong>Repair Attempts</strong>
      {attempts.map((attempt) => (
        <div key={attempt.proposal_id} className="attempt-entry" data-testid="attempt-entry">
          <div className="stage-header">
            <span className="meta">Attempt {attempt.attempt_number ?? "?"}</span>
            {attempt.revision_number != null && (
              <span className="meta">Revision {attempt.revision_number}</span>
            )}
            <span className={`status-badge ${statusColorClass(attempt.status)}`}>
              {attemptStatusLabel(attempt.status)}
            </span>
            {attempt.remaining_attempts != null && attempt.remaining_attempts >= 0 && (
              <span className="meta">{attempt.remaining_attempts} remaining</span>
            )}
          </div>
          <p className="meta">Proposal: {attempt.proposal_id}</p>
          {attempt.apply_status && <p className="meta">Apply: {attempt.apply_status}</p>}
          {attempt.rerun_status && <p className="meta">Validation: {attempt.rerun_status}</p>}
          {attempt.rollback_status && <p className="meta">Rollback: {attempt.rollback_status}</p>}
          {attempt.diff_checksum && <p className="checksum">Diff checksum: {attempt.diff_checksum}</p>}
          {attempt.next_gate_status && <p className="meta">Next status: {attempt.next_gate_status}</p>}
          {attempt.status_reason && <p className="meta">Reason: {attempt.status_reason}</p>}
          {attempt.created_at && <p className="meta">Created: {new Date(attempt.created_at).toLocaleString()}</p>}
          {attempt.completed_at && <p className="meta">Completed: {new Date(attempt.completed_at).toLocaleString()}</p>}
          {attempt.status === "exhausted" && (
            <div className="exhausted-notice" data-testid="exhausted-notice">
              <p className="meta">All repair attempts exhausted for this proposal.</p>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
