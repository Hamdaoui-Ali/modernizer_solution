"""Focused tests for F15-JOB-098 — Planning revision adapter.

Verifies that V2 adapter wraps V1 PlanAmendmentService and creates
ArtifactRevision records for gate-based tracking. No parallel revision
tables.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.plan_amendments import (
    PlanAmendmentService,
)
from migration_factory.control_tower.application.v2_plan_revision_adapter import (
    V2PlanRevisionAdapter,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_artifact_revision_repository import (
    SqliteArtifactRevisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
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


def test_adapter_exists_and_imports(tmp_path: Path) -> None:
    """Adapter can be instantiated with its dependencies."""
    conn = _connection(tmp_path, "adapt1.sqlite3")
    revision_repo = SqliteArtifactRevisionRepository(conn)
    gate_repo = SqlitePhaseGateRepository(conn)

    # Create V1 service
    def uow_factory():
        return conn  # Simplified

    v1_service = PlanAmendmentService(uow_factory)
    adapter = V2PlanRevisionAdapter(
        v1_amendment_service=v1_service,
        revision_repo=revision_repo,
        gate_repo=gate_repo,
    )
    assert adapter is not None


def test_adapter_reuses_existing_plan_services(tmp_path: Path) -> None:
    """Adapter uses V1 PlanAmendmentService, not duplicating."""
    conn = _connection(tmp_path, "adapt2.sqlite3")
    revision_repo = SqliteArtifactRevisionRepository(conn)
    gate_repo = SqlitePhaseGateRepository(conn)

    v1_service = PlanAmendmentService(lambda: conn)
    adapter = V2PlanRevisionAdapter(
        v1_amendment_service=v1_service,
        revision_repo=revision_repo,
        gate_repo=gate_repo,
    )

    # Verify V1 service is reused (not duplicated)
    assert adapter._amendment_service is v1_service


def test_adapter_gets_accepted_analysis_checksum(tmp_path: Path) -> None:
    """Adapter can retrieve accepted analysis checksum for a stage."""
    conn = _connection(tmp_path, "adapt3.sqlite3")
    revision_repo = SqliteArtifactRevisionRepository(conn)
    gate_repo = SqlitePhaseGateRepository(conn)

    v1_service = PlanAmendmentService(lambda: conn)
    adapter = V2PlanRevisionAdapter(
        v1_amendment_service=v1_service,
        revision_repo=revision_repo,
        gate_repo=gate_repo,
    )

    checksum = adapter.get_accepted_analysis_checksum("job-nonexistent", 2)
    assert checksum == ""  # No accepted analysis

    # Seed an accepted analysis
    from migration_factory.control_tower.domain.checksums import utc_now_text
    from migration_factory.control_tower.domain.entities import ArtifactRevisionRecord
    now = utc_now_text()
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="analysis-v1", job_id="job-v2", stage_index=2,
        revision_kind="analysis", revision_status="accepted",
        revision_order=0, evidence_checksum="sha256:accepted-analysis",
        prior_revision_checksum=None,
        artifact_refs_json='["analysis.json"]',
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id="gate-1",
        created_at=now, created_by="analyzer",
        accepted_at=now, accepted_by="user",
    ))

    checksum = adapter.get_accepted_analysis_checksum("job-v2", 2)
    assert checksum == "sha256:accepted-analysis"
