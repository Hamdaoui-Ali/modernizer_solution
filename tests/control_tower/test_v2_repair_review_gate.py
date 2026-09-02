"""Focused tests for F15 job029 — approve_repair gate action."""

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
from migration_factory.control_tower.application.v2_repair_flow import (
    V2RepairFlowService,
)
from migration_factory.control_tower.application.v2_reviewer_service import (
    ReviewerCritique,
    V2ReviewerService,
)
from migration_factory.control_tower.domain.checksums import sha256_canonical_json
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
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
    """Set up services with in-memory V2RepairFlowService (no SQLite repair repo)."""
    conn = _connection(tmp_path, "repair.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)

    # In-memory reviewer and repair services
    reviewer_svc = V2ReviewerService()
    repair_svc = V2RepairFlowService(reviewer_service=reviewer_svc)
    action_svc = V2GateActionService(
        gate_repo, decision_repo, gate_svc,
        repair_service=repair_svc,
    )
    return gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn


def _create_open_gate(gate_svc, phase="repair_review", stage=1, job="job-abc") -> str:
    result = gate_svc.create_gate(CreateGateRequest(
        job_id=job, gate_phase=phase, stage_index=stage,
        source_artifact_checksum="sha256:repair-chk",
        source_artifact_refs=("repair-ref",),
    ))
    assert result.status == "created"
    return result.gate_id


def _create_reviewed_repair_gate(gate_svc, stage=1, job="job-abc") -> tuple[str, dict[str, str]]:
    checksums = {
        "failure_evidence_checksum": "sha256:failure-v1",
        "context_pack_checksum": "sha256:ctx-v1",
        "primary_output_checksum": "sha256:primary-v1",
        "reviewer_output_checksum": "sha256:reviewer-v1",
        "final_reviewed_diff_checksum": "sha256:diff-v1",
        "policy_validation_checksum": "sha256:policy-v1",
        "base_repo_state_checksum": "sha256:repo-v1",
        "final_artifact_checksum": "sha256:final-v1",
    }
    source_checksum = sha256_canonical_json(checksums)
    refs = tuple(f"{key}:{value}" for key, value in checksums.items())
    result = gate_svc.create_gate(CreateGateRequest(
        job_id=job,
        gate_phase="repair_review",
        stage_index=stage,
        source_artifact_checksum=source_checksum,
        source_artifact_refs=refs,
    ))
    assert result.status == "created"
    return result.gate_id, checksums


def _seed_proposal_and_critique(
    repair_svc, reviewer_svc, proposal_checksum="sha256:prop-v1",
    context_checksum="sha256:ctx-v1",
) -> tuple[str, str]:
    """Create a draft repair proposal and accepted reviewer critique."""
    proposal = repair_svc.create_proposal(
        command_id="cmd-1",
        failure_summary="Build failure in module X",
        hypothesis="Missing dependency declaration",
        patch_summary="Add dependency to pom.xml",
        affected_paths=("pom.xml",),
    )

    # Add an accepted reviewer critique matching the checksums
    crit = ReviewerCritique(
        critique_id="crit-1",
        proposal_id=proposal.proposal_id,
        proposal_type="repair",
        proposal_checksum=proposal_checksum,
        context_pack_checksum=context_checksum,
        decision="accept",
        reasoning="Proposal looks correct",
        missing_evidence=(),
        unsafe_assumptions=(),
        model_invocation_id="",
        created_at="2026-06-17T12:00:00Z",
    )
    reviewer_svc._critiques[crit.critique_id] = crit

    return proposal.proposal_id, proposal_checksum, context_checksum


# ── approve_repair success ───────────────────────────────────────────


def test_approve_repair_success(tmp_path: Path) -> None:
    """Approve repair with valid proposal and reviewer critique."""
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )

    gate_id = _create_open_gate(gate_svc)
    proposal_id, prop_chk, ctx_chk = _seed_proposal_and_critique(repair_svc, reviewer_svc)

    result = action_svc.approve_repair(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
        proposal_id=proposal_id,
        proposal_checksum=prop_chk,
        context_pack_checksum=ctx_chk,
    )

    assert result.status == "executed"
    assert result.decision_id
    # Should link to the approved repair proposal
    assert result.result_revision_id == proposal_id

    # Proposal should now be approved
    approved = repair_svc._proposals[proposal_id]
    assert approved.status == "approved"
    assert approved.approval_checksum == "sha256:repair-chk"

    # Gate should be resolved with CONTINUE
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "resolved"
    assert gate.gate_decision == "continue"

    # Decision should be persisted
    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.action == "continue"


def test_approve_repair_idempotent(tmp_path: Path) -> None:
    """Approve repair with explicit idempotency key returns same result."""
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )

    gate_id = _create_open_gate(gate_svc)
    proposal_id, prop_chk, ctx_chk = _seed_proposal_and_critique(repair_svc, reviewer_svc)

    r1 = action_svc.approve_repair(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        proposal_id=proposal_id, proposal_checksum=prop_chk,
        context_pack_checksum=ctx_chk,
        idempotency_key="idem-repair-1",
    )
    assert r1.status == "executed"

    r2 = action_svc.approve_repair(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        proposal_id=proposal_id, proposal_checksum=prop_chk,
        context_pack_checksum=ctx_chk,
        idempotency_key="idem-repair-1",
    )
    assert r2.status == "idempotent"
    assert r2.decision_id == r1.decision_id
    assert r2.result_revision_id == r1.result_revision_id


# ── validation: proposal state and reviewer gate ─────────────────────


def test_approve_repair_no_proposal(tmp_path: Path) -> None:
    """Reject when proposal does not exist."""
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )

    gate_id = _create_open_gate(gate_svc)

    result = action_svc.approve_repair(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        proposal_id="nonexistent-proposal",
        proposal_checksum="sha256:x",
        context_pack_checksum="sha256:y",
    )

    assert result.status == "approval_failed"


def test_approve_repair_no_critique(tmp_path: Path) -> None:
    """Reject when no accepted reviewer critique matches."""
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )

    gate_id = _create_open_gate(gate_svc)

    # Create a proposal but no matching critique
    proposal = repair_svc.create_proposal(
        command_id="cmd-1",
        failure_summary="Failure",
        hypothesis="Fix",
        patch_summary="Patch",
        affected_paths=("file.txt",),
    )

    result = action_svc.approve_repair(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        proposal_id=proposal.proposal_id,
        proposal_checksum="sha256:prop-v1",
        context_pack_checksum="sha256:ctx-v1",
    )

    assert result.status == "approval_failed"
    assert "reviewer gate" in result.reason.lower()


def test_approve_repair_critique_mismatched_checksums(tmp_path: Path) -> None:
    """Reject when critique checksums don't match proposal checksums."""
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )

    gate_id = _create_open_gate(gate_svc)

    proposal = repair_svc.create_proposal(
        command_id="cmd-1",
        failure_summary="Failure",
        hypothesis="Fix",
        patch_summary="Patch",
        affected_paths=("file.txt",),
    )

    # Critique has different checksums
    crit = ReviewerCritique(
        critique_id="crit-mismatch",
        proposal_id=proposal.proposal_id,
        proposal_type="repair",
        proposal_checksum="sha256:different-prop",
        context_pack_checksum="sha256:different-ctx",
        decision="accept",
        reasoning="Looks okay",
        missing_evidence=(),
        unsafe_assumptions=(),
        model_invocation_id="",
        created_at="2026-06-17T12:00:00Z",
    )
    reviewer_svc._critiques[crit.critique_id] = crit

    result = action_svc.approve_repair(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        proposal_id=proposal.proposal_id,
        proposal_checksum="sha256:requested-prop",
        context_pack_checksum="sha256:requested-ctx",
    )

    assert result.status == "approval_failed"
    assert "reviewer gate" in result.reason.lower()


# ── validation: wrong phase ──────────────────────────────────────────


def test_approve_repair_on_analysis_gate_fails(tmp_path: Path) -> None:
    """Approve repair only works on repair_review gates."""
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )

    proposal_id, prop_chk, ctx_chk = _seed_proposal_and_critique(repair_svc, reviewer_svc)
    gate_id = _create_open_gate(gate_svc, phase="analysis_review")

    result = action_svc.approve_repair(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        proposal_id=proposal_id,
        proposal_checksum=prop_chk,
        context_pack_checksum=ctx_chk,
    )

    assert result.status == "invalid_decision"


def test_approve_repair_on_approval_gate_fails(tmp_path: Path) -> None:
    """Approve repair only works on repair_review gates."""
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )

    proposal_id, prop_chk, ctx_chk = _seed_proposal_and_critique(repair_svc, reviewer_svc)
    gate_id = _create_open_gate(gate_svc, phase="approval_review")

    result = action_svc.approve_repair(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        proposal_id=proposal_id,
        proposal_checksum=prop_chk,
        context_pack_checksum=ctx_chk,
    )

    assert result.status == "invalid_decision"


# ── validation: gate state ───────────────────────────────────────────


def test_approve_repair_on_resolved_gate(tmp_path: Path) -> None:
    """Approve repair fails on a resolved gate."""
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )

    gate_id = _create_open_gate(gate_svc)
    proposal_id, prop_chk, ctx_chk = _seed_proposal_and_critique(repair_svc, reviewer_svc)

    r1 = action_svc.approve_repair(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        proposal_id=proposal_id, proposal_checksum=prop_chk,
        context_pack_checksum=ctx_chk,
    )
    assert r1.status == "executed"

    r2 = action_svc.approve_repair(
        gate_id=gate_id, job_id="job-abc", decided_by="user-2",
        proposal_id=proposal_id, proposal_checksum=prop_chk,
        context_pack_checksum=ctx_chk,
    )
    assert r2.status == "gate_not_open"


def test_approve_repair_nonexistent_gate(tmp_path: Path) -> None:
    """Approve repair fails on a nonexistent gate."""
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )

    result = action_svc.approve_repair(
        gate_id="nonexistent", job_id="job-abc", decided_by="user-1",
        proposal_id="p1", proposal_checksum="sha256:x",
        context_pack_checksum="sha256:y",
    )
    assert result.status == "gate_not_found"


# ── no repair service configured ─────────────────────────────────────


def test_approve_repair_no_repair_service(tmp_path: Path) -> None:
    """Approve repair fails when no V2RepairFlowService is configured."""
    conn = _connection(tmp_path, "no_repair.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    # No repair_service passed
    action_svc = V2GateActionService(gate_repo, decision_repo, gate_svc)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc.approve_repair(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        proposal_id="p1", proposal_checksum="sha256:x",
        context_pack_checksum="sha256:y",
    )

    assert result.status == "no_repair_service"


# ── no source writes ─────────────────────────────────────────────────


def test_approve_repair_no_source_writes(tmp_path: Path) -> None:
    """Verify that no sandbox_path, argv, or command fields are involved."""
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )

    gate_id = _create_open_gate(gate_svc)
    proposal_id, prop_chk, ctx_chk = _seed_proposal_and_critique(repair_svc, reviewer_svc)

    result = action_svc.approve_repair(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        proposal_id=proposal_id, proposal_checksum=prop_chk,
        context_pack_checksum=ctx_chk,
    )

    assert result.status == "executed"
    assert not hasattr(result, "sandbox_path")
    assert not hasattr(result, "argv")
    assert not hasattr(result, "command")


def test_approve_reviewed_repair_requires_all_artifact_checksums(tmp_path: Path) -> None:
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )
    gate_id, checksums = _create_reviewed_repair_gate(gate_svc)
    proposal_id, prop_chk, ctx_chk = _seed_proposal_and_critique(
        repair_svc,
        reviewer_svc,
        context_checksum=checksums["context_pack_checksum"],
    )

    result = action_svc.approve_repair(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
        proposal_id=proposal_id,
        proposal_checksum=prop_chk,
        context_pack_checksum=ctx_chk,
    )

    assert result.status == "missing_repair_checksum"
    assert "reviewer_output_checksum" in result.reason
    assert gate_repo.get(gate_id).gate_status == "open"


def test_approve_reviewed_repair_rejects_stale_base_repo_checksum(tmp_path: Path) -> None:
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )
    gate_id, checksums = _create_reviewed_repair_gate(gate_svc)
    proposal_id, prop_chk, ctx_chk = _seed_proposal_and_critique(
        repair_svc,
        reviewer_svc,
        context_checksum=checksums["context_pack_checksum"],
    )

    result = action_svc.approve_repair(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
        proposal_id=proposal_id,
        proposal_checksum=prop_chk,
        context_pack_checksum=ctx_chk,
        reviewer_output_checksum=checksums["reviewer_output_checksum"],
        final_reviewed_diff_checksum=checksums["final_reviewed_diff_checksum"],
        policy_validation_checksum=checksums["policy_validation_checksum"],
        base_repo_state_checksum="sha256:stale",
        final_reviewed_artifact_checksum=checksums["final_artifact_checksum"],
    )

    assert result.status == "repair_checksum_mismatch"
    assert "base_repo_state_checksum" in result.reason
    assert gate_repo.get(gate_id).gate_status == "open"


def test_approve_reviewed_repair_binds_reviewed_chain_checksums(tmp_path: Path) -> None:
    gate_repo, decision_repo, gate_svc, action_svc, reviewer_svc, repair_svc, conn = (
        _svc(tmp_path)
    )
    gate_id, checksums = _create_reviewed_repair_gate(gate_svc)
    proposal_id, prop_chk, ctx_chk = _seed_proposal_and_critique(
        repair_svc,
        reviewer_svc,
        context_checksum=checksums["context_pack_checksum"],
    )

    result = action_svc.approve_repair(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
        proposal_id=proposal_id,
        proposal_checksum=prop_chk,
        context_pack_checksum=ctx_chk,
        reviewer_output_checksum=checksums["reviewer_output_checksum"],
        final_reviewed_diff_checksum=checksums["final_reviewed_diff_checksum"],
        policy_validation_checksum=checksums["policy_validation_checksum"],
        base_repo_state_checksum=checksums["base_repo_state_checksum"],
        final_reviewed_artifact_checksum=checksums["final_artifact_checksum"],
    )

    assert result.status == "executed"
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "resolved"
    assert gate.gate_decision == "continue"
