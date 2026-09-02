from __future__ import annotations

import pytest

from migration_factory.control_tower.application.repairs import RepairService
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.errors import (
    RepairAttemptLimitExceededError,
    RepairClassificationError,
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


@pytest.mark.parametrize(
    ("command_id", "failure_summary", "recommendation_type"),
    [
        (
            "cmd-import",
            "ImportError: No module named jakarta.servlet",
            "dependency_alignment",
        ),
        (
            "cmd-compile",
            "Compilation failure: javac failed to compile source",
            "compile_fixup",
        ),
        (
            "cmd-test",
            "AssertionError: tests failed after migration",
            "test_expectation_review",
        ),
    ],
)
def test_deterministic_fake_provider_proposal_by_repairable_category(
    tmp_path,
    command_id: str,
    failure_summary: str,
    recommendation_type: str,
) -> None:
    connection = migrated_connection(tmp_path, f"{command_id}.db")
    seed_job(connection)
    _seed_command(connection, command_id=command_id)
    service = _service(connection)
    service.classify_failed_command(
        command_id=command_id,
        evidence_kind="stderr_excerpt",
        failure_summary=failure_summary,
        actor_type="user",
        actor_id="tester",
    )

    first = service.generate_fake_repair_proposal(
        command_id=command_id,
        actor_type="user",
        actor_id="tester",
    )
    second = service.generate_fake_repair_proposal(
        command_id=command_id,
        actor_type="user",
        actor_id="tester",
    )

    assert second.proposal_id == first.proposal_id
    assert second.proposal_checksum == first.proposal_checksum
    assert first.proposal_kind == "generated"
    assert first.recommendation_type == recommendation_type
    assert first.applicable is True
    assert "manual_review_required" in first.warning_codes
    assert "non_authoritative" in first.warning_codes


def test_non_repairable_classification_returns_no_applicable_generated_proposal(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_14c_non_repairable.db")
    seed_job(connection)
    _seed_command(connection, command_id="cmd-timeout", status=CommandState.TIMED_OUT)
    service = _service(connection)
    service.classify_failed_command(
        command_id="cmd-timeout",
        evidence_kind="stderr_excerpt",
        failure_summary="Timed out waiting for worker heartbeat",
        actor_type="user",
        actor_id="tester",
    )

    with pytest.raises(RepairClassificationError):
        service.generate_fake_repair_proposal(
            command_id="cmd-timeout",
            actor_type="user",
            actor_id="tester",
        )


def test_excess_generated_proposal_blocked_by_attempt_limit(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_14c_limit.db")
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
    service.record_repair_attempt(
        command_id="cmd-limit",
        attempt_summary="Review unresolved type reference before next run",
        actor_type="user",
        actor_id="tester",
    )
    generated = service.generate_fake_repair_proposal(
        command_id="cmd-limit",
        actor_type="user",
        actor_id="tester",
    )
    repeated = service.generate_fake_repair_proposal(
        command_id="cmd-limit",
        actor_type="user",
        actor_id="tester",
    )

    assert repeated.proposal_id == generated.proposal_id
    assert repeated.proposal_order == generated.proposal_order == 2

    with pytest.raises(RepairAttemptLimitExceededError):
        service.record_repair_attempt(
            command_id="cmd-limit",
            attempt_summary="Third budget-consuming row must be blocked",
            actor_type="user",
            actor_id="tester",
        )
