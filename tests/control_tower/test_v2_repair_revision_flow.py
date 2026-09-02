"""PR-D: Focused tests for user-requested repair proposal revision flow."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.domain.checksums import sha256_canonical_json, utc_now_text
from migration_factory.control_tower.domain.entities import PhaseGateRecord
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteControlTowerUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import V2MigrationJobRecord
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
    V2RepairProposalRecord,
)
from migration_factory.control_tower.adapters.fastapi.app import create_app

FORBIDDEN_FIELDS = frozenset({
    "sandbox_path", "argv", "env", "raw_command", "endpoint",
    "deployment", "env_ref", "filesystem_target",
    "user_supplied_file_path", "target_path", "patch_content",
})
MIGRATION_DIR = Path(__file__).resolve().parent.parent.parent / "migration_factory" / "control_tower" / "infrastructure" / "sqlite" / "migrations"

POST_HEADERS = {
    "host": "127.0.0.1:8000",
    "origin": "http://127.0.0.1:3000",
    "X-Control-Tower-Client": "control-tower-frontend",
    "content-type": "application/json",
}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test_revision_flow.sqlite3"
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn, migrations_dir=MIGRATION_DIR)
    return conn


def _make_simple_diff_text() -> str:
    return (
        "diff --git a/src/App.java b/src/App.java\n"
        "--- a/src/App.java\n"
        "+++ b/src/App.java\n"
        "@@ -1,3 +1,4 @@\n"
        " class App {\n"
        "-    String mode = \"old\";\n"
        "+    String mode = \"new\";\n"
        " }\n"
    )


def _write_diff(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "final_reviewed_repair.diff"
    path.write_text(content, encoding="utf-8", newline="")
    return path


def _make_new_style_record(
    **kwargs,
) -> V2RepairProposalRecord:
    defaults = dict(
        proposal_id=uuid4().hex,
        command_id="cmd-1",
        failure_summary="Build failed in sandbox",
        hypothesis="Dependency version mismatch",
        patch_summary="Align spring-boot version",
        affected_paths_json='["pom.xml"]',
        status="user_review_required",
        approval_checksum=None,
        created_at=utc_now_text(),
        proposal_checksum="sha256:proposal-check",
        job_id="job-1",
        route_step_index=1,
        attempt_number=1,
        revision_number=None,
        diff_ref=None,
        diff_checksum=None,
        reviewer_verdict_id=None,
        reviewer_verdict_ref=None,
        reviewer_output_checksum="sha256:reviewer-out",
        policy_validation_checksum="sha256:policy-check",
        gate_id=None,
        status_reason=None,
        diagnosis_ref=None,
        repair_plan_ref=None,
        failure_evidence_ref=None,
        repair_context_ref=None,
        safe_diff_preview_ref=None,
    )
    defaults.update(kwargs)
    return V2RepairProposalRecord(**defaults)


def _make_job_record(**kwargs) -> V2MigrationJobRecord:
    defaults = dict(
        job_id="job-1",
        setup_id="setup-1",
        setup_checksum="sha256:setup",
        pipeline_id="pipeline-1",
        stage_chain_json="[]",
        status="started",
        created_at=utc_now_text(),
        updated_at=utc_now_text(),
        correlation_id=None,
    )
    defaults.update(kwargs)
    return V2MigrationJobRecord(**defaults)


def _make_gate_record(**kwargs) -> PhaseGateRecord:
    refs_json = json.dumps([
        "failure_evidence_checksum:sha256:ev",
        "context_pack_checksum:sha256:ctx",
        "primary_output_checksum:sha256:primary",
        "reviewer_output_checksum:sha256:reviewer",
        "final_reviewed_diff_checksum:sha256:diff",
        "policy_validation_checksum:sha256:policy",
        "base_repo_state_checksum:sha256:base",
    ], separators=(",", ":"))
    defaults = dict(
        gate_id=uuid4().hex,
        job_id="job-1",
        gate_phase="repair_review",
        stage_index=1,
        gate_status="open",
        gate_decision="",
        source_artifact_checksum="sha256:source",
        resolved_artifact_checksum=None,
        source_artifact_refs_json=refs_json,
        created_at=utc_now_text(),
        resolved_at=None,
        resolved_by=None,
    )
    defaults.update(kwargs)
    return PhaseGateRecord(**defaults)


def _build_app_and_data(
    conn: sqlite3.Connection,
    tmp_path: Path,
    *,
    job_id: str = "job-1",
    proposal_status: str = "user_review_required",
    gate_status: str = "open",
) -> tuple[TestClient, dict[str, str]]:
    diff_path = _write_diff(tmp_path, _make_simple_diff_text())
    diff_checksum = sha256_canonical_json({"unified_diff": _make_simple_diff_text()})
    reviewer_verdict_id = uuid4().hex
    gate_id = uuid4().hex
    proposal_id = uuid4().hex

    with SqliteControlTowerUnitOfWork(conn) as uow:
        uow.v2_jobs.save(_make_job_record(job_id=job_id))
        uow.phase_gates.save(_make_gate_record(
            gate_id=gate_id, job_id=job_id, gate_status=gate_status,
        ))
        uow.v2_repairs.save_proposal(_make_new_style_record(
            job_id=job_id,
            proposal_id=proposal_id,
            status=proposal_status,
            diff_ref=str(diff_path),
            diff_checksum=diff_checksum,
            reviewer_verdict_id=reviewer_verdict_id,
            gate_id=gate_id,
        ))

    app = create_app(lambda: SqliteControlTowerUnitOfWork(conn))
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    refs = {
        "diff_checksum": diff_checksum,
        "reviewer_verdict_id": reviewer_verdict_id,
        "gate_id": gate_id,
        "proposal_id": proposal_id,
    }
    return client, refs


class TestHttpEndpointRevise:
    """Tests for the POST /v1/v2/jobs/{job_id}/repair/proposals/{proposal_id}/revise endpoint."""

    def test_revise_rejects_wrong_job(self, conn: sqlite3.Connection) -> None:
        app = create_app(lambda: SqliteControlTowerUnitOfWork(conn))
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        resp = client.post(
            "/v1/v2/jobs/wrong-job/repair/proposals/nonexistent/revise",
            json={"user_instruction": "Fix it", "previous_diff_checksum": "sha256:abc", "previous_reviewer_verdict_id": "verdict-1"},
            headers=POST_HEADERS,
        )
        assert resp.status_code == 404

    def test_revise_rejects_missing_proposal(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        with SqliteControlTowerUnitOfWork(conn) as uow:
            uow.v2_jobs.save(_make_job_record())
        app = create_app(lambda: SqliteControlTowerUnitOfWork(conn))
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        resp = client.post(
            "/v1/v2/jobs/job-1/repair/proposals/nonexistent/revise",
            json={"user_instruction": "Fix it", "previous_diff_checksum": "sha256:abc", "previous_reviewer_verdict_id": "verdict-1"},
            headers=POST_HEADERS,
        )
        assert resp.status_code == 404

    def test_revise_rejects_stale_diff_checksum(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        client, refs = _build_app_and_data(conn, tmp_path)
        resp = client.post(
            f"/v1/v2/jobs/job-1/repair/proposals/{refs['proposal_id']}/revise",
            json={
                "user_instruction": "Fix it",
                "previous_diff_checksum": "sha256:wrong-checksum",
                "previous_reviewer_verdict_id": refs["reviewer_verdict_id"],
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 409
        data = resp.json()
        assert "STALE_DIFF_CHECKSUM" in str(data.get("detail") or data.get("error", {}))

    def test_revise_rejects_wrong_reviewer_verdict(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        client, refs = _build_app_and_data(conn, tmp_path)
        resp = client.post(
            f"/v1/v2/jobs/job-1/repair/proposals/{refs['proposal_id']}/revise",
            json={
                "user_instruction": "Fix it",
                "previous_diff_checksum": refs["diff_checksum"],
                "previous_reviewer_verdict_id": "wrong-verdict-id",
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 409
        data = resp.json()
        assert "STALE_REVIEWER_VERDICT" in str(data.get("detail") or data.get("error", {}))

    def test_revise_validates_non_empty_instruction(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        client, refs = _build_app_and_data(conn, tmp_path)
        resp = client.post(
            f"/v1/v2/jobs/job-1/repair/proposals/{refs['proposal_id']}/revise",
            json={
                "user_instruction": "",
                "previous_diff_checksum": refs["diff_checksum"],
                "previous_reviewer_verdict_id": refs["reviewer_verdict_id"],
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 422

    def test_revise_rejects_forbidden_instruction_content(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        client, refs = _build_app_and_data(conn, tmp_path)
        resp = client.post(
            f"/v1/v2/jobs/job-1/repair/proposals/{refs['proposal_id']}/revise",
            json={
                "user_instruction": "Change the sandbox_path value",
                "previous_diff_checksum": refs["diff_checksum"],
                "previous_reviewer_verdict_id": refs["reviewer_verdict_id"],
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 422
        data = resp.json()
        assert "FORBIDDEN_INSTRUCTION_CONTENT" in str(data.get("detail") or data.get("error", {}))

    def test_revise_rejects_extra_fields(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        client, refs = _build_app_and_data(conn, tmp_path)
        resp = client.post(
            f"/v1/v2/jobs/job-1/repair/proposals/{refs['proposal_id']}/revise",
            json={
                "user_instruction": "Fix it",
                "previous_diff_checksum": refs["diff_checksum"],
                "previous_reviewer_verdict_id": refs["reviewer_verdict_id"],
                "target_path": "src/App.java",
                "patch_content": "diff content",
                "sandbox_path": "/tmp/sandbox",
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 422

    def test_revise_response_has_no_forbidden_fields(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        client, refs = _build_app_and_data(conn, tmp_path)
        resp = client.post(
            f"/v1/v2/jobs/job-1/repair/proposals/{refs['proposal_id']}/revise",
            json={
                "user_instruction": "Fix the dependency version",
                "previous_diff_checksum": refs["diff_checksum"],
                "previous_reviewer_verdict_id": refs["reviewer_verdict_id"],
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
        serialized = json.dumps(resp.json())
        for forbidden in FORBIDDEN_FIELDS:
            assert forbidden not in serialized, f"Forbidden field {forbidden!r} found in response"

    def test_revise_rejects_superseded_proposal(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        client, refs = _build_app_and_data(conn, tmp_path, proposal_status="superseded")
        resp = client.post(
            f"/v1/v2/jobs/job-1/repair/proposals/{refs['proposal_id']}/revise",
            json={
                "user_instruction": "Fix it",
                "previous_diff_checksum": refs["diff_checksum"],
                "previous_reviewer_verdict_id": refs["reviewer_verdict_id"],
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 409
        data = resp.json()
        assert "PROPOSAL_ALREADY_FINAL" in str(data.get("detail") or data.get("error", {}))

    def test_revise_happy_path(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        client, refs = _build_app_and_data(conn, tmp_path)
        resp = client.post(
            f"/v1/v2/jobs/job-1/repair/proposals/{refs['proposal_id']}/revise",
            json={
                "user_instruction": "Fix the dependency version",
                "previous_diff_checksum": refs["diff_checksum"],
                "previous_reviewer_verdict_id": refs["reviewer_verdict_id"],
                "idempotency_key": "idem-1",
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
        data = resp.json()
        assert data["job_id"] == "job-1"
        assert "previous_proposal_id" in data
        assert data["previous_proposal_id"] == refs["proposal_id"]
        assert data["status"] in ("revision_requested", "user_review_required")
        assert "event_ids" in data
        assert isinstance(data["event_ids"], list)
        assert "artifact_refs" in data
        assert isinstance(data["artifact_refs"], dict)

    def test_revise_rejects_closed_gate(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        client, refs = _build_app_and_data(conn, tmp_path, gate_status="closed")
        resp = client.post(
            f"/v1/v2/jobs/job-1/repair/proposals/{refs['proposal_id']}/revise",
            json={
                "user_instruction": "Fix it",
                "previous_diff_checksum": refs["diff_checksum"],
                "previous_reviewer_verdict_id": refs["reviewer_verdict_id"],
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 409
        data = resp.json()
        assert "GATE_NOT_OPEN" in str(data.get("detail") or data.get("error", {}))

    def test_revise_rejects_proposal_no_diff(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        with SqliteControlTowerUnitOfWork(conn) as uow:
            uow.v2_jobs.save(_make_job_record())
            gate_id = uuid4().hex
            uow.phase_gates.save(_make_gate_record(gate_id=gate_id, job_id="job-1"))
            uow.v2_repairs.save_proposal(_make_new_style_record(
                job_id="job-1",
                proposal_id=uuid4().hex,
                status="user_review_required",
                diff_ref=None,
                diff_checksum=None,
                reviewer_verdict_id=uuid4().hex,
                gate_id=gate_id,
            ))
        app = create_app(lambda: SqliteControlTowerUnitOfWork(conn))
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        resp = client.post(
            "/v1/v2/jobs/job-1/repair/proposals/no-diff-id/revise",
            json={
                "user_instruction": "Fix it",
                "previous_diff_checksum": "sha256:abc",
                "previous_reviewer_verdict_id": "verdict-1",
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 404

    def test_revise_rejects_proposal_no_gate_id(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        with SqliteControlTowerUnitOfWork(conn) as uow:
            uow.v2_jobs.save(_make_job_record())
            uow.v2_repairs.save_proposal(_make_new_style_record(
                job_id="job-1",
                proposal_id=uuid4().hex,
                status="user_review_required",
                diff_ref="/tmp/some.diff",
                diff_checksum="sha256:abc",
                reviewer_verdict_id=uuid4().hex,
                gate_id=None,
            ))
        app = create_app(lambda: SqliteControlTowerUnitOfWork(conn))
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        resp = client.post(
            "/v1/v2/jobs/job-1/repair/proposals/no-gate-id/revise",
            json={
                "user_instruction": "Fix it",
                "previous_diff_checksum": "sha256:abc",
                "previous_reviewer_verdict_id": "verdict-1",
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 404

    def test_revise_rejects_expected_gate_checksum_mismatch(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        client, refs = _build_app_and_data(conn, tmp_path)
        resp = client.post(
            f"/v1/v2/jobs/job-1/repair/proposals/{refs['proposal_id']}/revise",
            json={
                "user_instruction": "Fix it",
                "previous_diff_checksum": refs["diff_checksum"],
                "previous_reviewer_verdict_id": refs["reviewer_verdict_id"],
                "expected_gate_checksum": "sha256:wrong-checksum",
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 409
        data = resp.json()
        assert "STALE_GATE_CHECKSUM" in str(data.get("detail") or data.get("error", {}))

    def test_revise_creates_event_on_success(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        client, refs = _build_app_and_data(conn, tmp_path)
        resp = client.post(
            f"/v1/v2/jobs/job-1/repair/proposals/{refs['proposal_id']}/revise",
            json={
                "user_instruction": "Fix the dependency version",
                "previous_diff_checksum": refs["diff_checksum"],
                "previous_reviewer_verdict_id": refs["reviewer_verdict_id"],
            },
            headers=POST_HEADERS,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
        data = resp.json()
        assert len(data["event_ids"]) > 0, "Expected at least one event ID on success"
        with SqliteControlTowerUnitOfWork(conn) as uow:
            events = uow.v2_events.list_by_job("job-1")
            assert any(e.type == "repair_revision_requested" for e in events)
