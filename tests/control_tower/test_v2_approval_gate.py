"""Focused tests for F15 job028 — approve_transformation gate action."""

from __future__ import annotations

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
    conn = _connection(tmp_path, "approval.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    revision_repo = SqliteArtifactRevisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    action_svc = V2GateActionService(gate_repo, decision_repo, gate_svc, revision_repo)
    return gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn


def _create_open_gate(gate_svc, phase="approval_review", stage=1, job="job-abc") -> str:
    result = gate_svc.create_gate(CreateGateRequest(
        job_id=job, gate_phase=phase, stage_index=stage,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=("ref1",),
    ))
    assert result.status == "created"
    return result.gate_id


def _seed_accepted_revisions(revision_repo, job="job-abc", stage=1):
    """Seed accepted analysis and planning revisions for the given stage."""
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="analysis-accepted",
        job_id=job, stage_index=stage,
        revision_kind="analysis", revision_status="accepted",
        revision_order=0, evidence_checksum="sha256:analysis-final",
        prior_revision_checksum=None,
        artifact_refs_json='["analysis-final.json"]',
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id="gate-analysis",
        created_at="2026-06-17T12:00:00Z", created_by="analyzer",
        accepted_at="2026-06-17T13:00:00Z", accepted_by="user-1",
    ))
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="plan-accepted",
        job_id=job, stage_index=stage,
        revision_kind="planning", revision_status="accepted",
        revision_order=0, evidence_checksum="sha256:plan-final",
        prior_revision_checksum=None,
        artifact_refs_json='["plan-final.json"]',
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id="gate-planning",
        created_at="2026-06-17T12:30:00Z", created_by="planner",
        accepted_at="2026-06-17T13:30:00Z", accepted_by="user-1",
    ))


# ── approve_transformation success ────────────────────────────────────


def test_approve_transformation_success(tmp_path: Path) -> None:
    """Approve transformation with accepted analysis and plan revisions."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)

    _seed_accepted_revisions(revision_repo)
    gate_id = _create_open_gate(gate_svc)

    result = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )

    assert result.status == "executed"
    assert result.decision_id
    assert result.result_command_id is not None, (
        "Should generate a command ID for the transform"
    )

    # Gate should be resolved with APPROVE
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "resolved"
    assert gate.gate_decision == "approve"

    # Decision should be persisted with the command reference
    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.action == "approve"
    assert decision.result_command_id == result.result_command_id


def test_approve_transformation_with_idempotency_key(tmp_path: Path) -> None:
    """Approve transformation with explicit idempotency key returns same result."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)

    _seed_accepted_revisions(revision_repo)
    gate_id = _create_open_gate(gate_svc)

    r1 = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
        idempotency_key="idem-approve-1",
    )
    assert r1.status == "executed"

    r2 = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
        idempotency_key="idem-approve-1",
    )
    assert r2.status == "gate_not_open"


def test_approve_transformation_no_accepted_analysis(tmp_path: Path) -> None:
    """Reject approval when no accepted analysis revision exists."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)

    # Only seed plan, no analysis
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="plan-accepted",
        job_id="job-abc", stage_index=1,
        revision_kind="planning", revision_status="accepted",
        revision_order=0, evidence_checksum="sha256:plan-final",
        prior_revision_checksum=None,
        artifact_refs_json='["plan-final.json"]',
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id="gate-planning",
        created_at="2026-06-17T12:30:00Z", created_by="planner",
        accepted_at="2026-06-17T13:30:00Z", accepted_by="user-1",
    ))
    gate_id = _create_open_gate(gate_svc)

    result = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )

    assert result.status == "no_accepted_analysis"
    # Gate should remain OPEN since pre-validation failed
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "open"


def test_approve_transformation_no_accepted_plan(tmp_path: Path) -> None:
    """Reject approval when no accepted plan revision exists (stale plan)."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)

    # Only seed analysis, no plan
    revision_repo.save(ArtifactRevisionRecord(
        revision_id="analysis-accepted",
        job_id="job-abc", stage_index=1,
        revision_kind="analysis", revision_status="accepted",
        revision_order=0, evidence_checksum="sha256:analysis-final",
        prior_revision_checksum=None,
        artifact_refs_json='["analysis-final.json"]',
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id="gate-analysis",
        created_at="2026-06-17T12:00:00Z", created_by="analyzer",
        accepted_at="2026-06-17T13:00:00Z", accepted_by="user-1",
    ))
    gate_id = _create_open_gate(gate_svc)

    result = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )

    assert result.status == "no_accepted_plan"
    # Gate should remain OPEN since pre-validation failed
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "open"


def test_approve_transformation_no_revisions_at_all(tmp_path: Path) -> None:
    """Reject approval when no revisions exist at all."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )

    assert result.status == "no_accepted_analysis"
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "open"


# ── validation: wrong phase ──────────────────────────────────────────


def test_approve_transformation_on_analysis_gate_fails(tmp_path: Path) -> None:
    """Approve transformation only works on approval_review gates."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)

    _seed_accepted_revisions(revision_repo)
    gate_id = _create_open_gate(gate_svc, phase="analysis_review")

    result = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )

    assert result.status == "invalid_decision"
    assert "not allowed at analysis_review" in result.reason.lower()


def test_approve_transformation_on_planning_gate_fails(tmp_path: Path) -> None:
    """Approve transformation only works on approval_review gates."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)

    _seed_accepted_revisions(revision_repo)
    gate_id = _create_open_gate(gate_svc, phase="planning_review")

    result = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )

    assert result.status == "invalid_decision"
    assert "not allowed at planning_review" in result.reason.lower()


# ── validation: gate state ───────────────────────────────────────────


def test_approve_transformation_on_already_resolved_gate(tmp_path: Path) -> None:
    """Approve transformation fails on a resolved gate."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)

    _seed_accepted_revisions(revision_repo)
    gate_id = _create_open_gate(gate_svc)

    # First approval resolves the gate
    r1 = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )
    assert r1.status == "executed"
    accepted = revision_repo.find_accepted("job-abc", 1, "approval_review")
    assert accepted is not None
    assert accepted.evidence_checksum == "sha256:abc"
    assert r1.result_revision_id == accepted.revision_id

    # Second attempt should fail (gate is now resolved)
    r2 = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-2",
    )
    assert r2.status == "gate_not_open"


def test_approve_transformation_on_nonexistent_gate(tmp_path: Path) -> None:
    """Approve transformation fails on a nonexistent gate."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)

    result = action_svc.approve_transformation(
        gate_id="nonexistent-gate",
        job_id="job-abc",
        decided_by="user-1",
    )
    assert result.status == "gate_not_found"


# ── stage scoping ─────────────────────────────────────────────────────


def test_approve_transformation_different_stage_revisions(tmp_path: Path) -> None:
    """Approve transformation checks accepted revisions for the same stage."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)

    # Seed accepted revisions for stage 2, but gate is for stage 1
    _seed_accepted_revisions(revision_repo, stage=2)
    gate_id = _create_open_gate(gate_svc, stage=1)

    result = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )

    assert result.status == "no_accepted_analysis"


# ── no source writes ─────────────────────────────────────────────────


def test_approve_transformation_no_source_writes(tmp_path: Path) -> None:
    """Verify that no sandbox_path, argv, or command fields are involved."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc, conn = _svc(tmp_path)

    _seed_accepted_revisions(revision_repo)
    gate_id = _create_open_gate(gate_svc)

    result = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )

    assert result.status == "executed"
    # The action service and its dataclasses contain no sandbox_path,
    # argv, env, or command fields.
    assert not hasattr(result, "sandbox_path")
    assert not hasattr(result, "argv")
    assert not hasattr(result, "command")


# ── revision_repo not configured ─────────────────────────────────────


def test_approve_transformation_no_revision_repo(tmp_path: Path) -> None:
    """Approve transformation works without revision_repo (falls back to base action)."""
    conn = _connection(tmp_path, "no_rev.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    # No revision_repo passed
    action_svc = V2GateActionService(gate_repo, decision_repo, gate_svc)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc.approve_transformation(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )

    assert result.status == "executed"
    assert result.result_command_id is not None
