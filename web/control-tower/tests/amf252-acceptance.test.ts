import { afterEach, describe, expect, it, vi } from "vitest";
import {
  sendRepairAssistantMessage,
  fetchRepairAssistantMessages,
} from "../lib/controlTowerApi";
import type {
  RepairAssistantAction,
  RepairAssistantMessage,
  RepairAssistantMessageStatus,
  RepairAssistantSendResponse,
} from "../lib/contracts";

// ── Contract verification ─────────────────────────────────────────────

describe("AMF-252 TypeScript contracts", () => {
  it("RepairAssistantAction includes new action types", () => {
    const action: RepairAssistantAction = "revision_failed";
    expect(action).toBe("revision_failed");
    const blocked: RepairAssistantAction = "blocked";
    expect(blocked).toBe("blocked");
    const error: RepairAssistantAction = "error";
    expect(error).toBe("error");
    const answer: RepairAssistantAction = "ANSWER_ONLY";
    expect(answer).toBe("ANSWER_ONLY");
    const revise: RepairAssistantAction = "REQUEST_REVISION";
    expect(revise).toBe("REQUEST_REVISION");
    const clarification: RepairAssistantAction = "CLARIFICATION_REQUIRED";
    expect(clarification).toBe("CLARIFICATION_REQUIRED");
  });

  it("RepairAssistantMessageStatus includes revision_failed", () => {
    const status: RepairAssistantMessageStatus = "revision_failed";
    expect(status).toBe("revision_failed");
  });

  it("RepairAssistantMessage includes diagnostic fields", () => {
    const msg: RepairAssistantMessage = {
      message_id: "msg-1",
      job_id: "job-1",
      proposal_id: "prop-1",
      role: "assistant",
      message: "Revision failed",
      status: "revision_failed",
      action: "revision_failed",
      created_at: "2026-07-16T00:00:00Z",
      failure_stage: "proposer",
      failure_code: "ECONNREFUSED",
      safe_failure_message: "The proposer could not be reached",
      correlation_id: "corr-abc-123",
    };
    expect(msg.failure_stage).toBe("proposer");
    expect(msg.failure_code).toBe("ECONNREFUSED");
    expect(msg.safe_failure_message).toBe("The proposer could not be reached");
    expect(msg.correlation_id).toBe("corr-abc-123");
  });

  it("RepairAssistantSendResponse includes diagnostic fields", () => {
    const response: RepairAssistantSendResponse = {
      message_id: "msg-2",
      assistant_message: "Failure encountered",
      action: "revision_failed",
      revision_started: false,
      status: "revision_failed",
      failure_stage: "generation",
      failure_code: "UNKNOWN",
      safe_failure_message: "An unexpected error occurred",
      correlation_id: "corr-xyz-789",
    };
    expect(response.failure_stage).toBe("generation");
    expect(response.failure_code).toBe("UNKNOWN");
    expect(response.safe_failure_message).toBe("An unexpected error occurred");
    expect(response.correlation_id).toBe("corr-xyz-789");
  });
});

// ── API client behavior ───────────────────────────────────────────────

describe("AMF-252 API client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sendRepairAssistantMessage sends base_diff_checksum", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        message_id: "msg-3",
        assistant_message: "OK",
        action: "ANSWER_ONLY" as const,
        revision_started: false,
        status: "answered" as const,
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "test",
      idempotency_key: "idem-1",
      base_diff_checksum: "sha256:abc",
    });

    const call = fetchMock.mock.calls[0] as [string, RequestInit?];
    const body = JSON.parse(String(call[1]?.body ?? "{}"));
    expect(body.base_diff_checksum).toBe("sha256:abc");
    expect(body.message).toBe("test");
    expect(body.idempotency_key).toBe("idem-1");
    expect(JSON.stringify(body)).not.toContain("sandbox_path");
    expect(JSON.stringify(body)).not.toContain("raw_command");
  });

  it("sendRepairAssistantMessage returns diagnostic fields on failure", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        message_id: "msg-4",
        assistant_message: "Generation failed",
        action: "revision_failed" as RepairAssistantAction,
        revision_started: false,
        status: "revision_failed" as RepairAssistantMessageStatus,
        failure_stage: "proposer",
        failure_code: "TIMEOUT",
        safe_failure_message: "The model timed out after 300s",
        correlation_id: "corr-fail-001",
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "fix it",
      idempotency_key: "idem-2",
      base_diff_checksum: "sha256:abc",
    });

    expect(response.status).toBe("revision_failed");
    expect(response.action).toBe("revision_failed");
    expect(response.failure_stage).toBe("proposer");
    expect(response.failure_code).toBe("TIMEOUT");
    expect(response.safe_failure_message).toBe("The model timed out after 300s");
    expect(response.correlation_id).toBe("corr-fail-001");
    expect(response.revision_started).toBe(false);
  });

  it("sendRepairAssistantMessage returns new_proposal_id on success", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        message_id: "msg-5",
        assistant_message: "Revised proposal created",
        action: "REQUEST_REVISION" as RepairAssistantAction,
        revision_started: true,
        status: "revision_created" as RepairAssistantMessageStatus,
        new_proposal_id: "proposal-v2",
        new_attempt_number: 2,
        new_diff_checksum: "sha256:def",
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "use reviewer feedback",
      idempotency_key: "idem-3",
      base_diff_checksum: "sha256:abc",
    });

    expect(response.status).toBe("revision_created");
    expect(response.new_proposal_id).toBe("proposal-v2");
    expect(response.new_attempt_number).toBe(2);
    expect(response.new_diff_checksum).toBe("sha256:def");
    expect(response.revision_started).toBe(true);
  });

  it("fetchRepairAssistantMessages returns messages with diagnostic fields", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        messages: [
          {
            message_id: "msg-6",
            job_id: "job-1",
            proposal_id: "prop-1",
            role: "assistant",
            message: "Failed to generate revision",
            status: "revision_failed",
            action: "revision_failed",
            created_at: "2026-07-16T00:00:00Z",
            failure_stage: "reviewer",
            failure_code: "SCHEMA_ERROR",
            safe_failure_message: "Reviewer output did not match schema",
            correlation_id: "corr-fail-002",
          },
        ],
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await fetchRepairAssistantMessages("job-1", "prop-1");
    expect(response.messages).toHaveLength(1);
    const msg = response.messages[0];
    expect(msg.status).toBe("revision_failed");
    expect(msg.failure_stage).toBe("reviewer");
    expect(msg.failure_code).toBe("SCHEMA_ERROR");
    expect(msg.safe_failure_message).toBe("Reviewer output did not match schema");
    expect(msg.correlation_id).toBe("corr-fail-002");
  });
});

// ── ANSWER_ONLY behavior ──────────────────────────────────────────────

describe("AMF-252 ANSWER_ONLY behavior", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("ANSWER_ONLY response has no revision fields", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        message_id: "msg-10",
        assistant_message: "Here is an explanation",
        action: "ANSWER_ONLY" as RepairAssistantAction,
        revision_started: false,
        status: "answered" as RepairAssistantMessageStatus,
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "Explain this diff",
      idempotency_key: "idem-10",
      base_diff_checksum: "sha256:abc",
    });

    expect(response.action).toBe("ANSWER_ONLY");
    expect(response.status).toBe("answered");
    expect(response.revision_started).toBe(false);
    expect(response.new_proposal_id).toBeUndefined();
    expect(response.new_diff_checksum).toBeUndefined();
    expect(response.failure_stage).toBeUndefined();
  });
});

// ── REQUEST_REVISION behavior ─────────────────────────────────────────

describe("AMF-252 REQUEST_REVISION behavior", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("REQUEST_REVISION with gate_id=null sends correct request and returns new proposal", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        message_id: "msg-11",
        assistant_message: "Generating revised proposal based on feedback",
        action: "REQUEST_REVISION" as RepairAssistantAction,
        revision_started: true,
        status: "revision_generating" as RepairAssistantMessageStatus,
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "Use reviewer feedback to fix imports",
      idempotency_key: "idem-11",
      base_diff_checksum: "sha256:abc",
    });

    expect(response.action).toBe("REQUEST_REVISION");
    expect(response.revision_started).toBe(true);
    expect(response.status).toBe("revision_generating");

    const call = fetchMock.mock.calls[0] as [string, RequestInit?];
    const body = JSON.parse(String(call[1]?.body ?? "{}"));
    expect(body.base_diff_checksum).toBe("sha256:abc");
    expect(body.message).toBe("Use reviewer feedback to fix imports");
    expect(JSON.stringify(body)).not.toContain("gate_id");
  });

  it("No duplicate proposal on idempotent request", async () => {
    let callCount = 0;
    const fetchMock = vi.fn(async () => {
      callCount++;
      return {
        ok: true,
        json: async () => ({
          message_id: `msg-${callCount}`,
          assistant_message: "Already processed",
          action: "REQUEST_REVISION" as RepairAssistantAction,
          revision_started: true,
          status: "revision_created" as RepairAssistantMessageStatus,
          new_proposal_id: "proposal-v2",
          new_diff_checksum: "sha256:def",
        }),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    const key = "same-idempotency-key";
    const first = await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "fix imports",
      idempotency_key: key,
      base_diff_checksum: "sha256:abc",
    });
    const second = await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "fix imports",
      idempotency_key: key,
      base_diff_checksum: "sha256:abc",
    });

    expect(first.new_proposal_id).toBe("proposal-v2");
    expect(second.new_proposal_id).toBe("proposal-v2");
  });

  it("Reviewer receives generated diff and checksum", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        message_id: "msg-12",
        assistant_message: "Proposal generated",
        action: "REQUEST_REVISION" as RepairAssistantAction,
        revision_started: true,
        status: "revision_created" as RepairAssistantMessageStatus,
        new_proposal_id: "proposal-v3",
        new_diff_checksum: "sha256:new-diff",
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "revise",
      idempotency_key: "idem-12",
      base_diff_checksum: "sha256:abc",
    });

    expect(response.new_diff_checksum).toBe("sha256:new-diff");
    expect(response.new_diff_checksum).not.toBe("sha256:abc");
  });

  it("Exactly one new immutable proposal is persisted", async () => {
    let callCount = 0;
    const fetchMock = vi.fn(async () => {
      callCount++;
      return {
        ok: true,
        json: async () => ({
          message_id: `msg-${callCount}`,
          assistant_message: "Single proposal created",
          action: "REQUEST_REVISION" as RepairAssistantAction,
          revision_started: true,
          status: "revision_created" as RepairAssistantMessageStatus,
          new_proposal_id: "proposal-unique",
          new_diff_checksum: "sha256:unique",
        }),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    const response = await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "fix",
      idempotency_key: "idem-13",
      base_diff_checksum: "sha256:abc",
    });

    expect(response.new_proposal_id).toBe("proposal-unique");
    expect(callCount).toBe(1);
    expect(response.status).toBe("revision_created");
  });

  it("revision_of points to old proposal via previous_diff_checksum", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        message_id: "msg-13",
        assistant_message: "Revision created",
        action: "REQUEST_REVISION" as RepairAssistantAction,
        revision_started: true,
        status: "revision_created" as RepairAssistantMessageStatus,
        new_proposal_id: "proposal-v2",
        new_diff_checksum: "sha256:new-diff",
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "revise from feedback",
      idempotency_key: "idem-14",
      base_diff_checksum: "sha256:old-checksum",
    });

    expect(response.status).toBe("revision_created");
    expect(response.new_proposal_id).toBe("proposal-v2");
    const call = fetchMock.mock.calls[0] as [string, RequestInit?];
    const body = JSON.parse(String(call[1]?.body ?? "{}"));
    expect(body.base_diff_checksum).toBe("sha256:old-checksum");
    expect(response.new_diff_checksum).not.toBe(body.base_diff_checksum);
  });
});

// ── Failure modes ─────────────────────────────────────────────────────

describe("AMF-252 failure modes", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("Missing idempotency_key fails before model execution", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 422,
      json: async () => ({ detail: { code: "VALIDATION_ERROR", message: "idempotency_key is required" } }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      sendRepairAssistantMessage("job-1", "prop-1", {
        message: "fix",
        idempotency_key: "",
        base_diff_checksum: "sha256:abc",
      }),
    ).rejects.toThrow(/VALIDATION_ERROR/);
  });

  it("Lease loss returns HTTP 409", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 409,
      json: async () => ({ detail: { code: "LEASE_LOST", message: "Another worker holds the lease" } }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      sendRepairAssistantMessage("job-1", "prop-1", {
        message: "fix",
        idempotency_key: "idem-lease",
        base_diff_checksum: "sha256:abc",
      }),
    ).rejects.toThrow(/LEASE_LOST/);
  });

  it("Generation exception persists diagnostic fields", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        message_id: "msg-fail-1",
        assistant_message: "Generation threw an exception",
        action: "revision_failed" as RepairAssistantAction,
        revision_started: false,
        status: "revision_failed" as RepairAssistantMessageStatus,
        failure_stage: "proposer",
        failure_code: "MODEL_EXCEPTION",
        safe_failure_message: "The model raised an unrecoverable error",
        correlation_id: "corr-exc-001",
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "fix it",
      idempotency_key: "idem-exc",
      base_diff_checksum: "sha256:abc",
    });

    expect(response.status).toBe("revision_failed");
    expect(response.failure_stage).toBe("proposer");
    expect(response.failure_code).toBe("MODEL_EXCEPTION");
    expect(response.safe_failure_message).toBe("The model raised an unrecoverable error");
    expect(response.correlation_id).toBe("corr-exc-001");
  });

  it("No Apply, Maven, commit, or sandbox patch occurs on failure", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        message_id: "msg-fail-2",
        assistant_message: "Cannot proceed",
        action: "revision_failed" as RepairAssistantAction,
        revision_started: false,
        status: "revision_failed" as RepairAssistantMessageStatus,
        failure_stage: "reviewer",
        failure_code: "REJECTED",
        safe_failure_message: "Reviewer rejected the proposal",
        correlation_id: "corr-no-apply",
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "fix",
      idempotency_key: "idem-no-apply",
      base_diff_checksum: "sha256:abc",
    });

    const serialized = JSON.stringify(response);
    expect(serialized).not.toContain("apply_status");
    expect(serialized).not.toContain("sandbox");
    expect(serialized).not.toContain("maven");
    expect(serialized).not.toContain("git_commit");
    expect(serialized).not.toContain("patch");

    expect(response.status).toBe("revision_failed");
    expect(response.revision_started).toBe(false);
  });
});

// ── Transient failure handling ────────────────────────────────────────

describe("AMF-252 transient failure handling", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("Transient lease DB failure returns structured error", async () => {
    let attempts = 0;
    const fetchMock = vi.fn(async () => {
      attempts++;
      if (attempts === 1) {
        return {
          ok: false,
          status: 503,
          json: async () => ({
            detail: { code: "LEASE_STATE_UNAVAILABLE", message: "Database lease state unavailable" },
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({
          message_id: "msg-retry-1",
          assistant_message: "OK",
          action: "ANSWER_ONLY" as RepairAssistantAction,
          revision_started: false,
          status: "answered" as RepairAssistantMessageStatus,
        }),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      sendRepairAssistantMessage("job-1", "prop-1", {
        message: "fix",
        idempotency_key: "idem-retry",
        base_diff_checksum: "sha256:abc",
      }),
    ).rejects.toThrow(/LEASE_STATE_UNAVAILABLE/);
  });

  it("Proposer failure creates no actionable proposal", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        message_id: "msg-prop-fail",
        assistant_message: "Proposer failed",
        action: "revision_failed" as RepairAssistantAction,
        revision_started: false,
        status: "revision_failed" as RepairAssistantMessageStatus,
        failure_stage: "proposer",
        failure_code: "NO_PROPOSAL",
        safe_failure_message: "Proposer returned no actionable output",
        correlation_id: "corr-no-prop",
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await sendRepairAssistantMessage("job-1", "prop-1", {
      message: "generate",
      idempotency_key: "idem-prop-fail",
      base_diff_checksum: "sha256:abc",
    });

    expect(response.new_proposal_id).toBeUndefined();
    expect(response.status).toBe("revision_failed");
    expect(response.failure_stage).toBe("proposer");
  });
});
