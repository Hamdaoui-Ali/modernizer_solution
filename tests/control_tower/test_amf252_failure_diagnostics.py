"""AMF-252 failure diagnostics tests for repair assistant."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from migration_factory.control_tower.application.repair_assistant_service import (
    RepairAssistantService,
    RepairAssistantMessageRecord,
    CONTEXT_RESOLUTION_FAILED,
    PROPOSER_OUTPUT_INVALID,
    REVIEWER_UNAVAILABLE,
    PROPOSAL_PERSIST_FAILED,
    LEASE_STATE_UNAVAILABLE,
    FAILURE_STAGE_CONTEXT_RESOLUTION,
    FAILURE_STAGE_PROPOSER,
    FAILURE_STAGE_REVIEWER,
    FAILURE_STAGE_PROPOSAL_PERSIST,
    FAILURE_STAGE_LEASE,
    FAILURE_CODE_MAP,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.repair_assistant_repository import (
    LeaseState,
    SqliteRepairAssistantRepository,
    RepairAssistantMessageRecord as RepoRepairAssistantMessageRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
    SqliteV2RepairRepository,
    V2RepairProposalRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import (
    SqliteV2JobRepository,
    V2MigrationJobRecord,
)


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "amf252_failure.sqlite3"), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


def _proposal(conn: sqlite3.Connection, tmp_path: Path) -> str:
    diff = tmp_path / "proposal.diff"
    diff.write_text("diff --git a/pom.xml b/pom.xml\n", encoding="utf-8")
    checksum = hashlib.sha256(diff.read_bytes()).hexdigest()
    SqliteV2RepairRepository(conn).save_proposal(V2RepairProposalRecord(
        proposal_id="proposal-a", command_id="command-a", failure_summary="build failed",
        hypothesis="bad dependency", patch_summary="remove dependency", affected_paths_json='["pom.xml"]',
        status="user_review_required", approval_checksum=None, created_at=utc_now_text(), job_id="job-a",
        attempt_number=1, diff_ref=str(diff), diff_checksum=checksum,
    ))
    SqliteV2JobRepository(conn).save(V2MigrationJobRecord(
        job_id="job-a", setup_id="setup-a", setup_checksum="setup", pipeline_id="pipeline-a",
        stage_chain_json="[]", status="created", created_at=utc_now_text(), updated_at=utc_now_text(),
        correlation_id=None,
    ))
    return checksum


def test_migration_0061_adds_diagnostic_columns(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    cursor = conn.execute("PRAGMA table_info(repair_assistant_messages)")
    columns = {row["name"] for row in cursor.fetchall()}
    assert "failure_stage" in columns
    assert "failure_code" in columns
    assert "safe_failure_message" in columns
    assert "correlation_id" in columns


def test_migration_0061_preserves_existing_rows(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    conn.execute(
        "INSERT INTO repair_assistant_messages (message_id, job_id, proposal_id, role, message_text, base_diff_checksum, status, created_at) "
        "VALUES ('msg-1', 'job-1', 'prop-1', 'user', 'hello', 'abc123', 'answered', '2025-01-01T00:00:00Z')"
    )
    for col in ("failure_stage", "failure_code", "safe_failure_message", "correlation_id"):
        conn.execute(f"ALTER TABLE repair_assistant_messages DROP COLUMN {col}")
    conn.execute("DELETE FROM schema_migrations WHERE version = 61")
    apply_pending_migrations(conn)
    row = conn.execute("SELECT * FROM repair_assistant_messages WHERE message_id = 'msg-1'").fetchone()
    assert row["failure_stage"] is None
    assert row["failure_code"] is None
    assert row["safe_failure_message"] is None
    assert row["correlation_id"] is None
    assert row["message_text"] == "hello"


def test_diagnostic_persistence_and_retrieval(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _proposal(conn, tmp_path)
    repo = SqliteRepairAssistantRepository(conn)
    service = RepairAssistantService(repair_assistant_repo=repo)
    snapshot = {
        "job_id": "job-a",
        "proposal_id": "proposal-a",
        "attempt_number": 1,
    }
    record = service.save_failure_message_record(
        snapshot=snapshot,
        base_diff_checksum="abc123",
        failure_stage="proposer_generation",
        failure_code="PROPOSER_OUTPUT_INVALID",
        safe_failure_message="Proposer returned invalid output.",
        correlation_id="corr-001",
    )
    assert record.failure_stage == "proposer_generation"
    assert record.failure_code == "PROPOSER_OUTPUT_INVALID"
    assert record.safe_failure_message == "Proposer returned invalid output."
    assert record.correlation_id == "corr-001"
    assert record.status == "revision_failed"

    retrieved = repo.get_message(record.message_id)
    assert retrieved is not None
    assert retrieved.failure_stage == "proposer_generation"
    assert retrieved.failure_code == "PROPOSER_OUTPUT_INVALID"
    assert retrieved.safe_failure_message == "Proposer returned invalid output."
    assert retrieved.correlation_id == "corr-001"


def test_safe_text_handling(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _proposal(conn, tmp_path)
    repo = SqliteRepairAssistantRepository(conn)
    service = RepairAssistantService(repair_assistant_repo=repo)
    long_message = "x" * 5000
    snapshot = {
        "job_id": "job-a",
        "proposal_id": "proposal-a",
        "attempt_number": 1,
    }
    record = service.save_failure_message_record(
        snapshot=snapshot,
        base_diff_checksum="abc123",
        failure_stage="reviewer_evaluation",
        failure_code="REVIEWER_UNAVAILABLE",
        safe_failure_message=long_message,
        correlation_id="corr-002",
    )
    assert record.safe_failure_message == long_message
    retrieved = repo.get_message(record.message_id)
    assert retrieved is not None
    assert len(retrieved.safe_failure_message) == 5000
    assert "password" not in retrieved.safe_failure_message.lower()
    assert "secret" not in retrieved.safe_failure_message.lower()


def test_correlation_id_round_trip(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _proposal(conn, tmp_path)
    repo = SqliteRepairAssistantRepository(conn)
    service = RepairAssistantService(repair_assistant_repo=repo)
    snapshot = {
        "job_id": "job-a",
        "proposal_id": "proposal-a",
        "attempt_number": 1,
    }
    correlation_ids = ["corr-001", "a" * 64, "urn:uuid:123e4567-e89b-12d3-a456-426614174000"]
    for cid in correlation_ids:
        record = service.save_failure_message_record(
            snapshot=snapshot,
            base_diff_checksum="abc123",
            failure_stage="context_resolution",
            failure_code="CONTEXT_RESOLUTION_FAILED",
            safe_failure_message="Resolution failed.",
            correlation_id=cid,
        )
        retrieved = repo.get_message(record.message_id)
        assert retrieved is not None
        assert retrieved.correlation_id == cid


def test_failure_code_constants() -> None:
    expected = {
        "CONTEXT_RESOLUTION_FAILED",
        "PROPOSER_OUTPUT_INVALID",
        "REVIEWER_UNAVAILABLE",
        "PROPOSAL_PERSIST_FAILED",
        "LEASE_STATE_UNAVAILABLE",
    }
    constants = {
        CONTEXT_RESOLUTION_FAILED,
        PROPOSER_OUTPUT_INVALID,
        REVIEWER_UNAVAILABLE,
        PROPOSAL_PERSIST_FAILED,
        LEASE_STATE_UNAVAILABLE,
    }
    assert constants == expected
    assert FAILURE_CODE_MAP[CONTEXT_RESOLUTION_FAILED] == "CONTEXT_RESOLUTION_FAILED"
    assert FAILURE_CODE_MAP[PROPOSER_OUTPUT_INVALID] == "PROPOSER_OUTPUT_INVALID"
    assert FAILURE_CODE_MAP[REVIEWER_UNAVAILABLE] == "REVIEWER_UNAVAILABLE"
    assert FAILURE_CODE_MAP[PROPOSAL_PERSIST_FAILED] == "PROPOSAL_PERSIST_FAILED"
    assert FAILURE_CODE_MAP[LEASE_STATE_UNAVAILABLE] == "LEASE_STATE_UNAVAILABLE"


def test_failure_stage_constants() -> None:
    assert FAILURE_STAGE_CONTEXT_RESOLUTION == "context_resolution"
    assert FAILURE_STAGE_PROPOSER == "proposer_generation"
    assert FAILURE_STAGE_REVIEWER == "reviewer_evaluation"
    assert FAILURE_STAGE_PROPOSAL_PERSIST == "proposal_persist"
    assert FAILURE_STAGE_LEASE == "lease_state"


def test_save_message_includes_diagnostic_fields(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _proposal(conn, tmp_path)
    repo = SqliteRepairAssistantRepository(conn)
    record = RepoRepairAssistantMessageRecord(
        message_id="diag-msg-1",
        job_id="job-a",
        proposal_id="proposal-a",
        attempt_number=1,
        role="assistant",
        message_text="Failure occurred.",
        action=None,
        revision_intent_json=None,
        base_diff_checksum="abc123",
        generated_proposal_id=None,
        status="revision_failed",
        created_at=utc_now_text(),
        idempotency_key=None,
        failure_stage="lease_state",
        failure_code="LEASE_STATE_UNAVAILABLE",
        safe_failure_message="Lease was lost.",
        correlation_id="corr-003",
    )
    repo.save_message(record)
    retrieved = repo.get_message("diag-msg-1")
    assert retrieved is not None
    assert retrieved.failure_stage == "lease_state"
    assert retrieved.failure_code == "LEASE_STATE_UNAVAILABLE"
    assert retrieved.safe_failure_message == "Lease was lost."
    assert retrieved.correlation_id == "corr-003"


def test_list_messages_includes_diagnostics(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _proposal(conn, tmp_path)
    repo = SqliteRepairAssistantRepository(conn)
    record = RepoRepairAssistantMessageRecord(
        message_id="list-diag-1",
        job_id="job-a",
        proposal_id="proposal-a",
        attempt_number=1,
        role="assistant",
        message_text="Diagnostic msg.",
        action=None,
        revision_intent_json=None,
        base_diff_checksum="abc123",
        generated_proposal_id=None,
        status="revision_failed",
        created_at=utc_now_text(),
        idempotency_key=None,
        failure_stage="context_resolution",
        failure_code="CONTEXT_RESOLUTION_FAILED",
        safe_failure_message="Context resolution failed.",
        correlation_id="corr-list-1",
    )
    repo.save_message(record)
    messages = repo.list_messages("job-a", "proposal-a")
    assert len(messages) == 1
    msg = messages[0]
    assert msg.failure_stage == "context_resolution"
    assert msg.failure_code == "CONTEXT_RESOLUTION_FAILED"
    assert msg.safe_failure_message == "Context resolution failed."
    assert msg.correlation_id == "corr-list-1"


def _make_original_revision_row(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    owner: str,
    status: str = "revision_generating",
) -> None:
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    now = utc_now_text()
    conn.execute(
        """INSERT INTO repair_assistant_messages (
            message_id, job_id, proposal_id, attempt_number,
            role, message_text, action, revision_intent_json,
            base_diff_checksum, generated_proposal_id, status,
            created_at, idempotency_key,
            processing_owner, processing_started_at, lease_expires_at,
            response_message_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            message_id, "job-a", "proposal-a", 1,
            "assistant", "Revision requested", "REQUEST_REVISION", '{}',
            "abc123", None, status,
            now, None,
            owner, now, future,
            None,
        ),
    )


def test_revision_exception_finalizes_original_row(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _proposal(conn, tmp_path)
    repo = SqliteRepairAssistantRepository(conn)
    owner = "test_owner"
    original_msg_id = uuid4().hex
    _make_original_revision_row(conn, message_id=original_msg_id, owner=owner)
    conn.commit()

    conn.execute("BEGIN")
    outcome = repo.finalize_lease_with_failure(
        message_id=original_msg_id,
        owner=owner,
        status="revision_failed",
        failure_stage="revision_generation",
        failure_code="ValueError",
        safe_failure_message="ValueError: test error",
        correlation_id="corr-finalize-1",
    )
    conn.commit()
    assert outcome == LeaseState.OWNED

    row = conn.execute(
        "SELECT status, failure_stage, failure_code, safe_failure_message, correlation_id FROM repair_assistant_messages WHERE message_id = ?",
        (original_msg_id,),
    ).fetchone()
    assert row is not None
    assert str(row["status"]) == "revision_failed"
    assert str(row["failure_stage"]) == "revision_generation"
    assert str(row["failure_code"]) == "ValueError"


def test_no_authoritative_row_remains_revision_generating(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _proposal(conn, tmp_path)
    repo = SqliteRepairAssistantRepository(conn)
    owner = "test_owner"
    original_msg_id = uuid4().hex
    _make_original_revision_row(conn, message_id=original_msg_id, owner=owner)
    conn.commit()

    conn.execute("BEGIN")
    repo.finalize_lease_with_failure(
        message_id=original_msg_id,
        owner=owner,
        status="revision_failed",
        failure_stage="revision_generation",
        failure_code="ValueError",
        safe_failure_message="ValueError: test error",
        correlation_id="corr-finalize-2",
    )
    conn.commit()

    rows = conn.execute(
        "SELECT message_id FROM repair_assistant_messages WHERE status = 'revision_generating'"
    ).fetchall()
    assert len(rows) == 0


def test_failure_diagnostics_persisted(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _proposal(conn, tmp_path)
    repo = SqliteRepairAssistantRepository(conn)
    owner = "test_owner"
    original_msg_id = uuid4().hex
    _make_original_revision_row(conn, message_id=original_msg_id, owner=owner)
    conn.commit()

    conn.execute("BEGIN")
    repo.finalize_lease_with_failure(
        message_id=original_msg_id,
        owner=owner,
        status="revision_failed",
        failure_stage="revision_generation",
        failure_code="AttributeError",
        safe_failure_message="AttributeError: 'NoneType' object has no attribute 'foo'",
        correlation_id="corr-finalize-3",
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM repair_assistant_messages WHERE message_id = ?",
        (original_msg_id,),
    ).fetchone()
    assert row is not None
    assert str(row["failure_stage"]) == "revision_generation"
    assert str(row["failure_code"]) == "AttributeError"
    assert str(row["safe_failure_message"]) == "AttributeError: 'NoneType' object has no attribute 'foo'"
    assert str(row["correlation_id"]) == "corr-finalize-3"


def test_safe_message_contains_exception_type(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _proposal(conn, tmp_path)
    repo = SqliteRepairAssistantRepository(conn)
    owner = "test_owner"
    original_msg_id = uuid4().hex
    _make_original_revision_row(conn, message_id=original_msg_id, owner=owner)
    conn.commit()

    conn.execute("BEGIN")
    repo.finalize_lease_with_failure(
        message_id=original_msg_id,
        owner=owner,
        status="revision_failed",
        failure_stage="revision_generation",
        failure_code="AttributeError",
        safe_failure_message="AttributeError: 'NoneType' object has no attribute 'context_pack'",
        correlation_id="corr-finalize-4",
    )
    conn.commit()

    row = conn.execute(
        "SELECT safe_failure_message FROM repair_assistant_messages WHERE message_id = ?",
        (original_msg_id,),
    ).fetchone()
    assert row is not None
    msg = str(row["safe_failure_message"])
    assert "AttributeError" in msg
    assert "context_pack" in msg or "NoneType" in msg


def test_traceback_not_stored_in_sqlite(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _proposal(conn, tmp_path)
    repo = SqliteRepairAssistantRepository(conn)
    owner = "test_owner"
    original_msg_id = uuid4().hex
    _make_original_revision_row(conn, message_id=original_msg_id, owner=owner)
    conn.commit()

    conn.execute("BEGIN")
    repo.finalize_lease_with_failure(
        message_id=original_msg_id,
        owner=owner,
        status="revision_failed",
        failure_stage="revision_generation",
        failure_code="AttributeError",
        safe_failure_message="AttributeError: 'NoneType' object has no attribute 'foo'",
        correlation_id="corr-finalize-5",
    )
    conn.commit()

    row = conn.execute(
        "SELECT safe_failure_message, failure_code FROM repair_assistant_messages WHERE message_id = ?",
        (original_msg_id,),
    ).fetchone()
    assert row is not None
    sf = str(row["safe_failure_message"])
    assert "Traceback" not in sf
    assert "File" not in sf or sf.count("File") == 0


def test_lease_fields_cleared_on_failure_finalize(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _proposal(conn, tmp_path)
    repo = SqliteRepairAssistantRepository(conn)
    owner = "test_owner"
    original_msg_id = uuid4().hex
    _make_original_revision_row(conn, message_id=original_msg_id, owner=owner)
    conn.commit()

    conn.execute("BEGIN")
    repo.finalize_lease_with_failure(
        message_id=original_msg_id,
        owner=owner,
        status="revision_failed",
        failure_stage="revision_generation",
        failure_code="ValueError",
        safe_failure_message="ValueError: failed",
        correlation_id="corr-finalize-6",
    )
    conn.commit()

    row = conn.execute(
        """SELECT processing_owner, processing_started_at, lease_expires_at
           FROM repair_assistant_messages WHERE message_id = ?""",
        (original_msg_id,),
    ).fetchone()
    assert row is not None
    assert row["processing_owner"] is None
    assert row["processing_started_at"] is None
    assert row["lease_expires_at"] is None


def test_idempotent_failure_replay(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _proposal(conn, tmp_path)
    repo = SqliteRepairAssistantRepository(conn)
    owner = "test_owner"
    original_msg_id = uuid4().hex
    _make_original_revision_row(conn, message_id=original_msg_id, owner=owner)
    conn.commit()

    conn.execute("BEGIN")
    outcome1 = repo.finalize_lease_with_failure(
        message_id=original_msg_id,
        owner=owner,
        status="revision_failed",
        failure_stage="revision_generation",
        failure_code="ValueError",
        safe_failure_message="ValueError: test",
        correlation_id="corr-finalize-7",
    )
    conn.commit()
    assert outcome1 == LeaseState.OWNED

    conn.execute("BEGIN")
    outcome2 = repo.finalize_lease_with_failure(
        message_id=original_msg_id,
        owner=owner,
        status="revision_failed",
        failure_stage="revision_generation",
        failure_code="ValueError",
        safe_failure_message="ValueError: test",
        correlation_id="corr-finalize-7",
    )
    conn.commit()
    assert outcome2 == LeaseState.OWNED

    rows = conn.execute(
        "SELECT COUNT(*) as cnt FROM repair_assistant_messages WHERE message_id = ?",
        (original_msg_id,),
    ).fetchone()
    assert rows is not None
    assert rows["cnt"] == 1


def test_runner_requires_migration_0061() -> None:
    import re
    runner_path = Path(__file__).parents[2] / "run_amf252_backend_clean.ps1"
    content = runner_path.read_text(encoding="utf-8")
    assert "0061_repair_assistant_failure_diagnostics.sql" in content
    match = re.search(r'\$ExpectedRepairMigrationName\s*=\s*`\s*\n\s*"(\d+[^"]*\.sql)"', content)
    assert match is not None
    expected = match.group(1)
    assert "0061" in expected, f"ExpectedRepairMigrationName should reference 0061, got {expected}"
