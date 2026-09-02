from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.commands import (
    CreateMigrationJobCommand,
    RegisterArtifactCommand,
    RegisterPipelineDefinitionCommand,
    RegisterRunnerProfileCommand,
    TransitionJobStateCommand,
)
from migration_factory.control_tower.application.dto import AuditRecordDto
from migration_factory.control_tower.application.services import (
    ArtifactRegistryService,
    ControlTowerRegistrationService,
    CreateMigrationJobService,
)
from migration_factory.control_tower.domain.errors import (
    ExpectedVersionRequiredError,
    InvalidJobStateTransitionError,
    StaleVersionError,
)
from migration_factory.control_tower.domain.states import JobState, TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.artifact_paths import hash_registered_artifact
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.repositories import (
    SqliteAuditRecordRepository,
    SqliteMigrationJobRepository,
    SqlitePipelineDefinitionRepository,
    SqliteRunConfigurationRepository,
    SqliteRunnerProfileRepository,
    SqliteRunEventRepository,
    SqliteStageRunRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.schemas.run_configuration import RunPolicy
from tests.control_tower.transition_helpers import (
    count_audit_records,
    count_run_events,
    fetch_job,
    seed_job,
)
from tests.control_tower._helpers import pipeline_definition_payload, runner_profile_payload
from tests.control_tower._helpers import (
    artifact_roots,
    make_migrated_connection,
    seed_pipeline_definition,
    seed_runner_profile_with_roots,
)


def test_global_audit_records_allow_null_job_id(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        service.register_runner_profile(_runner_command())

        row = connection.execute("SELECT job_id FROM audit_records").fetchone()
        assert row["job_id"] is None
    finally:
        connection.close()


def test_registration_and_audit_commit_atomically(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        service.register_runner_profile(_runner_command())

        assert _count(connection, "runner_profiles") == 1
        assert _count(connection, "audit_records") == 1
    finally:
        connection.close()


def test_audit_failure_rolls_back_runner_profile_registration(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = ControlTowerRegistrationService(lambda: _FailingAuditUnitOfWork(connection))

        with pytest.raises(RuntimeError, match="audit failed"):
            service.register_runner_profile(_runner_command())

        assert _count(connection, "runner_profiles") == 0
        assert _count(connection, "audit_records") == 0
    finally:
        connection.close()


def test_audit_failure_rolls_back_pipeline_definition_registration(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = ControlTowerRegistrationService(lambda: _FailingAuditUnitOfWork(connection))

        with pytest.raises(RuntimeError, match="audit failed"):
            service.register_pipeline_definition(_pipeline_command())

        assert _count(connection, "pipeline_definitions") == 0
        assert _count(connection, "audit_records") == 0
    finally:
        connection.close()


def test_audit_payload_json_is_valid_json(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        service.register_runner_profile(_runner_command())

        payload = json.loads(str(_audit_row(connection)["payload_json"]))
        assert payload["registration_type"] == "runner_profile"
    finally:
        connection.close()


def test_audit_payload_includes_registration_and_actor_context(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        registered = service.register_pipeline_definition(
            _pipeline_command(correlation_id="corr-1", causation_id="cause-1")
        )

        audit = _audit_row(connection)
        payload = json.loads(str(audit["payload_json"]))
        assert payload["id"] == registered.pipeline_id
        assert payload["version"] == registered.pipeline_version
        assert payload["checksum"] == registered.payload_checksum
        assert payload["actor_type"] == "user"
        assert payload["actor_id"] == "tester"
        assert payload["correlation_id"] == "corr-1"
        assert payload["causation_id"] == "cause-1"
        assert payload["action"] == "pipeline_definition_registered"
        assert audit["action"] == "pipeline_definition_registered"
    finally:
        connection.close()


def test_audit_records_are_append_only(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        service.register_runner_profile(_runner_command())
        audit_id = str(_audit_row(connection)["audit_id"])

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE audit_records SET actor_id = ? WHERE audit_id = ?", ("other", audit_id))

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM audit_records WHERE audit_id = ?", (audit_id,))
    finally:
        connection.close()


def test_audit_query_helpers_return_dtos_and_scalars_not_sqlite_rows(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        service.register_runner_profile(_runner_command())
        with SqliteUnitOfWork(connection) as uow:
            audits = uow.audit_records.list()
            count = uow.audit_records.count()

        assert count == 1
        assert isinstance(audits[0], AuditRecordDto)
        assert not isinstance(audits[0], sqlite3.Row)
    finally:
        connection.close()


def test_artifact_registration_creates_job_audit_record(tmp_path: Path) -> None:
    db_path, roots, job_id = _job_with_artifact_roots(tmp_path)
    artifact = _write_and_hash(roots, "reports/audit.txt", b"audit")

    registered = _artifact_service_for(db_path).register_artifact(
        _artifact_command(job_id, artifact)
    )

    with connect_control_tower(db_path) as connection:
        audit = connection.execute(
            """
            SELECT action, actor_type, actor_id, prior_state, new_state, job_version,
                   correlation_id, causation_id, payload_json
            FROM audit_records
            WHERE job_id = ? AND action = ?
            """,
            (job_id, "artifact_registered"),
        ).fetchone()
        event = connection.execute(
            """
            SELECT event_id, event_type
            FROM run_events
            WHERE job_id = ? AND event_type = ?
            """,
            (job_id, "artifact_registered"),
        ).fetchone()

    payload = json.loads(str(audit["payload_json"]))
    assert audit["action"] == "artifact_registered"
    assert audit["actor_type"] == "user"
    assert audit["actor_id"] == "tester"
    assert audit["prior_state"] is None
    assert audit["new_state"] is None
    assert audit["job_version"] == 1
    assert audit["correlation_id"] == "corr-artifact"
    assert audit["causation_id"] == event["event_id"]
    assert event["event_type"] == "artifact_registered"
    assert payload["artifact_id"] == registered.artifact_id
    assert payload["checksum"] == artifact.checksum
    assert payload["normalized_relative_path"] == "reports/audit.txt"


def test_audit_failure_rolls_back_artifact_registration(tmp_path: Path) -> None:
    db_path, roots, job_id = _job_with_artifact_roots(tmp_path)
    artifact = _write_and_hash(roots, "reports/audit-failure.txt", b"audit")
    service = ArtifactRegistryService(
        lambda: _FailingArtifactAuditUnitOfWork(
            connect_control_tower(db_path),
            close_connection=True,
        )
    )

    with pytest.raises(RuntimeError, match="audit failed"):
        service.register_artifact(_artifact_command(job_id, artifact))

    with connect_control_tower(db_path) as connection:
        assert _count(connection, "artifacts") == 0
        assert _artifact_event_count(connection, job_id) == 0
        assert _job_sequence(connection, job_id) == 1


def test_transition_creates_one_job_scoped_audit_record(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED)

        _service(connection).transition_job_state(_transition_command(JobState.QUEUED))

        with SqliteUnitOfWork(connection) as uow:
            audits = uow.audit_records.list_for_job("job-1")
            count = uow.audit_records.count_for_job("job-1")

        assert count == 1
        assert len(audits) == 1
        assert audits[0].job_id == "job-1"
        assert audits[0].action == "job_state_changed"
    finally:
        connection.close()


def test_transition_audit_records_states_version_actor_and_reason(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.RUNNING, version=3)

        _service(connection).transition_job_state(
            _transition_command(JobState.COMPLETED, expected_version=3)
        )

        audit = _transition_audit(connection)
        assert audit.prior_state == "RUNNING"
        assert audit.new_state == "COMPLETED"
        assert audit.job_version == 4
        assert audit.payload["prior_state"] == "RUNNING"
        assert audit.payload["new_state"] == "COMPLETED"
        assert audit.payload["prior_version"] == 3
        assert audit.payload["new_version"] == 4
        assert audit.payload["event_sequence"] == 1
        assert audit.payload["actor_type"] == "user"
        assert audit.payload["actor_id"] == "tester"
        assert audit.payload["reason"] == "advance lifecycle"
        assert audit.payload["correlation_id"] == "corr-1"
        assert audit.payload["causation_id"] == "cause-1"
    finally:
        connection.close()


def test_audit_failure_rolls_back_job_state_update_and_run_event_insert(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED)
        service = ControlTowerRegistrationService(lambda: _FailingAuditUnitOfWork(connection))

        with pytest.raises(RuntimeError, match="audit failed"):
            service.transition_job_state(_transition_command(JobState.QUEUED))

        row = fetch_job(connection)
        assert row["status"] == "CREATED"
        assert row["version"] == 1
        assert row["active_slot"] == 1
        assert row["last_event_sequence"] == 0
        assert count_run_events(connection) == 0
        assert count_audit_records(connection) == 0
    finally:
        connection.close()


def test_no_audit_is_created_for_invalid_transition(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED)

        with pytest.raises(InvalidJobStateTransitionError):
            _service(connection).transition_job_state(_transition_command(JobState.RUNNING))

        assert count_audit_records(connection) == 0
    finally:
        connection.close()


def test_no_audit_is_created_for_stale_version(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED, version=2)

        with pytest.raises(StaleVersionError):
            _service(connection).transition_job_state(
                _transition_command(JobState.QUEUED, expected_version=1)
            )

        assert count_audit_records(connection) == 0
    finally:
        connection.close()


def test_no_audit_is_created_for_missing_expected_version(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED)

        with pytest.raises(ExpectedVersionRequiredError):
            _service(connection).transition_job_state(
                _transition_command(JobState.QUEUED, expected_version=None)
            )

        assert count_audit_records(connection) == 0
    finally:
        connection.close()
class _FailingAuditRepository:
    def append_global_audit(self, **kwargs) -> None:
        raise RuntimeError("audit failed")

    def append_job_state_changed_audit(self, **kwargs) -> None:
        raise RuntimeError("audit failed")

    def list(self) -> tuple[AuditRecordDto, ...]:
        return ()

    def count(self) -> int:
        return 0


class _FailingAuditUnitOfWork:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self.runner_profiles = SqliteRunnerProfileRepository(connection)
        self.pipeline_definitions = SqlitePipelineDefinitionRepository(connection)
        self.migration_jobs = SqliteMigrationJobRepository(connection)
        self.run_configurations = SqliteRunConfigurationRepository(connection)
        self.stage_runs = SqliteStageRunRepository(connection)
        self.run_events = SqliteRunEventRepository(connection)
        self.audit_records = _FailingAuditRepository()

    def __enter__(self) -> "_FailingAuditUnitOfWork":
        self._connection.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self._connection.execute("COMMIT")
            return
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")


class _FailingArtifactAuditRepository:
    def insert(self, audit_record) -> None:
        raise RuntimeError("audit failed")

    def append_global_audit(self, **kwargs) -> None:
        raise RuntimeError("audit failed")

    def list(self) -> tuple[AuditRecordDto, ...]:
        return ()

    def count(self) -> int:
        return 0


class _FailingArtifactAuditUnitOfWork(SqliteUnitOfWork):
    def __enter__(self) -> "_FailingArtifactAuditUnitOfWork":
        super().__enter__()
        self.audit_records = _FailingArtifactAuditRepository()
        return self


def _service(connection: sqlite3.Connection) -> ControlTowerRegistrationService:
    return ControlTowerRegistrationService(lambda: SqliteUnitOfWork(connection))


def _artifact_service_for(db_path: Path) -> ArtifactRegistryService:
    return ArtifactRegistryService(
        lambda: SqliteUnitOfWork(
            connect_control_tower(db_path),
            close_connection=True,
        )
    )


def _create_job_service_for(db_path: Path) -> CreateMigrationJobService:
    return CreateMigrationJobService(
        lambda: SqliteUnitOfWork(
            connect_control_tower(db_path),
            close_connection=True,
        )
    )


def _job_with_artifact_roots(tmp_path: Path) -> tuple[Path, tuple, str]:
    db_path = tmp_path / "control_tower.sqlite3"
    roots = artifact_roots(tmp_path)
    connection = make_migrated_connection(tmp_path)
    seed_runner_profile_with_roots(connection, roots)
    seed_pipeline_definition(connection)
    connection.close()
    job = _create_job_service_for(db_path).execute(_create_command())
    return db_path, roots, job.job_id


def _migrated_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    apply_pending_migrations(connection)
    return connection


def _create_command() -> CreateMigrationJobCommand:
    return CreateMigrationJobCommand(
        actor="tester",
        legacy_source_ref="C:/legacy/source",
        output_root_ref="C:/workspace/output",
        runner_profile_id="runner-default",
        runner_profile_version="2026.06",
        pipeline_id="pipeline-default",
        pipeline_version="2026.06",
        target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
        enabled_gates=("build", "test"),
        policy=RunPolicy(),
        correlation_id="corr-job",
    )


def _artifact_command(job_id: str, artifact) -> RegisterArtifactCommand:
    return RegisterArtifactCommand(
        job_id=job_id,
        artifact=artifact,
        artifact_type="report",
        actor_type="user",
        actor_id="tester",
        content_type="text/plain",
        correlation_id="corr-artifact",
    )


def _write_and_hash(roots, relative_path: str, contents: bytes):
    path = Path(roots[0].path) / Path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return hash_registered_artifact(roots, "source-root", relative_path)


def _runner_command() -> RegisterRunnerProfileCommand:
    profile = runner_profile_payload()
    profile["runner_profile_version"] = "v1"
    return RegisterRunnerProfileCommand(
        profile=profile,
        actor_type="user",
        actor_id="tester",
    )


def _pipeline_command(
    *,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> RegisterPipelineDefinitionCommand:
    pipeline = pipeline_definition_payload()
    pipeline["pipeline_version"] = "v1"
    return RegisterPipelineDefinitionCommand(
        pipeline=pipeline,
        actor_type="user",
        actor_id="tester",
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def _transition_command(
    target_state: JobState,
    *,
    expected_version: int | None = 1,
) -> TransitionJobStateCommand:
    return TransitionJobStateCommand(
        job_id="job-1",
        expected_version=expected_version,
        target_state=target_state,
        actor_type="user",
        actor_id="tester",
        reason="advance lifecycle",
        correlation_id="corr-1",
        causation_id="cause-1",
    )


def _audit_row(connection: sqlite3.Connection) -> sqlite3.Row:
    return connection.execute("SELECT * FROM audit_records").fetchone()


def _transition_audit(connection: sqlite3.Connection) -> AuditRecordDto:
    with SqliteUnitOfWork(connection) as uow:
        audits = uow.audit_records.list_for_job("job-1")
    assert len(audits) == 1
    return audits[0]


def _count(connection: sqlite3.Connection, table_name: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _artifact_event_count(connection: sqlite3.Connection, job_id: str) -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM run_events WHERE job_id = ? AND event_type = ?",
            (job_id, "artifact_registered"),
        ).fetchone()[0]
    )


def _job_sequence(connection: sqlite3.Connection, job_id: str) -> int:
    return int(
        connection.execute(
            "SELECT last_event_sequence FROM migration_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
    )
