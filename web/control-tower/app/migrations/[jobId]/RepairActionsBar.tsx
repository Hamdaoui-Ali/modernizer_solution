"use client";

import { useState } from "react";
import { RepairRevisionDialog } from "./RepairRevisionDialog";

export function RepairActionsBar({
  onViewDiff,
  onViewReviewerOpinion,
  onViewFilesChanged,
  onViewAttemptHistory,
  onRequestRevision,
  onApproveSandboxApply,
  onReject,
  revisionPending,
  approvePending,
  approveEnabled,
  checksumMismatch,
  rejectDisabled,
  requestRevisionEnabled = true,
}: {
  onViewDiff: () => void;
  onViewReviewerOpinion: () => void;
  onViewFilesChanged: () => void;
  onViewAttemptHistory: () => void;
  onRequestRevision: (instruction: string) => Promise<void>;
  onApproveSandboxApply?: () => void;
  onReject?: () => void;
  revisionPending: boolean;
  approvePending?: boolean;
  approveEnabled?: boolean;
  checksumMismatch?: boolean;
  rejectDisabled?: boolean;
  requestRevisionEnabled?: boolean;
}) {
  const [showDialog, setShowDialog] = useState(false);

  async function handleSubmit(instruction: string) {
    await onRequestRevision(instruction);
    setShowDialog(false);
  }

  return (
    <div className="repair-actions-bar" data-testid="repair-actions-bar">
      <div className="repair-actions-readonly">
        <strong>View</strong>
        <button type="button" onClick={onViewDiff} data-testid="action-view-diff">
          View diff
        </button>
        <button type="button" onClick={onViewReviewerOpinion} data-testid="action-view-opinion">
          View reviewer opinion
        </button>
        <button type="button" onClick={onViewFilesChanged} data-testid="action-view-files">
          View files changed
        </button>
        <button type="button" onClick={onViewAttemptHistory} data-testid="action-view-history">
          View attempt history
        </button>
      </div>
      <div className="repair-actions-mutation">
        <strong>Actions</strong>
        <button
          type="button"
          onClick={() => setShowDialog(true)}
          disabled={revisionPending || !requestRevisionEnabled}
          data-testid="action-request-revision"
        >
          Request revision
        </button>
        {approveEnabled && onApproveSandboxApply ? (
          <button
            type="button"
            onClick={onApproveSandboxApply}
            disabled={approvePending || checksumMismatch}
            title={
              checksumMismatch
                ? "Cannot approve: diff checksum mismatch detected"
                : approvePending
                  ? "Applying reviewed diff to sandbox..."
                  : "Apply the reviewed diff to the sandbox"
            }
            data-testid="action-approve-sandbox-apply"
          >
            {approvePending ? "Applying..." : "Apply reviewed diff"}
          </button>
        ) : null}
        <button
          type="button"
          disabled={rejectDisabled !== false}
          onClick={onReject}
          title="Reject this repair proposal"
          data-testid="action-reject-repair"
        >
          Reject
        </button>
      </div>
      <RepairRevisionDialog
        open={showDialog}
        onClose={() => setShowDialog(false)}
        onSubmit={handleSubmit}
        pending={revisionPending}
      />
    </div>
  );
}
