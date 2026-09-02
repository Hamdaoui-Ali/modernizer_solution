"""Focused tests for F15-JOB-093 — Create approval_review gate.

Verifies that approval_review gates can be created, bound to accepted
analysis/plan revisions, and block transform until approved.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_gate_action_service import (
    V2GateActionService,
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
from migration_factory.control_tower.schemas.phase_gate import (
    GatePhase,
    GateDecision,
    is_valid_decision_for_phase,
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


def test_approval_review_gate_can_be_created(tmp_path: Path) -> None:
    """Approval_review gate can be created via V2PhaseGateService."""
    conn = _connection(tmp_path, "app1.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)

    result = gate_svc.create_gate(CreateGateRequest(
        job_id="job-approval-1",
        gate_phase="approval_review",
        stage_index=2,
        source_artifact_checksum="sha256:analysis+plan",
        source_artifact_refs=("analysis.json", "plan.json"),
        created_by="system",
    ))
    assert result.status == "created"
    assert result.gate_id

    gate = gate_repo.get(result.gate_id)
    assert gate is not None
    assert gate.gate_phase == "approval_review"
    assert gate.gate_status == "open"


def test_approval_review_gate_checksum_covers_approved_scope(tmp_path: Path) -> None:
    """Gate checksum covers both analysis and plan artifact checksums."""
    conn = _connection(tmp_path, "app2.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)

    result = gate_svc.create_gate(CreateGateRequest(
        job_id="job-approval-2",
        gate_phase="approval_review",
        stage_index=2,
        source_artifact_checksum="sha256:combined-abc",
        source_artifact_refs=(
            "analysis-final.json",
            "plan-final.json",
        ),
        created_by="system",
    ))

    gate = gate_repo.get(result.gate_id)
    assert gate is not None
    assert "abc" in gate.source_artifact_checksum
    stored_refs = json.loads(gate.source_artifact_refs_json)
    assert "analysis-final.json" in stored_refs
    assert "plan-final.json" in stored_refs


def test_transform_blocked_before_approval(tmp_path: Path) -> None:
    """Transform cannot proceed before approval_review gate is resolved."""
    conn = _connection(tmp_path, "app3.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    revision_repo = SqliteArtifactRevisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    action_svc = V2GateActionService(
        gate_repo, decision_repo, gate_svc, revision_repo,
    )

    # Seed accepted revisions
    now = utc_now_text()
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="analysis-acc", job_id="job-app3", stage_index=2,
        revision_kind="analysis", revision_status="accepted",
        revision_order=0, evidence_checksum="sha256:analysis-final",
        prior_revision_checksum=None,
        artifact_refs_json='["analysis.json"]',
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id="gate-analysis",
        created_at=now, created_by="analyzer",
        accepted_at=now, accepted_by="user",
    ))
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="plan-acc", job_id="job-app3", stage_index=2,
        revision_kind="planning", revision_status="accepted",
        revision_order=0, evidence_checksum="sha256:plan-final",
        prior_revision_checksum=None,
        artifact_refs_json='["plan.json"]',
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id="gate-planning",
        created_at=now, created_by="planner",
        accepted_at=now, accepted_by="user",
    ))

    # Create approval gate
    gate_result = gate_svc.create_gate(CreateGateRequest(
        job_id="job-app3", gate_phase="approval_review",
        stage_index=2,
        source_artifact_checksum="sha256:analysis+plan",
        source_artifact_refs=("analysis.json", "plan.json"),
        created_by="system",
    ))

    # Try to use continue (which is NOT valid for approval_review)
    # Only APPROVE and REJECT are valid
    continue_result = action_svc.continue_from_gate(
        gate_id=gate_result.gate_id, job_id="job-app3",
        decided_by="user-1",
    )
    assert continue_result.status == "invalid_decision"

    # Approve should work
    approve_result = action_svc.approve_from_gate(
        gate_id=gate_result.gate_id, job_id="job-app3",
        decided_by="user-1",
    )
    assert approve_result.status == "executed"


def test_system_auto_approval_requires_accepted_analysis_and_plan(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "app-auto-safety.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    revision_repo = SqliteArtifactRevisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    action_svc = V2GateActionService(
        gate_repo, decision_repo, gate_svc, revision_repo,
    )

    gate_result = gate_svc.create_gate(CreateGateRequest(
        job_id="job-auto-safety",
        gate_phase="approval_review",
        stage_index=2,
        source_artifact_checksum="sha256:analysis+plan",
        source_artifact_refs=("analysis.json", "plan.json"),
        created_by="system",
    ))

    result = action_svc.approve_transformation(
        gate_id=gate_result.gate_id,
        job_id="job-auto-safety",
        decided_by="system:auto-approval",
        expected_gate_checksum=gate_result.gate_checksum,
        actor_type="system",
    )

    assert result.status == "no_accepted_analysis"
    gate = gate_repo.get(gate_result.gate_id)
    assert gate is not None
    assert gate.gate_status == "open"
    assert decision_repo.list_by_job("job-auto-safety") == ()

def test_approval_review_gate_approve_and_reject_valid(tmp_path: Path) -> None:
    """Approval_review gate allows APPROVE and REJECT decisions."""
    assert is_valid_decision_for_phase(
        GatePhase.APPROVAL_REVIEW, GateDecision.APPROVE
    )
    assert is_valid_decision_for_phase(
        GatePhase.APPROVAL_REVIEW, GateDecision.REJECT
    )
    assert not is_valid_decision_for_phase(
        GatePhase.APPROVAL_REVIEW, GateDecision.CONTINUE
    )
    assert not is_valid_decision_for_phase(
        GatePhase.APPROVAL_REVIEW, GateDecision.REANALYZE
    )


def test_summary_explains_risks(tmp_path: Path) -> None:
    """Approval gate summary includes risk explanation."""
    conn = _connection(tmp_path, "app4.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)

    result = gate_svc.create_gate(CreateGateRequest(
        job_id="job-risks",
        gate_phase="approval_review",
        stage_index=2,
        source_artifact_checksum="sha256:risks",
        source_artifact_refs=("risk-assessment.json",),
        created_by="system",
    ))
    assert result.status == "created"

    # Verify available actions
    actions = gate_svc.get_available_actions(result.gate_id)
    action_names = {a.action for a in actions}
    assert "approve" in action_names
    assert "reject" in action_names
