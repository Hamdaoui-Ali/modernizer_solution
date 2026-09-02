"""Tests for F5-T10: Exact-Diff Apply — repair proposal approval and patch application."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_repair_flow import (
    V2RepairFlowService,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
    SqliteV2RepairRepository,
    V2RepairProposalRecord,
)
from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
)


def _approve_proposal(service: V2RepairFlowService, proposal_id: str) -> None:
    service._reviewer.record_critique(
        proposal_id=proposal_id,
        proposal_checksum="pc-test",
        context_pack_checksum="cp-test",
        decision="accept",
        reasoning="Proposal is bounded.",
    )
    service.approve_proposal(
        proposal_id=proposal_id,
        approval_checksum="abc123",
        proposal_checksum="pc-test",
        context_pack_checksum="cp-test",
    )


# ── T10-1: approve_proposal changes status to approved ────────────────

def test_approve_proposal_changes_status_to_approved() -> None:
    service = V2RepairFlowService()
    proposal = service.create_proposal(
        "cmd1", "fail", "hypothesis", "patch", ("f1.java",)
    )
    assert proposal.status == "draft"

    _approve_proposal(service, proposal.proposal_id)
    approved = service._proposals[proposal.proposal_id]

    assert approved.status == "approved"
    assert approved.approval_checksum == "abc123"


# ── T10-2: approve_proposal fails for non-draft proposal ───────────────

def test_approve_proposal_fails_for_non_draft() -> None:
    service = V2RepairFlowService()
    proposal = service.create_proposal(
        "cmd1", "fail", "hypothesis", "patch", ("f1.java",)
    )
    _approve_proposal(service, proposal.proposal_id)

    with pytest.raises(ValueError, match="already approved"):
        service.approve_proposal(
            proposal_id=proposal.proposal_id,
            approval_checksum="abc123",
            proposal_checksum="pc-test",
            context_pack_checksum="cp-test",
        )


# ── T10-3: approve_proposal fails when reviewer gate is not met ────────

def test_approve_proposal_fails_when_reviewer_gate_not_met() -> None:
    service = V2RepairFlowService()
    proposal = service.create_proposal(
        "cmd1", "fail", "hypothesis", "patch", ("f1.java",)
    )

    with pytest.raises(ValueError, match="blocked by reviewer gate"):
        service.approve_proposal(
            proposal_id=proposal.proposal_id,
            approval_checksum="abc123",
            proposal_checksum="pc-test",
            context_pack_checksum="cp-test",
        )


# ── T10-4: apply_patch fails for non-approved proposal ─────────────────

def test_apply_patch_fails_for_non_approved_proposal(tmp_path: Path) -> None:
    service = V2RepairFlowService()
    proposal = service.create_proposal(
        "cmd1", "fail", "hypothesis", "patch", ("pom.xml",)
    )

    with pytest.raises(ValueError, match="must be approved"):
        service.apply_patch(
            proposal_id=proposal.proposal_id,
            target_path="pom.xml",
            patch_content="diff content",
            run_dir=tmp_path / "run",
            sandbox_path=tmp_path / "sandbox",
            legacy_path=tmp_path / "legacy",
            deterministic_rule_id="RULE",
        )


# ── T10-5: apply_patch fails for missing proposal ──────────────────────

def test_apply_patch_fails_for_missing_proposal(tmp_path: Path) -> None:
    service = V2RepairFlowService()

    with pytest.raises(ValueError, match="not found"):
        service.apply_patch(
            proposal_id="nonexistent",
            target_path="pom.xml",
            patch_content="diff content",
            run_dir=tmp_path / "run",
            sandbox_path=tmp_path / "sandbox",
            legacy_path=tmp_path / "legacy",
            deterministic_rule_id="RULE",
        )


# ── T10-6: create_proposal creates draft proposal ──────────────────────

def test_create_proposal_creates_draft() -> None:
    service = V2RepairFlowService()
    proposal = service.create_proposal(
        "cmd1", "fail", "hypothesis", "patch", ("f1.java", "f2.java")
    )

    assert proposal.status == "draft"
    assert proposal.command_id == "cmd1"
    assert proposal.failure_summary == "fail"
    assert proposal.hypothesis == "hypothesis"
    assert proposal.patch_summary == "patch"
    assert proposal.affected_paths == ("f1.java", "f2.java")
    assert proposal.approval_checksum is None
    assert proposal.proposal_checksum != ""
    assert proposal.created_at != ""


# ── T10-7: Proposal checksum is deterministic ──────────────────────────

def test_proposal_checksum_is_deterministic() -> None:
    service = V2RepairFlowService()

    a = service.create_proposal(
        "cmd1", "fail", "hypothesis", "patch", ("f1.java",)
    )
    b = service.create_proposal(
        "cmd1", "fail", "hypothesis", "patch", ("f1.java",)
    )

    # The checksum is based on the same 5 inputs, so should be identical
    assert a.proposal_checksum == b.proposal_checksum
    # But proposal_id differs (different UUIDs)
    assert a.proposal_id != b.proposal_id


# ── T10-8: Proposal status transitions: draft -> approved -> applied ───

def test_proposal_status_transitions_draft_to_approved() -> None:
    service = V2RepairFlowService()
    proposal = service.create_proposal(
        "cmd1", "fail", "hypothesis", "patch", ("f1.java",)
    )
    assert proposal.status == "draft"

    _approve_proposal(service, proposal.proposal_id)
    approved = service._proposals[proposal.proposal_id]
    assert approved.status == "approved"


# ── T10-9: Repo lookup returns None for missing proposal ───────────────

def test_repo_lookup_returns_none_for_missing_proposal(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        str(tmp_path / "repair.sqlite3"),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    from migration_factory.control_tower.infrastructure.sqlite.migrations import (
        apply_pending_migrations,
    )

    apply_pending_migrations(conn)
    repo = SqliteV2RepairRepository(conn)

    result = repo.get_proposal("nonexistent")
    assert result is None


# ── T10-10: list_proposals_by_command returns proposals for a command ──

def test_list_proposals_by_command_returns_proposals(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        str(tmp_path / "repair.sqlite3"),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    from migration_factory.control_tower.infrastructure.sqlite.migrations import (
        apply_pending_migrations,
    )

    apply_pending_migrations(conn)
    repo = SqliteV2RepairRepository(conn)

    repo.save_proposal(
        V2RepairProposalRecord(
            proposal_id="prop-1",
            command_id="cmd-a",
            failure_summary="build failed",
            hypothesis="missing dep",
            patch_summary="add dep",
            affected_paths_json=json.dumps(["pom.xml"]),
            status="draft",
            approval_checksum=None,
            created_at="2026-06-01T00:00:00Z",
        )
    )
    repo.save_proposal(
        V2RepairProposalRecord(
            proposal_id="prop-2",
            command_id="cmd-a",
            failure_summary="test failed",
            hypothesis="wrong assert",
            patch_summary="fix test",
            affected_paths_json=json.dumps(["Test.java"]),
            status="draft",
            approval_checksum=None,
            created_at="2026-06-02T00:00:00Z",
        )
    )
    repo.save_proposal(
        V2RepairProposalRecord(
            proposal_id="prop-3",
            command_id="cmd-b",
            failure_summary="other",
            hypothesis="n/a",
            patch_summary="n/a",
            affected_paths_json=json.dumps(["other.txt"]),
            status="draft",
            approval_checksum=None,
            created_at="2026-06-03T00:00:00Z",
        )
    )

    results = repo.list_proposals_by_command("cmd-a")
    assert len(results) == 2
    assert results[0].proposal_id in ("prop-1", "prop-2")
    assert results[1].proposal_id in ("prop-1", "prop-2")

    results_b = repo.list_proposals_by_command("cmd-b")
    assert len(results_b) == 1
    assert results_b[0].proposal_id == "prop-3"
