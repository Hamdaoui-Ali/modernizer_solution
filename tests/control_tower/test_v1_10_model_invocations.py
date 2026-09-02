"""Focused tests for V1-10: Audit model invocations."""

from __future__ import annotations

import json
import sqlite3

import pytest

from migration_factory.control_tower.domain.entities import V1ModelInvocationRecord


# ── Migration tests ──────────────────────────────────────────────────


class TestV1ModelInvocationsMigration:
    """v1_model_invocations SQL migration produces correct schema."""

    MIGRATION_PATH = (
        "migration_factory/control_tower/infrastructure/sqlite/migrations"
        "/0015_v1_model_invocations.sql"
    )

    def _apply_migration(self, tmp_path) -> sqlite3.Connection:
        db_path = tmp_path / "test_v1_model_invocations.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        with open(self.MIGRATION_PATH) as f:
            cur.executescript(f.read())
        return conn

    def test_invocations_table_exists(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v1_model_invocations'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_has_job_id_index(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_v1_model_invocations_job_id'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_has_created_at_index(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_v1_model_invocations_created_at'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_append_only_trigger_prevents_update(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO v1_model_invocations (invocation_id, created_at) "
            "VALUES ('test-1', '2026-06-12T00:00:00.000000Z')"
        )
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="append-only"):
            cur.execute(
                "UPDATE v1_model_invocations SET profile_id = 'test' WHERE invocation_id = 'test-1'"
            )
        conn.close()

    def test_append_only_trigger_prevents_delete(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO v1_model_invocations (invocation_id, created_at) "
            "VALUES ('test-2', '2026-06-12T00:00:00.000000Z')"
        )
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="append-only"):
            cur.execute("DELETE FROM v1_model_invocations WHERE invocation_id = 'test-2'")
        conn.close()

    def test_insert_and_read_full_record(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO v1_model_invocations (
                invocation_id, job_id, profile_id, provider_kind, model_name,
                prompt_tokens, completion_tokens, total_tokens, redacted_summary,
                actor_type, actor_id, created_at, correlation_id, causation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "inv-001",
                "job-abc",
                "default-fake",
                "fake",
                "gpt-4",
                100,
                50,
                150,
                "Redacted: model analyzed migration script",
                "api",
                "system",
                "2026-06-12T00:00:00.000000Z",
                "corr-1",
                "caus-1",
            ),
        )
        cur.execute("SELECT * FROM v1_model_invocations WHERE invocation_id = 'inv-001'")
        row = cur.fetchone()
        assert row is not None
        assert row["invocation_id"] == "inv-001"
        assert row["job_id"] == "job-abc"
        assert row["profile_id"] == "default-fake"
        assert row["provider_kind"] == "fake"
        assert row["model_name"] == "gpt-4"
        assert row["prompt_tokens"] == 100
        assert row["completion_tokens"] == 50
        assert row["total_tokens"] == 150
        assert row["redacted_summary"] == "Redacted: model analyzed migration script"
        assert row["actor_type"] == "api"
        assert row["actor_id"] == "system"
        assert row["created_at"] == "2026-06-12T00:00:00.000000Z"
        assert row["correlation_id"] == "corr-1"
        assert row["causation_id"] == "caus-1"
        conn.close()

    def test_nullable_fields_accept_none(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO v1_model_invocations (invocation_id, created_at) "
            "VALUES ('inv-null-test', '2026-06-12T00:00:00.000000Z')"
        )
        cur.execute(
            "SELECT * FROM v1_model_invocations WHERE invocation_id = 'inv-null-test'"
        )
        row = cur.fetchone()
        assert row is not None
        assert row["invocation_id"] == "inv-null-test"
        assert row["job_id"] is None
        assert row["profile_id"] is None
        assert row["provider_kind"] is None
        assert row["model_name"] is None
        assert row["prompt_tokens"] is None
        assert row["completion_tokens"] is None
        assert row["total_tokens"] is None
        assert row["redacted_summary"] is None
        assert row["actor_type"] is None
        assert row["actor_id"] is None
        assert row["correlation_id"] is None
        assert row["causation_id"] is None
        conn.close()

    def test_foreign_key_references_job(self, tmp_path) -> None:
        """FK constraint requires migration_jobs table to exist.

        This test skips FK validation because the standalone migration
        does not create the migration_jobs table. FK enforcement is
        tested at integration level.
        """
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        # The table references migration_jobs but only in schema;
        # without that table present, FK check is a no-op unless
        # PRAGMA foreign_keys is on. This test just verifies the
        # schema is valid.
        cur.execute("PRAGMA foreign_keys")
        assert cur.fetchone()[0] == 0
        # Insert works without FK enforcement
        cur.execute(
            "INSERT INTO v1_model_invocations (invocation_id, job_id, created_at) "
            "VALUES ('inv-bad-job-ref', 'nonexistent-job', '2026-06-12T00:00:00.000000Z')"
        )
        conn.close()


# ── Domain model tests ───────────────────────────────────────────────


class TestV1ModelInvocationRecord:
    """V1ModelInvocationRecord dataclass behavior."""

    def test_create_minimal_record(self) -> None:
        record = V1ModelInvocationRecord(
            invocation_id="inv-001",
            created_at="2026-06-12T00:00:00.000000Z",
        )
        assert record.invocation_id == "inv-001"
        assert record.job_id is None
        assert record.profile_id is None
        assert record.provider_kind is None
        assert record.model_name is None
        assert record.prompt_tokens is None
        assert record.completion_tokens is None
        assert record.total_tokens is None
        assert record.redacted_summary is None
        assert record.actor_type is None
        assert record.actor_id is None

    def test_create_full_record(self) -> None:
        record = V1ModelInvocationRecord(
            invocation_id="inv-002",
            job_id="job-abc",
            profile_id="default-fake",
            provider_kind="fake",
            model_name="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            redacted_summary="Redacted: migration analysis",
            actor_type="api",
            actor_id="system",
            created_at="2026-06-12T00:00:00.000000Z",
            correlation_id="corr-1",
            causation_id="caus-1",
        )
        assert record.invocation_id == "inv-002"
        assert record.total_tokens == 150
        assert record.correlation_id == "corr-1"

    def test_record_is_frozen(self) -> None:
        record = V1ModelInvocationRecord(
            invocation_id="inv-003",
            created_at="2026-06-12T00:00:00.000000Z",
        )
        with pytest.raises(AttributeError):
            record.profile_id = "changed"  # type: ignore[misc]

    def test_record_has_slots(self) -> None:
        record = V1ModelInvocationRecord(
            invocation_id="inv-004",
            created_at="2026-06-12T00:00:00.000000Z",
        )
        # Verify the record uses __slots__ (frozen dataclass rejects any setattr)
        assert hasattr(record, "__slots__")
        with pytest.raises((AttributeError, TypeError)):
            object.__setattr__(record, "new_attr", "test")


# ── Repository tests ────────────────────────────────────────────────


class TestSqliteV1ModelInvocationRepository:
    """SqliteV1ModelInvocationRepository CRUD behavior."""

    MIGRATION_PATH = (
        "migration_factory/control_tower/infrastructure/sqlite/migrations"
        "/0015_v1_model_invocations.sql"
    )

    def _create_repo(self, tmp_path):
        db_path = tmp_path / "test_repo.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        with open(self.MIGRATION_PATH) as f:
            cur.executescript(f.read())
        from migration_factory.control_tower.infrastructure.sqlite.repositories import (
            SqliteV1ModelInvocationRepository,
        )
        repo = SqliteV1ModelInvocationRepository(conn)
        return conn, repo

    def test_insert_and_get(self, tmp_path) -> None:
        conn, repo = self._create_repo(tmp_path)
        record = V1ModelInvocationRecord(
            invocation_id="inv-repo-001",
            job_id="job-abc",
            profile_id="default-fake",
            provider_kind="fake",
            model_name="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            redacted_summary="Redacted: test invocation",
            actor_type="api",
            actor_id="system",
            created_at="2026-06-12T00:00:00.000000Z",
        )
        repo.insert(record)
        got = repo.get("inv-repo-001")
        assert got is not None
        assert got.invocation_id == "inv-repo-001"
        assert got.total_tokens == 150
        conn.close()

    def test_get_missing_returns_none(self, tmp_path) -> None:
        conn, repo = self._create_repo(tmp_path)
        got = repo.get("nonexistent")
        assert got is None
        conn.close()

    def test_list_returns_all(self, tmp_path) -> None:
        conn, repo = self._create_repo(tmp_path)
        for i in range(3):
            repo.insert(V1ModelInvocationRecord(
                invocation_id=f"inv-list-{i}",
                created_at=f"2026-06-12T00:00:00.00000{i}Z",
            ))
        all_records = repo.list()
        assert len(all_records) == 3
        # Should be ordered by created_at DESC
        ids = [r.invocation_id for r in all_records]
        assert ids == ["inv-list-2", "inv-list-1", "inv-list-0"]
        conn.close()

    def test_list_for_job(self, tmp_path) -> None:
        conn, repo = self._create_repo(tmp_path)
        for i in range(3):
            repo.insert(V1ModelInvocationRecord(
                invocation_id=f"inv-job-{i}",
                job_id="job-xyz",
                created_at=f"2026-06-12T00:00:00.00000{i}Z",
            ))
        repo.insert(V1ModelInvocationRecord(
            invocation_id="inv-other",
            job_id="job-other",
            created_at="2026-06-12T00:00:00.00000Z",
        ))
        job_records = repo.list_for_job("job-xyz")
        assert len(job_records) == 3
        conn.close()
