from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi.app import create_app
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import seed_job


_MUTATION_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "http://127.0.0.1:3000",
    "X-Control-Tower-Client": "control-tower-frontend",
}


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


def _open_api_db(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    apply_pending_migrations(connection)
    return connection


def _snapshot_directory(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file()}


def test_generated_repair_proposal_does_not_mutate_source_sandbox_job_or_stage_state(tmp_path) -> None:
    db_path = tmp_path / "v1_14c_invariants.db"
    connection = _open_api_db(db_path)
    seed_job(connection)
    _seed_command(connection, command_id="cmd-invariants")
    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

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
    before_events = connection.execute("SELECT COUNT(*) FROM run_events WHERE job_id = 'job-1'").fetchone()[0]

    classify = client.post(
        "/v1/commands/cmd-invariants/repair-classifications",
        content=json.dumps({
            "evidence_kind": "stderr_excerpt",
            "failure_summary": "AssertionError: tests failed after migration",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert classify.status_code == 200, classify.text
    generate = client.post(
        "/v1/commands/cmd-invariants/fake-repair-proposals",
        content=json.dumps({}),
        headers=_MUTATION_HEADERS,
    )
    assert generate.status_code == 200, generate.text

    assert _snapshot_directory(source_dir) == before_source
    assert _snapshot_directory(sandbox_dir) == before_sandbox
    after_job = connection.execute(
        """
        SELECT pipeline_id, pipeline_version, runner_profile_id, runner_profile_version,
               status, version, last_event_sequence
        FROM migration_jobs WHERE job_id = 'job-1'
        """
    ).fetchone()
    after_stage_runs = connection.execute("SELECT COUNT(*) FROM stage_runs WHERE job_id = 'job-1'").fetchone()[0]
    after_events = connection.execute("SELECT COUNT(*) FROM run_events WHERE job_id = 'job-1'").fetchone()[0]

    assert tuple(before_job) == tuple(after_job)
    assert after_stage_runs == before_stage_runs
    assert after_events == before_events
    assert before_job["pipeline_id"] == "pipeline-1"
    assert before_job["pipeline_version"] == "v1"


def test_generated_repair_status_and_attempts_remain_redacted_and_non_executable(tmp_path) -> None:
    db_path = tmp_path / "v1_14c_status.db"
    connection = _open_api_db(db_path)
    seed_job(connection)
    _seed_command(connection, command_id="cmd-status")
    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    classify = client.post(
        "/v1/commands/cmd-status/repair-classifications",
        content=json.dumps({
            "evidence_kind": "stderr_excerpt",
            "failure_summary": (
                r"Compilation failure in C:\repo\app.py SECRET=value model_deployment_id=dep-9 "
                r"mvn test"
            ),
        }),
        headers=_MUTATION_HEADERS,
    )
    assert classify.status_code == 200, classify.text
    generate = client.post(
        "/v1/commands/cmd-status/fake-repair-proposals",
        content=json.dumps({}),
        headers=_MUTATION_HEADERS,
    )
    assert generate.status_code == 200, generate.text

    status_response = client.get("/v1/commands/cmd-status/repair-status")
    assert status_response.status_code == 200, status_response.text
    payload = status_response.json()
    assert payload["proposal_count"] == 1
    assert payload["eligible_for_fake_repair"] is True
    assert payload["remaining_attempts"] == 1

    blob = json.dumps(payload)
    assert r"C:\repo\app.py" not in blob
    assert "SECRET=value" not in blob
    assert "model_deployment_id=dep-9" not in blob
    assert "mvn test" not in blob
