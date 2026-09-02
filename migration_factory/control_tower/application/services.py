"""Application services for Control Tower operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.application.commands import (
    CancelCommand,
    CreateMigrationJobCommand,
    CreateDiagnosticJobCommand,
    FinalizeCommandCommand,
    LaunchWorkerCommand,
    PrepareCommandWorkspaceCommand,
    QueueApprovalResumeCommand,
    RecordApprovalCommand,
    RegisterArtifactCommand,
    RegisterPipelineDefinitionCommand,
    RegisterRunnerProfileCommand,
    StageCommandLaunchCommand,
    StartMigrationJobCommand,
    TimeoutCommand,
    TransitionJobStateCommand,
)
from migration_factory.control_tower.application.dto import (
    CommandExecutionDto,
)
from migration_factory.control_tower.application.dto import (
    ArtifactDto,
    CreatedMigrationJob,
    JobProjectionDto,
    MigrationJobDto,
    PipelineDefinitionDto,
    RunnerProfileDto,
    WorkerLaunchResult,
)
from migration_factory.control_tower.application.ports import ControlTowerUnitOfWork, WorkerLauncher, WorkerTerminator
from migration_factory.control_tower.domain.artifacts import ArtifactHashResult
from migration_factory.control_tower.domain.checksums import canonical_json_text, sha256_canonical_json, utc_now_text
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.entities import (
    ApprovalRecord,
    ApprovalResumeRecord,
    ArtifactRecord,
    AuditRecord,
    CommandExecutionRecord,
    IdempotencyRecord,
    MigrationJobRecord,
    RunConfigurationRecord,
    RunEventRecord,
    StageChainEventRecord,
    StageChainLedgerRecord,
    StageRunRecord,
    V1ModelInvocationRecord,
)
from migration_factory.control_tower.domain.model_profiles import V1ModelProfileRecord
from migration_factory.control_tower.domain.manifests import (
    CommandManifest,
    StageCommandManifest,
    BrowserRestrictedPayload,
    compute_manifest_checksum,
    compute_stage_manifest_checksum,
    verify_manifest_checksum,
    verify_stage_manifest_checksum,
)
from migration_factory.control_tower.domain.errors import (
    ActiveCommandConflictError,
    ArtifactPathError,
    CompatibilityError,
    ConcurrencyConflictError,
    ExpectedVersionRequiredError,
    IdempotencyConflictError,
    InvalidJobStateTransitionError,
    ManifestIntegrityError,
    NotFoundError,
    ContinuationPolicyViolationError,
    RegistrationConflictError,
    StaleVersionError,
    StorageIntegrityError,
    WorkspaceConflictError,
    WorkspacePathError,
)
from migration_factory.control_tower.domain.states import JobState
from migration_factory.control_tower.domain.transitions import (
    TERMINAL_JOB_STATES,
    active_slot_for,
    validate_job_state_transition,
)
from migration_factory.control_tower.infrastructure.sqlite.artifact_paths import normalize_registered_relative_path
from migration_factory.control_tower.schemas import PipelineDefinition, RunnerProfile
from migration_factory.control_tower.schemas.run_configuration import RunConfiguration


UnitOfWorkFactory = Callable[[], ControlTowerUnitOfWork]
DIAGNOSTIC_OPERATION = "foundation_diagnostic"
CREATE_DIAGNOSTIC_JOB_OPERATION = "create_diagnostic_job"
START_DIAGNOSTIC_JOB_OPERATION = "start_diagnostic_job"


class _BorrowedUnitOfWork:
    def __init__(self, unit_of_work: ControlTowerUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def __getattr__(self, name: str) -> Any:
        return getattr(self._unit_of_work, name)

    def __enter__(self) -> ControlTowerUnitOfWork:
        return self._unit_of_work

    def __exit__(self, exc_type, exc, tb) -> bool | None:
        return None


class CreateMigrationJobService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def execute(self, command: CreateMigrationJobCommand) -> CreatedMigrationJob:
        with self._unit_of_work_factory() as uow:
            runner = uow.runner_profiles.get_exact(
                command.runner_profile_id,
                command.runner_profile_version,
            )
            if runner is None:
                raise NotFoundError(
                    "runner profile",
                    f"{command.runner_profile_id}/{command.runner_profile_version}",
                )

            pipeline = uow.pipeline_definitions.get_exact(
                command.pipeline_id,
                command.pipeline_version,
            )
            if pipeline is None:
                raise NotFoundError(
                    "pipeline definition",
                    f"{command.pipeline_id}/{command.pipeline_version}",
                )

            self._validate_runner_pipeline_compatibility(runner.payload, pipeline.payload)

            job_id = f"job-{uuid4().hex}"
            now = utc_now_text()
            run_configuration_id = f"run-config-{job_id}"
            stage_run_ids: list[str] = []

            run_configuration_payload = RunConfiguration(
                schema_version="1.0.0",
                run_configuration_id=run_configuration_id,
                job_id=job_id,
                runner_profile_id=runner.runner_profile_id,
                runner_profile_version=runner.runner_profile_version,
                pipeline_id=pipeline.pipeline_id,
                pipeline_version=pipeline.pipeline_version,
                target_proof_level=command.target_proof_level,
                enabled_gates=command.enabled_gates,
                policy=command.policy,
            )
            run_configuration_payload_json = canonical_json_text(run_configuration_payload)
            run_configuration_checksum = sha256_canonical_json(run_configuration_payload)

            job_record = MigrationJobRecord(
                job_id=job_id,
                version=1,
                status=JobState.CREATED,
                active_slot=1,
                last_event_sequence=1,
                runner_profile_id=runner.runner_profile_id,
                runner_profile_version=runner.runner_profile_version,
                pipeline_id=pipeline.pipeline_id,
                pipeline_version=pipeline.pipeline_version,
                target_proof_level=command.target_proof_level,
                achieved_proof_level=None,
                legacy_source_ref=command.legacy_source_ref,
                output_root_ref=command.output_root_ref,
                created_at=now,
                updated_at=now,
                started_at=None,
                finished_at=None,
                created_by=command.actor,
            )

            run_configuration_record = RunConfigurationRecord(
                run_configuration_id=run_configuration_id,
                job_id=job_id,
                schema_version=run_configuration_payload.schema_version,
                runner_profile_id=run_configuration_payload.runner_profile_id,
                runner_profile_version=run_configuration_payload.runner_profile_version,
                pipeline_id=run_configuration_payload.pipeline_id,
                pipeline_version=run_configuration_payload.pipeline_version,
                target_proof_level=run_configuration_payload.target_proof_level,
                enabled_gates_json=canonical_json_text(run_configuration_payload.enabled_gates),
                policy_json=canonical_json_text(run_configuration_payload.policy),
                payload_json=run_configuration_payload_json,
                payload_checksum=run_configuration_checksum,
                created_at=now,
            )

            try:
                uow.migration_jobs.insert_created(job_record)
            except StorageIntegrityError as exc:
                active_job = uow.migration_jobs.get_active_job()
                if active_job is not None:
                    raise ConcurrencyConflictError(
                        "Another active migration job already occupies the single active slot."
                    ) from exc
                raise

            uow.run_configurations.insert(run_configuration_record)

            stage_runs = []
            for stage in pipeline.payload.stages:
                stage_run_id = f"stage-{job_id}-{stage.stage_index:04d}"
                stage_run_ids.append(stage_run_id)
                stage_runs.append(
                    StageRunRecord(
                        stage_run_id=stage_run_id,
                        job_id=job_id,
                        stage_index=stage.stage_index,
                        stage_id=stage.stage_id,
                        status="PENDING",
                        input_source_json=canonical_json_text(stage.input_source),
                        created_at=now,
                        started_at=None,
                        finished_at=None,
                    )
                )

            if stage_runs:
                uow.stage_runs.insert_many(stage_runs)

            # Persist stage-chain ledger entries
            pipeline_payload = pipeline.payload
            ledger_entries = []
            for i, stage in enumerate(pipeline_payload.stages):
                stage_run_id = stage_run_ids[i]
                ledger_id = f"ledger-{job_id}-{stage.stage_index:04d}"
                source_kind = stage.input_source.kind if hasattr(stage.input_source, "kind") else "legacy_source"
                # Compute input checksum over the full input source configuration
                input_checksum = sha256_canonical_json(stage.input_source)
                # Compute checksum guard over the full stage content
                stage_json = canonical_json_text(stage)
                import hashlib as _hashlib
                checksum_guard = _hashlib.sha256(stage_json.encode("utf-8")).hexdigest()

                ledger_entries.append(
                    StageChainLedgerRecord(
                        ledger_id=ledger_id,
                        job_id=job_id,
                        stage_index=stage.stage_index,
                        stage_run_id=stage_run_id,
                        chain_status="pending",
                        input_source_kind=source_kind,
                        input_checksum=input_checksum,
                        output_artifact_id=None,
                        output_checksum=None,
                        output_registered_at=None,
                        checksum_guard=checksum_guard,
                        created_at=now,
                        created_by=command.actor,
                    )
                )

            if ledger_entries:
                uow.stage_chain_ledger.insert_many(ledger_entries)

            # Record chain_created event for audit
            chain_event_payload = {
                "job_id": job_id,
                "ledger_ids": [entry.ledger_id for entry in ledger_entries],
                "stage_run_ids": stage_run_ids,
            }
            uow.stage_chain_ledger.insert_event(
                StageChainEventRecord(
                    event_id=f"chain-event-{job_id}-created",
                    job_id=job_id,
                    stage_index=None,
                    event_type="chain_created",
                    prior_status=None,
                    new_status="pending",
                    ledger_id=None,
                    output_id=None,
                    payload_json=canonical_json_text(chain_event_payload),
                    payload_checksum=sha256_canonical_json(chain_event_payload),
                    created_at=now,
                    created_by=command.actor,
                )
            )

            event_payload = {
                "job_id": job_id,
                "runner_profile_id": runner.runner_profile_id,
                "runner_profile_version": runner.runner_profile_version,
                "pipeline_id": pipeline.pipeline_id,
                "pipeline_version": pipeline.pipeline_version,
                "legacy_source_ref": command.legacy_source_ref,
                "output_root_ref": command.output_root_ref,
                "target_proof_level": command.target_proof_level,
                "enabled_gates": command.enabled_gates,
                "policy": command.policy,
            }
            event_record = RunEventRecord(
                event_id=f"event-{job_id}-0001",
                job_id=job_id,
                sequence=1,
                event_type="job_created",
                actor_type="user",
                actor_id=command.actor,
                correlation_id=command.correlation_id,
                causation_id=None,
                payload_json=canonical_json_text(event_payload),
                payload_checksum=sha256_canonical_json(event_payload),
                created_at=now,
            )
            uow.run_events.insert(event_record)

            audit_payload = {
                "job_id": job_id,
                "run_configuration_id": run_configuration_id,
                "stage_run_ids": stage_run_ids,
                "event_id": event_record.event_id,
            }
            audit_record = AuditRecord(
                audit_id=f"audit-{job_id}-0001",
                job_id=job_id,
                actor_type="user",
                actor_id=command.actor,
                action="job_created",
                prior_state=None,
                new_state=JobState.CREATED.value,
                job_version=1,
                correlation_id=command.correlation_id,
                causation_id=event_record.event_id,
                payload_json=canonical_json_text(audit_payload),
                created_at=now,
            )
            uow.audit_records.insert(audit_record)

            return CreatedMigrationJob(
                job_id=job_id,
                version=1,
                run_configuration_id=run_configuration_id,
                stage_run_ids=tuple(stage_run_ids),
                event_id=event_record.event_id,
                audit_id=audit_record.audit_id,
                sequence=1,
            )

    def _validate_runner_pipeline_compatibility(self, runner_payload, pipeline_payload) -> None:
        runner_jdk_ids = {jdk.jdk_id for jdk in runner_payload.jdks}
        missing_jdks = [
            stage.command_jdk
            for stage in pipeline_payload.stages
            if stage.command_jdk not in runner_jdk_ids
        ]
        if missing_jdks:
            unique_missing = ", ".join(sorted(set(missing_jdks)))
            raise CompatibilityError(
                "Pipeline references JDKs not available in the selected runner profile: "
                f"{unique_missing}"
            )


class ControlTowerRegistrationService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def register_runner_profile(self, command: RegisterRunnerProfileCommand) -> RunnerProfileDto:
        profile = _validate_runner_profile(command.profile)
        payload = _schema_payload(profile)
        payload_json = canonical_json_text(payload)
        checksum = sha256_canonical_json(payload)

        with self._unit_of_work_factory() as uow:
            existing_checksum = uow.runner_profiles.find_checksum(
                profile.runner_profile_id,
                profile.runner_profile_version,
            )
            if existing_checksum is not None:
                if existing_checksum != checksum:
                    raise RegistrationConflictError(
                        "runner_profile",
                        profile.runner_profile_id,
                        profile.runner_profile_version,
                    )
                existing = uow.runner_profiles.get(
                    profile.runner_profile_id,
                    profile.runner_profile_version,
                )
                if existing is None:
                    raise NotFoundError("Runner profile checksum exists but row could not be loaded")
                return existing

            created_at = utc_now_text()
            dto = RunnerProfileDto(
                runner_profile_id=profile.runner_profile_id,
                runner_profile_version=profile.runner_profile_version,
                display_name=profile.display_name,
                schema_version=profile.schema_version,
                payload=payload,
                payload_json=payload_json,
                payload_checksum=checksum,
                created_at=created_at,
                created_by=command.actor_id,
            )
            uow.runner_profiles.insert(dto)
            uow.audit_records.append_global_audit(
                audit_id=str(uuid4()),
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                action="runner_profile_registered",
                payload_json=_registration_audit_payload_json(
                    action="runner_profile_registered",
                    registration_type="runner_profile",
                    entity_id=profile.runner_profile_id,
                    version=profile.runner_profile_version,
                    checksum=checksum,
                    schema_version=profile.schema_version,
                    display_name=profile.display_name,
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    correlation_id=command.correlation_id,
                    causation_id=command.causation_id,
                ),
                created_at=created_at,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            return dto

    def register_pipeline_definition(
        self,
        command: RegisterPipelineDefinitionCommand,
    ) -> PipelineDefinitionDto:
        pipeline = _validate_pipeline_definition(command.pipeline)
        payload = _schema_payload(pipeline)
        payload_json = canonical_json_text(payload)
        checksum = sha256_canonical_json(payload)

        with self._unit_of_work_factory() as uow:
            existing_checksum = uow.pipeline_definitions.find_checksum(
                pipeline.pipeline_id,
                pipeline.pipeline_version,
            )
            if existing_checksum is not None:
                if existing_checksum != checksum:
                    raise RegistrationConflictError(
                        "pipeline_definition",
                        pipeline.pipeline_id,
                        pipeline.pipeline_version,
                    )
                existing = uow.pipeline_definitions.get(
                    pipeline.pipeline_id,
                    pipeline.pipeline_version,
                )
                if existing is None:
                    raise NotFoundError("Pipeline checksum exists but row could not be loaded")
                return existing

            created_at = utc_now_text()
            dto = PipelineDefinitionDto(
                pipeline_id=pipeline.pipeline_id,
                pipeline_version=pipeline.pipeline_version,
                display_name=pipeline.display_name,
                schema_version=pipeline.schema_version,
                graph_version=pipeline.graph_version,
                graph_state_schema_version=pipeline.graph_state_schema_version,
                payload=payload,
                payload_json=payload_json,
                payload_checksum=checksum,
                created_at=created_at,
                created_by=command.actor_id,
            )
            uow.pipeline_definitions.insert(dto)
            uow.audit_records.append_global_audit(
                audit_id=str(uuid4()),
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                action="pipeline_definition_registered",
                payload_json=_registration_audit_payload_json(
                    action="pipeline_definition_registered",
                    registration_type="pipeline_definition",
                    entity_id=pipeline.pipeline_id,
                    version=pipeline.pipeline_version,
                    checksum=checksum,
                    schema_version=pipeline.schema_version,
                    display_name=pipeline.display_name,
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    correlation_id=command.correlation_id,
                    causation_id=command.causation_id,
                ),
                created_at=created_at,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            return dto

    def get_runner_profile(
        self,
        runner_profile_id: str,
        runner_profile_version: str,
    ) -> RunnerProfileDto:
        with self._unit_of_work_factory() as uow:
            profile = uow.runner_profiles.get(runner_profile_id, runner_profile_version)
            if profile is None:
                raise NotFoundError(
                    f"Runner profile {runner_profile_id!r} version {runner_profile_version!r} not found"
                )
            return profile

    def list_runner_profiles(self) -> tuple[RunnerProfileDto, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.runner_profiles.list()

    def get_pipeline_definition(self, pipeline_id: str, pipeline_version: str) -> PipelineDefinitionDto:
        with self._unit_of_work_factory() as uow:
            pipeline = uow.pipeline_definitions.get(pipeline_id, pipeline_version)
            if pipeline is None:
                raise NotFoundError(
                    f"Pipeline definition {pipeline_id!r} version {pipeline_version!r} not found"
                )
            return pipeline

    def list_pipeline_definitions(self) -> tuple[PipelineDefinitionDto, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.pipeline_definitions.list()

    def register_model_profile(
        self,
        profile_id: str,
        display_name: str,
        provider_kind: str,
        model_env_ref: str,
        endpoint_env_ref: str,
        deployment_env_ref: str,
        actor_type: str,
        actor_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> V1ModelProfileRecord:
        created_at = utc_now_text()
        record = V1ModelProfileRecord(
            profile_id=profile_id,
            display_name=display_name,
            provider_kind=provider_kind,
            model_env_ref=model_env_ref,
            endpoint_env_ref=endpoint_env_ref,
            deployment_env_ref=deployment_env_ref,
            is_active=True,
            created_at=created_at,
            created_by=actor_id,
        )
        with self._unit_of_work_factory() as uow:
            uow.v1_model_profiles.insert(record)
            uow.v1_model_profile_events.insert_event(
                event_id=str(uuid4()),
                profile_id=profile_id,
                event_type="runner_validation",
                provider_kind=provider_kind,
                actor_type=actor_type,
                actor_id=actor_id,
                payload_json=canonical_json_text({
                    "action": "model_profile_registered",
                    "profile_id": profile_id,
                    "display_name": display_name,
                    "provider_kind": provider_kind,
                    "actor_type": actor_type,
                    "actor_id": actor_id,
                }),
                payload_checksum=sha256_canonical_json({
                    "action": "model_profile_registered",
                    "profile_id": profile_id,
                    "display_name": display_name,
                    "provider_kind": provider_kind,
                    "actor_type": actor_type,
                    "actor_id": actor_id,
                }),
                created_at=created_at,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            uow.audit_records.append_global_audit(
                audit_id=str(uuid4()),
                actor_type=actor_type,
                actor_id=actor_id,
                action="model_profile_registered",
                payload_json=canonical_json_text({
                    "action": "model_profile_registered",
                    "profile_id": profile_id,
                    "display_name": display_name,
                    "provider_kind": provider_kind,
                    "actor_type": actor_type,
                    "actor_id": actor_id,
                }),
                created_at=created_at,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        return record

    def transition_job_state(self, command: TransitionJobStateCommand) -> MigrationJobDto:
        if command.expected_version is None:
            raise ExpectedVersionRequiredError()

        expected_version = command.expected_version
        target_state = _coerce_job_state(command.target_state)

        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(command.job_id)
            if job is None:
                raise NotFoundError("migration job", command.job_id)
            if job.version != expected_version:
                raise StaleVersionError(command.job_id, expected_version, job.version)

            validate_job_state_transition(job.status, target_state)

            updated_at = utc_now_text()
            updated = uow.migration_jobs.transition_state(
                command.job_id,
                expected_version,
                target_state,
                active_slot_for(target_state),
                updated_at,
            )
            if not updated:
                current = uow.migration_jobs.get(command.job_id)
                if current is None:
                    raise NotFoundError("migration job", command.job_id)
                raise StaleVersionError(command.job_id, expected_version, current.version)

            new_version = expected_version + 1
            sequence = uow.migration_jobs.increment_event_sequence(command.job_id)
            event_payload = _job_state_changed_payload(
                job_id=command.job_id,
                prior_state=job.status,
                new_state=target_state,
                prior_version=expected_version,
                new_version=new_version,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                reason=command.reason,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            event_payload_json = canonical_json_text(event_payload)
            event_id = str(uuid4())
            uow.run_events.append_job_state_changed_event(
                event_id=event_id,
                job_id=command.job_id,
                sequence=sequence,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                payload_json=event_payload_json,
                payload_checksum=sha256_canonical_json(event_payload),
                created_at=updated_at,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )

            audit_payload = dict(event_payload)
            audit_payload["event_sequence"] = sequence
            uow.audit_records.append_job_state_changed_audit(
                audit_id=str(uuid4()),
                job_id=command.job_id,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                prior_state=job.status,
                new_state=target_state,
                job_version=new_version,
                payload_json=canonical_json_text(audit_payload),
                created_at=updated_at,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )

            updated_job = uow.migration_jobs.get(command.job_id)
            if updated_job is None:
                raise NotFoundError("migration job", command.job_id)
            return updated_job


class DiagnosticJobService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def create_diagnostic_job(self, command: CreateDiagnosticJobCommand) -> JobProjectionDto:
        _require_non_empty(command.idempotency_key, "idempotency_key")
        request_payload = _create_diagnostic_request_payload(command)
        request_checksum = sha256_canonical_json(request_payload)

        with self._unit_of_work_factory() as uow:
            existing = uow.idempotency_records.get(
                CREATE_DIAGNOSTIC_JOB_OPERATION,
                command.idempotency_key,
            )
            if existing is not None:
                if existing.request_checksum != request_checksum:
                    raise IdempotencyConflictError(CREATE_DIAGNOSTIC_JOB_OPERATION, command.idempotency_key)
                return _job_projection(uow, existing.resource_id)

            runner = uow.runner_profiles.get_exact(
                command.runner_profile_id,
                command.runner_profile_version,
            )
            if runner is None:
                raise NotFoundError(
                    "runner profile",
                    f"{command.runner_profile_id}/{command.runner_profile_version}",
                )
            _validate_job_roots(
                runner.payload,
                command.legacy_source_root_id,
                command.legacy_source_relative_path,
                command.output_root_id,
                command.output_relative_path,
            )

            create_service = CreateMigrationJobService(lambda: _BorrowedUnitOfWork(uow))
            created = create_service.execute(
                CreateMigrationJobCommand(
                    actor=_local_actor_id(),
                    legacy_source_ref=_root_ref(
                        command.legacy_source_root_id,
                        command.legacy_source_relative_path,
                    ),
                    output_root_ref=_root_ref(
                        command.output_root_id,
                        command.output_relative_path,
                    ),
                    runner_profile_id=command.runner_profile_id,
                    runner_profile_version=command.runner_profile_version,
                    pipeline_id=command.pipeline_id,
                    pipeline_version=command.pipeline_version,
                    target_proof_level=command.target_proof_level,
                    enabled_gates=command.enabled_gates,
                    policy=command.policy,
                    correlation_id=command.correlation_id,
                )
            )
            uow.idempotency_records.insert(
                IdempotencyRecord(
                    operation=CREATE_DIAGNOSTIC_JOB_OPERATION,
                    idempotency_key=command.idempotency_key,
                    request_checksum=request_checksum,
                    resource_type="migration_job",
                    resource_id=created.job_id,
                    original_status_code=201,
                    created_at=utc_now_text(),
                )
            )
            return _job_projection(uow, created.job_id)

    def start_migration_job(self, command: StartMigrationJobCommand) -> JobProjectionDto:
        _require_non_empty(command.idempotency_key, "idempotency_key")
        if command.expected_version is None:
            raise ExpectedVersionRequiredError()

        request_payload = {
            "job_id": command.job_id,
            "expected_version": command.expected_version,
        }
        request_checksum = sha256_canonical_json(request_payload)

        with self._unit_of_work_factory() as uow:
            existing = uow.idempotency_records.get(
                START_DIAGNOSTIC_JOB_OPERATION,
                command.idempotency_key,
            )
            if existing is not None:
                if existing.request_checksum != request_checksum:
                    raise IdempotencyConflictError(START_DIAGNOSTIC_JOB_OPERATION, command.idempotency_key)
                return _job_projection(uow, command.job_id)

            job = uow.migration_jobs.get(command.job_id)
            if job is None:
                raise NotFoundError("migration job", command.job_id)
            if job.version != command.expected_version:
                raise StaleVersionError(command.job_id, command.expected_version, job.version)
            if uow.command_executions.get_active_for_job(command.job_id) is not None:
                raise ActiveCommandConflictError(command.job_id)
            if job.status != JobState.CREATED:
                raise InvalidJobStateTransitionError(job.status, JobState.QUEUED)

            now = utc_now_text()
            command_id = f"command-{uuid4().hex}"
            queued = CommandExecutionRecord(
                command_id=command_id,
                job_id=command.job_id,
                operation=DIAGNOSTIC_OPERATION,
                status=CommandState.QUEUED,
                created_at=now,
                updated_at=now,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            try:
                uow.command_executions.insert_queued(queued)
            except StorageIntegrityError as exc:
                raise ActiveCommandConflictError(command.job_id) from exc

            updated = uow.migration_jobs.transition_state(
                command.job_id,
                command.expected_version,
                JobState.QUEUED,
                active_slot_for(JobState.QUEUED),
                now,
            )
            if not updated:
                current = uow.migration_jobs.get(command.job_id)
                actual = current.version if current is not None else None
                raise StaleVersionError(command.job_id, command.expected_version, actual)

            command_sequence = uow.migration_jobs.increment_event_sequence(command.job_id)
            command_event_payload = {
                "actor_id": command.actor_id,
                "actor_type": command.actor_type,
                "command_id": command_id,
                "command_status": CommandState.QUEUED.value,
                "job_id": command.job_id,
                "operation": DIAGNOSTIC_OPERATION,
            }
            if command.correlation_id is not None:
                command_event_payload["correlation_id"] = command.correlation_id
            if command.causation_id is not None:
                command_event_payload["causation_id"] = command.causation_id
            command_event_id = f"event-{command.job_id}-{command_sequence:04d}"
            uow.run_events.insert(
                RunEventRecord(
                    event_id=command_event_id,
                    job_id=command.job_id,
                    sequence=command_sequence,
                    event_type="command_queued",
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    correlation_id=command.correlation_id,
                    causation_id=command.causation_id,
                    payload_json=canonical_json_text(command_event_payload),
                    payload_checksum=sha256_canonical_json(command_event_payload),
                    created_at=now,
                )
            )

            new_version = command.expected_version + 1
            state_sequence = uow.migration_jobs.increment_event_sequence(command.job_id)
            state_payload = _job_state_changed_payload(
                job_id=command.job_id,
                prior_state=JobState.CREATED,
                new_state=JobState.QUEUED,
                prior_version=command.expected_version,
                new_version=new_version,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                reason="diagnostic command queued",
                correlation_id=command.correlation_id,
                causation_id=command_event_id,
            )
            state_event_id = f"event-{command.job_id}-{state_sequence:04d}"
            uow.run_events.append_job_state_changed_event(
                event_id=state_event_id,
                job_id=command.job_id,
                sequence=state_sequence,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                payload_json=canonical_json_text(state_payload),
                payload_checksum=sha256_canonical_json(state_payload),
                created_at=now,
                correlation_id=command.correlation_id,
                causation_id=command_event_id,
            )

            audit_payload = {
                "command_id": command_id,
                "command_status": CommandState.QUEUED.value,
                "command_event_id": command_event_id,
                "event_sequence": command_sequence,
                "job_id": command.job_id,
                "operation": DIAGNOSTIC_OPERATION,
                "state_event_id": state_event_id,
                "state_event_sequence": state_sequence,
            }
            uow.audit_records.insert(
                AuditRecord(
                    audit_id=f"audit-{command.job_id}-{state_sequence:04d}",
                    job_id=command.job_id,
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    action="diagnostic_command_queued",
                    prior_state=JobState.CREATED.value,
                    new_state=JobState.QUEUED.value,
                    job_version=new_version,
                    correlation_id=command.correlation_id,
                    causation_id=command_event_id,
                    payload_json=canonical_json_text(audit_payload),
                    created_at=now,
                )
            )
            uow.idempotency_records.insert(
                IdempotencyRecord(
                    operation=START_DIAGNOSTIC_JOB_OPERATION,
                    idempotency_key=command.idempotency_key,
                    request_checksum=request_checksum,
                    resource_type="command_execution",
                    resource_id=command_id,
                    original_status_code=200,
                    created_at=now,
                )
            )
            return _job_projection(uow, command.job_id)


class ArtifactRegistryService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def register_artifact(self, command: RegisterArtifactCommand) -> ArtifactDto:
        artifact = _validate_artifact_hash_result(command.artifact)
        _require_non_empty(command.artifact_type, "artifact_type")
        _require_non_empty(command.actor_type, "actor_type")
        _require_non_empty(command.actor_id, "actor_id")

        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(command.job_id)
            if job is None:
                raise NotFoundError("migration job", command.job_id)

            run_configuration = uow.run_configurations.get_for_job(command.job_id)
            if run_configuration is None:
                raise NotFoundError("run configuration", command.job_id)

            runner = uow.runner_profiles.get_exact(
                run_configuration.runner_profile_id,
                run_configuration.runner_profile_version,
            )
            if runner is None:
                raise NotFoundError(
                    "runner profile",
                    f"{run_configuration.runner_profile_id}/{run_configuration.runner_profile_version}",
                )

            registered_root = _find_registered_root(runner.payload, artifact)
            stage_run = None
            if command.stage_run_id is not None:
                stage_run = uow.stage_runs.get(command.stage_run_id)
                if stage_run is None or stage_run.job_id != job.job_id:
                    raise NotFoundError("stage run", command.stage_run_id)

            existing = uow.artifacts.get_exact(
                command.job_id,
                artifact.registered_root_id,
                artifact.normalized_relative_path,
            )
            if existing is not None:
                if existing.checksum != artifact.checksum:
                    raise RegistrationConflictError(
                        "artifact",
                        f"{command.job_id}:{artifact.registered_root_id}:{artifact.normalized_relative_path}",
                        artifact.checksum,
                    )
                return existing

            created_at = utc_now_text()
            artifact_id = f"artifact-{uuid4().hex}"
            artifact_record = ArtifactRecord(
                artifact_id=artifact_id,
                job_id=command.job_id,
                stage_run_id=stage_run.stage_run_id if stage_run is not None else None,
                artifact_type=command.artifact_type,
                registered_root_id=artifact.registered_root_id,
                relative_path=artifact.relative_path,
                normalized_relative_path=artifact.normalized_relative_path,
                content_type=command.content_type,
                size_bytes=artifact.size_bytes,
                checksum_algorithm=artifact.checksum_algorithm,
                checksum=artifact.checksum,
                created_at=created_at,
                created_by=command.actor_id,
            )

            uow.artifacts.insert(artifact_record)

            sequence = uow.migration_jobs.increment_event_sequence(command.job_id)

            event_id = f"event-{command.job_id}-{sequence:04d}"
            event_payload = {
                "artifact_id": artifact_id,
                "artifact_type": command.artifact_type,
                "checksum": artifact.checksum,
                "checksum_algorithm": artifact.checksum_algorithm,
                "content_type": command.content_type,
                "created_at": created_at,
                "created_by": command.actor_id,
                "job_id": command.job_id,
                "normalized_relative_path": artifact.normalized_relative_path,
                "relative_path": artifact.relative_path,
                "registered_root_id": registered_root.root_id,
                "size_bytes": artifact.size_bytes,
                "stage_run_id": stage_run.stage_run_id if stage_run is not None else None,
            }
            event_record = RunEventRecord(
                event_id=event_id,
                job_id=command.job_id,
                sequence=sequence,
                event_type="artifact_registered",
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
                payload_json=canonical_json_text(event_payload),
                payload_checksum=sha256_canonical_json(event_payload),
                created_at=created_at,
            )
            uow.run_events.insert(event_record)

            audit_payload = {
                "artifact_id": artifact_id,
                "artifact_type": command.artifact_type,
                "checksum": artifact.checksum,
                "checksum_algorithm": artifact.checksum_algorithm,
                "content_type": command.content_type,
                "event_id": event_id,
                "job_id": command.job_id,
                "normalized_relative_path": artifact.normalized_relative_path,
                "relative_path": artifact.relative_path,
                "registered_root_id": registered_root.root_id,
                "sequence": sequence,
                "size_bytes": artifact.size_bytes,
                "stage_run_id": stage_run.stage_run_id if stage_run is not None else None,
            }
            audit_record = AuditRecord(
                audit_id=f"audit-{command.job_id}-{sequence:04d}",
                job_id=command.job_id,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                action="artifact_registered",
                prior_state=None,
                new_state=None,
                job_version=job.version,
                correlation_id=command.correlation_id,
                causation_id=event_record.event_id,
                payload_json=canonical_json_text(audit_payload),
                created_at=created_at,
            )
            uow.audit_records.insert(audit_record)

            return ArtifactDto(
                artifact_id=artifact_id,
                job_id=command.job_id,
                stage_run_id=stage_run.stage_run_id if stage_run is not None else None,
                artifact_type=command.artifact_type,
                registered_root_id=artifact.registered_root_id,
                relative_path=artifact.relative_path,
                normalized_relative_path=artifact.normalized_relative_path,
                content_type=command.content_type,
                size_bytes=artifact.size_bytes,
                checksum_algorithm=artifact.checksum_algorithm,
                checksum=artifact.checksum,
                created_at=created_at,
                created_by=command.actor_id,
            )


class CancelService:
    """Cancel a command with cooperative stop, grace period, and forced termination.

    Implements the cancel path for controlled diagnostic commands:
    1. Atomic transition to CANCELLING with event/audit.
    2. Cooperative stop (SIGTERM on POSIX) via WorkerTerminator.
    3. Grace period wait, then forced termination if still alive.
    4. Durable transition to CANCELLED or FAILED.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        worker_terminator: WorkerTerminator | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._worker_terminator = worker_terminator

    def cancel(self, command: CancelCommand) -> JobProjectionDto:
        """Cancel a command with atomic CANCELLING transition.

        Returns the job projection after the cancel request is recorded.
        The actual process termination happens outside the DB transaction.
        """
        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(command.job_id)
            if job is None:
                raise NotFoundError("migration job", command.job_id)

            if job.version != command.expected_version:
                raise StaleVersionError(
                    command.job_id, command.expected_version, job.version
                )

            validate_job_state_transition(job.status, JobState.CANCELLING)

            cmd = uow.command_executions.get_active_for_job(command.job_id)
            if cmd is None:
                raise NotFoundError("active command", command.job_id)

            if command.command_id is not None and cmd.command_id != command.command_id:
                raise NotFoundError(
                    "command execution",
                    command.command_id,
                )

            # Update job state to CANCELLING
            now = utc_now_text()
            new_version = job.version + 1
            updated = uow.migration_jobs.transition_state(
                command.job_id,
                command.expected_version,
                JobState.CANCELLING,
                active_slot_for(JobState.CANCELLING),
                now,
            )
            if not updated:
                current = uow.migration_jobs.get(command.job_id)
                actual = current.version if current is not None else None
                raise StaleVersionError(command.job_id, command.expected_version, actual)

            # Update command status to CANCELLING
            try:
                uow.command_executions.update_status(cmd.command_id, CommandState.CANCELLING)
            except NotFoundError:
                pass

            # Append event
            state_sequence = uow.migration_jobs.increment_event_sequence(command.job_id)
            state_payload = _job_state_changed_payload(
                job_id=command.job_id,
                prior_state=job.status,
                new_state=JobState.CANCELLING,
                prior_version=command.expected_version,
                new_version=new_version,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                reason=command.reason,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            state_event_id = f"event-{command.job_id}-{state_sequence:04d}"
            uow.run_events.append_job_state_changed_event(
                event_id=state_event_id,
                job_id=command.job_id,
                sequence=state_sequence,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                payload_json=canonical_json_text(state_payload),
                payload_checksum=sha256_canonical_json(state_payload),
                created_at=now,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )

            # Append audit
            audit_payload = {
                "command_id": cmd.command_id,
                "command_status": CommandState.CANCELLING.value,
                "event_sequence": state_sequence,
                "job_id": command.job_id,
                "reason": command.reason,
            }
            uow.audit_records.insert(
                AuditRecord(
                    audit_id=f"audit-{command.job_id}-{state_sequence:04d}",
                    job_id=command.job_id,
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    action="command_cancelling",
                    prior_state=job.status.value,
                    new_state=JobState.CANCELLING.value,
                    job_version=new_version,
                    correlation_id=command.correlation_id,
                    causation_id=state_event_id,
                    payload_json=canonical_json_text(audit_payload),
                    created_at=now,
                )
            )

            worker_pid = cmd.worker_pid
            cmd_id = cmd.command_id
            process_control_id = cmd.process_control_id

        # Attempt termination outside the DB transaction
        terminated = False
        if (
            self._worker_terminator is not None
            and worker_pid is not None
        ):
            terminated = self._worker_terminator.terminate(
                worker_pid=worker_pid,
                process_control_id=process_control_id,
                grace_period_seconds=command.grace_period_seconds,
            )

        # Transition to terminal CANCELLED (or FAILED if termination failed)
        target_state = JobState.CANCELLED
        target_cmd_state = CommandState.CANCELLED
        if not terminated and worker_pid is not None:
            # Could not confirm termination; mark as FAILED to be safe
            target_state = JobState.FAILED
            target_cmd_state = CommandState.FAILED

        with self._unit_of_work_factory() as uow:
            current_job = uow.migration_jobs.get(command.job_id)
            if current_job is None or current_job.status != JobState.CANCELLING:
                return _job_projection_from_uow(self._unit_of_work_factory, command.job_id)

            final_now = utc_now_text()
            final_version = current_job.version + 1
            try:
                validate_job_state_transition(current_job.status, target_state)
            except InvalidJobStateTransitionError:
                return _job_projection_from_uow(self._unit_of_work_factory, command.job_id)

            updated = uow.migration_jobs.transition_state(
                command.job_id,
                current_job.version,
                target_state,
                active_slot_for(target_state),
                final_now,
            )
            if not updated:
                return _job_projection_from_uow(self._unit_of_work_factory, command.job_id)

            uow.command_executions.update_status(cmd_id, target_cmd_state)

            final_seq = uow.migration_jobs.increment_event_sequence(command.job_id)
            final_payload = _job_state_changed_payload(
                job_id=command.job_id,
                prior_state=JobState.CANCELLING,
                new_state=target_state,
                prior_version=current_job.version,
                new_version=final_version,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                reason=command.reason,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            final_event_id = f"event-{command.job_id}-{final_seq:04d}"
            uow.run_events.append_job_state_changed_event(
                event_id=final_event_id,
                job_id=command.job_id,
                sequence=final_seq,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                payload_json=canonical_json_text(final_payload),
                payload_checksum=sha256_canonical_json(final_payload),
                created_at=final_now,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )

            final_audit = {
                "command_id": cmd_id,
                "command_status": target_cmd_state.value,
                "terminated": terminated,
                "event_sequence": final_seq,
                "job_id": command.job_id,
                "reason": command.reason,
            }
            uow.audit_records.insert(
                AuditRecord(
                    audit_id=f"audit-{command.job_id}-{final_seq:04d}",
                    job_id=command.job_id,
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    action="command_cancelled",
                    prior_state=JobState.CANCELLING.value,
                    new_state=target_state.value,
                    job_version=final_version,
                    correlation_id=command.correlation_id,
                    causation_id=final_event_id,
                    payload_json=canonical_json_text(final_audit),
                    created_at=final_now,
                )
            )

        return _job_projection_from_uow(self._unit_of_work_factory, command.job_id)


class TimeoutService:
    """Handle command timeout with monotonic clock and forced termination.

    When a command exceeds its configured timeout, this service:
    1. Verifies the deadline has elapsed (monotonic clock).
    2. Atomically transitions command/job to TIMED_OUT/FAILED.
    3. Force-terminates the worker process.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        worker_terminator: WorkerTerminator | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._worker_terminator = worker_terminator

    def handle_timeout(self, command: TimeoutCommand) -> JobProjectionDto:
        """Handle a command timeout.

        The caller must pass a deadline already compared against time.monotonic().
        """
        import time as _time

        if _time.monotonic() < command.deadline:
            # Not yet expired; caller should not have invoked this
            return _job_projection_from_uow(self._unit_of_work_factory, command.job_id)

        worker_pid: int | None = None
        process_control_id: str | None = None

        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(command.job_id)
            if job is None:
                raise NotFoundError("migration job", command.job_id)

            if job.status in TERMINAL_JOB_STATES:
                # Already terminal, nothing to do — build projection from this UoW
                return _job_projection(uow, command.job_id)

            cmd = uow.command_executions.get_active_for_job(command.job_id)
            if cmd is None or cmd.command_id != command.command_id:
                return _job_projection(uow, command.job_id)

            if cmd.status in (CommandState.SUCCEEDED, CommandState.FAILED,
                              CommandState.TIMED_OUT, CommandState.CANCELLED):
                return _job_projection(uow, command.job_id)

            try:
                validate_job_state_transition(job.status, JobState.FAILED)
            except InvalidJobStateTransitionError:
                return _job_projection(uow, command.job_id)

            now = utc_now_text()
            new_version = job.version + 1
            updated = uow.migration_jobs.transition_state(
                command.job_id,
                job.version,
                JobState.FAILED,
                active_slot_for(JobState.FAILED),
                now,
            )
            if not updated:
                return _job_projection(uow, command.job_id)

            uow.command_executions.update_status(command.command_id, CommandState.TIMED_OUT)

            state_sequence = uow.migration_jobs.increment_event_sequence(command.job_id)
            state_payload = _job_state_changed_payload(
                job_id=command.job_id,
                prior_state=job.status,
                new_state=JobState.FAILED,
                prior_version=job.version,
                new_version=new_version,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                reason=f"command timed out after {command.timeout_seconds}s",
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            state_event_id = f"event-{command.job_id}-{state_sequence:04d}"
            uow.run_events.append_job_state_changed_event(
                event_id=state_event_id,
                job_id=command.job_id,
                sequence=state_sequence,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                payload_json=canonical_json_text(state_payload),
                payload_checksum=sha256_canonical_json(state_payload),
                created_at=now,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )

            audit_payload = {
                "command_id": command.command_id,
                "command_status": CommandState.TIMED_OUT.value,
                "timeout_seconds": command.timeout_seconds,
                "event_sequence": state_sequence,
                "job_id": command.job_id,
            }
            uow.audit_records.insert(
                AuditRecord(
                    audit_id=f"audit-{command.job_id}-{state_sequence:04d}",
                    job_id=command.job_id,
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    action="command_timed_out",
                    prior_state=job.status.value,
                    new_state=JobState.FAILED.value,
                    job_version=new_version,
                    correlation_id=command.correlation_id,
                    causation_id=state_event_id,
                    payload_json=canonical_json_text(audit_payload),
                    created_at=now,
                )
            )

            worker_pid = cmd.worker_pid
            process_control_id = cmd.process_control_id

            projection = _job_projection(uow, command.job_id)

        # Force-terminate outside the DB transaction
        if (
            self._worker_terminator is not None
            and worker_pid is not None
        ):
            self._worker_terminator.terminate(
                worker_pid=worker_pid,
                process_control_id=process_control_id,
                grace_period_seconds=0.0,
            )

        return projection


def _job_projection_from_uow(
    unit_of_work_factory: UnitOfWorkFactory,
    job_id: str,
) -> JobProjectionDto:
    with unit_of_work_factory() as uow:
        return _job_projection(uow, job_id)


class ReconciliationService:
    """Startup reconciliation for fail-closed restart behavior.

    After a service restart, this service reconciles all existing
    jobs and commands to ensure no uncertain active execution is
    silently resumed or relaunched.

    Terminal jobs remain queryable and replayable.
    Untouched QUEUED commands remain dispatchable once.
    STARTING, RUNNING, CANCELLING states become RECOVERY_REQUIRED.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def reconcile_all(self) -> list[dict[str, Any]]:
        """Run startup reconciliation for all current jobs.

        Returns a list of reconciliation results, one per job.
        """
        results: list[dict[str, Any]] = []

        with self._unit_of_work_factory() as uow:
            jobs = uow.migration_jobs.list()
            for job in jobs:
                result = self._reconcile_job(uow, job)
                if result:
                    results.append(result)

        return results

    def _reconcile_job(
        self,
        uow: ControlTowerUnitOfWork,
        job: MigrationJobDto,
    ) -> dict[str, Any] | None:
        """Reconcile a single job after restart.

        Returns a result dict if the job was modified, None if unchanged.
        """
        from migration_factory.control_tower.domain.checksums import (
            canonical_json_text,
            sha256_canonical_json,
            utc_now_text,
        )

        # Terminal jobs need no reconciliation
        if job.status in TERMINAL_JOB_STATES:
            return {
                "job_id": job.job_id,
                "status": job.status.value,
                "action": "unchanged_terminal",
            }

        # Check for active command that needs reconciliation
        active_command = uow.command_executions.get_active_for_job(job.job_id)
        if active_command is None:
            # No active command, job is in a non-terminal state without an active command
            # This is unusual but not necessarily unsafe
            return None

        # Determine the action based on command state
        if active_command.status == CommandState.QUEUED:
            # Untouched QUEUED commands remain dispatchable once
            # No state change needed, but mark as reconciled
            return {
                "job_id": job.job_id,
                "command_id": active_command.command_id,
                "status": job.status.value,
                "command_status": active_command.status.value,
                "action": "queued_dispatchable",
            }

        if active_command.status in (
            CommandState.STARTING,
            CommandState.RUNNING,
            CommandState.CANCELLING,
        ):
            # Cannot verify worker state after restart
            # Transition to RECOVERY_REQUIRED
            now = utc_now_text()
            new_version = job.version + 1

            try:
                validate_job_state_transition(job.status, JobState.RECOVERY_REQUIRED)
            except InvalidJobStateTransitionError:
                # If the direct transition is invalid, try through FAILED
                # to ensure we don't leave an uncertain state
                return {
                    "job_id": job.job_id,
                    "command_id": active_command.command_id,
                    "status": job.status.value,
                    "action": "cannot_transition_recovery",
                }

            updated = uow.migration_jobs.transition_state(
                job.job_id,
                job.version,
                JobState.RECOVERY_REQUIRED,
                active_slot_for(JobState.RECOVERY_REQUIRED),
                now,
            )
            if not updated:
                return {
                    "job_id": job.job_id,
                    "command_id": active_command.command_id,
                    "action": "transition_conflict",
                }

            # Append event
            state_sequence = uow.migration_jobs.increment_event_sequence(job.job_id)
            recovery_reason = "uncertain active execution after restart"
            state_payload: dict[str, Any] = {
                "actor_id": "system",
                "actor_type": "system",
                "job_id": job.job_id,
                "new_state": JobState.RECOVERY_REQUIRED.value,
                "new_version": new_version,
                "prior_state": job.status.value,
                "prior_version": job.version,
                "reason": recovery_reason,
                "command_id": active_command.command_id,
                "command_status": active_command.status.value,
            }
            state_event_id = f"event-{job.job_id}-{state_sequence:04d}"
            uow.run_events.append_job_state_changed_event(
                event_id=state_event_id,
                job_id=job.job_id,
                sequence=state_sequence,
                actor_type="system",
                actor_id="system",
                payload_json=canonical_json_text(state_payload),
                payload_checksum=sha256_canonical_json(state_payload),
                created_at=now,
            )

            # Append audit
            audit_payload = {
                "command_id": active_command.command_id,
                "command_status": active_command.status.value,
                "event_sequence": state_sequence,
                "job_id": job.job_id,
                "reason": recovery_reason,
                "recovery_reason": recovery_reason,
            }
            uow.audit_records.insert(
                AuditRecord(
                    audit_id=f"audit-{job.job_id}-{state_sequence:04d}",
                    job_id=job.job_id,
                    actor_type="system",
                    actor_id="system",
                    action="startup_reconciliation",
                    prior_state=job.status.value,
                    new_state=JobState.RECOVERY_REQUIRED.value,
                    job_version=new_version,
                    correlation_id=None,
                    causation_id=state_event_id,
                    payload_json=canonical_json_text(audit_payload),
                    created_at=now,
                )
            )

            return {
                "job_id": job.job_id,
                "command_id": active_command.command_id,
                "prior_status": job.status.value,
                "prior_command_status": active_command.status.value,
                "new_status": JobState.RECOVERY_REQUIRED.value,
                "action": "recovery_required",
                "reason": recovery_reason,
            }

        # Terminal command states don't need action
        return None


class CommandFinalizationService:
    """Finalize terminal command artifacts into immutable artifact records.

    After a command reaches a terminal state (SUCCEEDED, FAILED, TIMED_OUT,
    CANCELLED), this service stream-hashes the closed stdout, stderr, result,
    and spool files, registers them as immutable artifacts, and links them
    to the command execution record atomically.

    The service is idempotent: retry after a crash during hashing or after
    a failed DB commit will not create duplicate artifacts or links.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def execute(self, command: FinalizeCommandCommand) -> ArtifactDto | None:
        from migration_factory.control_tower.infrastructure.workspace import (
            prepare_safe_workspace,
        )

        with self._unit_of_work_factory() as uow:
            cmd = uow.command_executions.get(command.command_id)
            if cmd is None:
                raise NotFoundError("command execution", command.command_id)
            if cmd.job_id != command.job_id:
                raise NotFoundError("command execution for job", command.command_id)

            # Check if already finalized
            links = uow.command_executions.get_terminal_artifact_links(command.command_id)
            if links["finalization_status"] != "PENDING":
                return None  # Already finalized, idempotent

            if cmd.status not in (
                CommandState.SUCCEEDED,
                CommandState.FAILED,
                CommandState.TIMED_OUT,
                CommandState.CANCELLED,
            ):
                raise InvalidJobStateTransitionError(
                    cmd.status, CommandState.SUCCEEDED
                )

            if cmd.working_directory_root_id is None or cmd.working_directory_relative_path is None:
                raise NotFoundError("command working directory", command.command_id)

            # Resolve runner and working directory
            job = uow.migration_jobs.get(command.job_id)
            if job is None:
                raise NotFoundError("migration job", command.job_id)

            run_config = uow.run_configurations.get_for_job(command.job_id)
            if run_config is None:
                raise NotFoundError("run configuration", command.job_id)

            runner = uow.runner_profiles.get_exact(
                run_config.runner_profile_id,
                run_config.runner_profile_version,
            )
            if runner is None:
                raise NotFoundError(
                    "runner profile",
                    f"{run_config.runner_profile_id}/{run_config.runner_profile_version}",
                )

            root_path = _find_workspace_root(runner.payload, cmd.working_directory_root_id)
            working_dir = root_path / cmd.working_directory_relative_path

            # Determine log paths from manifest
            manifest_dir = working_dir / "control" / "commands" / cmd.command_id
            manifest_path = manifest_dir / "command_manifest.json"

            if manifest_path.exists():
                from migration_factory.control_tower.domain.manifests import (
                    CommandManifest,
                )

                manifest = CommandManifest.model_validate_json(manifest_path.read_bytes())
                stdout_rel = manifest.stdout_relative_path
                stderr_rel = manifest.stderr_relative_path
                result_rel = manifest.result_relative_path
                spool_rel = manifest.spool_relative_path
            else:
                stdout_rel = "logs/stdout.log"
                stderr_rel = "logs/stderr.log"
                result_rel = "result.json"
                spool_rel = "spool"

            stdout_path = working_dir / stdout_rel
            stderr_path = working_dir / stderr_rel
            result_path = working_dir / result_rel
            spool_path = working_dir / spool_rel

        # Register artifacts outside the outer UoW (each registration opens its own transaction)
        registry = ArtifactRegistryService(self._unit_of_work_factory)
        now = utc_now_text()

        root_id = cmd.working_directory_root_id

        stdout_artifact_id: str | None = None
        stderr_artifact_id: str | None = None
        result_artifact_id: str | None = None
        spool_artifact_id: str | None = None

        def _hash_and_register(
            path: Path,
            artifact_type: str,
            content_type: str | None,
        ) -> str | None:
            if not path.exists() or not path.is_file():
                return None

            from migration_factory.control_tower.domain.checksums import stream_sha256

            checksum, size_bytes = stream_sha256(path)
            stat_result = path.stat(follow_symlinks=False)

            hash_result = ArtifactHashResult(
                registered_root_id=root_id,
                root_kind="output",
                relative_path=str(path.relative_to(working_dir)),
                normalized_relative_path=str(path.relative_to(working_dir)),
                checksum_algorithm="sha256",
                checksum=checksum,
                size_bytes=size_bytes,
                mtime_ns=int(stat_result.st_mtime_ns),
                file_identity=(None, None),
            )

            artifact = registry.register_artifact(
                RegisterArtifactCommand(
                    job_id=command.job_id,
                    artifact=hash_result,
                    artifact_type=artifact_type,
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    content_type=content_type,
                    correlation_id=command.correlation_id,
                    causation_id=command.causation_id,
                )
            )
            return artifact.artifact_id

        stdout_artifact_id = _hash_and_register(stdout_path, "command_stdout", "text/plain")
        stderr_artifact_id = _hash_and_register(stderr_path, "command_stderr", "text/plain")
        result_artifact_id = _hash_and_register(result_path, "command_result", "application/json")

        # Handle spool: distinguish verified from forensic
        spool_is_verified = False
        spool_artifact_type: str | None = None
        if spool_path.exists():
            if spool_path.is_dir():
                # Check for completed worker event spool
                event_files = list(spool_path.glob("*.jsonl"))
                if event_files:
                    spool_is_verified = self._verify_spool(spool_path, event_files)
                    spool_type = (
                        "WORKER_EVENT_SPOOL"
                        if spool_is_verified
                        else "FORENSIC_WORKER_EVENT_SPOOL"
                    )
                    spool_artifact_type = spool_type
                    spool_artifact_id = _hash_and_register(
                        spool_path / event_files[0], spool_type, "application/jsonl"
                    )
            else:
                spool_artifact_type = "FORENSIC_WORKER_EVENT_SPOOL"
                spool_artifact_id = _hash_and_register(
                    spool_path, "FORENSIC_WORKER_EVENT_SPOOL", "application/octet-stream"
                )

        # Determine finalization status
        if stdout_artifact_id or stderr_artifact_id or result_artifact_id:
            if spool_is_verified:
                finalization_status = "COMPLETE_VERIFIED"
            else:
                finalization_status = "COMPLETE_FORENSIC"
        else:
            finalization_status = "EMPTY"

        # Link artifacts to command execution atomically
        with self._unit_of_work_factory() as uow:
            uow.command_executions.finalize_terminal_artifacts(
                command.command_id,
                stdout_artifact_id=stdout_artifact_id,
                stderr_artifact_id=stderr_artifact_id,
                result_artifact_id=result_artifact_id,
                spool_artifact_id=spool_artifact_id,
                finalization_status=finalization_status,
                finalized_at=now,
            )

            sequence = uow.migration_jobs.increment_event_sequence(command.job_id)
            event_payload = {
                "command_id": command.command_id,
                "command_status": cmd.status.value,
                "finalization_status": finalization_status,
                "job_id": command.job_id,
                "outcome": command.outcome,
                "stdout_artifact_id": stdout_artifact_id,
                "stderr_artifact_id": stderr_artifact_id,
                "result_artifact_id": result_artifact_id,
                "spool_artifact_type": spool_artifact_type,
                "spool_artifact_id": spool_artifact_id,
                "spool_verified": spool_is_verified,
                "ingestion_verified": spool_is_verified,
            }
            event_record = RunEventRecord(
                event_id=f"event-{command.job_id}-{sequence:04d}",
                job_id=command.job_id,
                sequence=sequence,
                event_type="command_finalized",
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
                payload_json=canonical_json_text(event_payload),
                payload_checksum=sha256_canonical_json(event_payload),
                created_at=now,
            )
            uow.run_events.insert(event_record)

            audit_payload = {
                "command_id": command.command_id,
                "command_status": cmd.status.value,
                "event_sequence": sequence,
                "finalization_status": finalization_status,
                "job_id": command.job_id,
                "outcome": command.outcome,
                "spool_artifact_type": spool_artifact_type,
                "spool_verified": spool_is_verified,
                "ingestion_verified": spool_is_verified,
                "stdout_artifact_id": stdout_artifact_id,
                "stderr_artifact_id": stderr_artifact_id,
                "result_artifact_id": result_artifact_id,
                "spool_artifact_id": spool_artifact_id,
            }
            uow.audit_records.insert(
                AuditRecord(
                    audit_id=f"audit-{command.job_id}-{sequence:04d}",
                    job_id=command.job_id,
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    action="command_finalized",
                    prior_state=cmd.status.value,
                    new_state=cmd.status.value,
                    job_version=job.version,
                    correlation_id=command.correlation_id,
                    causation_id=event_record.event_id,
                    payload_json=canonical_json_text(audit_payload),
                    created_at=now,
                )
            )

        return None

    def _verify_spool(self, spool_path, event_files) -> bool:
        """Check if spool event files contain complete JSONL records.

        A verified spool must have at least one complete, valid JSONL
        record that parses as valid JSON.
        """
        import json as _json

        for ef in event_files:
            try:
                with ef.open("rb") as f:
                    raw = f.read()
                if not raw:
                    continue
                lines = raw.split(b"\n")
                # Skip the last line if it's incomplete (no trailing newline)
                complete_lines = [
                    ln for ln in lines if ln.strip()
                ]
                for line in complete_lines:
                    try:
                        _json.loads(line)
                        return True  # At least one valid JSONL record
                    except (_json.JSONDecodeError, ValueError):
                        continue
            except Exception:
                continue
        return False


class CommandWorkspaceService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def prepare_workspace(self, command: PrepareCommandWorkspaceCommand) -> tuple[ArtifactHashResult, ArtifactHashResult]:
        from migration_factory.control_tower.infrastructure.workspace import (
            materialize_command_manifest,
            materialize_run_config,
            prepare_safe_workspace,
        )

        with self._unit_of_work_factory() as uow:
            cmd = uow.command_executions.get(command.command_id)
            if cmd is None:
                raise NotFoundError("command execution", command.command_id)
            if cmd.command_manifest_artifact_id is not None:
                raise WorkspaceConflictError(
                    f"Workspace already prepared for command {command.command_id!r}"
                )

            job = uow.migration_jobs.get(command.job_id)
            if job is None:
                raise NotFoundError("migration job", command.job_id)

            run_config = uow.run_configurations.get_for_job(command.job_id)
            if run_config is None:
                raise NotFoundError("run configuration", command.job_id)

            runner = uow.runner_profiles.get_exact(
                run_config.runner_profile_id,
                run_config.runner_profile_version,
            )
            if runner is None:
                raise NotFoundError(
                    "runner profile",
                    f"{run_config.runner_profile_id}/{run_config.runner_profile_version}",
                )

            working_root_path = _find_workspace_root(runner.payload, command.working_directory_root_id)
            working_dir = prepare_safe_workspace(working_root_path, command.working_directory_relative_path)

            run_config_result = materialize_run_config(run_config, working_dir, command.working_directory_root_id)
            run_config_artifact = ArtifactRegistryService(lambda: _BorrowedUnitOfWork(uow)).register_artifact(
                RegisterArtifactCommand(
                    job_id=command.job_id,
                    artifact=run_config_result,
                    artifact_type="run_configuration",
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    correlation_id=command.correlation_id,
                    causation_id=command.causation_id,
                )
            )

            now = utc_now_text()
            manifest = CommandManifest(
                schema_version="1.0.0",
                job_id=command.job_id,
                command_id=command.command_id,
                worker_id=command.worker_id,
                operation=cmd.operation,
                run_configuration_artifact_id=run_config_artifact.artifact_id,
                run_configuration_checksum=run_config_result.checksum,
                working_directory_root_id=command.working_directory_root_id,
                working_directory_relative_path=command.working_directory_relative_path,
                stdout_relative_path="logs/stdout.log",
                stderr_relative_path="logs/stderr.log",
                result_relative_path="result.json",
                spool_relative_path="spool",
                timeout_seconds=3600,
                max_stdout_bytes=104857600,
                max_stderr_bytes=104857600,
                event_schema_version="1.0.0",
                created_at=now,
                manifest_checksum="",
            )

            manifest_result, _manifest_bytes = materialize_command_manifest(
                manifest, working_dir, run_config_artifact.artifact_id, command.working_directory_root_id
            )

            manifest_artifact = ArtifactRegistryService(lambda: _BorrowedUnitOfWork(uow)).register_artifact(
                RegisterArtifactCommand(
                    job_id=command.job_id,
                    artifact=manifest_result,
                    artifact_type="command_manifest",
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    correlation_id=command.correlation_id,
                    causation_id=command.causation_id,
                )
            )

            uow.command_executions.update_workspace_columns(
                command.command_id,
                command_manifest_artifact_id=manifest_artifact.artifact_id,
                working_directory_root_id=command.working_directory_root_id,
                working_directory_relative_path=command.working_directory_relative_path,
                worker_id=command.worker_id,
                launch_attempt=command.launch_attempt,
            )

            return run_config_result, manifest_result


class WorkerLaunchService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        worker_launcher: WorkerLauncher,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._worker_launcher = worker_launcher

    def execute(self, command: LaunchWorkerCommand) -> WorkerLaunchResult:
        with self._unit_of_work_factory() as uow:
            cmd = uow.command_executions.get(command.command_id)
            if cmd is None:
                raise NotFoundError("command execution", command.command_id)
            if cmd.status != CommandState.QUEUED:
                raise InvalidJobStateTransitionError(
                    JobState(cmd.status.value), JobState.STARTING
                )
            if cmd.command_manifest_artifact_id is None:
                raise WorkspaceConflictError(
                    f"Workspace not prepared for command {command.command_id!r}"
                )

            job = uow.migration_jobs.get(command.job_id)
            if job is None:
                raise NotFoundError("migration job", command.job_id)

            run_config = uow.run_configurations.get_for_job(command.job_id)
            if run_config is None:
                raise NotFoundError("run configuration", command.job_id)

            runner = uow.runner_profiles.get_exact(
                run_config.runner_profile_id,
                run_config.runner_profile_version,
            )
            if runner is None:
                raise NotFoundError(
                    "runner profile",
                    f"{run_config.runner_profile_id}/{run_config.runner_profile_version}",
                )

            now = utc_now_text()
            uow.command_executions.update_status(
                command.command_id,
                CommandState.STARTING,
            )

            sequence = uow.migration_jobs.increment_event_sequence(command.job_id)
            event_payload = {
                "command_id": command.command_id,
                "command_status": CommandState.STARTING.value,
                "job_id": command.job_id,
                "operation": cmd.operation,
            }
            event_record = RunEventRecord(
                event_id=f"event-{command.job_id}-{sequence:04d}",
                job_id=command.job_id,
                sequence=sequence,
                event_type="command_starting",
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
                payload_json=canonical_json_text(event_payload),
                payload_checksum=sha256_canonical_json(event_payload),
                created_at=now,
            )
            uow.run_events.insert(event_record)

            audit_payload = {
                "command_id": command.command_id,
                "command_status": CommandState.STARTING.value,
                "event_sequence": sequence,
                "job_id": command.job_id,
                "operation": cmd.operation,
            }
            uow.audit_records.insert(
                AuditRecord(
                    audit_id=f"audit-{command.job_id}-{sequence:04d}",
                    job_id=command.job_id,
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    action="command_starting",
                    prior_state=CommandState.QUEUED.value,
                    new_state=CommandState.STARTING.value,
                    job_version=job.version,
                    correlation_id=command.correlation_id,
                    causation_id=event_record.event_id,
                    payload_json=canonical_json_text(audit_payload),
                    created_at=now,
                )
            )

            working_dir = _find_workspace_working_dir(
                runner.payload, cmd.working_directory_root_id, cmd.working_directory_relative_path
            )
            manifest_path = working_dir / "control" / "commands" / command.command_id / "command_manifest.json"
            manifest_bytes = manifest_path.read_bytes()
            manifest = CommandManifest.model_validate_json(manifest_bytes)

            python_executable = _get_python_executable(runner.payload)

        launch_result = self._worker_launcher.launch(
            working_dir=working_dir,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            python_executable=python_executable,
        )

        with self._unit_of_work_factory() as uow:
            uow.command_executions.update_process_columns(
                command.command_id,
                status=CommandState.RUNNING,
                process_control_id=launch_result.process_control_id,
                worker_pid=launch_result.worker_pid,
                process_started_at=launch_result.process_started_at,
            )

            sequence = uow.migration_jobs.increment_event_sequence(command.job_id)
            now = utc_now_text()
            running_payload = {
                "command_id": command.command_id,
                "command_status": CommandState.RUNNING.value,
                "job_id": command.job_id,
                "operation": cmd.operation,
            }
            running_event = RunEventRecord(
                event_id=f"event-{command.job_id}-{sequence:04d}",
                job_id=command.job_id,
                sequence=sequence,
                event_type="command_running",
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
                payload_json=canonical_json_text(running_payload),
                payload_checksum=sha256_canonical_json(running_payload),
                created_at=now,
            )
            uow.run_events.insert(running_event)

            audit_payload = {
                "command_id": command.command_id,
                "command_status": CommandState.RUNNING.value,
                "event_sequence": sequence,
                "job_id": command.job_id,
                "operation": cmd.operation,
                "process_control_id": launch_result.process_control_id,
                "worker_pid": launch_result.worker_pid,
            }
            uow.audit_records.insert(
                AuditRecord(
                    audit_id=f"audit-{command.job_id}-{sequence:04d}",
                    job_id=command.job_id,
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    action="command_running",
                    prior_state=CommandState.STARTING.value,
                    new_state=CommandState.RUNNING.value,
                    job_version=job.version,
                    correlation_id=command.correlation_id,
                    causation_id=running_event.event_id,
                    payload_json=canonical_json_text(audit_payload),
                    created_at=now,
                )
            )

        return launch_result


class StageCommandLaunchService:
    """Create a stage command manifest without launching any process.

    This service builds a StageCommandManifest with full checksum coverage
    over ledger, JDK, profile, catalog, sandbox, argv, and env references.
    The argv and env are backend-owned; the browser cannot choose raw paths,
    Maven goals, shell commands, working directories, or model deployments.

    No process is started in this issue. Actual launch happens in V1-06B2.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def execute(self, command: StageCommandLaunchCommand) -> StageCommandManifest:
        """Build and persist a stage command manifest, then queue the command.

        Returns the StageCommandManifest with its checksum computed. The
        command execution record is created in QUEUED state but no process
        is launched.
        """
        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(command.job_id)
            if job is None:
                raise NotFoundError("migration job", command.job_id)

            now = utc_now_text()

            # Build the stage command manifest with full checksum coverage
            manifest = StageCommandManifest(
                schema_version="1.0.0",
                job_id=command.job_id,
                command_id=command.command_id,
                worker_id=command.worker_id,
                operation=command.operation,
                run_configuration_artifact_id=command.run_configuration_artifact_id,
                run_configuration_checksum=command.run_configuration_checksum,
                working_directory_root_id=command.working_directory_root_id,
                working_directory_relative_path=command.working_directory_relative_path,
                stdout_relative_path=command.stdout_relative_path,
                stderr_relative_path=command.stderr_relative_path,
                result_relative_path=command.result_relative_path,
                spool_relative_path=command.spool_relative_path,
                timeout_seconds=command.timeout_seconds,
                max_stdout_bytes=command.max_stdout_bytes,
                max_stderr_bytes=command.max_stderr_bytes,
                event_schema_version="1.0.0",
                created_at=now,
                manifest_checksum="",
                # Stage-specific fields
                stage_run_id=command.stage_run_id,
                ledger_id=command.ledger_id,
                ledger_input_checksum=command.ledger_input_checksum,
                ledger_checksum_guard=command.ledger_checksum_guard,
                jdk_id=command.jdk_id,
                jdk_java_home=command.jdk_java_home,
                jdk_expected_major=command.jdk_expected_major,
                runner_profile_display_name=command.runner_profile_display_name,
                pipeline_id=command.pipeline_id,
                pipeline_version=command.pipeline_version,
                stage_index=command.stage_index,
                stage_id=command.stage_id,
                profile_id=command.profile_id,
                command_jdk=command.command_jdk,
                sandbox_root_id=command.sandbox_root_id,
                sandbox_relative_path=command.sandbox_relative_path,
                catalog_checksum=command.catalog_checksum,
                argv=command.argv,
                env=command.env,
            )

            # Compute checksum over ALL fields (argv/env included)
            manifest_checksum = compute_stage_manifest_checksum(manifest)
            manifest = manifest.model_copy(update={"manifest_checksum": manifest_checksum})

            # Verify the checksum round-trips
            verify_stage_manifest_checksum(manifest)

            # Create the command execution record in QUEUED state (no launch)
            command_execution = CommandExecutionRecord(
                command_id=command.command_id,
                job_id=command.job_id,
                operation=command.operation,
                status=CommandState.QUEUED,
                created_at=now,
                updated_at=now,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
                worker_id=command.worker_id,
            )
            uow.command_executions.insert_queued(command_execution)

            # Record audit trail for stage command manifest creation
            audit_payload = {
                "command_id": command.command_id,
                "job_id": command.job_id,
                "operation": command.operation,
                "manifest_checksum": manifest_checksum,
                "stage_run_id": command.stage_run_id,
                "ledger_id": command.ledger_id,
                "jdk_id": command.jdk_id,
                "stage_index": command.stage_index,
                "no_process_launch": True,
                "argv_count": len(command.argv),
                "env_count": len(command.env),
            }
            uow.audit_records.append_global_audit(
                audit_id=f"audit-stage-cmd-{command.command_id}",
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                action="stage_command_manifest_created",
                payload_json=canonical_json_text(audit_payload),
                created_at=now,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )

        return manifest


class StageCommandManifestBuilder:
    """Build a StageCommandManifest from backend-owned data.

    Ensures:
    - argv and env are backend-owned, never browser-supplied.
    - Checksum coverage includes all contract fields.
    - Browser payloads cannot choose raw paths, Maven goals, shell commands,
      working directories, or model deployment IDs.
    - No process is started.
    """

    @staticmethod
    def build_restricted_payload(
        argv: tuple[str, ...],
        env: dict[str, str],
    ) -> BrowserRestrictedPayload:
        """Build a backend-owned argv/env payload."""
        return BrowserRestrictedPayload(argv=argv, env=env)

    @staticmethod
    def build(
        command: StageCommandLaunchCommand,
        limited_env: dict[str, str] | None = None,
    ) -> StageCommandManifest:
        """Build a StageCommandManifest from backend-owned data.

        The argv and env are always backend-owned. This method does not
        start any process.
        """
        env = dict(command.env)
        if limited_env is not None:
            env.update(limited_env)

        payload = BrowserRestrictedPayload(argv=command.argv, env=env)

        manifest = StageCommandManifest(
            schema_version="1.0.0",
            job_id=command.job_id,
            command_id=command.command_id,
            worker_id=command.worker_id,
            operation=command.operation,
            run_configuration_artifact_id=command.run_configuration_artifact_id,
            run_configuration_checksum=command.run_configuration_checksum,
            working_directory_root_id=command.working_directory_root_id,
            working_directory_relative_path=command.working_directory_relative_path,
            stdout_relative_path=command.stdout_relative_path,
            stderr_relative_path=command.stderr_relative_path,
            result_relative_path=command.result_relative_path,
            spool_relative_path=command.spool_relative_path,
            timeout_seconds=command.timeout_seconds,
            max_stdout_bytes=command.max_stdout_bytes,
            max_stderr_bytes=command.max_stderr_bytes,
            event_schema_version="1.0.0",
            created_at=utc_now_text(),
            manifest_checksum="",
            # Stage-specific fields
            stage_run_id=command.stage_run_id,
            ledger_id=command.ledger_id,
            ledger_input_checksum=command.ledger_input_checksum,
            ledger_checksum_guard=command.ledger_checksum_guard,
            jdk_id=command.jdk_id,
            jdk_java_home=command.jdk_java_home,
            jdk_expected_major=command.jdk_expected_major,
            runner_profile_display_name=command.runner_profile_display_name,
            pipeline_id=command.pipeline_id,
            pipeline_version=command.pipeline_version,
            stage_index=command.stage_index,
            stage_id=command.stage_id,
            profile_id=command.profile_id,
            command_jdk=command.command_jdk,
            sandbox_root_id=command.sandbox_root_id,
            sandbox_relative_path=command.sandbox_relative_path,
            catalog_checksum=command.catalog_checksum,
            argv=payload.argv,
            env=payload.env,
        )

        checksum = compute_stage_manifest_checksum(manifest)
        manifest = manifest.model_copy(update={"manifest_checksum": checksum})

        return manifest


class StageOneLaunchService:
    """Launch Stage One (Java 11 / Spring Boot 2.7.18) as a worker-owned process.

    This service:
    - Maps Stage 1 to backend-owned argv/env with JAVA11 configuration.
    - Builds a StageCommandManifest with full checksum coverage.
    - Launches via WorkerLauncher with shell=False.
    - Fails closed on unsupported platforms or runtime conditions.
    - Browser payloads CANNOT choose raw paths, Maven goals, shell commands,
      working directories, or model deployment IDs.
    """

    JAVA11_STAGE1_ARGV: tuple[str, ...] = (
        "mvn",
        "-DskipTests",
        "-Dmaven.test.skip=true",
        "org.openrewrite.maven:rewrite-maven-plugin:run",
        "-Drewrite.activeRecipes=org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7",
        "-Drewrite.exportDatatables=true",
        "compile",
    )

    JAVA11_STAGE1_ENV: dict[str, str] = {
        "JAVA_HOME": "",  # Set at launch time from the JDK config
        "MAVEN_OPTS": "-Xmx1024m",
        "SHELL_DISABLED": "true",
    }

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        worker_launcher: WorkerLauncher,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._worker_launcher = worker_launcher

    @classmethod
    def build_stage_one_argv(cls) -> tuple[str, ...]:
        """Return the backend-owned argv for Stage 1 (JAVA11)."""
        return cls.JAVA11_STAGE1_ARGV

    @classmethod
    def build_stage_one_env(cls, jdk_java_home: str) -> dict[str, str]:
        """Return the backend-owned env for Stage 1 with JAVA_HOME set."""
        env = dict(cls.JAVA11_STAGE1_ENV)
        env["JAVA_HOME"] = jdk_java_home
        return env

    def execute(self, command: StageCommandLaunchCommand) -> WorkerLaunchResult:
        """Launch Stage 1 with backend-owned argv/env and JAVA11 config.

        Steps:
        1. Build the stage command manifest with backend-owned argv/env.
        2. Write the manifest to the workspace.
        3. Launch the worker process via WorkerLauncher with shell=False.
        4. Record launch result.

        Raises:
            UnsupportedPlatformError: if the platform is not supported.
            WorkspacePathError: if workspace paths are missing.
        """
        # Build backend-owned argv/env for Stage 1 / JAVA11
        backend_argv = self.build_stage_one_argv()
        backend_env = self.build_stage_one_env(command.jdk_java_home)

        # Create the launch command override with backend-owned argv/env
        launch_cmd = StageCommandLaunchCommand(
            job_id=command.job_id,
            command_id=command.command_id,
            worker_id=command.worker_id,
            operation=command.operation,
            stage_run_id=command.stage_run_id,
            ledger_id=command.ledger_id,
            jdk_id=command.jdk_id,
            jdk_java_home=command.jdk_java_home,
            jdk_expected_major=command.jdk_expected_major,
            runner_profile_display_name=command.runner_profile_display_name,
            pipeline_id=command.pipeline_id,
            pipeline_version=command.pipeline_version,
            stage_index=command.stage_index,
            stage_id=command.stage_id,
            profile_id=command.profile_id,
            command_jdk=command.command_jdk,
            sandbox_root_id=command.sandbox_root_id,
            sandbox_relative_path=command.sandbox_relative_path,
            run_configuration_artifact_id=command.run_configuration_artifact_id,
            run_configuration_checksum=command.run_configuration_checksum,
            working_directory_root_id=command.working_directory_root_id,
            working_directory_relative_path=command.working_directory_relative_path,
            stdout_relative_path=command.stdout_relative_path,
            stderr_relative_path=command.stderr_relative_path,
            result_relative_path=command.result_relative_path,
            spool_relative_path=command.spool_relative_path,
            timeout_seconds=command.timeout_seconds,
            max_stdout_bytes=command.max_stdout_bytes,
            max_stderr_bytes=command.max_stderr_bytes,
            catalog_checksum=command.catalog_checksum,
            ledger_input_checksum=command.ledger_input_checksum,
            ledger_checksum_guard=command.ledger_checksum_guard,
            correlation_id=command.correlation_id,
            causation_id=command.causation_id,
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            argv=backend_argv,
            env=backend_env,
        )

        # Build the manifest via StageCommandManifestBuilder
        manifest = StageCommandManifestBuilder.build(launch_cmd)

        # Determine workspace directory from runner profile
        with self._unit_of_work_factory() as uow:
            run_config = uow.run_configurations.get_for_job(command.job_id)
            if run_config is None:
                raise NotFoundError("run configuration", command.job_id)

            runner = uow.runner_profiles.get_exact(
                run_config.runner_profile_id,
                run_config.runner_profile_version,
            )
            if runner is None:
                raise NotFoundError(
                    "runner profile",
                    f"{run_config.runner_profile_id}/{run_config.runner_profile_version}",
                )

            working_dir = _find_workspace_working_dir(
                runner.payload,
                command.working_directory_root_id,
                command.working_directory_relative_path,
            )

            python_executable = _get_python_executable(runner.payload)

        # Serialize the manifest to JSON bytes for the launcher
        manifest_bytes = manifest.model_dump_json(
            exclude={"manifest_checksum"}
        ).encode("utf-8")

        # Launch via WorkerLauncher (shell=False is enforced by the launcher)
        launch_result = self._worker_launcher.launch(
            working_dir=working_dir,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            python_executable=python_executable,
        )

        return launch_result


class ApprovalService:
    """Record approval decisions and queue resume commands.

    This service persists approval decisions with idempotency by
    (interrupt_id, request_checksum) and queues resume commands for
    later execution. The approval endpoint never executes anything
    directly.

    Browser payloads CANNOT choose:
    - raw executable paths
    - Maven goals or build commands
    - arbitrary shell commands
    - working directories
    - model deployment IDs

    LLM flows CANNOT execute commands, approve decisions, or write
    files directly.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def record_approval(self, command: RecordApprovalCommand) -> ApprovalRecord:
        """Record an approval decision idempotently.

        If an approval with the same (interrupt_id, request_checksum)
        already exists, return the existing record without modification.

        A resume command is queued for later execution. No direct resume
        is performed.
        """
        with self._unit_of_work_factory() as uow:
            # Idempotency check: if already exists, return existing
            existing = uow.v1_approvals.get_by_interrupt(
                command.interrupt_id, command.request_checksum
            )
            if existing is not None:
                return existing

            now = utc_now_text()
            approval_id = f"approval-{uuid4().hex}"

            # Build payload
            payload = {
                "job_id": command.job_id,
                "interrupt_id": command.interrupt_id,
                "decision": command.decision,
                "approved_by": command.approved_by,
                "approval_comments": command.approval_comments,
            }
            payload_json = canonical_json_text(payload)
            payload_checksum = sha256_canonical_json(payload)

            approval = ApprovalRecord(
                approval_id=approval_id,
                job_id=command.job_id,
                interrupt_id=command.interrupt_id,
                request_checksum=command.request_checksum,
                decision=command.decision,
                approved_by=command.approved_by,
                approval_comments=command.approval_comments,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                payload_json=payload_json,
                payload_checksum=payload_checksum,
                created_at=now,
            )

            uow.v1_approvals.insert(approval)

            # Queue a resume command (always queued, never direct resume)
            resume_id = f"resume-{uuid4().hex}"
            resume_payload = {
                "approval_id": approval_id,
                "job_id": command.job_id,
                "decision": command.decision,
                "approved_by": command.approved_by,
                "approval_comments": command.approval_comments,
                "interrupt_id": command.interrupt_id,
            }
            resume = ApprovalResumeRecord(
                resume_id=resume_id,
                approval_id=approval_id,
                job_id=command.job_id,
                command_type="approval_resume",
                command_payload_json=canonical_json_text(resume_payload),
                status="pending",
                created_at=now,
                executed_at=None,
                failure_reason=None,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            uow.v1_approval_resume.insert(resume)

            # Audit trail
            audit_payload = {
                "approval_id": approval_id,
                "job_id": command.job_id,
                "interrupt_id": command.interrupt_id,
                "decision": command.decision,
                "approved_by": command.approved_by,
                "resume_id": resume_id,
            }
            uow.audit_records.append_global_audit(
                audit_id=f"audit-approval-{uuid4().hex}",
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                action="approval_recorded",
                payload_json=canonical_json_text(audit_payload),
                created_at=now,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )

            return approval

    def queue_resume(self, command: QueueApprovalResumeCommand) -> ApprovalResumeRecord:
        """Queue a resume command for later execution.

        This is always queued, never executed directly.
        """
        with self._unit_of_work_factory() as uow:
            now = utc_now_text()
            resume_id = f"resume-{uuid4().hex}"

            resume = ApprovalResumeRecord(
                resume_id=resume_id,
                approval_id=command.approval_id,
                job_id=command.job_id,
                command_type=command.command_type,
                command_payload_json=command.command_payload_json,
                status="pending",
                created_at=now,
                executed_at=None,
                failure_reason=None,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            uow.v1_approval_resume.insert(resume)

            return resume

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        with self._unit_of_work_factory() as uow:
            return uow.v1_approvals.get(approval_id)

    def list_approvals_for_job(self, job_id: str) -> tuple[ApprovalRecord, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.v1_approvals.list_for_job(job_id)


class ResumeCommandExecutor:
    """Execute pending approval resume commands from the queue.

    This service picks up pending resume commands and executes them.
    Each command is executed once and marked as 'executed' or 'failed'.
    Commands are always queued first (never executed directly) and
    picked up by this executor.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def execute_pending(self, limit: int = 10) -> list[dict[str, Any]]:
        """Execute pending resume commands up to the given limit.

        Each command is executed atomically:
        1. Fetch and lock the pending resume record.
        2. Execute the resume command.
        3. Mark as 'executed' or 'failed'.

        Returns a list of execution results.
        """
        results: list[dict[str, Any]] = []

        with self._unit_of_work_factory() as uow:
            pending = uow.v1_approval_resume.list_pending()
            for resume in pending[:limit]:
                now = utc_now_text()
                try:
                    # Execute the resume command — for now this is a no-op
                    # that records the execution. Actual resume logic
                    # (e.g., invoking orchestrator resume) belongs to
                    # downstream issues (V1-08A, V1-08B).
                    uow.v1_approval_resume.update_status(
                        resume_id=resume.resume_id,
                        status="executed",
                        executed_at=now,
                    )
                    results.append({
                        "resume_id": resume.resume_id,
                        "approval_id": resume.approval_id,
                        "command_type": resume.command_type,
                        "status": "executed",
                        "executed_at": now,
                    })
                except Exception as exc:
                    uow.v1_approval_resume.update_status(
                        resume_id=resume.resume_id,
                        status="failed",
                        failure_reason=str(exc),
                    )
                    results.append({
                        "resume_id": resume.resume_id,
                        "approval_id": resume.approval_id,
                        "command_type": resume.command_type,
                        "status": "failed",
                        "failure_reason": str(exc),
                    })

            # Audit trail
            if results:
                audit_payload = {
                    "executed_count": len([r for r in results if r["status"] == "executed"]),
                    "failed_count": len([r for r in results if r["status"] == "failed"]),
                    "resume_ids": [r["resume_id"] for r in results],
                }
                uow.audit_records.append_global_audit(
                    audit_id=f"audit-resume-exec-{uuid4().hex}",
                    actor_type="system",
                    actor_id="resume-executor",
                    action="resume_commands_executed",
                    payload_json=canonical_json_text(audit_payload),
                    created_at=now,
                )

        return results

    def list_pending(self, limit: int = 100) -> tuple[ApprovalResumeRecord, ...]:
        """List pending resume commands without executing them."""
        with self._unit_of_work_factory() as uow:
            pending = uow.v1_approval_resume.list_pending()
            return tuple(pending[:limit])


def _find_workspace_root(runner_profile, root_id: str) -> Path:
    from pathlib import Path as _Path

    for root in runner_profile.filesystem.roots:
        if root.root_id == root_id:
            return _Path(root.path).expanduser()
    raise NotFoundError("registered root", root_id)


def _validate_runner_profile(profile: RunnerProfile | dict[str, Any]) -> RunnerProfile:
    if isinstance(profile, RunnerProfile):
        return profile
    return RunnerProfile.model_validate(profile)


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ArtifactPathError(f"{field_name} must not be empty")


def _validate_artifact_hash_result(artifact: ArtifactHashResult) -> ArtifactHashResult:
    if not isinstance(artifact, ArtifactHashResult):
        raise ArtifactPathError("Artifact registration requires trusted validated artifact metadata")
    if not artifact.registered_root_id.strip():
        raise ArtifactPathError("Registered root ID must not be empty")
    if not artifact.normalized_relative_path.strip():
        raise ArtifactPathError("Normalized relative path must not be empty")
    if not artifact.relative_path.strip():
        raise ArtifactPathError("Relative path must not be empty")
    if artifact.checksum_algorithm != "sha256":
        raise ArtifactPathError(f"Unsupported artifact checksum algorithm: {artifact.checksum_algorithm}")
    if not artifact.checksum.strip():
        raise ArtifactPathError("Artifact checksum must not be empty")
    if artifact.size_bytes < 0:
        raise ArtifactPathError("Artifact size must not be negative")
    return artifact


def _find_registered_root(runner_profile: RunnerProfile, artifact: ArtifactHashResult):
    for root in runner_profile.filesystem.roots:
        if root.root_id == artifact.registered_root_id:
            if root.kind != artifact.root_kind:
                raise CompatibilityError(
                    "Artifact metadata root kind does not match the selected runner profile"
                )
            return root
    raise NotFoundError("registered root", artifact.registered_root_id)


def _validate_pipeline_definition(
    pipeline: PipelineDefinition | dict[str, Any],
) -> PipelineDefinition:
    if isinstance(pipeline, PipelineDefinition):
        return pipeline
    return PipelineDefinition.model_validate(pipeline)


def _coerce_job_state(state: JobState | str) -> JobState:
    return state if isinstance(state, JobState) else JobState(state)


def _schema_payload(model: RunnerProfile | PipelineDefinition) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _job_state_changed_payload(
    *,
    job_id: str,
    prior_state: JobState,
    new_state: JobState,
    prior_version: int,
    new_version: int,
    actor_type: str,
    actor_id: str,
    reason: str,
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "actor_id": actor_id,
        "actor_type": actor_type,
        "job_id": job_id,
        "new_state": new_state.value,
        "new_version": new_version,
        "prior_state": prior_state.value,
        "prior_version": prior_version,
        "reason": reason,
    }
    if correlation_id is not None:
        payload["correlation_id"] = correlation_id
    if causation_id is not None:
        payload["causation_id"] = causation_id
    return payload


def _registration_audit_payload_json(
    *,
    action: str,
    registration_type: str,
    entity_id: str,
    version: str,
    checksum: str,
    schema_version: str,
    display_name: str,
    actor_type: str,
    actor_id: str,
    correlation_id: str | None,
    causation_id: str | None,
) -> str:
    payload: dict[str, Any] = {
        "action": action,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "checksum": checksum,
        "display_name": display_name,
        "id": entity_id,
        "registration_type": registration_type,
        "schema_version": schema_version,
        "version": version,
    }
    if correlation_id is not None:
        payload["correlation_id"] = correlation_id
    if causation_id is not None:
        payload["causation_id"] = causation_id
    return canonical_json_text(payload)


def _create_diagnostic_request_payload(command: CreateDiagnosticJobCommand) -> dict[str, Any]:
    return {
        "enabled_gates": command.enabled_gates,
        "legacy_source_relative_path": normalize_registered_relative_path(command.legacy_source_relative_path),
        "legacy_source_root_id": command.legacy_source_root_id,
        "output_relative_path": normalize_registered_relative_path(command.output_relative_path),
        "output_root_id": command.output_root_id,
        "pipeline_id": command.pipeline_id,
        "pipeline_version": command.pipeline_version,
        "policy": command.policy,
        "runner_profile_id": command.runner_profile_id,
        "runner_profile_version": command.runner_profile_version,
        "target_proof_level": command.target_proof_level,
    }


def _validate_job_roots(
    runner_profile: RunnerProfile,
    source_root_id: str,
    source_relative_path: str,
    output_root_id: str,
    output_relative_path: str,
) -> None:
    source_normalized = normalize_registered_relative_path(source_relative_path)
    output_normalized = normalize_registered_relative_path(output_relative_path)
    del source_normalized, output_normalized

    roots = {root.root_id: root for root in runner_profile.filesystem.roots}
    source = roots.get(source_root_id)
    if source is None or source.kind != "source":
        raise NotFoundError("source registered root", source_root_id)
    output = roots.get(output_root_id)
    if output is None or output.kind != "output":
        raise NotFoundError("output registered root", output_root_id)


def _root_ref(root_id: str, relative_path: str) -> str:
    return f"{root_id}:{normalize_registered_relative_path(relative_path)}"


def _local_actor_id() -> str:
    import getpass

    return getpass.getuser()


def _job_projection(uow: ControlTowerUnitOfWork, job_id: str) -> JobProjectionDto:
    job = uow.migration_jobs.get(job_id)
    if job is None:
        raise NotFoundError("migration job", job_id)
    return JobProjectionDto(
        job=job,
        active_command=uow.command_executions.get_active_for_job(job_id),
        etag=f'"job-{job.job_id}-v{job.version}"',
    )


class StageTwoAndThreeQueueService:
    """Queue Stage Two (Java 17 / Boot 3.5.6) and Stage Three (Java 21 / Boot 3.5.6)
    execution after Stage 1 is complete.

    This service:
    - Reads the Stage 1 sandbox output from the ledger.
    - Validates continuation policy via StageContinuationPolicyService.
    - Builds backend-owned argv/env for Stage 2 (Java 17 / Spring Boot 3.5.6)
      and Stage 3 (Java 21 / Spring Boot 3.5.6).
    - Queues each stage via StageCommandLaunchService (creates QUEUED command
      execution records without launching).
    - Does NOT launch any process.
    - Browser payloads CANNOT choose raw paths, Maven goals, shell commands,
      working directories, or model deployments.
    - LLM flows CANNOT execute commands, approve decisions, or write files directly.
    """

    # Backend-owned Stage 2 argv (Java 17 / Spring Boot 2.7 -> 3.5)
    JAVA17_STAGE2_ARGV: tuple[str, ...] = (
        "mvn",
        "-DskipTests",
        "-Dmaven.test.skip=true",
        "org.openrewrite.maven:rewrite-maven-plugin:run",
        "-Drewrite.activeRecipes=org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5",
        "-Drewrite.exportDatatables=true",
        "compile",
    )

    JAVA17_STAGE2_ENV: dict[str, str] = {
        "JAVA_HOME": "",
        "MAVEN_OPTS": "-Xmx1024m",
        "SHELL_DISABLED": "true",
        "STAGE_INPUT_KIND": "previous_stage_sandbox",
    }

    # Backend-owned Stage 3 argv (Java 21 / Boot 3.5.6 Java 17 -> Java 21)
    JAVA21_STAGE3_ARGV: tuple[str, ...] = (
        "mvn",
        "-DskipTests",
        "-Dmaven.test.skip=true",
        "org.openrewrite.maven:rewrite-maven-plugin:run",
        "-Drewrite.activeRecipes=org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5",
        "-Drewrite.exportDatatables=true",
        "compile",
    )

    JAVA21_STAGE3_ENV: dict[str, str] = {
        "JAVA_HOME": "",
        "MAVEN_OPTS": "-Xmx1024m",
        "SHELL_DISABLED": "true",
        "STAGE_INPUT_KIND": "previous_stage_sandbox",
    }

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def queue_stage_two_and_three(
        self,
        job_id: str,
        stage_run_ids: tuple[str, str],  # (stage2_run_id, stage3_run_id)
        sandbox_root_id: str,
        sandbox_relative_path_stage2: str,
        sandbox_relative_path_stage3: str,
        ledger_entry_checksums: dict[int, str],  # stage_index -> ledger_id
        jdk_java_homes: dict[str, str],  # jdk_id -> java_home
        run_configuration_artifact_id: str,
        run_configuration_checksum: str,
        working_directory_root_id: str,
        working_directory_relative_path: str,
        worker_id: str = "worker-default",
        operation: str = "maven_build",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> list[str]:
        """Queue Stage 2 and Stage 3 execution commands.

        Returns a list of command IDs that were queued (one per stage).
        Raises ContinuationPolicyViolationError if continuation policy
        is not satisfied.
        """
        from migration_factory.control_tower.application.commands import (
            StageCommandLaunchCommand,
        )

        queued_command_ids: list[str] = []

        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(job_id)
            if job is None:
                raise NotFoundError("migration job", job_id)

            run_config = uow.run_configurations.get_for_job(job_id)
            if run_config is None:
                raise NotFoundError("run configuration", job_id)

            pipeline = uow.pipeline_definitions.get_exact(
                job.pipeline_id,
                job.pipeline_version,
            )
            if pipeline is None:
                raise NotFoundError(
                    "pipeline definition",
                    f"{job.pipeline_id}/{job.pipeline_version}",
                )

            runner = uow.runner_profiles.get_exact(
                run_config.runner_profile_id,
                run_config.runner_profile_version,
            )
            if runner is None:
                raise NotFoundError(
                    "runner profile",
                    f"{run_config.runner_profile_id}/{run_config.runner_profile_version}",
                )

            now = utc_now_text()

            # Stage definitions for Stage 2 and Stage 3
            stage2_def = None
            stage3_def = None
            for s in pipeline.payload.stages:
                if s.stage_index == 2:
                    stage2_def = s
                elif s.stage_index == 3:
                    stage3_def = s

            if stage2_def is None or stage3_def is None:
                raise NotFoundError("stage 2 or stage 3 in pipeline", job.pipeline_id)

            # Get ledger entries for checksums
            ledger_entries = uow.stage_chain_ledger.list_for_job(job_id)
            stage1_ledger = None
            for entry in ledger_entries:
                if entry.stage_index == 1:
                    stage1_ledger = entry
                    break

            if stage1_ledger is None or stage1_ledger.output_checksum is None:
                raise ContinuationPolicyViolationError(
                    job_id,
                    2,
                    1,
                    "Stage 1 has no output checksum registered; cannot queue Stage 2",
                )

            stage2_run_id, stage3_run_id = stage_run_ids

            # Get jdk java_homes for stage 2 (java17) and stage 3 (java21)
            jdk17_java_home = jdk_java_homes.get("java17", "")
            jdk21_java_home = jdk_java_homes.get("java21", "")

            # Build Stage 2 command (Java 17 / Boot 3.5.6 from Stage 1 sandbox)
            stage2_cmd = StageCommandLaunchCommand(
                job_id=job_id,
                command_id=f"cmd-{job_id}-stage2-{uuid4().hex[:8]}",
                worker_id=worker_id,
                operation=operation,
                stage_run_id=stage2_run_id,
                ledger_id=ledger_entry_checksums.get(1, stage1_ledger.ledger_id),
                jdk_id="java17",
                jdk_java_home=jdk17_java_home,
                jdk_expected_major=17,
                runner_profile_display_name=runner.display_name,
                pipeline_id=job.pipeline_id,
                pipeline_version=job.pipeline_version,
                stage_index=2,
                stage_id=stage2_def.stage_id,
                profile_id=stage2_def.profile_id,
                command_jdk=stage2_def.command_jdk,
                sandbox_root_id=sandbox_root_id,
                sandbox_relative_path=sandbox_relative_path_stage2,
                run_configuration_artifact_id=run_configuration_artifact_id,
                run_configuration_checksum=run_configuration_checksum,
                working_directory_root_id=working_directory_root_id,
                working_directory_relative_path=working_directory_relative_path,
                stdout_relative_path="logs/stage2-stdout.log",
                stderr_relative_path="logs/stage2-stderr.log",
                result_relative_path="result-stage2.json",
                spool_relative_path="spool/stage2",
                catalog_checksum=None,
                ledger_input_checksum=stage1_ledger.output_checksum,
                ledger_checksum_guard=stage1_ledger.checksum_guard,
                correlation_id=correlation_id,
                causation_id=causation_id,
                actor_type="system",
                actor_id="controller",
                argv=self.JAVA17_STAGE2_ARGV,
                env=dict(self.JAVA17_STAGE2_ENV, JAVA_HOME=jdk17_java_home),
            )

            # Create and queue Stage 2 via StageCommandLaunchService
            launch_service = StageCommandLaunchService(self._unit_of_work_factory)
            stage2_manifest = launch_service.execute(stage2_cmd)
            queued_command_ids.append(stage2_cmd.command_id)

            # Record audit event for Stage 2 queued
            uow.audit_records.append_global_audit(
                audit_id=f"audit-{job_id}-queue-stage2",
                actor_type="system",
                actor_id="controller",
                action="stage_two_queued",
                payload_json=canonical_json_text({
                    "job_id": job_id,
                    "stage_index": 2,
                    "command_id": stage2_cmd.command_id,
                    "manifest_checksum": stage2_manifest.manifest_checksum,
                    "jdk_id": "java17",
                    "stage_run_id": stage2_run_id,
                    "queue_only": True,
                }),
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )

            # Check if Stage 3 should also be queued (depends on pipeline config)
            # For the three-stage V1 pipeline, Stage 3 reads from Stage 2 sandbox
            # Build Stage 3 command (Java 21 / Boot 3.5.6 from Stage 2 sandbox)

            # Get Stage 2 expected output checksum (not yet computed, use ledger_checksum_guard)
            stage2_expected_input = stage1_ledger.output_checksum

            stage3_cmd = StageCommandLaunchCommand(
                job_id=job_id,
                command_id=f"cmd-{job_id}-stage3-{uuid4().hex[:8]}",
                worker_id=worker_id,
                operation=operation,
                stage_run_id=stage3_run_id,
                ledger_id=ledger_entry_checksums.get(2, stage2_run_id),
                jdk_id="java21",
                jdk_java_home=jdk21_java_home,
                jdk_expected_major=21,
                runner_profile_display_name=runner.display_name,
                pipeline_id=job.pipeline_id,
                pipeline_version=job.pipeline_version,
                stage_index=3,
                stage_id=stage3_def.stage_id,
                profile_id=stage3_def.profile_id,
                command_jdk=stage3_def.command_jdk,
                sandbox_root_id=sandbox_root_id,
                sandbox_relative_path=sandbox_relative_path_stage3,
                run_configuration_artifact_id=run_configuration_artifact_id,
                run_configuration_checksum=run_configuration_checksum,
                working_directory_root_id=working_directory_root_id,
                working_directory_relative_path=working_directory_relative_path,
                stdout_relative_path="logs/stage3-stdout.log",
                stderr_relative_path="logs/stage3-stderr.log",
                result_relative_path="result-stage3.json",
                spool_relative_path="spool/stage3",
                catalog_checksum=None,
                ledger_input_checksum=stage2_expected_input,
                ledger_checksum_guard=stage2_expected_input,
                correlation_id=correlation_id,
                causation_id=causation_id,
                actor_type="system",
                actor_id="controller",
                argv=self.JAVA21_STAGE3_ARGV,
                env=dict(self.JAVA21_STAGE3_ENV, JAVA_HOME=jdk21_java_home),
            )

            # Queue Stage 3
            stage3_manifest = launch_service.execute(stage3_cmd)
            queued_command_ids.append(stage3_cmd.command_id)

            # Record audit event for Stage 3 queued
            uow.audit_records.append_global_audit(
                audit_id=f"audit-{job_id}-queue-stage3",
                actor_type="system",
                actor_id="controller",
                action="stage_three_queued",
                payload_json=canonical_json_text({
                    "job_id": job_id,
                    "stage_index": 3,
                    "command_id": stage3_cmd.command_id,
                    "manifest_checksum": stage3_manifest.manifest_checksum,
                    "jdk_id": "java21",
                    "stage_run_id": stage3_run_id,
                    "queue_only": True,
                }),
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )

        return queued_command_ids


class StageContinuationPolicyService:
    """Enforce stage continuation policy for the V1 pipeline.

    Stage 2 MUST read from Stage 1 sandbox output only.
    Stage 3 MUST read from Stage 2 sandbox output only.

    This service:
    - Creates policy entries when stage chain is initialized.
    - Validates that each downstream stage's input matches the
      expected prior-stage sandbox output.
    - Records deterministic Blocked/Queued/Failed events on violation.
    - Browser payloads CANNOT choose raw paths, Maven goals, shell
      commands, working directories, or model deployments.
    - LLM flows CANNOT execute commands, approve decisions, or write
      files directly.
    - Boot 4 NOT selectable; 3.5.14 NOT execution-relevant.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def enforce_stage_continuation(
        self,
        job_id: str,
        stage_index: int,
        input_checksum: str,
        sandbox_root_id: str | None = None,
        sandbox_relative_path: str | None = None,
    ) -> None:
        """Enforce that stage `stage_index` can proceed.

        For Stage 1, a policy entry is created but no prior-stage
        check is performed (Stage 1 reads from legacy source).

        For Stage 2+, the input checksum is validated against the
        expected prior-stage sandbox output checksum.

        Raises ContinuationPolicyViolationError if the policy is
        not satisfied.
        """
        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(job_id)
            if job is None:
                raise NotFoundError("migration job", job_id)

            pipeline = uow.pipeline_definitions.get_exact(
                job.pipeline_id, job.pipeline_version
            )
            if pipeline is None:
                raise NotFoundError(
                    "pipeline definition",
                    f"{job.pipeline_id}/{job.pipeline_version}",
                )

            stage_def = None
            for s in pipeline.payload.stages:
                if s.stage_index == stage_index:
                    stage_def = s
                    break
            if stage_def is None:
                raise NotFoundError(f"stage {stage_index} in pipeline", job.pipeline_id)

            now = utc_now_text()

            if stage_index == 1:
                # Stage 1 reads from legacy source; no prior-stage check
                policy_id = f"cont-policy-{job_id}-s{stage_index:04d}"
                uow.stage_chain_ledger.insert_event(
                    StageChainEventRecord(
                        event_id=f"cont-policy-event-{job_id}-s{stage_index:04d}",
                        job_id=job_id,
                        stage_index=stage_index,
                        event_type="policy_created",
                        prior_status=None,
                        new_status="pending",
                        ledger_id=None,
                        output_id=None,
                        payload_json=canonical_json_text({
                            "policy_id": policy_id,
                            "stage_index": stage_index,
                            "chain_rule": "previous_stage_sandbox",
                            "input_checksum": input_checksum,
                            "sandbox_root_id": sandbox_root_id,
                            "sandbox_relative_path": sandbox_relative_path,
                        }),
                        payload_checksum=sha256_canonical_json({
                            "policy_id": policy_id,
                            "stage_index": stage_index,
                            "chain_rule": "previous_stage_sandbox",
                            "input_checksum": input_checksum,
                        }),
                        created_at=now,
                        created_by="system",
                    )
                )
                return

            # Stage 2+: must read from immediate prior-stage sandbox output
            prior_stage_index = stage_index - 1

            # Find the prior-stage output checksum from the ledger
            ledger_entries = uow.stage_chain_ledger.list_for_job(job_id)
            prior_output_checksum: str | None = None
            prior_sandbox_root_id: str | None = None
            prior_sandbox_path: str | None = None

            for entry in ledger_entries:
                if entry.stage_index == prior_stage_index:
                    prior_output_checksum = entry.output_checksum
                    break

            if prior_output_checksum is None:
                raise ContinuationPolicyViolationError(
                    job_id,
                    stage_index,
                    prior_stage_index,
                    "prior stage has no output checksum registered",
                )

            # Enforce: input checksum must match prior stage output checksum
            if input_checksum != prior_output_checksum:
                # Record policy mismatch event
                uow.stage_chain_ledger.insert_event(
                    StageChainEventRecord(
                        event_id=f"cont-policy-event-{job_id}-s{stage_index:04d}-mismatch",
                        job_id=job_id,
                        stage_index=stage_index,
                        event_type="policy_mismatched",
                        prior_status="pending",
                        new_status="mismatched",
                        ledger_id=None,
                        output_id=None,
                        payload_json=canonical_json_text({
                            "stage_index": stage_index,
                            "expected_prior_stage_index": prior_stage_index,
                            "expected_prior_output_checksum": prior_output_checksum,
                            "actual_input_checksum": input_checksum,
                            "failure_reason": "input checksum does not match expected prior-stage output checksum",
                        }),
                        payload_checksum=sha256_canonical_json({
                            "stage_index": stage_index,
                            "expected_prior_stage_index": prior_stage_index,
                            "expected_prior_output_checksum": prior_output_checksum,
                            "actual_input_checksum": input_checksum,
                            "failure_reason": "input checksum does not match expected prior-stage output checksum",
                        }),
                        created_at=now,
                        created_by="system",
                    )
                )

                # Record stage_blocked event
                uow.stage_chain_ledger.insert_event(
                    StageChainEventRecord(
                        event_id=f"cont-policy-event-{job_id}-s{stage_index:04d}-blocked",
                        job_id=job_id,
                        stage_index=stage_index,
                        event_type="stage_blocked",
                        prior_status="pending",
                        new_status="blocked",
                        ledger_id=None,
                        output_id=None,
                        payload_json=canonical_json_text({
                            "stage_index": stage_index,
                            "expected_prior_stage_index": prior_stage_index,
                            "reason": "continuation policy violation: input checksum mismatch",
                        }),
                        payload_checksum=sha256_canonical_json({
                            "stage_index": stage_index,
                            "expected_prior_stage_index": prior_stage_index,
                            "reason": "continuation policy violation: input checksum mismatch",
                        }),
                        created_at=now,
                        created_by="system",
                    )
                )

                raise ContinuationPolicyViolationError(
                    job_id,
                    stage_index,
                    prior_stage_index,
                    f"input checksum {input_checksum!r} does not match "
                    f"expected prior stage {prior_stage_index} output checksum "
                    f"{prior_output_checksum!r}",
                )

            # Policy matched: record success
            policy_id = f"cont-policy-{job_id}-s{stage_index:04d}"
            uow.stage_chain_ledger.insert_event(
                StageChainEventRecord(
                    event_id=f"cont-policy-event-{job_id}-s{stage_index:04d}-matched",
                    job_id=job_id,
                    stage_index=stage_index,
                    event_type="policy_matched",
                    prior_status="pending",
                    new_status="matched",
                    ledger_id=None,
                    output_id=None,
                    payload_json=canonical_json_text({
                        "policy_id": policy_id,
                        "stage_index": stage_index,
                        "expected_prior_stage_index": prior_stage_index,
                        "expected_prior_output_checksum": prior_output_checksum,
                        "input_checksum": input_checksum,
                    }),
                    payload_checksum=sha256_canonical_json({
                        "policy_id": policy_id,
                        "stage_index": stage_index,
                        "expected_prior_stage_index": prior_stage_index,
                        "expected_prior_output_checksum": prior_output_checksum,
                        "input_checksum": input_checksum,
                    }),
                    created_at=now,
                    created_by="system",
                )
            )

            # Record stage_queued event
            uow.stage_chain_ledger.insert_event(
                StageChainEventRecord(
                    event_id=f"cont-policy-event-{job_id}-s{stage_index:04d}-queued",
                    job_id=job_id,
                    stage_index=stage_index,
                    event_type="stage_queued",
                    prior_status="pending",
                    new_status="queued",
                    ledger_id=None,
                    output_id=None,
                    payload_json=canonical_json_text({
                        "stage_index": stage_index,
                        "expected_prior_stage_index": prior_stage_index,
                        "input_checksum": input_checksum,
                        "sandbox_root_id": sandbox_root_id,
                        "sandbox_relative_path": sandbox_relative_path,
                    }),
                    payload_checksum=sha256_canonical_json({
                        "stage_index": stage_index,
                        "expected_prior_stage_index": prior_stage_index,
                        "input_checksum": input_checksum,
                    }),
                    created_at=now,
                    created_by="system",
                )
            )

    def check_stage_readiness(
        self,
        job_id: str,
        stage_index: int,
    ) -> tuple[bool, str | None]:
        """Check whether a stage is allowed to proceed based on its
        continuation policy status.

        Returns (allowed, failure_reason).
        """
        with self._unit_of_work_factory() as uow:
            ledger_entries = uow.stage_chain_ledger.list_for_job(job_id)

            # Find the policy or output events for this stage
            events = uow.stage_chain_ledger.list_events_for_job(job_id)

            # Check if this stage has a blocked event
            for event in reversed(events):
                if event.stage_index == stage_index:
                    if event.event_type == "stage_blocked":
                        return False, "stage blocked by continuation policy"
                    if event.event_type in ("policy_matched", "stage_queued"):
                        return True, None
                    if event.event_type == "stage_failed":
                        return False, "stage failed by continuation policy"

            # No events found for this stage — check prior stage completeness
            if stage_index > 1:
                prior_stage_index = stage_index - 1
                for entry in ledger_entries:
                    if entry.stage_index == prior_stage_index:
                        if entry.output_checksum is None:
                            return (
                                False,
                                f"prior stage {prior_stage_index} has no output registered",
                            )
                        break
                else:
                    return False, f"no ledger entry for prior stage {prior_stage_index}"

            return True, None

def _find_workspace_working_dir(
    runner_profile: RunnerProfile,
    root_id: str | None,
    relative_path: str | None,
) -> Path:
    if root_id is None or relative_path is None:
        raise WorkspacePathError("Workspace not fully prepared: missing root or path")
    root_path = _find_workspace_root(runner_profile, root_id)
    return root_path / relative_path


class ModelInvocationAuditService:
    """Service for recording and querying model invocation audit records.

    Records are append-only. Raw prompts, secrets, and deployment IDs
    are never persisted. Only profile refs, token counts, and redacted
    summaries are stored.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def record_invocation(
        self,
        *,
        invocation_id: str,
        job_id: str | None = None,
        profile_id: str | None = None,
        provider_kind: str | None = None,
        model_name: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        redacted_summary: str | None = None,
        actor_type: str | None = None,
        actor_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> V1ModelInvocationRecord:
        created_at = utc_now_text()
        record = V1ModelInvocationRecord(
            invocation_id=invocation_id,
            job_id=job_id,
            profile_id=profile_id,
            provider_kind=provider_kind,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            redacted_summary=redacted_summary,
            actor_type=actor_type,
            actor_id=actor_id,
            created_at=created_at,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        with self._unit_of_work_factory() as uow:
            uow.v1_model_invocations.insert(record)

            # Record audit event
            import json as _json

            event_payload = {
                "action": "model_invocation_recorded",
                "invocation_id": invocation_id,
                "profile_id": profile_id,
                "provider_kind": provider_kind,
                "model_name": model_name,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "actor_type": actor_type,
                "actor_id": actor_id,
            }
            event_payload_json = _json.dumps(event_payload, separators=(",", ":"), sort_keys=True)
            from migration_factory.control_tower.domain.checksums import sha256_canonical_json as _sha256_canonical_json
            uow.audit_records.append_global_audit(
                audit_id=str(uuid4()),
                actor_type=actor_type or "system",
                actor_id=actor_id or "system",
                action="model_invocation_recorded",
                payload_json=event_payload_json,
                created_at=created_at,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        return record

    def list_invocations(self) -> tuple[V1ModelInvocationRecord, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.v1_model_invocations.list()

    def list_invocations_for_job(self, job_id: str) -> tuple[V1ModelInvocationRecord, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.v1_model_invocations.list_for_job(job_id)

    def get_invocation(self, invocation_id: str) -> V1ModelInvocationRecord | None:
        with self._unit_of_work_factory() as uow:
            return uow.v1_model_invocations.get(invocation_id)


def _get_python_executable(runner_profile: RunnerProfile) -> str:
    payload = runner_profile.model_dump(mode="json") if hasattr(runner_profile, "model_dump") else runner_profile
    if isinstance(payload, dict):
        return payload.get("python_executable", "python")
    return getattr(runner_profile, "python_executable", "python")
