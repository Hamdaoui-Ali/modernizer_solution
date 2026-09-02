from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi.app import create_app
from migration_factory.control_tower.application.plan_amendments import PlanAmendmentService, PlanChange
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import seed_job


_MUTATION_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "http://127.0.0.1:3000",
    "X-Control-Tower-Client": "control-tower-frontend",
}


@pytest.fixture
def api_fixture(tmp_path: Path) -> tuple[TestClient, sqlite3.Connection, str, str, Path, Path]:
    db_path = tmp_path / "v1_13_api.db"
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

    service = PlanAmendmentService(lambda: SqliteUnitOfWork(connection))
    amendment = service.create_amendment(
        job_id="job-1",
        source_kind="manual",
        title="Plan request",
        summary="Plan only",
        notes=("safe",),
        changes=(PlanChange(stage_index=1, change_type="documentation", description="Clarify plan"),),
        created_by="tester",
    )
    revision = service.create_revision(
        amendment_id=amendment.amendment_id,
        source_kind="fake_provider",
        title="Proposal",
        summary="Candidate revision",
        notes=("safe",),
        changes=(PlanChange(stage_index=1, change_type="documentation", description="Clarify plan"),),
        created_by="tester",
    )

    client = TestClient(
        create_app(lambda: SqliteUnitOfWork(connection)),
        base_url="http://127.0.0.1:8000",
    )
    return client, connection, revision.revision_id, revision.payload_checksum, source_dir, sandbox_dir


def _payload(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "expected_checksum": "checksum-placeholder",
        "decision": "approved",
        "review_summary": "Safe approval",
    }
    payload.update(overrides)
    return payload


def _snapshot_directory(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file()}


def test_public_review_status_is_redacted_and_approved_status_is_eligible(api_fixture) -> None:
    client, _, revision_id, checksum, _, _ = api_fixture
    response = client.post(
        f"/v1/plan-revisions/{revision_id}/review-decisions",
        content=json.dumps(_payload(expected_checksum=checksum, review_summary=r"See C:\Users\secret TOKEN=abc")),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 200, response.text
    blob = json.dumps(response.json())
    assert r"C:\Users\secret" not in blob
    assert "TOKEN=abc" not in blob

    status_response = client.get(f"/v1/plan-revisions/{revision_id}/review-status")
    assert status_response.status_code == 200, status_response.text
    data = status_response.json()
    assert data["eligible_for_downstream"] is True
    assert data["status"] == "approved"


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "route",
        "ledger_id",
        "command_id",
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
def test_browser_cannot_inject_authority_fields(api_fixture, forbidden_field: str) -> None:
    client, _, revision_id, checksum, _, _ = api_fixture
    response = client.post(
        f"/v1/plan-revisions/{revision_id}/review-decisions",
        content=json.dumps(_payload(expected_checksum=checksum, **{forbidden_field: "forbidden"})),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 422


def test_review_endpoint_does_not_mutate_source_sandbox_or_job_state(api_fixture) -> None:
    client, connection, revision_id, checksum, source_dir, sandbox_dir = api_fixture
    before_source = _snapshot_directory(source_dir)
    before_sandbox = _snapshot_directory(sandbox_dir)
    before_job = connection.execute(
        "SELECT status, version, last_event_sequence FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()
    before_stage_runs = connection.execute("SELECT COUNT(*) FROM stage_runs WHERE job_id = 'job-1'").fetchone()[0]

    response = client.post(
        f"/v1/plan-revisions/{revision_id}/review-decisions",
        content=json.dumps(_payload(expected_checksum=checksum)),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert _snapshot_directory(source_dir) == before_source
    assert _snapshot_directory(sandbox_dir) == before_sandbox
    after_job = connection.execute(
        "SELECT status, version, last_event_sequence FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()
    assert tuple(before_job) == tuple(after_job)
    after_stage_runs = connection.execute("SELECT COUNT(*) FROM stage_runs WHERE job_id = 'job-1'").fetchone()[0]
    assert after_stage_runs == before_stage_runs


def test_stale_checksum_api_rejected(api_fixture) -> None:
    client, _, revision_id, _, _, _ = api_fixture
    response = client.post(
        f"/v1/plan-revisions/{revision_id}/review-decisions",
        content=json.dumps(_payload(expected_checksum="stale-checksum")),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PLAN_REVIEW_STALE_CHECKSUM"
