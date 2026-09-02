"""PR-F: Focused tests for repair attempt history (retry/validation/rollback)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from migration_factory.control_tower.application.v2_repair_projection import (
    record_to_attempt_summary,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteControlTowerUnitOfWork, SqliteUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import V2MigrationJobRecord
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
    SqliteV2RepairRepository,
    V2RepairProposalRecord,
)
from migration_factory.control_tower.adapters.fastapi.app import create_app
from fastapi.testclient import TestClient

FORBIDDEN_FIELDS = frozenset({
    "sandbox_path", "argv", "env", "raw_command", "endpoint",
    "deployment", "env_ref", "filesystem_target",
    "user_supplied_file_path", "target_path", "patch_content",
    "patch_text", "secret",
})
MIGRATION_DIR = Path(__file__).resolve().parent.parent.parent / "migration_factory" / "control_tower" / "infrastructure" / "sqlite" / "migrations"


def _connection(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test_attempt_history.sqlite3"
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn, migrations_dir=MIGRATION_DIR)
    return conn


def _make_attempt_record(
    *,
    job_id: str = "job-1",
    proposal_id: str | None = None,
    attempt_number: int = 1,
    status: str = "user_review_required",
    apply_status: str | None = None,
    rerun_status: str | None = None,
    rollback_status: str | None = None,
    remaining_attempts: int | None = None,
    next_gate_id: str | None = None,
    next_gate_status: str | None = None,
    completed_at: str | None = None,
    diff_checksum: str | None = None,
    gate_id: str | None = None,
    status_reason: str | None = None,
    reviewer_decision: str | None = None,
) -> V2RepairProposalRecord:
    return V2RepairProposalRecord(
        proposal_id=proposal_id or uuid4().hex,
        command_id="cmd-1",
        failure_summary="Build failed",
        hypothesis="Dependency mismatch",
        patch_summary="Align version",
        affected_paths_json='["test.txt"]',
        status=status,
        approval_checksum=None,
        created_at=utc_now_text(),
        proposal_checksum="sha256:proposal",
        job_id=job_id,
        route_step_index=1,
        attempt_number=attempt_number,
        revision_number=None,
        diff_ref=str(Path("/tmp/diff")),
        diff_checksum=diff_checksum or "sha256:diff",
        reviewer_verdict_id="verdict-1",
        reviewer_verdict_ref=None,
        reviewer_output_checksum="sha256:reviewer",
        policy_validation_checksum="sha256:policy",
        gate_id=gate_id or "gate-1",
        status_reason=status_reason,
        diagnosis_ref=None,
        repair_plan_ref=None,
        failure_evidence_ref=None,
        repair_context_ref=None,
        safe_diff_preview_ref=None,
        apply_status=apply_status,
        rerun_status=rerun_status,
        rollback_status=rollback_status,
        validation_result_ref=None,
        next_gate_id=next_gate_id,
        next_gate_status=next_gate_status,
        remaining_attempts=remaining_attempts,
        completed_at=completed_at,
        reviewer_decision=reviewer_decision,
    )


def _make_job_record(job_id: str = "job-1") -> V2MigrationJobRecord:
    return V2MigrationJobRecord(
        job_id=job_id,
        setup_id="setup-1",
        setup_checksum="sha256:setup",
        pipeline_id="pipeline-1",
        stage_chain_json="[]",
        status="started",
        created_at=utc_now_text(),
        updated_at=utc_now_text(),
        correlation_id=None,
    )


def _api_client(conn: sqlite3.Connection) -> TestClient:
    return TestClient(
        create_app(lambda: SqliteControlTowerUnitOfWork(conn)),
        base_url="http://127.0.0.1:8000",
    )


# ── Tests ──────────────────────────────────────────────────────────


class TestRecordToAttemptSummary:
    """Tests 1-6: record_to_attempt_summary includes all PR-F fields."""

    def test_attempt_history_includes_validation_pass_result(self, tmp_path: Path) -> None:
        """Test 1: Attempt summary includes passed rerun_status."""
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(
            attempt_number=1,
            status="approved_applied",
            apply_status="APPLIED",
            rerun_status="passed",
            rollback_status="",
            remaining_attempts=3,
            completed_at=utc_now_text(),
        )
        repo.save_proposal(record)
        loaded = repo.get_proposal(record.proposal_id)
        summary = record_to_attempt_summary(loaded)
        assert summary["status"] == "approved_applied"
        assert summary["apply_status"] == "APPLIED"
        assert summary["rerun_status"] == "passed"
        assert summary["remaining_attempts"] == 3
        assert summary["completed_at"] is not None

    def test_attempt_history_includes_validation_failure_result(self, tmp_path: Path) -> None:
        """Test 2: Attempt summary includes failed rerun_status."""
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(
            attempt_number=1,
            status="approve_failed",
            apply_status="APPLIED",
            rerun_status="failed",
            rollback_status="rolled_back",
            remaining_attempts=2,
            completed_at=utc_now_text(),
        )
        repo.save_proposal(record)
        loaded = repo.get_proposal(record.proposal_id)
        summary = record_to_attempt_summary(loaded)
        assert summary["rerun_status"] == "failed"
        assert summary["rollback_status"] == "rolled_back"

    def test_attempt_history_includes_rollback_status(self, tmp_path: Path) -> None:
        """Test 3: Attempt summary includes rollback status."""
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(
            attempt_number=2,
            status="approve_failed",
            apply_status="APPLIED",
            rerun_status="failed",
            rollback_status="rollback_failed",
            remaining_attempts=1,
            completed_at=utc_now_text(),
        )
        repo.save_proposal(record)
        loaded = repo.get_proposal(record.proposal_id)
        summary = record_to_attempt_summary(loaded)
        assert summary["rollback_status"] == "rollback_failed"
        assert summary["rerun_status"] == "failed"

    def test_attempt_history_includes_next_gate_after_validation_failure(self, tmp_path: Path) -> None:
        """Test 4: After validation failure, next gate info appears."""
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(
            attempt_number=1,
            status="approve_failed",
            apply_status="APPLIED",
            rerun_status="failed",
            rollback_status="rolled_back",
            remaining_attempts=2,
            next_gate_id="next-gate-2",
            next_gate_status="repair_gate_created",
            completed_at=utc_now_text(),
        )
        repo.save_proposal(record)
        loaded = repo.get_proposal(record.proposal_id)
        summary = record_to_attempt_summary(loaded)
        assert summary["next_gate_id"] == "next-gate-2"
        assert summary["next_gate_status"] == "repair_gate_created"

    def test_attempt_history_shows_remaining_attempts(self, tmp_path: Path) -> None:
        """Test 5: Remaining attempts visible after validation failure."""
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(
            attempt_number=1,
            status="approve_failed",
            remaining_attempts=2,
            completed_at=utc_now_text(),
        )
        repo.save_proposal(record)
        loaded = repo.get_proposal(record.proposal_id)
        summary = record_to_attempt_summary(loaded)
        assert summary["remaining_attempts"] == 2

    def test_exhausted_state_appears_when_retry_budget_exhausted(self, tmp_path: Path) -> None:
        """Test 6: Exhausted state with 0 remaining attempts."""
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(
            attempt_number=3,
            status="exhausted",
            apply_status="APPLIED",
            rerun_status="failed",
            rollback_status="rolled_back",
            remaining_attempts=0,
            completed_at=utc_now_text(),
            status_reason="All 3 repair attempts exhausted",
        )
        repo.save_proposal(record)
        loaded = repo.get_proposal(record.proposal_id)
        summary = record_to_attempt_summary(loaded)
        assert summary["status"] == "exhausted"
        assert summary["remaining_attempts"] == 0
        assert "exhausted" in summary["status_reason"].lower()


class TestOldRecordCompatibility:
    """Test 7: Old records without PR-F fields load safely."""

    def test_old_attempts_without_prf_fields_still_load_safely(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        old = V2RepairProposalRecord(
            proposal_id=uuid4().hex,
            command_id="cmd-old",
            failure_summary="Old failure",
            hypothesis="Old hypothesis",
            patch_summary="Old patch",
            affected_paths_json='["pom.xml"]',
            status="draft",
            approval_checksum=None,
            created_at="2026-01-01T00:00:00Z",
        )
        repo.save_proposal(old)
        loaded = repo.get_proposal(old.proposal_id)
        assert loaded is not None
        assert loaded.proposal_id == old.proposal_id
        assert getattr(loaded, "apply_status", None) is None
        assert getattr(loaded, "rerun_status", None) is None
        assert getattr(loaded, "rollback_status", None) is None
        assert getattr(loaded, "remaining_attempts", None) is None
        assert getattr(loaded, "completed_at", None) is None
        # record_to_attempt_summary should still work
        summary = record_to_attempt_summary(loaded)
        assert summary["proposal_id"] == old.proposal_id
        assert summary["apply_status"] is None
        assert summary["rerun_status"] is None
        assert summary["rollback_status"] is None
        assert summary["remaining_attempts"] is None
        assert summary["completed_at"] is None


class TestJobScoping:
    """Test 8: Wrong job cannot see attempts."""

    def test_wrong_job_cannot_see_attempts(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        uow = SqliteControlTowerUnitOfWork(conn)
        uow.v2_jobs.save(_make_job_record("job-A"))
        uow.v2_jobs.save(_make_job_record("job-B"))
        record = _make_attempt_record(job_id="job-A", attempt_number=1)
        repo.save_proposal(record)
        client = _api_client(conn)
        resp = client.get(
            "/v1/v2/jobs/job-B/repair/attempts",
            headers={"host": "127.0.0.1:8000"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["attempts"]) == 0
        resp_a = client.get(
            "/v1/v2/jobs/job-A/repair/attempts",
            headers={"host": "127.0.0.1:8000"},
        )
        data_a = resp_a.json()
        assert len(data_a["attempts"]) == 1


class TestForbiddenFields:
    """Tests 9-10: No forbidden fields in response."""

    def test_response_has_no_forbidden_fields(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        uow = SqliteControlTowerUnitOfWork(conn)
        uow.v2_jobs.save(_make_job_record("job-safe"))
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(job_id="job-safe", attempt_number=1)
        repo.save_proposal(record)
        client = _api_client(conn)
        resp = client.get(
            "/v1/v2/jobs/job-safe/repair/attempts",
            headers={"host": "127.0.0.1:8000"},
        )
        data = resp.json()
        serialized = json.dumps(data)
        for forbidden in FORBIDDEN_FIELDS:
            assert forbidden not in serialized, f"Forbidden field {forbidden!r} found in response"

    def test_no_raw_patch_path_env_argv_command_leaked(self, tmp_path: Path) -> None:
        """Test 10: No raw patch/path/env/argv/command in attempt responses."""
        conn = _connection(tmp_path)
        uow = SqliteControlTowerUnitOfWork(conn)
        uow.v2_jobs.save(_make_job_record("job-safe-2"))
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(job_id="job-safe-2", attempt_number=1)
        repo.save_proposal(record)
        client = _api_client(conn)
        resp = client.get(
            "/v1/v2/jobs/job-safe-2/repair/attempts",
            headers={"host": "127.0.0.1:8000"},
        )
        data = resp.json()
        serialized = json.dumps(data)
        for pattern in ("target_path", "patch_content", "sandbox_path", "argv", "env", "raw_command", "C:\\"):
            assert pattern not in serialized, f"Forbidden pattern {pattern!r} found in response"


class TestRepairRepositoryMethods:
    """Test new repository methods."""

    def test_update_proposal_prf_fields(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(attempt_number=1)
        repo.save_proposal(record)
        repo.update_proposal_prf_fields(
            record.proposal_id,
            status="approved_applied",
            apply_status="APPLIED",
            rerun_status="passed",
            remaining_attempts=3,
        )
        loaded = repo.get_proposal(record.proposal_id)
        assert loaded.status == "approved_applied"
        assert loaded.apply_status == "APPLIED"
        assert loaded.rerun_status == "passed"
        assert loaded.remaining_attempts == 3

    def test_update_proposal_prf_fields_rejects_unknown_fields(self) -> None:
        conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        apply_pending_migrations(conn, migrations_dir=MIGRATION_DIR)
        repo = SqliteV2RepairRepository(conn)
        with pytest.raises(ValueError, match="Unknown PR-F fields"):
            repo.update_proposal_prf_fields("prop-1", invalid_field="bad")

    def test_update_proposal_prf_fields_empty_noop(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(attempt_number=1)
        repo.save_proposal(record)
        repo.update_proposal_prf_fields(record.proposal_id)
        loaded = repo.get_proposal(record.proposal_id)
        assert loaded is not None
        assert loaded.status == "user_review_required"


class TestAttemptEndpoint:
    """End-to-end tests for the attempts endpoint."""

    def test_attempts_endpoint_returns_enriched_summaries(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        uow = SqliteControlTowerUnitOfWork(conn)
        uow.v2_jobs.save(_make_job_record("job-enriched"))
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(
            job_id="job-enriched",
            attempt_number=1,
            status="approved_applied",
            apply_status="APPLIED",
            rerun_status="passed",
            rollback_status="",
            remaining_attempts=3,
            next_gate_id="gate-completion",
            next_gate_status="stage_completion_gate_created",
            completed_at=utc_now_text(),
        )
        repo.save_proposal(record)
        client = _api_client(conn)
        resp = client.get(
            "/v1/v2/jobs/job-enriched/repair/attempts",
            headers={"host": "127.0.0.1:8000"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["attempts"]) == 1
        a = data["attempts"][0]
        assert a["apply_status"] == "APPLIED"
        assert a["rerun_status"] == "passed"
        assert a["remaining_attempts"] == 3
        assert a["next_gate_id"] == "gate-completion"
        assert a["next_gate_status"] == "stage_completion_gate_created"

    def test_attempts_endpoint_exhausted_state(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        uow = SqliteControlTowerUnitOfWork(conn)
        uow.v2_jobs.save(_make_job_record("job-exhausted"))
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(
            job_id="job-exhausted",
            attempt_number=3,
            status="exhausted",
            apply_status="APPLIED",
            rerun_status="failed",
            rollback_status="rolled_back",
            remaining_attempts=0,
            completed_at=utc_now_text(),
            status_reason="All repair attempts exhausted for stage 1",
        )
        repo.save_proposal(record)
        client = _api_client(conn)
        resp = client.get(
            "/v1/v2/jobs/job-exhausted/repair/attempts",
            headers={"host": "127.0.0.1:8000"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["attempts"]) == 1
        a = data["attempts"][0]
        assert a["status"] == "exhausted"
        assert a["remaining_attempts"] == 0
        assert "exhausted" in a["status_reason"].lower()

    def test_attempts_endpoint_no_forbidden_fields_http(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        uow = SqliteControlTowerUnitOfWork(conn)
        uow.v2_jobs.save(_make_job_record("job-safe-http"))
        repo = SqliteV2RepairRepository(conn)
        record = _make_attempt_record(
            job_id="job-safe-http",
            attempt_number=1,
            status="approved_applied",
            apply_status="APPLIED",
            rerun_status="passed",
            remaining_attempts=3,
            completed_at=utc_now_text(),
        )
        repo.save_proposal(record)
        client = _api_client(conn)
        resp = client.get(
            "/v1/v2/jobs/job-safe-http/repair/attempts",
            headers={"host": "127.0.0.1:8000"},
        )
        data = resp.json()
        serialized = json.dumps(data)
        for field in FORBIDDEN_FIELDS:
            assert field not in serialized, f"Forbidden field {field!r} in HTTP response"
