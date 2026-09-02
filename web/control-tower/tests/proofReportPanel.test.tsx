import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ProofReportContent } from "../app/jobs/[jobId]/ProofReportPanel";
import type { ProofReportEntry } from "../lib/contracts";

const MOCK_REPORT: ProofReportEntry = {
  report_id: "report-abc123",
  job_id: "test-job-123",
  report_version: 1,
  report_checksum: "sha256-checksum-value-for-testing-purposes-only",
  gate_count: 3,
  all_gates_present: true,
  proof_complete: true,
  target_proof_level: "BUILD_TEST_VERIFIED",
  pipeline_id: "springboot-216-to-356-java21-three-stage",
  summary: {
    job_id: "test-job-123",
    pipeline_id: "springboot-216-to-356-java21-three-stage",
    gate_count: 3,
    proof_complete: true,
    stages: [
      {
        stage_index: 1,
        output_checksum: "abc123",
        proof_gate_checksum: "gate1checksum",
        chain_status: "completed",
      },
      {
        stage_index: 2,
        output_checksum: "def456",
        proof_gate_checksum: "gate2checksum",
        chain_status: "completed",
      },
      {
        stage_index: 3,
        output_checksum: "ghi789",
        proof_gate_checksum: "gate3checksum",
        chain_status: "completed",
      },
    ],
  },
  gates: [
    {
      stage_index: 1,
      output_checksum: "abc123",
      proof_gate_checksum: "gate1checksum",
      chain_status: "completed",
    },
    {
      stage_index: 2,
      output_checksum: "def456",
      proof_gate_checksum: "gate2checksum",
      chain_status: "completed",
    },
    {
      stage_index: 3,
      output_checksum: "ghi789",
      proof_gate_checksum: "gate3checksum",
      chain_status: "completed",
    },
  ],
  generated_at: "2026-06-12T00:00:00Z",
  generated_by: "system",
};

describe("V1-18G Proof and final report panel", () => {
  // ── Loading state ──────────────────────────────────────────────────

  it("renders loading state", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "loading" }} />
    );

    expect(markup).toContain("Proof &amp; final report");
    expect(markup).toContain("Loading proof gates and final report");
  });

  // ── Empty state ────────────────────────────────────────────────────

  it("renders empty state when no report exists", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "empty" }} />
    );

    expect(markup).toContain("Proof &amp; final report");
    expect(markup).toContain("No proof report available yet");
    expect(markup).toContain("The final report is generated after all three stages complete and proof gates are computed");
  });

  // ── Error state ────────────────────────────────────────────────────

  it("renders error state when API fails", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "error", message: "Endpoint not available" }} />
    );

    expect(markup).toContain("Proof &amp; final report");
    expect(markup).toContain("Failed to load proof report");
    expect(markup).toContain("Endpoint not available");
  });

  // ── Success state ──────────────────────────────────────────────────

  it("renders report when data loads", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "success", report: MOCK_REPORT }} />
    );

    expect(markup).toContain("Proof &amp; final report");
    expect(markup).toContain("COMPLETE");
    expect(markup).toContain("BUILD_TEST_VERIFIED");
  });

  it("renders proof gate entries", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "success", report: MOCK_REPORT }} />
    );

    expect(markup).toContain("Stage 1");
    expect(markup).toContain("Stage 2");
    expect(markup).toContain("Stage 3");
    expect(markup).toContain("completed");
  });

  it("renders gate checksums (truncated)", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "success", report: MOCK_REPORT }} />
    );

    expect(markup).toContain("gate1checksum".slice(0, 12));
    expect(markup).toContain("gate2checksum".slice(0, 12));
    expect(markup).toContain("gate3checksum".slice(0, 12));
  });

  it("renders pipeline ID", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "success", report: MOCK_REPORT }} />
    );

    expect(markup).toContain("springboot-216-to-356-java21-three-stage");
  });

  it("renders static proof evidence table", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "success", report: MOCK_REPORT }} />
    );

    expect(markup).toContain("Proof and report evidence");
    expect(markup).toContain("Proof requires all three deterministic stage gates");
    expect(markup).toContain("Model summaries cannot create or override proof gates");
    expect(markup).toContain("Pipeline ID is locked to springboot-216-to-356-java21-three-stage");
  });

  it("renders all evidence with PASS status", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "success", report: MOCK_REPORT }} />
    );

    const passCount = (markup.match(/PASS/g) || []).length;
    expect(passCount).toBe(8); // 8 evidence items
  });

  it("renders evidence notes details section", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "success", report: MOCK_REPORT }} />
    );

    expect(markup).toContain("Proof and report evidence notes");
    expect(markup).toContain("Proof gates are computed from stage chain ledger outputs, never from LLM output");
    expect(markup).toContain("No proof generation triggers shell, Maven, or model execution from the UI");
  });

  it("shows redaction notice at top", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "success", report: MOCK_REPORT }} />
    );

    expect(markup).toContain("All paths, secrets, and identifiers are redacted");
  });

  // ── No dangerous controls ─────────────────────────────────────────

  it("has no dangerous input controls in any state", () => {
    const successMarkup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "success", report: MOCK_REPORT }} />
    );
    const loadingMarkup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "loading" }} />
    );
    const emptyMarkup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "empty" }} />
    );
    const errorMarkup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "error", message: "err" }} />
    );

    for (const markup of [successMarkup, loadingMarkup, emptyMarkup, errorMarkup]) {
      expect(markup).not.toContain("<input");
      expect(markup).not.toContain("<select");
      expect(markup).not.toContain("<textarea");
      expect(markup).not.toContain("<form");
    }
  });

  it("has no proof generation or execution buttons in any state", () => {
    const successMarkup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "success", report: MOCK_REPORT }} />
    );
    const loadingMarkup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "loading" }} />
    );
    const emptyMarkup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "empty" }} />
    );
    const errorMarkup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "error", message: "err" }} />
    );

    for (const markup of [successMarkup, loadingMarkup, emptyMarkup, errorMarkup]) {
      expect(markup).not.toContain("<input");
      expect(markup).not.toContain("<select");
      expect(markup).not.toContain("<textarea");
      expect(markup).not.toContain("<form");
    }
  });

  it("has no raw shell, Maven goal, path, or model deployment controls", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "success", report: MOCK_REPORT }} />
    );

    expect(markup).not.toContain("<input");
    expect(markup).not.toContain("<select");
    expect(markup).not.toContain("<textarea");
  });

  it("shows read-only proof evidence with no execution controls", () => {
    const markup = renderToStaticMarkup(
      <ProofReportContent state={{ status: "success", report: MOCK_REPORT }} />
    );

    expect(markup).toContain("No proof generation triggers shell, Maven, or model execution from the UI");
    expect(markup).toContain("Browser payloads cannot choose raw paths, Maven goals, shell commands, or model deployments");
  });
});
