"use client";

import type { ReviewerVerdictProjection } from "../../../lib/contracts";

function formatVerdictLabel(decision: string): string {
  switch (decision) {
    case "accept":
      return "Accepted";
    case "revise":
      return "Revision Requested";
    case "reject":
      return "Rejected";
    default:
      return "Unknown";
  }
}

function formatVerdictBadgeClass(decision: string): string {
  switch (decision) {
    case "accept":
      return "status-badge completed";
    case "revise":
      return "status-badge blocked";
    case "reject":
      return "status-badge failed";
    default:
      return "status-badge pending";
  }
}

export function ReviewerVerdictCard({
  verdict,
}: {
  verdict: ReviewerVerdictProjection | null;
}) {
  if (!verdict) {
    return (
      <div className="reviewer-verdict-missing" data-testid="reviewer-verdict-missing">
        <p className="meta">No reviewer verdict available.</p>
      </div>
    );
  }

  return (
    <div className="reviewer-verdict-card" data-testid="reviewer-verdict-card">
      <div className="reviewer-verdict-header">
        <strong>Reviewer Verdict</strong>
        <span className={formatVerdictBadgeClass(verdict.decision)} data-testid="verdict-decision">
          {formatVerdictLabel(verdict.decision)}
        </span>
      </div>
      {verdict.reasoning && (
        <div className="reviewer-verdict-section">
          <strong>Reasoning</strong>
          <p className="meta">{verdict.reasoning}</p>
        </div>
      )}
      {verdict.missing_evidence.length > 0 && (
        <div className="reviewer-verdict-section">
          <strong>Missing Evidence</strong>
          <ul className="meta">
            {verdict.missing_evidence.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {verdict.unsafe_assumptions.length > 0 && (
        <div className="reviewer-verdict-section">
          <strong>Unsafe Assumptions</strong>
          <ul className="meta">
            {verdict.unsafe_assumptions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </div>
      )}

    </div>
  );
}
