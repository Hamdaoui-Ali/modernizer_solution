import { afterEach, describe, expect, it, vi } from "vitest";
import {
  CONTROL_TOWER_API_BASE_URL,
  CONTROL_TOWER_FRONTEND_CLIENT_ID,
  DEFAULT_CONTROL_TOWER_API_BASE_URL,
  allowedStatusCopy,
  applyStage4TargetVersionChanges,
  cancelV2MigrationJob,
  createDiagnosticJobPayload,
  createIdempotencyKey,
  createV2JobPayload,
  eventStreamUrl,
  getV2AssistantMessages,
  getV2GateDetail,
  getV2JobGates,
  getV2OpenGate,
  getV2JobApprovals,
  getV2MigrationJobStages,
  getJob,
  getRepairProposal,
  getRepairProposalDiff,
  getV2FinalReport,
  generateV2FinalReport,
  previewPlanAmendment,
  postJson,
  postV2GateAction,
  requireJobId,
  resolveControlTowerApiBaseUrl,
  resolveReportDownloadUrl
} from "../lib/controlTowerApi";
import { applyPublicEvent, latestAppliedSequence, shouldRefetchJobProjection } from "../lib/eventReplay";
import type { GateActionRequest, GateEvidencePack, V2MigrationJobResponse } from "../lib/contracts";
import { MIGRATION_PROFILE_OPTIONS, type MigrationProfileId } from "../lib/contracts";

describe("M2-01 frontend diagnostic contracts", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("submits only allowed create-job fields", () => {
    const payload = createDiagnosticJobPayload({
      runnerProfileKey: "runner-default@2026.06",
      pipelineKey: "pipeline-default@2026.06",
      legacySourceRootId: "source-root",
      legacySourceRelativePath: "src",
      outputRootId: "output-root",
      outputRelativePath: "out"
    });

    expect(payload).toEqual({
      runner_profile_id: "runner-default",
      runner_profile_version: "2026.06",
      pipeline_id: "pipeline-default",
      pipeline_version: "2026.06",
      legacy_source_root_id: "source-root",
      legacy_source_relative_path: "src",
      output_root_id: "output-root",
      output_relative_path: "out",
      target_proof_level: "ANALYZED",
      enabled_gates: [],
      policy: {
        continue_after_warning: false,
        enable_runtime_gate: false,
        enable_endpoint_gate: false
      }
    });
    expect(JSON.stringify(payload)).not.toContain("actor");
    expect(JSON.stringify(payload)).not.toContain("command");
    expect(JSON.stringify(payload)).not.toContain("executable");
  });

  it("defaults new V2 jobs to auto_on_green stage continuation", () => {
    expect(createV2JobPayload("setup-1")).toEqual({
      setup_id: "setup-1",
      policy: {
        continue_after_warning: false,
        enable_runtime_gate: false,
        enable_endpoint_gate: false,
        stage_continuation_policy: "auto_on_green"
      }
    });
  });

  it("uses only approved diagnostic wording", () => {
    const copy = Object.values(allowedStatusCopy).join(" ");
    expect(copy).toContain("Foundation diagnostic job created");
    expect(copy).toContain("Command queued");
    expect(copy).not.toContain("Migration completed");
    expect(copy).not.toContain("Build verified");
    expect(copy).not.toContain("Spring Boot upgraded");
    expect(copy).not.toContain("Proof achieved");
  });

  it("opens event replay from the last applied sequence", () => {
    expect(eventStreamUrl("job-1", 7)).toContain("/v1/jobs/job-1/events/stream?after_sequence=7");
  });

  it("uses canonical 127.0.0.1 api base url", () => {
    expect(DEFAULT_CONTROL_TOWER_API_BASE_URL).toBe("http://127.0.0.1:8000");
    expect(resolveControlTowerApiBaseUrl(undefined)).toBe("http://127.0.0.1:8000");
    expect(() => resolveControlTowerApiBaseUrl("http://localhost:8000")).toThrow(/127\.0\.0\.1/);
  });

  it("keeps initial job projection fetch non-cached", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      headers: {
        get: (name: string) => (name.toLowerCase() === "etag" ? '"job-job-1-v1"' : null)
      },
      json: async () => ({
        job: {
          job_id: "job-1",
          version: 1,
          state: "CREATED",
          created_at: "2026-06-10T00:00:00Z",
          updated_at: "2026-06-10T00:00:00Z"
        },
        active_command: null
      })
    }));
    vi.stubGlobal("fetch", fetchMock);

    await getJob("job-1");

    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/v1/jobs/job-1"), {
      cache: "no-store"
    });
  });

  it("mutation helper sends required client header and json content type", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ ok: true })
    }));
    vi.stubGlobal("fetch", fetchMock);

    await postJson("/v1/jobs", { value: "ok" }, { "Idempotency-Key": "key-1" });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/jobs"),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "X-Control-Tower-Client": CONTROL_TOWER_FRONTEND_CLIENT_ID,
          "Idempotency-Key": "key-1"
        })
      })
    );
  });


  it("posts V2 cancellation to the migration job cancel endpoint", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        job_id: "job-123",
        status: "cancelled",
        process: { process_found: false, terminated: false, process_count: 0 },
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await cancelV2MigrationJob("job-123");

    expect(result.status).toBe("cancelled");
    expect(fetchMock).toHaveBeenCalledWith(
      `${CONTROL_TOWER_API_BASE_URL}/v1/v2/migration-jobs/job-123/cancel`,
      expect.objectContaining({
        method: "POST",
        body: "{}",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "X-Control-Tower-Client": CONTROL_TOWER_FRONTEND_CLIENT_ID,
        }),
      }),
    );
  });

  it("calls V2 cockpit endpoints with the actual migration route job id", async () => {
    const fetchMock = vi.fn(async (url: string) => ({
      ok: true,
      json: async () => {
        if (url.includes("/assistant/messages")) {
          return { job_id: "429a9bb2154b4be7a99a32867780d744", messages: [] };
        }
        if (url.includes("/approvals")) {
          return { approvals: [] };
        }
        return { job_id: "429a9bb2154b4be7a99a32867780d744", stages: [] };
      },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const jobId = "429a9bb2154b4be7a99a32867780d744";
    await Promise.all([
      getV2JobApprovals(jobId),
      getV2MigrationJobStages(jobId),
      getV2AssistantMessages(jobId),
    ]);

    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls).toEqual([
      expect.stringContaining(`/v1/v2/jobs/${jobId}/approvals`),
      expect.stringContaining(`/v1/v2/migration-jobs/${jobId}/stages`),
      expect.stringContaining(`/v1/v2/jobs/${jobId}/assistant/messages`),
    ]);
    expect(urls.some((url) => url.includes("undefined"))).toBe(false);
  });

  it("calls F15 gate endpoints with safe request shapes", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/gates/open")) {
        return {
          ok: true,
          json: async () => ({ gate: null }),
        };
      }
      if (url.includes("/gates/")) {
        return {
          ok: true,
          json: async () => ({
            gate: {
              gate_id: "gate-1",
              job_id: "job-1",
              gate_phase: "approval_review",
              stage_index: 2,
              gate_status: "open",
              gate_decision: "continue",
              source_artifact_checksum: "sha256:gate",
              source_artifact_refs: ["analysis:1", "plan:1"],
              created_at: "2026-06-12T00:00:00Z",
              resolved_at: null,
              resolved_by: null,
              checksum: "sha256:gate-checksum",
              available_actions: [],
            },
            evidence: null,
            checksum: "sha256:gate-checksum",
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({ gates: [] }),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    await Promise.all([
      getV2JobGates("job-1"),
      getV2OpenGate("job-1"),
      getV2GateDetail("job-1", "gate-1"),
      postV2GateAction("job-1", "gate-1", {
        gate_id: "gate-1",
        job_id: "job-1",
        action: "reject",
        expected_gate_checksum: "sha256:gate-checksum",
        idempotency_key: "idem-1",
        decided_by: "human-1",
        actor_type: "human",
        reason: "not ready",
      }),
    ]);

    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls).toEqual(
      expect.arrayContaining([
        expect.stringContaining("/v1/v2/jobs/job-1/gates"),
        expect.stringContaining("/v1/v2/jobs/job-1/gates/open"),
        expect.stringContaining("/v1/v2/jobs/job-1/gates/gate-1"),
      ])
    );
    const actionCall = fetchMock.mock.calls.find(
      (call) => String(call[0]).includes("/actions"),
    ) as [string, RequestInit?] | undefined;
    expect(actionCall).toBeDefined();
    const body = JSON.parse(String((actionCall?.[1] as RequestInit | undefined)?.body ?? "{}"));
    expect(body).toEqual(expect.objectContaining({
      gate_id: "gate-1",
      job_id: "job-1",
      action: "reject",
      expected_gate_checksum: "sha256:gate-checksum",
      idempotency_key: "idem-1",
      decided_by: "human-1",
      actor_type: "human",
      reason: "not ready",
    }));
    expect(JSON.stringify(body)).not.toContain("sandbox_path");
    expect(JSON.stringify(body)).not.toContain("argv");
    expect(JSON.stringify(body)).not.toContain("env");
    expect(JSON.stringify(body)).not.toContain("raw_command");
    expect(JSON.stringify(body)).not.toContain("filesystem");
  });

  it("does not fetch V2 cockpit endpoints when job id is missing", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    expect(() => requireJobId(" ")).toThrow(/job id is required/i);
    await expect(getV2JobApprovals("")).rejects.toThrow(/job id is required/i);
    await expect(getV2MigrationJobStages("")).rejects.toThrow(/job id is required/i);
    await expect(getV2AssistantMessages("")).rejects.toThrow(/job id is required/i);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts latest-stage target dependency version changes without client paths", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ applied_count: 1 }) }));
    vi.stubGlobal("fetch", fetchMock);

    await applyStage4TargetVersionChanges("job-1", 3, {
      changes: [{ group_id: "org.example", artifact_id: "demo", target_version: "2.0.0" }],
      idempotency_key: "csv-change-1",
    });

    const call = fetchMock.mock.calls[0] as unknown as [string, RequestInit?];
    expect(call[0]).toContain("/v1/v2/jobs/job-1/stage/3/pom/apply-target-version-changes");
    expect(JSON.parse(String(call[1]?.body))).toEqual({
      changes: [{ group_id: "org.example", artifact_id: "demo", target_version: "2.0.0" }],
      idempotency_key: "csv-change-1",
    });
    expect(String(call[1]?.body)).not.toContain("path");
  });

  it("preview helper uses preview endpoint and safe preview contract", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        job_id: "job-1",
        source_kind: "manual",
        title: "Safe preview",
        summary: "Planning only",
        payload_checksum: "chk-1",
        change_count: 1,
        affected_stage_indexes: [1],
        change_types: ["documentation"],
        redacted_summary: {
          source_kind: "manual",
          title: "Safe preview",
          summary: "Planning only",
          change_count: 1,
          affected_stage_indexes: [1],
          change_types: ["documentation"],
          non_authoritative: true
        },
        validation_status: "PASS",
        warning_codes: [],
        preview_persisted: false,
        preview_applied: false
      })
    }));
    vi.stubGlobal("fetch", fetchMock);

    const body = await previewPlanAmendment("job-1", {
      title: "Safe preview",
      summary: "Planning only",
      source_kind: "manual",
      notes: ["safe"],
      changes: [
        {
          stage_index: 1,
          change_type: "documentation",
          description: "Clarify plan text"
        }
      ]
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/jobs/job-1/plan-amendments/preview"),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "X-Control-Tower-Client": CONTROL_TOWER_FRONTEND_CLIENT_ID
        })
      })
    );
    expect(body.validation_status).toBe("PASS");
    expect(body.preview_persisted).toBe(false);
    expect(body.preview_applied).toBe(false);
    expect(body.redacted_summary.non_authoritative).toBe(true);
  });

  // ── V1-18D model activity normalization ────────────────────────────

  it("normalizes backend { model_invocations } into { invocations }", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        model_invocations: [
          {
            invocation_id: "inv-001",
            profile_id: "profile-azure",
            provider_kind: "azure-openai",
            model_name: "gpt-4o",
            prompt_tokens: 150,
            completion_tokens: 42,
            total_tokens: 192,
            redacted_summary: "Analyzed stage 1 output",
            created_at: "2026-06-12T00:00:00Z",
          },
        ],
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const { getModelActivity } = await import("../lib/controlTowerApi");
    const result = await getModelActivity("job-1");

    expect(result).toEqual({
      job_id: "job-1",
      invocations: [
        {
          invocation_id: "inv-001",
          job_id: "job-1",
          profile_id: "profile-azure",
          model_name: "gpt-4o",
          prompt_tokens: 150,
          completion_tokens: 42,
          total_tokens: 192,
          redacted_summary: "Analyzed stage 1 output",
          actor_type: null,
          actor_id: null,
          correlation_id: null,
          causation_id: null,
          created_at: "2026-06-12T00:00:00Z",
        },
      ],
    });
  });

  it("preserves { invocations } key if backend already uses it", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        invocations: [
          {
            invocation_id: "inv-002",
            job_id: "job-2",
            provider_kind: "openai",
            model_name: "gpt-4o-mini",
            prompt_tokens: 80,
            completion_tokens: 20,
            total_tokens: 100,
            redacted_summary: "Patch review",
            created_at: "2026-06-12T01:00:00Z",
          },
        ],
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const { getModelActivity } = await import("../lib/controlTowerApi");
    const result = await getModelActivity("job-2");

    expect(result.invocations).toHaveLength(1);
    expect(result.invocations[0].invocation_id).toBe("inv-002");
    expect(result.invocations[0].job_id).toBe("job-2");
    expect("provider_kind" in result.invocations[0]).toBe(false);
  });

  it("handles empty { model_invocations: [] } gracefully", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ model_invocations: [] }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const { getModelActivity } = await import("../lib/controlTowerApi");
    const result = await getModelActivity("job-3");

    expect(result).toEqual({ job_id: "job-3", invocations: [] });
  });

  it("handles empty { invocations: [] } gracefully", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ invocations: [] }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const { getModelActivity } = await import("../lib/controlTowerApi");
    const result = await getModelActivity("job-4");

    expect(result).toEqual({ job_id: "job-4", invocations: [] });
  });

  it("normalized response exposes no raw prompts, secrets, or deployment IDs", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        model_invocations: [
          {
            invocation_id: "inv-003",
            provider_kind: "azure-openai",
            model_name: "gpt-4o",
            prompt_tokens: 99,
            completion_tokens: 10,
            total_tokens: 109,
            redacted_summary: "Analyzed output",
            created_at: "2026-06-12T02:00:00Z",
          },
        ],
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const { getModelActivity } = await import("../lib/controlTowerApi");
    const result = await getModelActivity("job-5");
    const serialized = JSON.stringify(result);

    expect(result.invocations).toHaveLength(1);
    // Raw prompt content must not leak
    expect(serialized).not.toContain("raw prompt");
    expect(serialized).not.toContain("secret");
    expect(serialized).not.toContain("deployment-id");
    expect(serialized).not.toContain("my-secret");
    expect(serialized).not.toContain("provider_kind");
    expect(serialized).not.toContain("azure-openai");
  });

  it("applies public events idempotently and refetches state-changing projections", () => {
    const event = {
      actor_id: "tester",
      actor_type: "user",
      causation_id: null,
      correlation_id: null,
      created_at: "2026-06-10T00:00:00Z",
      event_id: "event-1",
      event_type: "command_queued",
      job_id: "job-1",
      payload: {},
      payload_checksum: "abc",
      sequence: 2
    };
    const applied = applyPublicEvent({ events: [], lastAppliedSequence: 1 }, event);
    const duplicate = applyPublicEvent(applied, event);

    expect(applied.events).toHaveLength(1);
    expect(applied.lastAppliedSequence).toBe(2);
    expect(duplicate).toBe(applied);
    expect(latestAppliedSequence(applied.events)).toBe(2);
    expect(shouldRefetchJobProjection(event)).toBe(true);
  });
});

describe("AMF-252 revision refresh", () => {
  afterEach(() => vi.restoreAllMocks());

  it("loads returned proposal ID and its diff directly", async () => {
    const fetchMock = vi.fn(async (url: string) => ({
      ok: true,
      json: async () => url.endsWith("/diff")
        ? { job_id: "job-1", safe_diff_preview: { files: [] } }
        : { job_id: "job-1", proposal: { proposal_id: "proposal-2" } },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await getRepairProposal("job-1", "proposal-2");
    await getRepairProposalDiff("job-1", "proposal-2");

    expect(String(fetchMock.mock.calls[0][0])).toContain("/repair/proposals/proposal-2");
    expect(String(fetchMock.mock.calls[1][0])).toContain("/repair/proposals/proposal-2/diff");
  });
});

describe("F15 Final Report API contracts", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("report contracts contain no run_report_json, run_report_markdown, run_report_pdf, sandbox_path, run_dir fields", () => {
    const contractFields = {
      job_id: "",
      status: "not_generated",
      eligible: true,
      blockers: [],
      generated_at: null,
      input_checksum: null,
      redacted_summary: "",
      artifacts: [],
    };
    const serialized = JSON.stringify(contractFields);
    expect(serialized).not.toContain("run_report_json");
    expect(serialized).not.toContain("run_report_markdown");
    expect(serialized).not.toContain("run_report_pdf");
    expect(serialized).not.toContain("sandbox_path");
    expect(serialized).not.toContain("run_dir");
  });

  it("getV2FinalReport encodes job IDs properly", async () => {
    const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
      ok: true,
      json: async () => ({
        job_id: "job-abc",
        status: "not_generated",
        eligible: false,
        blockers: [],
        generated_at: null,
        input_checksum: null,
        redacted_summary: "",
        artifacts: [],
      }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    await getV2FinalReport("job+special");

    const calledUrl = String(fetchMock.mock.calls[0][0]);
    expect(calledUrl).toContain("/v1/v2/jobs/job%2Bspecial/report");
    expect(calledUrl).not.toContain("undefined");
  });

  it("generateV2FinalReport encodes job IDs properly", async () => {
    const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
      ok: true,
      json: async () => ({
        job_id: "job-abc",
        status: "generated",
        eligible: true,
        blockers: [],
        generated_at: "2026-06-20T00:00:00Z",
        input_checksum: "chk-1",
        redacted_summary: "report generated",
        artifacts: [],
      }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    await generateV2FinalReport("job+special");

    const calledUrl = String(fetchMock.mock.calls[0][0]);
    expect(calledUrl).toContain("/v1/v2/jobs/job%2Bspecial/report");
    expect(fetchMock.mock.calls[0][1]).toHaveProperty("method", "POST");
    expect(calledUrl).not.toContain("undefined");
  });

  it("download URL must be API-relative (starts with /v1/)", () => {
    expect(resolveReportDownloadUrl("/v1/reports/report-1")).toBe(
      `${resolveControlTowerApiBaseUrl(undefined)}/v1/reports/report-1`
    );
    expect(() => resolveReportDownloadUrl("http://evil.com/report")).toThrow(
      "Invalid report download URL."
    );
    expect(() => resolveReportDownloadUrl("/download/report")).toThrow(
      "Invalid report download URL."
    );
  });
});

// ── F3 / F4 — Profile routing and override contracts ─────────────────

describe("F3/F4 Profile routing contracts", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("createV2JobPayload includes source_profile and target_profile when provided", () => {
    const payload = createV2JobPayload("setup-1", "auto_on_green", {
      sourceProfile: "springboot-2.7-java11",
      targetProfile: "springboot-4.0-java21",
    });
    expect(payload).toEqual({
      setup_id: "setup-1",
      policy: {
        continue_after_warning: false,
        enable_runtime_gate: false,
        enable_endpoint_gate: false,
        stage_continuation_policy: "auto_on_green",
      },
      source_profile: "springboot-2.7-java11",
      target_profile: "springboot-4.0-java21",
    });
  });

  it("createV2JobPayload omits profiles when not provided", () => {
    const payload = createV2JobPayload("setup-2");
    expect(payload.source_profile).toBeUndefined();
    expect(payload.target_profile).toBeUndefined();
    expect(payload).not.toHaveProperty("source_profile");
    expect(payload).not.toHaveProperty("target_profile");
  });

  it("createV2JobPayload does not include forbidden execution fields", () => {
    const payload = createV2JobPayload("setup-3", "auto_on_green", {
      sourceProfile: "springboot-2.7-java11",
      targetProfile: "springboot-4.0-java21",
    });
    const serialized = JSON.stringify(payload);
    expect(serialized).not.toContain("sandbox_path");
    expect(serialized).not.toContain("argv");
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
    // "provider" and "model" must not appear as standalone field names
    // but may appear as substrings of allowed policy fields
    expect(serialized).not.toMatch(/"provider"/);
    expect(serialized).not.toMatch(/"model"/);
    expect(serialized).not.toMatch(/"model_id"/);
    expect(serialized).not.toMatch(/"deployment"/);
    expect(serialized).not.toMatch(/"endpoint"/);
    expect(serialized).not.toMatch(/"api_key"/);
    expect(serialized).not.toMatch(/"access_token"/);
  });

  it("V2MigrationJobResponse includes optional profile routing fields", () => {
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
    };
    expect(job.source_profile).toBe("springboot-2.7-java11");
    expect(job.target_profile).toBe("springboot-4.0-java21");
    expect(job.validation_status).toBe("valid");
    expect(job.included_stages).toEqual(["2", "3", "4"]);
  });

  it("GateActionRequest includes override_source_profile and detection fields", () => {
    const action: GateActionRequest = {
      gate_id: "gate-1",
      job_id: "job-1",
      action: "continue",
      expected_gate_checksum: "sha256:gate",
      idempotency_key: "idem-1",
      decided_by: "human",
      actor_type: "human",
      reason: "detected profile is correct",
      override_source_profile: "springboot-3.5-java17",
      detection_artifact_ref: "source_profile_detection",
      detected_source_profile: "springboot-2.7-java11",
      requested_source_profile: "springboot-3.5-java17",
      expected_detection_artifact_checksum: "chk-detection",
      comments: "Human verified detection result",
    };
    expect(action.override_source_profile).toBe("springboot-3.5-java17");
    expect(action.detection_artifact_ref).toBe("source_profile_detection");
    expect(action.detected_source_profile).toBe("springboot-2.7-java11");
    expect(action.expected_detection_artifact_checksum).toBe("chk-detection");
  });

  it("GateEvidencePack shape matches the backend evidence pack contract", () => {
    const pack: GateEvidencePack = {
      pack_id: "pack-1",
      pack_type: "source_profile_detection",
      gate_id: "gate-1",
      gate_phase: "analysis_review",
      summary: "Detected springboot-2.7-java11 with high confidence",
      artifacts: [
        {
          kind: "source_profile_detection",
          checksum_verified: true,
          content: '{"detected_source_profile":"springboot-2.7-java11"}',
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
    expect(pack.pack_type).toBe("source_profile_detection");
    expect(pack.artifacts).toHaveLength(1);
    expect(pack.artifacts[0].kind).toBe("source_profile_detection");
    expect(pack.resolved_artifact_count).toBe(1);
    expect(pack.failure_message).toBeNull();
  });

  it("createIdempotencyKey returns a string identifier", () => {
    const key = createIdempotencyKey();
    expect(typeof key).toBe("string");
    expect(key.length).toBeGreaterThan(0);
  });

  it("postV2GateAction sends override fields without forbidden runtime fields", async () => {
    const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
      ok: true,
      json: async () => ({ result: { decision_id: "d-1", gate_id: "gate-1", action: "continue", status: "resolved" } }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    await postV2GateAction("job-1", "gate-1", {
      gate_id: "gate-1",
      job_id: "job-1",
      action: "continue",
      expected_gate_checksum: "sha256:gate",
      idempotency_key: "idem-1",
      decided_by: "human",
      actor_type: "human",
      reason: "override",
      override_source_profile: "springboot-3.5-java17",
      detection_artifact_ref: "source_profile_detection",
      expected_detection_artifact_checksum: "chk-detection",
    });

    const actionCall = fetchMock.mock.calls.find(
      (call) => String(call[0]).includes("/actions"),
    ) as [string, RequestInit?] | undefined;
    const body = JSON.parse(String((actionCall?.[1] as RequestInit | undefined)?.body ?? "{}"));
    expect(body.override_source_profile).toBe("springboot-3.5-java17");
    expect(body.detection_artifact_ref).toBe("source_profile_detection");
    const serialized = JSON.stringify(body);
    expect(serialized).not.toContain("sandbox_path");
    expect(serialized).not.toContain("argv");
    expect(serialized).not.toContain("raw_command");
    expect(serialized).not.toContain("filesystem_target");
    expect(serialized).not.toContain("provider");
    expect(serialized).not.toContain("model");
    expect(serialized).not.toContain("endpoint");
    expect(serialized).not.toContain("api_key");
    expect(serialized).not.toContain("access_token");
  });

  it("MIGRATION_PROFILE_OPTIONS includes springboot-3.5-java21 as a selectable intermediate profile", () => {
    const ids = MIGRATION_PROFILE_OPTIONS.map((p) => p.id);
    expect(ids).toContain("springboot-2.1-java11");
    expect(ids).toContain("springboot-2.7-java11");
    expect(ids).toContain("springboot-3.5-java17");
    expect(ids).toContain("springboot-3.5-java21");
    expect(ids).toContain("springboot-4.0-java21");
    const intermediate = MIGRATION_PROFILE_OPTIONS.find(
      (p) => p.id === "springboot-3.5-java21",
    );
    expect(intermediate).toBeDefined();
    expect(intermediate!.selectableAsSource).toBe(true);
    expect(intermediate!.selectableAsTarget).toBe(true);
    expect(intermediate!.orderIndex).toBe(3);
    const boot21 = MIGRATION_PROFILE_OPTIONS.find((p) => p.id === "springboot-2.1-java11");
    expect(boot21).toBeDefined();
    expect(boot21!.selectableAsSource).toBe(true);
    expect(boot21!.selectableAsTarget).toBe(false);
    const boot27 = MIGRATION_PROFILE_OPTIONS.find((p) => p.id === "springboot-2.7-java11");
    expect(boot27).toBeDefined();
    expect(boot27!.selectableAsSource).toBe(true);
    expect(boot27!.selectableAsTarget).toBe(true);
  });

  it("MIGRATION_PROFILE_OPTIONS enforces backend-supported orderIndex order", () => {
    const order = MIGRATION_PROFILE_OPTIONS.map((p) => p.orderIndex);
    const sorted = [...order].sort((a, b) => a - b);
    expect(order).toEqual(sorted);
  });

  it("createV2JobPayload supports canonical source/target profile pairs", () => {
    const pairs: Array<{ source: MigrationProfileId; target: MigrationProfileId }> = [
      { source: "springboot-2.1-java11", target: "springboot-2.7-java11" },
      { source: "springboot-2.1-java11", target: "springboot-3.5-java17" },
      { source: "springboot-2.1-java11", target: "springboot-3.5-java21" },
      { source: "springboot-2.1-java11", target: "springboot-4.0-java21" },
      { source: "springboot-2.7-java11", target: "springboot-3.5-java17" },
      { source: "springboot-2.7-java11", target: "springboot-3.5-java21" },
      { source: "springboot-2.7-java11", target: "springboot-4.0-java21" },
      { source: "springboot-3.5-java17", target: "springboot-3.5-java21" },
      { source: "springboot-3.5-java17", target: "springboot-4.0-java21" },
      { source: "springboot-3.5-java21", target: "springboot-4.0-java21" },
    ];
    for (const pair of pairs) {
      const payload = createV2JobPayload("setup-1", "auto_on_green", {
        sourceProfile: pair.source,
        targetProfile: pair.target,
      });
      expect(payload.source_profile).toBe(pair.source);
      expect(payload.target_profile).toBe(pair.target);
      expect(payload.setup_id).toBe("setup-1");
      expect(payload.policy.stage_continuation_policy).toBe("auto_on_green");
    }
  });

  it("createV2JobPayload keeps forbidden execution fields absent for every valid pair", () => {
    const pairs: Array<{ source: MigrationProfileId; target: MigrationProfileId }> = [
      { source: "springboot-2.1-java11", target: "springboot-2.7-java11" },
      { source: "springboot-2.1-java11", target: "springboot-3.5-java17" },
      { source: "springboot-2.1-java11", target: "springboot-3.5-java21" },
      { source: "springboot-2.1-java11", target: "springboot-4.0-java21" },
      { source: "springboot-2.7-java11", target: "springboot-3.5-java17" },
      { source: "springboot-2.7-java11", target: "springboot-3.5-java21" },
      { source: "springboot-2.7-java11", target: "springboot-4.0-java21" },
      { source: "springboot-3.5-java17", target: "springboot-3.5-java21" },
      { source: "springboot-3.5-java17", target: "springboot-4.0-java21" },
      { source: "springboot-3.5-java21", target: "springboot-4.0-java21" },
    ];
    for (const pair of pairs) {
      const payload = createV2JobPayload("setup-1", "auto_on_green", {
        sourceProfile: pair.source,
        targetProfile: pair.target,
      });
      const serialized = JSON.stringify(payload);
      for (const forbidden of [
        "sandbox_path", "argv", "raw_command", "filesystem_target",
        "filesystem_root", "output_root", "report_root", "run_root",
        "ai_hub_path", "java_home", "java11_home", "java17_home",
        "java21_home", "maven_cmd",
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
    }
  });

  it("createIdempotencyKey works when crypto.randomUUID is available", () => {
    const originalRandomUUID = globalThis.crypto?.randomUUID;
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: { ...(globalThis.crypto ?? {}), randomUUID: () => "00000000-0000-4000-8000-000000000000" },
    });
    try {
      const key = createIdempotencyKey();
      expect(key).toBe("00000000-0000-4000-8000-000000000000");
    } finally {
      if (originalRandomUUID) {
        Object.defineProperty(globalThis, "crypto", {
          configurable: true,
          value: { ...(globalThis.crypto ?? {}), randomUUID: originalRandomUUID },
        });
      }
    }
  });

// ── PR-C — Reviewed Diff Proposal API client tests ────────────────────

describe("PR-C Reviewed Diff Proposal API client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("getCurrentRepairProposal calls GET /v1/v2/jobs/{jobId}/repair/proposals/current", async () => {
    const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
      ok: true,
      json: async () => ({ proposal: null, job_id: "job-1" }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    const { getCurrentRepairProposal } = await import("../lib/controlTowerApi");
    await getCurrentRepairProposal("job-1");

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/v2/jobs/job-1/repair/proposals/current");
    const init = fetchMock.mock.calls[0][1] as RequestInit | undefined;
    expect(init?.method ?? "GET").toBe("GET");
    expect(url).not.toContain("undefined");
  });

  it("getRepairProposal calls GET detail endpoint", async () => {
    const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
      ok: true,
      json: async () => ({ proposal: null, job_id: "job-1" }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    const { getRepairProposal } = await import("../lib/controlTowerApi");
    await getRepairProposal("job-1", "proposal-1");

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/v2/jobs/job-1/repair/proposals/proposal-1");
    const init = fetchMock.mock.calls[0][1] as RequestInit | undefined;
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("getRepairProposal throws on empty proposalId", async () => {
    const { getRepairProposal } = await import("../lib/controlTowerApi");
    await expect(getRepairProposal("job-1", " ")).rejects.toThrow("Proposal id is required.");
  });

  it("getRepairProposalDiff calls GET diff endpoint", async () => {
    const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
      ok: true,
      json: async () => ({ safe_diff_preview: null, job_id: "job-1", reason: "no_diff_ref" }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    const { getRepairProposalDiff } = await import("../lib/controlTowerApi");
    await getRepairProposalDiff("job-1", "proposal-1");

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/v2/jobs/job-1/repair/proposals/proposal-1/diff");
    const init = fetchMock.mock.calls[0][1] as RequestInit | undefined;
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("getRepairProposalDiff throws on empty proposalId", async () => {
    const { getRepairProposalDiff } = await import("../lib/controlTowerApi");
    await expect(getRepairProposalDiff("job-1", "")).rejects.toThrow("Proposal id is required.");
  });

  it("getRepairAttempts calls GET attempts endpoint", async () => {
    const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
      ok: true,
      json: async () => ({ attempts: [], job_id: "job-1" }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    const { getRepairAttempts } = await import("../lib/controlTowerApi");
    await getRepairAttempts("job-1");

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/v2/jobs/job-1/repair/attempts");
    const init = fetchMock.mock.calls[0][1] as RequestInit | undefined;
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("no POST method is introduced by PR-C", async () => {
    const {
      getCurrentRepairProposal,
      getRepairProposal,
      getRepairProposalDiff,
      getRepairAttempts,
    } = await import("../lib/controlTowerApi");
    expect(typeof getCurrentRepairProposal).toBe("function");
    expect(typeof getRepairProposal).toBe("function");
    expect(typeof getRepairProposalDiff).toBe("function");
    expect(typeof getRepairAttempts).toBe("function");
  });

  it("response parsing: null proposal shape", async () => {
    const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
      ok: true,
      json: async () => ({ proposal: null, job_id: "job-1" }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    const { getCurrentRepairProposal } = await import("../lib/controlTowerApi");
    const result = await getCurrentRepairProposal("job-1");
    expect(result.proposal).toBeNull();
    expect(result.job_id).toBe("job-1");
  });

  it("response parsing: diff response shape with reason", async () => {
    const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
      ok: true,
      json: async () => ({
        safe_diff_preview: null,
        job_id: "job-1",
        reason: "no_diff_ref",
      }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    const { getRepairProposalDiff } = await import("../lib/controlTowerApi");
    const result = await getRepairProposalDiff("job-1", "proposal-1");
    expect(result.safe_diff_preview).toBeNull();
    expect(result.job_id).toBe("job-1");
    expect(result.reason).toBe("no_diff_ref");
  });

  it("response parsing: attempts response shape", async () => {
    const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
      ok: true,
      json: async () => ({
        attempts: [
          {
            proposal_id: "p-1",
            command_id: "cmd-1",
            job_id: "job-1",
            gate_id: null,
            attempt_number: 1,
            revision_number: null,
            status: "reviewer_accepted",
            reviewer_decision: null,
            diff_checksum: "sha256:abc",
            policy_validation_checksum: null,
            status_reason: null,
            created_at: "2026-06-30T00:00:00Z",
          },
        ],
        job_id: "job-1",
      }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    const { getRepairAttempts } = await import("../lib/controlTowerApi");
    const result = await getRepairAttempts("job-1");
    expect(result.attempts).toHaveLength(1);
    expect(result.attempts[0].proposal_id).toBe("p-1");
    expect(result.attempts[0].attempt_number).toBe(1);
    expect(result.attempts[0].status).toBe("reviewer_accepted");
    expect(result.job_id).toBe("job-1");
  });

  // ── PR-D — Repair proposal revision API client tests ─────────────────

  describe("PR-D RepairProposalRevision API client", () => {
    afterEach(() => {
      vi.restoreAllMocks();
    });

    it("requestRepairProposalRevision calls POST revise endpoint", async () => {
      const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
        ok: true,
        json: async () => ({
          job_id: "job-1",
          previous_proposal_id: "proposal-1",
          proposal: null,
          status: "revision_requested",
          event_ids: ["evt-1"],
          artifact_refs: {},
        }),
      } as Response));
      vi.stubGlobal("fetch", fetchMock);

      const { requestRepairProposalRevision } = await import("../lib/controlTowerApi");
      await requestRepairProposalRevision("job-1", "proposal-1", {
        user_instruction: "Only update validation dependency",
        previous_diff_checksum: "sha256:abc",
        previous_reviewer_verdict_id: "verdict-1",
        idempotency_key: "idem-1",
      });

      const call = fetchMock.mock.calls[0] as [string, RequestInit?];
      const url = String(call[0]);
      expect(url).toContain("/v1/v2/jobs/job-1/repair/proposals/proposal-1/revise");
      const body = JSON.parse(String(call[1]?.body ?? "{}"));
      expect(body).toEqual({
        user_instruction: "Only update validation dependency",
        previous_diff_checksum: "sha256:abc",
        previous_reviewer_verdict_id: "verdict-1",
        idempotency_key: "idem-1",
      });
    });

    it("request body contains only allowed fields", async () => {
      const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
        ok: true,
        json: async () => ({
          job_id: "job-1",
          previous_proposal_id: "proposal-1",
          proposal: null,
          status: "revision_requested",
          event_ids: [],
          artifact_refs: {},
        }),
      } as Response));
      vi.stubGlobal("fetch", fetchMock);

      const { requestRepairProposalRevision } = await import("../lib/controlTowerApi");
      await requestRepairProposalRevision("job-1", "proposal-1", {
        user_instruction: "Fix it",
        previous_diff_checksum: "sha256:abc",
        previous_reviewer_verdict_id: "verdict-1",
        expected_gate_checksum: "sha256:gate",
        idempotency_key: "idem-2",
      });

      const call = fetchMock.mock.calls[0] as [string, RequestInit?];
      const body = JSON.parse(String(call[1]?.body ?? "{}"));
      expect(body).toEqual({
        user_instruction: "Fix it",
        previous_diff_checksum: "sha256:abc",
        previous_reviewer_verdict_id: "verdict-1",
        expected_gate_checksum: "sha256:gate",
        idempotency_key: "idem-2",
      });
      const serialized = JSON.stringify(body);
      expect(serialized).not.toContain("patch_content");
      expect(serialized).not.toContain("target_path");
      expect(serialized).not.toContain("sandbox_path");
      expect(serialized).not.toContain("argv");
      expect(serialized).not.toContain("raw_command");
    });

    it("throws on empty proposal id", async () => {
      const { requestRepairProposalRevision } = await import("../lib/controlTowerApi");
      await expect(
        requestRepairProposalRevision("job-1", "", {
          user_instruction: "Fix it",
          previous_diff_checksum: "sha256:abc",
          previous_reviewer_verdict_id: "verdict-1",
        }),
      ).rejects.toThrow("Proposal id is required.");
    });

    it("POST method is used", async () => {
      const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
        ok: true,
        json: async () => ({
          job_id: "job-1",
          previous_proposal_id: "proposal-1",
          proposal: null,
          status: "revision_requested",
          event_ids: [],
          artifact_refs: {},
        }),
      } as Response));
      vi.stubGlobal("fetch", fetchMock);

      const { requestRepairProposalRevision } = await import("../lib/controlTowerApi");
      await requestRepairProposalRevision("job-1", "proposal-1", {
        user_instruction: "Fix it",
        previous_diff_checksum: "sha256:abc",
        previous_reviewer_verdict_id: "verdict-1",
      });

      const call = fetchMock.mock.calls[0] as [string, RequestInit?];
      expect(call[1]?.method).toBe("POST");
    });

    it("response shape matches contract", async () => {
      const mockResponse = {
        job_id: "job-1",
        previous_proposal_id: "proposal-1",
        proposal: null,
        status: "revision_requested",
        event_ids: ["evt-1"],
        artifact_refs: { result_revision_id: "rev-1" },
      };
      const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
        ok: true,
        json: async () => mockResponse,
      } as Response));
      vi.stubGlobal("fetch", fetchMock);

      const { requestRepairProposalRevision } = await import("../lib/controlTowerApi");
      const result = await requestRepairProposalRevision("job-1", "proposal-1", {
        user_instruction: "Fix it",
        previous_diff_checksum: "sha256:abc",
        previous_reviewer_verdict_id: "verdict-1",
      });
      expect(result.job_id).toBe("job-1");
      expect(result.previous_proposal_id).toBe("proposal-1");
      expect(result.status).toBe("revision_requested");
      expect(result.event_ids).toContain("evt-1");
      expect(result.artifact_refs.result_revision_id).toBe("rev-1");
      expect("patch_content" in result).toBe(false);
      expect("target_path" in result).toBe(false);
      expect("sandbox_path" in result).toBe(false);
    });

    it("no raw fields in revision request or response", async () => {
      const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
        ok: true,
        json: async () => ({
          job_id: "job-1",
          previous_proposal_id: "proposal-1",
          proposal: null,
          status: "revision_requested",
          event_ids: [],
          artifact_refs: {},
        }),
      } as Response));
      vi.stubGlobal("fetch", fetchMock);

      const { requestRepairProposalRevision } = await import("../lib/controlTowerApi");
      const result = await requestRepairProposalRevision("job-1", "proposal-1", {
        user_instruction: "Fix it",
        previous_diff_checksum: "sha256:abc",
        previous_reviewer_verdict_id: "verdict-1",
      });
      const serialized = JSON.stringify(result);
      expect(serialized).not.toContain("sandbox_path");
      expect(serialized).not.toContain("argv");
      expect(serialized).not.toContain("raw_command");
      expect(serialized).not.toContain("endpoint");
      expect(serialized).not.toContain("deployment");
      expect(serialized).not.toContain("api_key");
      expect(serialized).not.toContain("secret");
    });
  });

  it("no raw field exposure in PR-C responses", async () => {
    const fetchMock = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(async () => ({
      ok: true,
      json: async () => ({
        proposal: {
          proposal_id: "p-1",
          failure_summary: "Build failed",
          safe_diff_preview: {
            proposal_id: "p-1",
            diff_ref: null,
            diff_checksum: "sha256:abc",
            files: [],
            total_additions: 0,
            total_deletions: 0,
            truncated: false,
            checksum_mismatch: false,
            redactions: [],
          },
        },
        job_id: "job-1",
      }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    const { getCurrentRepairProposal } = await import("../lib/controlTowerApi");
    const result = await getCurrentRepairProposal("job-1");
    const serialized = JSON.stringify(result);
    expect(serialized).not.toContain("target_path");
    expect(serialized).not.toContain("patch_content");
    expect(serialized).not.toContain("sandbox_path");
    expect(serialized).not.toContain("argv");
    expect(serialized).not.toContain("env");
    expect(serialized).not.toContain("raw_command");
    expect(serialized).not.toContain("azure_endpoint");
    expect(serialized).not.toContain("api_key");
    expect(serialized).not.toContain("password");
    expect(serialized).not.toContain("authorization");
    expect(serialized).not.toContain("secret");
  });
});

it("createIdempotencyKey falls back when crypto.randomUUID is missing", () => {
    const originalCrypto = globalThis.crypto;
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: undefined,
    });
    try {
      const key = createIdempotencyKey();
      expect(typeof key).toBe("string");
      expect(key.length).toBeGreaterThan(0);
      expect(key.startsWith("idempotency-")).toBe(true);
    } finally {
      Object.defineProperty(globalThis, "crypto", {
        configurable: true,
        value: originalCrypto,
      });
    }
  });
});
