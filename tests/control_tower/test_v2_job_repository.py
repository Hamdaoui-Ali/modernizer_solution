"""Tests for V2 job and command repositories."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import (
    SqliteV2JobRepository,
    V2MigrationJobRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
)


def _connection(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        tmp_path / "v2_job_test.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    return conn


def _make_job(setup_id: str = "s1", job_id: str | None = None) -> V2MigrationJobRecord:
    now = utc_now_text()
    return V2MigrationJobRecord(
        job_id=job_id or uuid4().hex,
        setup_id=setup_id,
        setup_checksum="abc123",
        pipeline_id="springboot-216-to-356-java21-three-stage",
        stage_chain_json=json.dumps([
            {"stage_index": 1, "status": "queued"},
            {"stage_index": 2, "status": "pending"},
            {"stage_index": 3, "status": "pending"},
        ]),
        status="created",
        created_at=now,
        updated_at=now,
        correlation_id=None,
    )


def _make_command(job_id: str, stage_index: int = 1) -> V2StageCommandRecord:
    now = utc_now_text()
    return V2StageCommandRecord(
        command_id=uuid4().hex,
        job_id=job_id,
        stage_index=stage_index,
        manifest_checksum="v2-stage1",
        argv_json=json.dumps(["python", "-m", "runner", "--run-id", "test"]),
        env_json=json.dumps({"JAVA_HOME": "/opt/java/11"}),
        status="manifest_ready",
        created_at=now,
        updated_at=now,
        result_json=None,
    )


# ── Job repository tests ────────────────────────────────────────────


class TestSqliteV2JobRepository:

    def test_save_and_get(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2JobRepository(conn)
        record = _make_job()
        repo.save(record)
        loaded = repo.get(record.job_id)
        assert loaded is not None
        assert loaded.job_id == record.job_id
        assert loaded.setup_id == record.setup_id
        assert loaded.setup_checksum == record.setup_checksum
        assert loaded.pipeline_id == record.pipeline_id
        assert loaded.status == "created"

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2JobRepository(conn)
        assert repo.get("nonexistent") is None

    def test_list(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2JobRepository(conn)
        j1 = _make_job(setup_id="s1", job_id="j1")
        j2 = _make_job(setup_id="s2", job_id="j2")
        repo.save(j1)
        repo.save(j2)
        all_jobs = repo.list()
        assert len(all_jobs) == 2
        assert all_jobs[0].job_id in ("j1", "j2")

    def test_list_by_setup(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2JobRepository(conn)
        j1 = _make_job(setup_id="s1", job_id="j1")
        j2 = _make_job(setup_id="s1", job_id="j2")
        j3 = _make_job(setup_id="s2", job_id="j3")
        repo.save(j1)
        repo.save(j2)
        repo.save(j3)
        s1_jobs = repo.list_by_setup("s1")
        assert len(s1_jobs) == 2
        assert all(j.setup_id == "s1" for j in s1_jobs)

    def test_list_by_status(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2JobRepository(conn)
        j1 = _make_job(job_id="j1")
        j2 = _make_job(job_id="j2")
        repo.save(j1)
        repo.save(j2)
        created_jobs = repo.list_by_status("created")
        assert len(created_jobs) == 2
        assert repo.list_by_status("running") == ()

    def test_save_twice_raises(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2JobRepository(conn)
        record = _make_job()
        repo.save(record)
        with pytest.raises(Exception, match="UNIQUE|PRIMARY KEY"):
            repo.save(record)

    def test_persistence_across_connections(self, tmp_path: Path) -> None:
        """Verify data survives connection close and re-open."""
        db_path = tmp_path / "v2_job_persist.sqlite3"
        # First connection — save
        conn1 = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None, timeout=5.0
        )
        conn1.row_factory = sqlite3.Row
        conn1.execute("PRAGMA foreign_keys = ON")
        apply_pending_migrations(conn1)
        repo1 = SqliteV2JobRepository(conn1)
        record = _make_job()
        repo1.save(record)
        conn1.close()

        # Second connection — read
        conn2 = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None, timeout=5.0
        )
        conn2.row_factory = sqlite3.Row
        conn2.execute("PRAGMA foreign_keys = ON")
        repo2 = SqliteV2JobRepository(conn2)
        loaded = repo2.get(record.job_id)
        assert loaded is not None
        assert loaded.job_id == record.job_id
        assert loaded.setup_checksum == record.setup_checksum
        conn2.close()


# ── Command repository tests ────────────────────────────────────────


class TestSqliteV2CommandRepository:

    def test_save_and_get(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2CommandRepository(conn)
        job = _make_job()
        SqliteV2JobRepository(conn).save(job)
        cmd = _make_command(job.job_id)
        repo.save(cmd)
        loaded = repo.get(cmd.command_id)
        assert loaded is not None
        assert loaded.command_id == cmd.command_id
        assert loaded.job_id == job.job_id
        assert loaded.stage_index == 1
        assert loaded.status == "manifest_ready"

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2CommandRepository(conn)
        assert repo.get("nonexistent") is None

    def test_list_by_job(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2CommandRepository(conn)
        job_repo = SqliteV2JobRepository(conn)

        job = _make_job()
        job_repo.save(job)
        c1 = _make_command(job.job_id, stage_index=1)
        c2 = _make_command(job.job_id, stage_index=2)
        repo.save(c1)
        repo.save(c2)

        cmds = repo.list_by_job(job.job_id)
        assert len(cmds) == 2

    def test_list_by_job_and_stage(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2CommandRepository(conn)
        job_repo = SqliteV2JobRepository(conn)

        job = _make_job()
        job_repo.save(job)
        c1 = _make_command(job.job_id, stage_index=1)
        c2 = _make_command(job.job_id, stage_index=1)
        c3 = _make_command(job.job_id, stage_index=2)
        repo.save(c1)
        repo.save(c2)
        repo.save(c3)

        stage1_cmds = repo.list_by_job_and_stage(job.job_id, 1)
        assert len(stage1_cmds) == 2
        stage2_cmds = repo.list_by_job_and_stage(job.job_id, 2)
        assert len(stage2_cmds) == 1

    def test_save_additional_fields(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2CommandRepository(conn)
        job_repo = SqliteV2JobRepository(conn)

        job = _make_job()
        job_repo.save(job)
        cmd = V2StageCommandRecord(
            command_id="cmd-detail-test",
            job_id=job.job_id,
            stage_index=3,
            manifest_checksum="v2-stage3-check",
            argv_json=json.dumps(["python", "-m", "resume"]),
            env_json=json.dumps({"JAVA_HOME": "/opt/java/21"}),
            status="completed",
            created_at=utc_now_text(),
            updated_at=utc_now_text(),
            result_json=json.dumps({"exit_code": 0, "duration_ms": 1234}),
        )
        repo.save(cmd)
        loaded = repo.get("cmd-detail-test")
        assert loaded is not None
        assert loaded.stage_index == 3
        assert loaded.status == "completed"
        assert json.loads(loaded.argv_json) == ["python", "-m", "resume"]
        assert json.loads(loaded.result_json)["exit_code"] == 0

    def test_save_command_triggers_exist(self, tmp_path: Path) -> None:
        """Command table has the expected structure."""
        conn = _connection(tmp_path)
        repo = SqliteV2CommandRepository(conn)
        job = _make_job()
        SqliteV2JobRepository(conn).save(job)
        cmd = _make_command(job.job_id)
        # Should not raise
        repo.save(cmd)
        loaded = repo.get(cmd.command_id)
        assert loaded is not None
        assert loaded.job_id == job.job_id


# ── Append-only trigger tests ───────────────────────────────────────


class TestV2AppendOnlyTriggers:

    def _check_trigger(self, tmp_path: Path, table: str) -> None:
        conn = _connection(tmp_path)
        # Verify the trigger exists
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
            (f"{table}_no_update",),
        ).fetchall()
        assert len(rows) >= 1, f"Trigger {table}_no_update not found"

        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
            (f"{table}_no_delete",),
        ).fetchall()
        assert len(rows) >= 1, f"Trigger {table}_no_delete not found"

    def test_migration_jobs_triggers_exist(self, tmp_path: Path) -> None:
        self._check_trigger(tmp_path, "v2_migration_jobs")

    def test_stage_commands_triggers_exist(self, tmp_path: Path) -> None:
        self._check_trigger(tmp_path, "v2_stage_commands")
