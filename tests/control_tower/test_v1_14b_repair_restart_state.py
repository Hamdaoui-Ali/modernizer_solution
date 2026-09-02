from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi.app import create_app
from migration_factory.control_tower.application.repairs import RepairService
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import seed_job


_MUTATION_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "http://127.0.0.1:3000",
    "X-Control-Tower-Client": "control-tower-frontend",
}


def _open_db(db_path: Path):
    connection = connect_control_tower(db_path)
    apply_pending_migrations(connection)
    return connection


def _open_api_db(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    apply_pending_migrations(connection)
    return connection


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


def _snapshot_directory(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file()}


def test_repair_state_survives_new_uow_and_new_sqlite_connection(tmp_path) -> None:
    db_path = tmp_path / "v1_14b_restart.db"
    connection = _open_db(db_path)
    seed_job(connection)
    _seed_command(connection, command_id="cmd-restart")
    service = RepairService(lambda: SqliteUnitOfWork(connection))
    service.classify_failed_command(
        command_id="cmd-restart",
        evidence_kind="stderr_excerpt",
        failure_summary="Compilation failure: javac failed to compile source",
        actor_type="user",
        actor_id="tester",
    )
    service.record_repair_attempt(
        command_id="cmd-restart",
        attempt_summary="Add missing import and re-run compile checks",
        actor_type="user",
        actor_id="tester",
    )
    connection.close()

    reopened = _open_db(db_path)
    reopened_service = RepairService(lambda: SqliteUnitOfWork(reopened))
    status = reopened_service.get_repair_status("cmd-restart")
    attempts = reopened_service.list_repair_attempts("cmd-restart")

    assert status.attempts_used == 1
    assert status.remaining_attempts == 1
    assert len(attempts) == 1
    assert attempts[0].attempt_order == 1


def test_endpoints_do_not_mutate_source_sandbox_or_job_state_and_list_attempts(tmp_path) -> None:
    db_path = tmp_path / "v1_14b_api.db"
    connection = _open_api_db(db_path)
    seed_job(connection)
    _seed_command(connection, command_id="cmd-api")

    source_dir = tmp_path / "source"
    sandbox_dir = tmp_path / "sandbox"
    source_dir.mkdir()
    sandbox_dir.mkdir()
    (source_dir / "main.txt").write_text("source-stable", encoding="utf-8")
    (sandbox_dir / "work.txt").write_text("sandbox-stable", encoding="utf-8")
    before_source = _snapshot_directory(source_dir)
    before_sandbox = _snapshot_directory(sandbox_dir)
    before_job = connection.execute(
        """
        SELECT pipeline_id, pipeline_version, runner_profile_id, runner_profile_version,
               status, version, last_event_sequence
        FROM migration_jobs WHERE job_id = 'job-1'
        """
    ).fetchone()
    before_stage_runs = connection.execute("SELECT COUNT(*) FROM stage_runs WHERE job_id = 'job-1'").fetchone()[0]

    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")
    classify = client.post(
        "/v1/commands/cmd-api/repair-classifications",
        content=json.dumps({
            "evidence_kind": "stderr_excerpt",
            "failure_summary": "Compilation failure: javac failed to compile source",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert classify.status_code == 200, classify.text
    attempt = client.post(
        "/v1/commands/cmd-api/repair-attempts",
        content=json.dumps({"attempt_summary": "Add missing import and re-run compile checks"}),
        headers=_MUTATION_HEADERS,
    )
    assert attempt.status_code == 200, attempt.text
    listing = client.get("/v1/commands/cmd-api/repair-attempts")
    assert listing.status_code == 200, listing.text
    data = listing.json()
    assert len(data["attempts"]) == 1
    assert data["attempts"][0]["attempt_order"] == 1

    assert _snapshot_directory(source_dir) == before_source
    assert _snapshot_directory(sandbox_dir) == before_sandbox
    after_job = connection.execute(
        """
        SELECT pipeline_id, pipeline_version, runner_profile_id, runner_profile_version,
               status, version, last_event_sequence
        FROM migration_jobs WHERE job_id = 'job-1'
        """
    ).fetchone()
    assert tuple(before_job) == tuple(after_job)
    after_stage_runs = connection.execute("SELECT COUNT(*) FROM stage_runs WHERE job_id = 'job-1'").fetchone()[0]
    assert after_stage_runs == before_stage_runs


def test_public_projection_redacts_and_browser_cannot_inject_forbidden_fields(tmp_path) -> None:
    db_path = tmp_path / "v1_14b_redaction.db"
    connection = _open_api_db(db_path)
    seed_job(connection)
    _seed_command(connection, command_id="cmd-redact")
    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")
    classify = client.post(
        "/v1/commands/cmd-redact/repair-classifications",
        content=json.dumps({
            "evidence_kind": "stderr_excerpt",
            "failure_summary": r"Compilation failure in C:\Users\secret TOKEN=abc Traceback (most recent call last): mvn test",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert classify.status_code == 200, classify.text
    attempt = client.post(
        "/v1/commands/cmd-redact/repair-attempts",
        content=json.dumps({"attempt_summary": "Add missing import and re-run compile checks"}),
        headers=_MUTATION_HEADERS,
    )
    assert attempt.status_code == 200, attempt.text
    status_response = client.get("/v1/commands/cmd-redact/repair-status")
    assert status_response.status_code == 200, status_response.text
    blob = json.dumps(status_response.json())
    assert r"C:\Users\secret" not in blob
    assert "TOKEN=abc" not in blob
    assert "Traceback (most recent call last):" not in blob
    assert "mvn test" not in blob

    bad = client.post(
        "/v1/commands/cmd-redact/repair-attempts",
        content=json.dumps({
            "attempt_summary": "Safe",
            "raw_path": "forbidden",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert bad.status_code == 422
