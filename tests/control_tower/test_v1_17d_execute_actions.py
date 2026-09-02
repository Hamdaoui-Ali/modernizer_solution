"""Focused tests for V1-17D: Execute approved privileged actions.

Tests cover:
- Migration schema for execution table
- execute_action: approved maven/write execution
- execute_action: rejected for unapproved, stale, checksum mismatch
- execute_action: rejected for already-executed
- Redacted execution summaries
- Audit trail recording
"""

from __future__ import annotations

import sqlite3

import pytest

from migration_factory.control_tower.application.actions import (
    ActionNotApprovedError,
    ActionStaleError,
    ChecksumMismatchError,
    PrivilegedActionService,
)
from migration_factory.control_tower.domain.checksums import sha256_canonical_json


# ── Shared fixtures ──────────────────────────────────────────────────


def _create_service(tmp_path):
    db_path = tmp_path / "test_v17d.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    for name in (
        "0001_foundation.sql",
        "0017_v1_privileged_actions.sql",
        "0018_v1_privileged_action_decisions.sql",
        "0019_v1_privileged_action_executions.sql",
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


def _create_pending_and_approved(
    conn: sqlite3.Connection,
    service: PrivilegedActionService,
    *,
    action_type: str = "maven",
    params: dict | None = None,
    job_id: str = "job-001",
    approved_by: str = "reviewer",
):
    """Create a pending action and approve it, returning action record."""
    if params is None:
        params = {"goal": "compile"}
    action = service.request_action(
        job_id=job_id,
        action_type=action_type,
        parameters=params,
        requested_by="test",
    )
    chk = sha256_canonical_json(params)
    service.approve_action(
        action.action_id,
        approved_by=approved_by,
        parameters_checksum=chk,
    )
    return action, chk


# ── Migration tests ──────────────────────────────────────────────────


class TestV1PrivilegedActionExecutionsMigration:
    """0019 migration produces correct schema."""

    MIGRATION_PATH = (
        "migration_factory/control_tower/infrastructure/sqlite/migrations"
        "/0019_v1_privileged_action_executions.sql"
    )

    def _apply(self, tmp_path) -> sqlite3.Connection:
        db_path = tmp_path / "test_exec_migration.db"
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
            "AND name='v1_privileged_action_executions'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_status_check_constraint_valid(self, tmp_path) -> None:
        conn = self._apply(tmp_path)
        cur = conn.cursor()
        for s in ("executing", "completed", "failed"):
            cur.execute(
                """INSERT INTO v1_privileged_action_executions (
                    action_id, job_id, action_type, parameters_checksum,
                    status, started_at, executed_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (f"pa-{s}", "job-001", "maven", "chk", s, "now", "executor"),
            )
        conn.close()

    def test_status_check_constraint_invalid(self, tmp_path) -> None:
        conn = self._apply(tmp_path)
        cur = conn.cursor()
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                """INSERT INTO v1_privileged_action_executions (
                    action_id, job_id, action_type, parameters_checksum,
                    status, started_at, executed_by
                ) VALUES ('pa-bad', 'job-001', 'maven', 'chk',
                          'invalid_status', 'now', 'executor')"""
            )
        conn.close()

    def test_append_only_prevents_update(self, tmp_path) -> None:
        conn = self._apply(tmp_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO v1_privileged_action_executions (
                action_id, job_id, action_type, parameters_checksum,
                status, started_at, executed_by
            ) VALUES ('pa-upd', 'job-001', 'maven', 'chk', 'completed', 'now', 'exec')"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            cur.execute(
                "UPDATE v1_privileged_action_executions SET status = 'failed' "
                "WHERE action_id = 'pa-upd'"
            )
        conn.close()

    def test_append_only_prevents_delete(self, tmp_path) -> None:
        conn = self._apply(tmp_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO v1_privileged_action_executions (
                action_id, job_id, action_type, parameters_checksum,
                status, started_at, executed_by
            ) VALUES ('pa-del', 'job-001', 'maven', 'chk', 'completed', 'now', 'exec')"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            cur.execute(
                "DELETE FROM v1_privileged_action_executions WHERE action_id = 'pa-del'"
            )
        conn.close()


# ── Execute action tests ─────────────────────────────────────────────


class TestExecuteAction:
    """execute_action validates and records execution."""

    def test_execute_approved_maven(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action, chk = _create_pending_and_approved(conn, service)

        execution = service.execute_action(
            action.action_id,
            executed_by="worker",
            parameters_checksum=chk,
        )
        assert execution.status == "completed"
        assert execution.action_type == "maven"
        assert "compile" in (execution.result_summary or "")
        assert execution.executed_by == "worker"
        conn.close()

    def test_execute_approved_write(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        params = {"path": "/home/user/project/src/App.java", "content": "public class App {}"}
        action, chk = _create_pending_and_approved(
            conn, service, action_type="write", params=params,
        )

        execution = service.execute_action(
            action.action_id,
            executed_by="worker",
            parameters_checksum=chk,
        )
        assert execution.status == "completed"
        assert execution.action_type == "write"
        conn.close()

    def test_execute_unapproved_action(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = service.request_action(
            job_id="job-001",
            action_type="maven",
            parameters={"goal": "compile"},
        )
        chk = sha256_canonical_json({"goal": "compile"})

        with pytest.raises(ActionNotApprovedError, match="not approved"):
            service.execute_action(
                action.action_id,
                executed_by="worker",
                parameters_checksum=chk,
            )
        conn.close()

    def test_execute_rejected_action(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action = service.request_action(
            job_id="job-001",
            action_type="maven",
            parameters={"goal": "compile"},
        )
        chk = sha256_canonical_json({"goal": "compile"})
        service.reject_action(
            action.action_id,
            rejected_by="reviewer",
            parameters_checksum=chk,
            rejection_reason="Not needed",
        )

        with pytest.raises(ActionNotApprovedError, match="rejected"):
            service.execute_action(
                action.action_id,
                executed_by="worker",
                parameters_checksum=chk,
            )
        conn.close()

    def test_execute_checksum_mismatch(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action, _ = _create_pending_and_approved(conn, service)

        with pytest.raises(ChecksumMismatchError):
            service.execute_action(
                action.action_id,
                executed_by="worker",
                parameters_checksum="wrong-checksum",
            )
        conn.close()

    def test_execute_stale_action(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)

        with pytest.raises(ActionStaleError, match="not found"):
            service.execute_action(
                "nonexistent",
                executed_by="worker",
                parameters_checksum="chk",
            )
        conn.close()

    def test_execute_twice_rejected(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action, chk = _create_pending_and_approved(conn, service)

        service.execute_action(
            action.action_id,
            executed_by="worker",
            parameters_checksum=chk,
        )

        with pytest.raises(ActionNotApprovedError, match="already executed"):
            service.execute_action(
                action.action_id,
                executed_by="worker",
                parameters_checksum=chk,
            )
        conn.close()

    def test_execute_persists_execution(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action, chk = _create_pending_and_approved(conn, service)

        service.execute_action(
            action.action_id,
            executed_by="worker",
            parameters_checksum=chk,
        )

        stored = service.get_execution(action.action_id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.executed_by == "worker"
        conn.close()

    def test_execute_records_audit(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        action, chk = _create_pending_and_approved(conn, service)

        service.execute_action(
            action.action_id,
            executed_by="auditor",
            parameters_checksum=chk,
        )

        count = conn.execute(
            "SELECT COUNT(*) FROM audit_records "
            "WHERE action = 'privileged_action_executed'"
        ).fetchone()[0]
        assert count == 1
        conn.close()


# ── Redacted summary tests ──────────────────────────────────────────


class TestBuildRedactedExecutionSummary:
    """_build_redacted_execution_summary produces safe output."""

    def test_maven_summary_includes_goal(self) -> None:
        summary = PrivilegedActionService._build_redacted_execution_summary(
            "maven", {"goal": "compile"}
        )
        assert "Maven goal: compile" in summary

    def test_maven_summary_includes_module(self) -> None:
        summary = PrivilegedActionService._build_redacted_execution_summary(
            "maven", {"goal": "compile", "module": "core"}
        )
        assert "Maven goal: compile" in summary
        assert "module: core" in summary

    def test_write_summary_includes_path(self) -> None:
        summary = PrivilegedActionService._build_redacted_execution_summary(
            "write", {"path": "src/App.java", "content": "data"}
        )
        assert "Write action to:" in summary

    def test_write_summary_redacts_forbidden_paths(self) -> None:
        summary = PrivilegedActionService._build_redacted_execution_summary(
            "write", {"path": "/etc/passwd", "content": "hacked"}
        )
        # V1-00D redaction should redact the absolute path
        assert "/etc/passwd" not in summary
        assert "Write action to:" in summary

    def test_execute_write_redacts_summary(self, tmp_path) -> None:
        conn, service = _create_service(tmp_path)
        _create_job(conn)
        # Use a safe path that will be redacted in the summary by V1-00D
        params = {"path": "/home/user/project/src/App.java", "content": "real data"}
        action, _ = _create_pending_and_approved(
            conn, service, action_type="write", params=params,
        )
        chk = sha256_canonical_json(params)

        execution = service.execute_action(
            action.action_id,
            executed_by="worker",
            parameters_checksum=chk,
        )
        # The result_summary should be redacted (absolute path replaced)
        result = execution.result_summary or ""
        assert "/home/user/project/src/App.java" not in result
        conn.close()
