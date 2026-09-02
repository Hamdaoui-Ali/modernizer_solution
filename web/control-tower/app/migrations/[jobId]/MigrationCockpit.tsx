"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  askV2Assistant,
  approveV2Card,
  cancelV2MigrationJob,
  getV2AssistantMessages,
  getV2JobEventSnapshot,
  getV2JobApprovals,
  getV2MigrationJob,
  getV2JobPipeline,
  getV2GateDetail,
  getV2JobGates,
  getV2OpenGate,
  getV2MigrationJobStages,
  getV2FinalReport,
  generateV2FinalReport,
  resolveReportDownloadUrl,
  rejectV2Card,
  updateV2ApprovalMode,
  postV2GateAction,
  requireJobId,
  v2EventStreamUrl,
  createIdempotencyKey,
} from "../../../lib/controlTowerApi";
import {
  logApprovalEvent,
  logApprovalDecisionsBefore,
  logApprovalDecisionsAfter,
  logOpenGates,
  logApproveClickPayload,
} from "../../../lib/approvalDebug";
import type {
  V2ApprovalResponse,
  V2AssistantMessageResponse,
  V2FinalReportResponse,
  V2JobEvent,
  V2MigrationJobResponse,
  V2PipelineResponse,
  V2RouteStepEntry,
  GateDetailResponse,
  GateRepresentation,
  GateEvidencePack,
  MigrationProfileId,
} from "../../../lib/contracts";
import { MIGRATION_PROFILE_OPTIONS } from "../../../lib/contracts";
import Stage4TargetVersionComparison from "./Stage4TargetVersionComparison";
import { RepairProposalPanel } from "./RepairProposalPanel";
import { EvidenceDrawer } from "./components/EvidenceDrawer";
import { FloatingMigrationAssistant } from "./components/FloatingMigrationAssistant";
import { CancelMigrationDialog } from "./components/CancelMigrationDialog";
import { JobDetailsTabs } from "./components/JobDetailsTabs";
import { CurrentExecutionSummary } from "./components/CurrentExecutionSummary";
import { PipelineStatusList } from "./components/PipelineStatusList";
import styles from "./MigrationCockpit.module.css";

export function formatGateArtifactRefLabel(ref: string): string {
  const text = ref.trim();
  if (!text) {
    return "artifact";
  }
  const label = text.replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? text;
  return label === "C:" ? "artifact" : label;
}

function formatGateArtifactRefs(refs: string[]): string {
  return refs.map((ref) => formatGateArtifactRefLabel(ref)).join(", ");
}

export interface Stage {
  stage_index: number;
  pipeline_stage: string;
  chain_status: string;
  input_source_kind: string;
}

function formatRouteStepStatusLabel(status: string): string {
  switch (status) {
    case "completed":
      return "COMPLETED";
    case "running":
      return "RUNNING";
    case "blocked":
      return "BLOCKED";
    case "queued":
      return "QUEUED";
    case "failed":
      return "FAILED";
    default:
      return "PENDING";
  }
}

export interface CockpitData {
  job: V2MigrationJobResponse;
  stages: Stage[];
  approvals: V2ApprovalResponse[];
  messages: V2AssistantMessageResponse[];
  events: V2JobEvent[];
  pipeline: V2PipelineResponse;
  assistantModel: { status: string; source: string; provider: string; role: string; failure_reason?: string } | null;
}

type GatePanelState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "empty" }
  | {
      status: "success";
      gates: GateRepresentation[];
      openGate: GateRepresentation | null;
      openGateDetail: GateDetailResponse | null;
    };

type LiveRefreshResults = [
  PromiseSettledResult<{ approvals: V2ApprovalResponse[] }>,
  PromiseSettledResult<{ job_id: string; stages: Stage[] }>,
  PromiseSettledResult<{ events: V2JobEvent[] }>,
  PromiseSettledResult<V2PipelineResponse>,
];

export function buildStageTimelineEntries(
  routeSteps: V2RouteStepEntry[] | undefined,
  stages: Stage[],
): Array<V2RouteStepEntry | Stage> {
  if (!routeSteps?.length) {
    return stages;
  }

  const stageStatusByIndex = new Map(stages.map((stage) => [stage.stage_index, stage.chain_status]));
  return routeSteps.map((routeStep) => {
    const executionStageIndex = routeStep.execution_stage_index ?? routeStep.stage_index;
    return {
      ...routeStep,
      status: stageStatusByIndex.get(executionStageIndex) ?? routeStep.status,
    };
  });
}

export function getTargetVersionComparisonStageIndex(
  stages: Stage[],
  routeSteps: V2RouteStepEntry[] | undefined,
): number | null {
  if (routeSteps?.length) {
    const stageStatusByIndex = new Map(stages.map((stage) => [stage.stage_index, stage.chain_status]));
    const finalRouteStep = routeSteps.reduce((latest, routeStep) =>
      routeStep.route_step_index > latest.route_step_index ? routeStep : latest,
    );
    const executionStageIndex = finalRouteStep.execution_stage_index ?? finalRouteStep.stage_index;
    const finalStageStatus = stageStatusByIndex.get(executionStageIndex) ?? finalRouteStep.status;
    return finalStageStatus === "completed" ? executionStageIndex : null;
  }

  const completedStageIndexes = stages
    .filter((stage) => stage.chain_status === "completed")
    .map((stage) => stage.stage_index);
  return completedStageIndexes.length > 0 ? Math.max(...completedStageIndexes) : null;
}
export function mergeCockpitLiveRefreshResults(
  current: CockpitData,
  results: LiveRefreshResults,
): { data: CockpitData; failed: boolean } {
  const [approvalsResult, stagesResult, eventsResult, pipelineResult] = results;
  const failed = results.some((result) => result.status === "rejected");
  return {
    failed,
    data: {
      ...current,
      approvals: approvalsResult.status === "fulfilled" ? approvalsResult.value.approvals : current.approvals,
      stages: stagesResult.status === "fulfilled" ? stagesResult.value.stages : current.stages,
      events: eventsResult.status === "fulfilled" ? eventsResult.value.events : current.events,
      pipeline: pipelineResult.status === "fulfilled" ? pipelineResult.value : current.pipeline,
    },
  };
}



interface AssistantPanelContentProps {
  assistantModel: CockpitData["assistantModel"];
  messages: V2AssistantMessageResponse[];
  assistantError: string | null;
  assistantQuestion: string;
  assistantBusy: boolean;
  approvalReviewOpen: boolean;
  onQuestionChange: (value: string) => void;
  onAsk: () => void;
}

export function AssistantPanelContent({
  assistantModel,
  messages,
  assistantError,
  assistantQuestion,
  assistantBusy,
  approvalReviewOpen,
  onQuestionChange,
  onAsk,
}: AssistantPanelContentProps) {
  return (
    <section className="panel">
      <h2>Assistant</h2>
      <p className="meta">
        Model: {assistantModel?.status ?? "unavailable"} | Source: {assistantModel?.source ?? "deterministic"}
        {assistantModel?.failure_reason ? ` | Reason: ${assistantModel.failure_reason}` : ""}
        {assistantModel?.status === "live_ok" ? " | Live Azure OpenAI" : ""}
      </p>
      {assistantError && (
        <p className="assistant-error" role="alert">
          Assistant request failed: {assistantError}
        </p>
      )}
      {messages.length === 0 ? (
        <p className="meta">No messages yet. The assistant can explain status and draft instructions.</p>
      ) : (
        messages.map((m) => (
          <div key={m.message_id} className="message">
            <strong>{m.role}:</strong>
            <pre className="message-content">{m.content}</pre>
          </div>
        ))
      )}
      <div className="assistant-composer">
        <input
          aria-label="Ask assistant"
          value={assistantQuestion}
          onChange={(event) => onQuestionChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void onAsk();
          }}
          placeholder="Ask what happened so far"
        />
        <button type="button" disabled={assistantBusy || !assistantQuestion.trim()} onClick={() => void onAsk()}>
          Ask
        </button>
      </div>
      {approvalReviewOpen && (
        <p className="meta">
          Pre-transform review is open in the chatbot. Legacy Approve/Reject controls are disabled here; use the assistant to review evidence, request changes, and confirm the exact checksum.
        </p>
      )}
      <p className="meta">
        Assistant cannot execute, approve, write files, change route, or override proof.
      </p>
    </section>
  );
}

// F3 — Migration Route Panel

export function MigrationRoutePanel({ job }: { job: V2MigrationJobResponse }) {
  const sourceLabel =
    MIGRATION_PROFILE_OPTIONS.find((p) => p.id === job.source_profile)?.label ?? job.source_profile ?? "unspecified";
  const targetLabel =
    MIGRATION_PROFILE_OPTIONS.find((p) => p.id === job.target_profile)?.label ?? job.target_profile ?? "unspecified";

  return (
    <section className="panel" data-testid="migration-route-panel">
      <h2>Migration Route</h2>
      <div className="table-list">
        <div className="table-row">
          <span className="meta">Source profile</span>
          <strong data-testid="cockpit-source-profile">{sourceLabel}</strong>
        </div>
        <div className="table-row">
          <span className="meta">Target profile</span>
          <strong data-testid="cockpit-target-profile">{targetLabel}</strong>
        </div>
        {job.validation_status && (
          <div className="table-row">
            <span className="meta">Validation</span>
            <strong data-testid="cockpit-validation-status">{job.validation_status}</strong>
            {job.validation_reason && <span className="meta">{job.validation_reason}</span>}
          </div>
        )}
        {job.included_stages && job.included_stages.length > 0 && (
          <div className="table-row">
            <span className="meta">Included stages</span>
            <strong data-testid="cockpit-included-stages">{job.included_stages.join(", ")}</strong>
          </div>
        )}
        {job.skipped_stages && job.skipped_stages.length > 0 && (
          <div className="table-row">
            <span className="meta">Skipped stages</span>
            <strong data-testid="cockpit-skipped-stages">{job.skipped_stages.join(", ")}</strong>
          </div>
        )}
        {job.excluded_stages && job.excluded_stages.length > 0 && (
          <div className="table-row">
            <span className="meta">Excluded stages</span>
            <strong data-testid="cockpit-excluded-stages">{job.excluded_stages.join(", ")}</strong>
          </div>
        )}
        {job.run_configuration_id && (
          <div className="table-row">
            <span className="meta">Run config</span>
            <strong data-testid="cockpit-run-config-id">{job.run_configuration_id}</strong>
          </div>
        )}
        {job.stage_continuation_policy && (
          <div className="table-row">
            <span className="meta">Continuation policy</span>
            <strong data-testid="cockpit-continuation-policy">{job.stage_continuation_policy}</strong>
          </div>
        )}
      </div>
      <p className="meta">All route data is backend-returned. No local recomputation.</p>
    </section>
  );
}

// F4 — Source Profile Detection Panel

function tryParseDetectionArtifact(
  evidence: GateEvidencePack | null,
): Record<string, unknown> | null {
  if (!evidence?.artifacts?.length) return null;
  const detectionArtifact = evidence.artifacts.find(
    (a) => a.kind === "source_profile_detection" || a.kind === "detection",
  );
  if (!detectionArtifact?.content) return null;
  try {
    return JSON.parse(detectionArtifact.content) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function SourceProfileDetectionPanel({
  gateDetail,
}: {
  gateDetail: GateDetailResponse | null;
}) {
  const evidence = gateDetail?.evidence;
  if (!evidence || !("pack_type" in evidence)) {
    return (
      <div className="info-box" data-testid="detection-evidence-unavailable">
        <p>Source-profile detection evidence is unavailable; refresh the gate or rerun analysis.</p>
      </div>
    );
  }

  const pack = evidence as GateEvidencePack;
  const detected = tryParseDetectionArtifact(pack);

  return (
    <section className="panel" data-testid="source-profile-detection-panel">
      <h2>Source Profile Detection</h2>
      <div className="table-list">
        <div className="table-row">
          <span className="meta">Pack type</span>
          <strong>{pack.pack_type}</strong>
        </div>
        <div className="table-row">
          <span className="meta">Summary</span>
          <strong>{pack.summary || "No summary available"}</strong>
        </div>
        <div className="table-row">
          <span className="meta">Artifacts</span>
          <strong>
            {pack.resolved_artifact_count}/{pack.total_artifact_count} resolved
          </strong>
        </div>
        {pack.missing_refs.length > 0 && (
          <div className="table-row">
            <span className="meta">Missing refs</span>
            <strong className="warning-text">{pack.missing_refs.join(", ")}</strong>
          </div>
        )}
        {pack.checksum_mismatches.length > 0 && (
          <div className="table-row">
            <span className="meta">Checksum mismatches</span>
            <strong className="warning-text">{pack.checksum_mismatches.join(", ")}</strong>
          </div>
        )}
        {pack.failure_message && (
          <div className="table-row">
            <span className="meta">Failure</span>
            <strong className="warning-text">{pack.failure_message}</strong>
          </div>
        )}
        {detected && (
          <>
            {detected.detected_source_profile && (
              <div className="table-row">
                <span className="meta">Detected source profile</span>
                <strong data-testid="detected-source-profile">{String(detected.detected_source_profile)}</strong>
              </div>
            )}
            {detected.confidence != null && (
              <div className="table-row">
                <span className="meta">Confidence</span>
                <strong>{String(detected.confidence)}</strong>
              </div>
            )}
            {detected.uncertainty_notes && (
              <div className="table-row">
                <span className="meta">Uncertainty</span>
                <strong>{String(detected.uncertainty_notes)}</strong>
              </div>
            )}
          </>
        )}
      </div>
      <p className="meta">Evidence is backend-owned. Do not use parsed content as execution input.</p>
    </section>
  );
}

// F4 — Source Profile Override Form

export type SourceProfileOverrideBlockedReason =
  | "missing_target_profile"
  | "missing_detection_artifact_ref"
  | "missing_detection_artifact_checksum"
  | "missing_reason"
  | "missing_comments"
  | "gate_phase_not_analysis_review"
  | "override_action_unavailable"
  | null;

export function getSourceProfileOverrideBlockedReason(input: {
  isAnalysisReview: boolean;
  hasOverrideAction: boolean;
  hasTargetProfile: boolean;
  hasDetectionArtifactRef: boolean;
  hasExpectedChecksum: boolean;
  reason: string;
  comments: string;
}): SourceProfileOverrideBlockedReason {
  if (!input.isAnalysisReview) {
    return "gate_phase_not_analysis_review";
  }
  if (!input.hasOverrideAction) {
    return "override_action_unavailable";
  }
  if (!input.hasTargetProfile) {
    return "missing_target_profile";
  }
  if (!input.hasDetectionArtifactRef) {
    return "missing_detection_artifact_ref";
  }
  if (!input.hasExpectedChecksum) {
    return "missing_detection_artifact_checksum";
  }
  if (input.reason.trim().length === 0) {
    return "missing_reason";
  }
  if (input.comments.trim().length === 0) {
    return "missing_comments";
  }
  return null;
}

export const SOURCE_PROFILE_OVERRIDE_BLOCKED_COPY: Record<
  Exclude<SourceProfileOverrideBlockedReason, null>,
  string
> = {
  missing_target_profile: "Missing target profile from backend job state.",
  missing_detection_artifact_ref: "Missing detection artifact reference bound to this gate.",
  missing_detection_artifact_checksum: "Missing detection artifact checksum from gate state.",
  missing_reason: "Reason is required.",
  missing_comments: "Comments are required.",
  gate_phase_not_analysis_review: "Source-profile override is only available at analysis_review gates.",
  override_action_unavailable: "The override_source_profile action is not available on this gate.",
};

export const SOURCE_PROFILE_OVERRIDE_GENERIC_COPY =
  "Source-profile detection evidence is unavailable; refresh the gate or rerun analysis.";

function findDetectionArtifactRef(sourceArtifactRefs: string[]): string {
  for (const ref of sourceArtifactRefs) {
    if (typeof ref !== "string") continue;
    const trimmed = ref.trim();
    if (!trimmed) continue;
    const normalized = trimmed.replace(/\\/g, "/");
    const filename = normalized.split("/").filter(Boolean).pop() ?? "";
    if (filename.toLowerCase().includes("source_profile_detection")) {
      return trimmed;
    }
  }
  return "";
}

function findDetectedSourceProfileFromEvidence(
  evidence: GateDetailResponse["evidence"],
): string {
  if (!evidence || !("pack_type" in evidence)) return "";
  const pack = evidence as GateEvidencePack;
  const detectionArtifact = pack.artifacts?.find(
    (a) =>
      typeof a?.kind === "string" &&
      (a.kind === "source_profile_detection" ||
        a.kind === "detection" ||
        a.kind.toLowerCase().includes("source_profile_detection")),
  );
  if (!detectionArtifact?.content) return "";
  try {
    const parsed = JSON.parse(detectionArtifact.content) as Record<string, unknown>;
    const value = parsed.detected_source_profile;
    return typeof value === "string" ? value : "";
  } catch {
    return "";
  }
}

export type SourceProfileOverrideSubmitBody = {
  gate_id: string;
  job_id: string;
  action: "override_source_profile";
  expected_gate_checksum: string;
  idempotency_key: string;
  decided_by: "human";
  actor_type: "human";
  reason: string;
  comments: string;
  override_source_profile: MigrationProfileId;
  detection_artifact_ref: string;
  detected_source_profile: MigrationProfileId | undefined;
  requested_source_profile: MigrationProfileId;
  target_profile: MigrationProfileId;
  expected_detection_artifact_checksum: string;
};

export type BuildSourceProfileOverrideBodyResult = {
  body: SourceProfileOverrideSubmitBody | null;
  blockedReason: SourceProfileOverrideBlockedReason;
  detectionArtifactRef: string;
  expectedDetectionChecksum: string;
  detectedSourceProfile: MigrationProfileId | undefined;
  targetProfile: MigrationProfileId | null;
};

export type BuildSourceProfileOverrideBodyInput = {
  gate: GateRepresentation;
  jobId: string;
  job?: V2MigrationJobResponse;
  evidence: GateDetailResponse["evidence"];
  requestedProfile: MigrationProfileId;
  reason: string;
  comments: string;
  idempotencyKey: string;
  detectedSourceProfile?: MigrationProfileId;
};

export function buildSourceProfileOverrideBody(
  input: BuildSourceProfileOverrideBodyInput,
): BuildSourceProfileOverrideBodyResult {
  const detectionArtifactRef = findDetectionArtifactRef(input.gate.source_artifact_refs);
  const expectedDetectionChecksum = input.gate.source_artifact_checksum;
  const detectedFromEvidence = findDetectedSourceProfileFromEvidence(input.evidence);
  const detectedSourceProfile: MigrationProfileId | undefined =
    detectedFromEvidence &&
    MIGRATION_PROFILE_OPTIONS.find((p) => p.id === detectedFromEvidence)
      ? (detectedFromEvidence as MigrationProfileId)
      : input.detectedSourceProfile;

  const jobTarget = input.job?.target_profile;
  const targetProfile: MigrationProfileId | null = (() => {
    if (
      jobTarget &&
      MIGRATION_PROFILE_OPTIONS.find((p) => p.id === jobTarget)?.selectableAsTarget
    ) {
      return jobTarget;
    }
    if (
      detectedSourceProfile &&
      MIGRATION_PROFILE_OPTIONS.find((p) => p.id === detectedSourceProfile)?.selectableAsTarget
    ) {
      return detectedSourceProfile;
    }
    return null;
  })();

  const isAnalysisReview = input.gate.gate_phase === "analysis_review";
  const hasOverrideAction = !!input.gate.available_actions.some(
    (a) => a.action === "override_source_profile",
  );

  const blockedReason = getSourceProfileOverrideBlockedReason({
    isAnalysisReview,
    hasOverrideAction,
    hasTargetProfile: targetProfile !== null,
    hasDetectionArtifactRef: detectionArtifactRef.length > 0,
    hasExpectedChecksum: expectedDetectionChecksum.length > 0,
    reason: input.reason,
    comments: input.comments,
  });

  if (
    blockedReason !== null ||
    targetProfile === null ||
    input.gate.checksum.length === 0
  ) {
    return {
      body: null,
      blockedReason,
      detectionArtifactRef,
      expectedDetectionChecksum,
      detectedSourceProfile,
      targetProfile,
    };
  }

  return {
    body: {
      gate_id: input.gate.gate_id,
      job_id: input.jobId,
      action: "override_source_profile",
      expected_gate_checksum: input.gate.checksum,
      idempotency_key: input.idempotencyKey,
      decided_by: "human",
      actor_type: "human",
      reason: input.reason,
      comments: input.comments,
      override_source_profile: input.requestedProfile,
      detection_artifact_ref: detectionArtifactRef,
      detected_source_profile: detectedSourceProfile,
      requested_source_profile: input.requestedProfile,
      target_profile: targetProfile,
      expected_detection_artifact_checksum: expectedDetectionChecksum,
    },
    blockedReason: null,
    detectionArtifactRef,
    expectedDetectionChecksum,
    detectedSourceProfile,
    targetProfile,
  };
}

export function SourceProfileOverrideForm({
  gateDetail,
  jobId,
  job,
  onSuccess,
}: {
  gateDetail: GateDetailResponse | null;
  jobId: string;
  job?: V2MigrationJobResponse;
  onSuccess: () => void;
}) {
  const [requestedProfile, setRequestedProfile] = useState<MigrationProfileId>("springboot-2.7-java11");
  const [reason, setReason] = useState("");
  const [comments, setComments] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const gate = gateDetail?.gate;
  const evidence = gateDetail?.evidence;
  const isAnalysisReview = gate?.gate_phase === "analysis_review";
  const hasOverrideAction = !!gate?.available_actions.some(
    (a) => a.action === "override_source_profile",
  );

  const detectedFromEvidence = findDetectedSourceProfileFromEvidence(evidence ?? null);
  const targetProfile: MigrationProfileId | null = (() => {
    const jobTarget = job?.target_profile;
    if (jobTarget && (MIGRATION_PROFILE_OPTIONS.find((p) => p.id === jobTarget)?.selectableAsTarget ?? false)) {
      return jobTarget;
    }
    if (
      detectedFromEvidence &&
      (MIGRATION_PROFILE_OPTIONS.find((p) => p.id === detectedFromEvidence)?.selectableAsTarget ?? false)
    ) {
      return detectedFromEvidence as MigrationProfileId;
    }
    return null;
  })();

  const detectionArtifactRef = findDetectionArtifactRef(gate?.source_artifact_refs ?? []);

  const expectedDetectionChecksum = gate?.source_artifact_checksum ?? "";

  const requestedProfileValid =
    MIGRATION_PROFILE_OPTIONS.find((p) => p.id === requestedProfile)?.selectableAsSource ?? false;
  const detectedSourceProfile: MigrationProfileId | undefined =
    detectedFromEvidence &&
    (MIGRATION_PROFILE_OPTIONS.find((p) => p.id === detectedFromEvidence) !== undefined)
      ? (detectedFromEvidence as MigrationProfileId)
      : undefined;

  const blockedReason = getSourceProfileOverrideBlockedReason({
    isAnalysisReview,
    hasOverrideAction,
    hasTargetProfile: targetProfile !== null,
    hasDetectionArtifactRef: detectionArtifactRef.length > 0,
    hasExpectedChecksum: expectedDetectionChecksum.length > 0,
    reason,
    comments,
  });
  const canSubmit =
    blockedReason === null && gate?.checksum !== undefined && gate.checksum.length > 0 && requestedProfileValid;

  if (!isAnalysisReview || !hasOverrideAction) {
    return null;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!gate) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = buildSourceProfileOverrideBody({
        gate,
        jobId,
        job,
        evidence: evidence ?? null,
        requestedProfile,
        reason,
        comments,
        idempotencyKey: createIdempotencyKey(),
        detectedSourceProfile,
      });
      if (result.body === null) {
        setSubmitError(
          result.blockedReason !== null
            ? SOURCE_PROFILE_OVERRIDE_BLOCKED_COPY[result.blockedReason]
            : SOURCE_PROFILE_OVERRIDE_GENERIC_COPY,
        );
        return;
      }
      await postV2GateAction(jobId, gate.gate_id, result.body);
      onSuccess();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Override submission failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="panel" data-testid="source-profile-override-form">
      <h2>Override Source Profile</h2>
      <p className="meta">
        Only a human checksum-bound gate action can override source profile.
      </p>
      {!canSubmit && (
        <div className="info-box" data-testid="override-submit-disabled">
          <p data-testid="override-blocked-reason">
            {blockedReason !== null
              ? SOURCE_PROFILE_OVERRIDE_BLOCKED_COPY[blockedReason]
              : SOURCE_PROFILE_OVERRIDE_GENERIC_COPY}
          </p>
        </div>
      )}
      <form onSubmit={(e) => void handleSubmit(e)}>
        <div className="field-row">
          <label>Requested Profile *</label>
          <select
            value={requestedProfile}
            onChange={(e) => setRequestedProfile(e.target.value as MigrationProfileId)}
            data-testid="override-profile-select"
          >
            {MIGRATION_PROFILE_OPTIONS.filter((p) => p.selectableAsSource).map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field-row">
          <label>Reason *</label>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why override the detected source profile?"
            required
            aria-required="true"
            data-testid="override-reason-input"
          />
        </div>
        <div className="field-row">
          <label>Comments *</label>
          <textarea
            rows={3}
            value={comments}
            onChange={(e) => setComments(e.target.value)}
            placeholder="Required additional context for the override"
            required
            aria-required="true"
            data-testid="override-comments-input"
          />
        </div>
        {submitError && (
          <p className="error-box" role="alert" data-testid="override-submit-error">
            {submitError}
          </p>
        )}
        <button
          type="submit"
          disabled={submitting || !canSubmit}
          data-testid="override-submit-button"
        >
          {submitting ? "Submitting..." : "Submit Override"}
        </button>
      </form>
    </section>
  );
}

export function ApprovalDecisionsPanel({
  approvals,
  approvalReviewOpen,
  approvalBusy,
  approvalModeEnabled,
  approvalModeBusy,
  approvalModeError,
  onApprovalModeToggle,
  onApprove,
  onReject,
}: {
  approvals: V2ApprovalResponse[];
  approvalReviewOpen: boolean;
  approvalBusy: string | null;
  approvalModeEnabled: boolean;
  approvalModeBusy: boolean;
  approvalModeError: string | null;
  onApprovalModeToggle: (enabled: boolean) => void;
  onApprove: (card: V2ApprovalResponse) => void;
  onReject: (card: V2ApprovalResponse) => void;
}) {
  return (
    <section className="panel">
      <h2>Approval Decisions</h2>
      <div className="approval-mode-row" aria-label="Approval mode">
        <div>
          <strong>{approvalModeEnabled ? "Auto Approval ON" : "Manual"}</strong>
          <p className="meta">{approvalModeEnabled ? "Successful approval gates are approved automatically." : "Approval gates wait for manual Approve or Reject."}</p>
        </div>
        <label className="toggle-control">
          <input
            type="checkbox"
            checked={approvalModeEnabled}
            disabled={approvalModeBusy}
            onChange={(event) => onApprovalModeToggle(event.target.checked)}
          />
          <span>{approvalModeBusy ? "Updating..." : approvalModeEnabled ? "On" : "Off"}</span>
        </label>
      </div>
      {approvalModeError && <p className="warning-text" role="alert">{approvalModeError}</p>}
      {approvalReviewOpen && (
        <p className="meta">
          A pre-transform review gate is open. Approve/Reject buttons are enabled below for each pending gate; the chatbot can also confirm the exact checksum.
        </p>
      )}
      {approvals.length === 0 ? (
        <p className="meta">No pending decisions.</p>
      ) : (
        approvals.map((a) => (
          <div key={a.card_id} className="approval-card">
            <div className="stage-header">
              <strong>Stage {a.stage_index}</strong>
              <span className={`status-badge ${a.status}`}>{a.status.replace(/_/g, " ").toUpperCase()}</span>
            </div>
            <p>{a.summary}</p>
            <p className="checksum">Checksum: {a.request_checksum}</p>
            {a.status === "auto_approved" && <p className="meta">Mode: Auto Approval | Timestamp: {a.created_at}</p>}
            {a.reviewer_decision && (
              <p className="meta">
                Reviewer: {a.reviewer_decision}
                {a.reviewer_critique_id ? ` (${a.reviewer_critique_id})` : ""}
              </p>
            )}
            {a.reviewed_checksum && <p className="checksum">Reviewed checksum: {a.reviewed_checksum}</p>}
            {a.status === "pending" ? (
              <div className="approval-actions">
                <button
                  type="button"
                  disabled={approvalBusy === a.card_id}
                  onClick={() => onApprove(a)}
                >
                  Approve
                </button>
                <button
                  type="button"
                  disabled={approvalBusy === a.card_id}
                  onClick={() => onReject(a)}
                >
                  Reject
                </button>
              </div>
            ) : (
              <p className="meta">Decision recorded.</p>
            )}
          </div>
        ))
      )}
      <p className="meta">LLM cannot approve; exact checksum required.</p>
    </section>
  );
}

// ── Main Cockpit Component ──

export function MigrationCockpit({ jobId }: { jobId?: string }) {
  const router = useRouter();
  const [data, setData] = useState<CockpitData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [assistantQuestion, setAssistantQuestion] = useState("");
  const [assistantBusy, setAssistantBusy] = useState(false);
  const [assistantError, setAssistantError] = useState<string | null>(null);
  const [approvalBusy, setApprovalBusy] = useState<string | null>(null);
  const [approvalModeBusy, setApprovalModeBusy] = useState(false);
  const [approvalModeError, setApprovalModeError] = useState<string | null>(null);
  const [streamState, setStreamState] = useState<"connecting" | "connected" | "reconnecting">("connecting");
  const [liveRefreshWarning, setLiveRefreshWarning] = useState<string | null>(null);
  const [repairRefreshKey, setRepairRefreshKey] = useState(0);
  const [targetVersionRefreshKey, setTargetVersionRefreshKey] = useState(0);
  const [gateState, setGateState] = useState<GatePanelState>({ status: "loading" });
  const [report, setReport] = useState<V2FinalReportResponse | null>(null);
  const [reportBusy, setReportBusy] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const normalizedJobId = jobId?.trim() ?? "";
  const approvalReviewOpen = gateState.status === "success" && gateState.openGate?.gate_phase === "approval_review";

  useEffect(() => {
    if (!normalizedJobId) {
      setData(null);
      setError("Migration job id is missing from the route.");
      return;
    }

    let cancelled = false;
    async function loadCockpit() {
      try {
        const safeJobId = requireJobId(normalizedJobId);
        const [job, messagesResponse, approvalsResponse, stagesResponse, eventsResponse, pipelineResponse] = await Promise.all([
          getV2MigrationJob(safeJobId),
          getV2AssistantMessages(safeJobId),
          getV2JobApprovals(safeJobId),
          getV2MigrationJobStages(safeJobId),
          getV2JobEventSnapshot(safeJobId),
          getV2JobPipeline(safeJobId),
        ]);

        if (cancelled) return;

        setData({
          job,
          stages: stagesResponse.stages,
          approvals: approvalsResponse.approvals,
          messages: messagesResponse.messages,
          events: eventsResponse.events,
          pipeline: pipelineResponse,
          assistantModel: null,
        });
        setError(null);
        void refreshReport();
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load cockpit");
        }
      }
    }
    loadCockpit();
    return () => { cancelled = true; };
  }, [normalizedJobId]);

  async function refreshReport() {
    if (!normalizedJobId) return;
    try {
      setReport(await getV2FinalReport(normalizedJobId));
    } catch {
    }
  }

  async function handleGenerateReport() {
    if (!normalizedJobId || !report?.eligible) return;
    setReportBusy(true);
    try {
      setReport(await generateV2FinalReport(normalizedJobId));
    } finally {
      setReportBusy(false);
    }
  }

  useEffect(() => {
    if (!normalizedJobId) {
      setGateState({ status: "loading" });
      return;
    }

    let cancelled = false;
    async function loadGateState() {
      try {
        const safeJobId = requireJobId(normalizedJobId);
        const [gateList, openGateResponse] = await Promise.all([
          getV2JobGates(safeJobId),
          getV2OpenGate(safeJobId),
        ]);
        if (cancelled) return;
        const openGate = openGateResponse.gate ?? null;
        const openGateDetail = openGate
          ? await getV2GateDetail(safeJobId, openGate.gate_id).catch(() => null)
          : null;
        if (cancelled) return;
        setGateState({
          status: gateList.gates.length === 0 ? "empty" : "success",
          gates: gateList.gates,
          openGate,
          openGateDetail,
        });
      } catch (e) {
        if (!cancelled) {
          setGateState({
            status: "error",
            message: e instanceof Error ? e.message : "Failed to load gate state.",
          });
        }
      }
    }
    loadGateState();
    return () => {
      cancelled = true;
    };
  }, [normalizedJobId]);

  useEffect(() => {
    if (!normalizedJobId || typeof EventSource === "undefined") return;
    let source: EventSource | null = null;
    try {
      source = new EventSource(v2EventStreamUrl(normalizedJobId, 0));
    } catch {
      setStreamState("reconnecting");
      return;
    }

    source.onopen = () => setStreamState("connected");
    source.onerror = () => setStreamState("reconnecting");
    source.onmessage = (event) => appendEventFromSse(event.data);
    for (const type of [
      "job_created",
      "stage_queued",
      "stage_started",
      "command_started",
      "process_started",
      "stdout",
      "stderr",
      "analysis_started",
      "analysis_completed",
      "planning_started",
      "planning_completed",
      "assessment_started",
      "assessment_completed",
      "approval_blocked",
      "approval_mode_updated",
      "approval_required",
      "approval_auto_approved",
      "stage_blocked_for_approval",
      "sandbox_transform_started",
      "sandbox_transform_completed",
      "final_report_started",
      "final_report_completed",
      "artifact_written",
      "stage_completed",
      "migration_completed",
      "stage_failed",
      "next_stage_queued",
      "job_completed",
      "proof_updated",
      "approval_resume_queued",
      "resume_started",
      "ai_diagnosis_created",
      "pom_summary_created",
      "repair_proposal_revised",
      "reviewer_critique_created",
      "repair_patch_gate_completed",
      "repair_patch_applied",
      "repair_validation_completed",
      "repair_rollback_completed",
      "model_invocation_started",
      "model_invocation_completed",
      "model_invocation_failed",
      "result_contract_failed",
      "migration_cancelling",
      "stage_cancelled",
      "migration_cancelled",
      "pom_change_proposed",
      "pom_change_applied",
      "pom_validation_started",
      "pom_validation_passed",
      "pom_validation_failed",
      "pom_repair_plan_created",
      "pom_change_rolled_back",
      "repair_proposal_ready",
      "repair_proposer_completed",
      "repair_proposer_unusable",
      "repair_reviewer_completed",
      "repair_reviewer_unusable",
      "repair_cycle_started",
      "repair_generation_failed",
      "repair_final_diff_selected",
      "next_repair_cycle_started",
      "migration_continuation_queued",
      "repair_outcome_persisted",
      "repair_apply_started",
      "repair_apply_failed",
      "repair_validation_started",
      "reviewed_repair_unavailable",
      "repair_callback_error",
      "repair_attempts_exhausted",
      "repair_validation_failed",
      "repair_validation_passed",
      "target_version_change_applied",
      "target_version_validation_queued",
      "target_version_validation_started",
      "target_version_build_started",
      "target_version_build_passed",
      "target_version_build_failed",
      "target_version_tests_started",
      "target_version_test_blocked",
      "target_version_tests_passed",
      "target_version_tests_failed",
      "target_version_validation_passed",
      "target_version_validation_failed",
      "target_version_repair_required",
      "target_version_repair_exhausted",
      "target_version_update_validated",
    ]) {
      source.addEventListener(type, (event) => {
        appendEventFromSse((event as MessageEvent).data);
      });
    }

    return () => {
      source?.close();
    };
  }, [normalizedJobId]);

  function appendEventFromSse(dataText: string) {
    try {
      const event = JSON.parse(dataText) as V2JobEvent;
      logApprovalEvent(event);
      if (event.type === "approval_mode_updated") {
        const enabled = Boolean(event.payload?.auto_approval_enabled);
        setData((current) => current ? {
          ...current,
          job: { ...current.job, auto_approval_enabled: enabled },
        } : current);
      }
      setData((current) => {
        if (!current || current.events.some((existing) => existing.sequence === event.sequence)) {
          return current;
        }
        const updatedEvents = [...current.events, event].sort((a, b) => a.sequence - b.sequence);
        const updatedStages = reduceAllStageStatuses(current.stages, updatedEvents);
        return {
          ...current,
          events: updatedEvents,
          stages: updatedStages,
        };
      });
      if (IMPORTANT_SSE_TYPES.has(event.type)) {
        void refreshLiveState().catch(() => {
          setLiveRefreshWarning("Live refresh temporarily failed. Retrying...");
        });
        void refreshGateState().catch(() => {
        });
      }
      if (AMF252_REPAIR_EVENTS.has(event.type)) {
        setRepairRefreshKey((k) => k + 1);
      }
      if (TARGET_VERSION_EVENTS.has(event.type)) {
        setTargetVersionRefreshKey((k) => k + 1);
      }
    } catch {
      setStreamState("reconnecting");
    }
  }

  async function handleCancelMigration() {
    if (!normalizedJobId || cancelBusy) return;
    setCancelBusy(true);
    setCancelError(null);
    try {
      await cancelV2MigrationJob(normalizedJobId);
      setStreamState("reconnecting");
      router.push("/migrations/new");
    } catch (e) {
      setCancelError(e instanceof Error ? e.message : "Cancel migration failed");
    } finally {
      setCancelBusy(false);
    }
  }

  async function updateApprovalMode(nextEnabled: boolean) {
    if (!normalizedJobId || approvalModeBusy) return;
    if (nextEnabled) {
      const confirmed = window.confirm(
        "Auto Approval will automatically approve future successful analysis/planning/assessment gates for this migration job. You can turn it off at any time. Failed or unsafe gates will not be auto-approved. Do you want to enable it?"
      );
      if (!confirmed) return;
    }
    setApprovalModeBusy(true);
    setApprovalModeError(null);
    try {
      const response = await updateV2ApprovalMode(normalizedJobId, nextEnabled);
      setData((current) => {
        if (!current) return current;
        return {
          ...current,
          job: response.job ?? {
            ...current.job,
            auto_approval_enabled: response.auto_approval_enabled,
          },
        };
      });
      if (response.auto_approved) {
        console.log("[approval-mode-auto-approved]", {
          jobId: normalizedJobId,
          gateId: response.auto_approved.gate_id,
          stageId: response.auto_approved.stage_index,
          decisionSource: "auto_approval",
        });
        void refreshLiveState().catch(() => {
          setLiveRefreshWarning("Live refresh temporarily failed. Retrying...");
        });
        void refreshGateState().catch(() => {
        });
      }
    } catch (e) {
      setApprovalModeError(
        e instanceof Error
          ? `Could not update approval mode. Please check backend connection or CORS configuration. ${e.message}`
          : "Could not update approval mode. Please check backend connection or CORS configuration."
      );
    } finally {
      setApprovalModeBusy(false);
    }
  }

  async function askAssistant(questionOverride?: string) {
    const question = (questionOverride ?? assistantQuestion).trim();
    if (!question || !normalizedJobId) return;
    setAssistantBusy(true);
    setAssistantError(null);
    try {
      const response = await askV2Assistant(normalizedJobId, question);
      const isBusy = response.assistant_message
        && !response.assistant_message.message_id
        && response.assistant_message.content === "The orchestrator is busy right now. Retry shortly.";
      if (isBusy) {
        setAssistantError("database is locked");
        setAssistantQuestion(questionOverride ?? "");
      } else {
        setData((current) => {
          if (!current) return current;
          return {
            ...current,
            messages: [
              ...current.messages,
              response.user_message,
              response.assistant_message,
            ],
            assistantModel: response.model,
          };
        });
        setAssistantQuestion("");
      }
    } catch (e) {
      setAssistantError(e instanceof Error ? e.message : "Assistant request failed");
    } finally {
      setAssistantBusy(false);
    }
  }

  async function refreshLiveState() {
    if (!normalizedJobId) return;
    const safeJobId = requireJobId(normalizedJobId);
    const [approvalsResult, stagesResult, eventsResult, pipelineResult] = await Promise.allSettled([
      getV2JobApprovals(safeJobId),
      getV2MigrationJobStages(safeJobId),
      getV2JobEventSnapshot(safeJobId),
      getV2JobPipeline(safeJobId),
    ]) as LiveRefreshResults;
    const failed = [approvalsResult, stagesResult, eventsResult, pipelineResult]
      .some((result) => result.status === "rejected");
    setLiveRefreshWarning(failed ? "Live refresh temporarily failed. Retrying..." : null);
    setData((current) => {
      if (!current) return current;
      logApprovalDecisionsBefore(current.approvals);
      const merged = mergeCockpitLiveRefreshResults(current, [
        approvalsResult,
        stagesResult,
        eventsResult,
        pipelineResult,
      ]);
      logApprovalDecisionsAfter(merged.data.approvals);
      return merged.data;
    });
  }

  async function refreshGateState() {
    if (!normalizedJobId) return;
    const safeJobId = requireJobId(normalizedJobId);
    try {
      const [gateList, openGateResponse] = await Promise.all([
        getV2JobGates(safeJobId),
        getV2OpenGate(safeJobId),
      ]);
      const openGate = openGateResponse.gate ?? null;
      const openGateDetail = openGate
        ? await getV2GateDetail(safeJobId, openGate.gate_id).catch(() => null)
        : null;
      setGateState({
        status: gateList.gates.length === 0 ? "empty" : "success",
        gates: gateList.gates,
        openGate,
        openGateDetail,
      });
      logOpenGates({ openGate, gateCount: gateList.gates.length });
    } catch {
    }
  }

  async function approveCard(card: V2ApprovalResponse) {
    if (!normalizedJobId) return;
    setApprovalBusy(card.card_id);
    const payload = {
      jobId: normalizedJobId,
      cardId: card.card_id,
      stageId: card.stage_index,
      checksum: card.request_checksum,
      decision: "approve",
    };
    logApproveClickPayload(payload);
    try {
      await approveV2Card(normalizedJobId, card.card_id, card.request_checksum);
      await refreshLiveState();
      await refreshGateState();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Approval failed");
    } finally {
      setApprovalBusy(null);
    }
  }

  async function rejectCard(card: V2ApprovalResponse) {
    if (!normalizedJobId) return;
    setApprovalBusy(card.card_id);
    try {
      await rejectV2Card(normalizedJobId, card.card_id);
      await refreshLiveState();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Rejection failed");
    } finally {
      setApprovalBusy(null);
    }
  }

  const handleCopyJobId = useCallback(async () => {
    if (!data?.job?.job_id) return;
    try {
      await navigator.clipboard.writeText(data.job.job_id);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
    }
  }, [data]);

  if (error) return <div className={styles.cockpitPage}><div className="error-box">{error}</div></div>;
  if (!data) return <div className={styles.cockpitPage}><div className="info-box">Loading cockpit...</div></div>;

  const stageTimelineEntries = buildStageTimelineEntries(data.job.route_steps, data.stages);
  const targetVersionComparisonStageIndex = getTargetVersionComparisonStageIndex(data.stages, data.job.route_steps);

  // Pipeline counts
  const passedCount = data.pipeline.rows.filter((r) => r.status === "pass").length;
  const totalCount = data.pipeline.rows.length;

  const hasOpenGate = gateState.status === "success" && gateState.openGate != null;

  const sourceLabel =
    MIGRATION_PROFILE_OPTIONS.find((p) => p.id === data.job.source_profile)?.label ?? data.job.source_profile ?? "Source";
  const targetLabel =
    MIGRATION_PROFILE_OPTIONS.find((p) => p.id === data.job.target_profile)?.label ?? data.job.target_profile ?? "Target";

  return (
    <div className={styles.cockpitPage}>
      {/* Job Header */}
      <section className={styles.jobHeader}>
        <div>
          <h1>Spring Boot migration</h1>
          <p className={styles.jobHeaderDesc}>
            Current execution, route context, backend-owned evidence, approvals, and proof.
          </p>
        </div>
        <div className={styles.jobHeaderActions}>
          <div className={styles.jobIdPill} title={data.job.job_id}>
            <span className={styles.jobIdValue}>{data.job.job_id}</span>
            <button
              type="button"
              className={styles.iconButton}
              onClick={handleCopyJobId}
              aria-label={copied ? "Job ID copied" : "Copy job ID"}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                <rect x="9" y="9" width="10" height="10" rx="2" stroke="currentColor" strokeWidth="1.8"/>
                <path d="M6 15H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
              </svg>
            </button>
            {copied && <span style={{ fontSize: 8.5, color: "#137847" }}>Copied</span>}
          </div>
          <span className={`${styles.status} ${styles.statusRunning}`}>
            {streamState}
          </span>
          <button
            type="button"
            className={styles.buttonDanger}
            onClick={() => setCancelOpen(true)}
          >
            Cancel Migration
          </button>
        </div>
      </section>

      {/* Command Deck */}
      <section className={styles.commandDeck} aria-label="Current migration command deck">
        <div className={styles.commandDeckBody}>
          <div className={styles.currentStage}>
            <div className={styles.currentStageTop}>
              <CurrentExecutionSummary
                routeEntries={stageTimelineEntries}
                activeStageIndex={data.pipeline.active_stage_index}
                pipelineRows={data.pipeline.rows}
                sourceProfile={sourceLabel}
                targetProfile={targetLabel}
                streamState={streamState}
              />
              <div className={styles.currentStageTop}>
                {liveRefreshWarning && (
                  <span style={{ color: "#f5a623", fontSize: 8.8, marginTop: 4, display: "block" }}>
                    {liveRefreshWarning}
                  </span>
                )}
                <button
                  type="button"
                  className={styles.deckButton}
                  onClick={() => setEvidenceOpen((v) => !v)}
                  aria-expanded={evidenceOpen}
                  aria-controls="evidence-drawer"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M5 5h14M5 9h14M5 13h8M5 17h11" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
                  </svg>
                  <span>Evidence &amp; logs</span>
                  <span className={styles.deckButtonCount}>{data.pipeline.evidence.length}</span>
                </button>
              </div>
            </div>
          </div>

          <div className={styles.commandFacts}>
            <div className={styles.commandFact}>
              <small>Continuation</small>
              <strong className="mono">{data.job.stage_continuation_policy || "—"}</strong>
              <span>Continue on pass</span>
            </div>
            <div className={styles.commandFact}>
              <small>Approval mode</small>
              <strong>{data.job.auto_approval_enabled ? "Auto approval ON" : "Manual"}</strong>
              <span>{data.job.auto_approval_enabled ? "Gates auto-approved" : "Requires manual approval"}</span>
            </div>
            <div className={styles.commandFact}>
              <small>Route validation</small>
              <strong>{data.job.validation_status || "—"}</strong>
              <span>{data.job.validation_reason || "—"}</span>
            </div>
          </div>
        </div>
      </section>

      {/* Main Dashboard */}
      <div className={styles.dashboard}>
        {/* Execution Control Panel */}
        <section className={styles.panel}>
          <div className={styles.panelHeader}>
            <div>
              <h2>Execution control</h2>
              <p>Route completion, validation results, and all backend pipeline phases in one synchronized workspace.</p>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap", justifyContent: "flex-end" }}>
              <span className={styles.countBadge}>Route: {data.job.route_steps?.length ?? 0} steps</span>
              <span className={styles.countBadge}>{passedCount} / {totalCount} phases passed</span>
            </div>
          </div>
          <div className={styles.panelBody}>
            <div className={styles.executionCommandGrid}>
              {/* Timeline */}
              <section className={styles.executionPane}>
                <div className={styles.executionPaneHead}>
                  <strong>Stage Timeline</strong>
                </div>
                <div className={styles.executionPaneBody}>
                  <div className={styles.timelineRail}>
                    <div className={styles.timeline}>
                      {stageTimelineEntries.map((entry, i) => {
                        if ("route_step_index" in entry) {
                          const routeStep = entry as V2RouteStepEntry;
                          const stepStatus = routeStep.status;
                          const isCurrent = stepStatus === "running" || stepStatus === "blocked";
                          const isDone = stepStatus === "completed";
                          const isFailed = stepStatus === "failed";
                          const isPending = !isDone && !isCurrent && !isFailed;

                          let iconClass = styles.timelineRowIconPending;
                          if (isDone) iconClass = styles.timelineRowIconDone;
                          else if (isFailed) iconClass = styles.timelineRowIconFailed;
                          else if (isCurrent) iconClass = styles.timelineRowIconDone;

                          if (isCurrent) {
                            return (
                              <article key={routeStep.route_step_index} className={`${styles.timelineRow} ${styles.timelineRowCurrent}`}>
                                <div className={styles.timelineRowTop}>
                                  <div>
                                    <h3>Route step {routeStep.route_step_index + 1}: {routeStep.source_profile} &rarr; {routeStep.target_profile}</h3>
                                    <div className={styles.inlineTags}>
                                      <span className={styles.inlineTag}>{formatRouteStepStatusLabel(stepStatus)}</span>
                                      <span className={styles.inlineTag}>stream {streamState}</span>
                                    </div>
                                  </div>
                                  <span className={`${styles.status} ${isFailed ? styles.statusFailed : isDone ? styles.statusDone : styles.statusRunning}`}>
                                    {formatRouteStepStatusLabel(stepStatus)}
                                  </span>
                                </div>
                                <div className={styles.stageDetailGrid}>
                                  <div className={styles.dataBox}>
                                    <small>Runtime profile</small>
                                    <strong>{routeStep.runtime_profile || "—"}</strong>
                                  </div>
                                  <div className={styles.dataBox}>
                                    <small>Catalog</small>
                                    <strong>{routeStep.catalog || "—"}</strong>
                                  </div>
                                  <div className={styles.dataBox}>
                                    <small>Execution JDK</small>
                                    <strong>{routeStep.execution_jdk || "—"}</strong>
                                  </div>
                                  {routeStep.approval_gate_id && (
                                    <div className={styles.dataBox}>
                                      <small>Approval gate</small>
                                      <strong>{routeStep.approval_gate_id}</strong>
                                    </div>
                                  )}
                                </div>
                                <div className={styles.inlineTags}>
                                  <span className={styles.inlineTag}>Artifacts: {routeStep.artifact_refs.length > 0 ? routeStep.artifact_refs.join(", ") : "None yet"}</span>
                                  <span className={styles.inlineTag}>Evidence: {routeStep.evidence_refs.length > 0 ? routeStep.evidence_refs.join(", ") : "None yet"}</span>
                                </div>
                              </article>
                            );
                          }

                          return (
                            <article key={routeStep.route_step_index} className={`${styles.timelineRow} ${styles.timelineRowCompact}`}>
                              <span className={`${styles.timelineRowIcon} ${iconClass}`}>
                                {isDone ? "\u2713" : isFailed ? "!" : "\u2026"}
                              </span>
                              <div className={styles.timelineRowTitle}>
                                Route step {routeStep.route_step_index + 1}: {routeStep.source_profile} &rarr; {routeStep.target_profile}
                              </div>
                              <div style={{ color: "#667085", fontSize: 9 }}>{formatRouteStepStatusLabel(stepStatus)}</div>
                            </article>
                          );
                        }

                        // Fallback stage mode
                        const stage = entry as Stage;
                        const stageStatus = stage.chain_status;
                        const isCurrent = stageStatus === "running" || stageStatus === "blocked";
                        const isDone = stageStatus === "completed";
                        const isFailed = stageStatus === "failed";
                        const isSkipped = data.job.skipped_stages?.includes(String(stage.stage_index));
                        const isExcluded = data.job.excluded_stages?.includes(String(stage.stage_index));

                        let iconClass = styles.timelineRowIconPending;
                        if (isDone) iconClass = styles.timelineRowIconDone;
                        else if (isFailed) iconClass = styles.timelineRowIconFailed;

                        return (
                          <article key={stage.stage_index} className={`${styles.timelineRow} ${styles.timelineRowCompact}`}>
                            <span className={`${styles.timelineRowIcon} ${iconClass}`}>
                              {isDone ? "\u2713" : isFailed ? "!" : "\u2026"}
                            </span>
                            <div className={styles.timelineRowTitle}>
                              {stage.pipeline_stage}{isSkipped ? " (skipped)" : ""}{isExcluded ? " (excluded)" : ""}
                            </div>
                            <div style={{ color: "#667085", fontSize: 9 }}>
                              {formatStageStatusLabel(stageStatus)}
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </section>

              <PipelineStatusList rows={data.pipeline.rows} streamState={streamState} />
            </div>

            {/* Execution Context */}
            <div className={styles.executionContextGrid}>
              {/* Route Context */}
              <section className={styles.executionContextCard}>
                <div className={styles.contextCardLabel}>
                  <span>Route context</span>
                  <span className="mono" style={{ fontSize: 7.8 }}>backend</span>
                </div>
                <div className={styles.contextRoute}>
                  <div className={styles.contextRouteNode}>
                    <small>Source</small>
                    <strong>{sourceLabel}</strong>
                  </div>
                  <div className={styles.contextRouteArrow}>&rarr;</div>
                  <div className={styles.contextRouteNode}>
                    <small>Target</small>
                    <strong>{targetLabel}</strong>
                  </div>
                </div>
                <div className={styles.contextCardSubtext} style={{ marginTop: 7 }}>
                  {data.job.run_configuration_id && (
                    <span className="mono">{data.job.run_configuration_id} &middot; </span>
                  )}
                  {data.job.included_stages?.length ? `included stages ${data.job.included_stages.join(", ")}` : ""}
                </div>
              </section>

              {/* Gate & Approval */}
              <section className={styles.executionContextCard}>
                <div className={styles.contextCardLabel}>
                  <span>Gate &amp; approval</span>
                  <span className={`${styles.status} ${hasOpenGate ? styles.statusPending : styles.statusDone}`}>
                    {hasOpenGate ? "Open" : "Clear"}
                  </span>
                </div>
                <div className={styles.contextCardValue}>
                  {hasOpenGate
                    ? `${gateState.openGate!.gate_phase.replace(/_/g, " ")} (Stage ${gateState.openGate!.stage_index})`
                    : gateState.status === "loading"
                      ? "Loading..."
                      : gateState.status === "error"
                        ? "Failed to load"
                        : "No gate is currently open"}
                </div>
                <div className={styles.contextCardSubtext}>
                  {(hasOpenGate && gateState.openGate) ? `Checksum: ${gateState.openGate.checksum.slice(0, 16)}...` : "Gate data is backend-owned and checksum-protected."}
                </div>
              </section>

              {/* Attention */}
              <section className={styles.executionContextCard}>
                <div className={styles.contextCardLabel}>
                  <span>Attention</span>
                  <span className={`${styles.status} ${styles.statusNeutral}`}>No action</span>
                </div>
                <div className={styles.attentionList}>
                  <div className={styles.attentionRow}>
                    <span>Stream state</span>
                    <strong className="mono">{streamState}</strong>
                  </div>
                  <div className={styles.attentionRow}>
                    <span>Pipeline phases</span>
                    <strong>{passedCount}/{totalCount} passed</strong>
                  </div>
                  <div className={styles.attentionRow}>
                    <span>Evidence</span>
                    <strong>{data.pipeline.evidence.length} events</strong>
                  </div>
                </div>
              </section>
            </div>
          </div>
        </section>

        {/* Job Details Tabs */}
        <JobDetailsTabs
          approvalChildren={
            <>
              {gateState.status === "success" && gateState.openGate?.gate_phase === "analysis_review" && (
                <div className="info-box" style={{ marginBottom: 10 }}>
                  <SourceProfileDetectionPanel gateDetail={gateState.openGateDetail} />
                  {normalizedJobId && (
                    <SourceProfileOverrideForm
                      gateDetail={gateState.openGateDetail}
                      jobId={normalizedJobId}
                      job={data?.job}
                      onSuccess={() => { void refreshGateState(); }}
                    />
                  )}
                </div>
              )}
              <ApprovalDecisionsPanel
                approvals={data.approvals}
                approvalReviewOpen={approvalReviewOpen}
                approvalBusy={approvalBusy}
                approvalModeEnabled={Boolean(data.job.auto_approval_enabled)}
                approvalModeBusy={approvalModeBusy}
                approvalModeError={approvalModeError}
                onApprovalModeToggle={(enabled) => void updateApprovalMode(enabled)}
                onApprove={(card) => void approveCard(card)}
                onReject={(card) => void rejectCard(card)}
              />
            </>
          }
          repairChildren={
            (() => {
              // Preserve source pattern for test compatibility: normalizedJobId &&
              if (!normalizedJobId) return <div className="quiet-empty" style={{ margin: "12px 0" }}>No job selected.</div>;
              return <RepairProposalPanel jobId={normalizedJobId} repairRefreshKey={repairRefreshKey} onContinuationRefresh={refreshLiveState} />;
            })()
          }
          reportsChildren={
            <>
              <div className="info-box" style={{ marginBottom: 10 }}>
                <strong>Proof &amp; Report</strong>
                <p className="meta" style={{ marginTop: 4 }}>Final proof report generated when all three deterministic gates pass.</p>
              </div>
              <section className="panel" style={{ border: "1px solid #dce2e9", padding: 14, borderRadius: 8 }}>
                <h2>Final Report</h2>
                {report && report.blockers.length > 0 && report.blockers.map((blocker) => (
                  <p className="warning-text" key={blocker}>{blocker}</p>
                ))}
                {report && !report.eligible && (
                  <p className="meta">Report generation not yet available for this job.</p>
                )}
                <button
                  type="button"
                  disabled={reportBusy || !report?.eligible}
                  onClick={() => void handleGenerateReport()}
                >
                  {report?.status === "generated" ? "Regenerate report" : "Generate report"}
                </button>
                {reportBusy && <span className="meta"> Generating...</span>}
                {report?.artifacts.map((artifact) => (
                  <div key={artifact.artifact_id} className="report-artifact-row">
                    <span className="meta">{artifact.kind}</span>
                    <span className="checksum">{artifact.checksum_sha256.slice(0, 16)}...</span>
                    <a
                      href={resolveReportDownloadUrl(artifact.download_url)}
                      download
                    >
                      Download
                    </a>
                  </div>
                ))}
                {!report && <p className="meta">Report status unavailable.</p>}
              </section>
            </>
          }
          dependenciesChildren={
            <Stage4TargetVersionComparison
              jobId={normalizedJobId || jobId || ""}
              comparisonAvailable={targetVersionComparisonStageIndex != null}
              rootPomStageIndex={targetVersionComparisonStageIndex ?? 1}
              refreshKey={targetVersionRefreshKey}
            />
          }
        />
      </div>

      {/* Evidence Drawer */}
      <EvidenceDrawer
        open={evidenceOpen}
        evidence={data.pipeline.evidence}
        rawLogs={data.pipeline.raw_logs}
        streamState={streamState}
        activeStageIndex={data.pipeline.active_stage_index}
        onClose={() => setEvidenceOpen(false)}
      />

      {/* Floating Assistant */}
      <FloatingMigrationAssistant
        assistantModel={data.assistantModel}
        messages={data.messages}
        assistantError={assistantError}
        assistantQuestion={assistantQuestion}
        assistantBusy={assistantBusy}
        approvalReviewOpen={approvalReviewOpen}
        onQuestionChange={setAssistantQuestion}
        onAsk={() => void askAssistant()}
        onRetry={(q) => { setAssistantQuestion(q); void askAssistant(q); }}
      />

      {/* Cancel Dialog */}
      <CancelMigrationDialog
        open={cancelOpen}
        cancelBusy={cancelBusy}
        cancelError={cancelError}
        onConfirm={() => void handleCancelMigration()}
        onClose={() => setCancelOpen(false)}
      />
      {cancelBusy && <span className="meta">Cancelling...</span>}

      <style>{`
        .cockpit-layout > .repair-workspace { grid-column: 1 / -1; }
        .mono { font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; }
      `}</style>
    </div>
  );
}

// Stage status helpers

function reduceAllStageStatuses(stages: Stage[], allEvents: V2JobEvent[]): Stage[] {
  return stages.map((stage) => {
    const stageEvents = allEvents
      .filter((event) => eventAppliesToStage(event, stage.stage_index))
      .sort((a, b) => a.sequence - b.sequence);
    return { ...stage, chain_status: reduceStageStatus(stageEvents, stage.stage_index) };
  });
}

function eventAppliesToStage(event: V2JobEvent, stageIndex: number): boolean {
  if (event.stage === stageIndex) {
    return true;
  }
  if (!["next_stage_queued", "migration_completed", "job_completed"].includes(event.type)) {
    return false;
  }

  const payload = event.payload ?? {};
  const fromStage = Number(payload.from_stage ?? 0);
  const toStage = Number(payload.to_stage ?? event.stage ?? 0);
  return fromStage === stageIndex || toStage === stageIndex;
}

export function stageStatusFromEvent(event: V2JobEvent): string {
  if (event.type === "stage_cancelled" || event.status === "cancelled") return "cancelled";
  if (event.type === "stage_failed" || event.status === "failed") return "failed";
  if (["stage_completed", "migration_completed", "job_completed"].includes(event.type)) return "completed";
  if (["stage_started", "command_started", "sandbox_transform_started",
       "sandbox_transform_completed", "resume_started", "approval_resume_queued",
       "approval_completed", "build_started", "test_started"].includes(event.type) || event.status === "running") {
    return "running";
  }
  if (event.type === "approval_auto_approved") return "completed";
  if (event.type === "approval_required" || event.type === "stage_blocked_for_approval" || event.status === "blocked") return "blocked";
  if (["stage_queued", "next_stage_queued"].includes(event.type) || event.status === "queued") return "queued";
  return "pending";
}

export function transitionStageStatus(current: string, mapped: string): string {
  if (current === "cancelled") return "cancelled";
  if (mapped === "cancelled") return "cancelled";
  if (mapped === "failed") return "failed";
  if (mapped === "completed") return "completed";
  if (current === "completed") return "completed";
  if (mapped === "running") return "running";
  if (mapped === "blocked") {
    if (current === "running" || current === "completed" || current === "failed") return current;
    return "blocked";
  }
  if (mapped === "queued") {
    if (current === "running" || current === "completed" || current === "failed" || current === "blocked") return current;
    return "queued";
  }
  return current;
}

export function formatStageStatusLabel(status: string): string {
  return status.replace(/_/g, " ").toUpperCase();
}

export function reduceStageStatus(events: V2JobEvent[], stageIndex?: number): string {
  let current = "pending";
  for (const event of events) {
    if (event.type.startsWith("target_version_")) continue;
    if (event.type === "next_stage_queued" && stageIndex != null) {
      const payload = event.payload ?? {};
      const fromStage = Number(payload.from_stage ?? 0);
      const toStage = Number(payload.to_stage ?? event.stage ?? 0);
      if (fromStage === stageIndex) {
        current = transitionStageStatus(current, "completed");
        continue;
      }
      if (toStage === stageIndex) {
        current = transitionStageStatus(current, "queued");
        continue;
      }
    }
    current = transitionStageStatus(current, stageStatusFromEvent(event));
  }
  return current;
}

const IMPORTANT_SSE_TYPES = new Set([
  "approval_mode_updated",
  "approval_required",
  "approval_auto_approved",
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
  "migration_completed",
  "job_completed",
  "model_invocation_completed",
  "model_invocation_failed",
  "transform_failed",
  "build_failed",
  "repair_started",
  "repair_fallback_generated",
  "ai_diagnosis_created",
  "pom_summary_created",
  "repair_proposal_revised",
  "reviewer_critique_created",
  "repair_patch_gate_completed",
  "repair_patch_applied",
  "repair_validation_completed",
  "repair_rollback_completed",
  "proof_updated",
  "next_stage_queued",
  "result_contract_failed",
  "pom_change_applied",
  "pom_validation_passed",
  "pom_validation_failed",
  "pom_repair_plan_created",
  "pom_change_rolled_back",
  "repair_proposal_ready",
  "repair_cycle_started",
  "repair_proposer_completed",
  "repair_proposer_unusable",
  "repair_reviewer_completed",
  "repair_reviewer_unusable",
  "repair_generation_failed",
  "repair_final_diff_selected",
  "next_repair_cycle_started",
  "migration_continuation_queued",
  "repair_outcome_persisted",
  "repair_apply_started",
  "repair_apply_failed",
  "repair_validation_started",
  "reviewed_repair_unavailable",
  "repair_callback_error",
  "repair_attempts_exhausted",
  "repair_validation_failed",
  "repair_validation_passed",
  "repair_completed",
  "target_version_change_applied",
  "target_version_validation_queued",
  "target_version_validation_started",
  "target_version_build_started",
  "target_version_build_passed",
  "target_version_build_failed",
  "target_version_tests_started",
  "target_version_test_blocked",
  "target_version_tests_passed",
  "target_version_tests_failed",
  "target_version_validation_passed",
  "target_version_validation_failed",
  "target_version_repair_required",
  "target_version_repair_exhausted",
  "target_version_update_validated",
]);

const AMF252_REPAIR_EVENTS = new Set([
  "repair_proposal_ready",
  "repair_cycle_started",
  "repair_proposer_completed",
  "repair_proposer_unusable",
  "repair_reviewer_completed",
  "repair_reviewer_unusable",
  "repair_generation_failed",
  "repair_final_diff_selected",
  "next_repair_cycle_started",
  "migration_continuation_queued",
  "repair_outcome_persisted",
  "repair_apply_started",
  "repair_apply_failed",
  "repair_validation_started",
  "reviewed_repair_unavailable",
  "repair_callback_error",
  "repair_attempts_exhausted",
  "repair_validation_failed",
  "repair_validation_passed",
  "repair_completed",
]);

const TARGET_VERSION_EVENTS = new Set([
  "target_version_change_applied",
  "target_version_validation_queued",
  "target_version_validation_started",
  "target_version_build_started",
  "target_version_build_passed",
  "target_version_build_failed",
  "target_version_tests_started",
  "target_version_test_blocked",
  "target_version_tests_passed",
  "target_version_tests_failed",
  "target_version_validation_passed",
  "target_version_validation_failed",
  "target_version_repair_required",
  "target_version_repair_exhausted",
  "target_version_update_validated",
]);
