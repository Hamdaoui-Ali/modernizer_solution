"""Application command DTOs for Control Tower."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from migration_factory.control_tower.domain.artifacts import ArtifactHashResult
from migration_factory.control_tower.domain.states import JobState, TargetProofLevel
from migration_factory.control_tower.schemas import PipelineDefinition, RunnerProfile
from migration_factory.control_tower.schemas.run_configuration import RunPolicy


@dataclass(frozen=True, slots=True)
class CreateMigrationJobCommand:
    actor: str
    legacy_source_ref: str
    output_root_ref: str
    runner_profile_id: str
    runner_profile_version: str
    pipeline_id: str
    pipeline_version: str
    target_proof_level: TargetProofLevel
    enabled_gates: tuple[str, ...]
    policy: RunPolicy
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterRunnerProfileCommand:
    profile: RunnerProfile | dict[str, Any]
    actor_type: str
    actor_id: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterPipelineDefinitionCommand:
    pipeline: PipelineDefinition | dict[str, Any]
    actor_type: str
    actor_id: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterArtifactCommand:
    job_id: str
    artifact: ArtifactHashResult
    artifact_type: str
    actor_type: str
    actor_id: str
    stage_run_id: str | None = None
    content_type: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class TransitionJobStateCommand:
    job_id: str
    expected_version: int | None
    target_state: JobState
    actor_type: str
    actor_id: str
    reason: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class CreateDiagnosticJobCommand:
    idempotency_key: str
    runner_profile_id: str
    runner_profile_version: str
    pipeline_id: str
    pipeline_version: str
    legacy_source_root_id: str
    legacy_source_relative_path: str
    output_root_id: str
    output_relative_path: str
    target_proof_level: TargetProofLevel
    enabled_gates: tuple[str, ...]
    policy: RunPolicy
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class StartMigrationJobCommand:
    job_id: str
    expected_version: int | None
    idempotency_key: str
    actor_type: str
    actor_id: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class PrepareCommandWorkspaceCommand:
    command_id: str
    job_id: str
    working_directory_root_id: str
    working_directory_relative_path: str
    worker_id: str
    launch_attempt: int
    actor_type: str
    actor_id: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class LaunchWorkerCommand:
    command_id: str
    job_id: str
    actor_type: str
    actor_id: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class FinalizeCommandCommand:
    command_id: str
    job_id: str
    outcome: str
    actor_type: str
    actor_id: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class CancelCommand:
    job_id: str
    expected_version: int
    command_id: str | None = None
    reason: str = "user_cancelled"
    grace_period_seconds: float = 5.0
    actor_type: str = "user"
    actor_id: str = "user"
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class StageCommandLaunchCommand:
    """Command to create a stage command manifest payload without launching a process.

    All argv and env values are backend-owned. The browser never chooses
    raw paths, Maven goals, shell commands, working directories, or model
    deployment IDs. No process is started by this command.
    """

    job_id: str
    command_id: str
    worker_id: str
    operation: str
    stage_run_id: str
    ledger_id: str
    jdk_id: str
    jdk_java_home: str
    jdk_expected_major: int
    runner_profile_display_name: str
    pipeline_id: str
    pipeline_version: str
    stage_index: int
    stage_id: str
    profile_id: str
    command_jdk: str
    sandbox_root_id: str
    sandbox_relative_path: str
    run_configuration_artifact_id: str
    run_configuration_checksum: str
    working_directory_root_id: str
    working_directory_relative_path: str
    stdout_relative_path: str
    stderr_relative_path: str
    result_relative_path: str
    spool_relative_path: str
    # Optional fields (positioned after all required fields)
    timeout_seconds: int = 3600
    max_stdout_bytes: int = 104857600
    max_stderr_bytes: int = 104857600
    actor_type: str = "system"
    actor_id: str = "system"
    correlation_id: str | None = None
    causation_id: str | None = None
    catalog_checksum: str | None = None
    ledger_input_checksum: str | None = None
    ledger_checksum_guard: str | None = None
    argv: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RecordApprovalCommand:
    """Record an approval decision for a job.

    The approval is idempotent by (interrupt_id, request_checksum).
    The decision is persisted, and a resume command is queued for
    later execution. No direct resume is performed by this command.
    """

    job_id: str
    interrupt_id: str
    request_checksum: str
    decision: str
    approved_by: str
    approval_comments: str = ""
    actor_type: str = "system"
    actor_id: str = "system"
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class QueueApprovalResumeCommand:
    """Queue a resume command for an approved approval.

    This is always queued, never executed directly.
    """

    approval_id: str
    job_id: str
    command_type: str
    command_payload_json: str
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class TimeoutCommand:
    job_id: str
    command_id: str
    timeout_seconds: int
    deadline: float
    actor_type: str = "system"
    actor_id: str = "system"
    correlation_id: str | None = None
    causation_id: str | None = None
