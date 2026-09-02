"""SQLite repository implementations for Control Tower."""

from __future__ import annotations

import json
import sqlite3
from typing import Sequence

from migration_factory.control_tower.application.dto import (
    ArtifactDto,
    AuditRecordDto,
    CommandExecutionDto,
    IdempotencyRecordDto,
    MigrationJobDto,
    PipelineDefinitionDto,
    RunnerProfileDto,
    RunEventDto,
)
from migration_factory.control_tower.domain.entities import (
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
    V1ContextPackManifestRecord,
    V1FakeRepairProposalRecord,
    V1ModelInvocationRecord,
    V1PatchApplicationRecord,
    V1PatchPolicyValidationRecord,
    V1PatchRollbackRecord,
    V1PlanAmendmentRecord,
    V1PlanReviewDecisionRecord,
    V1RepairClassificationRecord,
    V1PlanRevisionRecord,
    V1PrivilegedActionDecisionRecord,
    V1PrivilegedActionExecutionRecord,
    V1PrivilegedActionRecord,
    V1ProofReportGateRecord,
    V1ProofReportRecord,
    V1SandboxSnapshotRecord,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.errors import NotFoundError, StorageIntegrityError, WorkspaceConflictError
from migration_factory.control_tower.domain.states import JobState, TargetProofLevel
from migration_factory.control_tower.schemas.pipeline_definition import PipelineDefinition
from migration_factory.control_tower.schemas.runner_profile import RunnerProfile


class SqliteRunnerProfileRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_exact(self, runner_profile_id: str, runner_profile_version: str) -> RunnerProfileRecord | None:
        row = self._select_one(runner_profile_id, runner_profile_version)
        if row is None:
            return None
        payload = RunnerProfile.model_validate_json(str(row["payload_json"]))
        return RunnerProfileRecord(
            runner_profile_id=str(row["runner_profile_id"]),
            runner_profile_version=str(row["runner_profile_version"]),
            display_name=str(row["display_name"]),
            schema_version=str(row["schema_version"]),
            payload_json=str(row["payload_json"]),
            payload_checksum=str(row["payload_checksum"]),
            created_at=str(row["created_at"]),
            created_by=str(row["created_by"]),
            payload=payload,
        )

    def get(self, runner_profile_id: str, runner_profile_version: str) -> RunnerProfileDto | None:
        row = self._select_one(runner_profile_id, runner_profile_version)
        return _runner_profile_from_row(row) if row is not None else None

    def list(self) -> tuple[RunnerProfileDto, ...]:
        rows = self._connection.execute(
            """
            SELECT runner_profile_id, runner_profile_version, display_name, schema_version,
                   payload_json, payload_checksum, created_at, created_by
            FROM runner_profiles
            ORDER BY runner_profile_id, runner_profile_version
            """
        ).fetchall()
        return tuple(_runner_profile_from_row(row) for row in rows)

    def insert(self, profile: RunnerProfileDto) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO runner_profiles (
                    runner_profile_id, runner_profile_version, display_name, schema_version,
                    payload_json, payload_checksum, created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.runner_profile_id,
                    profile.runner_profile_version,
                    profile.display_name,
                    profile.schema_version,
                    profile.payload_json,
                    profile.payload_checksum,
                    profile.created_at,
                    profile.created_by,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def find_checksum(self, runner_profile_id: str, runner_profile_version: str) -> str | None:
        row = self._connection.execute(
            """
            SELECT payload_checksum
            FROM runner_profiles
            WHERE runner_profile_id = ? AND runner_profile_version = ?
            """,
            (runner_profile_id, runner_profile_version),
        ).fetchone()
        return str(row["payload_checksum"]) if row is not None else None

    def _select_one(self, runner_profile_id: str, runner_profile_version: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT runner_profile_id, runner_profile_version, display_name, schema_version,
                   payload_json, payload_checksum, created_at, created_by
            FROM runner_profiles
            WHERE runner_profile_id = ? AND runner_profile_version = ?
            """,
            (runner_profile_id, runner_profile_version),
        ).fetchone()


class SqlitePipelineDefinitionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_exact(self, pipeline_id: str, pipeline_version: str) -> PipelineDefinitionRecord | None:
        row = self._select_one(pipeline_id, pipeline_version)
        if row is None:
            return None
        payload = PipelineDefinition.model_validate_json(str(row["payload_json"]))
        return PipelineDefinitionRecord(
            pipeline_id=str(row["pipeline_id"]),
            pipeline_version=str(row["pipeline_version"]),
            display_name=str(row["display_name"]),
            schema_version=str(row["schema_version"]),
            graph_version=str(row["graph_version"]),
            graph_state_schema_version=str(row["graph_state_schema_version"]),
            payload_json=str(row["payload_json"]),
            payload_checksum=str(row["payload_checksum"]),
            created_at=str(row["created_at"]),
            created_by=str(row["created_by"]),
            payload=payload,
        )

    def get(self, pipeline_id: str, pipeline_version: str) -> PipelineDefinitionDto | None:
        row = self._select_one(pipeline_id, pipeline_version)
        return _pipeline_definition_from_row(row) if row is not None else None

    def list(self) -> tuple[PipelineDefinitionDto, ...]:
        rows = self._connection.execute(
            """
            SELECT pipeline_id, pipeline_version, display_name, schema_version,
                   graph_version, graph_state_schema_version, payload_json,
                   payload_checksum, created_at, created_by
            FROM pipeline_definitions
            ORDER BY pipeline_id, pipeline_version
            """
        ).fetchall()
        return tuple(_pipeline_definition_from_row(row) for row in rows)

    def insert(self, pipeline: PipelineDefinitionDto) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO pipeline_definitions (
                    pipeline_id, pipeline_version, display_name, schema_version,
                    graph_version, graph_state_schema_version, payload_json, payload_checksum,
                    created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pipeline.pipeline_id,
                    pipeline.pipeline_version,
                    pipeline.display_name,
                    pipeline.schema_version,
                    pipeline.graph_version,
                    pipeline.graph_state_schema_version,
                    pipeline.payload_json,
                    pipeline.payload_checksum,
                    pipeline.created_at,
                    pipeline.created_by,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def find_checksum(self, pipeline_id: str, pipeline_version: str) -> str | None:
        row = self._connection.execute(
            """
            SELECT payload_checksum
            FROM pipeline_definitions
            WHERE pipeline_id = ? AND pipeline_version = ?
            """,
            (pipeline_id, pipeline_version),
        ).fetchone()
        return str(row["payload_checksum"]) if row is not None else None

    def _select_one(self, pipeline_id: str, pipeline_version: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT pipeline_id, pipeline_version, display_name, schema_version,
                   graph_version, graph_state_schema_version, payload_json,
                   payload_checksum, created_at, created_by
            FROM pipeline_definitions
            WHERE pipeline_id = ? AND pipeline_version = ?
            """,
            (pipeline_id, pipeline_version),
        ).fetchone()


class SqliteMigrationJobRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert_created(self, job: MigrationJobRecord) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO migration_jobs (
                    job_id, version, status, active_slot, last_event_sequence,
                    runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
                    target_proof_level, achieved_proof_level, legacy_source_ref, output_root_ref,
                    created_at, updated_at, started_at, finished_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.version,
                    job.status.value,
                    job.active_slot,
                    job.last_event_sequence,
                    job.runner_profile_id,
                    job.runner_profile_version,
                    job.pipeline_id,
                    job.pipeline_version,
                    job.target_proof_level.value,
                    job.achieved_proof_level.value if job.achieved_proof_level else None,
                    job.legacy_source_ref,
                    job.output_root_ref,
                    job.created_at,
                    job.updated_at,
                    job.started_at,
                    job.finished_at,
                    job.created_by,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get_active_job(self) -> MigrationJobRecord | None:
        row = self._connection.execute(
            """
            SELECT job_id, version, status, active_slot, last_event_sequence,
                   runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
                   target_proof_level, achieved_proof_level, legacy_source_ref, output_root_ref,
                   created_at, updated_at, started_at, finished_at, created_by
            FROM migration_jobs
            WHERE active_slot = 1
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        return _migration_job_record_from_row(row) if row is not None else None

    def get(self, job_id: str) -> MigrationJobDto | None:
        row = self._connection.execute(
            """
            SELECT job_id, version, status, active_slot, last_event_sequence,
                   created_at, updated_at, started_at, finished_at
            FROM migration_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        return _migration_job_dto_from_row(row) if row is not None else None

    def transition_state(
        self,
        job_id: str,
        expected_version: int,
        target_state: JobState,
        active_slot: int | None,
        updated_at: str,
    ) -> bool:
        cursor = self._connection.execute(
            """
            UPDATE migration_jobs
            SET status = ?,
                version = version + 1,
                active_slot = ?,
                updated_at = ?
            WHERE job_id = ?
              AND version = ?
            """,
            (
                target_state.value,
                active_slot,
                updated_at,
                job_id,
                expected_version,
            ),
        )
        return cursor.rowcount == 1

    def list(self) -> tuple[MigrationJobDto, ...]:
        rows = self._connection.execute(
            """
            SELECT job_id, version, status, active_slot, last_event_sequence,
                   created_at, updated_at, started_at, finished_at
            FROM migration_jobs
            ORDER BY created_at, job_id
            """
        ).fetchall()
        return tuple(_migration_job_dto_from_row(row) for row in rows)

    def increment_event_sequence(self, job_id: str) -> int:
        cursor = self._connection.execute(
            """
            UPDATE migration_jobs
            SET last_event_sequence = last_event_sequence + 1
            WHERE job_id = ?
            """,
            (job_id,),
        )
        if cursor.rowcount != 1:
            raise NotFoundError("migration job", job_id)

        row = self._connection.execute(
            """
            SELECT last_event_sequence
            FROM migration_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("migration job", job_id)
        return int(row["last_event_sequence"])

class SqliteRunConfigurationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, run_configuration: RunConfigurationRecord) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO run_configurations (
                    run_configuration_id, job_id, schema_version,
                    runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
                    target_proof_level, enabled_gates_json, policy_json,
                    payload_json, payload_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_configuration.run_configuration_id,
                    run_configuration.job_id,
                    run_configuration.schema_version,
                    run_configuration.runner_profile_id,
                    run_configuration.runner_profile_version,
                    run_configuration.pipeline_id,
                    run_configuration.pipeline_version,
                    run_configuration.target_proof_level.value,
                    run_configuration.enabled_gates_json,
                    run_configuration.policy_json,
                    run_configuration.payload_json,
                    run_configuration.payload_checksum,
                    run_configuration.created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get_for_job(self, job_id: str) -> RunConfigurationRecord | None:
        row = self._connection.execute(
            """
            SELECT run_configuration_id, job_id, schema_version, runner_profile_id,
                   runner_profile_version, pipeline_id, pipeline_version, target_proof_level,
                   enabled_gates_json, policy_json, payload_json, payload_checksum, created_at
            FROM run_configurations
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return RunConfigurationRecord(
            run_configuration_id=str(row["run_configuration_id"]),
            job_id=str(row["job_id"]),
            schema_version=str(row["schema_version"]),
            runner_profile_id=str(row["runner_profile_id"]),
            runner_profile_version=str(row["runner_profile_version"]),
            pipeline_id=str(row["pipeline_id"]),
            pipeline_version=str(row["pipeline_version"]),
            target_proof_level=TargetProofLevel(str(row["target_proof_level"])),
            enabled_gates_json=str(row["enabled_gates_json"]),
            policy_json=str(row["policy_json"]),
            payload_json=str(row["payload_json"]),
            payload_checksum=str(row["payload_checksum"]),
            created_at=str(row["created_at"]),
        )


class SqliteStageRunRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert_many(self, stage_runs: Sequence[StageRunRecord]) -> None:
        try:
            self._connection.executemany(
                """
                INSERT INTO stage_runs (
                    stage_run_id, job_id, stage_index, stage_id, status,
                    input_source_json, created_at, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        stage.stage_run_id,
                        stage.job_id,
                        stage.stage_index,
                        stage.stage_id,
                        stage.status,
                        stage.input_source_json,
                        stage.created_at,
                        stage.started_at,
                        stage.finished_at,
                    )
                    for stage in stage_runs
                ],
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, stage_run_id: str) -> StageRunRecord | None:
        row = self._connection.execute(
            """
            SELECT stage_run_id, job_id, stage_index, stage_id, status, input_source_json,
                   created_at, started_at, finished_at
            FROM stage_runs
            WHERE stage_run_id = ?
            """,
            (stage_run_id,),
        ).fetchone()
        if row is None:
            return None
        return _stage_run_record_from_row(row)

    def list_for_job(self, job_id: str) -> tuple[StageRunRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT stage_run_id, job_id, stage_index, stage_id, status, input_source_json,
                   created_at, started_at, finished_at
            FROM stage_runs
            WHERE job_id = ?
            ORDER BY stage_index
            """,
            (job_id,),
        ).fetchall()
        return tuple(_stage_run_record_from_row(row) for row in rows)


class SqliteArtifactRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, artifact: ArtifactRecord) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, job_id, stage_run_id, artifact_type,
                    registered_root_id, relative_path, normalized_relative_path,
                    content_type, size_bytes, checksum_algorithm, checksum,
                    created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    artifact.job_id,
                    artifact.stage_run_id,
                    artifact.artifact_type,
                    artifact.registered_root_id,
                    artifact.relative_path,
                    artifact.normalized_relative_path,
                    artifact.content_type,
                    artifact.size_bytes,
                    artifact.checksum_algorithm,
                    artifact.checksum,
                    artifact.created_at,
                    artifact.created_by,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get_exact(
        self,
        job_id: str,
        registered_root_id: str,
        normalized_relative_path: str,
    ) -> ArtifactDto | None:
        row = self._connection.execute(
            """
            SELECT artifact_id, job_id, stage_run_id, artifact_type, registered_root_id,
                   relative_path, normalized_relative_path, content_type, size_bytes,
                   checksum_algorithm, checksum, created_at, created_by
            FROM artifacts
            WHERE job_id = ?
              AND registered_root_id = ?
              AND normalized_relative_path = ?
            """,
            (job_id, registered_root_id, normalized_relative_path),
        ).fetchone()
        return _artifact_from_row(row) if row is not None else None

    def list_for_job(self, job_id: str) -> tuple[ArtifactDto, ...]:
        rows = self._connection.execute(
            """
            SELECT artifact_id, job_id, stage_run_id, artifact_type, registered_root_id,
                   relative_path, normalized_relative_path, content_type, size_bytes,
                   checksum_algorithm, checksum, created_at, created_by
            FROM artifacts
            WHERE job_id = ?
            ORDER BY created_at, artifact_id
            """,
            (job_id,),
        ).fetchall()
        return tuple(_artifact_from_row(row) for row in rows)


class SqliteRunEventRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, event: RunEventRecord) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO run_events (
                    event_id, job_id, sequence, event_type, actor_type, actor_id,
                    correlation_id, causation_id, payload_json, payload_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.job_id,
                    event.sequence,
                    event.event_type,
                    event.actor_type,
                    event.actor_id,
                    event.correlation_id,
                    event.causation_id,
                    event.payload_json,
                    event.payload_checksum,
                    event.created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

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
    ) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO run_events (
                    event_id, job_id, sequence, event_type, actor_type, actor_id,
                    correlation_id, causation_id, payload_json, payload_checksum,
                    created_at
                ) VALUES (?, ?, ?, 'job_state_changed', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    job_id,
                    sequence,
                    actor_type,
                    actor_id,
                    correlation_id,
                    causation_id,
                    payload_json,
                    payload_checksum,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def list_for_job(self, job_id: str) -> tuple[RunEventDto, ...]:
        rows = self._connection.execute(
            """
            SELECT event_id, job_id, sequence, event_type, actor_type, actor_id,
                   correlation_id, causation_id, payload_json, payload_checksum,
                   created_at
            FROM run_events
            WHERE job_id = ?
            ORDER BY sequence
            """,
            (job_id,),
        ).fetchall()
        return tuple(_run_event_from_row(row) for row in rows)

    def list_for_job_after(
        self,
        job_id: str,
        after_sequence: int,
        limit: int,
    ) -> tuple[RunEventDto, ...]:
        rows = self._connection.execute(
            """
            SELECT event_id, job_id, sequence, event_type, actor_type, actor_id,
                   correlation_id, causation_id, payload_json, payload_checksum,
                   created_at
            FROM run_events
            WHERE job_id = ?
              AND sequence > ?
            ORDER BY sequence
            LIMIT ?
            """,
            (job_id, after_sequence, limit),
        ).fetchall()
        return tuple(_run_event_from_row(row) for row in rows)

    def count_for_job(self, job_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM run_events WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return int(row["count"])


class SqliteAuditRecordRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, audit_record: AuditRecord) -> None:
        try:
            self._insert(
                audit_id=audit_record.audit_id,
                job_id=audit_record.job_id,
                actor_type=audit_record.actor_type,
                actor_id=audit_record.actor_id,
                action=audit_record.action,
                prior_state=audit_record.prior_state,
                new_state=audit_record.new_state,
                job_version=audit_record.job_version,
                correlation_id=audit_record.correlation_id,
                causation_id=audit_record.causation_id,
                payload_json=audit_record.payload_json,
                created_at=audit_record.created_at,
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

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
    ) -> None:
        try:
            self._insert(
                audit_id=audit_id,
                job_id=None,
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                prior_state=None,
                new_state=None,
                job_version=None,
                correlation_id=correlation_id,
                causation_id=causation_id,
                payload_json=payload_json,
                created_at=created_at,
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

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
    ) -> None:
        try:
            self._insert(
                audit_id=audit_id,
                job_id=job_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action="job_state_changed",
                prior_state=prior_state.value,
                new_state=new_state.value,
                job_version=job_version,
                correlation_id=correlation_id,
                causation_id=causation_id,
                payload_json=payload_json,
                created_at=created_at,
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def list(self) -> tuple[AuditRecordDto, ...]:
        rows = self._connection.execute(
            """
            SELECT audit_id, job_id, actor_type, actor_id, action, prior_state, new_state,
                   job_version, correlation_id, causation_id, payload_json, created_at
            FROM audit_records
            ORDER BY created_at, audit_id
            """
        ).fetchall()
        return tuple(_audit_record_from_row(row) for row in rows)

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS count FROM audit_records").fetchone()
        return int(row["count"])

    def list_for_job(self, job_id: str) -> tuple[AuditRecordDto, ...]:
        rows = self._connection.execute(
            """
            SELECT audit_id, job_id, actor_type, actor_id, action, prior_state, new_state,
                   job_version, correlation_id, causation_id, payload_json, created_at
            FROM audit_records
            WHERE job_id = ?
            ORDER BY created_at, audit_id
            """,
            (job_id,),
        ).fetchall()
        return tuple(_audit_record_from_row(row) for row in rows)

    def count_for_job(self, job_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM audit_records WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return int(row["count"])

    def _insert(
        self,
        *,
        audit_id: str,
        job_id: str | None,
        actor_type: str,
        actor_id: str,
        action: str,
        prior_state: str | None,
        new_state: str | None,
        job_version: int | None,
        correlation_id: str | None,
        causation_id: str | None,
        payload_json: str,
        created_at: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO audit_records (
                audit_id, job_id, actor_type, actor_id, action, prior_state, new_state,
                job_version, correlation_id, causation_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                job_id,
                actor_type,
                actor_id,
                action,
                prior_state,
                new_state,
                job_version,
                correlation_id,
                causation_id,
                payload_json,
                created_at,
            ),
        )


class SqliteCommandExecutionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert_queued(self, command: CommandExecutionRecord) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO command_executions (
                    command_id, job_id, operation, status, created_at, updated_at,
                    correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command.command_id,
                    command.job_id,
                    command.operation,
                    command.status.value,
                    command.created_at,
                    command.updated_at,
                    command.correlation_id,
                    command.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, command_id: str) -> CommandExecutionDto | None:
        row = self._connection.execute(
            """
            SELECT command_id, job_id, operation, status, created_at, updated_at,
                   correlation_id, causation_id,
                   command_manifest_artifact_id, working_directory_root_id,
                   working_directory_relative_path, worker_id, launch_attempt,
                   stdout_offset, stderr_offset,
                   worker_pid, process_control_id, process_started_at
            FROM command_executions
            WHERE command_id = ?
            """,
            (command_id,),
        ).fetchone()
        return _command_execution_from_row(row) if row is not None else None

    def list_for_job(self, job_id: str) -> tuple[CommandExecutionDto, ...]:
        rows = self._connection.execute(
            """
            SELECT command_id, job_id, operation, status, created_at, updated_at,
                   correlation_id, causation_id,
                   command_manifest_artifact_id, working_directory_root_id,
                   working_directory_relative_path, worker_id, launch_attempt,
                   stdout_offset, stderr_offset,
                   worker_pid, process_control_id, process_started_at
            FROM command_executions
            WHERE job_id = ?
            ORDER BY created_at, command_id
            """,
            (job_id,),
        ).fetchall()
        return tuple(_command_execution_from_row(row) for row in rows)

    def get_active_for_job(self, job_id: str) -> CommandExecutionDto | None:
        row = self._connection.execute(
            """
            SELECT command_id, job_id, operation, status, created_at, updated_at,
                   correlation_id, causation_id,
                   command_manifest_artifact_id, working_directory_root_id,
                   working_directory_relative_path, worker_id, launch_attempt,
                   stdout_offset, stderr_offset,
                   worker_pid, process_control_id, process_started_at
            FROM command_executions
            WHERE job_id = ?
              AND status IN ('QUEUED', 'STARTING', 'RUNNING', 'CANCELLING')
            ORDER BY created_at, command_id
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        return _command_execution_from_row(row) if row is not None else None

    def update_status(self, command_id: str, status: CommandState) -> None:
        cursor = self._connection.execute(
            """UPDATE command_executions
            SET status = ?,
                updated_at = ?
            WHERE command_id = ?""",
            (
                status.value,
                utc_now_text(),
                command_id,
            ),
        )
        if cursor.rowcount == 0:
            raise NotFoundError("command execution", command_id)

    def update_workspace_columns(
        self,
        command_id: str,
        *,
        command_manifest_artifact_id: str,
        working_directory_root_id: str,
        working_directory_relative_path: str,
        worker_id: str,
        launch_attempt: int,
    ) -> None:
        cursor = self._connection.execute(
            """UPDATE command_executions
            SET command_manifest_artifact_id = ?,
                working_directory_root_id = ?,
                working_directory_relative_path = ?,
                worker_id = ?,
                launch_attempt = ?,
                updated_at = ?
            WHERE command_id = ?
              AND command_manifest_artifact_id IS NULL""",
            (
                command_manifest_artifact_id,
                working_directory_root_id,
                working_directory_relative_path,
                worker_id,
                launch_attempt,
                utc_now_text(),
                command_id,
            ),
        )
        if cursor.rowcount == 0:
            raise WorkspaceConflictError(
                f"Workspace already prepared for command {command_id!r}"
            )

    def update_process_columns(
        self,
        command_id: str,
        *,
        status: CommandState,
        process_control_id: str,
        worker_pid: int,
        process_started_at: str,
    ) -> None:
        cursor = self._connection.execute(
            """UPDATE command_executions
            SET status = ?,
                process_control_id = ?,
                worker_pid = ?,
                process_started_at = ?,
                updated_at = ?
            WHERE command_id = ?
              AND command_manifest_artifact_id IS NOT NULL
              AND status IN ('QUEUED', 'STARTING')""",
            (
                status.value,
                process_control_id,
                worker_pid,
                process_started_at,
                utc_now_text(),
                command_id,
            ),
        )
        if cursor.rowcount == 0:
            raise NotFoundError(
                "command execution",
                f"{command_id} not in QUEUED/STARTING or workspace not prepared",
            )

    def get_output_offsets(self, command_id: str) -> tuple[int, int]:
        row = self._connection.execute(
            """
            SELECT stdout_offset, stderr_offset
            FROM command_executions
            WHERE command_id = ?
            """,
            (command_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("command execution", command_id)
        return int(row["stdout_offset"]), int(row["stderr_offset"])

    def update_output_offsets(
        self,
        command_id: str,
        *,
        stdout_offset: int,
        stderr_offset: int,
    ) -> None:
        cursor = self._connection.execute(
            """UPDATE command_executions
            SET stdout_offset = ?,
                stderr_offset = ?,
                updated_at = ?
            WHERE command_id = ?""",
            (stdout_offset, stderr_offset, utc_now_text(), command_id),
        )
        if cursor.rowcount == 0:
            raise NotFoundError("command execution", command_id)

    def set_output_limit_exceeded(self, command_id: str) -> None:
        cursor = self._connection.execute(
            """UPDATE command_executions
            SET output_limit_exceeded = 1,
                updated_at = ?
            WHERE command_id = ?""",
            (utc_now_text(), command_id),
        )
        if cursor.rowcount == 0:
            raise NotFoundError("command execution", command_id)

    def get_terminal_artifact_links(self, command_id: str) -> dict[str, str | None]:
        row = self._connection.execute(
            """
            SELECT stdout_artifact_id, stderr_artifact_id, result_artifact_id,
                   spool_artifact_id, finalization_status, finalized_at
            FROM command_executions
            WHERE command_id = ?
            """,
            (command_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("command execution", command_id)
        return {
            "stdout_artifact_id": row["stdout_artifact_id"],
            "stderr_artifact_id": row["stderr_artifact_id"],
            "result_artifact_id": row["result_artifact_id"],
            "spool_artifact_id": row["spool_artifact_id"],
            "finalization_status": str(row["finalization_status"]),
            "finalized_at": row["finalized_at"],
        }

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
    ) -> None:
        cursor = self._connection.execute(
            """UPDATE command_executions
            SET stdout_artifact_id = ?,
                stderr_artifact_id = ?,
                result_artifact_id = ?,
                spool_artifact_id = ?,
                finalization_status = ?,
                finalized_at = ?,
                updated_at = ?
            WHERE command_id = ?
              AND finalization_status = 'PENDING'""",
            (
                stdout_artifact_id,
                stderr_artifact_id,
                result_artifact_id,
                spool_artifact_id,
                finalization_status,
                finalized_at,
                utc_now_text(),
                command_id,
            ),
        )
        if cursor.rowcount == 0:
            raise NotFoundError(
                "command execution",
                f"{command_id} already finalized or not found",
            )


class SqliteIdempotencyRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, operation: str, idempotency_key: str) -> IdempotencyRecordDto | None:
        row = self._connection.execute(
            """
            SELECT operation, idempotency_key, request_checksum, resource_type,
                   resource_id, original_status_code, created_at
            FROM idempotency_records
            WHERE operation = ? AND idempotency_key = ?
            """,
            (operation, idempotency_key),
        ).fetchone()
        return _idempotency_record_from_row(row) if row is not None else None

    def insert(self, record: IdempotencyRecord) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO idempotency_records (
                    operation, idempotency_key, request_checksum, resource_type,
                    resource_id, original_status_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.operation,
                    record.idempotency_key,
                    record.request_checksum,
                    record.resource_type,
                    record.resource_id,
                    record.original_status_code,
                    record.created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc


class SqliteStageChainLedgerRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert_many(self, ledger_entries: Sequence[StageChainLedgerRecord]) -> None:
        try:
            self._connection.executemany(
                """
                INSERT INTO v1_stage_chain_ledger (
                    ledger_id, job_id, stage_index, stage_run_id, chain_status,
                    input_source_kind, input_checksum,
                    output_artifact_id, output_checksum, output_registered_at,
                    checksum_guard, created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        entry.ledger_id,
                        entry.job_id,
                        entry.stage_index,
                        entry.stage_run_id,
                        entry.chain_status,
                        entry.input_source_kind,
                        entry.input_checksum,
                        entry.output_artifact_id,
                        entry.output_checksum,
                        entry.output_registered_at,
                        entry.checksum_guard,
                        entry.created_at,
                        entry.created_by,
                    )
                    for entry in ledger_entries
                ],
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def list_for_job(self, job_id: str) -> tuple[StageChainLedgerRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT ledger_id, job_id, stage_index, stage_run_id, chain_status,
                   input_source_kind, input_checksum,
                   output_artifact_id, output_checksum, output_registered_at,
                   checksum_guard, created_at, created_by
            FROM v1_stage_chain_ledger
            WHERE job_id = ?
            ORDER BY stage_index
            """,
            (job_id,),
        ).fetchall()
        return tuple(_stage_chain_ledger_record_from_row(row) for row in rows)

    def insert_output(self, output: StageOutputRegistryRecord) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO v1_stage_output_registry (
                    output_id, job_id, stage_index, stage_run_id,
                    artifact_id, artifact_type, output_kind,
                    checksum_algorithm, checksum,
                    registered_at, registered_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    output.output_id,
                    output.job_id,
                    output.stage_index,
                    output.stage_run_id,
                    output.artifact_id,
                    output.artifact_type,
                    output.output_kind,
                    output.checksum_algorithm,
                    output.checksum,
                    output.registered_at,
                    output.registered_by,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def list_outputs_for_job(self, job_id: str) -> tuple[StageOutputRegistryRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT output_id, job_id, stage_index, stage_run_id,
                   artifact_id, artifact_type, output_kind,
                   checksum_algorithm, checksum,
                   registered_at, registered_by
            FROM v1_stage_output_registry
            WHERE job_id = ?
            ORDER BY stage_index, output_kind
            """,
            (job_id,),
        ).fetchall()
        return tuple(_stage_output_registry_record_from_row(row) for row in rows)

    def insert_event(self, event: StageChainEventRecord) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO v1_stage_chain_events (
                    event_id, job_id, stage_index, event_type,
                    prior_status, new_status,
                    ledger_id, output_id,
                    payload_json, payload_checksum,
                    created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.job_id,
                    event.stage_index,
                    event.event_type,
                    event.prior_status,
                    event.new_status,
                    event.ledger_id,
                    event.output_id,
                    event.payload_json,
                    event.payload_checksum,
                    event.created_at,
                    event.created_by,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def list_events_for_job(self, job_id: str) -> tuple[StageChainEventRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT event_id, job_id, stage_index, event_type,
                   prior_status, new_status,
                   ledger_id, output_id,
                   payload_json, payload_checksum,
                   created_at, created_by
            FROM v1_stage_chain_events
            WHERE job_id = ?
            ORDER BY created_at, event_id
            """,
            (job_id,),
        ).fetchall()
        return tuple(_stage_chain_event_record_from_row(row) for row in rows)


def _stage_chain_ledger_record_from_row(row: sqlite3.Row) -> StageChainLedgerRecord:
    return StageChainLedgerRecord(
        ledger_id=str(row["ledger_id"]),
        job_id=str(row["job_id"]),
        stage_index=int(row["stage_index"]),
        stage_run_id=str(row["stage_run_id"]),
        chain_status=str(row["chain_status"]),
        input_source_kind=str(row["input_source_kind"]),
        input_checksum=str(row["input_checksum"]) if row["input_checksum"] is not None else None,
        output_artifact_id=str(row["output_artifact_id"]) if row["output_artifact_id"] is not None else None,
        output_checksum=str(row["output_checksum"]) if row["output_checksum"] is not None else None,
        output_registered_at=str(row["output_registered_at"]) if row["output_registered_at"] is not None else None,
        checksum_guard=str(row["checksum_guard"]),
        created_at=str(row["created_at"]),
        created_by=str(row["created_by"]),
    )


def _stage_output_registry_record_from_row(row: sqlite3.Row) -> StageOutputRegistryRecord:
    return StageOutputRegistryRecord(
        output_id=str(row["output_id"]),
        job_id=str(row["job_id"]),
        stage_index=int(row["stage_index"]),
        stage_run_id=str(row["stage_run_id"]),
        artifact_id=str(row["artifact_id"]),
        artifact_type=str(row["artifact_type"]),
        output_kind=str(row["output_kind"]),
        checksum_algorithm=str(row["checksum_algorithm"]),
        checksum=str(row["checksum"]),
        registered_at=str(row["registered_at"]),
        registered_by=str(row["registered_by"]),
    )


def _stage_chain_event_record_from_row(row: sqlite3.Row) -> StageChainEventRecord:
    return StageChainEventRecord(
        event_id=str(row["event_id"]),
        job_id=str(row["job_id"]),
        stage_index=int(row["stage_index"]) if row["stage_index"] is not None else None,
        event_type=str(row["event_type"]),
        prior_status=str(row["prior_status"]) if row["prior_status"] is not None else None,
        new_status=str(row["new_status"]) if row["new_status"] is not None else None,
        ledger_id=str(row["ledger_id"]) if row["ledger_id"] is not None else None,
        output_id=str(row["output_id"]) if row["output_id"] is not None else None,
        payload_json=str(row["payload_json"]),
        payload_checksum=str(row["payload_checksum"]),
        created_at=str(row["created_at"]),
        created_by=str(row["created_by"]),
    )


def _runner_profile_from_row(row: sqlite3.Row) -> RunnerProfileDto:
    payload_json = str(row["payload_json"])
    return RunnerProfileDto(
        runner_profile_id=str(row["runner_profile_id"]),
        runner_profile_version=str(row["runner_profile_version"]),
        display_name=str(row["display_name"]),
        schema_version=str(row["schema_version"]),
        payload=json.loads(payload_json),
        payload_json=payload_json,
        payload_checksum=str(row["payload_checksum"]),
        created_at=str(row["created_at"]),
        created_by=str(row["created_by"]),
    )


def _pipeline_definition_from_row(row: sqlite3.Row) -> PipelineDefinitionDto:
    payload_json = str(row["payload_json"])
    return PipelineDefinitionDto(
        pipeline_id=str(row["pipeline_id"]),
        pipeline_version=str(row["pipeline_version"]),
        display_name=str(row["display_name"]),
        schema_version=str(row["schema_version"]),
        graph_version=str(row["graph_version"]),
        graph_state_schema_version=str(row["graph_state_schema_version"]),
        payload=json.loads(payload_json),
        payload_json=payload_json,
        payload_checksum=str(row["payload_checksum"]),
        created_at=str(row["created_at"]),
        created_by=str(row["created_by"]),
    )


def _migration_job_dto_from_row(row: sqlite3.Row) -> MigrationJobDto:
    active_slot = row["active_slot"]
    return MigrationJobDto(
        job_id=str(row["job_id"]),
        version=int(row["version"]),
        status=JobState(str(row["status"])),
        active_slot=int(active_slot) if active_slot is not None else None,
        last_event_sequence=int(row["last_event_sequence"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=str(row["started_at"]) if row["started_at"] is not None else None,
        finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
    )


def _run_event_from_row(row: sqlite3.Row) -> RunEventDto:
    payload_json = str(row["payload_json"])
    return RunEventDto(
        event_id=str(row["event_id"]),
        job_id=str(row["job_id"]),
        sequence=int(row["sequence"]),
        event_type=str(row["event_type"]),
        actor_type=str(row["actor_type"]),
        actor_id=str(row["actor_id"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
        payload=json.loads(payload_json),
        payload_json=payload_json,
        payload_checksum=str(row["payload_checksum"]),
        created_at=str(row["created_at"]),
    )


def _audit_record_from_row(row: sqlite3.Row) -> AuditRecordDto:
    payload_json = str(row["payload_json"])
    job_version = row["job_version"]
    return AuditRecordDto(
        audit_id=str(row["audit_id"]),
        job_id=str(row["job_id"]) if row["job_id"] is not None else None,
        actor_type=str(row["actor_type"]),
        actor_id=str(row["actor_id"]),
        action=str(row["action"]),
        prior_state=str(row["prior_state"]) if row["prior_state"] is not None else None,
        new_state=str(row["new_state"]) if row["new_state"] is not None else None,
        job_version=int(job_version) if job_version is not None else None,
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
        payload=json.loads(payload_json),
        payload_json=payload_json,
        created_at=str(row["created_at"]),
    )


def _artifact_from_row(row: sqlite3.Row) -> ArtifactDto:
    return ArtifactDto(
        artifact_id=str(row["artifact_id"]),
        job_id=str(row["job_id"]),
        stage_run_id=str(row["stage_run_id"]) if row["stage_run_id"] is not None else None,
        artifact_type=str(row["artifact_type"]),
        registered_root_id=str(row["registered_root_id"]),
        relative_path=str(row["relative_path"]),
        normalized_relative_path=str(row["normalized_relative_path"]),
        content_type=str(row["content_type"]) if row["content_type"] is not None else None,
        size_bytes=int(row["size_bytes"]),
        checksum_algorithm=str(row["checksum_algorithm"]),
        checksum=str(row["checksum"]),
        created_at=str(row["created_at"]),
        created_by=str(row["created_by"]),
    )


def _command_execution_from_row(row: sqlite3.Row) -> CommandExecutionDto:
    return CommandExecutionDto(
        command_id=str(row["command_id"]),
        job_id=str(row["job_id"]),
        operation=str(row["operation"]),
        status=CommandState(str(row["status"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
        command_manifest_artifact_id=(
            str(row["command_manifest_artifact_id"])
            if row["command_manifest_artifact_id"] is not None
            else None
        ),
        working_directory_root_id=(
            str(row["working_directory_root_id"])
            if row["working_directory_root_id"] is not None
            else None
        ),
        working_directory_relative_path=(
            str(row["working_directory_relative_path"])
            if row["working_directory_relative_path"] is not None
            else None
        ),
        worker_id=(
            str(row["worker_id"]) if row["worker_id"] is not None else None
        ),
        launch_attempt=(
            int(row["launch_attempt"]) if row["launch_attempt"] is not None else None
        ),
        worker_pid=(
            int(row["worker_pid"]) if row["worker_pid"] is not None else None
        ),
        process_control_id=(
            str(row["process_control_id"]) if row["process_control_id"] is not None else None
        ),
        process_started_at=(
            str(row["process_started_at"]) if row["process_started_at"] is not None else None
        ),
    )


def _idempotency_record_from_row(row: sqlite3.Row) -> IdempotencyRecordDto:
    return IdempotencyRecordDto(
        operation=str(row["operation"]),
        idempotency_key=str(row["idempotency_key"]),
        request_checksum=str(row["request_checksum"]),
        resource_type=str(row["resource_type"]),
        resource_id=str(row["resource_id"]),
        original_status_code=int(row["original_status_code"]),
        created_at=str(row["created_at"]),
    )


def _stage_run_record_from_row(row: sqlite3.Row) -> StageRunRecord:
    return StageRunRecord(
        stage_run_id=str(row["stage_run_id"]),
        job_id=str(row["job_id"]),
        stage_index=int(row["stage_index"]),
        stage_id=str(row["stage_id"]),
        status=str(row["status"]),
        input_source_json=str(row["input_source_json"]),
        created_at=str(row["created_at"]),
        started_at=None if row["started_at"] is None else str(row["started_at"]),
        finished_at=None if row["finished_at"] is None else str(row["finished_at"]),
    )


def _migration_job_record_from_row(row: sqlite3.Row) -> MigrationJobRecord:
    return MigrationJobRecord(
        job_id=str(row["job_id"]),
        version=int(row["version"]),
        status=JobState(str(row["status"])),
        active_slot=None if row["active_slot"] is None else int(row["active_slot"]),
        last_event_sequence=int(row["last_event_sequence"]),
        runner_profile_id=str(row["runner_profile_id"]),
        runner_profile_version=str(row["runner_profile_version"]),
        pipeline_id=str(row["pipeline_id"]),
        pipeline_version=str(row["pipeline_version"]),
        target_proof_level=TargetProofLevel(str(row["target_proof_level"])),
        achieved_proof_level=(
            None
            if row["achieved_proof_level"] is None
            else TargetProofLevel(str(row["achieved_proof_level"]))
        ),
        legacy_source_ref=str(row["legacy_source_ref"]),
        output_root_ref=str(row["output_root_ref"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=None if row["started_at"] is None else str(row["started_at"]),
        finished_at=None if row["finished_at"] is None else str(row["finished_at"]),
        created_by=str(row["created_by"]),
    )


class SqliteV1ModelInvocationRepository:
    """SQLite repository for v1_model_invocations table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, invocation: V1ModelInvocationRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_model_invocations (
                    invocation_id, job_id, profile_id, provider_kind, model_name,
                    prompt_tokens, completion_tokens, total_tokens, redacted_summary,
                    actor_type, actor_id, created_at, correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    invocation.invocation_id,
                    invocation.job_id,
                    invocation.profile_id,
                    invocation.provider_kind,
                    invocation.model_name,
                    invocation.prompt_tokens,
                    invocation.completion_tokens,
                    invocation.total_tokens,
                    invocation.redacted_summary,
                    invocation.actor_type,
                    invocation.actor_id,
                    invocation.created_at,
                    invocation.correlation_id,
                    invocation.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, invocation_id: str) -> V1ModelInvocationRecord | None:
        row = self._connection.execute(
            """SELECT invocation_id, job_id, profile_id, provider_kind, model_name,
                      prompt_tokens, completion_tokens, total_tokens, redacted_summary,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_model_invocations WHERE invocation_id = ?""",
            (invocation_id,),
        ).fetchone()
        if row is None:
            return None
        return _model_invocation_record_from_row(row)

    def list(self) -> tuple[V1ModelInvocationRecord, ...]:
        rows = self._connection.execute(
            """SELECT invocation_id, job_id, profile_id, provider_kind, model_name,
                      prompt_tokens, completion_tokens, total_tokens, redacted_summary,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_model_invocations ORDER BY created_at DESC"""
        ).fetchall()
        return tuple(_model_invocation_record_from_row(r) for r in rows)

    def list_for_job(self, job_id: str) -> tuple[V1ModelInvocationRecord, ...]:
        rows = self._connection.execute(
            """SELECT invocation_id, job_id, profile_id, provider_kind, model_name,
                      prompt_tokens, completion_tokens, total_tokens, redacted_summary,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_model_invocations
               WHERE job_id = ? ORDER BY created_at DESC""",
            (job_id,),
        ).fetchall()
        return tuple(_model_invocation_record_from_row(r) for r in rows)


def _model_invocation_record_from_row(row: sqlite3.Row) -> V1ModelInvocationRecord:
    return V1ModelInvocationRecord(
        invocation_id=str(row["invocation_id"]),
        job_id=str(row["job_id"]) if row["job_id"] is not None else None,
        profile_id=str(row["profile_id"]) if row["profile_id"] is not None else None,
        provider_kind=str(row["provider_kind"]) if row["provider_kind"] is not None else None,
        model_name=str(row["model_name"]) if row["model_name"] is not None else None,
        prompt_tokens=int(row["prompt_tokens"]) if row["prompt_tokens"] is not None else None,
        completion_tokens=int(row["completion_tokens"]) if row["completion_tokens"] is not None else None,
        total_tokens=int(row["total_tokens"]) if row["total_tokens"] is not None else None,
        redacted_summary=str(row["redacted_summary"]) if row["redacted_summary"] is not None else None,
        actor_type=str(row["actor_type"]) if row["actor_type"] is not None else None,
        actor_id=str(row["actor_id"]) if row["actor_id"] is not None else None,
        created_at=str(row["created_at"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1ContextPackManifestRepository:
    """SQLite repository for v1_context_pack_manifests table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, manifest: V1ContextPackManifestRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_context_pack_manifests (
                    manifest_id, job_id, stage_run_id, pack_type, pack_version,
                    title, description, evidence_refs_json, bounds_json,
                    redaction_policy, redacted_summary, checksum_algorithm,
                    checksum, model_profile_id, model_name, token_count,
                    created_at, created_by, correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    manifest.manifest_id,
                    manifest.job_id,
                    manifest.stage_run_id,
                    manifest.pack_type,
                    manifest.pack_version,
                    manifest.title,
                    manifest.description,
                    manifest.evidence_refs_json,
                    manifest.bounds_json,
                    manifest.redaction_policy,
                    manifest.redacted_summary,
                    manifest.checksum_algorithm,
                    manifest.checksum,
                    manifest.model_profile_id,
                    manifest.model_name,
                    manifest.token_count,
                    manifest.created_at,
                    manifest.created_by,
                    manifest.correlation_id,
                    manifest.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, manifest_id: str) -> V1ContextPackManifestRecord | None:
        row = self._connection.execute(
            """SELECT manifest_id, job_id, stage_run_id, pack_type, pack_version,
                      title, description, evidence_refs_json, bounds_json,
                      redaction_policy, redacted_summary, checksum_algorithm,
                      checksum, model_profile_id, model_name, token_count,
                      created_at, created_by, correlation_id, causation_id
               FROM v1_context_pack_manifests WHERE manifest_id = ?""",
            (manifest_id,),
        ).fetchone()
        if row is None:
            return None
        return _context_pack_manifest_from_row(row)

    def list(self) -> tuple[V1ContextPackManifestRecord, ...]:
        rows = self._connection.execute(
            """SELECT manifest_id, job_id, stage_run_id, pack_type, pack_version,
                      title, description, evidence_refs_json, bounds_json,
                      redaction_policy, redacted_summary, checksum_algorithm,
                      checksum, model_profile_id, model_name, token_count,
                      created_at, created_by, correlation_id, causation_id
               FROM v1_context_pack_manifests ORDER BY created_at DESC"""
        ).fetchall()
        return tuple(_context_pack_manifest_from_row(r) for r in rows)

    def list_for_job(self, job_id: str) -> tuple[V1ContextPackManifestRecord, ...]:
        rows = self._connection.execute(
            """SELECT manifest_id, job_id, stage_run_id, pack_type, pack_version,
                      title, description, evidence_refs_json, bounds_json,
                      redaction_policy, redacted_summary, checksum_algorithm,
                      checksum, model_profile_id, model_name, token_count,
                      created_at, created_by, correlation_id, causation_id
               FROM v1_context_pack_manifests
               WHERE job_id = ? ORDER BY created_at DESC""",
            (job_id,),
        ).fetchall()
        return tuple(_context_pack_manifest_from_row(r) for r in rows)


def _context_pack_manifest_from_row(row: sqlite3.Row) -> V1ContextPackManifestRecord:
    return V1ContextPackManifestRecord(
        manifest_id=str(row["manifest_id"]),
        job_id=str(row["job_id"]) if row["job_id"] is not None else None,
        stage_run_id=str(row["stage_run_id"]) if row["stage_run_id"] is not None else None,
        pack_type=str(row["pack_type"]),
        pack_version=str(row["pack_version"]),
        title=str(row["title"]),
        description=str(row["description"]) if row["description"] is not None else None,
        evidence_refs_json=str(row["evidence_refs_json"]) if row["evidence_refs_json"] is not None else None,
        bounds_json=str(row["bounds_json"]) if row["bounds_json"] is not None else None,
        redaction_policy=str(row["redaction_policy"]) if row["redaction_policy"] is not None else None,
        redacted_summary=str(row["redacted_summary"]) if row["redacted_summary"] is not None else None,
        checksum_algorithm=str(row["checksum_algorithm"]),
        checksum=str(row["checksum"]),
        model_profile_id=str(row["model_profile_id"]) if row["model_profile_id"] is not None else None,
        model_name=str(row["model_name"]) if row["model_name"] is not None else None,
        token_count=int(row["token_count"]) if row["token_count"] is not None else None,
        created_at=str(row["created_at"]),
        created_by=str(row["created_by"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1PrivilegedActionRepository:
    """SQLite repository for v1_privileged_actions table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, action: V1PrivilegedActionRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_privileged_actions (
                    action_id, job_id, action_type, action_version,
                    parameters_json, parameters_checksum, policy_json,
                    policy_version, status, requested_by, requested_at,
                    approved_by, approved_at, rejected_by, rejected_reason,
                    executed_at, failure_reason, correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    action.action_id,
                    action.job_id,
                    action.action_type,
                    action.action_version,
                    action.parameters_json,
                    action.parameters_checksum,
                    action.policy_json,
                    action.policy_version,
                    action.status,
                    action.requested_by,
                    action.requested_at,
                    action.approved_by,
                    action.approved_at,
                    action.rejected_by,
                    action.rejected_reason,
                    action.executed_at,
                    action.failure_reason,
                    action.correlation_id,
                    action.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, action_id: str) -> V1PrivilegedActionRecord | None:
        row = self._connection.execute(
            """SELECT action_id, job_id, action_type, action_version,
                      parameters_json, parameters_checksum, policy_json,
                      policy_version, status, requested_by, requested_at,
                      approved_by, approved_at, rejected_by, rejected_reason,
                      executed_at, failure_reason, correlation_id, causation_id
               FROM v1_privileged_actions WHERE action_id = ?""",
            (action_id,),
        ).fetchone()
        if row is None:
            return None
        return _privileged_action_from_row(row)

    def list(self) -> tuple[V1PrivilegedActionRecord, ...]:
        rows = self._connection.execute(
            """SELECT action_id, job_id, action_type, action_version,
                      parameters_json, parameters_checksum, policy_json,
                      policy_version, status, requested_by, requested_at,
                      approved_by, approved_at, rejected_by, rejected_reason,
                      executed_at, failure_reason, correlation_id, causation_id
               FROM v1_privileged_actions ORDER BY requested_at DESC"""
        ).fetchall()
        return tuple(_privileged_action_from_row(r) for r in rows)

    def list_for_job(self, job_id: str) -> tuple[V1PrivilegedActionRecord, ...]:
        rows = self._connection.execute(
            """SELECT action_id, job_id, action_type, action_version,
                      parameters_json, parameters_checksum, policy_json,
                      policy_version, status, requested_by, requested_at,
                      approved_by, approved_at, rejected_by, rejected_reason,
                      executed_at, failure_reason, correlation_id, causation_id
               FROM v1_privileged_actions
               WHERE job_id = ? ORDER BY requested_at DESC""",
            (job_id,),
        ).fetchall()
        return tuple(_privileged_action_from_row(r) for r in rows)

    def list_by_status(self, status: str) -> tuple[V1PrivilegedActionRecord, ...]:
        rows = self._connection.execute(
            """SELECT action_id, job_id, action_type, action_version,
                      parameters_json, parameters_checksum, policy_json,
                      policy_version, status, requested_by, requested_at,
                      approved_by, approved_at, rejected_by, rejected_reason,
                      executed_at, failure_reason, correlation_id, causation_id
               FROM v1_privileged_actions
               WHERE status = ? ORDER BY requested_at DESC""",
            (status,),
        ).fetchall()
        return tuple(_privileged_action_from_row(r) for r in rows)


def _privileged_action_from_row(row: sqlite3.Row) -> V1PrivilegedActionRecord:
    return V1PrivilegedActionRecord(
        action_id=str(row["action_id"]),
        job_id=str(row["job_id"]),
        action_type=str(row["action_type"]),
        action_version=str(row["action_version"]),
        parameters_json=str(row["parameters_json"]),
        parameters_checksum=str(row["parameters_checksum"]),
        policy_json=str(row["policy_json"]) if row["policy_json"] is not None else None,
        policy_version=str(row["policy_version"]) if row["policy_version"] is not None else None,
        status=str(row["status"]),
        requested_by=str(row["requested_by"]),
        requested_at=str(row["requested_at"]),
        approved_by=str(row["approved_by"]) if row["approved_by"] is not None else None,
        approved_at=str(row["approved_at"]) if row["approved_at"] is not None else None,
        rejected_by=str(row["rejected_by"]) if row["rejected_by"] is not None else None,
        rejected_reason=str(row["rejected_reason"]) if row["rejected_reason"] is not None else None,
        executed_at=str(row["executed_at"]) if row["executed_at"] is not None else None,
        failure_reason=str(row["failure_reason"]) if row["failure_reason"] is not None else None,
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )

class SqliteV1PlanAmendmentRepository:
    """SQLite repository for v1_plan_amendments table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, amendment: V1PlanAmendmentRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_plan_amendments (
                    amendment_id, job_id, source_kind, title, summary,
                    payload_json, payload_checksum, redacted_summary_json,
                    created_at, created_by, correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    amendment.amendment_id,
                    amendment.job_id,
                    amendment.source_kind,
                    amendment.title,
                    amendment.summary,
                    amendment.payload_json,
                    amendment.payload_checksum,
                    amendment.redacted_summary_json,
                    amendment.created_at,
                    amendment.created_by,
                    amendment.correlation_id,
                    amendment.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, amendment_id: str) -> V1PlanAmendmentRecord | None:
        row = self._connection.execute(
            """SELECT amendment_id, job_id, source_kind, title, summary,
                      payload_json, payload_checksum, redacted_summary_json,
                      created_at, created_by, correlation_id, causation_id
               FROM v1_plan_amendments WHERE amendment_id = ?""",
            (amendment_id,),
        ).fetchone()
        return _plan_amendment_from_row(row) if row is not None else None

    def list_for_job(self, job_id: str) -> tuple[V1PlanAmendmentRecord, ...]:
        rows = self._connection.execute(
            """SELECT amendment_id, job_id, source_kind, title, summary,
                      payload_json, payload_checksum, redacted_summary_json,
                      created_at, created_by, correlation_id, causation_id
               FROM v1_plan_amendments
               WHERE job_id = ? ORDER BY created_at DESC""",
            (job_id,),
        ).fetchall()
        return tuple(_plan_amendment_from_row(row) for row in rows)


def _plan_amendment_from_row(row: sqlite3.Row) -> V1PlanAmendmentRecord:
    return V1PlanAmendmentRecord(
        amendment_id=str(row["amendment_id"]),
        job_id=str(row["job_id"]),
        source_kind=str(row["source_kind"]),
        title=str(row["title"]),
        summary=str(row["summary"]),
        payload_json=str(row["payload_json"]),
        payload_checksum=str(row["payload_checksum"]),
        redacted_summary_json=str(row["redacted_summary_json"]),
        created_at=str(row["created_at"]),
        created_by=str(row["created_by"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1PlanRevisionRepository:
    """SQLite repository for v1_plan_revisions table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, revision: V1PlanRevisionRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_plan_revisions (
                    revision_id, amendment_id, job_id, revision_order, revision_state,
                    source_kind, payload_json, payload_checksum, redacted_summary_json,
                    created_at, created_by, decided_at, decided_by,
                    correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    revision.revision_id,
                    revision.amendment_id,
                    revision.job_id,
                    revision.revision_order,
                    revision.revision_state,
                    revision.source_kind,
                    revision.payload_json,
                    revision.payload_checksum,
                    revision.redacted_summary_json,
                    revision.created_at,
                    revision.created_by,
                    revision.decided_at,
                    revision.decided_by,
                    revision.correlation_id,
                    revision.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, revision_id: str) -> V1PlanRevisionRecord | None:
        row = self._connection.execute(
            """SELECT revision_id, amendment_id, job_id, revision_order, revision_state,
                      source_kind, payload_json, payload_checksum, redacted_summary_json,
                      created_at, created_by, decided_at, decided_by,
                      correlation_id, causation_id
               FROM v1_plan_revisions WHERE revision_id = ?""",
            (revision_id,),
        ).fetchone()
        return _plan_revision_from_row(row) if row is not None else None

    def list_for_amendment(self, amendment_id: str) -> tuple[V1PlanRevisionRecord, ...]:
        rows = self._connection.execute(
            """SELECT revision_id, amendment_id, job_id, revision_order, revision_state,
                      source_kind, payload_json, payload_checksum, redacted_summary_json,
                      created_at, created_by, decided_at, decided_by,
                      correlation_id, causation_id
               FROM v1_plan_revisions
               WHERE amendment_id = ? ORDER BY revision_order ASC""",
            (amendment_id,),
        ).fetchall()
        return tuple(_plan_revision_from_row(row) for row in rows)

    def list_for_job(self, job_id: str) -> tuple[V1PlanRevisionRecord, ...]:
        rows = self._connection.execute(
            """SELECT revision_id, amendment_id, job_id, revision_order, revision_state,
                      source_kind, payload_json, payload_checksum, redacted_summary_json,
                      created_at, created_by, decided_at, decided_by,
                      correlation_id, causation_id
               FROM v1_plan_revisions
               WHERE job_id = ? ORDER BY created_at DESC""",
            (job_id,),
        ).fetchall()
        return tuple(_plan_revision_from_row(row) for row in rows)

    def next_revision_order(self, amendment_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(revision_order), 0) AS max_revision_order FROM v1_plan_revisions WHERE amendment_id = ?",
            (amendment_id,),
        ).fetchone()
        return int(row["max_revision_order"]) + 1 if row is not None else 1

    def has_terminal_revision(self, amendment_id: str) -> bool:
        row = self._connection.execute(
            """SELECT 1
               FROM v1_plan_revisions
               WHERE amendment_id = ?
                 AND revision_state IN ('accepted', 'finalized')
               LIMIT 1""",
            (amendment_id,),
        ).fetchone()
        return row is not None


def _plan_revision_from_row(row: sqlite3.Row) -> V1PlanRevisionRecord:
    return V1PlanRevisionRecord(
        revision_id=str(row["revision_id"]),
        amendment_id=str(row["amendment_id"]),
        job_id=str(row["job_id"]),
        revision_order=int(row["revision_order"]),
        revision_state=str(row["revision_state"]),
        source_kind=str(row["source_kind"]),
        payload_json=str(row["payload_json"]),
        payload_checksum=str(row["payload_checksum"]),
        redacted_summary_json=str(row["redacted_summary_json"]),
        created_at=str(row["created_at"]),
        created_by=str(row["created_by"]),
        decided_at=str(row["decided_at"]) if row["decided_at"] is not None else None,
        decided_by=str(row["decided_by"]) if row["decided_by"] is not None else None,
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1PlanReviewDecisionRepository:
    """SQLite repository for v1_plan_review_decisions table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, review_decision: V1PlanReviewDecisionRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_plan_review_decisions (
                    review_decision_id, revision_id, amendment_id, job_id,
                    decision, reviewed_checksum, review_summary,
                    actor_type, actor_id, created_at, correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    review_decision.review_decision_id,
                    review_decision.revision_id,
                    review_decision.amendment_id,
                    review_decision.job_id,
                    review_decision.decision,
                    review_decision.reviewed_checksum,
                    review_decision.review_summary,
                    review_decision.actor_type,
                    review_decision.actor_id,
                    review_decision.created_at,
                    review_decision.correlation_id,
                    review_decision.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get_for_revision(self, revision_id: str) -> V1PlanReviewDecisionRecord | None:
        row = self._connection.execute(
            """SELECT review_decision_id, revision_id, amendment_id, job_id,
                      decision, reviewed_checksum, review_summary,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_plan_review_decisions WHERE revision_id = ?""",
            (revision_id,),
        ).fetchone()
        return _plan_review_decision_from_row(row) if row is not None else None

    def list_for_job(self, job_id: str) -> tuple[V1PlanReviewDecisionRecord, ...]:
        rows = self._connection.execute(
            """SELECT review_decision_id, revision_id, amendment_id, job_id,
                      decision, reviewed_checksum, review_summary,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_plan_review_decisions
               WHERE job_id = ? ORDER BY created_at DESC""",
            (job_id,),
        ).fetchall()
        return tuple(_plan_review_decision_from_row(row) for row in rows)


def _plan_review_decision_from_row(row: sqlite3.Row) -> V1PlanReviewDecisionRecord:
    return V1PlanReviewDecisionRecord(
        review_decision_id=str(row["review_decision_id"]),
        revision_id=str(row["revision_id"]),
        amendment_id=str(row["amendment_id"]),
        job_id=str(row["job_id"]),
        decision=str(row["decision"]),
        reviewed_checksum=str(row["reviewed_checksum"]),
        review_summary=str(row["review_summary"]),
        actor_type=str(row["actor_type"]),
        actor_id=str(row["actor_id"]),
        created_at=str(row["created_at"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1RepairClassificationRepository:
    """SQLite repository for v1_repair_classifications table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, classification: V1RepairClassificationRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_repair_classifications (
                    classification_id, command_id, job_id, command_status,
                    evidence_kind, evidence_summary, evidence_checksum,
                    classification_code, reason_code, repairable, attempt_limit,
                    actor_type, actor_id, created_at, correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    classification.classification_id,
                    classification.command_id,
                    classification.job_id,
                    classification.command_status,
                    classification.evidence_kind,
                    classification.evidence_summary,
                    classification.evidence_checksum,
                    classification.classification_code,
                    classification.reason_code,
                    1 if classification.repairable else 0,
                    classification.attempt_limit,
                    classification.actor_type,
                    classification.actor_id,
                    classification.created_at,
                    classification.correlation_id,
                    classification.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get_by_command_and_checksum(
        self,
        command_id: str,
        evidence_checksum: str,
    ) -> V1RepairClassificationRecord | None:
        row = self._connection.execute(
            """SELECT classification_id, command_id, job_id, command_status,
                      evidence_kind, evidence_summary, evidence_checksum,
                      classification_code, reason_code, repairable, attempt_limit,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_repair_classifications
               WHERE command_id = ? AND evidence_checksum = ?""",
            (command_id, evidence_checksum),
        ).fetchone()
        return _repair_classification_from_row(row) if row is not None else None

    def get_latest_for_command(self, command_id: str) -> V1RepairClassificationRecord | None:
        row = self._connection.execute(
            """SELECT classification_id, command_id, job_id, command_status,
                      evidence_kind, evidence_summary, evidence_checksum,
                      classification_code, reason_code, repairable, attempt_limit,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_repair_classifications
               WHERE command_id = ?
               ORDER BY created_at DESC, classification_id DESC
               LIMIT 1""",
            (command_id,),
        ).fetchone()
        return _repair_classification_from_row(row) if row is not None else None

    def list_for_job(self, job_id: str) -> tuple[V1RepairClassificationRecord, ...]:
        rows = self._connection.execute(
            """SELECT classification_id, command_id, job_id, command_status,
                      evidence_kind, evidence_summary, evidence_checksum,
                      classification_code, reason_code, repairable, attempt_limit,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_repair_classifications
               WHERE job_id = ?
               ORDER BY created_at DESC, classification_id DESC""",
            (job_id,),
        ).fetchall()
        return tuple(_repair_classification_from_row(row) for row in rows)


def _repair_classification_from_row(row: sqlite3.Row) -> V1RepairClassificationRecord:
    return V1RepairClassificationRecord(
        classification_id=str(row["classification_id"]),
        command_id=str(row["command_id"]),
        job_id=str(row["job_id"]),
        command_status=str(row["command_status"]),
        evidence_kind=str(row["evidence_kind"]),
        evidence_summary=str(row["evidence_summary"]),
        evidence_checksum=str(row["evidence_checksum"]),
        classification_code=str(row["classification_code"]),
        reason_code=str(row["reason_code"]),
        repairable=bool(row["repairable"]),
        attempt_limit=int(row["attempt_limit"]),
        actor_type=str(row["actor_type"]),
        actor_id=str(row["actor_id"]),
        created_at=str(row["created_at"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1FakeRepairProposalRepository:
    """SQLite repository for v1_fake_repair_proposals table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, proposal: V1FakeRepairProposalRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_fake_repair_proposals (
                    proposal_id, classification_id, command_id, job_id,
                    proposal_order, proposal_kind, proposal_summary, proposal_checksum,
                    recommendation_type, confidence_label, confidence_score,
                    warning_codes_json, applicable, context_checksum,
                    actor_type, actor_id, created_at, correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal.proposal_id,
                    proposal.classification_id,
                    proposal.command_id,
                    proposal.job_id,
                    proposal.proposal_order,
                    proposal.proposal_kind,
                    proposal.proposal_summary,
                    proposal.proposal_checksum,
                    proposal.recommendation_type,
                    proposal.confidence_label,
                    proposal.confidence_score,
                    proposal.warning_codes_json,
                    1 if proposal.applicable else 0,
                    proposal.context_checksum,
                    proposal.actor_type,
                    proposal.actor_id,
                    proposal.created_at,
                    proposal.correlation_id,
                    proposal.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get_for_classification_and_checksum(
        self,
        classification_id: str,
        proposal_checksum: str,
    ) -> V1FakeRepairProposalRecord | None:
        row = self._connection.execute(
            """SELECT proposal_id, classification_id, command_id, job_id,
                      proposal_order, proposal_kind, proposal_summary, proposal_checksum,
                      recommendation_type, confidence_label, confidence_score,
                      warning_codes_json, applicable, context_checksum,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_fake_repair_proposals
               WHERE classification_id = ? AND proposal_checksum = ?""",
            (classification_id, proposal_checksum),
        ).fetchone()
        return _fake_repair_proposal_from_row(row) if row is not None else None

    def list_for_classification(
        self,
        classification_id: str,
    ) -> tuple[V1FakeRepairProposalRecord, ...]:
        rows = self._connection.execute(
            """SELECT proposal_id, classification_id, command_id, job_id,
                      proposal_order, proposal_kind, proposal_summary, proposal_checksum,
                      recommendation_type, confidence_label, confidence_score,
                      warning_codes_json, applicable, context_checksum,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_fake_repair_proposals
               WHERE classification_id = ?
               ORDER BY proposal_order, proposal_id""",
            (classification_id,),
        ).fetchall()
        return tuple(_fake_repair_proposal_from_row(row) for row in rows)

    def get_for_classification_kind_and_context(
        self,
        classification_id: str,
        proposal_kind: str,
        context_checksum: str,
    ) -> V1FakeRepairProposalRecord | None:
        row = self._connection.execute(
            """SELECT proposal_id, classification_id, command_id, job_id,
                      proposal_order, proposal_kind, proposal_summary, proposal_checksum,
                      recommendation_type, confidence_label, confidence_score,
                      warning_codes_json, applicable, context_checksum,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_fake_repair_proposals
               WHERE classification_id = ? AND proposal_kind = ? AND context_checksum = ?""",
            (classification_id, proposal_kind, context_checksum),
        ).fetchone()
        return _fake_repair_proposal_from_row(row) if row is not None else None


def _fake_repair_proposal_from_row(row: sqlite3.Row) -> V1FakeRepairProposalRecord:
    return V1FakeRepairProposalRecord(
        proposal_id=str(row["proposal_id"]),
        classification_id=str(row["classification_id"]),
        command_id=str(row["command_id"]),
        job_id=str(row["job_id"]),
        proposal_order=int(row["proposal_order"]),
        proposal_kind=str(row["proposal_kind"]),
        proposal_summary=str(row["proposal_summary"]),
        proposal_checksum=str(row["proposal_checksum"]),
        recommendation_type=str(row["recommendation_type"]) if row["recommendation_type"] is not None else None,
        confidence_label=str(row["confidence_label"]) if row["confidence_label"] is not None else None,
        confidence_score=float(row["confidence_score"]) if row["confidence_score"] is not None else None,
        warning_codes_json=str(row["warning_codes_json"]),
        applicable=bool(row["applicable"]),
        context_checksum=str(row["context_checksum"]) if row["context_checksum"] is not None else None,
        actor_type=str(row["actor_type"]),
        actor_id=str(row["actor_id"]),
        created_at=str(row["created_at"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1PrivilegedActionDecisionRepository:
    """SQLite repository for v1_privileged_action_decisions table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, decision: V1PrivilegedActionDecisionRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_privileged_action_decisions (
                    action_id, decision, decided_by, decided_at,
                    parameters_checksum, rejection_reason,
                    correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.action_id,
                    decision.decision,
                    decision.decided_by,
                    decision.decided_at,
                    decision.parameters_checksum,
                    decision.rejection_reason,
                    decision.correlation_id,
                    decision.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, action_id: str) -> V1PrivilegedActionDecisionRecord | None:
        row = self._connection.execute(
            """SELECT action_id, decision, decided_by, decided_at,
                      parameters_checksum, rejection_reason,
                      correlation_id, causation_id
               FROM v1_privileged_action_decisions WHERE action_id = ?""",
            (action_id,),
        ).fetchone()
        if row is None:
            return None
        return _decision_from_row(row)

    def list(self) -> tuple[V1PrivilegedActionDecisionRecord, ...]:
        rows = self._connection.execute(
            """SELECT action_id, decision, decided_by, decided_at,
                      parameters_checksum, rejection_reason,
                      correlation_id, causation_id
               FROM v1_privileged_action_decisions
               ORDER BY decided_at DESC"""
        ).fetchall()
        return tuple(_decision_from_row(r) for r in rows)

    def list_by_decision(self, decision: str) -> tuple[V1PrivilegedActionDecisionRecord, ...]:
        rows = self._connection.execute(
            """SELECT action_id, decision, decided_by, decided_at,
                      parameters_checksum, rejection_reason,
                      correlation_id, causation_id
               FROM v1_privileged_action_decisions
               WHERE decision = ? ORDER BY decided_at DESC""",
            (decision,),
        ).fetchall()
        return tuple(_decision_from_row(r) for r in rows)


def _decision_from_row(row: sqlite3.Row) -> V1PrivilegedActionDecisionRecord:
    return V1PrivilegedActionDecisionRecord(
        action_id=str(row["action_id"]),
        decision=str(row["decision"]),
        decided_by=str(row["decided_by"]),
        decided_at=str(row["decided_at"]),
        parameters_checksum=str(row["parameters_checksum"]),
        rejection_reason=str(row["rejection_reason"]) if row["rejection_reason"] is not None else None,
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1PrivilegedActionExecutionRepository:
    """SQLite repository for v1_privileged_action_executions table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, execution: V1PrivilegedActionExecutionRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_privileged_action_executions (
                    action_id, job_id, action_type, parameters_checksum,
                    status, started_at, completed_at, result_summary,
                    failure_reason, executed_by, execution_version,
                    correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    execution.action_id,
                    execution.job_id,
                    execution.action_type,
                    execution.parameters_checksum,
                    execution.status,
                    execution.started_at,
                    execution.completed_at,
                    execution.result_summary,
                    execution.failure_reason,
                    execution.executed_by,
                    execution.execution_version,
                    execution.correlation_id,
                    execution.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, action_id: str) -> V1PrivilegedActionExecutionRecord | None:
        row = self._connection.execute(
            """SELECT action_id, job_id, action_type, parameters_checksum,
                      status, started_at, completed_at, result_summary,
                      failure_reason, executed_by, execution_version,
                      correlation_id, causation_id
               FROM v1_privileged_action_executions WHERE action_id = ?""",
            (action_id,),
        ).fetchone()
        if row is None:
            return None
        return _execution_from_row(row)

    def list(self) -> tuple[V1PrivilegedActionExecutionRecord, ...]:
        rows = self._connection.execute(
            """SELECT action_id, job_id, action_type, parameters_checksum,
                      status, started_at, completed_at, result_summary,
                      failure_reason, executed_by, execution_version,
                      correlation_id, causation_id
               FROM v1_privileged_action_executions
               ORDER BY started_at DESC"""
        ).fetchall()
        return tuple(_execution_from_row(r) for r in rows)

    def list_by_status(self, status: str) -> tuple[V1PrivilegedActionExecutionRecord, ...]:
        rows = self._connection.execute(
            """SELECT action_id, job_id, action_type, parameters_checksum,
                      status, started_at, completed_at, result_summary,
                      failure_reason, executed_by, execution_version,
                      correlation_id, causation_id
               FROM v1_privileged_action_executions
               WHERE status = ? ORDER BY started_at DESC""",
            (status,),
        ).fetchall()
        return tuple(_execution_from_row(r) for r in rows)


def _execution_from_row(row: sqlite3.Row) -> V1PrivilegedActionExecutionRecord:
    return V1PrivilegedActionExecutionRecord(
        action_id=str(row["action_id"]),
        job_id=str(row["job_id"]),
        action_type=str(row["action_type"]),
        parameters_checksum=str(row["parameters_checksum"]),
        status=str(row["status"]),
        started_at=str(row["started_at"]),
        completed_at=str(row["completed_at"]) if row["completed_at"] is not None else None,
        result_summary=str(row["result_summary"]) if row["result_summary"] is not None else None,
        failure_reason=str(row["failure_reason"]) if row["failure_reason"] is not None else None,
        executed_by=str(row["executed_by"]),
        execution_version=str(row["execution_version"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1PatchPolicyValidationRepository:
    """SQLite repository for v1_patch_policy_validations table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, validation: V1PatchPolicyValidationRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_patch_policy_validations (
                    validation_id, command_id, job_id, approved,
                    validation_code, reason_code, target_path_hash,
                    patch_size_bytes, metacharacter_hits, policy_version,
                    actor_type, actor_id, created_at, correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    validation.validation_id,
                    validation.command_id,
                    validation.job_id,
                    1 if validation.approved else 0,
                    validation.validation_code,
                    validation.reason_code,
                    validation.target_path_hash,
                    validation.patch_size_bytes,
                    validation.metacharacter_hits,
                    validation.policy_version,
                    validation.actor_type,
                    validation.actor_id,
                    validation.created_at,
                    validation.correlation_id,
                    validation.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, validation_id: str) -> V1PatchPolicyValidationRecord | None:
        row = self._connection.execute(
            """SELECT validation_id, command_id, job_id, approved,
                      validation_code, reason_code, target_path_hash,
                      patch_size_bytes, metacharacter_hits, policy_version,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_patch_policy_validations WHERE validation_id = ?""",
            (validation_id,),
        ).fetchone()
        return _patch_validation_from_row(row) if row is not None else None

    def list_for_command(self, command_id: str) -> tuple[V1PatchPolicyValidationRecord, ...]:
        rows = self._connection.execute(
            """SELECT validation_id, command_id, job_id, approved,
                      validation_code, reason_code, target_path_hash,
                      patch_size_bytes, metacharacter_hits, policy_version,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_patch_policy_validations
               WHERE command_id = ?
               ORDER BY created_at DESC, validation_id DESC""",
            (command_id,),
        ).fetchall()
        return tuple(_patch_validation_from_row(row) for row in rows)

    def get_latest_for_command(self, command_id: str) -> V1PatchPolicyValidationRecord | None:
        row = self._connection.execute(
            """SELECT validation_id, command_id, job_id, approved,
                      validation_code, reason_code, target_path_hash,
                      patch_size_bytes, metacharacter_hits, policy_version,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_patch_policy_validations
               WHERE command_id = ?
               ORDER BY created_at DESC, validation_id DESC
               LIMIT 1""",
            (command_id,),
        ).fetchone()
        return _patch_validation_from_row(row) if row is not None else None


def _patch_validation_from_row(row: sqlite3.Row) -> V1PatchPolicyValidationRecord:
    return V1PatchPolicyValidationRecord(
        validation_id=str(row["validation_id"]),
        command_id=str(row["command_id"]),
        job_id=str(row["job_id"]),
        approved=bool(row["approved"]),
        validation_code=str(row["validation_code"]),
        reason_code=str(row["reason_code"]),
        target_path_hash=str(row["target_path_hash"]),
        patch_size_bytes=int(row["patch_size_bytes"]),
        metacharacter_hits=int(row["metacharacter_hits"]),
        policy_version=str(row["policy_version"]),
        actor_type=str(row["actor_type"]),
        actor_id=str(row["actor_id"]),
        created_at=str(row["created_at"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1SandboxSnapshotRepository:
    """SQLite repository for v1_sandbox_snapshots table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, snapshot: V1SandboxSnapshotRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_sandbox_snapshots (
                    snapshot_id, command_id, job_id, stage_index,
                    sandbox_artifact_id, sandbox_checksum,
                    actor_type, actor_id, created_at, correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.snapshot_id,
                    snapshot.command_id,
                    snapshot.job_id,
                    snapshot.stage_index,
                    snapshot.sandbox_artifact_id,
                    snapshot.sandbox_checksum,
                    snapshot.actor_type,
                    snapshot.actor_id,
                    snapshot.created_at,
                    snapshot.correlation_id,
                    snapshot.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, snapshot_id: str) -> V1SandboxSnapshotRecord | None:
        row = self._connection.execute(
            """SELECT snapshot_id, command_id, job_id, stage_index,
                      sandbox_artifact_id, sandbox_checksum,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_sandbox_snapshots WHERE snapshot_id = ?""",
            (snapshot_id,),
        ).fetchone()
        return _sandbox_snapshot_from_row(row) if row is not None else None

    def get_for_command(self, command_id: str) -> V1SandboxSnapshotRecord | None:
        row = self._connection.execute(
            """SELECT snapshot_id, command_id, job_id, stage_index,
                      sandbox_artifact_id, sandbox_checksum,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_sandbox_snapshots
               WHERE command_id = ?
               ORDER BY created_at DESC, snapshot_id DESC
               LIMIT 1""",
            (command_id,),
        ).fetchone()
        return _sandbox_snapshot_from_row(row) if row is not None else None

    def list_for_job(self, job_id: str) -> tuple[V1SandboxSnapshotRecord, ...]:
        rows = self._connection.execute(
            """SELECT snapshot_id, command_id, job_id, stage_index,
                      sandbox_artifact_id, sandbox_checksum,
                      actor_type, actor_id, created_at, correlation_id, causation_id
               FROM v1_sandbox_snapshots
               WHERE job_id = ?
               ORDER BY created_at DESC, snapshot_id DESC""",
            (job_id,),
        ).fetchall()
        return tuple(_sandbox_snapshot_from_row(row) for row in rows)


def _sandbox_snapshot_from_row(row: sqlite3.Row) -> V1SandboxSnapshotRecord:
    return V1SandboxSnapshotRecord(
        snapshot_id=str(row["snapshot_id"]),
        command_id=str(row["command_id"]),
        job_id=str(row["job_id"]),
        stage_index=int(row["stage_index"]),
        sandbox_artifact_id=str(row["sandbox_artifact_id"]),
        sandbox_checksum=str(row["sandbox_checksum"]),
        actor_type=str(row["actor_type"]),
        actor_id=str(row["actor_id"]),
        created_at=str(row["created_at"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1PatchApplicationRepository:
    """SQLite repository for v1_patch_applications table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, application: V1PatchApplicationRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_patch_applications (
                    application_id, command_id, job_id, validation_id,
                    snapshot_id, stage_index, target_path_hash,
                    patch_size_bytes, applied_by, applied_at, status,
                    correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    application.application_id,
                    application.command_id,
                    application.job_id,
                    application.validation_id,
                    application.snapshot_id,
                    application.stage_index,
                    application.target_path_hash,
                    application.patch_size_bytes,
                    application.applied_by,
                    application.applied_at,
                    application.status,
                    application.correlation_id,
                    application.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, application_id: str) -> V1PatchApplicationRecord | None:
        row = self._connection.execute(
            """SELECT application_id, command_id, job_id, validation_id,
                      snapshot_id, stage_index, target_path_hash,
                      patch_size_bytes, applied_by, applied_at, status,
                      correlation_id, causation_id
               FROM v1_patch_applications WHERE application_id = ?""",
            (application_id,),
        ).fetchone()
        return _patch_application_from_row(row) if row is not None else None

    def get_for_command(self, command_id: str) -> V1PatchApplicationRecord | None:
        row = self._connection.execute(
            """SELECT application_id, command_id, job_id, validation_id,
                      snapshot_id, stage_index, target_path_hash,
                      patch_size_bytes, applied_by, applied_at, status,
                      correlation_id, causation_id
               FROM v1_patch_applications
               WHERE command_id = ?
               ORDER BY applied_at DESC, application_id DESC
               LIMIT 1""",
            (command_id,),
        ).fetchone()
        return _patch_application_from_row(row) if row is not None else None

    def list_for_job(self, job_id: str) -> tuple[V1PatchApplicationRecord, ...]:
        rows = self._connection.execute(
            """SELECT application_id, command_id, job_id, validation_id,
                      snapshot_id, stage_index, target_path_hash,
                      patch_size_bytes, applied_by, applied_at, status,
                      correlation_id, causation_id
               FROM v1_patch_applications
               WHERE job_id = ?
               ORDER BY applied_at DESC, application_id DESC""",
            (job_id,),
        ).fetchall()
        return tuple(_patch_application_from_row(row) for row in rows)


def _patch_application_from_row(row: sqlite3.Row) -> V1PatchApplicationRecord:
    return V1PatchApplicationRecord(
        application_id=str(row["application_id"]),
        command_id=str(row["command_id"]),
        job_id=str(row["job_id"]),
        validation_id=str(row["validation_id"]),
        snapshot_id=str(row["snapshot_id"]),
        stage_index=int(row["stage_index"]),
        target_path_hash=str(row["target_path_hash"]),
        patch_size_bytes=int(row["patch_size_bytes"]),
        applied_by=str(row["applied_by"]),
        applied_at=str(row["applied_at"]),
        status=str(row["status"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1PatchMavenValidationRepository:
    """SQLite repository for v1_patch_maven_validations table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, validation: V1PatchMavenValidationRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_patch_maven_validations (
                    maven_validation_id, application_id, command_id, job_id,
                    maven_goal, passed, result_summary,
                    actor_type, actor_id, created_at,
                    correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    validation.maven_validation_id,
                    validation.application_id,
                    validation.command_id,
                    validation.job_id,
                    validation.maven_goal,
                    1 if validation.passed else 0,
                    validation.result_summary,
                    validation.actor_type,
                    validation.actor_id,
                    validation.created_at,
                    validation.correlation_id,
                    validation.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, maven_validation_id: str) -> V1PatchMavenValidationRecord | None:
        row = self._connection.execute(
            """SELECT maven_validation_id, application_id, command_id, job_id,
                      maven_goal, passed, result_summary,
                      actor_type, actor_id, created_at,
                      correlation_id, causation_id
               FROM v1_patch_maven_validations WHERE maven_validation_id = ?""",
            (maven_validation_id,),
        ).fetchone()
        return _maven_validation_from_row(row) if row is not None else None

    def get_for_application(self, application_id: str) -> V1PatchMavenValidationRecord | None:
        row = self._connection.execute(
            """SELECT maven_validation_id, application_id, command_id, job_id,
                      maven_goal, passed, result_summary,
                      actor_type, actor_id, created_at,
                      correlation_id, causation_id
               FROM v1_patch_maven_validations
               WHERE application_id = ?
               ORDER BY created_at DESC, maven_validation_id DESC
               LIMIT 1""",
            (application_id,),
        ).fetchone()
        return _maven_validation_from_row(row) if row is not None else None

    def list_for_job(self, job_id: str) -> tuple[V1PatchMavenValidationRecord, ...]:
        rows = self._connection.execute(
            """SELECT maven_validation_id, application_id, command_id, job_id,
                      maven_goal, passed, result_summary,
                      actor_type, actor_id, created_at,
                      correlation_id, causation_id
               FROM v1_patch_maven_validations
               WHERE job_id = ?
               ORDER BY created_at DESC, maven_validation_id DESC""",
            (job_id,),
        ).fetchall()
        return tuple(_maven_validation_from_row(row) for row in rows)


def _maven_validation_from_row(row: sqlite3.Row) -> V1PatchMavenValidationRecord:
    return V1PatchMavenValidationRecord(
        maven_validation_id=str(row["maven_validation_id"]),
        application_id=str(row["application_id"]),
        command_id=str(row["command_id"]),
        job_id=str(row["job_id"]),
        maven_goal=str(row["maven_goal"]),
        passed=bool(row["passed"]),
        result_summary=str(row["result_summary"]),
        actor_type=str(row["actor_type"]),
        actor_id=str(row["actor_id"]),
        created_at=str(row["created_at"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1PatchRollbackRepository:
    """SQLite repository for v1_patch_rollbacks table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, rollback: V1PatchRollbackRecord) -> None:
        try:
            self._connection.execute(
                """INSERT INTO v1_patch_rollbacks (
                    rollback_id, command_id, job_id, application_id,
                    snapshot_id, maven_validation_id, stage_index, target_path_hash,
                    rolled_back_by, rolled_back_at, reason_code, redacted_summary,
                    correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rollback.rollback_id,
                    rollback.command_id,
                    rollback.job_id,
                    rollback.application_id,
                    rollback.snapshot_id,
                    rollback.maven_validation_id,
                    rollback.stage_index,
                    rollback.target_path_hash,
                    rollback.rolled_back_by,
                    rollback.rolled_back_at,
                    rollback.reason_code,
                    rollback.redacted_summary,
                    rollback.correlation_id,
                    rollback.causation_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageIntegrityError(str(exc)) from exc

    def get(self, rollback_id: str) -> V1PatchRollbackRecord | None:
        row = self._connection.execute(
            """SELECT rollback_id, command_id, job_id, application_id,
                      snapshot_id, maven_validation_id, stage_index, target_path_hash,
                      rolled_back_by, rolled_back_at, reason_code, redacted_summary,
                      correlation_id, causation_id
               FROM v1_patch_rollbacks WHERE rollback_id = ?""",
            (rollback_id,),
        ).fetchone()
        return _patch_rollback_from_row(row) if row is not None else None

    def get_for_command(self, command_id: str) -> V1PatchRollbackRecord | None:
        row = self._connection.execute(
            """SELECT rollback_id, command_id, job_id, application_id,
                      snapshot_id, maven_validation_id, stage_index, target_path_hash,
                      rolled_back_by, rolled_back_at, reason_code, redacted_summary,
                      correlation_id, causation_id
               FROM v1_patch_rollbacks
               WHERE command_id = ?
               ORDER BY rolled_back_at DESC, rollback_id DESC
               LIMIT 1""",
            (command_id,),
        ).fetchone()
        return _patch_rollback_from_row(row) if row is not None else None

    def get_for_application(self, application_id: str) -> V1PatchRollbackRecord | None:
        row = self._connection.execute(
            """SELECT rollback_id, command_id, job_id, application_id,
                      snapshot_id, maven_validation_id, stage_index, target_path_hash,
                      rolled_back_by, rolled_back_at, reason_code, redacted_summary,
                      correlation_id, causation_id
               FROM v1_patch_rollbacks
               WHERE application_id = ?
               ORDER BY rolled_back_at DESC, rollback_id DESC
               LIMIT 1""",
            (application_id,),
        ).fetchone()
        return _patch_rollback_from_row(row) if row is not None else None

    def list_for_job(self, job_id: str) -> tuple[V1PatchRollbackRecord, ...]:
        rows = self._connection.execute(
            """SELECT rollback_id, command_id, job_id, application_id,
                      snapshot_id, maven_validation_id, stage_index, target_path_hash,
                      rolled_back_by, rolled_back_at, reason_code, redacted_summary,
                      correlation_id, causation_id
               FROM v1_patch_rollbacks
               WHERE job_id = ?
               ORDER BY rolled_back_at DESC, rollback_id DESC""",
            (job_id,),
        ).fetchall()
        return tuple(_patch_rollback_from_row(row) for row in rows)


def _patch_rollback_from_row(row: sqlite3.Row) -> V1PatchRollbackRecord:
    return V1PatchRollbackRecord(
        rollback_id=str(row["rollback_id"]),
        command_id=str(row["command_id"]),
        job_id=str(row["job_id"]),
        application_id=str(row["application_id"]),
        snapshot_id=str(row["snapshot_id"]),
        maven_validation_id=str(row["maven_validation_id"]),
        stage_index=int(row["stage_index"]),
        target_path_hash=str(row["target_path_hash"]),
        rolled_back_by=str(row["rolled_back_by"]),
        rolled_back_at=str(row["rolled_back_at"]),
        reason_code=str(row["reason_code"]),
        redacted_summary=str(row["redacted_summary"]),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        causation_id=str(row["causation_id"]) if row["causation_id"] is not None else None,
    )


class SqliteV1ProofReportRepository:
    """SQLite repository for v1_proof_reports table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, report: V1ProofReportRecord) -> None:
        self._connection.execute(
            """INSERT INTO v1_proof_reports (
                report_id, job_id, report_version, report_checksum,
                gate_count, all_gates_present, proof_complete,
                target_proof_level, pipeline_id, stage_count,
                summary_json, generated_at, generated_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report.report_id,
                report.job_id,
                report.report_version,
                report.report_checksum,
                report.gate_count,
                report.all_gates_present,
                report.proof_complete,
                report.target_proof_level,
                report.pipeline_id,
                report.stage_count,
                report.summary_json,
                report.generated_at,
                report.generated_by,
            ),
        )

    def get(self, report_id: str) -> V1ProofReportRecord | None:
        row = self._connection.execute(
            """SELECT report_id, job_id, report_version, report_checksum,
                      gate_count, all_gates_present, proof_complete,
                      target_proof_level, pipeline_id, stage_count,
                      summary_json, generated_at, generated_by
               FROM v1_proof_reports WHERE report_id = ?""",
            (report_id,),
        ).fetchone()
        return _proof_report_from_row(row) if row is not None else None

    def get_latest_for_job(self, job_id: str) -> V1ProofReportRecord | None:
        row = self._connection.execute(
            """SELECT report_id, job_id, report_version, report_checksum,
                      gate_count, all_gates_present, proof_complete,
                      target_proof_level, pipeline_id, stage_count,
                      summary_json, generated_at, generated_by
               FROM v1_proof_reports
               WHERE job_id = ?
               ORDER BY generated_at DESC, report_id DESC
               LIMIT 1""",
            (job_id,),
        ).fetchone()
        return _proof_report_from_row(row) if row is not None else None

    def list_for_job(self, job_id: str) -> tuple[V1ProofReportRecord, ...]:
        rows = self._connection.execute(
            """SELECT report_id, job_id, report_version, report_checksum,
                      gate_count, all_gates_present, proof_complete,
                      target_proof_level, pipeline_id, stage_count,
                      summary_json, generated_at, generated_by
               FROM v1_proof_reports
               WHERE job_id = ?
               ORDER BY generated_at DESC, report_id DESC""",
            (job_id,),
        ).fetchall()
        return tuple(_proof_report_from_row(row) for row in rows)


class SqliteV1ProofReportGateRepository:
    """SQLite repository for v1_proof_report_gates table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, gate: V1ProofReportGateRecord) -> None:
        self._connection.execute(
            """INSERT INTO v1_proof_report_gates (
                report_gate_id, report_id, job_id, stage_index,
                output_checksum, proof_gate_checksum, chain_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                gate.report_gate_id,
                gate.report_id,
                gate.job_id,
                gate.stage_index,
                gate.output_checksum,
                gate.proof_gate_checksum,
                gate.chain_status,
            ),
        )

    def list_for_report(self, report_id: str) -> tuple[V1ProofReportGateRecord, ...]:
        rows = self._connection.execute(
            """SELECT report_gate_id, report_id, job_id, stage_index,
                      output_checksum, proof_gate_checksum, chain_status
               FROM v1_proof_report_gates
               WHERE report_id = ?
               ORDER BY stage_index ASC""",
            (report_id,),
        ).fetchall()
        return tuple(_proof_report_gate_from_row(row) for row in rows)

    def list_for_job(self, job_id: str) -> tuple[V1ProofReportGateRecord, ...]:
        rows = self._connection.execute(
            """SELECT report_gate_id, report_id, job_id, stage_index,
                      output_checksum, proof_gate_checksum, chain_status
               FROM v1_proof_report_gates
               WHERE job_id = ?
               ORDER BY stage_index ASC""",
            (job_id,),
        ).fetchall()
        return tuple(_proof_report_gate_from_row(row) for row in rows)


def _proof_report_from_row(row: sqlite3.Row) -> V1ProofReportRecord:
    return V1ProofReportRecord(
        report_id=str(row["report_id"]),
        job_id=str(row["job_id"]),
        report_version=int(row["report_version"]),
        report_checksum=str(row["report_checksum"]),
        gate_count=int(row["gate_count"]),
        all_gates_present=int(row["all_gates_present"]),
        proof_complete=int(row["proof_complete"]),
        target_proof_level=str(row["target_proof_level"]),
        pipeline_id=str(row["pipeline_id"]),
        stage_count=int(row["stage_count"]),
        summary_json=str(row["summary_json"]),
        generated_at=str(row["generated_at"]),
        generated_by=str(row["generated_by"]),
    )


def _proof_report_gate_from_row(row: sqlite3.Row) -> V1ProofReportGateRecord:
    return V1ProofReportGateRecord(
        report_gate_id=str(row["report_gate_id"]),
        report_id=str(row["report_id"]),
        job_id=str(row["job_id"]),
        stage_index=int(row["stage_index"]),
        output_checksum=str(row["output_checksum"]),
        proof_gate_checksum=str(row["proof_gate_checksum"]),
        chain_status=str(row["chain_status"]),
    )
