"""Focused tests for V1-17A: Persist pending privileged actions."""

from __future__ import annotations

import json
import sqlite3

import pytest

from migration_factory.control_tower.domain.entities import V1PrivilegedActionRecord
from migration_factory.control_tower.application.actions import (
    ALLOWED_ACTION_TYPES,
    ActionNotFoundError,
    InvalidActionTypeError,
    PrivilegedActionService,
)


# ── Migration tests ──────────────────────────────────────────────────


class TestV1PrivilegedActionsMigration:
    """v1_privileged_actions SQL migration produces correct schema."""

    MIGRATION_PATH = (
        "migration_factory/control_tower/infrastructure/sqlite/migrations"
        "/0017_v1_privileged_actions.sql"
    )

    def _apply_migration(self, tmp_path) -> sqlite3.Connection:
        db_path = tmp_path / "test_privileged_actions.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        with open(self.MIGRATION_PATH) as f:
            cur.executescript(f.read())
        return conn

    def test_table_exists(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v1_privileged_actions'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_has_job_id_index(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_v1_privileged_actions_job_id'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_has_status_index(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_v1_privileged_actions_status'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_has_requested_at_index(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_v1_privileged_actions_requested_at'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_action_type_check_constraint_valid(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        # Valid types
        for t in ("maven", "write"):
            cur.execute(
                """INSERT INTO v1_privileged_actions (
                    action_id, job_id, action_type, parameters_json,
                    parameters_checksum, requested_by, requested_at
                ) VALUES (?, ?, ?, '{}', 'chk', 'test', '2026-06-12T00:00:00Z')""",
                (f"pa-valid-{t}", "job-001", t),
            )
        conn.close()

    def test_action_type_check_constraint_invalid(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                """INSERT INTO v1_privileged_actions (
                    action_id, job_id, action_type, parameters_json,
                    parameters_checksum, requested_by, requested_at
                ) VALUES ('pa-bad', 'job-001', 'shell', '{}', 'chk', 'test', '2026-06-12T00:00:00Z')"""
            )
        conn.close()

    def test_append_only_trigger_prevents_update(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO v1_privileged_actions (
                action_id, job_id, action_type, parameters_json,
                parameters_checksum, requested_by, requested_at
            ) VALUES ('pa-upd', 'job-001', 'maven', '{}', 'chk', 'test', '2026-06-12T00:00:00Z')"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            cur.execute(
                "UPDATE v1_privileged_actions SET status = 'executing' WHERE action_id = 'pa-upd'"
            )
        conn.close()

    def test_append_only_trigger_prevents_delete(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO v1_privileged_actions (
                action_id, job_id, action_type, parameters_json,
                parameters_checksum, requested_by, requested_at
            ) VALUES ('pa-del', 'job-001', 'maven', '{}', 'chk', 'test', '2026-06-12T00:00:00Z')"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            cur.execute("DELETE FROM v1_privileged_actions WHERE action_id = 'pa-del'")
        conn.close()

    def test_insert_and_read_full_record(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO v1_privileged_actions (
                action_id, job_id, action_type, action_version,
                parameters_json, parameters_checksum, policy_json,
                policy_version, status, requested_by, requested_at,
                approved_by, approved_at, rejected_by, rejected_reason,
                executed_at, failure_reason, correlation_id, causation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "pa-full", "job-001", "maven", "1.0",
                '{"goal": "compile"}', "chk123", '{"policy": "default"}',
                "v1", "pending", "admin", "2026-06-12T00:00:00.000000Z",
                None, None, None, None,
                None, None, "corr-1", "caus-1",
            ),
        )
        cur.execute("SELECT * FROM v1_privileged_actions WHERE action_id = 'pa-full'")
        row = cur.fetchone()
        assert row is not None
        assert row["action_id"] == "pa-full"
        assert row["action_type"] == "maven"
        assert row["status"] == "pending"
        conn.close()


# ── Domain model tests ───────────────────────────────────────────────


class TestV1PrivilegedActionRecord:
    """V1PrivilegedActionRecord dataclass behavior."""

    def test_create_minimal_record(self) -> None:
        record = V1PrivilegedActionRecord(
            action_id="pa-001",
            job_id="job-001",
            action_type="maven",
            parameters_json='{"goal": "compile"}',
            parameters_checksum="chk123",
        )
        assert record.action_id == "pa-001"
        assert record.action_type == "maven"
        assert record.status == "pending"

    def test_create_full_record(self) -> None:
        record = V1PrivilegedActionRecord(
            action_id="pa-002",
            job_id="job-002",
            action_type="write",
            parameters_json='{"path": "src/main/java"}',
            parameters_checksum="chk456",
            status="approved",
            requested_by="admin",
            requested_at="2026-06-12T00:00:00Z",
            approved_by="reviewer",
            approved_at="2026-06-12T01:00:00Z",
            policy_json='{"version": "1"}',
            policy_version="1.0",
            correlation_id="corr-1",
        )
        assert record.action_type == "write"
        assert record.status == "approved"
        assert record.approved_by == "reviewer"

    def test_rejects_shell_type(self) -> None:
        record = V1PrivilegedActionRecord(
            action_id="pa-shell",
            job_id="job-001",
            action_type="shell",  # model allows it, validation is at service layer
            parameters_json='{"cmd": "rm -rf /"}',
            parameters_checksum="bad",
        )
        # The dataclass accepts any string, but the SQL CHECK constraint and
        # service layer both reject 'shell'
        assert record.action_type == "shell"

    def test_record_is_frozen(self) -> None:
        record = V1PrivilegedActionRecord(
            action_id="pa-003",
            job_id="job-001",
            action_type="maven",
            parameters_json="{}",
            parameters_checksum="chk",
        )
        with pytest.raises(AttributeError):
            record.status = "executing"  # type: ignore[misc]

    def test_record_has_slots(self) -> None:
        record = V1PrivilegedActionRecord(
            action_id="pa-004",
            job_id="job-001",
            action_type="maven",
            parameters_json="{}",
            parameters_checksum="chk",
        )
        assert hasattr(record, "__slots__")


# ── Service tests ────────────────────────────────────────────────────


class TestPrivilegedActionService:
    """PrivilegedActionService persistence behavior."""

    def _create_service(self, tmp_path):
        """Create a service backed by SQLite."""
        db_path = tmp_path / "test_actions_service.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        with open(
            "migration_factory/control_tower/infrastructure/sqlite/migrations/0017_v1_privileged_actions.sql"
        ) as f:
            cur.executescript(f.read())
        # Also need base tables for foreign key
        with open(
            "migration_factory/control_tower/infrastructure/sqlite/migrations/0001_foundation.sql"
        ) as f:
            cur.executescript(f.read())
        conn.commit()

        from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
            SqliteControlTowerUnitOfWork,
        )

        def uow_factory():
            return SqliteControlTowerUnitOfWork(conn)

        service = PrivilegedActionService(uow_factory)
        return conn, service

    def _create_job(self, conn: sqlite3.Connection, job_id: str = "job-001") -> None:
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

    def test_request_maven_action(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        record = service.request_action(
            job_id="job-001",
            action_type="maven",
            parameters={"goal": "compile", "module": "core"},
            requested_by="admin",
        )
        assert record.action_id.startswith("pa-")
        assert record.action_type == "maven"
        assert record.status == "pending"
        assert record.parameters_checksum is not None
        conn.close()

    def test_request_write_action(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        record = service.request_action(
            job_id="job-001",
            action_type="write",
            parameters={"path": "src/main/java/com/example/App.java", "content": "..."},
            requested_by="admin",
        )
        assert record.action_type == "write"
        assert record.status == "pending"
        conn.close()

    def test_rejects_shell_action(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        with pytest.raises(InvalidActionTypeError, match="shell"):
            service.request_action(
                job_id="job-001",
                action_type="shell",
                parameters={"cmd": "rm -rf"},
            )
        conn.close()

    def test_rejects_empty_parameters(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        with pytest.raises(ValueError, match="must not be empty"):
            service.request_action(
                job_id="job-001",
                action_type="maven",
                parameters={},
            )
        conn.close()

    def test_get_action(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        record = service.request_action(
            job_id="job-001",
            action_type="maven",
            parameters={"goal": "test"},
        )
        got = service.get_action(record.action_id)
        assert got is not None
        assert got.action_id == record.action_id
        assert got.status == "pending"
        conn.close()

    def test_get_missing_action_returns_none(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        got = service.get_action("nonexistent")
        assert got is None
        conn.close()

    def test_list_actions(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        service.request_action(job_id="job-001", action_type="maven", parameters={"goal": "a"})
        service.request_action(job_id="job-001", action_type="write", parameters={"path": "b", "content": "data"})

        all_actions = service.list_actions()
        assert len(all_actions) == 2
        conn.close()

    def test_list_actions_for_job(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn, "job-001")
        self._create_job(conn, "job-002")

        service.request_action(job_id="job-001", action_type="maven", parameters={"goal": "a"})
        service.request_action(job_id="job-001", action_type="maven", parameters={"goal": "b"})
        service.request_action(job_id="job-002", action_type="write", parameters={"path": "c", "content": "data"})

        job1_actions = service.list_actions_for_job("job-001")
        assert len(job1_actions) == 2
        conn.close()

    def test_list_pending_actions(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        service.request_action(job_id="job-001", action_type="maven", parameters={"goal": "a"})
        service.request_action(job_id="job-001", action_type="maven", parameters={"goal": "b"})

        pending = service.list_pending_actions()
        assert len(pending) == 2
        for a in pending:
            assert a.status == "pending"
        conn.close()

    def test_to_dto(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        record = service.request_action(
            job_id="job-001",
            action_type="maven",
            parameters={"goal": "compile"},
            requested_by="admin",
        )
        dto = service.to_dto(record)
        assert dto["action_id"] == record.action_id
        assert dto["action_type"] == "maven"
        assert dto["parameters"] == {"goal": "compile"}
        assert dto["status"] == "pending"
        conn.close()

    def test_to_dto_includes_redacted_fields(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        record = service.request_action(
            job_id="job-001",
            action_type="maven",
            parameters={"goal": "compile"},
        )
        dto = service.to_dto(record)
        assert "parameters_checksum" in dto
        assert "requested_by" in dto
        assert "requested_at" in dto


# ── ALLOWED_ACTION_TYPES tests ──────────────────────────────────────


class TestAllowedActionTypes:
    """ALLOWED_ACTION_TYPES configuration."""

    def test_maven_is_allowed(self) -> None:
        assert "maven" in ALLOWED_ACTION_TYPES

    def test_write_is_allowed(self) -> None:
        assert "write" in ALLOWED_ACTION_TYPES

    def test_shell_is_not_allowed(self) -> None:
        assert "shell" not in ALLOWED_ACTION_TYPES
