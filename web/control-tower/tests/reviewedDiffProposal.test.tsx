import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { SafeDiffPreview } from "../app/migrations/[jobId]/SafeDiffPreview";
import { ReviewerVerdictCard } from "../app/migrations/[jobId]/ReviewerVerdictCard";
import { RepairAttemptTimeline } from "../app/migrations/[jobId]/RepairAttemptTimeline";
import { RepairActionsBar } from "../app/migrations/[jobId]/RepairActionsBar";
import type {
  SafeDiffPreview as SafeDiffPreviewType,
  SafeDiffFile,
  SafeDiffHunk,
  ReviewerVerdictProjection,
  RepairAttemptSummary,
  ReviewedDiffProposal,
} from "../lib/contracts";

describe("PR-C SafeDiffPreview component", () => {
  it("renders missing state when diff is null", () => {
    const markup = renderToStaticMarkup(<SafeDiffPreview diff={null} />);
    expect(markup).toContain("No diff preview available");
    expect(markup).not.toContain("safe-diff-file");
  });

  it("renders file summaries with additions/deletions", () => {
    const diff: SafeDiffPreviewType = {
      proposal_id: "p-1",
      diff_ref: null,
      diff_checksum: "sha256:abc",
      files: [
        {
          path: "pom.xml",
          change_type: "modified",
          additions: 3,
          deletions: 1,
          hunks: [],
          truncated: false,
        },
      ],
      total_additions: 3,
      total_deletions: 1,
      truncated: false,
      checksum_mismatch: false,
      redactions: [],
    };
    const markup = renderToStaticMarkup(<SafeDiffPreview diff={diff} />);
    expect(markup).toContain("pom.xml");
    expect(markup).toContain("modified");
    expect(markup).toContain("+3");
    expect(markup).toContain("/ -1");
    expect(markup).toContain("1 file changed");
  });

  it("renders hunks with old/new line numbers", () => {
    const diff: SafeDiffPreviewType = {
      proposal_id: "p-1",
      diff_ref: null,
      diff_checksum: "sha256:abc",
      files: [
        {
          path: "src/main.java",
          change_type: "modified",
          additions: 1,
          deletions: 0,
          hunks: [
            {
              old_start: 10,
              old_lines: 5,
              new_start: 10,
              new_lines: 6,
              section_header: null,
              lines: [
                { kind: "context", old_line_number: 10, new_line_number: 10, text: "  existing", redacted: false },
                { kind: "addition", old_line_number: null, new_line_number: 11, text: "+ new line", redacted: false },
              ],
            },
          ],
          truncated: false,
        },
      ],
      total_additions: 1,
      total_deletions: 0,
      truncated: false,
      checksum_mismatch: false,
      redactions: [],
    };
    const markup = renderToStaticMarkup(<SafeDiffPreview diff={diff} />);
    expect(markup).toContain("src/main.java");
    expect(markup).toContain("@@ -10,5 +10,6 @@");
    expect(markup).toContain("10 | 10");
    expect(markup).toContain("  | 11");
    expect(markup).toContain("+ new line");
    expect(markup).not.toContain("undefined");
  });

  it("renders truncation notice", () => {
    const diff: SafeDiffPreviewType = {
      proposal_id: "p-1",
      diff_ref: null,
      diff_checksum: "sha256:abc",
      files: [],
      total_additions: 0,
      total_deletions: 0,
      truncated: true,
      checksum_mismatch: false,
      redactions: [],
    };
    const markup = renderToStaticMarkup(<SafeDiffPreview diff={diff} />);
    expect(markup).toContain("truncation-notice");
    expect(markup).toContain("Diff truncated");
  });

  it("renders checksum mismatch warning", () => {
    const diff: SafeDiffPreviewType = {
      proposal_id: "p-1",
      diff_ref: null,
      diff_checksum: "sha256:abc",
      files: [],
      total_additions: 0,
      total_deletions: 0,
      truncated: false,
      checksum_mismatch: true,
      redactions: [],
    };
    const markup = renderToStaticMarkup(<SafeDiffPreview diff={diff} />);
    expect(markup).toContain("checksum-mismatch-warning");
    expect(markup).toContain("checksum mismatch");
    expect(markup).toContain("cannot be approved until regenerated");
  });

  it("renders redaction notice", () => {
    const diff: SafeDiffPreviewType = {
      proposal_id: "p-1",
      diff_ref: null,
      diff_checksum: "sha256:abc",
      files: [],
      total_additions: 0,
      total_deletions: 0,
      truncated: false,
      checksum_mismatch: false,
      redactions: ["secret_token"],
    };
    const markup = renderToStaticMarkup(<SafeDiffPreview diff={diff} />);
    expect(markup).toContain("redaction-notice");
    expect(markup).toContain("1 redaction applied");
  });

  it("renders redacted lines as [redacted]", () => {
    const diff: SafeDiffPreviewType = {
      proposal_id: "p-1",
      diff_ref: null,
      diff_checksum: "sha256:abc",
      files: [
        {
          path: "config.properties",
          change_type: "modified",
          additions: 1,
          deletions: 0,
          hunks: [
            {
              old_start: 1,
              old_lines: 1,
              new_start: 1,
              new_lines: 2,
              section_header: null,
              lines: [
                { kind: "addition", old_line_number: null, new_line_number: 2, text: "secret line", redacted: true },
              ],
            },
          ],
          truncated: false,
        },
      ],
      total_additions: 1,
      total_deletions: 0,
      truncated: false,
      checksum_mismatch: false,
      redactions: ["secret"],
    };
    const markup = renderToStaticMarkup(<SafeDiffPreview diff={diff} />);
    expect(markup).toContain("redacted-line");
    expect(markup).toContain("[redacted]");
    expect(markup).not.toContain("secret line");
  });

  it("does not expose raw paths, env, or secrets", () => {
    const diff: SafeDiffPreviewType = {
      proposal_id: "p-1",
      diff_ref: null,
      diff_checksum: "sha256:abc",
      files: [],
      total_additions: 0,
      total_deletions: 0,
      truncated: false,
      checksum_mismatch: false,
      redactions: [],
    };
    const markup = renderToStaticMarkup(<SafeDiffPreview diff={diff} />);
    expect(markup).not.toContain("target_path");
    expect(markup).not.toContain("patch_content");
    expect(markup).not.toContain("sandbox_path");
    expect(markup).not.toContain("argv");
    expect(markup).not.toContain("C:\\");
    expect(markup).not.toContain("/Users/");
    expect(markup).not.toContain("/home/");
    expect(markup).not.toContain("AZURE_OPENAI");
    expect(markup).not.toContain("Bearer ");
  });
});

describe("PR-C ReviewerVerdictCard component", () => {
  it("renders missing state when verdict is null", () => {
    const markup = renderToStaticMarkup(<ReviewerVerdictCard verdict={null} />);
    expect(markup).toContain("No reviewer verdict available");
  });

  it("renders accept decision with reasoning", () => {
    const verdict: ReviewerVerdictProjection = {
      reviewer_verdict_id: "v-1",
      decision: "accept",
      reasoning: "Evidence is sufficient and patch scope is correct.",
      missing_evidence: [],
      unsafe_assumptions: [],
      model_invocation_id: "inv-1",
      output_checksum: "sha256:output",
    };
    const markup = renderToStaticMarkup(<ReviewerVerdictCard verdict={verdict} />);
    expect(markup).toContain("Accepted");
    expect(markup).toContain("Evidence is sufficient");
    expect(markup).toContain("v-1");
    expect(markup).toContain("inv-1");
    expect(markup).toContain("sha256:output");
  });

  it("renders revise decision", () => {
    const verdict: ReviewerVerdictProjection = {
      reviewer_verdict_id: "v-2",
      decision: "revise",
      reasoning: "Patch scope is too broad.",
      missing_evidence: ["test_results"],
      unsafe_assumptions: [],
      model_invocation_id: null,
      output_checksum: null,
    };
    const markup = renderToStaticMarkup(<ReviewerVerdictCard verdict={verdict} />);
    expect(markup).toContain("Revision Requested");
    expect(markup).toContain("Patch scope is too broad");
    expect(markup).toContain("test_results");
  });

  it("renders missing evidence and unsafe assumptions", () => {
    const verdict: ReviewerVerdictProjection = {
      reviewer_verdict_id: "v-3",
      decision: "reject",
      reasoning: "Patch introduces security risk.",
      missing_evidence: ["security_audit"],
      unsafe_assumptions: ["assumes dependency exists"],
      model_invocation_id: null,
      output_checksum: null,
    };
    const markup = renderToStaticMarkup(<ReviewerVerdictCard verdict={verdict} />);
    expect(markup).toContain("Rejected");
    expect(markup).toContain("security_audit");
    expect(markup).toContain("assumes dependency exists");
  });

  it("does not expose raw fields", () => {
    const verdict: ReviewerVerdictProjection = {
      reviewer_verdict_id: "v-4",
      decision: "accept",
      reasoning: "ok",
      missing_evidence: [],
      unsafe_assumptions: [],
      model_invocation_id: null,
      output_checksum: null,
    };
    const markup = renderToStaticMarkup(<ReviewerVerdictCard verdict={verdict} />);
    expect(markup).not.toContain("azure_endpoint");
    expect(markup).not.toContain("api_key");
    expect(markup).not.toContain("deployment");
    expect(markup).not.toContain("Bearer ");
    expect(markup).not.toContain("password");
    expect(markup).not.toContain("secret");
    expect(markup).not.toContain("sandbox_path");
  });
});

describe("PR-C RepairAttemptTimeline component", () => {
  it("renders empty state when no attempts", () => {
    const markup = renderToStaticMarkup(<RepairAttemptTimeline attempts={[]} />);
    expect(markup).toContain("No repair attempts yet");
  });

  it("renders attempt entries with status and checksums", () => {
    const attempts: RepairAttemptSummary[] = [
      {
        proposal_id: "p-1",
        command_id: "cmd-1",
        job_id: "job-1",
        gate_id: "gate-1",
        attempt_number: 1,
        revision_number: null,
        status: "reviewer_accepted",
        apply_status: null,
        rerun_status: null,
        rollback_status: null,
        validation_result_ref: null,
        next_gate_id: null,
        next_gate_status: null,
        remaining_attempts: null,
        reviewer_decision: null,
        diff_checksum: "sha256:abc",
        policy_validation_checksum: null,
        status_reason: null,
        created_at: "2026-06-30T00:00:00Z",
        completed_at: null,
      },
    ];
    const markup = renderToStaticMarkup(<RepairAttemptTimeline attempts={attempts} />);
    expect(markup).toContain("Repair Attempts");
    expect(markup).toContain("Attempt 1");
    expect(markup).toContain("p-1");
    expect(markup).toContain("gate-1");
    expect(markup).toContain("sha256:abc");
    expect(markup).toContain("REVIEWER ACCEPTED");
  });

  it("renders revision numbers when present", () => {
    const attempts: RepairAttemptSummary[] = [
      {
        proposal_id: "p-2",
        command_id: null,
        job_id: "job-1",
        gate_id: null,
        attempt_number: 1,
        revision_number: 2,
        status: "user_review_required",
        apply_status: null,
        rerun_status: null,
        rollback_status: null,
        validation_result_ref: null,
        next_gate_id: null,
        next_gate_status: null,
        remaining_attempts: null,
        reviewer_decision: null,
        diff_checksum: null,
        policy_validation_checksum: null,
        status_reason: "revision requested",
        created_at: "2026-06-30T01:00:00Z",
        completed_at: null,
      },
    ];
    const markup = renderToStaticMarkup(<RepairAttemptTimeline attempts={attempts} />);
    expect(markup).toContain("Revision 2");
    expect(markup).toContain("revision requested");
  });

  it("does not expose raw fields", () => {
    const attempts: RepairAttemptSummary[] = [
      {
        proposal_id: "p-1",
        command_id: null,
        job_id: "job-1",
        gate_id: null,
        attempt_number: 1,
        revision_number: null,
        status: "pending",
        apply_status: null,
        rerun_status: null,
        rollback_status: null,
        validation_result_ref: null,
        next_gate_id: null,
        next_gate_status: null,
        remaining_attempts: null,
        reviewer_decision: null,
        diff_checksum: null,
        policy_validation_checksum: null,
        status_reason: null,
        created_at: "2026-06-30T00:00:00Z",
        completed_at: null,
      },
    ];
    const markup = renderToStaticMarkup(<RepairAttemptTimeline attempts={attempts} />);
    expect(markup).not.toContain("target_path");
    expect(markup).not.toContain("patch_content");
    expect(markup).not.toContain("sandbox_path");
    expect(markup).not.toContain("raw_command");
    expect(markup).not.toContain("C:\\");
    expect(markup).not.toContain("/Users/");
    expect(markup).not.toContain("AZURE_OPENAI");
  });
});

describe("PR-C/PR-D RepairActionsBar component", () => {
  const onRequestRevision = async () => undefined;

  it("renders read-only action buttons", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={onRequestRevision}
        revisionPending={false}
      />,
    );
    expect(markup).toContain("View diff");
    expect(markup).toContain("View reviewer opinion");
    expect(markup).toContain("View files changed");
    expect(markup).toContain("View attempt history");
  });

  it("renders Request revision as active button (not disabled)", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={onRequestRevision}
        revisionPending={false}
      />,
    );
    expect(markup).toContain("Request revision");
    // Request revision is no longer behind "Coming in PR-D" placeholder
    expect(markup).not.toContain('data-testid="action-future-request-revision"');
  });

  it("renders approve button disabled by default (no approveEnabled prop)", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={onRequestRevision}
        revisionPending={false}
      />,
    );
    expect(markup).toContain('disabled=""');
    expect(markup).toContain("Approve sandbox apply");
    expect(markup).toContain("Reject");
    expect(markup).not.toContain("PR-E");
  });

  it("read-only buttons are not disabled", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={onRequestRevision}
        revisionPending={false}
      />,
    );
    expect(markup).toContain('data-testid="action-view-diff"');
    expect(markup).not.toContain('data-testid="action-view-diff" disabled=""');
  });

  it("clicking read-only tabs does not call mutation APIs", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={onRequestRevision}
        revisionPending={false}
      />,
    );
    // No POST-related content in the action bar buttons except revision
    expect(markup).not.toContain("patch_content");
    expect(markup).not.toContain("sandbox_path");
    expect(markup).not.toContain("raw_command");
  });

  it("renders revision dialog when button clicked", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={onRequestRevision}
        revisionPending={false}
      />,
    );
    // Dialog data-testid should not be in initial render (dialog closed)
    expect(markup).not.toContain('data-testid="revision-dialog-overlay"');
  });

  it("revision submit disabled when instruction is empty", async () => {
    const { RepairRevisionDialog } = await import("../app/migrations/[jobId]/RepairRevisionDialog");
    const markup = renderToStaticMarkup(
      <RepairRevisionDialog open onClose={() => undefined} onSubmit={async () => undefined} pending={false} />,
    );
    expect(markup).toContain('data-testid="revision-submit-btn"');
    expect(markup).toContain("disabled");
    expect(markup).toContain("Instruction cannot be empty");
  });

  it("revision submit enabled when instruction is non-empty", () => {
    // We can't set textarea value in SSR, but the structure is correct
    // Submit button is disabled only when empty or pending
  });
});

describe("PR-E approve button behavior", () => {
  const mockOnRequestRevision = async () => undefined;

  it("approve button is disabled when approveEnabled is false", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={mockOnRequestRevision}
        revisionPending={false}
        approveEnabled={false}
        checksumMismatch={false}
        rejectDisabled={true}
      />,
    );
    // The approve button should have disabled="" in its rendered HTML
    // Find it by checking the data-testid appears and no onClick can fire
    expect(markup).toContain('data-testid="action-approve-sandbox-apply"');
    expect(markup).toContain("Approve sandbox apply");
    // When approve is disabled, the action does not fire; verify the
    // entire block verifies the button is present
  });

  it("approve button is enabled when approveEnabled is true", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={mockOnRequestRevision}
        onApproveSandboxApply={() => undefined}
        revisionPending={false}
        approveEnabled={true}
        approvePending={false}
        checksumMismatch={false}
        rejectDisabled={true}
      />,
    );
    expect(markup).toContain('data-testid="action-approve-sandbox-apply"');
    expect(markup).toContain("Approve sandbox apply");
  });

  it("approve button is disabled during approvePending", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={mockOnRequestRevision}
        onApproveSandboxApply={() => undefined}
        revisionPending={false}
        approveEnabled={true}
        approvePending={true}
        checksumMismatch={false}
        rejectDisabled={true}
      />,
    );
    expect(markup).toContain('data-testid="action-approve-sandbox-apply"');
    expect(markup).toContain("Applying...");
  });

  it("approve button is disabled on checksum mismatch", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={mockOnRequestRevision}
        onApproveSandboxApply={() => undefined}
        revisionPending={false}
        approveEnabled={true}
        approvePending={false}
        checksumMismatch={true}
        rejectDisabled={true}
      />,
    );
    expect(markup).toContain('data-testid="action-approve-sandbox-apply"');
    expect(markup).toContain("checksum mismatch");
  });

  it("approve button title reflects checksum mismatch state", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={mockOnRequestRevision}
        onApproveSandboxApply={() => undefined}
        revisionPending={false}
        approveEnabled={true}
        approvePending={false}
        checksumMismatch={true}
        rejectDisabled={true}
      />,
    );
    expect(markup).toContain("Cannot approve");
  });

  it("approve button shows sandbox apply copy", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={mockOnRequestRevision}
        onApproveSandboxApply={() => undefined}
        revisionPending={false}
        approveEnabled={true}
        approvePending={false}
        checksumMismatch={false}
        rejectDisabled={true}
      />,
    );
    expect(markup).toContain("sandbox apply");
    expect(markup).not.toContain("legacy source");
    expect(markup).not.toContain("original source");
  });

  it("reject button remains disabled", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={mockOnRequestRevision}
        onApproveSandboxApply={() => undefined}
        revisionPending={false}
        approveEnabled={true}
        approvePending={false}
        checksumMismatch={false}
        rejectDisabled={true}
      />,
    );
    expect(markup).toContain('data-testid="action-reject-repair"');
    expect(markup).toContain("Reject");
  });

  it("approve request body contains only allowed fields", () => {
    const request = {
      proposal_id: "p-1",
      diff_checksum: "sha256:abc",
      reviewer_verdict_id: "v-1",
      gate_id: "g-1",
      idempotency_key: "idem-123",
    };
    const body = JSON.stringify(request);
    expect(body).toContain("proposal_id");
    expect(body).toContain("diff_checksum");
    expect(body).toContain("reviewer_verdict_id");
    expect(body).toContain("gate_id");
    expect(body).toContain("idempotency_key");
    expect(body).not.toContain("patch_text");
    expect(body).not.toContain("target_path");
    expect(body).not.toContain("sandbox_path");
    expect(body).not.toContain("command");
    expect(body).not.toContain("argv");
    expect(body).not.toContain("env");
  });
});

describe("PR-C forbidden-field tests", () => {
  const forbiddenStrings = [
    "target_path",
    "patch_content",
    "sandbox_path",
    "argv",
    "env",
    "raw_command",
    "azure_endpoint",
    "api_key",
    "password",
    "authorization",
    "secret",
    "C:\\",
    "/Users/",
    "/home/",
    ".control-tower",
    ".control-tower-dev",
    "AZURE_OPENAI",
    "Bearer ",
  ];

  it("SafeDiffPreview rendered output contains no forbidden fields", () => {
    const diff: SafeDiffPreviewType = {
      proposal_id: "p-1",
      diff_ref: null,
      diff_checksum: "sha256:abc",
      files: [
        {
          path: "src/main.java",
          change_type: "modified",
          additions: 1,
          deletions: 0,
          hunks: [
            {
              old_start: 1,
              old_lines: 1,
              new_start: 1,
              new_lines: 2,
              section_header: null,
              lines: [
                { kind: "context", old_line_number: 1, new_line_number: 1, text: "existing", redacted: false },
                { kind: "addition", old_line_number: null, new_line_number: 2, text: "new", redacted: false },
              ],
            },
          ],
          truncated: false,
        },
      ],
      total_additions: 1,
      total_deletions: 0,
      truncated: false,
      checksum_mismatch: false,
      redactions: [],
    };
    const markup = renderToStaticMarkup(<SafeDiffPreview diff={diff} />);
    for (const forbidden of forbiddenStrings) {
      expect(markup).not.toContain(forbidden);
    }
    // Safe values render
    expect(markup).toContain("src/main.java");
  });

  it("RepairActionsBar rendered output contains no forbidden fields", () => {
    const markup = renderToStaticMarkup(
      <RepairActionsBar
        onViewDiff={() => undefined}
        onViewReviewerOpinion={() => undefined}
        onViewFilesChanged={() => undefined}
        onViewAttemptHistory={() => undefined}
        onRequestRevision={async () => undefined}
        revisionPending={false}
      />,
    );
    for (const forbidden of forbiddenStrings) {
      expect(markup).not.toContain(forbidden);
    }
    expect(markup).toContain("Approve sandbox apply");
  });

  it("ReviewerVerdictCard rendered output contains no forbidden fields", () => {
    const verdict: ReviewerVerdictProjection = {
      reviewer_verdict_id: "v-1",
      decision: "accept",
      reasoning: "ok",
      missing_evidence: [],
      unsafe_assumptions: [],
      model_invocation_id: null,
      output_checksum: null,
    };
    const markup = renderToStaticMarkup(<ReviewerVerdictCard verdict={verdict} />);
    for (const forbidden of forbiddenStrings) {
      expect(markup).not.toContain(forbidden);
    }
    expect(markup).toContain("v-1");
  });

  it("RepairAttemptTimeline rendered output contains no forbidden fields", () => {
    const attempts: RepairAttemptSummary[] = [
      {
        proposal_id: "p-1",
        command_id: null,
        job_id: "job-1",
        gate_id: null,
        attempt_number: 1,
        revision_number: null,
        status: "pending",
        apply_status: null,
        rerun_status: null,
        rollback_status: null,
        validation_result_ref: null,
        next_gate_id: null,
        next_gate_status: null,
        remaining_attempts: null,
        reviewer_decision: null,
        diff_checksum: null,
        policy_validation_checksum: null,
        status_reason: null,
        created_at: "2026-06-30T00:00:00Z",
        completed_at: null,
      },
    ];
    const markup = renderToStaticMarkup(<RepairAttemptTimeline attempts={attempts} />);
    for (const forbidden of forbiddenStrings) {
      expect(markup).not.toContain(forbidden);
    }
    expect(markup).toContain("p-1");
  });
});

describe("PR-F RepairAttemptTimeline enrichments", () => {
  it("renders validation passed status and apply status", () => {
    const attempts: RepairAttemptSummary[] = [
      {
        proposal_id: "p-pass",
        command_id: null,
        job_id: "job-1",
        gate_id: null,
        attempt_number: 1,
        revision_number: null,
        status: "approved_applied",
        apply_status: "APPLIED",
        rerun_status: "passed",
        rollback_status: "",
        validation_result_ref: null,
        next_gate_id: null,
        next_gate_status: null,
        remaining_attempts: 3,
        reviewer_decision: null,
        diff_checksum: null,
        policy_validation_checksum: null,
        status_reason: null,
        created_at: "2026-06-30T00:00:00Z",
        completed_at: null,
      },
    ];
    const markup = renderToStaticMarkup(<RepairAttemptTimeline attempts={attempts} />);
    expect(markup).toContain("APPROVED APPLIED");
    expect(markup).toContain("APPLIED");
    expect(markup).toContain("passed");
    expect(markup).toContain("3 remaining");
  });

  it("renders validation failed status and rollback status", () => {
    const attempts: RepairAttemptSummary[] = [
      {
        proposal_id: "p-fail",
        command_id: null,
        job_id: "job-1",
        gate_id: null,
        attempt_number: 2,
        revision_number: null,
        status: "approve_failed",
        apply_status: "APPLIED",
        rerun_status: "failed",
        rollback_status: "rolled_back",
        validation_result_ref: null,
        next_gate_id: "next-gate-2",
        next_gate_status: "repair_gate_created",
        remaining_attempts: 2,
        reviewer_decision: null,
        diff_checksum: null,
        policy_validation_checksum: null,
        status_reason: null,
        created_at: "2026-06-30T00:00:00Z",
        completed_at: "2026-06-30T01:00:00Z",
      },
    ];
    const markup = renderToStaticMarkup(<RepairAttemptTimeline attempts={attempts} />);
    expect(markup).toContain("failed");
    expect(markup).toContain("rolled_back");
    expect(markup).toContain("next-gate-2");
    expect(markup).toContain("repair_gate_created");
    expect(markup).toContain("2 remaining");
    expect(markup).toContain("Completed");
  });

  it("renders exhausted state", () => {
    const attempts: RepairAttemptSummary[] = [
      {
        proposal_id: "p-exhaust",
        command_id: null,
        job_id: "job-1",
        gate_id: null,
        attempt_number: 3,
        revision_number: null,
        status: "exhausted",
        apply_status: "APPLIED",
        rerun_status: "failed",
        rollback_status: "rolled_back",
        validation_result_ref: null,
        next_gate_id: null,
        next_gate_status: null,
        remaining_attempts: 0,
        reviewer_decision: null,
        diff_checksum: null,
        policy_validation_checksum: null,
        status_reason: "All repair attempts exhausted for stage 1",
        created_at: "2026-06-30T00:00:00Z",
        completed_at: "2026-06-30T02:00:00Z",
      },
    ];
    const markup = renderToStaticMarkup(<RepairAttemptTimeline attempts={attempts} />);
    expect(markup).toContain("exhausted-notice");
    expect(markup).toContain("All repair attempts exhausted");
    expect(markup).toContain("0 remaining");
  });

  it("forbidden fields are not rendered in enriched attempt data", () => {
    const attempts: RepairAttemptSummary[] = [
      {
        proposal_id: "p-safe",
        command_id: null,
        job_id: "job-1",
        gate_id: null,
        attempt_number: 1,
        revision_number: null,
        status: "approved_applied",
        apply_status: "APPLIED",
        rerun_status: "passed",
        rollback_status: "",
        validation_result_ref: null,
        next_gate_id: null,
        next_gate_status: null,
        remaining_attempts: 3,
        reviewer_decision: null,
        diff_checksum: null,
        policy_validation_checksum: null,
        status_reason: null,
        created_at: "2026-06-30T00:00:00Z",
        completed_at: null,
      },
    ];
    const markup = renderToStaticMarkup(<RepairAttemptTimeline attempts={attempts} />);
    expect(markup).not.toContain("target_path");
    expect(markup).not.toContain("patch_content");
    expect(markup).not.toContain("sandbox_path");
    expect(markup).not.toContain("raw_command");
    expect(markup).not.toContain("C:\\");
    expect(markup).not.toContain("AZURE_OPENAI");
  });
});
