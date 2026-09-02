from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from migration_factory.control_tower.application.commands import (
    CreateMigrationJobCommand,
    RegisterArtifactCommand,
    TransitionJobStateCommand,
)
from migration_factory.control_tower.application.services import (
    ArtifactRegistryService,
    CreateMigrationJobService,
)
from migration_factory.control_tower.domain.checksums import canonical_json
from migration_factory.control_tower.domain.errors import (
    ExpectedVersionRequiredError,
    InvalidJobStateTransitionError,
    StaleVersionError,
)
from migration_factory.control_tower.domain.states import JobState, TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.artifact_paths import hash_registered_artifact
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteControlTowerUnitOfWork,
    SqliteUnitOfWork,
)
from migration_factory.control_tower.schemas.run_configuration import RunPolicy
from ._helpers import (
    artifact_roots,
    make_migrated_connection,
    seed_pipeline_definition,
    seed_runner_and_pipeline,
    seed_runner_profile_with_roots,
)
from tests.control_tower.transition_helpers import (
    count_audit_records,
    count_run_events,
    fetch_job,
    migrated_connection,
    seed_job,
    service,
)


def test_job_created_event_sequence_is_one(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    seed_runner_and_pipeline(connection)
    connection.close()

    result = _create_service_for(db_path).execute(_create_command())

    with connect_control_tower(db_path) as verification_connection:
        row = verification_connection.execute(
            """
            SELECT sequence, event_type, actor_type, actor_id
            FROM run_events
            WHERE job_id = ?
            """,
            (result.job_id,),
        ).fetchone()

    assert row is not None
    assert row["sequence"] == 1
    assert row["event_type"] == "job_created"
    assert row["actor_type"] == "user"
    assert row["actor_id"] == "tester"


def test_event_sequence_is_unique_per_job(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    seed_runner_and_pipeline(connection)
    connection.close()

    result = _create_service_for(db_path).execute(_create_command())

    with connect_control_tower(db_path) as verification_connection:
        with pytest.raises(sqlite3.IntegrityError):
            verification_connection.execute(
                """
                INSERT INTO run_events (
                    event_id, job_id, sequence, event_type, actor_type, actor_id,
                    correlation_id, causation_id, payload_json, payload_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "duplicate-event",
                    result.job_id,
                    1,
                    "job_created",
                    "user",
                    "tester",
                    None,
                    None,
                    "{}",
                    "checksum",
                    "2026-01-01T00:00:00.000000Z",
                ),
            )


def test_artifact_registered_event_sequence_uses_job_counter_not_max_event(tmp_path: Path) -> None:
    db_path, roots, job_id = _job_with_artifact_roots(tmp_path)
    artifact = _write_and_hash(roots, "reports/event.txt", b"event")

    with connect_control_tower(db_path) as connection:
        connection.execute(
            """
            INSERT INTO run_events (
                event_id, job_id, sequence, event_type, actor_type, actor_id,
                correlation_id, causation_id, payload_json, payload_checksum, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "out-of-band-event",
                job_id,
                50,
                "job_created",
                "system",
                "tester",
                None,
                None,
                "{}",
                "checksum",
                "2026-01-01T00:00:00.000000Z",
            ),
        )

    _artifact_service_for(db_path).register_artifact(_artifact_command(job_id, artifact))

    with connect_control_tower(db_path) as verification_connection:
        row = verification_connection.execute(
            """
            SELECT sequence, event_type
            FROM run_events
            WHERE job_id = ? AND event_type = ?
            """,
            (job_id, "artifact_registered"),
        ).fetchone()
        job_row = verification_connection.execute(
            """
            SELECT last_event_sequence
            FROM migration_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()

    assert row["sequence"] == 2
    assert row["event_type"] == "artifact_registered"
    assert job_row["last_event_sequence"] == 2


def test_successful_transition_creates_job_state_changed_event(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED)

        service(connection).transition_job_state(_transition_command(JobState.QUEUED))

        event = _single_event(connection)
        assert event.event_type == "job_state_changed"
        assert event.job_id == "job-1"
        assert event.sequence == 1
        assert event.actor_type == "user"
        assert event.actor_id == "tester"
        assert event.correlation_id == "corr-1"
        assert event.causation_id == "cause-1"
    finally:
        connection.close()


def test_event_sequence_starts_from_existing_last_event_sequence_plus_one(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED, last_event_sequence=4)

        service(connection).transition_job_state(_transition_command(JobState.QUEUED))

        event = _single_event(connection)
        assert event.sequence == 5
        assert fetch_job(connection)["last_event_sequence"] == 5
    finally:
        connection.close()


def test_event_payload_records_state_actor_reason_and_versions(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.RUNNING, version=3)

        service(connection).transition_job_state(
            _transition_command(JobState.PAUSED_FOR_PLAN_APPROVAL, expected_version=3)
        )

        payload = _single_event(connection).payload
        assert payload["job_id"] == "job-1"
        assert payload["prior_state"] == "RUNNING"
        assert payload["new_state"] == "PAUSED_FOR_PLAN_APPROVAL"
        assert payload["prior_version"] == 3
        assert payload["new_version"] == 4
        assert payload["actor_type"] == "user"
        assert payload["actor_id"] == "tester"
        assert payload["reason"] == "advance lifecycle"
        assert payload["correlation_id"] == "corr-1"
        assert payload["causation_id"] == "cause-1"
    finally:
        connection.close()


def test_event_payload_checksum_matches_canonical_payload_json(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED)

        service(connection).transition_job_state(_transition_command(JobState.QUEUED))

        event = _single_event(connection)
        assert event.payload_json == canonical_json(event.payload)
        assert event.payload_checksum == hashlib.sha256(
            event.payload_json.encode("utf-8")
        ).hexdigest()
    finally:
        connection.close()


def test_event_failure_rolls_back_job_update_and_sequence(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED)
        transition_service = service_with_failing_events(connection)

        with pytest.raises(RuntimeError, match="event failed"):
            transition_service.transition_job_state(_transition_command(JobState.QUEUED))

        row = fetch_job(connection)
        assert row["status"] == "CREATED"
        assert row["version"] == 1
        assert row["active_slot"] == 1
        assert row["last_event_sequence"] == 0
        assert count_run_events(connection) == 0
        assert count_audit_records(connection) == 0
    finally:
        connection.close()


def test_no_event_is_created_for_invalid_transition(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED)

        with pytest.raises(InvalidJobStateTransitionError):
            service(connection).transition_job_state(_transition_command(JobState.RUNNING))

        assert count_run_events(connection) == 0
    finally:
        connection.close()


def test_no_event_is_created_for_stale_version(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED, version=2)

        with pytest.raises(StaleVersionError):
            service(connection).transition_job_state(
                _transition_command(JobState.QUEUED, expected_version=1)
            )

        assert count_run_events(connection) == 0
    finally:
        connection.close()


def test_no_event_is_created_for_missing_expected_version(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED)

        with pytest.raises(ExpectedVersionRequiredError):
            service(connection).transition_job_state(
                _transition_command(JobState.QUEUED, expected_version=None)
            )

        assert count_run_events(connection) == 0
    finally:
        connection.close()


class _FailingRunEventRepository:
    def append_job_state_changed_event(self, **kwargs) -> None:
        raise RuntimeError("event failed")


def service_with_failing_events(connection):
    def factory() -> SqliteUnitOfWork:
        uow = SqliteUnitOfWork(connection)
        uow.run_events = _FailingRunEventRepository()
        return uow

    from migration_factory.control_tower.application.services import ControlTowerRegistrationService

    return ControlTowerRegistrationService(factory)


def _single_event(connection):
    with SqliteUnitOfWork(connection) as uow:
        events = uow.run_events.list_for_job("job-1")
    assert len(events) == 1
    return events[0]


def _create_service_for(db_path: Path) -> CreateMigrationJobService:
    def factory() -> SqliteControlTowerUnitOfWork:
        return SqliteControlTowerUnitOfWork(connect_control_tower(db_path), close_connection=True)

    return CreateMigrationJobService(factory)


def _artifact_service_for(db_path: Path) -> ArtifactRegistryService:
    return ArtifactRegistryService(
        lambda: SqliteControlTowerUnitOfWork(
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
    job = _create_service_for(db_path).execute(_create_command())
    return db_path, roots, job.job_id


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
        correlation_id="corr-1",
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
