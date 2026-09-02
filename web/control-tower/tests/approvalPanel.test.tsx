import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApprovalContent } from "../app/jobs/[jobId]/ApprovalPanel";
import type { ApprovalEntry, PrivilegedActionEntry } from "../lib/contracts";

const MOCK_APPROVALS: ApprovalEntry[] = [
  {
    approval_id: "appr-001",
    interrupt_id: "intr-001",
    decision: "approved",
    approved_by: "human-01",
    approval_comments: "Approved after review",
    created_at: "2026-06-12T00:00:00Z",
  },
  {
    approval_id: "appr-002",
    interrupt_id: "intr-002",
    decision: "rejected",
    approved_by: "human-01",
    approval_comments: "Rejected due to policy concern",
    created_at: "2026-06-12T00:01:00Z",
  },
];

const MOCK_PENDING_ACTIONS: PrivilegedActionEntry[] = [
  {
    action_id: "act-001",
    job_id: "test-job-123",
    action_type: "maven",
    parameters: { goal: "compile" },
    parameters_checksum: "chk-001",
    requested_by: "assistant-01",
    status: "pending",
    requested_at: "2026-06-12T00:00:00Z",
    decided_at: null,
    decision: null,
    decided_by: null,
  },
  {
    action_id: "act-002",
    job_id: "test-job-123",
    action_type: "write",
    parameters: { file: "pom.xml", content: "<redacted>" },
    parameters_checksum: "chk-002",
    requested_by: "assistant-01",
    status: "requested",
    requested_at: "2026-06-12T00:01:00Z",
    decided_at: null,
    decision: null,
    decided_by: null,
  },
];

describe("V1-18C Approvals and action cards panel", () => {
  // ── Loading state ──────────────────────────────────────────────────

  it("renders loading state", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent state={{ status: "loading" }} />
    );

    expect(markup).toContain("Approvals &amp; action cards");
    expect(markup).toContain("Loading approvals and pending actions");
  });

  // ── Empty state ────────────────────────────────────────────────────

  it("renders empty state when no approvals or actions exist", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent state={{ status: "empty" }} />
    );

    expect(markup).toContain("Approvals &amp; action cards");
    expect(markup).toContain("No approvals or pending actions yet");
    expect(markup).toContain("Approvals and action cards appear after a migration job reaches a privileged action step");
  });

  // ── Error state ────────────────────────────────────────────────────

  it("renders error state when API fails", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent state={{ status: "error", message: "Endpoint not available" }} />
    );

    expect(markup).toContain("Approvals &amp; action cards");
    expect(markup).toContain("Failed to load approvals");
    expect(markup).toContain("Endpoint not available");
  });

  // ── Success state ──────────────────────────────────────────────────

  it("renders approvals when data loads", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent
        state={{ status: "success", approvals: MOCK_APPROVALS, pendingActions: [] }}
      />
    );

    expect(markup).toContain("Approvals &amp; action cards");
    expect(markup).toContain("Approvals");
    expect(markup).toContain("approved");
    expect(markup).toContain("rejected");
  });

  it("renders pending action cards when actions exist", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent
        state={{ status: "success", approvals: [], pendingActions: MOCK_PENDING_ACTIONS }}
      />
    );

    expect(markup).toContain("Pending action cards");
    expect(markup).toContain("maven");
    expect(markup).toContain("write");
    expect(markup).toContain("pending");
    expect(markup).toContain("requested");
  });

  it("renders both approvals and pending actions when both exist", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent
        state={{ status: "success", approvals: MOCK_APPROVALS, pendingActions: MOCK_PENDING_ACTIONS }}
      />
    );

    expect(markup).toContain("Approvals");
    expect(markup).toContain("Pending action cards");
    expect(markup).toContain("approved");
    expect(markup).toContain("rejected");
    expect(markup).toContain("maven");
    expect(markup).toContain("write");
  });

  it("renders action type and status without raw parameters", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent
        state={{ status: "success", approvals: [], pendingActions: MOCK_PENDING_ACTIONS }}
      />
    );

    // Action type and status are visible
    expect(markup).toContain("maven");
    expect(markup).toContain("write");
    expect(markup).toContain("pending");
    expect(markup).toContain("requested");
    // Raw parameter values should not appear (redacted or absent)
    expect(markup).not.toContain("compile");
    expect(markup).not.toContain("pom.xml");
  });

  it("renders static approval evidence table", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent
        state={{ status: "success", approvals: MOCK_APPROVALS, pendingActions: MOCK_PENDING_ACTIONS }}
      />
    );

    expect(markup).toContain("Approval and action evidence");
    expect(markup).toContain("Approvals are recorded with actor attribution");
    expect(markup).toContain("Only typed Maven/write actions are displayed");
    expect(markup).toContain("Shell actions are rejected at the service layer");
    expect(markup).toContain("No approve/reject/execute buttons are exposed in read-only views");
    expect(markup).toContain("Browser payloads cannot choose raw paths, Maven goals, shell commands, or model deployments");
  });

  it("renders all evidence with PASS status", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent
        state={{ status: "success", approvals: MOCK_APPROVALS, pendingActions: MOCK_PENDING_ACTIONS }}
      />
    );

    const passCount = (markup.match(/PASS/g) || []).length;
    expect(passCount).toBe(8); // 8 evidence items
  });

  it("renders evidence notes details section", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent
        state={{ status: "success", approvals: MOCK_APPROVALS, pendingActions: MOCK_PENDING_ACTIONS }}
      />
    );

    expect(markup).toContain("Approval and action evidence notes");
    expect(markup).toContain("raw paths, secrets, and deployment identifiers are redacted from approval records");
    expect(markup).toContain("All evidence is static and deterministic based on the V1 design");
  });

  it("shows redaction notice at top", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent
        state={{ status: "success", approvals: MOCK_APPROVALS, pendingActions: MOCK_PENDING_ACTIONS }}
      />
    );

    expect(markup).toContain("All paths, secrets, and identifiers are redacted");
  });

  // ── No dangerous controls ─────────────────────────────────────────

  it("has no dangerous input controls in any state", () => {
    const successMarkup = renderToStaticMarkup(
      <ApprovalContent state={{ status: "success", approvals: MOCK_APPROVALS, pendingActions: MOCK_PENDING_ACTIONS }} />
    );
    const loadingMarkup = renderToStaticMarkup(
      <ApprovalContent state={{ status: "loading" }} />
    );
    const emptyMarkup = renderToStaticMarkup(
      <ApprovalContent state={{ status: "empty" }} />
    );
    const errorMarkup = renderToStaticMarkup(
      <ApprovalContent state={{ status: "error", message: "err" }} />
    );

    for (const markup of [successMarkup, loadingMarkup, emptyMarkup, errorMarkup]) {
      expect(markup).not.toContain("<input");
      expect(markup).not.toContain("<select");
      expect(markup).not.toContain("<textarea");
      expect(markup).not.toContain("<form");
    }
  });

  it("has no approve/reject/execute buttons in any state", () => {
    const successMarkup = renderToStaticMarkup(
      <ApprovalContent state={{ status: "success", approvals: MOCK_APPROVALS, pendingActions: MOCK_PENDING_ACTIONS }} />
    );
    const loadingMarkup = renderToStaticMarkup(
      <ApprovalContent state={{ status: "loading" }} />
    );
    const emptyMarkup = renderToStaticMarkup(
      <ApprovalContent state={{ status: "empty" }} />
    );
    const errorMarkup = renderToStaticMarkup(
      <ApprovalContent state={{ status: "error", message: "err" }} />
    );

    for (const markup of [successMarkup, loadingMarkup, emptyMarkup, errorMarkup]) {
      expect(markup).not.toContain("Approve");
      expect(markup).not.toContain("Reject");
      expect(markup).not.toContain("Execute");
    }
  });

  it("has no raw shell, Maven goal editor, path input, or model deployment controls", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent
        state={{ status: "success", approvals: MOCK_APPROVALS, pendingActions: MOCK_PENDING_ACTIONS }}
      />
    );

    // No executable input controls or dangerous form elements
    expect(markup).not.toContain("<input");
    expect(markup).not.toContain("<select");
    expect(markup).not.toContain("<textarea");
    expect(markup).not.toContain("<form");
    // No approve/reject/execute buttons
    expect(markup).not.toContain("Approve");
    expect(markup).not.toContain("Reject");
    expect(markup).not.toContain("Execute");
  });

  it("shows read-only approval evidence with no execution controls", () => {
    const markup = renderToStaticMarkup(
      <ApprovalContent
        state={{ status: "success", approvals: MOCK_APPROVALS, pendingActions: MOCK_PENDING_ACTIONS }}
      />
    );

    expect(markup).toContain("No approve/reject/execute buttons are exposed in read-only views");
    expect(markup).toContain("Browser payloads cannot choose raw paths, Maven goals, shell commands, or model deployments");
  });
});
