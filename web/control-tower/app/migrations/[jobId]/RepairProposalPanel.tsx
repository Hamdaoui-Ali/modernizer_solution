"use client";

import { useEffect, useState } from "react";
import {
  getCurrentRepairProposal,
  getRepairProposal,
  getRepairProposalDiff,
  getRepairAttempts,
  requestRepairProposalRevision,
  approveRepairProposal,
  rejectRepairProposal,
  continueRepairProposal,
} from "../../../lib/controlTowerApi";
import type {
  ReviewedDiffProposal,
  SafeDiffPreview as SafeDiffPreviewType,
  RepairAttemptSummary,
  RepairState,
  RepairProposalApproveResponse,
} from "../../../lib/contracts";
import { formatFinalDiffSource } from "../../../lib/contracts";
import { ReviewedDiffTabs } from "./ReviewedDiffTabs";
import { RepairAttemptTimeline } from "./RepairAttemptTimeline";
import { RepairActionsBar } from "./RepairActionsBar";
import { RepairAssistantChat } from "./RepairAssistantChat";

type ProposalState =
  | { status: "loading" }
  | { status: "no-proposal"; repairState?: RepairState }
  | { status: "unavailable"; repairState: RepairState }
  | { status: "error"; message: string }
  | { status: "available"; proposal: ReviewedDiffProposal; repairState?: RepairState };

type DiffState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "available"; diff: SafeDiffPreviewType };

type AttemptsState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "available"; attempts: RepairAttemptSummary[] };

export function RepairProposalPanel({ jobId, repairRefreshKey, onContinuationRefresh }: { jobId: string; repairRefreshKey?: number; onContinuationRefresh?: () => Promise<void> }) {
  const [proposalState, setProposalState] = useState<ProposalState>({ status: "loading" });
  const [diffState, setDiffState] = useState<DiffState>({ status: "idle" });
  const [attemptsState, setAttemptsState] = useState<AttemptsState>({ status: "idle" });
  const [showAttempts, setShowAttempts] = useState(false);
  const [revisionPending, setRevisionPending] = useState(false);
  const [approvePending, setApprovePending] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [applyResult, setApplyResult] = useState<RepairProposalApproveResponse | null>(null);
  const [continuationPending, setContinuationPending] = useState(false);

  useEffect(() => {
    if (!jobId) return;

    let cancelled = false;
    async function load() {
      try {
        const response = await getCurrentRepairProposal(jobId);
        if (cancelled) return;
        if (response.proposal) {
          setProposalState({ status: "available", proposal: response.proposal, repairState: response.repair_state ?? undefined });
          setDiffState({ status: "loading" });
          setAttemptsState({ status: "loading" });
          const [diffResponse, attemptsResponse] = await Promise.all([
            getRepairProposalDiff(jobId, response.proposal.proposal_id).catch(() => null),
            getRepairAttempts(jobId).catch(() => null),
          ]);
          if (cancelled) return;
          if (diffResponse?.safe_diff_preview) {
            setDiffState({ status: "available", diff: diffResponse.safe_diff_preview });
          } else {
            setDiffState({ status: "error", message: diffResponse?.reason ?? "Diff unavailable" });
          }
          if (attemptsResponse?.attempts) {
            setAttemptsState({ status: "available", attempts: attemptsResponse.attempts });
          } else {
            setAttemptsState({ status: "available", attempts: [] });
          }
        } else if (response.repair_state) {
          if (
            response.repair_state.status === "unavailable" ||
            response.repair_state.status === "blocked" ||
            response.repair_state.status === "attempts_exhausted" ||
            response.repair_state.status === "error"
          ) {
            setProposalState({ status: "unavailable", repairState: response.repair_state });
          } else {
            setProposalState({ status: "no-proposal", repairState: response.repair_state });
          }
        } else {
          setProposalState({ status: "no-proposal" });
        }
      } catch (e) {
        if (!cancelled) {
          setProposalState({
            status: "error",
            message: e instanceof Error ? e.message : "Failed to load proposal",
          });
        }
      }
    }
    load();
    return () => { cancelled = true; };
  }, [jobId, repairRefreshKey]);

  async function refreshProposalData(proposalId?: string) {
    if (!jobId) return;
    try {
      const response = proposalId
        ? await getRepairProposal(jobId, proposalId)
        : await getCurrentRepairProposal(jobId);
      if (response.proposal) {
          setProposalState({ status: "available", proposal: response.proposal });
        setDiffState({ status: "loading" });
        setAttemptsState({ status: "loading" });
        const [diffResponse, attemptsResponse] = await Promise.all([
          getRepairProposalDiff(jobId, response.proposal.proposal_id).catch(() => null),
          getRepairAttempts(jobId).catch(() => null),
        ]);
        if (diffResponse?.safe_diff_preview) {
          setDiffState({ status: "available", diff: diffResponse.safe_diff_preview });
        } else {
          setDiffState({ status: "error", message: diffResponse?.reason ?? "Diff unavailable" });
        }
        if (attemptsResponse?.attempts) {
          setAttemptsState({ status: "available", attempts: attemptsResponse.attempts });
        } else {
          setAttemptsState({ status: "available", attempts: [] });
        }
      } else if (response.repair_state) {
        if (
          response.repair_state.status === "unavailable" ||
          response.repair_state.status === "blocked" ||
          response.repair_state.status === "attempts_exhausted" ||
          response.repair_state.status === "error"
        ) {
          setProposalState({ status: "unavailable", repairState: response.repair_state });
        } else {
          setProposalState({ status: "no-proposal", repairState: response.repair_state });
        }
      } else {
        setProposalState({ status: "no-proposal" });
      }
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Repair mutation failed.");
      setProposalState({
        status: "error",
        message: "Failed to refresh proposal data",
      });
    }
  }

  async function handleNewProposal(newProposalId: string) {
    await refreshProposalData(newProposalId);
  }

  async function handleRefreshProposal() {
    if (proposalState.status !== "available") return;
    await refreshProposalData(proposalState.proposal.proposal_id);
  }

  async function handleRequestRevision(instruction: string) {
    if (!jobId) return;
    setRevisionPending(true);
    try {
      const state = proposalState;
      if (state.status !== "available") return;
      await requestRepairProposalRevision(jobId, state.proposal.proposal_id, {
        user_instruction: instruction,
        previous_diff_checksum: state.proposal.diff_checksum,
        previous_reviewer_verdict_id:
          state.proposal.reviewer_verdict?.reviewer_verdict_id ?? "",
      });
      await refreshProposalData();
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Repair mutation failed.");
      // Safe error display — no raw paths/stacks
    } finally {
      setRevisionPending(false);
    }
  }

  async function handleApproveSandboxApply() {
    if (!jobId) return;
      const state = proposalState;
      if (state.status !== "available") return;
      setApprovePending(true);
      try {
        const result = await approveRepairProposal(jobId, state.proposal.proposal_id, {
          proposal_id: state.proposal.proposal_id,
          diff_checksum: state.proposal.diff_checksum,
          idempotency_key: crypto.randomUUID(),
        });
        setApplyResult(result);
        await refreshProposalData();
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Repair Apply failed.");
      // Safe error display — no raw paths/stacks
    } finally {
      setApprovePending(false);
    }
  }

  async function handleReject() {
    if (proposalState.status !== "available") return;
    setMutationError(null);
    try {
      await rejectRepairProposal(jobId, proposalState.proposal.proposal_id, {
        proposal_id: proposalState.proposal.proposal_id,
        idempotency_key: crypto.randomUUID(),
      });
      await refreshProposalData();
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Repair rejection failed.");
    }
  }

  async function handleContinue() {
    if (proposal.status !== "approved_applied") return;
    setContinuationPending(true);
    setMutationError(null);
    try {
      await continueRepairProposal(jobId, proposal.proposal_id);
      await refreshProposalData(proposal.proposal_id);
      await onContinuationRefresh?.();
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Migration continuation failed.");
    } finally {
      setContinuationPending(false);
    }
  }

  if (proposalState.status === "loading") {
    return (
      <section className="panel" data-testid="repair-proposal-panel">
        <h2>Repair Proposal</h2>
        <p className="meta">Loading proposal...</p>
      </section>
    );
  }

  if (proposalState.status === "no-proposal") {
    return (
      <section className="panel" data-testid="repair-proposal-panel">
        <h2>Repair Proposal</h2>
        <p className="meta">No repair proposal available for this job.</p>
        {proposalState.repairState?.status ? (
          <p className="meta">Repair state: {proposalState.repairState.status}</p>
        ) : null}
      </section>
    );
  }

  if (proposalState.status === "unavailable") {
    const rs = proposalState.repairState;
    return (
      <section className="panel" data-testid="repair-proposal-panel">
        <h2>Reviewed Repair</h2>
        <p className="meta">Reviewed repair unavailable</p>
        <p className="meta">No reviewed diff was created.</p>
        {rs.reason_code && <p className="meta">Reason: {rs.reason_code}</p>}
        {rs.detail && <p className="meta">Details: {rs.detail}</p>}
        {rs.created_at && <p className="meta">At: {rs.created_at}</p>}
        <p className="meta">No apply action is available.</p>
        {rs.allowed_actions?.includes("view_failure_summary") && (
          <p className="meta">See failure summary for details.</p>
        )}
      </section>
    );
  }

  if (proposalState.status === "error") {
    return (
      <section className="panel" data-testid="repair-proposal-panel">
        <h2>Repair Proposal</h2>
        <p className="meta" role="alert">{proposalState.message}</p>
      </section>
    );
  }

  const proposal = proposalState.proposal;
  const repairState = proposalState.repairState;
  const diff = diffState.status === "available" ? diffState.diff : null;
  const attempts = attemptsState.status === "available" ? attemptsState.attempts : [];

  return (
    <section className="repair-workspace" data-testid="repair-proposal-panel">
      <header className="repair-workspace-header">
        <div><h2>Repair Workspace</h2><span className="meta">Proposal {proposal.proposal_id}</span></div>
        <div className="repair-workspace-meta"><strong>{proposal.status.replace(/_/g, " ").toUpperCase()}</strong><span>Attempt {proposal.attempt_number ?? "—"}</span><span>{formatFinalDiffSource(proposal.final_diff_source)}</span></div>
      </header>

      <div className="table-list">
        <div className="table-row">
          <span className="meta">Status</span>
          <strong>{proposal.status.replace(/_/g, " ").toUpperCase()}</strong>
        </div>
        {repairState?.reason_code && (
          <p className="meta" role="alert">Apply reason: {repairState.reason_code}</p>
        )}
        {repairState?.detail && (
          <p className="meta" role="alert">Apply details: {repairState.detail}</p>
        )}
        {proposal.stage_index != null && (
          <div className="table-row">
            <span className="meta">Stage</span>
            <strong>{proposal.stage_index}</strong>
          </div>
        )}
        {proposal.route_step_index != null && (
          <div className="table-row">
            <span className="meta">Route step</span>
            <strong>{proposal.route_step_index}</strong>
          </div>
        )}
        {proposal.attempt_number != null && (
          <div className="table-row">
            <span className="meta">Attempt</span>
            <strong>{proposal.attempt_number}</strong>
          </div>
        )}
        {proposal.revision_number != null && (
          <div className="table-row">
            <span className="meta">Revision</span>
            <strong>{proposal.revision_number}</strong>
          </div>
        )}
        {proposal.diff_checksum && (
          <div className="table-row">
            <span className="meta">Diff checksum</span>
            <strong className="checksum">{proposal.diff_checksum}</strong>
          </div>
        )}
        {proposal.reviewer_verdict?.decision && (
          <div className="table-row">
            <span className="meta">Reviewer decision</span>
            <strong>{proposal.reviewer_verdict.decision}</strong>
          </div>
        )}
        {proposal.final_diff_source && (
          <div className="table-row">
            <span className="meta">Final diff source</span>
            <strong>{formatFinalDiffSource(proposal.final_diff_source)}</strong>
          </div>
        )}
        {proposal.validation_proof_status && (
          <div className="table-row">
            <span className="meta">Validation proof</span>
            <strong>{proposal.validation_proof_status.replace(/_/g, " ")}</strong>
          </div>
        )}
        {proposal.generation_reason && proposal.generation_status === "failed" && (
          <p className="meta">Generation reason: {proposal.generation_reason}</p>
        )}
      </div>

      {proposal.failure_summary && (
        <div className="failure-summary" data-testid="failure-summary">
          <strong>Failure Summary</strong>
          <p className="meta">{proposal.failure_summary}</p>
        </div>
      )}

      <div
        className="repair-assistant-collapsible"
        data-testid="repair-assistant-collapsible"
      >
        <h3>Assistant conversation</h3>
        <div className="repair-assistant-content">
          <RepairAssistantChat
            jobId={jobId}
            proposal={proposal}
            proposalId={proposal.proposal_id}
            attemptNumber={proposal.attempt_number}
            reviewerDecision={proposal.reviewer_verdict?.decision ?? null}
            finalDiffSource={proposal.final_diff_source ?? null}
            diffChecksum={proposal.diff_checksum}
            onNewProposal={handleNewProposal}
            onRefreshProposal={handleRefreshProposal}
          />
        </div>
      </div>

      <div className="repair-workspace-side"><h3>Current proposed repair</h3><ReviewedDiffTabs proposal={proposal} diff={diff} /></div>

      {mutationError && <p className="error" role="alert">{mutationError}</p>}
      {applyResult?.apply_succeeded && applyResult.validation_succeeded && (
        <div className="table-list" role="status">
          <p className="meta">Patch applied</p>
          <p className="meta">Build passed</p>
          <p className="meta">Test phase completed — zero tests discovered (warning)</p>
          {applyResult.continuation_status === "continuation_failed" && <p className="error">Migration continuation failed</p>}
        </div>
      )}
      {proposal.status === "approved_applied" && (
        <div className="table-list" data-testid="repair-continuation-actions">
          <p className="meta">Migration continuation failed or is awaiting recovery.</p>
          <button type="button" onClick={() => void handleContinue()} disabled={continuationPending}>
            {continuationPending ? "Retrying continuation..." : "Retry continuation"}
          </button>
          <button type="button" onClick={() => setShowAttempts(true)}>View validation proof</button>
          <button type="button" onClick={() => document.querySelector('[data-testid="tab-diff"]')?.dispatchEvent(new MouseEvent("click", { bubbles: true }))}>View patched diff</button>
        </div>
      )}

      {showAttempts && (
        <RepairAttemptTimeline attempts={attempts} />
      )}

      <RepairActionsBar
        onViewDiff={() => {
          const tabEl = document.querySelector('[data-testid="tab-diff"]') as HTMLButtonElement | null;
          tabEl?.click();
        }}
        onViewReviewerOpinion={() => {
          const tabEl = document.querySelector('[data-testid="tab-reviewer-opinion"]') as HTMLButtonElement | null;
          tabEl?.click();
        }}
        onViewFilesChanged={() => {
          const tabEl = document.querySelector('[data-testid="tab-files-changed"]') as HTMLButtonElement | null;
          tabEl?.click();
        }}
        onViewAttemptHistory={() => setShowAttempts((v) => !v)}
        onRequestRevision={handleRequestRevision}
        onApproveSandboxApply={handleApproveSandboxApply}
        onReject={handleReject}
        requestRevisionEnabled={proposal.allowed_actions?.includes("request_revision") === true}
        revisionPending={revisionPending}
        approvePending={approvePending}
        approveEnabled={
          (proposal.status === "user_review_required" || proposal.status === "reviewer_accepted") &&
          proposal.allowed_actions?.includes("approve_sandbox_apply") &&
          diff?.checksum_mismatch !== true &&
          diff?.parse_status !== "unparseable" &&
          diff?.parse_status !== "hunk_count_mismatch"
        }
        checksumMismatch={diff?.checksum_mismatch ?? false}
        rejectDisabled={!proposal.allowed_actions?.includes("reject")}
      />

      <style>{`
        .cockpit-layout > .repair-workspace { grid-column: 1 / -1; }
        .repair-workspace { min-height: calc(100vh - 4rem); padding: 1rem; background: #f8fafc; display:grid; grid-template-columns:minmax(0,1.2fr) minmax(22rem,.8fr); gap:1rem; }
        .repair-workspace > :not(.repair-assistant-collapsible):not(.repair-workspace-side) { grid-column:1 / -1; }
        .repair-workspace-header { display:flex; justify-content:space-between; gap:1rem; align-items:center; margin-bottom:1rem; }
        .repair-workspace-meta { display:flex; gap:1rem; flex-wrap:wrap; align-items:center; }
        .repair-workspace-side { grid-column:2; grid-row:3 / span 2; background:#fff; border:1px solid #e2e8f0; border-radius:.75rem; padding:1rem; }
        .repair-assistant-collapsible { grid-column:1; }
        .repair-workspace h3 { margin:.25rem 0 .75rem; }
        .repair-assistant-collapsible {
          margin: 0.5rem 0;
          border: 1px solid #e2e8f0;
          border-radius: 0.5rem;
          background: #fff;
        }
        .repair-assistant-summary {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0.5rem 0.75rem;
          cursor: pointer;
          font-size: 0.85rem;
          font-weight: 500;
          color: #475569;
          user-select: none;
          list-style: none;
        }
        .repair-assistant-summary::-webkit-details-marker {
          display: none;
        }
        .repair-assistant-summary:hover {
          background: #f8fafc;
        }
        .repair-assistant-summary:focus-visible {
          outline: 2px solid #6366f1;
          outline-offset: 2px;
        }
        .rac-summary-label {
          display: flex;
          align-items: center;
          gap: 0.4rem;
        }
        .rac-toggle-icon {
          font-size: 1.1rem;
          font-weight: 600;
          color: #94a3b8;
          line-height: 1;
        }
        @media (max-width: 900px) { .repair-workspace { display:block; } .repair-workspace-header { align-items:flex-start; flex-direction:column; } .repair-workspace-side { margin-top:1rem; } }
      `}</style>
    </section>
  );
}
