"""SQLite repository for F15 artifact revisions."""

from __future__ import annotations

import sqlite3

from migration_factory.control_tower.domain.entities import ArtifactRevisionRecord


class SqliteArtifactRevisionRepository:
    """Repository for v2_artifact_revisions append-only table.

    Revisions are never updated. New revisions supersede old ones
    by inserting new rows and setting superseded_by_revision_id
    on prior rows via save (the save method for superseding is
    called by the service layer).
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, record: ArtifactRevisionRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_artifact_revisions (
                revision_id, job_id, stage_index, revision_kind,
                revision_status, revision_order, evidence_checksum,
                prior_revision_checksum, artifact_refs_json,
                prior_revision_id, superseded_by_revision_id,
                accepted_at_gate_id, created_at, created_by,
                accepted_at, accepted_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.revision_id,
                record.job_id,
                record.stage_index,
                record.revision_kind,
                record.revision_status,
                record.revision_order,
                record.evidence_checksum,
                record.prior_revision_checksum,
                record.artifact_refs_json,
                record.prior_revision_id,
                record.superseded_by_revision_id,
                record.accepted_at_gate_id,
                record.created_at,
                record.created_by,
                record.accepted_at,
                record.accepted_by,
            ),
        )

    def get(self, revision_id: str) -> ArtifactRevisionRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_artifact_revisions WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_by_job(self, job_id: str) -> tuple[ArtifactRevisionRecord, ...]:
        rows = self._connection.execute(
            """SELECT * FROM v2_artifact_revisions
               WHERE job_id = ?
               ORDER BY stage_index, revision_kind, revision_order DESC""",
            (job_id,),
        ).fetchall()
        return tuple(self._row_to_record(r) for r in rows)

    def list_by_job_and_stage(
        self, job_id: str, stage_index: int
    ) -> tuple[ArtifactRevisionRecord, ...]:
        rows = self._connection.execute(
            """SELECT * FROM v2_artifact_revisions
               WHERE job_id = ? AND stage_index = ?
               ORDER BY revision_kind, revision_order DESC""",
            (job_id, stage_index),
        ).fetchall()
        return tuple(self._row_to_record(r) for r in rows)

    def find_accepted(
        self, job_id: str, stage_index: int, revision_kind: str
    ) -> ArtifactRevisionRecord | None:
        """Find the currently accepted revision for a given kind/stage."""
        row = self._connection.execute(
            """SELECT * FROM v2_artifact_revisions
               WHERE job_id = ? AND stage_index = ? AND revision_kind = ?
                 AND revision_status = 'accepted'
               ORDER BY revision_order DESC
               LIMIT 1""",
            (job_id, stage_index, revision_kind),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def find_latest_by_kind(
        self, job_id: str, stage_index: int, revision_kind: str
    ) -> ArtifactRevisionRecord | None:
        """Find the latest revision of a given kind for a stage."""
        row = self._connection.execute(
            """SELECT * FROM v2_artifact_revisions
               WHERE job_id = ? AND stage_index = ? AND revision_kind = ?
               ORDER BY revision_order DESC
               LIMIT 1""",
            (job_id, stage_index, revision_kind),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_by_prior(self, prior_revision_id: str) -> tuple[ArtifactRevisionRecord, ...]:
        """List revisions that superseded the given prior revision."""
        rows = self._connection.execute(
            """SELECT * FROM v2_artifact_revisions
               WHERE prior_revision_id = ?
               ORDER BY revision_order""",
            (prior_revision_id,),
        ).fetchall()
        return tuple(self._row_to_record(r) for r in rows)

    def _row_to_record(self, row: sqlite3.Row) -> ArtifactRevisionRecord:
        return ArtifactRevisionRecord(
            revision_id=str(row["revision_id"]),
            job_id=str(row["job_id"]),
            stage_index=int(row["stage_index"]),
            revision_kind=str(row["revision_kind"]),
            revision_status=str(row["revision_status"]),
            revision_order=int(row["revision_order"]),
            evidence_checksum=str(row["evidence_checksum"]),
            prior_revision_checksum=(
                str(row["prior_revision_checksum"])
                if row["prior_revision_checksum"] is not None
                else None
            ),
            artifact_refs_json=str(row["artifact_refs_json"]),
            prior_revision_id=(
                str(row["prior_revision_id"])
                if row["prior_revision_id"] is not None
                else None
            ),
            superseded_by_revision_id=(
                str(row["superseded_by_revision_id"])
                if row["superseded_by_revision_id"] is not None
                else None
            ),
            accepted_at_gate_id=(
                str(row["accepted_at_gate_id"])
                if row["accepted_at_gate_id"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            created_by=str(row["created_by"]),
            accepted_at=(
                str(row["accepted_at"])
                if row["accepted_at"] is not None
                else None
            ),
            accepted_by=(
                str(row["accepted_by"])
                if row["accepted_by"] is not None
                else None
            ),
        )
