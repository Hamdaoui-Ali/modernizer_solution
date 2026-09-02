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
    db_path = tmp_path / "v1_12a_api.db"
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


def test_preview_endpoint_returns_expected_metadata(api_fixture) -> None:
    client, _, _, _ = api_fixture
    response = client.post(
        "/v1/jobs/job-1/plan-amendments/preview",
        content=json.dumps(_payload()),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["job_id"] == "job-1"
    assert data["change_count"] == 1
    assert data["affected_stage_indexes"] == [1]
    assert data["preview_applied"] is False


def test_preview_leaves_source_and_sandbox_and_plan_state_unchanged(api_fixture) -> None:
    client, connection, source_dir, sandbox_dir = api_fixture
    before_source = _snapshot_directory(source_dir)
    before_sandbox = _snapshot_directory(sandbox_dir)
    before_counts = {
        "amendments": connection.execute("SELECT COUNT(*) FROM v1_plan_amendments").fetchone()[0],
        "revisions": connection.execute("SELECT COUNT(*) FROM v1_plan_revisions").fetchone()[0],
        "audit": connection.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0],
    }
    before_job = connection.execute(
        "SELECT version, status, last_event_sequence FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()

    response = client.post(
        "/v1/jobs/job-1/plan-amendments/preview",
        content=json.dumps(_payload()),
        headers=_MUTATION_HEADERS,
    )

    assert response.status_code == 200, response.text
    assert before_source == _snapshot_directory(source_dir)
    assert before_sandbox == _snapshot_directory(sandbox_dir)
    after_counts = {
        "amendments": connection.execute("SELECT COUNT(*) FROM v1_plan_amendments").fetchone()[0],
        "revisions": connection.execute("SELECT COUNT(*) FROM v1_plan_revisions").fetchone()[0],
        "audit": connection.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0],
    }
    assert after_counts == before_counts
    after_job = connection.execute(
        "SELECT version, status, last_event_sequence FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()
    assert tuple(after_job) == tuple(before_job)


def test_create_amendment_and_revision_persist(api_fixture) -> None:
    client, connection, _, _ = api_fixture
    amendment_response = client.post(
        "/v1/jobs/job-1/plan-amendments",
        content=json.dumps(_payload()),
        headers=_MUTATION_HEADERS,
    )
    assert amendment_response.status_code == 201, amendment_response.text
    amendment_id = amendment_response.json()["amendment_id"]

    revision_response = client.post(
        f"/v1/plan-amendments/{amendment_id}/revisions",
        content=json.dumps(_payload(title="Revision", summary="Revision summary")),
        headers=_MUTATION_HEADERS,
    )
    assert revision_response.status_code == 201, revision_response.text
    revision = revision_response.json()
    assert revision["revision_order"] == 1
    assert connection.execute("SELECT COUNT(*) FROM v1_plan_amendments").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM v1_plan_revisions").fetchone()[0] == 1


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "pipeline_id",
        "ledger_id",
        "command_id",
        "arguments",
        "approval_id",
        "decision",
        "artifact_id",
        "artifact_path",
        "target_proof_level",
        "achieved_proof_level",
    ],
)
def test_plan_requests_cannot_alter_protected_identities(api_fixture, forbidden_field: str) -> None:
    client, _, _, _ = api_fixture
    response = client.post(
        "/v1/jobs/job-1/plan-amendments",
        content=json.dumps(_payload(**{forbidden_field: "forbidden"})),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 422


def test_plan_revision_request_cannot_alter_protected_identities(api_fixture) -> None:
    client, _, _, _ = api_fixture
    amendment_response = client.post(
        "/v1/jobs/job-1/plan-amendments",
        content=json.dumps(_payload()),
        headers=_MUTATION_HEADERS,
    )
    amendment_id = amendment_response.json()["amendment_id"]
    response = client.post(
        f"/v1/plan-amendments/{amendment_id}/revisions",
        content=json.dumps(_payload(command_id="forbidden")),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 422


def test_public_projection_redacts_paths_env_and_secrets(api_fixture) -> None:
    client, _, _, _ = api_fixture
    response = client.post(
        "/v1/jobs/job-1/plan-amendments",
        content=json.dumps(
            _payload(
                title=r"See C:\Users\secret\file.txt",
                summary="TOKEN=abc123 secret=xyz",
            )
        ),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    blob = json.dumps(data)
    assert r"C:\Users\secret\file.txt" not in blob
    assert "TOKEN=abc123" not in blob
    assert "secret=xyz" not in blob
    assert "[redacted-path]" in blob or "[redacted-env]" in blob
