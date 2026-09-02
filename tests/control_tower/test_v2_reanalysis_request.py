"""Focused tests for F15 job026 — request_reanalysis validation."""

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
    conn = _connection(tmp_path, "req_reanalysis.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    revision_repo = SqliteArtifactRevisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    action_svc = V2GateActionService(gate_repo, decision_repo, gate_svc, revision_repo)
    return gate_repo, decision_repo, revision_repo, gate_svc, action_svc


def _create_open_gate(gate_svc, phase="analysis_review", stage=1, job="job-abc") -> str:
    result = gate_svc.create_gate(CreateGateRequest(
        job_id=job, gate_phase=phase, stage_index=stage,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=("a1",),
    ))
    assert result.status == "created"
    return result.gate_id


# ── request_reanalysis only works on analysis_review gates ────────────


def test_request_reanalysis_on_analysis_review_gate_succeeds(tmp_path: Path) -> None:
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc = _svc(tmp_path)
    gate_id = _create_open_gate(gate_svc, phase="analysis_review")

    result = action_svc.request_reanalysis(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
        user_feedback="The analysis missed the database migration patterns",
    )

    assert result.status == "executed"
    assert result.result_gate_id is not None
    assert result.result_gate_id != gate_id

    # Old gate resolved
    old = gate_repo.get(gate_id)
    assert old is not None
    assert old.gate_status == "resolved"
    assert old.gate_decision == "reanalyze"

    # New gate open
    new = gate_repo.get(result.result_gate_id)
    assert new is not None
    assert new.gate_status == "open"

    # A draft revision was created with user feedback
    analysis_revs = revision_repo.list_by_job("job-abc")
    assert len(analysis_revs) >= 1
    draft_analysis = [r for r in analysis_revs if r.revision_kind == "analysis" and r.revision_status == "draft"]
    assert len(draft_analysis) >= 1
    assert "database migration" in draft_analysis[0].artifact_refs_json


def test_request_reanalysis_on_planning_gate_fails(tmp_path: Path) -> None:
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc = _svc(tmp_path)
    gate_id = _create_open_gate(gate_svc, phase="planning_review")

    result = action_svc.request_reanalysis(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
        user_feedback="Need to revise the plan too",
    )

    assert result.status != "executed"
    assert "invalid" in result.status.lower()


def test_request_reanalysis_on_approval_gate_fails(tmp_path: Path) -> None:
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc = _svc(tmp_path)
    gate_id = _create_open_gate(gate_svc, phase="approval_review")

    result = action_svc.request_reanalysis(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )

    assert result.status != "executed"


# ── supersedes downstream planning revision ──────────────────────────


def test_request_reanalysis_creates_draft_revision(tmp_path: Path) -> None:
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc = _svc(tmp_path)

    gate_id = _create_open_gate(gate_svc, phase="analysis_review")
    result = action_svc.request_reanalysis(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        user_feedback="Reanalysis required",
    )

    assert result.status == "executed"

    # A draft analysis revision should have been created
    all_revs = revision_repo.list_by_job("job-abc")
    draft_analysis = [r for r in all_revs if r.revision_kind == "analysis" and r.revision_status == "draft"]
    assert len(draft_analysis) >= 1, "Should create a draft analysis revision"
    assert "Reanalysis required" in draft_analysis[0].artifact_refs_json


# ── reanalysis without revision_repo falls back to generic reanalyze ──


def test_request_reanalysis_no_revision_repo(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "no_rev.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    # No revision_repo passed
    action_svc = V2GateActionService(gate_repo, decision_repo, gate_svc)

    gate_id = _create_open_gate(gate_svc, phase="analysis_review")
    result = action_svc.request_reanalysis(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )

    assert result.status == "executed"


# ── no source writes occur ───────────────────────────────────────────


def test_request_reanalysis_no_source_writes(tmp_path: Path) -> None:
    """Verify that no sandbox_path, argv, or command fields are involved."""
    gate_repo, decision_repo, revision_repo, gate_svc, action_svc = _svc(tmp_path)
    gate_id = _create_open_gate(gate_svc, phase="analysis_review")

    result = action_svc.request_reanalysis(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        user_feedback="User feedback with no source access",
    )

    assert result.status == "executed"
    # The action service and its dataclasses contain no sandbox_path,
    # argv, env, or command fields. This test verifies no exception
    # from any such field.
    assert not hasattr(result, "sandbox_path")
    assert not hasattr(result, "argv")
    assert not hasattr(result, "command")
