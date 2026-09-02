from __future__ import annotations

from pathlib import Path

import pytest

from migration_factory.control_tower.application.repairs import RepairService
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.errors import (
    RepairAttemptLimitExceededError,
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


def _snapshot_directory(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file()}


def test_attempt_limits_enforced_deterministically(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_14a_limit.db")
    seed_job(connection)
    _seed_command(connection, command_id="cmd-limit")
    service = _service(connection)
    classification = service.classify_failed_command(
        command_id="cmd-limit",
        evidence_kind="stderr_excerpt",
        failure_summary="Compilation failure: javac failed to compile source",
        actor_type="user",
        actor_id="tester",
    )

    assert classification.attempt_limit == 2
    service.record_fake_repair_proposal(
        command_id="cmd-limit",
        proposal_summary="Add missing import and re-run compile checks",
        actor_type="user",
        actor_id="tester",
    )
    service.record_fake_repair_proposal(
        command_id="cmd-limit",
        proposal_summary="Adjust compile target and restore missing type reference",
        actor_type="user",
        actor_id="tester",
    )
    with pytest.raises(RepairAttemptLimitExceededError):
        service.record_fake_repair_proposal(
            command_id="cmd-limit",
            proposal_summary="Third proposal should exceed deterministic limit",
            actor_type="user",
            actor_id="tester",
        )


def test_fake_proposal_recorded_without_applying_patch(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_14a_proposal.db")
    seed_job(connection)
    _seed_command(connection, command_id="cmd-proposal")
    source_dir = tmp_path / "source"
    sandbox_dir = tmp_path / "sandbox"
    source_dir.mkdir()
    sandbox_dir.mkdir()
    (source_dir / "main.txt").write_text("source-stable", encoding="utf-8")
    (sandbox_dir / "work.txt").write_text("sandbox-stable", encoding="utf-8")
    before_source = _snapshot_directory(source_dir)
    before_sandbox = _snapshot_directory(sandbox_dir)

    service = _service(connection)
    service.classify_failed_command(
        command_id="cmd-proposal",
        evidence_kind="stderr_excerpt",
        failure_summary="AssertionError: tests failed after migration",
        actor_type="user",
        actor_id="tester",
    )
    proposal = service.record_fake_repair_proposal(
        command_id="cmd-proposal",
        proposal_summary="Tighten assertion expectation and re-run affected tests",
        actor_type="user",
        actor_id="tester",
    )
    status = service.get_repair_status("cmd-proposal")

    assert proposal.proposal_order == 1
    assert status.proposal_count == 1
    assert _snapshot_directory(source_dir) == before_source
    assert _snapshot_directory(sandbox_dir) == before_sandbox


def test_invalid_unsafe_proposal_rejected_safely(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_14a_unsafe.db")
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
        service.record_fake_repair_proposal(
            command_id="cmd-unsafe",
            proposal_summary="diff --git a/pom.xml b/pom.xml",
            actor_type="user",
            actor_id="tester",
        )
