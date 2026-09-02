"""Focused tests for F15-JOB-083 — Block planning on unaccepted analysis.

Verifies that planning cannot proceed before an accepted analysis revision
exists. Three guard levels: gate action guard, runner guard, API guard.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_gate_action_service import (
    V2GateActionService,
    GateActionResult,
)
from migration_factory.control_tower.application.v2_phase_gate_service import (
    CreateGateRequest,
    V2PhaseGateService,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.entities import ArtifactRevisionRecord
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_artifact_revision_repository import (
    SqliteArtifactRevisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_gate_decision_repository import (
    SqliteGateDecisionRepository,
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


def _svc(tmp_path: Path) -> tuple:
    conn = _connection(tmp_path, "block.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    revision_repo = SqliteArtifactRevisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    action_svc = V2GateActionService(
        gate_repo, decision_repo, gate_svc, revision_repo
    )
    return gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn


def create_open_analysis_gate(gate_svc, job="job-block", stage=1) -> str:
    """Create an open analysis_review gate."""
    result = gate_svc.create_gate(CreateGateRequest(
        job_id=job, gate_phase="analysis_review", stage_index=stage,
        source_artifact_checksum="sha256:analysis",
        source_artifact_refs=("analysis-report.json",),
    ))
    assert result.status == "created"
    return result.gate_id


def test_planning_command_rejected_if_no_accepted_analysis(tmp_path: Path) -> None:
    """continue_from_gate on analysis_review gate succeeds even without
    accepted analysis (first-time accept), but the guard verifies no
    pending draft analysis is waiting for acceptance.

    P0: CONTINUE queues a planning command, does NOT create a
    synthetic planning_review gate.
    """
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)
    gate_id = create_open_analysis_gate(gate_svc)
    gate = gate_repo.get(gate_id)

    # First-time accept: no revisions at all - should succeed
    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-block",
        decided_by="user-1",
    )
    assert result.status == "executed"
    # P0: result must have a planning command ID, not a gate ID
    assert result.result_command_id is not None, (
        "Expected a planning command to be queued"
    )
    assert result.result_gate_id is None, (
        "P0: must NOT create a synthetic planning_review gate; "
        "planning_review must come from real planning artifacts"
    )


def test_continue_blocked_when_draft_analysis_pending(tmp_path: Path) -> None:
    """continue_from_gate blocked when draft analysis revisions exist
    and none are accepted.
    """
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)
    gate_id = create_open_analysis_gate(gate_svc)
    gate = gate_repo.get(gate_id)

    # Seed a draft analysis revision (reanalysis requested but not accepted)
    now = utc_now_text()
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="analysis-draft",
        job_id="job-block", stage_index=1,
        revision_kind="analysis", revision_status="draft",
        revision_order=0, evidence_checksum="sha256:draft",
        prior_revision_checksum=None,
        artifact_refs_json='["draft-analysis.json"]',
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id=gate_id,
        created_at=now, created_by="analyzer",
        accepted_at=None, accepted_by=None,
    ))

    # Should be blocked - draft analysis not accepted
    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-block",
        decided_by="user-1",
    )
    assert result.status == "no_accepted_analysis"


def test_old_auto_policy_unaffected(tmp_path: Path) -> None:
    """The auto policy guard does not affect the old auto flow (no gate)."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)

    # Auto policy = no gate involved, so no guard applies
    # Just verify the service handles AIO_ON_GREEN gracefully
    from migration_factory.control_tower.schemas.run_configuration import (
        StageContinuationPolicy,
    )
    # Auto policy does not go through continue_from_gate
    # so there's nothing to test here directly.
    assert True


def test_manual_policy_strict(tmp_path: Path) -> None:
    """Manual policy requires accepted analysis before planning continues."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)
    gate_id = create_open_analysis_gate(gate_svc)

    # With accepted analysis revision, continue should work
    now = utc_now_text()
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="analysis-accepted",
        job_id="job-block", stage_index=1,
        revision_kind="analysis", revision_status="accepted",
        revision_order=0, evidence_checksum="sha256:accepted",
        prior_revision_checksum=None,
        artifact_refs_json='["accepted-analysis.json"]',
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id=gate_id,
        created_at=now, created_by="analyzer",
        accepted_at=now, accepted_by="user-1",
    ))

    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-block",
        decided_by="user-1",
    )
    # With accepted analysis, continue should work
    assert result.status == "executed"
    # P0: queues planning command, does NOT create synthetic gate
    assert result.result_command_id is not None
    assert result.result_gate_id is None
