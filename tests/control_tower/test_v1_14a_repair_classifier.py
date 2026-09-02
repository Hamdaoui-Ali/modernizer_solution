from __future__ import annotations

from migration_factory.control_tower.application.repairs import RepairService
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.entities import CommandExecutionRecord
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import migrated_connection, seed_job


def _seed_command(connection, *, command_id: str, status: CommandState) -> None:
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


def test_same_failed_command_evidence_gives_same_repairability_reason(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_14a_same.db")
    seed_job(connection)
    _seed_command(connection, command_id="cmd-1", status=CommandState.FAILED)
    service = _service(connection)

    first = service.classify_failed_command(
        command_id="cmd-1",
        evidence_kind="stderr_excerpt",
        failure_summary="ImportError: No module named jakarta.servlet",
        actor_type="user",
        actor_id="tester",
    )
    second = service.classify_failed_command(
        command_id="cmd-1",
        evidence_kind="stderr_excerpt",
        failure_summary="ImportError: No module named jakarta.servlet",
        actor_type="user",
        actor_id="tester",
    )

    assert second.classification_id == first.classification_id
    assert second.classification_code == "repairable_dependency_or_import"
    assert second.reason_code == "dependency_import_missing"


def test_compile_and_import_failures_classified_repairable(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_14a_compile.db")
    seed_job(connection)
    _seed_command(connection, command_id="cmd-import", status=CommandState.FAILED)
    _seed_command(connection, command_id="cmd-compile", status=CommandState.FAILED)
    service = _service(connection)

    import_result = service.classify_failed_command(
        command_id="cmd-import",
        evidence_kind="stderr_excerpt",
        failure_summary="ModuleNotFoundError: No module named 'foo'",
        actor_type="user",
        actor_id="tester",
    )
    compile_result = service.classify_failed_command(
        command_id="cmd-compile",
        evidence_kind="stderr_excerpt",
        failure_summary="Compilation failure: javac failed to compile source",
        actor_type="user",
        actor_id="tester",
    )

    assert import_result.repairable is True
    assert import_result.classification_code == "repairable_dependency_or_import"
    assert compile_result.repairable is True
    assert compile_result.classification_code == "repairable_compile_error"


def test_infrastructure_and_policy_failures_classified_not_repairable(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_14a_policy.db")
    seed_job(connection)
    _seed_command(connection, command_id="cmd-policy", status=CommandState.FAILED)
    _seed_command(connection, command_id="cmd-timeout", status=CommandState.TIMED_OUT)
    service = _service(connection)

    policy = service.classify_failed_command(
        command_id="cmd-policy",
        evidence_kind="stderr_excerpt",
        failure_summary="Policy violation: Boot 4 is not allowed for this route",
        actor_type="user",
        actor_id="tester",
    )
    infra = service.classify_failed_command(
        command_id="cmd-timeout",
        evidence_kind="stderr_excerpt",
        failure_summary="Timed out waiting for worker heartbeat",
        actor_type="user",
        actor_id="tester",
    )

    assert policy.repairable is False
    assert policy.classification_code == "not_repairable_policy"
    assert infra.repairable is False
    assert infra.classification_code == "not_repairable_infrastructure"


def test_unknown_failure_classified_deterministically(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_14a_unknown.db")
    seed_job(connection)
    _seed_command(connection, command_id="cmd-unknown", status=CommandState.FAILED)

    result = _service(connection).classify_failed_command(
        command_id="cmd-unknown",
        evidence_kind="stderr_excerpt",
        failure_summary="Unhandled failure with opaque signature 77",
        actor_type="user",
        actor_id="tester",
    )

    assert result.repairable is False
    assert result.classification_code == "not_repairable_unknown"
    assert result.reason_code == "unknown_failure_signature"
