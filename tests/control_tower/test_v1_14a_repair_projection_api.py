from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
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


@pytest.fixture
def api_fixture(tmp_path: Path) -> tuple[TestClient, sqlite3.Connection, str, Path, Path]:
    db_path = tmp_path / "v1_14a_api.db"
    connection = sqlite3.connect(str(db_path), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    apply_pending_migrations(connection)
    seed_job(connection)
    _seed_command(connection, command_id="cmd-api", status=CommandState.FAILED)
    connection.commit()

    source_dir = tmp_path / "source"
    sandbox_dir = tmp_path / "sandbox"
    source_dir.mkdir()
    sandbox_dir.mkdir()
    (source_dir / "main.txt").write_text("source-stable", encoding="utf-8")
    (sandbox_dir / "work.txt").write_text("sandbox-stable", encoding="utf-8")

    client = TestClient(
        create_app(lambda: SqliteUnitOfWork(connection)),
        base_url="http://127.0.0.1:8000",
    )
    return client, connection, "cmd-api", source_dir, sandbox_dir


def _snapshot_directory(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file()}


def test_public_projection_redacts_path_env_stack_and_command(api_fixture) -> None:
    client, _, command_id, _, _ = api_fixture
    response = client.post(
        f"/v1/commands/{command_id}/repair-classifications",
        content=json.dumps({
            "evidence_kind": "stderr_excerpt",
            "failure_summary": (
                r"Traceback (most recent call last): ImportError in C:\Users\secret\app.py "
                r"TOKEN=abc mvn test -DskipTests"
            ),
        }),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 200, response.text
    blob = json.dumps(response.json())
    assert r"C:\Users\secret" not in blob
    assert "TOKEN=abc" not in blob
    assert "mvn test -DskipTests" not in blob
    assert "Traceback (most recent call last):" not in blob


def test_repair_endpoints_do_not_mutate_source_sandbox_or_job_state(api_fixture) -> None:
    client, connection, command_id, source_dir, sandbox_dir = api_fixture
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

    classify = client.post(
        f"/v1/commands/{command_id}/repair-classifications",
        content=json.dumps({
            "evidence_kind": "stderr_excerpt",
            "failure_summary": "Compilation failure: javac failed to compile source",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert classify.status_code == 200, classify.text
    propose = client.post(
        f"/v1/commands/{command_id}/fake-repair-proposals",
        content=json.dumps({"proposal_summary": "Add missing import and re-run compile checks"}),
        headers=_MUTATION_HEADERS,
    )
    assert propose.status_code == 200, propose.text
    status_response = client.get(f"/v1/commands/{command_id}/repair-status")
    assert status_response.status_code == 200, status_response.text
    assert status_response.json()["eligible_for_fake_repair"] is True

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


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "raw_path",
        "maven_goals",
        "shell_command",
        "working_directory",
        "model_deployment_id",
        "patch_path",
        "route",
        "ledger_id",
        "approval_id",
        "artifact_id",
        "target_proof_level",
        "achieved_proof_level",
    ],
)
def test_browser_cannot_inject_authority_fields(api_fixture, forbidden_field: str) -> None:
    client, _, command_id, _, _ = api_fixture
    response = client.post(
        f"/v1/commands/{command_id}/fake-repair-proposals",
        content=json.dumps({"proposal_summary": "Safe summary", forbidden_field: "forbidden"}),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 422


def test_invalid_unsafe_proposal_rejected(api_fixture) -> None:
    client, _, command_id, _, _ = api_fixture
    classify = client.post(
        f"/v1/commands/{command_id}/repair-classifications",
        content=json.dumps({
            "evidence_kind": "stderr_excerpt",
            "failure_summary": "Compilation failure: javac failed to compile source",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert classify.status_code == 200, classify.text

    response = client.post(
        f"/v1/commands/{command_id}/fake-repair-proposals",
        content=json.dumps({"proposal_summary": "diff --git a/pom.xml b/pom.xml"}),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REPAIR_PROPOSAL_INVALID"
