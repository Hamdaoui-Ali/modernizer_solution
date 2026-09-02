"""Focused tests for V1-17C: Approve or reject privileged actions.

Tests cover:
- approve_action: valid approval, checksum mismatch, stale action, duplicate
- reject_action: valid rejection, checksum mismatch, stale action, duplicate
- Decision persistence and retrieval
- Audit trail recording
"""

from __future__ import annotations

import sqlite3

import pytest

from migration_factory.control_tower.application.actions import (
    ActionStaleError,
    ChecksumMismatchError,
    DuplicateActionDecisionError,
    PrivilegedActionService,
)
from migration_factory.control_tower.domain.checksums import sha256_canonical_json
from migration_factory.control_tower.domain.entities import (
    V1PrivilegedActionDecisionRecord,
)


# ── Test helpers ──────────────────────────────────────────────────────


def _create_service(tmp_path):
    db_path = tmp_path / "test_v17c.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # All migrations needed
    for name in (
        "0001_foundation.sql",
        "0017_v1_privileged_actions.sql",
        "0018_v1_privileged_action_decisions.sql",
    ):
        path = f"migration_factory/control_tower/infrastructure/sqlite/migrations/{name}"
        with open(path) as f:
            cur.executescript(f.read())
    conn.commit()

    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
        SqliteControlTowerUnitOfWork,
    )

    def uow_factory():
        return SqliteControlTowerUnitOfWork(conn)

    service = PrivilegedActionService(uow_factory)
    return conn, service


def _create_job(conn: sqlite3.Connection, job_id: str = "job-001") -> None:
    conn.execute(
        """INSERT OR IGNORE INTO migration_jobs (
            job_id, version, status, active_slot, last_event_sequence,
            runner_profile_id, runner_profile_version, pipeline_id,
            pipeline_version, target_proof_level, legacy_source_ref,
            output_root_ref, created_at, updated_at, created_by
        ) VALUES (?, 1, 'created', NULL, 0, 'rp-1', '1.0', 'pl-1', '1.0',
                  'none', 'legacy', 'output', '2026-06-12T00:00:00Z',
                  '2026-06-12T00:00:00Z', 'test')""",
        (job_id,),
    )
    conn.commit()


def _create_pending_action(
    conn: sqlite3.Connection,
    service: PrivilegedActionService,
    *,
    action_type: str = "maven",
    params: dict | None = None,
    job_id: str = "job-001",
):
    """Create a pending action and return the record."""
    if params is None:
        params = {"goal": "compile"}
    return service.request_action(
        job_id=job_id,
        action_type=action_type,
        parameters=params,
        requested_by="test",
    )


# ── Migration tests ──────────────────────────────────────────────────


class TestV1PrivilegedActionDecisionsMigration:
    """0018 migration produces correct schema."""

    MIGRATION_PATH = (
        "migration_factory/control_tower/infrastructure/sqlite/migrations"
        "/0018_v1_privileged_action_decisions.sql"
    )

    def _apply(self, tmp_path) -> sqlite3.Connection:
        db_path = tmp_path / "test_decisions_migration.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        with open(self.MIGRATION_PATH) as f:
            cur.executescript(f.read())
        return conn

    def test_table_exists(self, tmp_path) -> None:
        conn = self._apply(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='v1_privileged_action_decisions'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_decision_check_constraint_valid(self, tmp_path) -> None:
        conn = self._apply(tmp_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO v1_privileged_action_decisions (
                action_id, decision, decided_by, decided_at,
                parameters_checksum
            ) VALUES (?, ?, ?, ?, ?)""",
            ("pa-001", "approved", "admin", "2026-06-12T00:00:00Z", "chk123"),
        )
        conn.close()

    def test_decision_check_constraint_invalid(self, tmp_path) -> None:
        conn = self._apply(tmp_path)
        cur = conn.cursor()
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                """INSERT INTO v1_privileged_action_decisions (
                    action_id, decision, decided_by, decided_at,
                    parameters_checksum
                ) VALUES (?, ?, ?, ?, ?)""",
                ("pa-bad", "invalid_decision", "admin", "now", "chk"),
            )
        conn.close()

    def test_append_only_prevents_update(self, tmp_path) -> None:
        conn = self._apply(tmp_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO v1_privileged_action_decisions (
                action_id, decision, decided_by, decided_at,
                parameters_checksum
            ) VALUES ('pa-upd', 'approved', 'admin', 'now', 'chk')"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            cur.execute(
                "UPDATE v1_privileged_action_decisions SET decision = 'rejected' "
                "WHERE action_id = 'pa-upd'"
            )
        conn.close()

    def test_append_only_prevents_delete(self, tmp_path) -> None:
        conn = self._apply(tmp_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO v1_privileged_action_decisions (
                action_id, decision, decided_by, decided_at,
                parameters_checksum
            ) VALUES ('pa-del', 'rejected', 'admin', 'now', 'chk')"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            cur.execute(
                "DELETE FROM v1_privileged_action_decisions WHERE action_id = 'pa-del'"
            )
        conn.close()


# ── Approve action tests ─────────────────────────────────────────────


class TestApproveAction:
    """approve_action validates and records approvals."""

    def test_approve_valid_action(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = _create_pending_action(conn, service)
        chk = sha256_canonical_json({"goal": "compile"})

        decision = service.approve_action(
            action.action_id,
            approved_by="reviewer",
            parameters_checksum=chk,
        )
        assert decision.decision == "approved"
        assert decision.decided_by == "reviewer"
        assert decision.action_id == action.action_id
        conn.close()

    def test_approve_checksum_mismatch(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = _create_pending_action(conn, service)

        with pytest.raises(ChecksumMismatchError):
            service.approve_action(
                action.action_id,
                approved_by="reviewer",
                parameters_checksum="wrong-checksum",
            )
        conn.close()

    def test_approve_stale_action(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)

        with pytest.raises(ActionStaleError, match="not found"):
            service.approve_action(
                "nonexistent",
                approved_by="reviewer",
                parameters_checksum="chk",
            )
        conn.close()

    def test_approve_already_approved_action(self, tmp_path) -> None:
        """Re-approving an already-approved action is rejected."""
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = _create_pending_action(conn, service)
        chk = sha256_canonical_json({"goal": "compile"})

        # First approval
        service.approve_action(
            action.action_id,
            approved_by="reviewer",
            parameters_checksum=chk,
        )

        # Second approval should fail
        with pytest.raises(DuplicateActionDecisionError, match="approved"):
            service.approve_action(
                action.action_id,
                approved_by="other",
                parameters_checksum=chk,
            )
        conn.close()

    def test_approve_persists_decision(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = _create_pending_action(conn, service)
        chk = sha256_canonical_json({"goal": "compile"})

        decision = service.approve_action(
            action.action_id,
            approved_by="reviewer",
            parameters_checksum=chk,
        )

        # Read back via repository
        from migration_factory.control_tower.infrastructure.sqlite.repositories import (
            SqliteV1PrivilegedActionDecisionRepository,
        )
        repo = SqliteV1PrivilegedActionDecisionRepository(conn)
        stored = repo.get(action.action_id)
        assert stored is not None
        assert stored.decision == "approved"
        assert stored.decided_by == "reviewer"
        conn.close()

    def test_approve_records_audit(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = _create_pending_action(conn, service)
        chk = sha256_canonical_json({"goal": "compile"})

        service.approve_action(
            action.action_id,
            approved_by="auditor",
            parameters_checksum=chk,
        )

        # Verify audit record exists
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM audit_records "
            "WHERE action = 'privileged_action_approved'"
        ).fetchone()[0]
        assert audit_count == 1
        conn.close()


# ── Reject action tests ──────────────────────────────────────────────


class TestRejectAction:
    """reject_action validates and records rejections."""

    def test_reject_valid_action(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = _create_pending_action(conn, service)
        chk = sha256_canonical_json({"goal": "compile"})

        decision = service.reject_action(
            action.action_id,
            rejected_by="reviewer",
            parameters_checksum=chk,
            rejection_reason="Policy violation: unsafe goal",
        )
        assert decision.decision == "rejected"
        assert decision.decided_by == "reviewer"
        assert decision.rejection_reason == "Policy violation: unsafe goal"
        conn.close()

    def test_reject_valid_action_no_reason(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = _create_pending_action(conn, service)
        chk = sha256_canonical_json({"goal": "compile"})

        decision = service.reject_action(
            action.action_id,
            rejected_by="reviewer",
            parameters_checksum=chk,
        )
        assert decision.decision == "rejected"
        assert decision.rejection_reason is None
        conn.close()

    def test_reject_checksum_mismatch(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = _create_pending_action(conn, service)

        with pytest.raises(ChecksumMismatchError):
            service.reject_action(
                action.action_id,
                rejected_by="reviewer",
                parameters_checksum="wrong-checksum",
            )
        conn.close()

    def test_reject_stale_action(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)

        with pytest.raises(ActionStaleError, match="not found"):
            service.reject_action(
                "nonexistent",
                rejected_by="reviewer",
                parameters_checksum="chk",
            )
        conn.close()

    def test_reject_already_rejected(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = _create_pending_action(conn, service)
        chk = sha256_canonical_json({"goal": "compile"})

        service.reject_action(action.action_id, rejected_by="r1",
                              parameters_checksum=chk)

        with pytest.raises(DuplicateActionDecisionError, match="rejected"):
            service.reject_action(action.action_id, rejected_by="r2",
                                  parameters_checksum=chk)
        conn.close()

    def test_approve_after_reject_is_duplicate(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = _create_pending_action(conn, service)
        chk = sha256_canonical_json({"goal": "compile"})

        service.reject_action(action.action_id, rejected_by="r1",
                              parameters_checksum=chk)

        with pytest.raises(DuplicateActionDecisionError):
            service.approve_action(action.action_id, approved_by="a1",
                                   parameters_checksum=chk)
        conn.close()

    def test_reject_records_audit(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = _create_pending_action(conn, service)
        chk = sha256_canonical_json({"goal": "compile"})

        service.reject_action(action.action_id, rejected_by="auditor",
                              parameters_checksum=chk,
                              rejection_reason="Bad goal")

        count = conn.execute(
            "SELECT COUNT(*) FROM audit_records "
            "WHERE action = 'privileged_action_rejected'"
        ).fetchone()[0]
        assert count == 1
        conn.close()
