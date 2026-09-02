"""Focused tests for F15 job013 — v2_artifact_revisions SQLite migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)


def _connection(tmp_path: Path, name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(tmp_path / name),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


def test_migration_creates_table(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_rev.sqlite3")
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_artifact_revisions'"
    ).fetchone()
    assert row is not None


def test_insert_draft_revision(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_rev.sqlite3")
    conn.execute(
        """
        INSERT INTO v2_artifact_revisions
            (revision_id, job_id, stage_index, revision_kind, revision_status,
             revision_order, evidence_checksum, artifact_refs_json, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("rev-001", "job-abc", 1, "analysis", "draft", 0,
         "sha256:abc", '["a1"]', "2026-06-17T12:00:00Z", "system"),
    )
    row = conn.execute(
        "SELECT * FROM v2_artifact_revisions WHERE revision_id = 'rev-001'"
    ).fetchone()
    assert row["revision_kind"] == "analysis"
    assert row["revision_status"] == "draft"


def test_stage_index_constraint(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_rev.sqlite3")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO v2_artifact_revisions
                (revision_id, job_id, stage_index, revision_kind, revision_status,
                 revision_order, evidence_checksum, artifact_refs_json, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("rev-bad", "job-abc", 0, "analysis", "draft", 0,
             "sha256:abc", "[]", "2026-06-17T12:00:00Z", "system"),
        )


def test_accepted_revision_uniqueness(tmp_path: Path) -> None:
    """At most one ACCEPTED revision per (job_id, stage_index, revision_kind)."""
    conn = _connection(tmp_path, "test_rev.sqlite3")
    conn.execute(
        """
        INSERT INTO v2_artifact_revisions
            (revision_id, job_id, stage_index, revision_kind, revision_status,
             revision_order, evidence_checksum, artifact_refs_json, created_at, created_by,
             accepted_at, accepted_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("rev-a1", "job-abc", 1, "analysis", "accepted", 0,
         "sha256:abc", '["a1"]', "2026-06-17T12:00:00Z", "system",
         "2026-06-17T13:00:00Z", "user-1"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO v2_artifact_revisions
                (revision_id, job_id, stage_index, revision_kind, revision_status,
                 revision_order, evidence_checksum, artifact_refs_json, created_at, created_by,
                 accepted_at, accepted_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("rev-a2", "job-abc", 1, "analysis", "accepted", 1,
             "sha256:xyz", '["a2"]', "2026-06-17T14:00:00Z", "system",
             "2026-06-17T15:00:00Z", "user-2"),
        )


def test_different_kind_accepted_allowed(tmp_path: Path) -> None:
    """Different revision_kind can each have an accepted revision."""
    conn = _connection(tmp_path, "test_rev.sqlite3")
    conn.execute(
        """
        INSERT INTO v2_artifact_revisions
            (revision_id, job_id, stage_index, revision_kind, revision_status,
             revision_order, evidence_checksum, artifact_refs_json, created_at, created_by,
             accepted_at, accepted_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("rev-a", "job-abc", 1, "analysis", "accepted", 0,
         "sha256:abc", "[]", "2026-06-17T12:00:00Z", "system",
         "2026-06-17T13:00:00Z", "user-1"),
    )
    conn.execute(
        """
        INSERT INTO v2_artifact_revisions
            (revision_id, job_id, stage_index, revision_kind, revision_status,
             revision_order, evidence_checksum, artifact_refs_json, created_at, created_by,
             accepted_at, accepted_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("rev-p", "job-abc", 1, "planning", "accepted", 0,
         "sha256:def", "[]", "2026-06-17T12:00:00Z", "system",
         "2026-06-17T13:00:00Z", "user-1"),
    )


def test_lineage_fields(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_rev.sqlite3")
    conn.execute(
        """
        INSERT INTO v2_artifact_revisions
            (revision_id, job_id, stage_index, revision_kind, revision_status,
             revision_order, evidence_checksum, prior_revision_checksum,
             artifact_refs_json, prior_revision_id, superseded_by_revision_id,
             created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("rev-v1", "job-abc", 1, "analysis", "superseded", 0,
         "sha256:v1", None, "[]", None, "rev-v2",
         "2026-06-17T12:00:00Z", "system"),
    )
    row = conn.execute(
        "SELECT prior_revision_id, superseded_by_revision_id FROM v2_artifact_revisions WHERE revision_id = 'rev-v1'"
    ).fetchone()
    assert row["prior_revision_id"] is None
    assert row["superseded_by_revision_id"] == "rev-v2"


def test_update_blocked(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_rev.sqlite3")
    conn.execute(
        """
        INSERT INTO v2_artifact_revisions
            (revision_id, job_id, stage_index, revision_kind, revision_status,
             revision_order, evidence_checksum, artifact_refs_json, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("rev-u1", "job-abc", 1, "analysis", "draft", 0,
         "sha256:abc", "[]", "2026-06-17T12:00:00Z", "system"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE v2_artifact_revisions SET revision_status = 'accepted' WHERE revision_id = 'rev-u1'"
        )


def test_delete_blocked(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_rev.sqlite3")
    conn.execute(
        """
        INSERT INTO v2_artifact_revisions
            (revision_id, job_id, stage_index, revision_kind, revision_status,
             revision_order, evidence_checksum, artifact_refs_json, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("rev-d1", "job-abc", 1, "analysis", "draft", 0,
         "sha256:abc", "[]", "2026-06-17T12:00:00Z", "system"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM v2_artifact_revisions WHERE revision_id = 'rev-d1'")


def test_accepted_at_gate_id_nullable(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_rev.sqlite3")
    conn.execute(
        """
        INSERT INTO v2_artifact_revisions
            (revision_id, job_id, stage_index, revision_kind, revision_status,
             revision_order, evidence_checksum, artifact_refs_json, created_at, created_by,
             accepted_at_gate_id, accepted_at, accepted_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("rev-g1", "job-abc", 1, "analysis", "accepted", 0,
         "sha256:abc", "[]", "2026-06-17T12:00:00Z", "system",
         "gate-001", "2026-06-17T13:00:00Z", "user-1"),
    )
    row = conn.execute("SELECT accepted_at_gate_id FROM v2_artifact_revisions WHERE revision_id = 'rev-g1'").fetchone()
    assert row["accepted_at_gate_id"] == "gate-001"


def _apply_up_to_0045(conn: sqlite3.Connection) -> None:
    from migration_factory.control_tower.infrastructure.sqlite.migrations import (
        _apply_single_migration,
        discover_migrations,
    )
    for m in discover_migrations():
        _apply_single_migration(conn, m)
        if m.version == 45:
            break


def test_migration_0046_artifact_revisions_stage4(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        str(tmp_path / "test_0046_rev.sqlite3"),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    try:
        _apply_up_to_0045(conn)

        job = "job-0046-r"

        # Seed with Stage 1-3 data
        conn.execute(
            "INSERT INTO v2_artifact_revisions (revision_id, job_id, stage_index, revision_kind, revision_status, revision_order, evidence_checksum, artifact_refs_json, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("r-pre1", job, 1, "analysis", "draft", 0, "chk1", "[]", "2026-06-17T12:00:00Z", "system"),
        )
        conn.execute(
            "INSERT INTO v2_artifact_revisions (revision_id, job_id, stage_index, revision_kind, revision_status, revision_order, evidence_checksum, artifact_refs_json, created_at, created_by, accepted_at, accepted_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("r-pre2", job, 2, "planning", "accepted", 0, "chk2", "[]", "2026-06-17T13:00:00Z", "system", "2026-06-17T14:00:00Z", "user"),
        )

        # Apply 0046
        from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
        apply_pending_migrations(conn)

        # Verify Stage 1-3 values survive
        row = conn.execute("SELECT stage_index FROM v2_artifact_revisions WHERE revision_id = 'r-pre1'").fetchone()
        assert row["stage_index"] == 1
        row = conn.execute("SELECT stage_index FROM v2_artifact_revisions WHERE revision_id = 'r-pre2'").fetchone()
        assert row["stage_index"] == 2

        # Stage 4 insert succeeds
        conn.execute(
            "INSERT INTO v2_artifact_revisions (revision_id, job_id, stage_index, revision_kind, revision_status, evidence_checksum, artifact_refs_json, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("r-s4", job, 4, "analysis", "draft", "chk4", "[]", "2026-06-17T15:00:00Z", "system"),
        )

        # Stage 5 insert fails
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO v2_artifact_revisions (revision_id, job_id, stage_index, revision_kind, evidence_checksum, artifact_refs_json, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("r-s5", job, 5, "analysis", "chk5", "[]", "2026-06-17T16:00:00Z", "system"),
            )

        # Accepted uniqueness still enforced for Stage 4
        conn.execute(
            "INSERT INTO v2_artifact_revisions (revision_id, job_id, stage_index, revision_kind, revision_status, evidence_checksum, artifact_refs_json, created_at, created_by, accepted_at, accepted_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("r-s4acc", job, 4, "planning", "accepted", "chk4a", "[]", "2026-06-17T17:00:00Z", "system", "2026-06-17T18:00:00Z", "user"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO v2_artifact_revisions (revision_id, job_id, stage_index, revision_kind, revision_status, evidence_checksum, artifact_refs_json, created_at, created_by, accepted_at, accepted_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("r-s4acc2", job, 4, "planning", "accepted", "chk4b", "[]", "2026-06-17T19:00:00Z", "system", "2026-06-17T20:00:00Z", "user"),
            )

        # Different kind at same job+stage+accepted is still allowed
        conn.execute(
            "INSERT INTO v2_artifact_revisions (revision_id, job_id, stage_index, revision_kind, revision_status, evidence_checksum, artifact_refs_json, created_at, created_by, accepted_at, accepted_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("r-s4acc3", job, 4, "analysis", "accepted", "chk4c", "[]", "2026-06-17T21:00:00Z", "system", "2026-06-17T22:00:00Z", "user"),
        )

        # UPDATE still blocked
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE v2_artifact_revisions SET revision_status = 'accepted' WHERE revision_id = 'r-pre1'"
            )

        # DELETE still blocked
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM v2_artifact_revisions WHERE revision_id = 'r-pre1'")
    finally:
        conn.close()
