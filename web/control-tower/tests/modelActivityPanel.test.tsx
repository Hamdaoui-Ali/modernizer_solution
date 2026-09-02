import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ModelActivityContent } from "../app/jobs/[jobId]/ModelActivityPanel";
import type { ModelInvocationEntry } from "../lib/contracts";

const MOCK_INVOCATIONS: ModelInvocationEntry[] = [
  {
    invocation_id: "inv-001",
    job_id: "test-job-123",
    profile_id: "profile-azure-eastus",
    model_name: "gpt-4o",
    prompt_tokens: 150,
    completion_tokens: 42,
    total_tokens: 192,
    redacted_summary: "Analyzed stage 1 compilation output",
    actor_type: "assistant",
    actor_id: "assistant-01",
    created_at: "2026-06-12T00:00:00Z",
    correlation_id: null,
    causation_id: null,
  },
  {
    invocation_id: "inv-002",
    job_id: "test-job-123",
    profile_id: "profile-azure-eastus",
    model_name: "gpt-4o",
    prompt_tokens: 210,
    completion_tokens: 78,
    total_tokens: 288,
    redacted_summary: "Generated patch for pom.xml dependency",
    actor_type: "assistant",
    actor_id: "assistant-01",
    created_at: "2026-06-12T00:01:00Z",
    correlation_id: null,
    causation_id: null,
  },
];

describe("V1-18D Model activity panel", () => {
  // ── Loading state ──────────────────────────────────────────────────

  it("renders loading state", () => {
    const markup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "loading" }} />
    );

    expect(markup).toContain("Model activity");
    expect(markup).toContain("Loading model activity evidence");
  });

  // ── Empty state ────────────────────────────────────────────────────

  it("renders empty state when no invocations exist", () => {
    const markup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "empty" }} />
    );

    expect(markup).toContain("Model activity");
    expect(markup).toContain("No model invocations");
    expect(markup).toContain("Invocations appear after a migration job runs model-backed steps");
  });

  // ── Error state ────────────────────────────────────────────────────

  it("renders error state when API fails", () => {
    const markup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "error", message: "Endpoint not available" }} />
    );

    expect(markup).toContain("Model activity");
    expect(markup).toContain("Failed to load model activity");
    expect(markup).toContain("Endpoint not available");
  });

  // ── Success state ──────────────────────────────────────────────────

  it("renders invocations when data loads", () => {
    const markup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "success", invocations: MOCK_INVOCATIONS }} />
    );

    expect(markup).toContain("Model activity");
    expect(markup).toContain("gpt-4o");
    expect(markup).toContain("profile-azure-eastus");
    expect(markup).not.toContain("azure-openai");
  });

  it("renders token counts for each invocation", () => {
    const markup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "success", invocations: MOCK_INVOCATIONS }} />
    );

    expect(markup).toContain("150 prompt");
    expect(markup).toContain("42 completion");
    expect(markup).toContain("192 total");
    expect(markup).toContain("210 prompt");
    expect(markup).toContain("78 completion");
    expect(markup).toContain("288 total");
  });

  it("renders redacted summaries without raw prompts", () => {
    const markup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "success", invocations: MOCK_INVOCATIONS }} />
    );

    expect(markup).toContain("Analyzed stage 1 compilation output");
    expect(markup).toContain("Generated patch for pom.xml dependency");
    // Raw prompt data, secrets, and deployment IDs must not leak in invocation fields
    // (static evidence descriptions mentioning redaction are acceptable)
    expect(markup).not.toContain(">This is the raw prompt");
    expect(markup).not.toContain(">my-secret-token");
    expect(markup).not.toContain(">deployment-id");
    // Deployment IDs must not appear as invocation field values
    expect(markup).not.toContain(">azure-eastus");
  });

  it("renders static model redaction and audit evidence", () => {
    const markup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "success", invocations: MOCK_INVOCATIONS }} />
    );

    expect(markup).toContain("Model redaction and audit evidence");
    expect(markup).toContain("Raw prompts are never stored in DTOs");
    expect(markup).toContain("Secrets and deployment IDs are absent from audit records");
    expect(markup).toContain("Prompt tokens are counted and exposed without raw prompt content");
    expect(markup).toContain("Runtime provider details are absent from browser DTOs");
    expect(markup).toContain("Context pack manifests carry redacted summaries without raw prompts");
    expect(markup).toContain("Advisory validation reports redact model reasoning and raw payloads");
  });

  it("renders all evidence with PASS status", () => {
    const markup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "success", invocations: MOCK_INVOCATIONS }} />
    );

    const passCount = (markup.match(/PASS/g) || []).length;
    expect(passCount).toBe(10); // 10 evidence items
  });

  it("renders evidence notes details section", () => {
    const markup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "success", invocations: MOCK_INVOCATIONS }} />
    );

    expect(markup).toContain("Model activity evidence notes");
    expect(markup).toContain("raw prompts, secrets, and deployment IDs are never exposed to the browser");
    expect(markup).toContain("No live Azure behavior is triggered from the browser");
  });

  it("shows redaction notice at top", () => {
    const markup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "success", invocations: MOCK_INVOCATIONS }} />
    );

    expect(markup).toContain("All prompts, secrets, and deployment identifiers are redacted");
  });

  // ── No dangerous controls ─────────────────────────────────────────

  it("has no dangerous input controls in any state", () => {
    const successMarkup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "success", invocations: MOCK_INVOCATIONS }} />
    );
    const loadingMarkup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "loading" }} />
    );
    const emptyMarkup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "empty" }} />
    );
    const errorMarkup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "error", message: "err" }} />
    );

    for (const markup of [successMarkup, loadingMarkup, emptyMarkup, errorMarkup]) {
      expect(markup).not.toContain("<input");
      expect(markup).not.toContain("<select");
      expect(markup).not.toContain("<textarea");
      expect(markup).not.toContain("<button");
      expect(markup).not.toContain("<form");
    }
  });

  it("has no raw shell, Maven, path, model deployment, or execute controls", () => {
    const markup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "success", invocations: MOCK_INVOCATIONS }} />
    );

    // No executable editors
    expect(markup).not.toContain("mvn");
    expect(markup).not.toContain("goal");
    expect(markup).not.toContain("cmd");
    // No approve/reject/execute
    expect(markup).not.toContain("Approve");
    expect(markup).not.toContain("Reject");
    expect(markup).not.toContain("Execute");
  });

  it("shows read-only model evidence with no execution controls", () => {
    const markup = renderToStaticMarkup(
      <ModelActivityContent state={{ status: "success", invocations: MOCK_INVOCATIONS }} />
    );

    expect(markup).toContain("No model execute, approve, or deploy controls are exposed");
  });
});
