"""API-level checksum gating tests for V2 approval endpoints."""

from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_approval_repository import (
    SqliteV2ApprovalRepository,
    V2ApprovalDecisionRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import V2StageCommandRecord
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import V2MigrationJobRecord


def _mutation_headers():
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID
    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _api_client(tmp_path: Path):
    from migration_factory.control_tower.adapters.fastapi import create_app
    conn = sqlite3.connect(
        tmp_path / "checksum_test.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    app = create_app(lambda: SqliteUnitOfWork(conn), v2_orchestrator_runner=_FakeResumeRunner())
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    return client, conn


class _FakeResumeRunner:
    def start_resume(self, *, job_id: str, resume_id: str):
        return None

    def start(self, *, job_id: str, command_id: str):
        return None


def _create_pending_card(conn: sqlite3.Connection, checksum: str = "chk-123") -> str:
    repo = SqliteV2ApprovalRepository(conn)
    now = utc_now_text()
    SqliteUnitOfWork(conn).v2_jobs.save(
        V2MigrationJobRecord(
            job_id="j-1",
            setup_id="setup-1",
            setup_checksum="setup-checksum",
            pipeline_id="springboot-216-to-356-java21-three-stage",
            stage_chain_json="[]",
            status="running",
            created_at=now,
            updated_at=now,
            correlation_id=None,
        )
    )
    SqliteUnitOfWork(conn).v2_commands.save(
        V2StageCommandRecord(
            command_id="cmd-approval",
            job_id="j-1",
            stage_index=1,
            manifest_checksum="manifest",
            argv_json=json.dumps(["python", "-m", "migration_factory.orchestrator.runner", "--run-id", "int-test", "--modernized", "/tmp/output"]),
            env_json="{}",
            status="manifest_ready",
            created_at=now,
            updated_at=now,
            result_json=None,
        )
    )
    card = V2ApprovalDecisionRecord(
        card_id=uuid4().hex,
        job_id="j-1",
        interrupt_id="int-test",
        request_checksum=checksum,
        stage_index=1,
        summary="Test approval",
        status="pending",
        created_at=now,
    )
    repo.save_card(card)
    return card.card_id


class TestChecksumAPI:

    def test_approve_with_correct_checksum(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        card_id = _create_pending_card(conn, "correct-checksum")

        response = client.post(
            f"/v1/v2/jobs/j-1/approvals/{card_id}/approve",
            json={"expected_checksum": "correct-checksum"},
            headers=_mutation_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "approved"

    def test_approve_with_wrong_checksum_rejected(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        card_id = _create_pending_card(conn, "correct-checksum")

        response = client.post(
            f"/v1/v2/jobs/j-1/approvals/{card_id}/approve",
            json={"expected_checksum": "wrong-checksum"},
            headers=_mutation_headers(),
        )
        assert response.status_code == 400
        assert "checksum" in response.text.lower() or "Checksum mismatch" in response.text

    def test_approve_after_reject_rejected(self, tmp_path: Path) -> None:
        """Once a card is rejected, approve should fail."""
        client, conn = _api_client(tmp_path)
        card_id = _create_pending_card(conn, "abc")

        # Reject first
        response = client.post(
            f"/v1/v2/jobs/j-1/approvals/{card_id}/reject",
            json={},
            headers=_mutation_headers(),
        )
        assert response.status_code == 200

        # Then try to approve
        response = client.post(
            f"/v1/v2/jobs/j-1/approvals/{card_id}/approve",
            json={"expected_checksum": "abc"},
            headers=_mutation_headers(),
        )
        assert response.status_code == 400
        assert "already" in response.text.lower()

    def test_approve_nonexistent_card(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/j-1/approvals/nonexistent/approve",
            json={"expected_checksum": "abc"},
            headers=_mutation_headers(),
        )
        assert response.status_code == 404
        assert "not found" in response.text.lower() or "CARD_NOT_FOUND" in response.text

    def test_reject_then_approve_with_extra_spaces(self, tmp_path: Path) -> None:
        """Checksum validation is exact — trailing spaces cause rejection."""
        client, conn = _api_client(tmp_path)
        card_id = _create_pending_card(conn, "exact-string")

        response = client.post(
            f"/v1/v2/jobs/j-1/approvals/{card_id}/approve",
            json={"expected_checksum": "exact-string "},  # trailing space
            headers=_mutation_headers(),
        )
        assert response.status_code == 400
        assert "checksum" in response.text.lower() or "mismatch" in response.text.lower()

    def test_approval_idempotency(self, tmp_path: Path) -> None:
        """Duplicate approve must succeed idempotently."""
        client, conn = _api_client(tmp_path)
        card_id = _create_pending_card(conn, "idempotent")

        # First approve — should succeed
        resp1 = client.post(
            f"/v1/v2/jobs/j-1/approvals/{card_id}/approve",
            json={"expected_checksum": "idempotent"},
            headers=_mutation_headers(),
        )
        assert resp1.status_code == 200

        # Second approve — idempotent, must succeed
        resp2 = client.post(
            f"/v1/v2/jobs/j-1/approvals/{card_id}/approve",
            json={"expected_checksum": "idempotent"},
            headers=_mutation_headers(),
        )
        assert resp2.status_code == 200
        assert resp2.json()["decision"] == "approved"
