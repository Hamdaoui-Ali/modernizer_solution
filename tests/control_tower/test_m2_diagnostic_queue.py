from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from migration_factory.control_tower.application.commands import (
    CreateDiagnosticJobCommand,
    StartMigrationJobCommand,
)
from migration_factory.control_tower.application.services import DiagnosticJobService
from migration_factory.control_tower.domain.commands import (
    CommandState,
    NONTERMINAL_COMMAND_STATES,
)
from migration_factory.control_tower.domain.errors import (
    ActiveCommandConflictError,
    IdempotencyConflictError,
    InvalidJobStateTransitionError,
    StaleVersionError,
    StorageIntegrityError,
)
from migration_factory.control_tower.domain.states import JobState, TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.schemas.run_configuration import RunPolicy
from tests.control_tower._helpers import (
    artifact_roots,
    seed_pipeline_definition,
    seed_runner_profile_with_roots,
)


def test_command_state_contracts_are_stable() -> None:
    assert CommandState.QUEUED.value == "QUEUED"
    assert {state.value for state in NONTERMINAL_COMMAND_STATES} == {
        "QUEUED",
        "STARTING",
        "RUNNING",
        "CANCELLING",
    }
    assert "OUTPUT_LIMIT_EXCEEDED" not in {state.value for state in CommandState}
    assert "LAUNCH_FAILED" not in {state.value for state in CommandState}


def test_m2_migration_adds_command_idempotency_and_event_catalog(tmp_path: Path) -> None:
    connection = _connection(tmp_path)

    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "command_executions" in tables
    assert "idempotency_records" in tables
    assert "event_types" in tables

    connection.execute(
        """
        INSERT INTO event_types (event_type, description)
        VALUES ('m2_test_event', 'test')
        """
    )
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_one_nonterminal_command_per_job_is_enforced(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    service = _service(connection)
    created = service.create_diagnostic_job(_create_command("idem-create"))
    service.start_migration_job(_start_command(created.job.job_id, 1, "idem-start"))

    with pytest.raises(ActiveCommandConflictError):
        service.start_migration_job(_start_command(created.job.job_id, 2, "idem-start-2"))


def test_create_diagnostic_job_idempotency_replays_and_conflicts(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    service = _service(connection)

    first = service.create_diagnostic_job(_create_command("idem-create"))
    replay = service.create_diagnostic_job(_create_command("idem-create"))

    assert replay.job.job_id == first.job.job_id
    assert replay.job.status == JobState.CREATED

    with pytest.raises(IdempotencyConflictError):
        service.create_diagnostic_job(
            _create_command("idem-create", legacy_source_relative_path="different")
        )


def test_failed_create_before_commit_leaves_no_job_or_completed_idempotency(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    service = DiagnosticJobService(lambda: _FailingCreateIdempotencyUnitOfWork(connection))

    with pytest.raises(StorageIntegrityError):
        service.create_diagnostic_job(_create_command("idem-create"))

    assert _count(connection, "migration_jobs") == 0
    assert _count(connection, "run_configurations") == 0
    assert _count(connection, "stage_runs") == 0
    assert _count(connection, "run_events") == 0
    assert _count(connection, "audit_records") == 0
    assert _count(connection, "idempotency_records") == 0


def test_former_create_crash_window_rolls_back_job_and_idempotency_together(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    service = DiagnosticJobService(lambda: _FailingCreateIdempotencyUnitOfWork(connection))

    with pytest.raises(StorageIntegrityError):
        service.create_diagnostic_job(_create_command("idem-create"))

    retry = _service(connection).create_diagnostic_job(_create_command("idem-create"))

    assert retry.job.status == JobState.CREATED
    assert _count(connection, "migration_jobs") == 1
    assert _count(connection, "run_configurations") == 1
    assert _count(connection, "run_events") == 1
    assert _count(connection, "idempotency_records") == 1


def test_concurrent_identical_creates_produce_one_job(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    connection = connect_control_tower(db_path)
    apply_pending_migrations(connection)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    connection.close()

    def attempt() -> str:
        service = DiagnosticJobService(
            lambda: SqliteUnitOfWork(
                connect_control_tower(db_path),
                close_connection=True,
            )
        )
        return service.create_diagnostic_job(_create_command("idem-create")).job.job_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        job_ids = list(executor.map(lambda _: attempt(), range(2)))

    verification = connect_control_tower(db_path)
    assert len(set(job_ids)) == 1
    assert _count(verification, "migration_jobs") == 1
    assert _count(verification, "idempotency_records") == 1
    verification.close()


def test_concurrent_changed_create_requests_under_one_key_conflict(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    connection = connect_control_tower(db_path)
    apply_pending_migrations(connection)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    connection.close()

    def attempt(relative_path: str) -> str:
        service = DiagnosticJobService(
            lambda: SqliteUnitOfWork(
                connect_control_tower(db_path),
                close_connection=True,
            )
        )
        try:
            service.create_diagnostic_job(
                _create_command("idem-create", legacy_source_relative_path=relative_path)
            )
        except IdempotencyConflictError:
            return "conflict"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ("src", "different")))

    verification = connect_control_tower(db_path)
    assert results.count("created") == 1
    assert results.count("conflict") == 1
    assert _count(verification, "migration_jobs") == 1
    assert _count(verification, "idempotency_records") == 1
    verification.close()


def test_start_queues_command_transitions_job_events_audit_and_idempotency(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    service = _service(connection)
    created = service.create_diagnostic_job(_create_command("idem-create"))

    queued = service.start_migration_job(_start_command(created.job.job_id, 1, "idem-start"))

    assert queued.job.status == JobState.QUEUED
    assert queued.job.version == 2
    assert queued.active_command is not None
    assert queued.active_command.status == CommandState.QUEUED

    event_types = [
        row["event_type"]
        for row in connection.execute(
            "SELECT event_type FROM run_events WHERE job_id = ? ORDER BY sequence",
            (created.job.job_id,),
        ).fetchall()
    ]
    assert event_types == ["job_created", "command_queued", "job_state_changed"]
    assert _count(connection, "audit_records") == 2
    assert _count(connection, "idempotency_records") == 2


def test_start_idempotency_replays_and_conflicts(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    service = _service(connection)
    created = service.create_diagnostic_job(_create_command("idem-create"))

    first = service.start_migration_job(_start_command(created.job.job_id, 1, "idem-start"))
    replay = service.start_migration_job(_start_command(created.job.job_id, 1, "idem-start"))

    assert replay.job.job_id == first.job.job_id
    assert replay.job.status == JobState.QUEUED
    assert replay.active_command == first.active_command

    with pytest.raises(IdempotencyConflictError):
        service.start_migration_job(_start_command(created.job.job_id, 2, "idem-start"))


def test_start_requires_created_state_and_expected_version(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    service = _service(connection)
    created = service.create_diagnostic_job(_create_command("idem-create"))

    with pytest.raises(StaleVersionError):
        service.start_migration_job(_start_command(created.job.job_id, 99, "idem-start"))

    connection.execute(
        "UPDATE migration_jobs SET status = 'QUEUED', version = 2 WHERE job_id = ?",
        (created.job.job_id,),
    )
    with pytest.raises(InvalidJobStateTransitionError):
        service.start_migration_job(_start_command(created.job.job_id, 2, "idem-start-2"))


def test_failed_start_rolls_back_command_event_audit_and_idempotency(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    service = _service(connection)
    created = service.create_diagnostic_job(_create_command("idem-create"))

    with pytest.raises(StaleVersionError):
        service.start_migration_job(_start_command(created.job.job_id, 99, "idem-start"))

    assert _count(connection, "command_executions") == 0
    assert _count(connection, "run_events") == 1
    assert _count(connection, "audit_records") == 1
    assert _count(connection, "idempotency_records") == 1


def test_concurrent_start_attempts_result_in_exactly_one_queued_command(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    connection = connect_control_tower(db_path)
    apply_pending_migrations(connection)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    created = _service(connection).create_diagnostic_job(_create_command("idem-create"))
    connection.close()

    def attempt(idempotency_key: str) -> str:
        service = DiagnosticJobService(
            lambda: SqliteUnitOfWork(
                connect_control_tower(db_path),
                close_connection=True,
            )
        )
        try:
            service.start_migration_job(_start_command(created.job.job_id, 1, idempotency_key))
        except (ActiveCommandConflictError, StaleVersionError):
            return "conflict"
        return "queued"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ("idem-start-a", "idem-start-b")))

    verification = connect_control_tower(db_path)
    assert results.count("queued") == 1
    assert results.count("conflict") == 1
    assert _count(verification, "command_executions") == 1
    assert _count(verification, "idempotency_records") == 2
    verification.close()


def _connection(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    apply_pending_migrations(connection)
    return connection


def _seeded_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = _connection(tmp_path)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    return connection


def _service(connection: sqlite3.Connection) -> DiagnosticJobService:
    return DiagnosticJobService(lambda: SqliteUnitOfWork(connection))


def _create_command(
    idempotency_key: str,
    *,
    legacy_source_relative_path: str = "src",
) -> CreateDiagnosticJobCommand:
    return CreateDiagnosticJobCommand(
        idempotency_key=idempotency_key,
        runner_profile_id="runner-default",
        runner_profile_version="2026.06",
        pipeline_id="pipeline-default",
        pipeline_version="2026.06",
        legacy_source_root_id="source-root",
        legacy_source_relative_path=legacy_source_relative_path,
        output_root_id="output-root",
        output_relative_path="out",
        target_proof_level=TargetProofLevel.ANALYZED,
        enabled_gates=(),
        policy=RunPolicy(),
    )


def _start_command(job_id: str, version: int, idempotency_key: str) -> StartMigrationJobCommand:
    return StartMigrationJobCommand(
        job_id=job_id,
        expected_version=version,
        idempotency_key=idempotency_key,
        actor_type="user",
        actor_id="tester",
    )


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


class _FailingCreateIdempotencyUnitOfWork(SqliteUnitOfWork):
    def __enter__(self):
        result = super().__enter__()
        self.idempotency_records = _FailingIdempotencyRepository(self.idempotency_records)
        return result


class _FailingIdempotencyRepository:
    def __init__(self, wrapped) -> None:
        self._wrapped = wrapped

    def get(self, operation: str, idempotency_key: str):
        return self._wrapped.get(operation, idempotency_key)

    def insert(self, record) -> None:
        raise StorageIntegrityError("injected idempotency failure")
