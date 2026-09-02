from __future__ import annotations

import pytest

from migration_factory.control_tower.application.repairs import RepairService
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.errors import (
    RepairAttemptLimitExceededError,
    RepairClassificationError,
    RepairProposalValidationError,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import migrated_connection, seed_job


def _seed_command(connection, *, command_id: str, status: CommandState = CommandState.FAILED) -> None:
    connection.execute(
        """
        INSERT INTO command_executions (
            command_id, job_id, operation, status, created_at, updated_at,
            correlation_id, causation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            command_id,
            "job-1",
            "diagnostic",
            status.value,
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            None,
            None,
        ),
    )


def _service(connection) -> RepairService:
    return RepairService(lambda: SqliteUnitOfWork(connection))


def test_limit_blocks_excess_attempts_and_projection_counts(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_14b_limit.db")
    seed_job(connection)
    _seed_command(connection, command_id="cmd-limit")
    service = _service(connection)
    service.classify_failed_command(
        command_id="cmd-limit",
        evidence_kind="stderr_excerpt",
        failure_summary="Compilation failure: javac failed to compile source",
        actor_type="user",
        actor_id="tester",
    )

    first = service.record_repair_attempt(
        command_id="cmd-limit",
        attempt_summary="Add missing import and re-run compile checks",
        actor_type="user",
        actor_id="tester",
    )
    second = service.record_repair_attempt(
        command_id="cmd-limit",
        attempt_summary="Restore unresolved type and re-run compile checks",
        actor_type="user",
        actor_id="tester",
    )
    status = service.get_repair_status("cmd-limit")

    assert first.attempt_order == 1
    assert second.attempt_order == 2
    assert status.attempts_used == 2
    assert status.remaining_attempts == 0

    with pytest.raises(RepairAttemptLimitExceededError):
        service.record_repair_attempt(
            command_id="cmd-limit",
            attempt_summary="Third repair attempt must be blocked",
            actor_type="user",
            actor_id="tester",
        )


def test_non_repairable_classification_allows_zero_attempts(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_14b_zero.db")
    seed_job(connection)
    _seed_command(connection, command_id="cmd-zero", status=CommandState.TIMED_OUT)
    service = _service(connection)
    classification = service.classify_failed_command(
        command_id="cmd-zero",
        evidence_kind="stderr_excerpt",
        failure_summary="Timed out waiting for worker heartbeat",
        actor_type="user",
        actor_id="tester",
    )
    status = service.get_repair_status("cmd-zero")

    assert classification.attempt_limit == 0
    assert status.remaining_attempts == 0

    with pytest.raises(RepairClassificationError):
        service.record_repair_attempt(
            command_id="cmd-zero",
            attempt_summary="Should not allow repair attempt",
            actor_type="user",
            actor_id="tester",
        )


def test_unsafe_attempt_payload_rejected_safely(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_14b_unsafe.db")
    seed_job(connection)
    _seed_command(connection, command_id="cmd-unsafe")
    service = _service(connection)
    service.classify_failed_command(
        command_id="cmd-unsafe",
        evidence_kind="stderr_excerpt",
        failure_summary="Compilation failure: javac failed to compile source",
        actor_type="user",
        actor_id="tester",
    )

    with pytest.raises(RepairProposalValidationError):
        service.record_repair_attempt(
            command_id="cmd-unsafe",
            attempt_summary="diff --git a/pom.xml b/pom.xml",
            actor_type="user",
            actor_id="tester",
        )
