from __future__ import annotations

import sqlite3
from pathlib import Path

from migration_factory.control_tower.application.repairs import RepairService
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import seed_job


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


def _open_db(db_path: Path):
    connection = connect_control_tower(db_path)
    apply_pending_migrations(connection)
    return connection


def _service(connection) -> RepairService:
    return RepairService(lambda: SqliteUnitOfWork(connection))


def test_attempt_records_persist_deterministically(tmp_path) -> None:
    db_path = tmp_path / "v1_14b_persist.db"
    connection = _open_db(db_path)
    seed_job(connection)
    _seed_command(connection, command_id="cmd-persist")
    service = _service(connection)
    service.classify_failed_command(
        command_id="cmd-persist",
        evidence_kind="stderr_excerpt",
        failure_summary="Compilation failure: javac failed to compile source",
        actor_type="user",
        actor_id="tester",
    )

    first = service.record_repair_attempt(
        command_id="cmd-persist",
        attempt_summary="Add missing import and re-run compile checks",
        actor_type="user",
        actor_id="tester",
    )
    second = service.record_repair_attempt(
        command_id="cmd-persist",
        attempt_summary="Add missing import and re-run compile checks",
        actor_type="user",
        actor_id="tester",
    )

    assert second.attempt_id == first.attempt_id
    row = connection.execute(
        "SELECT COUNT(*) FROM v1_fake_repair_proposals WHERE command_id = ?",
        ("cmd-persist",),
    ).fetchone()
    assert int(row[0]) == 1


def test_attempt_order_auto_increments_and_checksum_stable(tmp_path) -> None:
    db_path = tmp_path / "v1_14b_order.db"
    connection = _open_db(db_path)
    seed_job(connection)
    _seed_command(connection, command_id="cmd-order")
    service = _service(connection)
    service.classify_failed_command(
        command_id="cmd-order",
        evidence_kind="stderr_excerpt",
        failure_summary="Compilation failure: javac failed to compile source",
        actor_type="user",
        actor_id="tester",
    )

    first = service.record_repair_attempt(
        command_id="cmd-order",
        attempt_summary="Add missing import and re-run compile checks",
        actor_type="user",
        actor_id="tester",
    )
    second = service.record_repair_attempt(
        command_id="cmd-order",
        attempt_summary="Restore unresolved type and re-run compile checks",
        actor_type="user",
        actor_id="tester",
    )

    assert first.attempt_order == 1
    assert second.attempt_order == 2
    repeated = service.record_repair_attempt(
        command_id="cmd-order",
        attempt_summary="Add missing import and re-run compile checks",
        actor_type="user",
        actor_id="tester",
    )
    assert repeated.attempt_checksum == first.attempt_checksum
    assert repeated.attempt_id == first.attempt_id
