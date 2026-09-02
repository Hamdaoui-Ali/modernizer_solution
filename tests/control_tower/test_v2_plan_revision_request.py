"""Focused tests for F15 job027 — request_plan_revision validation."""

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
    conn = _connection(tmp_path, "req_plan_rev.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    revision_repo = SqliteArtifactRevisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    action_svc = V2GateActionService(gate_repo, decision_repo, gate_svc, revision_repo)
    return gate_repo, decision_repo, revision_repo, gate_svc, action_svc


def _create_open_gate(gate_svc, phase="planning_review", stage=1, job="job-abc") -> str:
    result = gate_svc.create_gate(CreateGateRequest(
        job_id=job, gate_phase=phase, stage_index=stage,
        source_artifact_checksum="sha256:plan-chk",
        source_artifact_refs=("plan-ref",),
    ))
    assert result.status == "created"
    return result.gate_id


# ── request_plan_revision only works on planning_review gates ─────────


def test_request_plan_revision_on_planning_gate_succeeds(tmp_path: Path) -> None:
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc = _svc(tmp_path)
    gate_id = _create_open_gate(gate_svc)

    result = action_svc.request_plan_revision(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
        user_feedback="The plan needs to account for database indexes",
    )

    assert result.status == "executed"
    assert result.result_gate_id is not None
    assert result.result_gate_id != gate_id

    # Old gate resolved with REVISE
    old = gate_repo.get(gate_id)
    assert old is not None
    assert old.gate_status == "resolved"
    assert old.gate_decision == "revise"

    # New gate open
    new = gate_repo.get(result.result_gate_id)
    assert new is not None
    assert new.gate_status == "open"

    # Draft planning revision created
    all_revs = revision_repo.list_by_job("job-abc")
    draft_planning = [r for r in all_revs if r.revision_kind == "planning" and r.revision_status == "draft"]
    assert len(draft_planning) >= 1
    assert "database indexes" in draft_planning[0].artifact_refs_json


def test_request_plan_revision_on_analysis_gate_fails(tmp_path: Path) -> None:
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc = _svc(tmp_path)
    gate_id = _create_open_gate(gate_svc, phase="analysis_review")

    result = action_svc.request_plan_revision(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )

    assert result.status == "invalid_decision"


def test_request_plan_revision_on_approval_gate_fails(tmp_path: Path) -> None:
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc = _svc(tmp_path)
    gate_id = _create_open_gate(gate_svc, phase="approval_review")

    result = action_svc.request_plan_revision(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )

    assert result.status == "invalid_decision"


# ── binds accepted analysis revision ─────────────────────────────────


def test_request_plan_revision_binds_accepted_analysis(tmp_path: Path) -> None:
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc = _svc(tmp_path)

    # Create an accepted analysis revision
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
    result = action_svc.request_plan_revision(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        user_feedback="Revise based on new analysis",
    )

    assert result.status == "executed"

    # Verify the draft planning revision references the accepted analysis
    all_revs = revision_repo.list_by_job("job-abc")
    draft_planning = [r for r in all_revs if r.revision_kind == "planning"]
    assert len(draft_planning) >= 1
    # The evidence_checksum should match the accepted analysis
    assert draft_planning[0].evidence_checksum == "sha256:analysis-final"


# ── no source writes ─────────────────────────────────────────────────


def test_request_plan_revision_no_source_writes(tmp_path: Path) -> None:
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc = _svc(tmp_path)
    gate_id = _create_open_gate(gate_svc)

    result = action_svc.request_plan_revision(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )

    assert result.status == "executed"
    assert not hasattr(result, "sandbox_path")
    assert not hasattr(result, "argv")
    assert not hasattr(result, "command")


# ── idempotent ─────────────────────────────────────────────────────────


def test_request_plan_revision_idempotent(tmp_path: Path) -> None:
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc = _svc(tmp_path)
    gate_id = _create_open_gate(gate_svc)

    r1 = action_svc.request_plan_revision(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        idempotency_key="idem-plan-rev-1",
    )
    assert r1.status == "executed"

    r2 = action_svc.request_plan_revision(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        idempotency_key="idem-plan-rev-1",
    )
    assert r2.status == "gate_not_open"
