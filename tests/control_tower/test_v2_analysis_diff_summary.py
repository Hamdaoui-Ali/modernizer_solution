"""Focused tests for F15-JOB-081 — Analysis diff summary.

Verifies that analysis revisions can be compared for diffs, showing
added/removed/modified artifacts, and no full raw sensitive content leaks.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_analysis_diff_summary import (
    V2AnalysisDiffService,
    AnalysisDiffResult,
    AnalysisDiffEntry,
)
from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.domain.entities import ArtifactRevisionRecord
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_artifact_revision_repository import (
    SqliteArtifactRevisionRepository,
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


def _seed_revisions(revision_repo, job="job-diff-1", stage=1):
    """Seed two analysis revisions for diff testing."""
    now = utc_now_text()
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="analysis-v1",
        job_id=job, stage_index=stage,
        revision_kind="analysis", revision_status="accepted",
        revision_order=0,
        evidence_checksum="sha256:analysis-evidence-v1",
        prior_revision_checksum=None,
        artifact_refs_json=json.dumps({
            "report": "sha256:report-v1",
            "dependencies": "sha256:deps-v1",
        }),
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id="gate-v1",
        created_at=now, created_by="analyzer",
        accepted_at=now, accepted_by="user",
    ))
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="analysis-v2",
        job_id=job, stage_index=stage,
        revision_kind="analysis", revision_status="draft",
        revision_order=1,
        evidence_checksum="sha256:analysis-evidence-v2",
        prior_revision_checksum="sha256:analysis-evidence-v1",
        artifact_refs_json=json.dumps({
            "report": "sha256:report-v2",
            "dependencies": "sha256:deps-v1",
            "security": "sha256:security-v1",
        }),
        prior_revision_id="analysis-v1", superseded_by_revision_id=None,
        accepted_at_gate_id="gate-v2",
        created_at=now, created_by="analyzer",
        accepted_at=None, accepted_by=None,
    ))


def test_analysis_diff_added_and_modified(tmp_path: Path) -> None:
    """Diff shows added and modified artifacts between revisions."""
    conn = _connection(tmp_path, "diff1.sqlite3")
    revision_repo = SqliteArtifactRevisionRepository(conn)
    _seed_revisions(revision_repo)
    service = V2AnalysisDiffService(revision_repo)

    result = service.compute_analysis_diff("job-diff-1", 1)

    assert result.added_count == 1  # security artifact added
    assert result.modified_count == 1  # report checksum changed
    assert result.removed_count == 0
    assert result.unchanged_count >= 1  # dependencies unchanged
    assert len(result.entries) >= 3

    # Verify redaction
    for entry in result.entries:
        assert "sha256:" in (entry.old_checksum or "") or entry.old_checksum is None
        assert "sha256:" in (entry.new_checksum or "") or entry.new_checksum is None


def test_analysis_diff_with_explicit_revisions(tmp_path: Path) -> None:
    """Diff works with explicit revision IDs."""
    conn = _connection(tmp_path, "diff2.sqlite3")
    revision_repo = SqliteArtifactRevisionRepository(conn)
    _seed_revisions(revision_repo)
    service = V2AnalysisDiffService(revision_repo)

    result = service.compute_analysis_diff(
        "job-diff-1", 1,
        prior_revision_id="analysis-v1",
        current_revision_id="analysis-v2",
    )

    assert result.prior_revision_id == "analysis-v1"
    assert result.current_revision_id == "analysis-v2"


def test_analysis_diff_insufficient_revisions(tmp_path: Path) -> None:
    """Diff returns empty result with only one revision."""
    conn = _connection(tmp_path, "diff3.sqlite3")
    revision_repo = SqliteArtifactRevisionRepository(conn)
    service = V2AnalysisDiffService(revision_repo)

    now = utc_now_text()
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="single-v1",
        job_id="job-single", stage_index=1,
        revision_kind="analysis", revision_status="draft",
        revision_order=0, evidence_checksum="sha256:single",
        prior_revision_checksum=None,
        artifact_refs_json='{"report": "sha256:single-report"}',
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id="gate-single",
        created_at=now, created_by="analyzer",
        accepted_at=None, accepted_by=None,
    ))

    result = service.compute_analysis_diff("job-single", 1)
    assert len(result.entries) == 0
    assert result.added_count == 0


def test_analysis_diff_to_dict_redacted(tmp_path: Path) -> None:
    """to_dict produces redacted output with no sensitive leaks."""
    conn = _connection(tmp_path, "diff4.sqlite3")
    revision_repo = SqliteArtifactRevisionRepository(conn)
    _seed_revisions(revision_repo)
    service = V2AnalysisDiffService(revision_repo)

    result = service.compute_analysis_diff("job-diff-1", 1)
    d = service.to_dict(result)

    assert "diff_id" in d
    assert "entries" in d
    assert isinstance(d["entries"], list)
    for entry in d["entries"]:
        assert "kind" in entry
        assert "summary" in entry
    assert d["checksum"] is not None


def test_analysis_diff_metadata_comparison(tmp_path: Path) -> None:
    """Diff compares metadata when artifact refs are empty."""
    conn = _connection(tmp_path, "diff5.sqlite3")
    revision_repo = SqliteArtifactRevisionRepository(conn)
    service = V2AnalysisDiffService(revision_repo)
    now = utc_now_text()

    revision_repo.save(ArtifactRevisionRecord(
        revision_id="meta-v1", job_id="job-meta", stage_index=1,
        revision_kind="analysis", revision_status="accepted",
        revision_order=0, evidence_checksum="sha256:v1",
        prior_revision_checksum=None,
        artifact_refs_json='{}',
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id="gate-m1", created_at=now,
        created_by="analyzer", accepted_at=now, accepted_by="user",
    ))
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="meta-v2", job_id="job-meta", stage_index=1,
        revision_kind="analysis", revision_status="draft",
        revision_order=1, evidence_checksum="sha256:v2",
        prior_revision_checksum="sha256:v1",
        artifact_refs_json='{}',
        prior_revision_id="meta-v1", superseded_by_revision_id=None,
        accepted_at_gate_id="gate-m2", created_at=now,
        created_by="user", accepted_at=None, accepted_by=None,
    ))

    result = service.compute_analysis_diff("job-meta", 1)
    # Metadata comparison should find evidence and creator changes
    assert len(result.entries) >= 1
    assert result.checksum is not None


def test_analysis_diff_no_raw_sensitive_content_leak(tmp_path: Path) -> None:
    """Diff entries contain no full raw sensitive content."""
    conn = _connection(tmp_path, "diff6.sqlite3")
    revision_repo = SqliteArtifactRevisionRepository(conn)
    _seed_revisions(revision_repo)
    service = V2AnalysisDiffService(revision_repo)

    result = service.compute_analysis_diff("job-diff-1", 1)

    for entry in result.entries:
        # Detail should not contain absolute paths or raw secrets
        assert "/tmp/" not in entry.detail
        assert "executive_order" not in entry.detail.lower()
        assert len(entry.summary) < 500
        assert len(entry.detail) < 600
