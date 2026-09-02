import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StageTimelineContent } from "../app/jobs/[jobId]/StageTimelinePanel";
import type { StageChainEntry } from "../lib/contracts";

const MOCK_STAGES: StageChainEntry[] = [
  {
    ledger_id: "l1",
    job_id: "test-job-123",
    stage_index: 1,
    stage_run_id: "sr1",
    chain_status: "pending",
    input_source_kind: "legacy_source",
    input_checksum: null,
    output_artifact_id: null,
    output_checksum: null,
    output_registered_at: null,
    created_at: "2026-06-12T00:00:00Z",
  },
  {
    ledger_id: "l2",
    job_id: "test-job-123",
    stage_index: 2,
    stage_run_id: "sr2",
    chain_status: "pending",
    input_source_kind: "previous_stage",
    input_checksum: null,
    output_artifact_id: null,
    output_checksum: null,
    output_registered_at: null,
    created_at: "2026-06-12T00:00:00Z",
  },
  {
    ledger_id: "l3",
    job_id: "test-job-123",
    stage_index: 3,
    stage_run_id: "sr3",
    chain_status: "pending",
    input_source_kind: "previous_stage",
    input_checksum: null,
    output_artifact_id: null,
    output_checksum: null,
    output_registered_at: null,
    created_at: "2026-06-12T00:00:00Z",
  },
];

describe("V1-18B Stage timeline panel", () => {
  // ── Loading state ──────────────────────────────────────────────────

  it("renders loading state", () => {
    const markup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "loading" }} />
    );

    expect(markup).toContain("Stage timeline");
    expect(markup).toContain("Loading stage chain evidence");
  });

  // ── Empty state ────────────────────────────────────────────────────

  it("renders empty state when no stages exist", () => {
    const markup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "empty" }} />
    );

    expect(markup).toContain("Stage timeline");
    expect(markup).toContain("No stage chain entries");
    expect(markup).toContain("Stages appear after a migration job is created");
  });

  // ── Error state ────────────────────────────────────────────────────

  it("renders error state when API fails", () => {
    const markup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "error", message: "API unavailable" }} />
    );

    expect(markup).toContain("Stage timeline");
    expect(markup).toContain("Failed to load stage timeline");
    expect(markup).toContain("API unavailable");
  });

  // ── Success state ──────────────────────────────────────────────────

  it("renders three stages in order when data loads", () => {
    const markup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "success", stages: MOCK_STAGES }} />
    );

    expect(markup).toContain("Stage timeline");
    expect(markup).toContain("Stage 1");
    expect(markup).toContain("Stage 2");
    expect(markup).toContain("Stage 3");
  });

  it("renders V1 pipeline evidence text", () => {
    const markup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "success", stages: MOCK_STAGES }} />
    );

    expect(markup).toContain("springboot-216-to-356-java21-three-stage");
    expect(markup).toContain("Java 11");
    expect(markup).toContain("Java 17");
    expect(markup).toContain("Java 21");
    expect(markup).toContain("Spring Boot 2.7.18");
    expect(markup).toContain("Spring Boot 3.5.6");
  });

  it("renders stage input source kinds", () => {
    const markup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "success", stages: MOCK_STAGES }} />
    );

    expect(markup).toContain("legacy source");
    expect(markup).toContain("previous stage sandbox");
  });

  it("renders chain status for each stage", () => {
    const markup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "success", stages: MOCK_STAGES }} />
    );

    const pendingCount = (markup.match(/pending/g) || []).length;
    expect(pendingCount).toBeGreaterThanOrEqual(3);
  });

  it("renders evidence notes details section", () => {
    const markup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "success", stages: MOCK_STAGES }} />
    );

    expect(markup).toContain("Stage pipeline evidence notes");
    expect(markup).toContain("Boot 4 is not available");
    expect(markup).toContain("is not execution-relevant for V1");
    expect(markup).toContain("Stage ordering is enforced by the backend");
  });

  it("shows Boot 4 not selectable and 3.5.14 not execution-relevant", () => {
    const markup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "success", stages: MOCK_STAGES }} />
    );

    expect(markup).toContain("Boot 4 is not selectable");
    expect(markup).toContain("3.5.14</code> is not execution-relevant for V1");
  });

  it("renders redaction notice", () => {
    const markup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "success", stages: MOCK_STAGES }} />
    );

    expect(markup).toContain("All paths, secrets, and identifiers are redacted");
  });

  // ── No dangerous controls ─────────────────────────────────────────

  it("has no dangerous input controls in any state", () => {
    const successMarkup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "success", stages: MOCK_STAGES }} />
    );
    const loadingMarkup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "loading" }} />
    );
    const emptyMarkup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "empty" }} />
    );
    const errorMarkup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "error", message: "err" }} />
    );

    for (const markup of [successMarkup, loadingMarkup, emptyMarkup, errorMarkup]) {
      expect(markup).not.toContain("<input");
      expect(markup).not.toContain("<select");
      expect(markup).not.toContain("<textarea");
      expect(markup).not.toContain("<button");
      expect(markup).not.toContain("<form");
    }
  });

  it("has no raw shell, Maven, path, model, or execute controls", () => {
    const markup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "success", stages: MOCK_STAGES }} />
    );

    // No executable editors
    expect(markup).not.toContain("mvn");
    expect(markup).not.toContain("goal");
    expect(markup).not.toContain("cmd");
    // No deployment/model selectors
    expect(markup).not.toContain("deployment");
    expect(markup).not.toContain("model");
    // No approve/reject/execute
    expect(markup).not.toContain("Approve");
    expect(markup).not.toContain("Reject");
    expect(markup).not.toContain("Execute");
  });

  it("shows read-only stage evidence with no execution controls", () => {
    const markup = renderToStaticMarkup(
      <StageTimelineContent state={{ status: "success", stages: MOCK_STAGES }} />
    );

    expect(markup).toContain("All stage chain evidence is read-only");
    expect(markup).toContain("No approve, reject, or execute controls");
  });
});
