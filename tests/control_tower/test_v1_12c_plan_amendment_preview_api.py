from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi.app import create_app
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import seed_job


_MUTATION_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "http://127.0.0.1:3000",
    "X-Control-Tower-Client": "control-tower-frontend",
}


@pytest.fixture
def api_fixture(tmp_path: Path) -> tuple[TestClient, sqlite3.Connection, Path, Path]:
    db_path = tmp_path / "v1_12c_api.db"
    connection = sqlite3.connect(str(db_path), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    apply_pending_migrations(connection)
    seed_job(connection)
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
    return client, connection, source_dir, sandbox_dir


def _payload(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Safe V1 plan",
        "summary": "Planning only",
        "source_kind": "manual",
        "notes": ["safe"],
        "changes": [
            {
                "stage_index": 1,
                "change_type": "documentation",
                "description": "Clarify plan text",
                "rationale": "Operator clarity",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _snapshot_directory(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file()}


def test_preview_returns_safe_contract(api_fixture) -> None:
    client, _, _, _ = api_fixture
    response = client.post(
        "/v1/jobs/job-1/plan-amendments/preview",
        content=json.dumps(_payload()),
        headers=_MUTATION_HEADERS,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["validation_status"] == "PASS"
    assert data["warning_codes"] == []
    assert data["preview_persisted"] is False
    assert data["preview_applied"] is False
    assert data["redacted_summary"]["non_authoritative"] is True


def test_preview_does_not_persist_or_mutate_job_or_files(api_fixture) -> None:
    client, connection, source_dir, sandbox_dir = api_fixture
    before_source = _snapshot_directory(source_dir)
    before_sandbox = _snapshot_directory(sandbox_dir)
    before_counts = {
        "amendments": connection.execute("SELECT COUNT(*) FROM v1_plan_amendments").fetchone()[0],
        "revisions": connection.execute("SELECT COUNT(*) FROM v1_plan_revisions").fetchone()[0],
        "audit": connection.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0],
    }
    before_job = connection.execute(
        "SELECT status, version, last_event_sequence FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()

    response = client.post(
        "/v1/jobs/job-1/plan-amendments/preview",
        content=json.dumps(_payload()),
        headers=_MUTATION_HEADERS,
    )

    assert response.status_code == 200, response.text
    assert _snapshot_directory(source_dir) == before_source
    assert _snapshot_directory(sandbox_dir) == before_sandbox
    after_counts = {
        "amendments": connection.execute("SELECT COUNT(*) FROM v1_plan_amendments").fetchone()[0],
        "revisions": connection.execute("SELECT COUNT(*) FROM v1_plan_revisions").fetchone()[0],
        "audit": connection.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0],
    }
    assert after_counts == before_counts
    after_job = connection.execute(
        "SELECT status, version, last_event_sequence FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()
    assert tuple(after_job) == tuple(before_job)


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "pipeline_id",
        "ledger_id",
        "command_id",
        "arguments",
        "approval_id",
        "artifact_id",
        "artifact_path",
        "target_proof_level",
        "achieved_proof_level",
        "working_directory",
        "shell_command",
        "maven_goals",
        "model_deployment_id",
    ],
)
def test_preview_rejects_authority_and_browser_selected_execution_fields(api_fixture, forbidden_field: str) -> None:
    client, _, _, _ = api_fixture
    response = client.post(
        "/v1/jobs/job-1/plan-amendments/preview",
        content=json.dumps(_payload(**{forbidden_field: "forbidden"})),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 422


def test_preview_response_is_redacted(api_fixture) -> None:
    client, _, _, _ = api_fixture
    response = client.post(
        "/v1/jobs/job-1/plan-amendments/preview",
        content=json.dumps(
            _payload(
                title=r"See C:\Users\secret\file.txt",
                summary="TOKEN=abc123 secret=xyz",
            )
        ),
        headers=_MUTATION_HEADERS,
    )

    assert response.status_code == 200, response.text
    blob = json.dumps(response.json())
    assert r"C:\Users\secret\file.txt" not in blob
    assert "TOKEN=abc123" not in blob
    assert "secret=xyz" not in blob
    assert "[redacted" in blob
