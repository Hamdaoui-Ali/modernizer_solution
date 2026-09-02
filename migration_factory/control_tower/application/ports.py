"""Ports for Control Tower application services."""

from __future__ import annotations

from typing import Protocol, Sequence
from typing_extensions import Self

from pathlib import Path

from migration_factory.control_tower.application.dto import (
    AuditRecordDto,
    ArtifactDto,
    CommandExecutionDto,
    IdempotencyRecordDto,
    MigrationJobDto,
    PipelineDefinitionDto,
    RunnerProfileDto,
    RunEventDto,
    WorkerLaunchResult,
)
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.entities import (
    ApprovalRecord,
    ApprovalResumeRecord,
    ArtifactRecord,
    AuditRecord,
    CommandExecutionRecord,
    IdempotencyRecord,
    MigrationJobRecord,
    PipelineDefinitionRecord,
    RunConfigurationRecord,
    RunEventRecord,
    RunnerProfileRecord,
    StageChainEventRecord,
    StageChainLedgerRecord,
    StageOutputRegistryRecord,
    StageRunRecord,
    V1FakeRepairProposalRecord,
    V1PatchApplicationRecord,
    V1PatchMavenValidationRecord,
    V1PatchRollbackRecord,
    V1PlanAmendmentRecord,
    V1PlanReviewDecisionRecord,
    V1PatchPolicyValidationRecord,
    V1RepairClassificationRecord,
    V1PlanRevisionRecord,
    V1SandboxSnapshotRecord,
)
from migration_factory.control_tower.domain.entities import V1ContextPackManifestRecord
from migration_factory.control_tower.domain.entities import V1ModelInvocationRecord
from migration_factory.control_tower.infrastructure.sqlite.v2_llm_invocation_repository import (
    V2LLMInvocationRecord,
)
from migration_factory.control_tower.domain.entities import V1PrivilegedActionDecisionRecord
from migration_factory.control_tower.domain.entities import V1PrivilegedActionExecutionRecord
from migration_factory.control_tower.domain.entities import V1PrivilegedActionRecord
from migration_factory.control_tower.domain.entities import V1ProofReportRecord
from migration_factory.control_tower.domain.entities import V1ProofReportGateRecord
from migration_factory.control_tower.domain.model_profiles import V1ModelProfileRecord
from migration_factory.control_tower.domain.manifests import CommandManifest
from migration_factory.control_tower.domain.states import JobState


class RunnerProfileRepository(Protocol):
    def get_exact(self, runner_profile_id: str, runner_profile_version: str) -> RunnerProfileRecord | None: ...

    def get(self, runner_profile_id: str, runner_profile_version: str) -> RunnerProfileDto | None: ...

    def list(self) -> tuple[RunnerProfileDto, ...]: ...

    def insert(self, profile: RunnerProfileDto) -> None: ...

    def find_checksum(self, runner_profile_id: str, runner_profile_version: str) -> str | None: ...


class PipelineDefinitionRepository(Protocol):
    def get_exact(self, pipeline_id: str, pipeline_version: str) -> PipelineDefinitionRecord | None: ...

    def get(self, pipeline_id: str, pipeline_version: str) -> PipelineDefinitionDto | None: ...

    def list(self) -> tuple[PipelineDefinitionDto, ...]: ...

    def insert(self, pipeline: PipelineDefinitionDto) -> None: ...

    def find_checksum(self, pipeline_id: str, pipeline_version: str) -> str | None: ...


class MigrationJobRepository(Protocol):
    def insert_created(self, job: MigrationJobRecord) -> None: ...

    def get(self, job_id: str) -> MigrationJobDto | None: ...

    def get_active_job(self) -> MigrationJobRecord | None: ...

    def list(self) -> tuple[MigrationJobDto, ...]: ...

    def transition_state(
        self,
        job_id: str,
        expected_version: int,
        target_state: JobState,
        active_slot: int | None,
        updated_at: str,
    ) -> bool: ...

    def increment_event_sequence(self, job_id: str) -> int: ...


class RunConfigurationRepository(Protocol):
    def insert(self, run_configuration: RunConfigurationRecord) -> None: ...

    def get_for_job(self, job_id: str) -> RunConfigurationRecord | None: ...


class StageRunRepository(Protocol):
    def insert_many(self, stage_runs: Sequence[StageRunRecord]) -> None: ...

    def get(self, stage_run_id: str) -> StageRunRecord | None: ...

    def list_for_job(self, job_id: str) -> tuple[StageRunRecord, ...]: ...


class RunEventRepository(Protocol):
    def insert(self, event: RunEventRecord) -> None: ...

    def append_job_state_changed_event(
        self,
        *,
        event_id: str,
        job_id: str,
        sequence: int,
        actor_type: str,
        actor_id: str,
        payload_json: str,
        payload_checksum: str,
        created_at: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> None: ...

    def list_for_job(self, job_id: str) -> tuple[RunEventDto, ...]: ...

    def list_for_job_after(
        self,
        job_id: str,
        after_sequence: int,
        limit: int,
    ) -> tuple[RunEventDto, ...]: ...

    def count_for_job(self, job_id: str) -> int: ...


class ArtifactRepository(Protocol):
    def insert(self, artifact: ArtifactRecord) -> None: ...

    def get_exact(
        self,
        job_id: str,
        registered_root_id: str,
        normalized_relative_path: str,
    ) -> ArtifactDto | None: ...

    def list_for_job(self, job_id: str) -> tuple[ArtifactDto, ...]: ...


class AuditRecordRepository(Protocol):
    def insert(self, audit_record: AuditRecord) -> None: ...

    def append_global_audit(
        self,
        *,
        audit_id: str,
        actor_type: str,
        actor_id: str,
        action: str,
        payload_json: str,
        created_at: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> None: ...

    def append_job_state_changed_audit(
        self,
        *,
        audit_id: str,
        job_id: str,
        actor_type: str,
        actor_id: str,
        prior_state: JobState,
        new_state: JobState,
        job_version: int,
        payload_json: str,
        created_at: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> None: ...

    def list(self) -> tuple[AuditRecordDto, ...]: ...

    def count(self) -> int: ...

    def list_for_job(self, job_id: str) -> tuple[AuditRecordDto, ...]: ...

    def count_for_job(self, job_id: str) -> int: ...


class CommandExecutionRepository(Protocol):
    def insert_queued(self, command: CommandExecutionRecord) -> None: ...

    def get(self, command_id: str) -> CommandExecutionDto | None: ...

    def list_for_job(self, job_id: str) -> tuple[CommandExecutionDto, ...]: ...

    def get_active_for_job(self, job_id: str) -> CommandExecutionDto | None: ...

    def update_status(self, command_id: str, status: CommandState) -> None: ...

    def update_workspace_columns(
        self,
        command_id: str,
        *,
        command_manifest_artifact_id: str,
        working_directory_root_id: str,
        working_directory_relative_path: str,
        worker_id: str,
        launch_attempt: int,
    ) -> None: ...

    def update_process_columns(
        self,
        command_id: str,
        *,
        status: CommandState,
        process_control_id: str,
        worker_pid: int,
        process_started_at: str,
    ) -> None: ...

    def get_output_offsets(self, command_id: str) -> tuple[int, int]: ...

    def update_output_offsets(
        self,
        command_id: str,
        *,
        stdout_offset: int,
        stderr_offset: int,
    ) -> None: ...

    def set_output_limit_exceeded(self, command_id: str) -> None: ...

    def get_terminal_artifact_links(self, command_id: str) -> dict[str, str | None]: ...

    def finalize_terminal_artifacts(
        self,
        command_id: str,
        *,
        stdout_artifact_id: str | None,
        stderr_artifact_id: str | None,
        result_artifact_id: str | None,
        spool_artifact_id: str | None,
        finalization_status: str,
        finalized_at: str,
    ) -> None: ...


class WorkerLauncher(Protocol):
    def launch(
        self,
        *,
        working_dir: Path,
        manifest: CommandManifest,
        manifest_bytes: bytes,
        python_executable: str,
    ) -> WorkerLaunchResult: ...


class WorkerTerminator(Protocol):
    def terminate(
        self,
        *,
        worker_pid: int,
        process_control_id: str | None = None,
        grace_period_seconds: float = 5.0,
    ) -> bool: ...


class IdempotencyRepository(Protocol):
    def get(self, operation: str, idempotency_key: str) -> IdempotencyRecordDto | None: ...

    def insert(self, record: IdempotencyRecord) -> None: ...


class StageChainLedgerRepository(Protocol):
    def insert_many(self, ledger_entries: Sequence[StageChainLedgerRecord]) -> None: ...

    def list_for_job(self, job_id: str) -> tuple[StageChainLedgerRecord, ...]: ...

    def insert_output(self, output: StageOutputRegistryRecord) -> None: ...

    def list_outputs_for_job(self, job_id: str) -> tuple[StageOutputRegistryRecord, ...]: ...

    def insert_event(self, event: StageChainEventRecord) -> None: ...

    def list_events_for_job(self, job_id: str) -> tuple[StageChainEventRecord, ...]: ...


class V1ModelProfileRepository(Protocol):
    def insert(self, profile: V1ModelProfileRecord) -> None: ...

    def get(self, profile_id: str) -> V1ModelProfileRecord | None: ...

    def list(self) -> tuple[V1ModelProfileRecord, ...]: ...


class V1ModelInvocationRepository(Protocol):
    """Append-only repository for model invocation audit records."""

    def insert(self, invocation: V1ModelInvocationRecord) -> None: ...

    def get(self, invocation_id: str) -> V1ModelInvocationRecord | None: ...

    def list(self) -> tuple[V1ModelInvocationRecord, ...]: ...

    def list_for_job(self, job_id: str) -> tuple[V1ModelInvocationRecord, ...]: ...


class V2LLMInvocationRepository(Protocol):
    """Append-only repository for governed LLM invocation ledger."""

    def save(self, invocation: V2LLMInvocationRecord) -> None: ...

    def get(self, invocation_id: str) -> V2LLMInvocationRecord | None: ...

    def list_by_job(self, job_id: str) -> tuple[V2LLMInvocationRecord, ...]: ...

    def list_by_proposal(self, proposal_id: str) -> tuple[V2LLMInvocationRecord, ...]: ...

    def update_status(
        self,
        invocation_id: str,
        status: str,
        *,
        output_checksum: str | None = None,
        redacted_error: str | None = None,
        redacted_summary: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_ms: int | None = None,
        completed_at: str | None = None,
        fallback_used: int | None = None,
    ) -> None: ...


class V1ContextPackManifestRepository(Protocol):
    """Append-only repository for context pack manifest records."""

    def insert(self, manifest: V1ContextPackManifestRecord) -> None: ...

    def get(self, manifest_id: str) -> V1ContextPackManifestRecord | None: ...

    def list(self) -> tuple[V1ContextPackManifestRecord, ...]: ...

    def list_for_job(self, job_id: str) -> tuple[V1ContextPackManifestRecord, ...]: ...


class V1ModelProfileEventRepository(Protocol):
    def insert_event(
        self,
        *,
        event_id: str,
        profile_id: str,
        event_type: str,
        provider_kind: str,
        actor_type: str,
        actor_id: str,
        payload_json: str,
        payload_checksum: str,
        created_at: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> None: ...


class V1ApprovalRepository(Protocol):
    def insert(self, approval: ApprovalRecord) -> None: ...

    def get(self, approval_id: str) -> ApprovalRecord | None: ...

    def get_by_interrupt(
        self, interrupt_id: str, request_checksum: str
    ) -> ApprovalRecord | None: ...

    def list_for_job(self, job_id: str) -> tuple[ApprovalRecord, ...]: ...


class V1ApprovalResumeRepository(Protocol):
    def insert(self, resume: ApprovalResumeRecord) -> None: ...

    def list_pending(self) -> tuple[ApprovalResumeRecord, ...]: ...

    def list_for_approval(
        self, approval_id: str
    ) -> tuple[ApprovalResumeRecord, ...]: ...

    def update_status(
        self,
        resume_id: str,
        status: str,
        executed_at: str | None = None,
        failure_reason: str | None = None,
    ) -> None: ...


class V1PrivilegedActionRepository(Protocol):
    """Append-only repository for privileged action records."""

    def insert(self, action: V1PrivilegedActionRecord) -> None: ...

    def get(self, action_id: str) -> V1PrivilegedActionRecord | None: ...

    def list(self) -> tuple[V1PrivilegedActionRecord, ...]: ...

    def list_for_job(self, job_id: str) -> tuple[V1PrivilegedActionRecord, ...]: ...

    def list_by_status(self, status: str) -> tuple[V1PrivilegedActionRecord, ...]: ...


class V1PlanAmendmentRepository(Protocol):
    def insert(self, amendment: V1PlanAmendmentRecord) -> None: ...

    def get(self, amendment_id: str) -> V1PlanAmendmentRecord | None: ...

    def list_for_job(self, job_id: str) -> tuple[V1PlanAmendmentRecord, ...]: ...


class V1PlanRevisionRepository(Protocol):
    def insert(self, revision: V1PlanRevisionRecord) -> None: ...

    def get(self, revision_id: str) -> V1PlanRevisionRecord | None: ...

    def list_for_amendment(self, amendment_id: str) -> tuple[V1PlanRevisionRecord, ...]: ...

    def list_for_job(self, job_id: str) -> tuple[V1PlanRevisionRecord, ...]: ...

    def next_revision_order(self, amendment_id: str) -> int: ...

    def has_terminal_revision(self, amendment_id: str) -> bool: ...


class V1PlanReviewDecisionRepository(Protocol):
    def insert(self, review_decision: V1PlanReviewDecisionRecord) -> None: ...

    def get_for_revision(self, revision_id: str) -> V1PlanReviewDecisionRecord | None: ...

    def list_for_job(self, job_id: str) -> tuple[V1PlanReviewDecisionRecord, ...]: ...


class V1RepairClassificationRepository(Protocol):
    def insert(self, classification: V1RepairClassificationRecord) -> None: ...

    def get_by_command_and_checksum(
        self,
        command_id: str,
        evidence_checksum: str,
    ) -> V1RepairClassificationRecord | None: ...

    def get_latest_for_command(self, command_id: str) -> V1RepairClassificationRecord | None: ...

    def list_for_job(self, job_id: str) -> tuple[V1RepairClassificationRecord, ...]: ...


class V1FakeRepairProposalRepository(Protocol):
    def insert(self, proposal: V1FakeRepairProposalRecord) -> None: ...

    def get_for_classification_and_checksum(
        self,
        classification_id: str,
        proposal_checksum: str,
    ) -> V1FakeRepairProposalRecord | None: ...

    def list_for_classification(
        self,
        classification_id: str,
    ) -> tuple[V1FakeRepairProposalRecord, ...]: ...

    def get_for_classification_kind_and_context(
        self,
        classification_id: str,
        proposal_kind: str,
        context_checksum: str,
    ) -> V1FakeRepairProposalRecord | None: ...


class V1PrivilegedActionExecutionRepository(Protocol):
    """Append-only repository for privileged action execution records."""

    def insert(self, execution: V1PrivilegedActionExecutionRecord) -> None: ...

    def get(self, action_id: str) -> V1PrivilegedActionExecutionRecord | None: ...

    def list(self) -> tuple[V1PrivilegedActionExecutionRecord, ...]: ...

    def list_by_status(self, status: str) -> tuple[V1PrivilegedActionExecutionRecord, ...]: ...


class V1PrivilegedActionDecisionRepository(Protocol):
    """Append-only repository for privileged action decision records."""

    def insert(self, decision: V1PrivilegedActionDecisionRecord) -> None: ...

    def get(self, action_id: str) -> V1PrivilegedActionDecisionRecord | None: ...

    def list(self) -> tuple[V1PrivilegedActionDecisionRecord, ...]: ...

    def list_by_decision(self, decision: str) -> tuple[V1PrivilegedActionDecisionRecord, ...]: ...


class V1PatchPolicyValidationRepository(Protocol):
    """Append-only repository for patch policy validation records."""

    def insert(self, validation: V1PatchPolicyValidationRecord) -> None: ...

    def get(self, validation_id: str) -> V1PatchPolicyValidationRecord | None: ...

    def list_for_command(self, command_id: str) -> tuple[V1PatchPolicyValidationRecord, ...]: ...

    def get_latest_for_command(self, command_id: str) -> V1PatchPolicyValidationRecord | None: ...


class V1SandboxSnapshotRepository(Protocol):
    """Append-only repository for sandbox snapshot records."""

    def insert(self, snapshot: V1SandboxSnapshotRecord) -> None: ...

    def get(self, snapshot_id: str) -> V1SandboxSnapshotRecord | None: ...

    def get_for_command(self, command_id: str) -> V1SandboxSnapshotRecord | None: ...

    def list_for_job(self, job_id: str) -> tuple[V1SandboxSnapshotRecord, ...]: ...


class V1PatchApplicationRepository(Protocol):
    """Append-only repository for patch application records."""

    def insert(self, application: V1PatchApplicationRecord) -> None: ...

    def get(self, application_id: str) -> V1PatchApplicationRecord | None: ...

    def get_for_command(self, command_id: str) -> V1PatchApplicationRecord | None: ...

    def list_for_job(self, job_id: str) -> tuple[V1PatchApplicationRecord, ...]: ...


class V1PatchRollbackRepository(Protocol):
    """Append-only repository for patch rollback records."""

    def insert(self, rollback: V1PatchRollbackRecord) -> None: ...

    def get(self, rollback_id: str) -> V1PatchRollbackRecord | None: ...

    def get_for_command(self, command_id: str) -> V1PatchRollbackRecord | None: ...

    def get_for_application(self, application_id: str) -> V1PatchRollbackRecord | None: ...

    def list_for_job(self, job_id: str) -> tuple[V1PatchRollbackRecord, ...]: ...


class V1PatchMavenValidationRepository(Protocol):
    """Append-only repository for Maven validation records."""

    def insert(self, validation: V1PatchMavenValidationRecord) -> None: ...

    def get(self, maven_validation_id: str) -> V1PatchMavenValidationRecord | None: ...

    def get_for_application(self, application_id: str) -> V1PatchMavenValidationRecord | None: ...

    def list_for_job(self, job_id: str) -> tuple[V1PatchMavenValidationRecord, ...]: ...


class V1ProofReportRepository(Protocol):
    """Append-only repository for proof report artifacts."""

    def insert(self, report: V1ProofReportRecord) -> None: ...

    def get(self, report_id: str) -> V1ProofReportRecord | None: ...

    def get_latest_for_job(self, job_id: str) -> V1ProofReportRecord | None: ...

    def list_for_job(self, job_id: str) -> tuple[V1ProofReportRecord, ...]: ...


class V1ProofReportGateRepository(Protocol):
    """Append-only repository for proof report gate associations."""

    def insert(self, gate: V1ProofReportGateRecord) -> None: ...

    def list_for_report(self, report_id: str) -> tuple[V1ProofReportGateRecord, ...]: ...

    def list_for_job(self, job_id: str) -> tuple[V1ProofReportGateRecord, ...]: ...


class ControlTowerUnitOfWork(Protocol):
    runner_profiles: RunnerProfileRepository
    pipeline_definitions: PipelineDefinitionRepository
    migration_jobs: MigrationJobRepository
    run_configurations: RunConfigurationRepository
    stage_runs: StageRunRepository
    run_events: RunEventRepository
    artifacts: ArtifactRepository
    audit_records: AuditRecordRepository
    command_executions: CommandExecutionRepository
    idempotency_records: IdempotencyRepository
    stage_chain_ledger: StageChainLedgerRepository
    v1_model_profiles: V1ModelProfileRepository
    v1_model_profile_events: V1ModelProfileEventRepository
    v1_approvals: V1ApprovalRepository
    v1_approval_resume: V1ApprovalResumeRepository
    v1_model_invocations: V1ModelInvocationRepository
    v1_context_pack_manifests: V1ContextPackManifestRepository
    v1_privileged_actions: V1PrivilegedActionRepository
    v1_plan_amendments: V1PlanAmendmentRepository
    v1_plan_revisions: V1PlanRevisionRepository
    v1_plan_review_decisions: V1PlanReviewDecisionRepository
    v1_repair_classifications: V1RepairClassificationRepository
    v1_fake_repair_proposals: V1FakeRepairProposalRepository
    v1_privileged_action_decisions: V1PrivilegedActionDecisionRepository
    v1_privileged_action_executions: V1PrivilegedActionExecutionRepository
    v1_patch_policy_validations: V1PatchPolicyValidationRepository
    v1_sandbox_snapshots: V1SandboxSnapshotRepository
    v1_patch_applications: V1PatchApplicationRepository
    v1_patch_maven_validations: V1PatchMavenValidationRepository
    v1_patch_rollbacks: V1PatchRollbackRepository
    v1_proof_reports: V1ProofReportRepository
    v1_proof_report_gates: V1ProofReportGateRepository
    v2_llm_invocations: V2LLMInvocationRepository

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type, exc, tb) -> bool | None: ...


UnitOfWork = ControlTowerUnitOfWork
