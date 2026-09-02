from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi.app import create_app
from migration_factory.control_tower.application.context_packs import ContextPackManifestService
from migration_factory.control_tower.application.plan_amendments import PlanAmendmentService, PlanChange
from migration_factory.control_tower.application.services import ModelInvocationAuditService
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import seed_job


_MUTATION_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "http://127.0.0.1:3000",
    "X-Control-Tower-Client": "control-tower-frontend",
}


@pytest.fixture
def api_fixture(tmp_path: Path) -> tuple[TestClient, sqlite3.Connection, str]:
    db_path = tmp_path / "v1_12b_api.db"
    connection = sqlite3.connect(str(db_path), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    apply_pending_migrations(connection)
    seed_job(connection)
    connection.commit()

    amendments = PlanAmendmentService(lambda: SqliteUnitOfWork(connection))
    amendment = amendments.create_amendment(
        job_id="job-1",
        source_kind="manual",
        title="Operator request",
        summary="Need safer advisory wording only",
        notes=("safe",),
        changes=(PlanChange(stage_index=1, change_type="documentation", description="Clarify checklist"),),
        created_by="tester",
    )
    ModelInvocationAuditService(lambda: SqliteUnitOfWork(connection)).record_invocation(
        invocation_id="inv-1",
        job_id="job-1",
        profile_id="default-fake",
        provider_kind="fake",
        model_name="fake-provider",
        redacted_summary="Safe invocation",
        actor_type="system",
        actor_id="tester",
    )
    ContextPackManifestService(lambda: SqliteUnitOfWork(connection)).persist_manifest(
        manifest_id="cp-1",
        pack_type="plan",
        pack_version="1.0",
        title="Plan context",
        job_id="job-1",
        created_by="tester",
    )

    client = TestClient(
        create_app(lambda: SqliteUnitOfWork(connection)),
        base_url="http://127.0.0.1:8000",
    )
    return client, connection, amendment.amendment_id


def _payload(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Fake-provider proposal",
        "summary": "Documentation-only advisory proposal",
        "notes": ["safe"],
        "changes": [
            {
                "stage_index": 1,
                "change_type": "documentation",
                "description": "Clarify operator checklist",
                "rationale": "No execution change",
            }
        ],
        "confidence_label": "high",
        "confidence_score": 0.9,
        "model_invocation_id": "inv-1",
        "context_pack_manifest_id": "cp-1",
    }
    payload.update(overrides)
    return payload


def test_api_projection_returns_pass_and_safe_summary_only(api_fixture) -> None:
    client, _, amendment_id = api_fixture

    response = client.post(
        f"/v1/plan-amendments/{amendment_id}/fake-provider-proposals",
        content=json.dumps(_payload()),
        headers=_MUTATION_HEADERS,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["validation_status"] == "PASS"
    assert data["revision_persisted"] is True
    assert data["source_kind"] == "fake_provider"
    assert data["revision_id"] is not None
    blob = json.dumps(data)
    assert "prompt" not in blob
    assert "provider_response" not in blob
    assert "raw_model_output" not in blob
    assert "deployment_id" not in blob


def test_public_projection_redacts_path_env_and_trace_like_content(api_fixture) -> None:
    client, _, amendment_id = api_fixture

    response = client.post(
        f"/v1/plan-amendments/{amendment_id}/fake-provider-proposals",
        content=json.dumps(_payload(summary="C:\\Users\\secret\\data.txt TOKEN=abc123 trace=Traceback ...")),
        headers=_MUTATION_HEADERS,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["validation_status"] == "FAILED"
    blob = json.dumps(data)
    assert r"C:\Users\secret\data.txt" not in blob
    assert "TOKEN=abc123" not in blob
    assert "Traceback" not in blob


def test_get_validation_report_projection_uses_persisted_safe_summary(api_fixture) -> None:
    client, _, amendment_id = api_fixture
    create_response = client.post(
        f"/v1/plan-amendments/{amendment_id}/fake-provider-proposals",
        content=json.dumps(_payload()),
        headers=_MUTATION_HEADERS,
    )
    revision_id = create_response.json()["revision_id"]

    response = client.get(f"/v1/plan-revisions/{revision_id}/advisory-validation")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["revision_id"] == revision_id
    assert data["validation_status"] == "PASS"
    assert data["redacted_summary"]["non_authoritative"] is True


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "route",
        "ledger_id",
        "command_id",
        "arguments",
        "approval_id",
        "artifact_id",
        "target_proof_level",
        "achieved_proof_level",
    ],
)
def test_api_rejects_authority_fields_from_browser_payload(api_fixture, forbidden_field: str) -> None:
    client, _, amendment_id = api_fixture

    response = client.post(
        f"/v1/plan-amendments/{amendment_id}/fake-provider-proposals",
        content=json.dumps(_payload(**{forbidden_field: "forbidden"})),
        headers=_MUTATION_HEADERS,
    )

    assert response.status_code == 422


def test_api_call_does_not_mutate_source_sandbox_or_job_state(api_fixture, tmp_path: Path) -> None:
    client, connection, amendment_id = api_fixture
    source_dir = tmp_path / "source"
    sandbox_dir = tmp_path / "sandbox"
    source_dir.mkdir()
    sandbox_dir.mkdir()
    (source_dir / "main.txt").write_text("source-stable", encoding="utf-8")
    (sandbox_dir / "work.txt").write_text("sandbox-stable", encoding="utf-8")
    before_source = (source_dir / "main.txt").read_text(encoding="utf-8")
    before_sandbox = (sandbox_dir / "work.txt").read_text(encoding="utf-8")
    before_job = connection.execute(
        "SELECT version, status, last_event_sequence FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()

    response = client.post(
        f"/v1/plan-amendments/{amendment_id}/fake-provider-proposals",
        content=json.dumps(_payload()),
        headers=_MUTATION_HEADERS,
    )

    assert response.status_code == 200, response.text
    assert (source_dir / "main.txt").read_text(encoding="utf-8") == before_source
    assert (sandbox_dir / "work.txt").read_text(encoding="utf-8") == before_sandbox
    after_job = connection.execute(
        "SELECT version, status, last_event_sequence FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()
    assert tuple(before_job) == tuple(after_job)
