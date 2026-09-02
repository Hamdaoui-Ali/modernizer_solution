"""Immutable DTOs returned by Control Tower application services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from migration_factory.control_tower.domain.states import JobState
from migration_factory.control_tower.domain.commands import CommandState


@dataclass(frozen=True, slots=True)
class CreatedMigrationJob:
    job_id: str
    version: int
    run_configuration_id: str
    stage_run_ids: tuple[str, ...]
    event_id: str
    audit_id: str
    sequence: int


@dataclass(frozen=True, slots=True)
class RunnerProfileDto:
    runner_profile_id: str
    runner_profile_version: str
    display_name: str
    schema_version: str
    payload: dict[str, Any]
    payload_json: str
    payload_checksum: str
    created_at: str
    created_by: str


@dataclass(frozen=True, slots=True)
class PipelineDefinitionDto:
    pipeline_id: str
    pipeline_version: str
    display_name: str
    schema_version: str
    graph_version: str
    graph_state_schema_version: str
    payload: dict[str, Any]
    payload_json: str
    payload_checksum: str
    created_at: str
    created_by: str


@dataclass(frozen=True, slots=True)
class MigrationJobDto:
    job_id: str
    version: int
    status: JobState
    active_slot: int | None
    last_event_sequence: int
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class RunEventDto:
    event_id: str
    job_id: str
    sequence: int
    event_type: str
    actor_type: str
    actor_id: str
    correlation_id: str | None
    causation_id: str | None
    payload: dict[str, Any]
    payload_json: str
    payload_checksum: str
    created_at: str


@dataclass(frozen=True, slots=True)
class AuditRecordDto:
    audit_id: str
    job_id: str | None
    actor_type: str
    actor_id: str
    action: str
    prior_state: str | None
    new_state: str | None
    job_version: int | None
    correlation_id: str | None
    causation_id: str | None
    payload: dict[str, Any]
    payload_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class RunConfigurationDto:
    run_configuration_id: str
    job_id: str
    schema_version: str
    runner_profile_id: str
    runner_profile_version: str
    pipeline_id: str
    pipeline_version: str
    target_proof_level: str
    enabled_gates: tuple[str, ...]
    policy: dict[str, Any]
    payload_json: str
    payload_checksum: str
    created_at: str


@dataclass(frozen=True, slots=True)
class StageRunDto:
    stage_run_id: str
    job_id: str
    stage_index: int
    stage_id: str
    status: str
    input_source: dict[str, Any]
    created_at: str
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class ArtifactDto:
    artifact_id: str
    job_id: str
    stage_run_id: str | None
    artifact_type: str
    registered_root_id: str
    relative_path: str
    normalized_relative_path: str
    content_type: str | None
    size_bytes: int
    checksum_algorithm: str
    checksum: str
    created_at: str
    created_by: str


@dataclass(frozen=True, slots=True)
class CommandExecutionDto:
    command_id: str
    job_id: str
    operation: str
    status: CommandState
    created_at: str
    updated_at: str
    correlation_id: str | None
    causation_id: str | None
    command_manifest_artifact_id: str | None = None
    working_directory_root_id: str | None = None
    working_directory_relative_path: str | None = None
    worker_id: str | None = None
    launch_attempt: int | None = None
    process_control_id: str | None = None
    worker_pid: int | None = None
    process_started_at: str | None = None


@dataclass(frozen=True, slots=True)
class WorkerLaunchResult:
    command_id: str
    job_id: str
    process_control_id: str
    worker_pid: int
    process_started_at: str
    worker_id: str
    launch_attempt: int


@dataclass(frozen=True, slots=True)
class CommandOutputWindowDto:
    """Bounded window of command output bytes."""

    command_id: str
    job_id: str
    stream: str
    requested_offset: int
    start_offset: int
    next_offset: int
    data: str
    encoding: str
    replacement_characters_used: int
    truncated: bool
    terminal: bool
    max_bytes: int


@dataclass(frozen=True, slots=True)
class IdempotencyRecordDto:
    operation: str
    idempotency_key: str
    request_checksum: str
    resource_type: str
    resource_id: str
    original_status_code: int
    created_at: str


@dataclass(frozen=True, slots=True)
class StageChainEntryDto:
    """Redacted, ordered stage chain projection from the V1 ledger."""

    ledger_id: str
    job_id: str
    stage_index: int
    stage_run_id: str
    chain_status: str
    input_source_kind: str
    input_checksum: str | None
    output_artifact_id: str | None
    output_checksum: str | None
    output_registered_at: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class ModelInvocationDto:
    """Redacted model invocation audit record DTO.

    Raw prompts, secrets, and deployment IDs are never exposed.
    """

    invocation_id: str
    job_id: str | None
    profile_id: str | None
    provider_kind: str | None
    model_name: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    redacted_summary: str | None
    actor_type: str | None
    actor_id: str | None
    created_at: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContextPackManifestDto:
    """Redacted context pack manifest DTO.

    Evidence refs, bounds, and redacted summaries are included.
    Raw prompts, secrets, and deployment IDs are absent.
    Enrichment metadata (F01) is included when available.
    """

    manifest_id: str
    pack_type: str
    pack_version: str
    title: str
    description: str | None = None
    evidence_refs_json: str | None = None
    bounds_json: str | None = None
    redacted_summary: str | None = None
    checksum_algorithm: str = "sha256"
    checksum: str = ""
    model_profile_id: str | None = None
    model_name: str | None = None
    token_count: int | None = None
    created_at: str = ""
    created_by: str = ""
    enrichment_metadata: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class PlanAmendmentDto:
    amendment_id: str
    job_id: str
    source_kind: str
    title: str
    summary: str
    payload_checksum: str
    redacted_summary: dict[str, Any]
    created_at: str
    created_by: str


@dataclass(frozen=True, slots=True)
class PlanRevisionDto:
    revision_id: str
    amendment_id: str
    job_id: str
    revision_order: int
    revision_state: str
    source_kind: str
    payload_checksum: str
    redacted_summary: dict[str, Any]
    created_at: str
    created_by: str
    decided_at: str | None = None
    decided_by: str | None = None


@dataclass(frozen=True, slots=True)
class PlanPreviewDto:
    job_id: str
    source_kind: str
    title: str
    summary: str
    payload_checksum: str
    change_count: int
    affected_stage_indexes: tuple[int, ...]
    change_types: tuple[str, ...]
    redacted_summary: dict[str, Any]
    validation_status: str = "PASS"
    warning_codes: tuple[str, ...] = ()
    preview_persisted: bool = False
    preview_applied: bool = False


@dataclass(frozen=True, slots=True)
class AdvisoryValidationReportDto:
    amendment_id: str
    job_id: str
    validation_status: str
    source_kind: str
    revision_persisted: bool
    non_authoritative: bool
    warning_codes: tuple[str, ...]
    rejection_codes: tuple[str, ...]
    confidence_label: str | None
    confidence_score: float | None
    payload_checksum: str | None
    model_invocation_id: str | None
    context_pack_manifest_id: str | None
    revision_id: str | None = None
    revision_order: int | None = None
    revision_state: str | None = None
    redacted_summary: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PlanReviewDecisionDto:
    review_decision_id: str
    revision_id: str
    amendment_id: str
    job_id: str
    decision: str
    reviewed_checksum: str
    review_summary: str
    actor_type: str
    actor_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class PlanReviewStatusDto:
    revision_id: str
    amendment_id: str
    job_id: str
    payload_checksum: str
    review_required: bool
    eligible_for_downstream: bool
    status: str
    decision: str | None = None
    review_summary: str | None = None
    review_decision_id: str | None = None
    reviewed_checksum: str | None = None
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class RepairClassificationDto:
    classification_id: str
    command_id: str
    job_id: str
    command_status: str
    evidence_kind: str
    evidence_summary: str
    evidence_checksum: str
    classification_code: str
    reason_code: str
    repairable: bool
    attempt_limit: int
    actor_type: str
    actor_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class FakeRepairProposalDto:
    proposal_id: str
    classification_id: str
    command_id: str
    job_id: str
    proposal_order: int
    proposal_kind: str
    proposal_summary: str
    proposal_checksum: str
    recommendation_type: str | None
    confidence_label: str | None
    confidence_score: float | None
    warning_codes: tuple[str, ...]
    applicable: bool
    context_checksum: str | None
    actor_type: str
    actor_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class RepairAttemptDto:
    attempt_id: str
    classification_id: str
    command_id: str
    job_id: str
    attempt_order: int
    attempt_status: str
    attempt_summary: str
    attempt_checksum: str
    actor_type: str
    actor_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class RepairStatusDto:
    command_id: str
    job_id: str
    command_status: str
    classification: RepairClassificationDto | None
    attempts_used: int
    proposal_count: int
    attempt_limit: int
    remaining_attempts: int
    eligible_for_fake_repair: bool
    proposals: tuple[FakeRepairProposalDto, ...]
    attempts: tuple[RepairAttemptDto, ...]


@dataclass(frozen=True, slots=True)
class PatchPolicyValidationDto:
    """DTO for patch policy validation results.

    Never contains raw patch content. Only redacted metadata.
    """

    validation_id: str
    command_id: str
    job_id: str
    approved: bool
    validation_code: str
    reason_code: str
    target_path_hash: str
    patch_size_bytes: int
    metacharacter_hits: int
    policy_version: str
    actor_type: str
    actor_id: str
    created_at: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxSnapshotDto:
    """DTO for sandbox snapshot metadata."""

    snapshot_id: str
    command_id: str
    job_id: str
    stage_index: int
    sandbox_artifact_id: str
    sandbox_checksum: str
    actor_type: str
    actor_id: str
    created_at: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class RepairAttemptSummaryDto:
    """Safe attempt summary for PR-B attempt-history endpoint.

    Contains only redacted/safe metadata. Never includes raw patch
    content, target_path, raw command, argv, env, or secrets.
    """

    proposal_id: str
    command_id: str | None = None
    job_id: str | None = None
    gate_id: str | None = None
    attempt_number: int | None = None
    revision_number: int | None = None
    status: str = ""
    reviewer_decision: str | None = None
    diff_checksum: str | None = None
    policy_validation_checksum: str | None = None
    status_reason: str | None = None
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class LlmInvocationDto:
    """API-safe projection of a governed LLM invocation.

    Raw prompts, completions, endpoints, and API keys are never exposed.
    Only redacted summaries, checksums, token counts, and safe alias values.
    """

    invocation_id: str
    job_id: str
    role: str
    responsibility: str
    status: str
    created_at: str
    proposal_id: str | None = None
    gate_id: str | None = None
    provider_alias: str | None = None
    deployment_alias_hash: str | None = None
    context_checksum: str | None = None
    output_checksum: str | None = None
    schema_name: str | None = None
    fallback_used: bool = False
    redacted_error: str | None = None
    redacted_summary: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    completed_at: str | None = None
    configured_max_input_tokens: int | None = None
    configured_max_output_tokens: int | None = None
    response_format_used: str | None = None
    transport: str | None = None
    http_status: str | None = None
    azure_request_id: str | None = None
    retry_count: int = 0
    retry_after: str | None = None
    parse_result: str | None = None


@dataclass(frozen=True, slots=True)
class PatchApplicationDto:
    """DTO for an approved patch application result."""

    application_id: str
    command_id: str
    job_id: str
    validation_id: str
    snapshot_id: str
    stage_index: int
    target_path_hash: str
    patch_size_bytes: int
    applied_by: str
    applied_at: str
    status: str = "applied"
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class PatchRollbackDto:
    """DTO for a patch rollback result.

    All public output is redacted. Never contains raw patch content,
    paths, or shell commands.
    """

    rollback_id: str
    command_id: str
    job_id: str
    application_id: str
    snapshot_id: str
    maven_validation_id: str
    stage_index: int
    target_path_hash: str
    rolled_back_by: str
    rolled_back_at: str
    reason_code: str
    redacted_summary: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class PatchMavenValidationDto:
    """DTO for typed Maven validation after patch application."""

    maven_validation_id: str
    application_id: str
    command_id: str
    job_id: str
    maven_goal: str
    passed: bool
    result_summary: str
    actor_type: str
    actor_id: str
    created_at: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class JobProjectionDto:
    job: MigrationJobDto
    active_command: CommandExecutionDto | None
    etag: str


# ── F15 gate projection DTOs ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GateDto:
    """API-safe projection of a PhaseGate.

    Path-like values are redacted. Only checksums and statuses
    are exposed — never raw filesystem targets.
    """

    gate_id: str
    job_id: str
    gate_phase: str
    stage_index: int
    gate_status: str
    gate_decision: str
    source_artifact_checksum: str
    source_artifact_refs: tuple[str, ...]
    created_at: str
    resolved_at: str | None = None
    resolved_by: str | None = None


@dataclass(frozen=True, slots=True)
class GateDecisionDto:
    """API-safe projection of a gate decision.

    Result references are exposed as opaque ids only.
    No path, command, or env data is included.
    """

    decision_id: str
    gate_id: str
    action: str
    expected_gate_checksum: str
    idempotency_key: str
    decided_by: str
    decided_at: str
    result_gate_id: str | None = None
    result_command_id: str | None = None
    result_revision_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactRevisionDto:
    """API-safe projection of an artifact revision.

    Contains checksums, status, and opaque refs only.
    Never exposes filesystem paths or raw evidence content.
    """

    revision_id: str
    job_id: str
    stage_index: int
    revision_kind: str
    revision_status: str
    revision_order: int
    evidence_checksum: str
    artifact_refs: tuple[str, ...]
    created_at: str
    created_by: str
    accepted_at: str | None = None
    accepted_by: str | None = None
    prior_revision_id: str | None = None
    superseded_by_revision_id: str | None = None
