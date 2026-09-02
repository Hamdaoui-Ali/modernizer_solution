"""Focused tests for V1-11A: Persist context pack manifests."""

from __future__ import annotations

import json
import sqlite3

import pytest

from migration_factory.control_tower.domain.entities import V1ContextPackManifestRecord


# ── Migration tests ──────────────────────────────────────────────────


class TestV1ContextPackManifestsMigration:
    """v1_context_pack_manifests SQL migration produces correct schema."""

    MIGRATION_PATH = (
        "migration_factory/control_tower/infrastructure/sqlite/migrations"
        "/0016_v1_context_packs.sql"
    )

    def _apply_migration(self, tmp_path) -> sqlite3.Connection:
        db_path = tmp_path / "test_context_packs.db"
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
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v1_context_pack_manifests'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_has_job_id_index(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_v1_context_pack_manifests_job_id'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_has_stage_run_id_index(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_v1_context_pack_manifests_stage_run_id'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_has_created_at_index(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_v1_context_pack_manifests_created_at'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_append_only_trigger_prevents_update(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO v1_context_pack_manifests "
            "(manifest_id, pack_type, pack_version, title, checksum, created_at, created_by) "
            "VALUES ('cp-1', 'analysis', '1.0', 'Test pack', 'abc', '2026-06-12T00:00:00Z', 'test')"
        )
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="append-only"):
            cur.execute(
                "UPDATE v1_context_pack_manifests SET title = 'changed' WHERE manifest_id = 'cp-1'"
            )
        conn.close()

    def test_append_only_trigger_prevents_delete(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO v1_context_pack_manifests "
            "(manifest_id, pack_type, pack_version, title, checksum, created_at, created_by) "
            "VALUES ('cp-2', 'analysis', '1.0', 'Test pack', 'abc', '2026-06-12T00:00:00Z', 'test')"
        )
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="append-only"):
            cur.execute("DELETE FROM v1_context_pack_manifests WHERE manifest_id = 'cp-2'")
        conn.close()

    def test_insert_and_read_full_record(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO v1_context_pack_manifests (
                manifest_id, job_id, stage_run_id, pack_type, pack_version,
                title, description, evidence_refs_json, bounds_json,
                redaction_policy, redacted_summary, checksum_algorithm,
                checksum, model_profile_id, model_name, token_count,
                created_at, created_by, correlation_id, causation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "cp-full",
                "job-abc",
                "stage-001",
                "analysis",
                "1.0",
                "Migration analysis pack",
                "Contains stage 1 migration analysis",
                json.dumps([{"type": "file", "path": "src/main.java"}]),
                json.dumps({"max_tokens": 4000}),
                "standard",
                "Redacted: analysis complete",
                "sha256",
                "abc123def456",
                "default-fake",
                "gpt-4",
                1500,
                "2026-06-12T00:00:00.000000Z",
                "system",
                "corr-1",
                "caus-1",
            ),
        )
        cur.execute("SELECT * FROM v1_context_pack_manifests WHERE manifest_id = 'cp-full'")
        row = cur.fetchone()
        assert row is not None
        assert row["manifest_id"] == "cp-full"
        assert row["pack_type"] == "analysis"
        assert row["token_count"] == 1500
        assert row["model_name"] == "gpt-4"
        conn.close()

    def test_nullable_fields_accept_none(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO v1_context_pack_manifests "
            "(manifest_id, pack_type, pack_version, title, checksum, created_at, created_by) "
            "VALUES ('cp-null-test', 'analysis', '1.0', 'Minimal', 'chk', '2026-06-12T00:00:00Z', 'test')"
        )
        cur.execute("SELECT * FROM v1_context_pack_manifests WHERE manifest_id = 'cp-null-test'")
        row = cur.fetchone()
        assert row["description"] is None
        assert row["evidence_refs_json"] is None
        assert row["job_id"] is None
        conn.close()


# ── Domain model tests ───────────────────────────────────────────────


class TestV1ContextPackManifestRecord:
    """V1ContextPackManifestRecord dataclass behavior."""

    def test_create_minimal_record(self) -> None:
        record = V1ContextPackManifestRecord(
            manifest_id="cp-001",
            pack_type="analysis",
            pack_version="1.0",
            title="Minimal pack",
        )
        assert record.manifest_id == "cp-001"
        assert record.job_id is None
        assert record.checksum_algorithm == "sha256"
        assert record.checksum == ""

    def test_create_full_record(self) -> None:
        record = V1ContextPackManifestRecord(
            manifest_id="cp-002",
            pack_type="summary",
            pack_version="2.0",
            title="Full pack",
            job_id="job-abc",
            stage_run_id="stage-001",
            description="Full description",
            evidence_refs_json='[{"file": "test.java"}]',
            bounds_json='{"max": 4000}',
            redaction_policy="strict",
            redacted_summary="Redacted: full analysis",
            checksum_algorithm="sha256",
            checksum="def789",
            model_profile_id="default-fake",
            model_name="gpt-4",
            token_count=2000,
            created_at="2026-06-12T00:00:00Z",
            created_by="system",
            correlation_id="corr-1",
            causation_id="caus-1",
        )
        assert record.token_count == 2000
        assert record.model_name == "gpt-4"

    def test_record_is_frozen(self) -> None:
        record = V1ContextPackManifestRecord(
            manifest_id="cp-003",
            pack_type="test",
            pack_version="1.0",
            title="test",
        )
        with pytest.raises(AttributeError):
            record.title = "changed"  # type: ignore[misc]

    def test_record_has_slots(self) -> None:
        record = V1ContextPackManifestRecord(
            manifest_id="cp-004",
            pack_type="test",
            pack_version="1.0",
            title="test",
        )
        assert hasattr(record, "__slots__")


# ── Repository tests ────────────────────────────────────────────────


class TestSqliteV1ContextPackManifestRepository:
    """SqliteV1ContextPackManifestRepository CRUD behavior."""

    MIGRATION_PATH = (
        "migration_factory/control_tower/infrastructure/sqlite/migrations"
        "/0016_v1_context_packs.sql"
    )

    def _create_repo(self, tmp_path):
        db_path = tmp_path / "test_repo.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        with open(self.MIGRATION_PATH) as f:
            cur.executescript(f.read())
        from migration_factory.control_tower.infrastructure.sqlite.repositories import (
            SqliteV1ContextPackManifestRepository,
        )
        repo = SqliteV1ContextPackManifestRepository(conn)
        return conn, repo

    def test_insert_and_get(self, tmp_path) -> None:
        conn, repo = self._create_repo(tmp_path)
        record = V1ContextPackManifestRecord(
            manifest_id="cp-repo-001",
            pack_type="analysis",
            pack_version="1.0",
            title="Repo test",
            created_at="2026-06-12T00:00:00Z",
            created_by="test",
            checksum="chk123",
        )
        repo.insert(record)
        got = repo.get("cp-repo-001")
        assert got is not None
        assert got.manifest_id == "cp-repo-001"
        assert got.title == "Repo test"
        conn.close()

    def test_get_missing_returns_none(self, tmp_path) -> None:
        conn, repo = self._create_repo(tmp_path)
        got = repo.get("nonexistent")
        assert got is None
        conn.close()

    def test_list_returns_all(self, tmp_path) -> None:
        conn, repo = self._create_repo(tmp_path)
        for i in range(3):
            repo.insert(V1ContextPackManifestRecord(
                manifest_id=f"cp-list-{i}",
                pack_type="analysis",
                pack_version="1.0",
                title=f"Pack {i}",
                created_at=f"2026-06-12T00:00:00.00000{i}Z",
                created_by="test",
                checksum=f"chk{i}",
            ))
        all_records = repo.list()
        assert len(all_records) == 3
        conn.close()

    def test_list_for_job(self, tmp_path) -> None:
        conn, repo = self._create_repo(tmp_path)
        for i in range(3):
            repo.insert(V1ContextPackManifestRecord(
                manifest_id=f"cp-job-{i}",
                job_id="job-xyz",
                pack_type="analysis",
                pack_version="1.0",
                title=f"Job pack {i}",
                created_at=f"2026-06-12T00:00:00.00000{i}Z",
                created_by="test",
                checksum=f"chk{i}",
            ))
        repo.insert(V1ContextPackManifestRecord(
            manifest_id="cp-other",
            job_id="job-other",
            pack_type="analysis",
            pack_version="1.0",
            title="Other",
            created_at="2026-06-12T00:00:00Z",
            created_by="test",
            checksum="chk-other",
        ))
        job_records = repo.list_for_job("job-xyz")
        assert len(job_records) == 3
        for r in job_records:
            assert r.job_id == "job-xyz"
        conn.close()
