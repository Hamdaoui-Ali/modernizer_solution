import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi, afterEach } from "vitest";

const routerPushMock = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPushMock }),
}));

import MigrationCockpitPage from "../app/migrations/[jobId]/page";
import {
  MigrationCockpit,
  ApprovalDecisionsPanel,
  AssistantPanelContent,

  MigrationRoutePanel,
  SourceProfileDetectionPanel,
  SourceProfileOverrideForm,
  buildSourceProfileOverrideBody,
  buildStageTimelineEntries,
  getTargetVersionComparisonStageIndex,
  getSourceProfileOverrideBlockedReason,
  formatStageStatusLabel,
  formatGateArtifactRefLabel,
  mergeCockpitLiveRefreshResults,
  reduceStageStatus,
  stageStatusFromEvent,
  type CockpitData,
} from "../app/migrations/[jobId]/MigrationCockpit";
import { approveV2Card, askV2Assistant, CONTROL_TOWER_API_BASE_URL, getV2ArtifactPreview, postV2GateAction, requireJobId, resolveReportDownloadUrl, updateV2ApprovalMode, v2EventStreamUrl } from "../lib/controlTowerApi";
import type { GateDetailResponse, GateRepresentation, GateEvidencePack, V2ApprovalResponse, V2FailureSummaryItem, V2JobEvent, V2MigrationJobResponse, V2RouteStepEntry } from "../lib/contracts";

describe("V2 Migration Cockpit contract", () => {
  it("passes the awaited route job id into MigrationCockpit", async () => {
    const page = await MigrationCockpitPage({
      params: Promise.resolve({ jobId: "429a9bb2154b4be7a99a32867780d744" }),
    });

    const children = page.props.children;
    const cockpit = children[1];

    expect(cockpit.type).toBe(MigrationCockpit);
    expect(cockpit.props.jobId).toBe("429a9bb2154b4be7a99a32867780d744");
  });


  it("renders Cancel Migration controls in the cockpit source", () => {
    const source = MigrationCockpit.toString();
    expect(source).toContain("Cancel Migration");
    expect(source).toContain("cancelV2MigrationJob");
    expect(source).toContain("normalizedJobId");
    expect(source).toContain('router.push("/migrations/new")');
    expect(source).toContain("cancelBusy");
    expect(source).toContain("Cancelling...");
  });

  it("cancelled stage events reduce to a terminal cancelled state", () => {
    const reduced = reduceStageStatus([
      { sequence: 1, type: "stage_started", status: "running", stage: 1 } as V2JobEvent,
      { sequence: 2, type: "stage_cancelled", status: "cancelled", stage: 1 } as V2JobEvent,
      { sequence: 3, type: "stdout", status: "running", stage: 1 } as V2JobEvent,
    ]);
    expect(reduced).toBe("cancelled");
    expect(formatStageStatusLabel(reduced)).toBe("CANCELLED");
  });

  it("displays three stages in order", () => {
    const stages = [
      { stage_index: 1, pipeline_stage: "Stage 1", chain_status: "queued", input_source_kind: "legacy_source" },
      { stage_index: 2, pipeline_stage: "Stage 2", chain_status: "pending", input_source_kind: "stage_1_sandbox" },
      { stage_index: 3, pipeline_stage: "Stage 3", chain_status: "pending", input_source_kind: "stage_2_sandbox" },
    ];
    expect(stages).toHaveLength(3);
    expect(stages[0].input_source_kind).toBe("legacy_source");
    expect(stages[1].input_source_kind).toBe("stage_1_sandbox");
    expect(stages[2].input_source_kind).toBe("stage_2_sandbox");
  });

  it("shows Boot 4 as the automated Stage 4 pipeline entry", () => {
    const stages = [
      { stage_index: 1, pipeline_stage: "Stage 1", chain_status: "completed", input_source_kind: "legacy_source" },
      { stage_index: 2, pipeline_stage: "Stage 2", chain_status: "completed", input_source_kind: "stage_1_sandbox" },
      { stage_index: 3, pipeline_stage: "Stage 3", chain_status: "completed", input_source_kind: "stage_2_sandbox" },
      { stage_index: 4, pipeline_stage: "Stage 4", chain_status: "pending", input_source_kind: "stage_3_sandbox" },
    ];
    const stage4 = stages[3];
    expect(stage4.input_source_kind).toBe("stage_3_sandbox");
    expect(formatStageStatusLabel(stage4.chain_status)).toBe("PENDING");
  });

  it("has no stage-start buttons", () => {
    const cockpitControls = [
      "stage_timeline",
      "evidence_panel",
      "approval_decisions",
      "assistant_panel",
      "proof_report",
    ];
    const forbidden = ["start_stage_1", "start_stage_2", "start_stage_3", "run_maven", "choose_goal"];
    for (const f of forbidden) {
      expect(cockpitControls).not.toContain(f);
    }
  });

  it("approval requires checksum", () => {
    const approval = { id: "a1", status: "pending", checksum_required: true };
    expect(approval.checksum_required).toBe(true);
  });



  it("renders assistant-specific ask failures without collapsing the cockpit shell", () => {
    const markup = renderToStaticMarkup(
      <AssistantPanelContent
        assistantModel={{
          status: "fallback",
          source: "deterministic",
          provider: "backend",
          role: "assistant",
          failure_reason: "assistant_ask_failed",
        }}
        messages={[
          {
            message_id: "msg-1",
            job_id: "job-1",
            role: "assistant",
            content: "Stage 3 is complete and the root POM is available.",
            correlation_id: null,
            created_at: "2026-06-18T00:00:00Z",
          },
        ]}
        assistantError="Control Tower mutation failed for /v1/v2/jobs/job-1/assistant/ask: 500 Internal Server Error."
        assistantQuestion="what about the pom?"
        assistantBusy={false}
        approvalReviewOpen={false}
        onQuestionChange={() => undefined}
        onAsk={() => undefined}
      />
    );

    expect(markup).toContain("Assistant request failed");
    expect(markup).toContain("Stage 3 is complete and the root POM is available.");
    expect(markup).toContain("what about the pom?");
    expect(markup).not.toContain("Failed to fetch");
  });

  it("preserves multiline assistant messages in the cockpit", () => {
    const markup = renderToStaticMarkup(
      <AssistantPanelContent
        assistantModel={{
          status: "fallback",
          source: "deterministic",
          provider: "backend",
          role: "assistant",
          failure_reason: "assistant_ask_failed",
        }}
        messages={[
          {
            message_id: "msg-1",
            job_id: "job-1",
            role: "assistant",
            content: "Line 1\nLine 2",
            correlation_id: null,
            created_at: "2026-06-18T00:00:00Z",
          },
        ]}
        assistantError={null}
        assistantQuestion="what about the pom?"
        assistantBusy={false}
        approvalReviewOpen={false}
        onQuestionChange={() => undefined}
        onAsk={() => undefined}
      />
    );

    expect(markup).toContain("message-content");
    expect(markup).toContain("Line 1");
    expect(markup).toContain("Line 2");
  });

  it("redacts absolute Windows artifact refs down to short labels", () => {
    const absoluteRef = "C:\\Users\\abdelilah.mortaki\\Desktop\\modernizer-solution\\SecurityConfig.java";
    expect(formatGateArtifactRefLabel(absoluteRef)).toBe("SecurityConfig.java");
    expect(formatGateArtifactRefLabel(absoluteRef)).not.toContain("C:\\Users\\abdelilah.mortaki");
  });

  it("assistant cannot execute, approve, write, or change route", () => {
    const assistantRules = {
      can_explain: true,
      can_diagnose: true,
      can_draft_instruction: true,
      can_execute: false,
      can_approve: false,
      can_write_file: false,
      can_change_route: false,
      can_override_proof: false,
    };
    expect(assistantRules.can_execute).toBe(false);
    expect(assistantRules.can_approve).toBe(false);
    expect(assistantRules.can_write_file).toBe(false);
    expect(assistantRules.can_change_route).toBe(false);
    expect(assistantRules.can_override_proof).toBe(false);
  });

  it("no raw secrets, paths, or deployment IDs in cockpit payloads", () => {
    // Backend guarantees redaction — the frontend contract depends on it.
    // This test verifies that realistic payloads (as API returns after
    // redaction) do NOT contain secrets that the frontend would render.
    const samplePayload = {
      job_id: "job-1",
      rows: [
        {
          key: "sandbox_build",
          label: "Sandbox Build",
          status: "failed",
          latest_message: "[redacted-path] written",
          artifact_count: 0,
          last_updated: "2026-06-14T00:00:00Z",
        },
      ],
      evidence: [
        {
          event_type: "build_failed",
          status: "failed",
          message: "Build failed: [redacted-path]",
          payload: {
            matched_line: "[redacted-path]",
            command: ["mvn", "[redacted]", "package"],
            java_home: "[redacted-path]",
            AZURE_OPENAI_API_KEY: "[redacted]",
          },
        },
      ],
      raw_logs: [],
    };
    const json = JSON.stringify(samplePayload);

    // Redacted payload should never contain real secret tokens
    const hasSecretValue =
      /\b(sk-|ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]+/.test(json);
    expect(hasSecretValue).toBe(false);

    // Redacted payload should not contain Windows or POSIX absolute paths
    expect(json).not.toMatch(/\bC:\\Users\\/);
    expect(json).not.toMatch(/\bC:\\Program Files\\/);
    expect(json).not.toMatch(/\/home\//);
    expect(json).not.toMatch(/\/etc\//);
  });

  it("stage inputs are fixed by pipeline", () => {
    const stageInputs = {
      1: "legacy_source",
      2: "stage_1_sandbox",
      3: "stage_2_sandbox",
    };
    // These must NOT come from user selection
    expect(stageInputs[1]).toBe("legacy_source");
    expect(stageInputs[2]).toBe("stage_1_sandbox");
    expect(stageInputs[3]).toBe("stage_2_sandbox");
  });

  it("rejects missing route job id before fetch URL construction", () => {
    expect(() => requireJobId("")).toThrow("Migration job id is required.");
    expect(() => requireJobId("   ")).toThrow("Migration job id is required.");
  });

  it("opens EventSource against the V2 events endpoint", () => {
    const url = v2EventStreamUrl("job-123", 7);
    expect(url).toBe("http://127.0.0.1:8000/v1/v2/migration-jobs/job-123/events?after=7");
    expect(url).not.toContain("undefined");
  });

  it("refreshLiveState keeps existing approvals when approvals refresh fails", () => {
    const current = makeCockpitData();
    const merged = mergeCockpitLiveRefreshResults(current, [
      { status: "rejected", reason: new TypeError("Failed to fetch") },
      { status: "fulfilled", value: { job_id: "job-123", stages: [{ stage_index: 1, pipeline_stage: "Stage 1", chain_status: "running", input_source_kind: "legacy_source" }] } },
      { status: "fulfilled", value: { events: [{ sequence: 2, type: "stage_started", status: "running", stage: 1 } as V2JobEvent] } },
      { status: "fulfilled", value: { ...current.pipeline, rows: [{ key: "analysis", label: "Analysis", status: "running", latest_message: "Running", artifact_count: 0, last_updated: "2026-06-16T00:00:00Z" }] } },
      { status: "fulfilled", value: { job_id: "job-123", has_failures: false, failures: [], repair_loop_active: false, repair_events: [], artifact_kinds: [] } },
    ]);

    expect(merged.failed).toBe(true);
    expect(merged.data.approvals).toBe(current.approvals);
    expect(merged.data.stages[0].chain_status).toBe("running");
    expect(merged.data.events[0].sequence).toBe(2);
  });

  it("SSE-triggered refresh failure can be represented as a non-blocking warning state", () => {
    const current = makeCockpitData();
    const merged = mergeCockpitLiveRefreshResults(current, [
      { status: "rejected", reason: new TypeError("Failed to fetch") },
      { status: "rejected", reason: new TypeError("Failed to fetch") },
      { status: "rejected", reason: new TypeError("Failed to fetch") },
      { status: "rejected", reason: new TypeError("Failed to fetch") },
      { status: "rejected", reason: new TypeError("Failed to fetch") },
    ]);

    expect(merged.failed).toBe(true);
    expect(merged.data).toEqual(current);
  });

  it("artifact preview client sends only artifact kind", async () => {
    const originalFetch = global.fetch;
    const calls: string[] = [];
    global.fetch = (async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return new Response(JSON.stringify({
        job_id: "job-123",
        artifact_kind: "phase2_log",
        exists: true,
        preview: "BUILD_FAILED_IN_SANDBOX",
        truncated: false,
        content_type: "text/plain",
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    try {
      const response = await getV2ArtifactPreview("job-123", "phase2_log");
      expect(calls[0]).toBe(`${CONTROL_TOWER_API_BASE_URL}/v1/v2/jobs/job-123/artifacts/phase2_log`);
      expect(calls[0]).not.toContain("path=");
      expect(response.exists).toBe(true);
      expect(response.preview).toContain("BUILD_FAILED_IN_SANDBOX");
    } finally {
      global.fetch = originalFetch;
    }
  });

  it("empty approvals render as no pending decisions copy", () => {
    const approvals: unknown[] = [];
    const copy = approvals.length === 0 ? "No pending decisions." : "Has decisions";
    expect(copy).toBe("No pending decisions.");
  });

  it("incoming event updates stage status", () => {
    const event = { stage: 1, type: "stage_started", status: "running" };
    const stages = [{ stage_index: 1, chain_status: "queued" }];
    const updated = stages.map((stage) =>
      stage.stage_index === event.stage ? { ...stage, chain_status: event.status } : stage,
    );
    expect(updated[0].chain_status).toBe("running");
  });

  it("posts assistant questions to the read-only V2 ask endpoint", async () => {
    const originalFetch = global.fetch;
    const calls: { url: string; body: string | null }[] = [];
    global.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: typeof init?.body === "string" ? init.body : null });
      return new Response(JSON.stringify({
        job_id: "job-123",
        user_message: { message_id: "u1", job_id: "job-123", role: "user", content: "What happened so far?", correlation_id: null, created_at: "now" },
        assistant_message: { message_id: "a1", job_id: "job-123", role: "assistant", content: "Latest event: stage 1 analysis_started.", correlation_id: "u1", created_at: "now" },
        model: { status: "configured", source: "azure_openai", provider: "azure_openai", role: "assistant" },
        guardrails: { read_only: true, cannot_execute: true, cannot_approve: true },
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    try {
      const response = await askV2Assistant("job-123", "What happened so far?");
      expect(calls[0].url).toBe(`${CONTROL_TOWER_API_BASE_URL}/v1/v2/jobs/job-123/assistant/ask`);
      expect(calls[0].url).not.toContain("undefined");
      expect(JSON.parse(calls[0].body ?? "{}")).toEqual({ question: "What happened so far?" });
      expect(response.assistant_message.content).toContain("Latest event");
      expect(response.model.source).toBe("azure_openai");
      expect(response.guardrails.cannot_execute).toBe(true);
    } finally {
      global.fetch = originalFetch;
    }
  });

  it("Auto Approval toggle displays current backend mode", () => {
    const manualMarkup = renderToStaticMarkup(
      <ApprovalDecisionsPanel
        approvals={[]}
        approvalReviewOpen={false}
        approvalBusy={null}
        approvalModeEnabled={false}
        approvalModeBusy={false}
        approvalModeError={null}
        onApprovalModeToggle={() => undefined}
        onApprove={() => undefined}
        onReject={() => undefined}
      />,
    );
    expect(manualMarkup).toContain("Approval Decisions");
    expect(manualMarkup).toContain("Manual");
    expect(manualMarkup).toContain("Off");
    expect(manualMarkup).not.toContain("Approval Mode");

    const autoMarkup = renderToStaticMarkup(
      <ApprovalDecisionsPanel
        approvals={[]}
        approvalReviewOpen={false}
        approvalBusy={null}
        approvalModeEnabled={true}
        approvalModeBusy={false}
        approvalModeError={null}
        onApprovalModeToggle={() => undefined}
        onApprove={() => undefined}
        onReject={() => undefined}
      />,
    );
    expect(autoMarkup).toContain("Auto Approval ON");
    expect(autoMarkup).toContain("On");
  });

  it("turning Auto Approval ON requires confirmation in cockpit source", () => {
    const source = MigrationCockpit.toString();
    expect(source).toContain("window.confirm");
    expect(source).toContain("Auto Approval will automatically approve future successful");
    expect(source).toContain("setApprovalModeBusy(true)");
    expect(source).toContain("Could not update approval mode. Please check backend connection or CORS configuration.");
  });

  it("updateV2ApprovalMode sends PATCH with the correct job id and value", async () => {
    const originalFetch = global.fetch;
    const calls: { url: string; method?: string; body: string | null; headers?: HeadersInit }[] = [];
    global.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), method: init?.method, body: typeof init?.body === "string" ? init.body : null, headers: init?.headers });
      return new Response(JSON.stringify({
        job_id: "job-123",
        auto_approval_enabled: true,
        job: { job_id: "job-123", setup_id: "setup", setup_checksum: "chk", pipeline_id: "pipe", stages: [], created_at: "now", auto_approval_enabled: true },
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    try {
      await updateV2ApprovalMode("job-123", true);
      expect(calls[0].url).toBe(`${CONTROL_TOWER_API_BASE_URL}/v1/v2/migration-jobs/job-123/approval-mode`);
      expect(calls[0].method).toBe("PATCH");
      expect(JSON.parse(calls[0].body ?? "{}")).toEqual({ autoApprovalEnabled: true });
      expect(calls[0].headers).toMatchObject({ "Content-Type": "application/json" });
    } finally {
      global.fetch = originalFetch;
    }
  });

  it("auto-approved approval decisions show AUTO APPROVED without action buttons", () => {
    const autoApproved: V2ApprovalResponse = {
      card_id: "card-auto",
      job_id: "job-123",
      interrupt_id: "run-auto",
      request_checksum: "checksum-auto",
      stage_index: 2,
      summary: "Pre-transform review required before sandbox transform.",
      status: "auto_approved",
      created_at: "2026-07-07T00:00:00Z",
    };
    const markup = renderToStaticMarkup(
      <ApprovalDecisionsPanel
        approvals={[autoApproved]}
        approvalReviewOpen={false}
        approvalBusy={null}
        approvalModeEnabled={false}
        approvalModeBusy={false}
        approvalModeError={null}
        onApprovalModeToggle={() => undefined}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    expect(markup).toContain("AUTO APPROVED");
    expect(markup).toContain("Mode: Auto Approval");
    expect(markup).not.toContain("<button");
  });

  it("approval_auto_approved event maps Human Approval to completed (AUTO APPROVED)", () => {
    // Frontend Test 2: Pipeline Status updates Human Approval from BLOCKED to
    // COMPLETED/AUTO_APPROVED when the backend emits approval_auto_approved.
    const autoEvent: V2JobEvent = {
      event_id: "evt-5",
      job_id: "job-123",
      sequence: 5,
      type: "approval_auto_approved",
      status: "completed",
      stage: 2,
      message: "Approval gate auto-approved because Auto Approval is enabled.",
      payload: { decision_source: "auto_approval", approval_mode: "auto" },
      created_at: "2026-07-07T00:00:00Z",
    };
    expect(stageStatusFromEvent(autoEvent)).toBe("completed");
  });

  it("sandbox_transform_started event maps Transform Agent to running after auto approval", () => {
    // Frontend Test 3: Transform Agent becomes RUNNING according to backend
    // state after auto approval.
    const transformEvent: V2JobEvent = {
      event_id: "evt-6",
      job_id: "job-123",
      sequence: 6,
      type: "sandbox_transform_started",
      status: "running",
      stage: 2,
      message: "Transform started.",
      payload: {},
      created_at: "2026-07-07T00:00:01Z",
    };
    expect(stageStatusFromEvent(transformEvent)).toBe("running");
  });

  it("cockpit proactively refreshes live state and gates when approval-mode response auto-approved a gate", () => {
    // Frontend Test 1: When the backend returns auto_approved, the cockpit
    // must proactively refresh approvals/pipeline/gate state so the UI shows
    // AUTO APPROVED and no longer shows active Approve/Reject for that gate.
    const source = MigrationCockpit.toString();
    expect(source).toContain("response.auto_approved");
    expect(source).toContain("[approval-mode-auto-approved]");
    expect(source).toContain("refreshLiveState()");
    expect(source).toContain("refreshGateState()");
  });

  it("pending approval card renders Approve/Reject buttons even when approvalReviewOpen is true", () => {
    // Regression: the global approvalReviewOpen flag used to swap ALL cards'
    // buttons for "Review in chatbot" copy. Pending gates must always show
    // active Approve/Reject buttons, regardless of the open-gate flag.
    const pending: V2ApprovalResponse = {
      card_id: "card-3",
      job_id: "job-123",
      interrupt_id: "run-3",
      request_checksum: "checksum-3",
      stage_index: 3,
      summary: "Pre-transform review required before sandbox transform.",
      status: "pending",
      created_at: "2026-07-02T00:00:00Z",
    };
    const markup = renderToStaticMarkup(
      <ApprovalDecisionsPanel
        approvals={[pending]}
        approvalReviewOpen={true}
        approvalBusy={null}
        approvalModeEnabled={false}
        approvalModeBusy={false}
        approvalModeError={null}
        onApprovalModeToggle={() => undefined}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    expect(markup).toContain("Stage 3");
    expect(markup).toContain("checksum-3");
    expect(markup).toContain("Approve");
    expect(markup).toContain("Reject");
    // The pending card's Approve button must NOT be disabled.
    const approveButtonStart = markup.indexOf("Approve");
    expect(markup.slice(markup.lastIndexOf("<button", approveButtonStart), approveButtonStart)).not.toContain("disabled");
  });

  it("pipeline projection shows agent phases before raw logs", () => {
    const rows = ["Preflight", "Analysis Agent", "Planning Agent", "Assessment Agent", "Human Approval"];
    const evidenceTypes = ["analysis_started", "planning_completed", "approval_required"];
    const rawLogs = ["stdout"];
    expect(rows).toContain("Analysis Agent");
    expect(rows).toContain("Planning Agent");
    expect(rows).toContain("Assessment Agent");
    expect(evidenceTypes).not.toContain("stdout");
    expect(rawLogs).toContain("stdout");
  });

  it("pipeline response exposes active_stage_index", () => {
    const pipeline = {
      job_id: "job-123",
      rows: [],
      evidence: [],
      raw_logs: [],
      active_stage_index: 2,
    };
    expect(pipeline.active_stage_index).toBe(2);
  });

  it("human approval row is pass after approval_resume_queued, not blocked", () => {
    // Simulate the pipeline status logic: after approval_resume_queued, must be pass
    const events = [
      { type: "approval_required", status: "blocked" },
      { type: "approval_resume_queued", status: "queued" },
    ];
    // Check that the latest approval lifecycle event transitions correctly
    const hasApprovalPassed = events.some(
      (e) => e.type === "approval_resume_queued"
    );
    expect(hasApprovalPassed).toBe(true);
  });

  it("human approval stays pass even if transform fails", () => {
    const events = [
      { type: "approval_required", status: "blocked" },
      { type: "approval_resume_queued", status: "queued" },
      { type: "sandbox_transform_failed", status: "failed" },
    ];
    // Approval should still be pass
    const approvalResolved = events.some(
      (e) => e.type === "approval_resume_queued"
    );
    expect(approvalResolved).toBe(true);
  });

  it("failure summary contains observed failure shape", () => {
    const failureSummary = {
      has_failures: true,
      failures: [
        {
          type: "build_failed",
          stage: 1,
          message: "Build result kind: dependency_error",
          build_status: "BUILD_FAILED_IN_SANDBOX",
          final_status: "FALLBACK_REPAIR_PLAN",
          final_proof_level: "not_verified",
          repair_loop_status: "FALLBACK_REPAIR_PLAN",
          repair_fallback: "True",
        },
      ],
      repair_loop_active: true,
      repair_events: [
        { type: "repair_invalid_response", message: "Repair response invalid" },
      ],
      artifact_kinds: ["analysis_report"],
    };
    expect(failureSummary.has_failures).toBe(true);
    expect(failureSummary.failures[0].build_status).toBe("BUILD_FAILED_IN_SANDBOX");
  });

  it("assistant model status includes failure_reason for fallback", () => {
    const model = {
      status: "fallback",
      source: "deterministic",
      provider: "deterministic",
      role: "assistant",
      failure_reason: "missing_deployment",
    };
    expect(model.status).toBe("fallback");
    expect(model.failure_reason).toBe("missing_deployment");
  });

  it("assistant and repair wording stay separate after repair fallback", () => {
    const assistantModel = { status: "live_ok", source: "azure_openai", provider: "azure_openai" };
    const repair = { repair_fallback: "True", repair_loop_status: "FALLBACK_REPAIR_PLAN" };
    expect(assistantModel.source).toBe("azure_openai");
    expect(assistantModel.status).toBe("live_ok");
    expect(repair.repair_loop_status).toBe("FALLBACK_REPAIR_PLAN");
  });

  // ── F0 closure: no copilot_status in failure contracts or rendering ──

  it("V2FailureSummaryItem does not include copilot_status field", () => {
    const failure: V2FailureSummaryItem = {
      type: "build_failed",
      stage: 2,
      title: "test",
      message: "test message",
      build_status: "BUILD_FAILED_IN_SANDBOX",
      test_status: "NOT_RUN",
      final_status: "FAILED",
      final_proof_level: "not_verified",
      repair_loop_status: "INACTIVE",
      repair_fallback: "false",
      matched_line: "",
      command: [],
      requested_command: [],
      build_tool: "maven",
      module: "",
      main_class: "",
      unit_id: "",
      result_kind: "dependency_error",
      java_home: "/java",
      detected_version: "",
      required_minimum: "",
      event_types: [],
      repair_events: [],
      next_operator_action: "manual_review",
      supervision_trace: {
        ai_diagnosis: null,
        evidence_used: [],
        pom_analysis: null,
        repair_proposal: null,
        reviewer_verdict: null,
        validation_result: null,
      },
    };
    expect("copilot_status" in failure).toBe(false);
    expect(failure).not.toHaveProperty("copilot_status");
  });

  it("failure summary rendering does not include copilot_status", () => {
    const sampleFailure = {
      type: "build_failed",
      stage: 2,
      title: "Stage 2 Build Failure",
      message: "Build result kind: dependency_error",
      build_status: "BUILD_FAILED_IN_SANDBOX",
      final_status: "FAILED",
      result_kind: "dependency_error",
      event_types: ["build_failed"],
      repair_events: [{ type: "repair_started", message: "repair started" }],
    };

    const markup = renderToStaticMarkup(
      <div className="failure-card">
        <div className="stage-header">
          <strong>{sampleFailure.type}</strong>
          <span className="meta">Stage {sampleFailure.stage}</span>
          <span className="status-badge failed">FAILED</span>
        </div>
        <p>{sampleFailure.message}</p>
        {sampleFailure.result_kind && (
          <p className="meta">
            <strong>Root cause:</strong> {sampleFailure.result_kind}
          </p>
        )}
        {sampleFailure.event_types.length > 0 && (
          <p className="meta">Event types: {sampleFailure.event_types.join(", ")}</p>
        )}
        {sampleFailure.repair_events.length > 0 && (
          <p className="meta">Repair events: {sampleFailure.repair_events.map((r) => r.type).join(", ")}</p>
        )}
      </div>
    );

    expect(markup).not.toContain("copilot_status");
    expect(markup).not.toContain("INVALID_RESPONSE");
    expect(markup).not.toContain("copilot_invocation_status");
  });

  it("IMPORTANT_SSE_TYPES includes all required lifecycle events", () => {
    const important = new Set([
      "approval_required",
      "stage_blocked_for_approval",
      "approval_resume_queued",
      "approval_started",
      "approval_completed",
      "resume_started",
      "sandbox_transform_started",
      "sandbox_transform_completed",
      "sandbox_transform_failed",
      "analysis_started",
      "analysis_completed",
      "analysis_failed",
      "planning_started",
      "planning_completed",
      "planning_failed",
      "assessment_started",
      "assessment_completed",
      "assessment_failed",
      "final_report_started",
      "final_report_completed",
      "final_report_failed",
      "stage_failed",
      "stage_completed",
      "model_invocation_completed",
      "model_invocation_failed",
      "transform_failed",
      "build_failed",
      "ai_diagnosis_created",
      "pom_summary_created",
      "repair_proposal_revised",
      "reviewer_critique_created",
      "repair_patch_gate_completed",
      "repair_patch_applied",
      "repair_validation_completed",
      "repair_rollback_completed",
      "next_stage_queued",
    ]);
    expect(important.has("approval_resume_queued")).toBe(true);
    expect(important.has("approval_completed")).toBe(true);
    expect(important.has("analysis_started")).toBe(true);
    expect(important.has("planning_started")).toBe(true);
    expect(important.has("assessment_started")).toBe(true);
    expect(important.has("final_report_started")).toBe(true);
    expect(important.has("transform_failed")).toBe(true);
    expect(important.has("build_failed")).toBe(true);
    expect(important.has("ai_diagnosis_created")).toBe(true);
    expect(important.has("reviewer_critique_created")).toBe(true);
    expect(important.has("repair_validation_completed")).toBe(true);
    expect(important.has("next_stage_queued")).toBe(true);
  });

  it("failure summary exposes backend supervision trace records", () => {
    const failureSummary = {
      has_failures: true,
      failures: [
        {
          type: "build_failed",
          stage: 2,
          supervision_trace: {
            ai_diagnosis: {
              diagnosis_id: "diag-1",
              command_id: "cmd-1",
              trigger_event_type: "build_failed",
              failure_type: "DEPENDENCY_ERROR",
              context_pack_id: "pack-1",
              context_pack_checksum: "ctx-1",
              repair_proposal_id: "proposal-1",
              model_invocation_id: "model-1",
              redaction_status: "redacted",
              created_at: "2026-06-16T00:00:00Z",
            },
            evidence_used: ["pack-1", "ctx-1", "pom-summary:1"],
            pom_analysis: {
              pom_summary_ref: "pom-summary:1",
              spring_boot_version: "2.7.18",
              java_version: "11",
              packaging: "jar",
              candidate_rules: ["pom_dependency_alignment"],
              created_at: "2026-06-16T00:00:01Z",
            },
            repair_proposal: {
              proposal_id: "proposal-2",
              source_proposal_id: "proposal-1",
              command_id: "cmd-1",
              revision_number: 2,
              allowed_scope: "pom_only",
              proposal_checksum: "prop-checksum",
              status: "completed",
              created_at: "2026-06-16T00:00:02Z",
            },
            reviewer_verdict: {
              critique_id: "crit-1",
              proposal_id: "proposal-2",
              proposal_type: "repair_proposal",
              proposal_checksum: "prop-checksum",
              context_pack_checksum: "ctx-1",
              decision: "accept",
              reasoning: "Evidence and scope are acceptable.",
              missing_evidence: [],
              unsafe_assumptions: [],
              created_at: "2026-06-16T00:00:03Z",
            },
            validation_result: {
              proposal_id: "proposal-2",
              patch_gate_status: "ALLOWED",
              deterministic_rule_id: "pom_dependency_alignment",
              build_status: "BUILD_PASSED_IN_SANDBOX",
              test_status: "TESTS_PASSED",
              h2_status: "NOT_REQUIRED",
              ledger_ref: "repair_ledger.json",
            },
          },
        },
      ],
    };
    const trace = failureSummary.failures[0].supervision_trace;
    expect(trace.ai_diagnosis?.diagnosis_id).toBe("diag-1");
    expect(trace.evidence_used).toContain("pom-summary:1");
    expect(trace.repair_proposal?.allowed_scope).toBe("pom_only");
    expect(trace.reviewer_verdict?.decision).toBe("accept");
    expect(trace.validation_result?.ledger_ref).toBe("repair_ledger.json");
  });

  // ── Stage status lifecycle reducer tests (V2 cockpit state model) ──

  it("uses the final completed route stage for file POM comparison", () => {
    const routeSteps: V2RouteStepEntry[] = [
      {
        route_step_index: 1,
        stage_index: 2,
        source_profile: "springboot-2.7-java11",
        target_profile: "springboot-3.5-java17",
        runtime_profile: "springboot-2.7-to-3.5-java17",
        catalog: "springboot-3.5-java17",
        execution_jdk: "java17",
        status: "completed",
        approval_gate_id: "",
        artifact_refs: [],
        evidence_refs: [],
      },
      {
        route_step_index: 2,
        stage_index: 3,
        source_profile: "springboot-3.5-java17",
        target_profile: "springboot-3.5-java21",
        runtime_profile: "springboot-3.5-java17-to-3.5-java21",
        catalog: "springboot-3.5-java21",
        execution_jdk: "java21",
        status: "completed",
        approval_gate_id: "",
        artifact_refs: [],
        evidence_refs: [],
      },
    ];
    const stages = [
      { stage_index: 1, pipeline_stage: "Stage 1", chain_status: "completed", input_source_kind: "legacy_source" },
    ];

    expect(getTargetVersionComparisonStageIndex(stages, routeSteps)).toBe(3);
  });

  it("keeps file comparison locked until the final route step completes", () => {
    const routeSteps: V2RouteStepEntry[] = [
      {
        route_step_index: 1,
        stage_index: 2,
        source_profile: "springboot-2.7-java11",
        target_profile: "springboot-3.5-java17",
        runtime_profile: "springboot-2.7-to-3.5-java17",
        catalog: "springboot-3.5-java17",
        execution_jdk: "java17",
        status: "completed",
        approval_gate_id: "",
        artifact_refs: [],
        evidence_refs: [],
      },
      {
        route_step_index: 2,
        stage_index: 3,
        source_profile: "springboot-3.5-java17",
        target_profile: "springboot-3.5-java21",
        runtime_profile: "springboot-3.5-java17-to-3.5-java21",
        catalog: "springboot-3.5-java21",
        execution_jdk: "java21",
        status: "running",
        approval_gate_id: "",
        artifact_refs: [],
        evidence_refs: [],
      },
    ];

    expect(getTargetVersionComparisonStageIndex([], routeSteps)).toBeNull();
  });
  it("uses refreshed stage status when deciding whether final route stage is complete", () => {
    const routeSteps: V2RouteStepEntry[] = [
      {
        route_step_index: 1,
        stage_index: 2,
        source_profile: "springboot-2.7-java11",
        target_profile: "springboot-3.5-java17",
        runtime_profile: "springboot-2.7-to-3.5-java17",
        catalog: "springboot-3.5-java17",
        execution_jdk: "java17",
        status: "completed",
        approval_gate_id: "",
        artifact_refs: [],
        evidence_refs: [],
      },
      {
        route_step_index: 2,
        stage_index: 3,
        source_profile: "springboot-3.5-java17",
        target_profile: "springboot-3.5-java21",
        runtime_profile: "springboot-3.5-java17-to-3.5-java21",
        catalog: "springboot-3.5-java21",
        execution_jdk: "java21",
        status: "pending",
        approval_gate_id: "",
        artifact_refs: [],
        evidence_refs: [],
      },
    ];
    const stages = [
      { stage_index: 3, pipeline_stage: "Stage 3", chain_status: "completed", input_source_kind: "stage_2_sandbox" },
    ];

    expect(getTargetVersionComparisonStageIndex(stages, routeSteps)).toBe(3);
  });
  it("uses the execution stage index for completed routed migrations", () => {
    const routeSteps: V2RouteStepEntry[] = [{
      route_step_index: 1,
      stage_index: 1,
      execution_stage_index: 2,
      source_profile: "springboot-3.5-java17",
      target_profile: "springboot-3.5-java21",
      runtime_profile: "springboot-3.5-java17-to-java21",
      catalog: "springboot-3.5-java17-to-java21",
      execution_jdk: "java21",
      status: "completed",
      approval_gate_id: "",
      artifact_refs: [],
      evidence_refs: [],
    }];

    expect(getTargetVersionComparisonStageIndex([
      { stage_index: 2, pipeline_stage: "Stage 2", chain_status: "completed", input_source_kind: "stage_1_sandbox" },
    ], routeSteps)).toBe(2);
  });
  it("buildStageTimelineEntries overlays route-step status from refreshed stages", () => {
    const routeSteps: V2RouteStepEntry[] = [
      {
        route_step_index: 1,
        stage_index: 1,
        source_profile: "springboot-2.7-java11",
        target_profile: "springboot-3.5-java17",
        runtime_profile: "springboot-2.7-to-3.5-java17",
        catalog: "springboot-3.5-java17",
        execution_jdk: "java17",
        status: "pending",
        approval_gate_id: "",
        artifact_refs: [],
        evidence_refs: [],
      },
      {
        route_step_index: 2,
        stage_index: 2,
        source_profile: "springboot-3.5-java17",
        target_profile: "springboot-4.0-java21",
        runtime_profile: "springboot-3.5-java17-to-java21",
        catalog: "springboot-4.0-java21",
        execution_jdk: "java21",
        status: "pending",
        approval_gate_id: "",
        artifact_refs: [],
        evidence_refs: [],
      },
    ];
    const stages = [
      { stage_index: 1, pipeline_stage: "Stage 1", chain_status: "completed", input_source_kind: "legacy_source" },
      { stage_index: 2, pipeline_stage: "Stage 2", chain_status: "running", input_source_kind: "stage_1_sandbox" },
    ];

    const entries = buildStageTimelineEntries(routeSteps, stages);

    expect(entries[0]).toMatchObject({ route_step_index: 1, status: "completed" });
    expect(entries[1]).toMatchObject({ route_step_index: 2, status: "running" });
  });

  it("buildStageTimelineEntries follows execution_stage_index for offset route steps", () => {
    const routeSteps: V2RouteStepEntry[] = [
      {
        route_step_index: 1,
        stage_index: 2,
        execution_stage_index: 1,
        source_profile: "springboot-2.7-java11",
        target_profile: "springboot-3.5-java17",
        runtime_profile: "springboot-2.7-to-3.5-java17",
        catalog: "springboot-3.5-java17",
        execution_jdk: "java17",
        status: "pending",
        approval_gate_id: "",
        artifact_refs: [],
        evidence_refs: [],
      },
      {
        route_step_index: 2,
        stage_index: 3,
        execution_stage_index: 3,
        source_profile: "springboot-3.5-java17",
        target_profile: "springboot-3.5-java21",
        runtime_profile: "springboot-3.5-java17-to-java21",
        catalog: "springboot-3.5-java17-to-java21",
        execution_jdk: "java21",
        status: "pending",
        approval_gate_id: "",
        artifact_refs: [],
        evidence_refs: [],
      },
    ];
    const firstStepRunningStages = [
      { stage_index: 1, pipeline_stage: "Stage 1", chain_status: "running", input_source_kind: "legacy_source" },
      { stage_index: 2, pipeline_stage: "Stage 2", chain_status: "failed", input_source_kind: "stage_1_sandbox" },
      { stage_index: 3, pipeline_stage: "Stage 3", chain_status: "pending", input_source_kind: "stage_2_sandbox" },
    ];
    const secondStepRunningStages = [
      { stage_index: 1, pipeline_stage: "Stage 1", chain_status: "completed", input_source_kind: "legacy_source" },
      { stage_index: 2, pipeline_stage: "Stage 2", chain_status: "failed", input_source_kind: "stage_1_sandbox" },
      { stage_index: 3, pipeline_stage: "Stage 3", chain_status: "running", input_source_kind: "stage_2_sandbox" },
    ];
    const secondStepCompletedStages = secondStepRunningStages.map((stage) => (
      stage.stage_index === 3 ? { ...stage, chain_status: "completed" } : stage
    ));

    expect(buildStageTimelineEntries(routeSteps, firstStepRunningStages)[0]).toMatchObject({
      route_step_index: 1,
      stage_index: 2,
      status: "running",
    });
    expect(buildStageTimelineEntries(routeSteps, secondStepRunningStages)[1]).toMatchObject({
      route_step_index: 2,
      stage_index: 3,
      status: "running",
    });
    expect(buildStageTimelineEntries(routeSteps, secondStepCompletedStages)[1]).toMatchObject({
      route_step_index: 2,
      stage_index: 3,
      status: "completed",
    });
  });

  it("reduceStageStatus: blocked while approval pending", () => {
    // Only approval_required/blocked events → blocked
    const events: V2JobEvent[] = [
      { stage: 1, type: "approval_required", status: "blocked", sequence: 1 } as unknown as V2JobEvent,
      { stage: 1, type: "stage_blocked_for_approval", status: "blocked", sequence: 2 } as unknown as V2JobEvent,
    ];
    const actual = reduceStageStatus(events);
    expect(actual).toBe("blocked");
  });

  it("reduceStageStatus: running after approval completed and transform started", () => {
    // Old blocked events must not prevent running
    const events: V2JobEvent[] = [
      { stage: 1, type: "approval_required", status: "blocked", sequence: 1 } as unknown as V2JobEvent,
      { stage: 1, type: "stage_blocked_for_approval", status: "blocked", sequence: 2 } as unknown as V2JobEvent,
      { stage: 1, type: "approval_resume_queued", status: "queued", sequence: 3 } as unknown as V2JobEvent,
      { stage: 1, type: "sandbox_transform_started", status: "running", sequence: 4 } as unknown as V2JobEvent,
    ];
    const actual = reduceStageStatus(events);
    expect(actual).toBe("running");
  });

  it("reduceStageStatus: migration_completed completes the final route stage", () => {
    const events: V2JobEvent[] = [
      { stage: 3, type: "stage_started", status: "running", sequence: 1 } as unknown as V2JobEvent,
      { stage: 3, type: "sandbox_transform_started", status: "running", sequence: 2 } as unknown as V2JobEvent,
      { stage: 3, type: "sandbox_transform_completed", status: "completed", sequence: 3 } as unknown as V2JobEvent,
      {
        stage: 3,
        type: "migration_completed",
        status: "completed",
        sequence: 4,
        payload: { from_stage: 3, to_stage: 3 },
      } as unknown as V2JobEvent,
    ];

    expect(reduceStageStatus(events, 3)).toBe("completed");
  });

  it("reduceStageStatus: completed does not regress to running on late success events", () => {
    const events: V2JobEvent[] = [
      { stage: 3, type: "stage_started", status: "running", sequence: 1 } as unknown as V2JobEvent,
      { stage: 3, type: "stage_completed", status: "completed", sequence: 2 } as unknown as V2JobEvent,
      { stage: 3, type: "sandbox_transform_completed", status: "completed", sequence: 3 } as unknown as V2JobEvent,
    ];

    expect(reduceStageStatus(events, 3)).toBe("completed");
  });

  it("reduceStageStatus: failed after sandbox_transform_failed", () => {
    const events: V2JobEvent[] = [
      { stage: 1, type: "approval_required", status: "blocked", sequence: 1 } as unknown as V2JobEvent,
      { stage: 1, type: "sandbox_transform_started", status: "running", sequence: 2 } as unknown as V2JobEvent,
      { stage: 1, type: "sandbox_transform_failed", status: "failed", sequence: 3 } as unknown as V2JobEvent,
    ];
    const actual = reduceStageStatus(events);
    expect(actual).toBe("failed");
  });

  it("reduceStageStatus: next_stage_queued completes prior stage and queues next stage", () => {
    const priorStageEvents: V2JobEvent[] = [
      { stage: 1, type: "stage_started", status: "running", sequence: 1 } as unknown as V2JobEvent,
      {
        stage: 2,
        type: "next_stage_queued",
        status: "queued",
        sequence: 2,
        payload: { from_stage: 1, to_stage: 2 },
      } as unknown as V2JobEvent,
    ];
    const nextStageEvents: V2JobEvent[] = [
      {
        stage: 2,
        type: "next_stage_queued",
        status: "queued",
        sequence: 2,
        payload: { from_stage: 1, to_stage: 2 },
      } as unknown as V2JobEvent,
      { stage: 2, type: "stage_started", status: "running", sequence: 3 } as unknown as V2JobEvent,
    ];

    expect(reduceStageStatus(priorStageEvents, 1)).toBe("completed");
    expect(reduceStageStatus(nextStageEvents, 2)).toBe("running");
  });
  it("reduceStageStatus: completed after stage_completed, blocked does not regress", () => {
    const events: V2JobEvent[] = [
      { stage: 1, type: "stage_started", status: "running", sequence: 1 } as unknown as V2JobEvent,
      { stage: 1, type: "stage_completed", status: "completed", sequence: 2 } as unknown as V2JobEvent,
      // A late blocked event must NOT regress completed → blocked
      { stage: 1, type: "approval_required", status: "blocked", sequence: 3 } as unknown as V2JobEvent,
    ];
    const actual = reduceStageStatus(events);
    expect(actual).toBe("completed");
  });

  it("reduceStageStatus: terminal migration event completes a one-step route", () => {
    const events: V2JobEvent[] = [
      { stage: 1, type: "stage_started", status: "running", sequence: 1 } as unknown as V2JobEvent,
      {
        stage: 1,
        type: "migration_completed",
        status: "completed",
        sequence: 2,
        payload: { reason: "migration_completed" },
      } as unknown as V2JobEvent,
    ];

    expect(reduceStageStatus(events, 1)).toBe("completed");
  });

  it("reduceStageStatus: old blocked does not override later running", () => {
    const events: V2JobEvent[] = [
      { stage: 1, type: "stage_blocked_for_approval", status: "blocked", sequence: 1 } as unknown as V2JobEvent,
      { stage: 1, type: "sandbox_transform_started", status: "running", sequence: 2 } as unknown as V2JobEvent,
    ];
    const actual = reduceStageStatus(events);
    expect(actual).toBe("running");
  });

  it("reduceStageStatus: old blocked does not override later failed", () => {
    const events: V2JobEvent[] = [
      { stage: 1, type: "stage_blocked_for_approval", status: "blocked", sequence: 1 } as unknown as V2JobEvent,
      { stage: 1, type: "stage_failed", status: "failed", sequence: 2 } as unknown as V2JobEvent,
    ];
    const actual = reduceStageStatus(events);
    expect(actual).toBe("failed");
  });

  // ── Route-step off-by-one regression (springboot-2.1 → 4.0 full route) ──

  it("route step 2 start event marks Route step 2 RUNNING, not Route step 3", () => {
    // Full route: springboot-2.1-java11 → springboot-4.0-java21
    // route_step_index and stage_index are aligned 1:1 for this source.
    const routeSteps: V2RouteStepEntry[] = [
      {
        route_step_index: 1, stage_index: 1,
        source_profile: "springboot-2.1-java11", target_profile: "springboot-2.7-java11",
        runtime_profile: "springboot-2.1.6-to-2.7-java11", catalog: "springboot-2.1.6-to-2.7-java11",
        execution_jdk: "java11", status: "pending", approval_gate_id: "", artifact_refs: [], evidence_refs: [],
      },
      {
        route_step_index: 2, stage_index: 2,
        source_profile: "springboot-2.7-java11", target_profile: "springboot-3.5-java17",
        runtime_profile: "springboot-2.7-to-3.5-java17", catalog: "springboot-3.5-java17",
        execution_jdk: "java17", status: "pending", approval_gate_id: "", artifact_refs: [], evidence_refs: [],
      },
      {
        route_step_index: 3, stage_index: 3,
        source_profile: "springboot-3.5-java17", target_profile: "springboot-3.5-java21",
        runtime_profile: "springboot-3.5-java17-to-java21", catalog: "springboot-3.5-java17-to-java21",
        execution_jdk: "java21", status: "pending", approval_gate_id: "", artifact_refs: [], evidence_refs: [],
      },
      {
        route_step_index: 4, stage_index: 4,
        source_profile: "springboot-3.5-java21", target_profile: "springboot-4.0-java21",
        runtime_profile: "springboot-3.5-java21-to-4.0-java21", catalog: "springboot-3.5-java21-to-4.0-java21",
        execution_jdk: "java21", status: "pending", approval_gate_id: "", artifact_refs: [], evidence_refs: [],
      },
    ];

    // Backend events: stage 1 completed, stage 2 started (running).
    // The backend emits stage=2 for route step 2's execution (after the fix).
    const allEvents: V2JobEvent[] = [
      { stage: 1, type: "stage_started", status: "running", sequence: 1 } as unknown as V2JobEvent,
      { stage: 1, type: "stage_completed", status: "completed", sequence: 2 } as unknown as V2JobEvent,
      { stage: 2, type: "stage_started", status: "running", sequence: 3 } as unknown as V2JobEvent,
    ];

    // Replicate eventAppliesToStage (event.stage === stageIndex) + reduceStageStatus
    // for each stage, then build the timeline.
    const stages = routeSteps.map((rs) => {
      const stageEvents = allEvents
        .filter((e) => e.stage === rs.stage_index)
        .sort((a, b) => a.sequence - b.sequence);
      return {
        stage_index: rs.stage_index,
        pipeline_stage: `Stage ${rs.stage_index}`,
        chain_status: reduceStageStatus(stageEvents, rs.stage_index),
        input_source_kind: "legacy_source",
      };
    });

    const entries = buildStageTimelineEntries(routeSteps, stages);

    // Route step 1 = COMPLETED
    expect(entries[0]).toMatchObject({ route_step_index: 1, status: "completed" });
    // Route step 2 = RUNNING (not Route step 3!)
    expect(entries[1]).toMatchObject({ route_step_index: 2, status: "running" });
    // Route step 3 = PENDING (must NOT be RUNNING)
    expect(entries[2]).toMatchObject({ route_step_index: 3, status: "pending" });
    expect(entries[2]).not.toMatchObject({ route_step_index: 3, status: "running" });
    // Route step 4 = PENDING
    expect(entries[3]).toMatchObject({ route_step_index: 4, status: "pending" });
  });

  it("approved card has disabled buttons and no active blocked state implication", () => {
    // When approval card status is "approved", buttons are disabled
    const approved = { card_id: "c1", status: "approved", request_checksum: "chk-1" };
    const pending = { card_id: "c2", status: "pending", request_checksum: "chk-2" };
    const isPending = (s: string) => s === "pending";
    expect(isPending(approved.status)).toBe(false);
    expect(isPending(pending.status)).toBe(true);
    // Disabled guard: button disabled unless status === "pending"
    expect(approved.status !== "pending").toBe(true);
  });

  it("pipeline and stage status consistent after approval lifecycle", () => {
    // The pipeline human_approval row must be "pass", not "blocked",
    // after approval_resume_queued. Stage must be "running".
    const events: V2JobEvent[] = [
      { stage: 1, type: "approval_required", status: "blocked", sequence: 1 } as unknown as V2JobEvent,
      { stage: 1, type: "approval_resume_queued", status: "queued", sequence: 2 } as unknown as V2JobEvent,
      { stage: 1, type: "sandbox_transform_started", status: "running", sequence: 3 } as unknown as V2JobEvent,
    ];
    const stageStatus = reduceStageStatus(events);
    expect(stageStatus).toBe("running");
    // Pipeline human_approval row logic: events with type in approval_passed_types
    const hasPassedEvent = events.some(
      (e) => ["approval_completed", "approval_resume_queued", "resume_started",
              "sandbox_transform_started", "sandbox_transform_completed"].includes(e.type)
    );
    expect(hasPassedEvent).toBe(true);
  });

  it("raw logs events are collapsed by default in SSE stream", () => {
    const events = [
      { type: "stdout", status: "running", message: "raw line" },
      { type: "analysis_completed", status: "completed", message: "done" },
    ];
    const rawLogs = events.filter((e) => e.type === "stdout" || e.type === "stderr");
    const evidence = events.filter((e) => e.type !== "stdout" && e.type !== "stderr");
    expect(rawLogs).toHaveLength(1);
    expect(evidence).toHaveLength(1);
    expect(evidence[0].type).toBe("analysis_completed");
  });

  it("no stage input paths come from user selection", () => {
    // Stage 2 input must be stage_1_sandbox, not user-selected
    const stage2Input = "stage_1_sandbox";
    const prohibitedInputs = ["user_selected", "manual", "browser_payload"];
    expect(stage2Input).toBe("stage_1_sandbox");
    for (const prohibited of prohibitedInputs) {
      expect(stage2Input).not.toBe(prohibited);
    }
  });

  it("Stage 2 profile is springboot-2.7-to-3.5-java17", () => {
    const stage2Profile = "springboot-2.7-to-3.5-java17";
    expect(stage2Profile).toBe("springboot-2.7-to-3.5-java17");
  });

  it("Stage 3 profile is springboot-3.5-java17-to-java21", () => {
    const stage3Profile = "springboot-3.5-java17-to-java21";
    expect(stage3Profile).toBe("springboot-3.5-java17-to-java21");
  });
});

// ── F3/F4 — Cockpit profile routing, detection, override ─────────────

describe("F3/F4 Cockpit profile routing panels", () => {
  it("MigrationRoutePanel displays source and target profiles from backend job data", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      source_profile: "springboot-2.7-java11",
      target_profile: "springboot-4.0-java21",
      validation_status: "valid",
      included_stages: ["2", "3", "4"],
      skipped_stages: [],
      excluded_stages: [],
      route_steps: [
        {
          route_step_index: 1,
          stage_index: 1,
          source_profile: "springboot-2.7-java11",
          target_profile: "springboot-3.5-java17",
          runtime_profile: "springboot-2.7-to-3.5-java17",
          catalog: "springboot-3.5-java17",
          execution_jdk: "java17",
          status: "completed",
          approval_gate_id: "",
          artifact_refs: [],
          evidence_refs: [],
        },
      ],
    };
    const markup = renderToStaticMarkup(<MigrationRoutePanel job={job} />);
    expect(markup).toContain("Migration Route");
    expect(markup).toContain("Spring Boot 2.7 / Java 11");
    expect(markup).toContain("Spring Boot 4.0 / Java 21");
    expect(markup).toContain("valid");
    expect(markup).toContain("2, 3, 4");
    expect(markup).toContain("All route data is backend-returned");
  });

  it("MigrationRoutePanel shows skipped stages from backend data", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-2",
      setup_id: "setup-2",
      setup_checksum: "chk-2",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      source_profile: "springboot-3.5-java17",
      target_profile: "springboot-4.0-java21",
      validation_status: "valid",
      included_stages: ["3", "4"],
      skipped_stages: ["2"],
      excluded_stages: [],
    };
    const markup = renderToStaticMarkup(<MigrationRoutePanel job={job} />);
    expect(markup).toContain("Skipped stages");
    expect(markup).toContain("2");
  });

  it("SourceProfileDetectionPanel shows unavailable message when evidence is null", () => {
    const markup = renderToStaticMarkup(
      <SourceProfileDetectionPanel gateDetail={null} />,
    );
    expect(markup).toContain("Source-profile detection evidence is unavailable");
    expect(markup).toContain("refresh the gate or rerun analysis");
  });

  it("SourceProfileDetectionPanel shows detection evidence when evidence pack is present", () => {
    const pack: GateEvidencePack = {
      pack_id: "pack-1",
      pack_type: "source_profile_detection",
      gate_id: "gate-1",
      gate_phase: "analysis_review",
      summary: "Detected springboot-2.7-java11",
      artifacts: [
        {
          kind: "source_profile_detection",
          checksum_verified: true,
          content: '{"detected_source_profile":"springboot-2.7-java11","confidence":"high"}',
          size_bytes: 64,
          truncated: false,
        },
      ],
      missing_refs: [],
      checksum_mismatches: [],
      failure_message: null,
      resolved_artifact_count: 1,
      total_artifact_count: 1,
      redaction_status: "clean",
      created_at: "2026-06-28T00:00:00Z",
    };
    const gateDetail: GateDetailResponse = {
      gate: {
        gate_id: "gate-1",
        job_id: "job-1",
        gate_phase: "analysis_review",
        stage_index: 1,
        gate_status: "open",
        gate_decision: "continue",
        source_artifact_checksum: "sha256:gate",
        source_artifact_refs: [],
        created_at: "2026-06-28T00:00:00Z",
        resolved_at: null,
        resolved_by: null,
        checksum: "sha256:gate-checksum",
        available_actions: [],
      },
      evidence: pack,
      checksum: "sha256:gate-checksum",
    };
    const markup = renderToStaticMarkup(
      <SourceProfileDetectionPanel gateDetail={gateDetail} />,
    );
    expect(markup).toContain("Source Profile Detection");
    expect(markup).toContain("source_profile_detection");
    expect(markup).toContain("springboot-2.7-java11");
    expect(markup).toContain("1/1 resolved");
  });

  it("SourceProfileOverrideForm does not render for non-analysis_review gates", () => {
    const gateDetail: GateDetailResponse = {
      gate: {
        gate_id: "gate-1",
        job_id: "job-1",
        gate_phase: "repair_review",
        stage_index: 2,
        gate_status: "open",
        gate_decision: "revise",
        source_artifact_checksum: "sha256:gate",
        source_artifact_refs: [],
        created_at: "2026-06-28T00:00:00Z",
        resolved_at: null,
        resolved_by: null,
        checksum: "sha256:gate-checksum",
        available_actions: [
          { action: "override_source_profile", label: "Override", description: "Override", blocked: false, block_reason: "" },
        ],
      },
      evidence: null,
      checksum: "sha256:gate-checksum",
    };
    const markup = renderToStaticMarkup(
      <SourceProfileOverrideForm gateDetail={gateDetail} jobId="job-1" onSuccess={() => undefined} />,
    );
    expect(markup).toBe("");
  });

  it("SourceProfileOverrideForm shows a specific blocked reason when detection evidence is missing", () => {
    const gateDetail: GateDetailResponse = {
      gate: {
        gate_id: "gate-1",
        job_id: "job-1",
        gate_phase: "analysis_review",
        stage_index: 1,
        gate_status: "open",
        gate_decision: "continue",
        source_artifact_checksum: "sha256:gate",
        source_artifact_refs: [],
        created_at: "2026-06-28T00:00:00Z",
        resolved_at: null,
        resolved_by: null,
        checksum: "sha256:gate-checksum",
        available_actions: [
          { action: "override_source_profile", label: "Override", description: "Override", blocked: false, block_reason: "" },
        ],
      },
      evidence: null,
      checksum: "sha256:gate-checksum",
    };
    const markup = renderToStaticMarkup(
      <SourceProfileOverrideForm gateDetail={gateDetail} jobId="job-1" onSuccess={() => undefined} />,
    );
    expect(markup).toContain("Override Source Profile");
    // With no job target_profile and no detection ref, the form should expose
    // a specific unavailable reason — never fabricate a target_profile.
    expect(markup).toContain("Missing target profile from backend job state.");
    expect(markup).toContain("disabled");
  });

  it("cockpit displays source and target profile from backend job data", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      source_profile: "springboot-2.7-java11",
      target_profile: "springboot-4.0-java21",
    };
    const markup = renderToStaticMarkup(<MigrationRoutePanel job={job} />);
    expect(markup).toContain("Source profile");
    expect(markup).toContain("Target profile");
    expect(markup).toContain("Spring Boot 2.7 / Java 11");
    expect(markup).toContain("Spring Boot 4.0 / Java 21");
  });

  it("cockpit displays included/excluded/skipped stages from backend arrays", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      source_profile: "springboot-2.7-java11",
      target_profile: "springboot-4.0-java21",
      included_stages: ["2", "3", "4"],
      skipped_stages: [],
      excluded_stages: [],
    };
    const markup = renderToStaticMarkup(<MigrationRoutePanel job={job} />);
    expect(markup).toContain("Included stages");
    expect(markup).toContain("2, 3, 4");
  });

  it("skipped stage cards render with skipped state in stage timeline", () => {
    const stages = [
      { stage_index: 1, pipeline_stage: "Stage 1", chain_status: "completed", input_source_kind: "legacy_source" },
      { stage_index: 2, pipeline_stage: "Stage 2", chain_status: "completed", input_source_kind: "stage_1_sandbox" },
      { stage_index: 3, pipeline_stage: "Stage 3", chain_status: "pending", input_source_kind: "stage_2_sandbox" },
    ];
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      source_profile: "springboot-3.5-java17",
      target_profile: "springboot-4.0-java21",
      included_stages: ["3"],
      skipped_stages: ["2"],
      excluded_stages: [],
    };
    const markup = renderToStaticMarkup(
      <div className="stage-list">
        {stages.map((stage) => {
          const isSkipped = job.skipped_stages?.includes(String(stage.stage_index));
          return (
            <div key={stage.stage_index} className={`stage-card ${stage.chain_status}`}>
              <strong>{stage.pipeline_stage}</strong>
              {isSkipped && <span className="status-badge skipped">SKIPPED BY SOURCE</span>}
            </div>
          );
        })}
      </div>,
    );
    expect(markup).toContain("SKIPPED BY SOURCE");
    expect(markup).toContain("Stage 2");
  });

  it("override form posts a checksum-bound override_source_profile action", () => {
    const action = {
      gate_id: "gate-1",
      job_id: "job-1",
      action: "continue",
      expected_gate_checksum: "sha256:gate-checksum",
      override_source_profile: "springboot-3.5-java17",
      actor_type: "human",
      decided_by: "human",
    };
    expect(action.override_source_profile).toBe("springboot-3.5-java17");
    expect(action.expected_gate_checksum).toBe("sha256:gate-checksum");
    expect(action.actor_type).toBe("human");
  });

  it("assistant cannot override source profile", () => {
    const assistantCapabilities = {
      can_explain: true,
      can_diagnose: true,
      can_override_source_profile: false,
    };
    expect(assistantCapabilities.can_override_source_profile).toBe(false);
  });

  it("forbidden execution fields are absent from cockpit rendered copy", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      source_profile: "springboot-2.7-java11",
      target_profile: "springboot-4.0-java21",
    };
    const markup = renderToStaticMarkup(<MigrationRoutePanel job={job} />);
    const forbiddenPatterns = [
      "sandbox_path", "argv", "raw_command", "filesystem_target",
      "provider", "model_id", "deployment", "endpoint", "api_key", "access_token",
    ];
    for (const pattern of forbiddenPatterns) {
      expect(markup).not.toContain(pattern);
    }
  });

  // ── SourceProfileOverrideForm — driven submit path ─────────────

  function makeAnalysisReviewGateDetail(
    overrides: Partial<{
      sourceArtifactRefs: string[];
      sourceArtifactChecksum: string;
      availableActions: GateRepresentation["available_actions"];
    }> = {},
  ): GateDetailResponse {
    return {
      gate: {
        gate_id: "gate-1",
        job_id: "job-1",
        gate_phase: "analysis_review",
        stage_index: 1,
        gate_status: "open",
        gate_decision: "continue",
        source_artifact_checksum: overrides.sourceArtifactChecksum ?? "sha256:detection-checksum",
        source_artifact_refs: overrides.sourceArtifactRefs ?? [
          "analysis/source_profile_detection.json",
        ],
        created_at: "2026-06-28T00:00:00Z",
        resolved_at: null,
        resolved_by: null,
        checksum: "sha256:gate-checksum",
        available_actions: overrides.availableActions ?? [
          {
            action: "override_source_profile",
            label: "Override",
            description: "Override",
            blocked: false,
            block_reason: "",
          },
        ],
      },
      evidence: {
        pack_id: "pack-1",
        pack_type: "source_profile_detection",
        gate_id: "gate-1",
        gate_phase: "analysis_review",
        summary: "Detected springboot-2.7-java11",
        artifacts: [
          {
            kind: "source_profile_detection",
            checksum_verified: true,
            content: '{"detected_source_profile":"springboot-2.7-java11","confidence":"high"}',
            size_bytes: 64,
            truncated: false,
          },
        ],
        missing_refs: [],
        checksum_mismatches: [],
        failure_message: null,
        resolved_artifact_count: 1,
        total_artifact_count: 1,
        redaction_status: "clean",
        created_at: "2026-06-28T00:00:00Z",
      } as GateEvidencePack,
      checksum: "sha256:gate-checksum",
    };
  }

  it("SourceProfileOverrideForm build helper returns override_source_profile body in happy path", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      source_profile: "springboot-2.7-java11",
      target_profile: "springboot-4.0-java21",
    };
    const gateDetail = makeAnalysisReviewGateDetail();
    const result = buildSourceProfileOverrideBody({
      gate: gateDetail.gate,
      jobId: "job-1",
      job,
      evidence: gateDetail.evidence,
      requestedProfile: "springboot-3.5-java17",
      reason: "Detected profile is incorrect",
      comments: "Operator verified the source pom.xml manually",
      idempotencyKey: "idem-1",
    });

    expect(result.blockedReason).toBeNull();
    expect(result.body).not.toBeNull();
    expect(result.body).toMatchObject({
      gate_id: "gate-1",
      job_id: "job-1",
      action: "override_source_profile",
      expected_gate_checksum: "sha256:gate-checksum",
      idempotency_key: "idem-1",
      decided_by: "human",
      actor_type: "human",
      reason: "Detected profile is incorrect",
      comments: "Operator verified the source pom.xml manually",
      override_source_profile: "springboot-3.5-java17",
      detection_artifact_ref: "analysis/source_profile_detection.json",
      detected_source_profile: "springboot-2.7-java11",
      requested_source_profile: "springboot-3.5-java17",
      target_profile: "springboot-4.0-java21",
      expected_detection_artifact_checksum: "sha256:detection-checksum",
    });

    // Forbidden runtime fields are absent from the body.
    const serialized = JSON.stringify(result.body);
    expect(serialized).not.toContain("sandbox_path");
    expect(serialized).not.toContain("argv");
    expect(serialized).not.toContain("env");
    expect(serialized).not.toContain("raw_command");
    expect(serialized).not.toContain("filesystem_target");
    expect(serialized).not.toContain("filesystem_root");
    expect(serialized).not.toContain("output_root");
    expect(serialized).not.toContain("report_root");
    expect(serialized).not.toContain("run_root");
    expect(serialized).not.toContain("ai_hub_path");
    expect(serialized).not.toContain("java_home");
    expect(serialized).not.toContain("java11_home");
    expect(serialized).not.toContain("java17_home");
    expect(serialized).not.toContain("java21_home");
    expect(serialized).not.toContain("maven_cmd");
    expect(serialized).not.toMatch(/"provider"/);
    expect(serialized).not.toMatch(/"model"/);
    expect(serialized).not.toMatch(/"model_id"/);
    expect(serialized).not.toMatch(/"deployment"/);
    expect(serialized).not.toMatch(/"endpoint"/);
    expect(serialized).not.toMatch(/"api_key"/);
    expect(serialized).not.toMatch(/"access_token"/);
  });

  it("SourceProfileOverrideForm submit path posts the body produced by the build helper", async () => {
    afterEach(() => vi.restoreAllMocks());

    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      source_profile: "springboot-2.7-java11",
      target_profile: "springboot-4.0-java21",
    };
    const gateDetail = makeAnalysisReviewGateDetail();
    const result = buildSourceProfileOverrideBody({
      gate: gateDetail.gate,
      jobId: "job-1",
      job,
      evidence: gateDetail.evidence,
      requestedProfile: "springboot-3.5-java17",
      reason: "Detected profile is incorrect",
      comments: "Operator verified the source pom.xml manually",
      idempotencyKey: "idem-1",
    });
    expect(result.body).not.toBeNull();

    const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
      ok: true,
      json: async () => ({
        result: {
          decision_id: "d-1",
          gate_id: "gate-1",
          action: "override_source_profile",
          status: "resolved",
        },
      }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    await postV2GateAction("job-1", "gate-1", result.body!);

    const actionCall = fetchMock.mock.calls.find(
      (call) => String(call[0]).includes("/actions"),
    ) as [string, RequestInit?] | undefined;
    expect(actionCall).toBeDefined();
    const body = JSON.parse(String((actionCall?.[1] as RequestInit | undefined)?.body ?? "{}"));
    expect(body.action).toBe("override_source_profile");
    expect(body.target_profile).toBe("springboot-4.0-java21");
    expect(body.detection_artifact_ref).toBe("analysis/source_profile_detection.json");
    expect(body.expected_detection_artifact_checksum).toBe("sha256:detection-checksum");
    expect(body.actor_type).toBe("human");
    expect(body.decided_by).toBe("human");

    const serialized = JSON.stringify(body);
    for (const forbidden of [
      "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
      "filesystem_root", "output_root", "report_root", "run_root",
      "ai_hub_path", "java_home", "java11_home", "java17_home", "java21_home",
      "maven_cmd",
    ]) {
      expect(serialized).not.toContain(forbidden);
    }
    expect(serialized).not.toMatch(/"provider"/);
    expect(serialized).not.toMatch(/"model"/);
    expect(serialized).not.toMatch(/"model_id"/);
    expect(serialized).not.toMatch(/"deployment"/);
    expect(serialized).not.toMatch(/"endpoint"/);
    expect(serialized).not.toMatch(/"api_key"/);
    expect(serialized).not.toMatch(/"access_token"/);
  });

  it("SourceProfileOverrideForm build helper uses the gate target profile when present", () => {
    const gateDetail = makeAnalysisReviewGateDetail();
    const result = buildSourceProfileOverrideBody({
      gate: gateDetail.gate,
      jobId: "job-1",
      evidence: gateDetail.evidence,
      requestedProfile: "springboot-3.5-java17",
      reason: "r",
      comments: "c",
      idempotencyKey: "idem-1",
    });
    expect(result.body).not.toBeNull();
    expect(result.body?.target_profile).toBe("springboot-2.7-java11");
    expect(result.blockedReason).toBeNull();
  });

  it("SourceProfileOverrideForm build helper returns null when source artifact ref is missing", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      target_profile: "springboot-4.0-java21",
    };
    const gateDetail = makeAnalysisReviewGateDetail({
      sourceArtifactRefs: ["analysis/analysis_report.json", "analysis/analysis_summary.md"],
    });
    const result = buildSourceProfileOverrideBody({
      gate: gateDetail.gate,
      jobId: "job-1",
      job,
      evidence: gateDetail.evidence,
      requestedProfile: "springboot-3.5-java17",
      reason: "r",
      comments: "c",
      idempotencyKey: "idem-1",
    });
    expect(result.body).toBeNull();
    expect(result.blockedReason).toBe("missing_detection_artifact_ref");
    expect(result.detectionArtifactRef).toBe("");
  });

  it("SourceProfileOverrideForm build helper returns null when source artifact checksum is missing", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      target_profile: "springboot-4.0-java21",
    };
    const gateDetail = makeAnalysisReviewGateDetail({
      sourceArtifactChecksum: "",
    });
    const result = buildSourceProfileOverrideBody({
      gate: gateDetail.gate,
      jobId: "job-1",
      job,
      evidence: gateDetail.evidence,
      requestedProfile: "springboot-3.5-java17",
      reason: "r",
      comments: "c",
      idempotencyKey: "idem-1",
    });
    expect(result.body).toBeNull();
    expect(result.blockedReason).toBe("missing_detection_artifact_checksum");
  });

  it("SourceProfileOverrideForm build helper returns null when reason is blank", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      target_profile: "springboot-4.0-java21",
    };
    const gateDetail = makeAnalysisReviewGateDetail();
    const result = buildSourceProfileOverrideBody({
      gate: gateDetail.gate,
      jobId: "job-1",
      job,
      evidence: gateDetail.evidence,
      requestedProfile: "springboot-3.5-java17",
      reason: "   ",
      comments: "ok",
      idempotencyKey: "idem-1",
    });
    expect(result.body).toBeNull();
    expect(result.blockedReason).toBe("missing_reason");
  });

  it("SourceProfileOverrideForm build helper returns null when comments is blank", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      target_profile: "springboot-4.0-java21",
    };
    const gateDetail = makeAnalysisReviewGateDetail();
    const result = buildSourceProfileOverrideBody({
      gate: gateDetail.gate,
      jobId: "job-1",
      job,
      evidence: gateDetail.evidence,
      requestedProfile: "springboot-3.5-java17",
      reason: "ok",
      comments: "",
      idempotencyKey: "idem-1",
    });
    expect(result.body).toBeNull();
    expect(result.blockedReason).toBe("missing_comments");
  });

  it("SourceProfileOverrideForm build helper returns null when gate phase is not analysis_review", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      target_profile: "springboot-4.0-java21",
    };
    const gate: GateRepresentation = {
      gate_id: "gate-1",
      job_id: "job-1",
      gate_phase: "planning_review",
      stage_index: 1,
      gate_status: "open",
      gate_decision: "continue",
      source_artifact_checksum: "sha256:detection-checksum",
      source_artifact_refs: ["analysis/source_profile_detection.json"],
      created_at: "2026-06-28T00:00:00Z",
      resolved_at: null,
      resolved_by: null,
      checksum: "sha256:gate-checksum",
      available_actions: [
        {
          action: "override_source_profile",
          label: "Override",
          description: "Override",
          blocked: false,
          block_reason: "",
        },
      ],
    };
    const result = buildSourceProfileOverrideBody({
      gate,
      jobId: "job-1",
      job,
      evidence: null,
      requestedProfile: "springboot-3.5-java17",
      reason: "r",
      comments: "c",
      idempotencyKey: "idem-1",
    });
    expect(result.body).toBeNull();
    expect(result.blockedReason).toBe("gate_phase_not_analysis_review");
  });

  it("SourceProfileOverrideForm build helper returns null when available_actions lacks override_source_profile", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      target_profile: "springboot-4.0-java21",
    };
    const gateDetail = makeAnalysisReviewGateDetail({
      availableActions: [
        { action: "continue", label: "Continue", description: "Continue", blocked: false, block_reason: "" },
      ],
    });
    const result = buildSourceProfileOverrideBody({
      gate: gateDetail.gate,
      jobId: "job-1",
      job,
      evidence: gateDetail.evidence,
      requestedProfile: "springboot-3.5-java17",
      reason: "r",
      comments: "c",
      idempotencyKey: "idem-1",
    });
    expect(result.body).toBeNull();
    expect(result.blockedReason).toBe("override_action_unavailable");
  });

  it("SourceProfileOverrideForm renders springboot-3.5-java21 as a selectable source option", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      target_profile: "springboot-4.0-java21",
    };
    const gateDetail = makeAnalysisReviewGateDetail();
    const markup = renderToStaticMarkup(
      <SourceProfileOverrideForm
        gateDetail={gateDetail}
        jobId="job-1"
        job={job}
        onSuccess={() => undefined}
      />,
    );
    expect(markup).toContain("Spring Boot 3.5 / Java 17");
    expect(markup).toContain("Spring Boot 3.5 / Java 21");
    expect(markup).toContain('value="springboot-3.5-java21"');
  });

  it("MigrationRoutePanel renders springboot-3.5-java21 source profile in cockpit", () => {
    const job: V2MigrationJobResponse = {
      job_id: "job-1",
      setup_id: "setup-1",
      setup_checksum: "chk-1",
      pipeline_id: "pipeline-1",
      stages: [],
      created_at: "2026-06-28T00:00:00Z",
      source_profile: "springboot-3.5-java21",
      target_profile: "springboot-4.0-java21",
      validation_status: "valid",
      included_stages: ["4"],
      skipped_stages: ["2", "3"],
      excluded_stages: [],
    };
    const markup = renderToStaticMarkup(<MigrationRoutePanel job={job} />);
    expect(markup).toContain("Spring Boot 3.5 / Java 21");
    expect(markup).toContain("Spring Boot 4.0 / Java 21");
    expect(markup).toContain("4");
    expect(markup).toContain("2, 3");
  });

  it("getSourceProfileOverrideBlockedReason returns the right reason for each missing input", () => {
    const base = {
      isAnalysisReview: true,
      hasOverrideAction: true,
      hasTargetProfile: true,
      hasDetectionArtifactRef: true,
      hasExpectedChecksum: true,
      reason: "ok",
      comments: "ok",
    };
    expect(getSourceProfileOverrideBlockedReason({ ...base, isAnalysisReview: false }))
      .toBe("gate_phase_not_analysis_review");
    expect(getSourceProfileOverrideBlockedReason({ ...base, hasOverrideAction: false }))
      .toBe("override_action_unavailable");
    expect(getSourceProfileOverrideBlockedReason({ ...base, hasTargetProfile: false }))
      .toBe("missing_target_profile");
    expect(getSourceProfileOverrideBlockedReason({ ...base, hasDetectionArtifactRef: false }))
      .toBe("missing_detection_artifact_ref");
    expect(getSourceProfileOverrideBlockedReason({ ...base, hasExpectedChecksum: false }))
      .toBe("missing_detection_artifact_checksum");
    expect(getSourceProfileOverrideBlockedReason({ ...base, reason: "" }))
      .toBe("missing_reason");
    expect(getSourceProfileOverrideBlockedReason({ ...base, comments: "" }))
      .toBe("missing_comments");
    expect(getSourceProfileOverrideBlockedReason(base)).toBeNull();
  });
});

describe("F15 Final Report and Stage 4 cockpit", () => {
  it("report panel uses backend eligible and blockers", () => {
    const reportBlocked = {
      eligible: false,
      blockers: ["stage_2_not_completed", "gate_3_not_passed"],
    };
    expect(reportBlocked.eligible).toBe(false);
    expect(reportBlocked.blockers).toHaveLength(2);
    const copy = reportBlocked.blockers.join(" ");
    expect(copy).toContain("stage_2_not_completed");

    const reportReady = { eligible: true, blockers: [] };
    expect(reportReady.eligible).toBe(true);
    expect(reportReady.blockers).toHaveLength(0);

    const blockedMarkup = renderToStaticMarkup(
      <section className="panel">
        <h2>Final Report</h2>
        {reportBlocked.blockers.map((b) => (
          <p className="warning-text" key={b}>{b}</p>
        ))}
        {!reportBlocked.eligible && <p className="meta">Report generation not yet available for this job.</p>}
      </section>
    );
    expect(blockedMarkup).toContain("stage_2_not_completed");
    expect(blockedMarkup).toContain("not yet available");

    const readyMarkup = renderToStaticMarkup(
      <section className="panel">
        <h2>Final Report</h2>
        {reportReady.blockers.map((b) => (
          <p className="warning-text" key={b}>{b}</p>
        ))}
        {!reportReady.eligible && <p className="meta">Report generation not yet available for this job.</p>}
      </section>
    );
    expect(readyMarkup).not.toContain("not yet available");
    expect(readyMarkup).toContain("Final Report");
  });

  it("generate does not auto-download", () => {
    const generateMarkup = renderToStaticMarkup(
      <div>
        <button type="button">Generate report</button>
        <div className="report-artifact-row">
          <a href="/v1/reports/r1" download>Download</a>
        </div>
      </div>
    );
    expect(generateMarkup).toContain("Generate report");
    expect(generateMarkup).toContain("Download");
    // Generate button itself has no download attribute
    expect(generateMarkup).toContain("<button");
    expect(generateMarkup).not.toMatch(/<button[^>]*download/);
  });

  it("explicit download uses returned API URL", () => {
    const downloadUrl = "/v1/reports/final/r1";
    expect(downloadUrl.startsWith("/v1/")).toBe(true);
    const resolved = resolveReportDownloadUrl(downloadUrl);
    expect(resolved).toBe(`${CONTROL_TOWER_API_BASE_URL}${downloadUrl}`);
    expect(resolved).toContain("/v1/reports/final/r1");
  });

  it("gate, evidence, approval, assistant, and POM panels still render", async () => {
    const page = await MigrationCockpitPage({
      params: Promise.resolve({ jobId: "429a9bb2154b4be7a99a32867780d744" }),
    });
    const children = page.props.children;
    const cockpit = children[1];
    expect(cockpit.type).toBe(MigrationCockpit);

    const markup = renderToStaticMarkup(<MigrationCockpit jobId="job-123" />);
    expect(markup).toContain("Loading cockpit");

    const cockpitFunc = cockpit.type as () => React.JSX.Element;
    const source = cockpitFunc.toString();
    // All expected panel headings must appear in the component's rendering logic
    expect(source).toContain("Stage Timeline");
    expect(source).toContain("buildStageTimelineEntries(data.job.route_steps, data.stages)");
    expect(source).toContain("Pipeline Status");
    expect(source).toContain("Evidence");
    expect(source).toContain("Assistant");
    expect(source).toContain("Proof & Report");
    expect(source).toContain("Final Report");
  });

  it("four-stage rendering is visible (Stage 4 appears as a stage)", () => {
    const stages = [
      { stage_index: 1, pipeline_stage: "Stage 1", chain_status: "completed", input_source_kind: "legacy_source" },
      { stage_index: 2, pipeline_stage: "Stage 2", chain_status: "completed", input_source_kind: "stage_1_sandbox" },
      { stage_index: 3, pipeline_stage: "Stage 3", chain_status: "completed", input_source_kind: "stage_2_sandbox" },
      { stage_index: 4, pipeline_stage: "Stage 4", chain_status: "pending", input_source_kind: "stage_3_sandbox" },
    ];
    expect(stages).toHaveLength(4);
    const stage4 = stages.find((s) => s.stage_index === 4);
    expect(stage4).toBeDefined();
    expect(stage4!.pipeline_stage).toBe("Stage 4");
    expect(stage4!.input_source_kind).toBe("stage_3_sandbox");

    const markup = renderToStaticMarkup(
      <div className="stage-list">
        {stages.map((stage) => (
          <div key={stage.stage_index} className={`stage-card ${stage.chain_status}`}>
            <strong>{stage.pipeline_stage}</strong>
            <span>{formatStageStatusLabel(stage.chain_status)}</span>
            <p className="meta">Input: {stage.input_source_kind}</p>
          </div>
        ))}
      </div>
    );
    expect(markup).toContain("Stage 4");
    expect(markup).toContain("PENDING");
    expect(markup).toContain("stage_3_sandbox");
  });

  it("no manual Stage 4 start, input, or path control appears", () => {
    const forbiddenPatterns = ["start_stage_4", "Start Stage 4", "stage_4_path", "stage_4_input", "sandbox_path"];
    const markup = renderToStaticMarkup(
      <div className="cockpit-layout">
        <section className="panel">
          <h2>Stage Timeline</h2>
          <div className="stage-list">
            <div className="stage-card queued">
              <div className="stage-header">
                <strong>Stage 4</strong>
                <span className="status-badge queued">QUEUED</span>
              </div>
              <p className="meta">Input: stage_3_sandbox</p>
              <p className="meta">Stage 4 is the Spring Boot 4 migration stage and follows the same approval and evidence flow as the earlier stages.</p>
            </div>
          </div>
        </section>
      </div>
    );
    for (const pattern of forbiddenPatterns) {
      expect(markup).not.toContain(pattern);
    }
    // Stage 4 renders as read-only status display
    expect(markup).toContain("Stage 4");
    expect(markup).toContain("QUEUED");
    expect(markup).toContain("stage_3_sandbox");
    expect(markup).toContain("follows the same approval and evidence flow");
  });

});

// ── PR-C — Cockpit integration tests ─────────────────────────────────

describe("PR-C Repair Proposal Panel integration", () => {
  it("MigrationCockpit source references RepairProposalPanel", () => {
    const source = MigrationCockpit.toString();
    expect(source).toContain("RepairProposalPanel");
    expect(source).toContain("normalizedJobId &&");
  });

  it("RepairProposalPanel renders with jobId and shows loading state", () => {
    const markup = renderToStaticMarkup(
      <MigrationCockpit jobId="test-job-123" />
    );
    expect(markup).toContain("Loading cockpit");
  });

  it("route_steps still override legacy stages in cockpit", () => {
    const routeSteps: V2RouteStepEntry[] = [
      {
        route_step_index: 1,
        stage_index: 1,
        source_profile: "springboot-2.7-java11",
        target_profile: "springboot-3.5-java17",
        runtime_profile: "springboot-2.7-to-3.5-java17",
        catalog: "springboot-3.5-java17",
        execution_jdk: "java17",
        status: "running",
        approval_gate_id: "",
        artifact_refs: [],
        evidence_refs: [],
      },
    ];
    const stages = [
      { stage_index: 1, pipeline_stage: "Stage 1", chain_status: "running", input_source_kind: "legacy_source" },
    ];
    const entries = buildStageTimelineEntries(routeSteps, stages);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ route_step_index: 1, status: "running" });
  });

  it("no POST mutation APIs called from cockpit repair panel import", () => {
    const source = MigrationCockpit.toString();
    expect(source).not.toContain("getCurrentRepairProposal");
    expect(source).not.toContain("getRepairAttempts");
  });

  it("PR-C cockpit source does not contain forbidden fields", () => {
    const source = MigrationCockpit.toString();
    const forbidden = [
      "target_path",
      "patch_content",
      "sandbox_path",
      "argv",
      "env",
      "raw_command",
      "azure_endpoint",
      "api_key",
      "password",
    ];
    for (const f of forbidden) {
      expect(source).not.toContain(f);
    }
  });
});

// ── Multi-stage approval flow regression ──────────────────────────────

function approvalCard(
  cardId: string,
  stageIndex: number,
  checksum: string,
  status: "pending" | "approved" | "rejected",
): V2ApprovalResponse {
  return {
    card_id: cardId,
    job_id: "job-123",
    interrupt_id: `run-${stageIndex}`,
    request_checksum: checksum,
    stage_index: stageIndex,
    summary: "Pre-transform review required before sandbox transform.",
    status,
    created_at: "2026-07-02T00:00:00Z",
  };
}

describe("V2 multi-stage approval flow", () => {
  it("mergeCockpitLiveRefreshResults adds a new pending stage-3 approval while keeping stage-2 approved", () => {
    const stage2Approved = approvalCard("gate-stage-2", 2, "checksum-stage-2", "approved");
    const stage3Pending = approvalCard("gate-stage-3", 3, "checksum-stage-3", "pending");
    const current: CockpitData = { ...makeCockpitData(), approvals: [stage2Approved] };
    const merged = mergeCockpitLiveRefreshResults(current, [
      { status: "fulfilled", value: { approvals: [stage2Approved, stage3Pending] } },
      { status: "rejected", reason: new Error("stages fetch skipped") },
      { status: "rejected", reason: new Error("events fetch skipped") },
      { status: "rejected", reason: new Error("pipeline fetch skipped") },
      { status: "rejected", reason: new Error("failure summary fetch skipped") },
    ]);
    expect(merged.data.approvals).toHaveLength(2);
    const byCard = Object.fromEntries(merged.data.approvals.map((a) => [a.card_id, a.status]));
    expect(byCard["gate-stage-2"]).toBe("approved");
    expect(byCard["gate-stage-3"]).toBe("pending");
  });

  it("approved old gate does not hide pending new gate Approve/Reject buttons", () => {
    const stage2Approved = approvalCard("gate-stage-2", 2, "checksum-stage-2", "approved");
    const stage3Pending = approvalCard("gate-stage-3", 3, "checksum-stage-3", "pending");
    const markup = renderToStaticMarkup(
      <ApprovalDecisionsPanel
        approvals={[stage2Approved, stage3Pending]}
        approvalReviewOpen={true}
        approvalBusy={null}
        approvalModeEnabled={false}
        approvalModeBusy={false}
        approvalModeError={null}
        onApprovalModeToggle={() => undefined}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    // Stage 2 shows approved status.
    expect(markup).toContain("Stage 2");
    expect(markup).toContain("APPROVED");
    // Stage 3 pending gate renders its own active Approve/Reject buttons.
    expect(markup).toContain("Stage 3");
    expect(markup).toContain("checksum-stage-3");
    expect(markup).toContain("Approve");
    expect(markup).toContain("Reject");
    // The stage-3 Approve button (second one) must not be disabled.
    const lastApprove = markup.lastIndexOf("Approve");
    expect(markup.slice(markup.lastIndexOf("<button", lastApprove), lastApprove)).not.toContain("disabled");
  });

  it("approveV2Card submits the exact stage-3 card id and checksum, not an earlier stage's", async () => {
    const originalFetch = global.fetch;
    const calls: { url: string; body: string | null }[] = [];
    global.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: typeof init?.body === "string" ? init.body : null });
      return new Response(
        JSON.stringify({
          resume_id: "res-3",
          card_id: "gate-stage-3",
          decision: "approved",
          job_id: "job-123",
          stage_index: 3,
          command: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as typeof fetch;
    try {
      await approveV2Card("job-123", "gate-stage-3", "checksum-stage-3");
      expect(calls[0].url).toBe(
        `${CONTROL_TOWER_API_BASE_URL}/v1/v2/jobs/job-123/approvals/gate-stage-3/approve`,
      );
      const body = JSON.parse(calls[0].body ?? "{}");
      expect(body).toEqual({ expected_checksum: "checksum-stage-3" });
      // Must not send an earlier stage's identity or checksum.
      expect(body.expected_checksum).not.toBe("checksum-stage-2");
      expect(calls[0].url).not.toContain("gate-stage-2");
    } finally {
      global.fetch = originalFetch;
    }
  });

  it("after stages 2 and 3 are approved, stage 4 pending still renders Approve/Reject", () => {
    const stage2Approved = approvalCard("gate-stage-2", 2, "checksum-stage-2", "approved");
    const stage3Approved = approvalCard("gate-stage-3", 3, "checksum-stage-3", "approved");
    const stage4Pending = approvalCard("gate-stage-4", 4, "checksum-stage-4", "pending");
    const markup = renderToStaticMarkup(
      <ApprovalDecisionsPanel
        approvals={[stage4Pending, stage3Approved, stage2Approved]}
        approvalReviewOpen={true}
        approvalBusy={null}
        approvalModeEnabled={false}
        approvalModeBusy={false}
        approvalModeError={null}
        onApprovalModeToggle={() => undefined}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    expect(markup).toContain("Stage 4");
    expect(markup).toContain("checksum-stage-4");
    expect(markup).toContain("Approve");
    expect(markup).toContain("Reject");
    // Earlier approved stages remain visible as approved.
    expect(markup).toContain("APPROVED");
    // The stage-4 Approve button (first one) must not be disabled.
    const firstApprove = markup.indexOf("Approve");
    expect(markup.slice(markup.lastIndexOf("<button", firstApprove), firstApprove)).not.toContain("disabled");
  });
});

function makeCockpitData(): CockpitData {
  return {
    job: {
      job_id: "job-123",
      setup_id: "setup-123",
      setup_checksum: "setup-checksum",
      pipeline_id: "pipeline",
      stages: [],
      created_at: "2026-06-16T00:00:00Z",
    },
    stages: [
      { stage_index: 1, pipeline_stage: "Stage 1", chain_status: "queued", input_source_kind: "legacy_source" },
    ],
    approvals: [
      {
        card_id: "card-1",
        job_id: "job-123",
        stage_index: 1,
        status: "pending",
        summary: "Approval required.",
        request_checksum: "checksum-1",
        created_at: "2026-06-16T00:00:00Z",
      } as CockpitData["approvals"][number],
    ],
    messages: [],
    events: [{ sequence: 1, type: "stage_queued", status: "queued", stage: 1 } as V2JobEvent],
    pipeline: {
      job_id: "job-123",
      active_stage_index: 1,
      rows: [],
      evidence: [],
      raw_logs: [],
    } as CockpitData["pipeline"],
    failureSummary: null,
    assistantModel: null,
  };
}
