import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AssistantPanel } from "../app/jobs/[jobId]/AssistantPanel";

const JOB_ID = "job-001";

describe("V1-18F Assistant panel", () => {
  it("renders panel with assistant heading", () => {
    const markup = renderToStaticMarkup(<AssistantPanel jobId={JOB_ID} />);

    expect(markup).toContain("Assistant panel");
    expect(markup).toContain("Read-only tool allowlist");
    expect(markup).toContain("Redaction and guardrails");
    expect(markup).toContain("Messages");
  });

  it("renders connect button when disconnected", () => {
    const markup = renderToStaticMarkup(<AssistantPanel jobId={JOB_ID} />);

    expect(markup).toContain("Connect");
    expect(markup).toContain("Disconnected");
  });

  it("shows empty state when no messages", () => {
    const markup = renderToStaticMarkup(<AssistantPanel jobId={JOB_ID} />);

    expect(markup).toContain("No assistant messages yet");
  });

  it("renders all read-only tool allowlist entries", () => {
    const markup = renderToStaticMarkup(<AssistantPanel jobId={JOB_ID} />);

    expect(markup).toContain("get_job_status");
    expect(markup).toContain("get_context_pack");
    expect(markup).toContain("list_context_packs");
    expect(markup).toContain("get_command_output_window");
    expect(markup).toContain("list_artifacts");
    expect(markup).toContain("list_model_invocations");
    expect(markup).toContain("get_pipeline_info");
    expect(markup).toContain("get_stage_chain");
    expect(markup).toContain("list_audit_records");
    expect(markup).toContain("retrieve_evidence");
  });

  it("renders redaction and guardrails description", () => {
    const markup = renderToStaticMarkup(<AssistantPanel jobId={JOB_ID} />);

    expect(markup).toContain("V1-00D redaction baseline");
    expect(markup).toContain("read-only");
    expect(markup).toContain("replace");
  });

  it("renders initial messages when provided", () => {
    const messages = [
      {
        message_id: "msg-1",
        role: "assistant" as const,
        content: "Hello, I am the assistant",
      },
      {
        message_id: "msg-2",
        role: "user" as const,
        content: "What is the job status?",
      },
    ];
    const markup = renderToStaticMarkup(
      <AssistantPanel jobId={JOB_ID} initialMessages={messages} />
    );

    expect(markup).toContain("Hello, I am the assistant");
    expect(markup).toContain("What is the job status?");
    expect(markup).toContain("Assistant");
    expect(markup).toContain("User");
  });

  // ── No dangerous controls evidence ─────────────────────────────────
  // The panel describes what the assistant CANNOT do (guardrails).
  // These tests verify no actual dangerous UI controls exist.

  it("has no shell command input controls", () => {
    const markup = renderToStaticMarkup(<AssistantPanel jobId={JOB_ID} />);

    expect(markup).not.toContain('<input name="cmd"');
    expect(markup).not.toContain('<input name="shell"');
    expect(markup).not.toContain('type="submit"');
    expect(markup).not.toContain('<textarea');
  });

  it("has no Maven goal inputs", () => {
    const markup = renderToStaticMarkup(<AssistantPanel jobId={JOB_ID} />);

    expect(markup).not.toContain("mvn");
    expect(markup).not.toContain('<input name="goal"');
    expect(markup).not.toContain('<select name="goal"');
  });

  it("has no file write or save controls", () => {
    const markup = renderToStaticMarkup(<AssistantPanel jobId={JOB_ID} />);

    expect(markup).not.toContain('<input type="file"');
    expect(markup).not.toContain('<input name="content"');
    expect(markup).not.toContain('<input name="save"');
    expect(markup).not.toContain('<input name="upload"');
  });

  it("has no approve/reject or execute buttons", () => {
    const markup = renderToStaticMarkup(<AssistantPanel jobId={JOB_ID} />);

    // Only "Connect" and "Disconnect" buttons should exist
    const buttonCount = (markup.match(/<button/g) || []).length;
    expect(buttonCount).toBeLessThanOrEqual(2);
    expect(markup).not.toContain("<button>Approve");
    expect(markup).not.toContain("<button>Reject");
  });

  it("has no model deployment selectors", () => {
    const markup = renderToStaticMarkup(<AssistantPanel jobId={JOB_ID} />);

    expect(markup).not.toContain('<select name="model"');
    expect(markup).not.toContain('<select name="deployment"');
    expect(markup).not.toContain('<input name="deployment_id"');
  });

  it("has no raw path or directory inputs", () => {
    const markup = renderToStaticMarkup(<AssistantPanel jobId={JOB_ID} />);

    expect(markup).not.toContain('<input name="path"');
    expect(markup).not.toContain('<input name="directory"');
    expect(markup).not.toContain('<input name="working_dir"');
  });
});
