import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { RepairContent } from "../app/jobs/[jobId]/RepairPanel";
import type { FakeRepairProposalEntry } from "../lib/contracts";

const MOCK_PROPOSALS: FakeRepairProposalEntry[] = [
  {
    proposal_id: "prop-001",
    classification_id: "cls-001",
    command_id: "cmd-001",
    job_id: "test-job-123",
    proposal_order: 1,
    proposal_kind: "fake",
    proposal_summary: "Pom.xml dependency version bump to 2.7.x",
    proposal_checksum: "chk-prop-001",
    recommendation_type: "apply",
    confidence_label: "high",
    confidence_score: 0.85,
    warning_codes: ["WARN_DEPRECATED_PROPERTY"],
    applicable: true,
    context_checksum: "chk-ctx-001",
    actor_type: "assistant",
    actor_id: "assistant-01",
    created_at: "2026-06-12T00:00:00Z",
  },
  {
    proposal_id: "prop-002",
    classification_id: "cls-001",
    command_id: "cmd-001",
    job_id: "test-job-123",
    proposal_order: 2,
    proposal_kind: "fake",
    proposal_summary: "Remove deprecated property from pom.xml",
    proposal_checksum: "chk-prop-002",
    recommendation_type: "apply",
    confidence_label: "medium",
    confidence_score: 0.65,
    warning_codes: [],
    applicable: true,
    context_checksum: "chk-ctx-001",
    actor_type: "assistant",
    actor_id: "assistant-01",
    created_at: "2026-06-12T00:01:00Z",
  },
];

describe("V1-18E Repair panel", () => {
  // ── Loading state ──────────────────────────────────────────────────

  it("renders loading state", () => {
    const markup = renderToStaticMarkup(
      <RepairContent state={{ status: "loading" }} />
    );

    expect(markup).toContain("Repair panel");
    expect(markup).toContain("Loading repair classifications and proposals");
  });

  // ── Empty state ────────────────────────────────────────────────────

  it("renders empty state when no proposals exist", () => {
    const markup = renderToStaticMarkup(
      <RepairContent state={{ status: "empty" }} />
    );

    expect(markup).toContain("Repair panel");
    expect(markup).toContain("No repair data available yet");
    expect(markup).toContain("Repair classifications and proposals appear after a command fails and is classified for repair");
  });

  // ── Error state ────────────────────────────────────────────────────

  it("renders error state when API fails", () => {
    const markup = renderToStaticMarkup(
      <RepairContent state={{ status: "error", message: "Endpoint not available" }} />
    );

    expect(markup).toContain("Repair panel");
    expect(markup).toContain("Failed to load repair data");
    expect(markup).toContain("Endpoint not available");
  });

  // ── Success state ──────────────────────────────────────────────────

  it("renders proposals when data loads", () => {
    const markup = renderToStaticMarkup(
      <RepairContent state={{ status: "success", proposals: MOCK_PROPOSALS }} />
    );

    expect(markup).toContain("Repair panel");
    expect(markup).toContain("Repair proposals");
    expect(markup).toContain("fake");
    expect(markup).toContain("apply");
  });

  it("renders confidence scores and labels", () => {
    const markup = renderToStaticMarkup(
      <RepairContent state={{ status: "success", proposals: MOCK_PROPOSALS }} />
    );

    expect(markup).toContain("high");
    expect(markup).toContain("medium");
    expect(markup).toContain("0.85");
    expect(markup).toContain("0.65");
  });

  it("renders proposal summaries", () => {
    const markup = renderToStaticMarkup(
      <RepairContent state={{ status: "success", proposals: MOCK_PROPOSALS }} />
    );

    expect(markup).toContain("Pom.xml dependency version bump to 2.7.x");
    expect(markup).toContain("Remove deprecated property from pom.xml");
  });

  it("renders static repair evidence table", () => {
    const markup = renderToStaticMarkup(
      <RepairContent state={{ status: "success", proposals: MOCK_PROPOSALS }} />
    );

    expect(markup).toContain("Repair evidence");
    expect(markup).toContain("Failed commands are classified with evidence kind and summary");
    expect(markup).toContain("Repair proposals are deterministic and reproducible");
    expect(markup).toContain("No repair generation, patch, Maven, or rollback execution from UI");
  });

  it("renders all evidence with PASS status", () => {
    const markup = renderToStaticMarkup(
      <RepairContent state={{ status: "success", proposals: MOCK_PROPOSALS }} />
    );

    const passCount = (markup.match(/PASS/g) || []).length;
    expect(passCount).toBe(8); // 8 evidence items
  });

  it("renders evidence notes details section", () => {
    const markup = renderToStaticMarkup(
      <RepairContent state={{ status: "success", proposals: MOCK_PROPOSALS }} />
    );

    expect(markup).toContain("Repair evidence notes");
    expect(markup).toContain("Warning codes are bounded and never expose raw workspace paths");
    expect(markup).toContain("No repair generation, patching, Maven execution, or rollback from the UI");
  });

  it("shows redaction notice at top", () => {
    const markup = renderToStaticMarkup(
      <RepairContent state={{ status: "success", proposals: MOCK_PROPOSALS }} />
    );

    expect(markup).toContain("All paths, secrets, and identifiers are redacted");
  });

  // ── No dangerous controls ─────────────────────────────────────────

  it("has no dangerous input controls in any state", () => {
    const successMarkup = renderToStaticMarkup(
      <RepairContent state={{ status: "success", proposals: MOCK_PROPOSALS }} />
    );
    const loadingMarkup = renderToStaticMarkup(
      <RepairContent state={{ status: "loading" }} />
    );
    const emptyMarkup = renderToStaticMarkup(
      <RepairContent state={{ status: "empty" }} />
    );
    const errorMarkup = renderToStaticMarkup(
      <RepairContent state={{ status: "error", message: "err" }} />
    );

    for (const markup of [successMarkup, loadingMarkup, emptyMarkup, errorMarkup]) {
      expect(markup).not.toContain("<input");
      expect(markup).not.toContain("<select");
      expect(markup).not.toContain("<textarea");
      expect(markup).not.toContain("<form");
    }
  });

  it("has no repair/patch/rollback execution controls in any state", () => {
    const successMarkup = renderToStaticMarkup(
      <RepairContent state={{ status: "success", proposals: MOCK_PROPOSALS }} />
    );
    const loadingMarkup = renderToStaticMarkup(
      <RepairContent state={{ status: "loading" }} />
    );
    const emptyMarkup = renderToStaticMarkup(
      <RepairContent state={{ status: "empty" }} />
    );
    const errorMarkup = renderToStaticMarkup(
      <RepairContent state={{ status: "error", message: "err" }} />
    );

    for (const markup of [successMarkup, loadingMarkup, emptyMarkup, errorMarkup]) {
      // No buttons, forms, or interactive controls
      expect(markup).not.toContain("<input");
      expect(markup).not.toContain("<select");
      expect(markup).not.toContain("<textarea");
      expect(markup).not.toContain("<form");
      // No execution-related buttons
      expect(markup).not.toContain("<button");
    }
  });

  it("has no raw shell, Maven goal, path, or model deployment controls", () => {
    const markup = renderToStaticMarkup(
      <RepairContent state={{ status: "success", proposals: MOCK_PROPOSALS }} />
    );

    // No executable input controls
    expect(markup).not.toContain("<input");
    expect(markup).not.toContain("<select");
    expect(markup).not.toContain("<textarea");
    // No approve/execute controls
    expect(markup).not.toContain("Generate");
    expect(markup).not.toContain("Execute");
  });

  it("shows read-only repair evidence with no execution controls", () => {
    const markup = renderToStaticMarkup(
      <RepairContent state={{ status: "success", proposals: MOCK_PROPOSALS }} />
    );

    expect(markup).toContain("No repair generation, patching, Maven execution, or rollback from the UI");
    expect(markup).toContain("Browser payloads cannot choose raw paths, Maven goals, shell commands, or model deployments");
  });
});
