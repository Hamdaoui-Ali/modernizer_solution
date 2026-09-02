from __future__ import annotations

import sqlite3

import pytest

from migration_factory.control_tower.application.plan_amendments import (
    PlanAmendmentService,
    PlanChange,
)
from migration_factory.control_tower.domain.entities import V1PlanAmendmentRecord
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import migrated_connection, seed_job


class TestV1PlanAmendmentsMigration:
    MIGRATION_PATH = (
        "migration_factory/control_tower/infrastructure/sqlite/migrations"
        "/0020_v1_plan_amendments.sql"
    )

    def _apply_migration(self, tmp_path) -> sqlite3.Connection:
        db_path = tmp_path / "test_v1_plan_amendments.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        with open(self.MIGRATION_PATH, encoding="utf-8") as handle:
            conn.cursor().executescript(handle.read())
        return conn

    def test_tables_exist(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v1_plan_amendments'"
        )
        assert cur.fetchone() is not None
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v1_plan_revisions'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_append_only_triggers_block_mutation(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE migration_jobs (job_id TEXT PRIMARY KEY)"
        )
        cur.execute("INSERT INTO migration_jobs (job_id) VALUES ('job-1')")
        cur.execute(
            """INSERT INTO v1_plan_amendments (
                amendment_id, job_id, source_kind, title, summary,
                payload_json, payload_checksum, redacted_summary_json,
                created_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "am-1",
                "job-1",
                "manual",
                "Title",
                "Summary",
                "{}",
                "chk",
                "{}",
                "2026-06-12T00:00:00Z",
                "tester",
            ),
        )
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="append-only"):
            cur.execute("UPDATE v1_plan_amendments SET title = 'changed' WHERE amendment_id = 'am-1'")
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="append-only"):
            cur.execute("DELETE FROM v1_plan_amendments WHERE amendment_id = 'am-1'")
        conn.close()


class TestPlanAmendmentService:
    def test_valid_plan_amendment_persists(self, tmp_path) -> None:
        conn = migrated_connection(tmp_path)
        seed_job(conn)
        service = PlanAmendmentService(lambda: SqliteUnitOfWork(conn))

        record = service.create_amendment(
            job_id="job-1",
            source_kind="manual",
            title="Refine stage docs",
            summary="Clarify stage notes only",
            notes=("safe change",),
            changes=(
                PlanChange(
                    stage_index=1,
                    change_type="documentation",
                    description="Clarify migration note",
                    rationale="Improve operator guidance",
                ),
            ),
            created_by="tester",
        )

        assert record.job_id == "job-1"
        assert record.source_kind == "manual"
        stored = conn.execute(
            "SELECT payload_checksum FROM v1_plan_amendments WHERE amendment_id = ?",
            (record.amendment_id,),
        ).fetchone()
        assert stored is not None
        assert stored["payload_checksum"] == record.payload_checksum

    def test_public_dto_uses_safe_summary(self, tmp_path) -> None:
        conn = migrated_connection(tmp_path)
        seed_job(conn)
        service = PlanAmendmentService(lambda: SqliteUnitOfWork(conn))
        record = service.create_amendment(
            job_id="job-1",
            source_kind="manual",
            title="Docs",
            summary="Docs only",
            changes=(
                PlanChange(
                    stage_index=2,
                    change_type="documentation",
                    description="Note stage 2 expectations",
                ),
            ),
            created_by="tester",
        )

        dto = service.to_amendment_dto(record)
        assert dto.redacted_summary["change_count"] == 1
        assert dto.redacted_summary["affected_stage_indexes"] == [2]
        assert not hasattr(dto, "payload_json")
