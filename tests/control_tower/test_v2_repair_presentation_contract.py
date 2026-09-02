"""F5-T14: UI/API presentation contract tests for v2 repair projection."""

from __future__ import annotations

import pytest

from migration_factory.control_tower.application.v2_repair_projection import (
    FORBIDDEN_PROJECTION_KEYS,
    RepairProposalProjection,
    _safe_diff_preview,
    build_repair_projection_from_review_chain,
    projection_to_safe_dict,
    validate_projection_safety,
)


# ── Test 1 ──────────────────────────────────────────────────────────


def test_build_projection_populates_all_fields():
    """build_repair_projection_from_review_chain creates projection with all fields populated."""
    chain = {
        "failure_source": "build",
        "failure_summary": "compilation failed",
        "error_summary": "3 errors",
        "root_cause": "missing import",
        "fix_strategy": "add import",
        "changed_files": ["a.java", "b.java"],
        "diff_preview": "--- a/a.java\n+++ b/a.java\n@@ -1,1 +1,1 @@\n-old\n+new",
        "final_artifact_ref": "/tmp/artifact.json",
        "final_artifact_checksum": "abc123",
        "risk": "LOW",
        "confidence": 0.92,
        "reviewer_decision": "accept",
        "reviewer_notes": ["looks good", "safe"],
        "policy_status": "ok",
        "policy_reason": "no concerns",
        "policy_validation_checksum": "pol123",
        "context_pack_checksum": "ctx123",
        "base_repo_state_checksum": "base123",
        "primary_output_checksum": "pri123",
        "reviewer_output_checksum": "rev123",
        "cycle_number": 2,
        "deterministic_artifact_checksum": "det123",
    }
    proj = build_repair_projection_from_review_chain(
        proposal_id="p1",
        gate_id="g1",
        job_id="j1",
        stage_index=3,
        command_id="c1",
        review_chain=chain,
        gate_checksum="gc1",
        allowed_actions=("approve", "reject", "revise"),
        remaining_attempts=2,
    )
    assert proj.proposal_id == "p1"
    assert proj.gate_id == "g1"
    assert proj.job_id == "j1"
    assert proj.stage_index == 3
    assert proj.command_id == "c1"
    assert proj.failure_source == "build"
    assert proj.failure_summary == "compilation failed"
    assert proj.error_summary == "3 errors"
    assert proj.root_cause == "missing import"
    assert proj.fix_strategy == "add import"
    assert proj.changed_files == ("a.java", "b.java")
    assert proj.risk == "LOW"
    assert proj.confidence == 0.92
    assert proj.reviewer_decision == "accept"
    assert proj.reviewer_notes == ("looks good", "safe")
    assert proj.policy_status == "ok"
    assert proj.policy_reason == "no concerns"
    assert proj.policy_checksum == "pol123"
    assert proj.gate_checksum == "gc1"
    assert proj.allowed_actions == ("approve", "reject", "revise")
    assert proj.context_pack_checksum == "ctx123"
    assert proj.base_repo_state_checksum == "base123"
    assert proj.primary_output_checksum == "pri123"
    assert proj.reviewer_output_checksum == "rev123"
    assert proj.cycle_number == 2
    assert proj.remaining_attempts == 2
    assert proj.deterministic_artifact_checksum == "det123"


# ── Test 2 ──────────────────────────────────────────────────────────


def test_build_projection_empty_chain():
    """build_repair_projection_from_review_chain handles empty review_chain dict."""
    proj = build_repair_projection_from_review_chain(
        proposal_id="p2",
        gate_id="g2",
        review_chain={},
    )
    assert proj.proposal_id == "p2"
    assert proj.failure_source == ""
    assert proj.root_cause == ""
    assert proj.changed_files == ()
    assert proj.confidence == 0.0
    assert proj.reviewer_decision == ""
    assert proj.reviewer_notes == ()
    assert proj.cycle_number == 0
    assert proj.remaining_attempts == 3


# ── Test 3 ──────────────────────────────────────────────────────────


def test_projection_to_safe_dict_excludes_forbidden_keys():
    """projection_to_safe_dict does not include forbidden keys."""
    proj = RepairProposalProjection(
        proposal_id="p3",
    )
    safe = projection_to_safe_dict(proj)
    for forbidden in FORBIDDEN_PROJECTION_KEYS:
        assert forbidden not in safe, f"forbidden key {forbidden!r} leaked into safe dict"


# ── Test 4 ──────────────────────────────────────────────────────────


def test_validate_projection_safety_rejects_forbidden_keys():
    """validate_projection_safety returns failures for forbidden keys."""
    dirty = {
        "proposal_id": "p4",
        "sandbox_path": "/secret/sandbox",
        "argv": ["--dangerous"],
    }
    failures = validate_projection_safety(dirty)
    assert len(failures) >= 1
    assert any("sandbox_path" in f for f in failures)
    assert any("argv" in f for f in failures)


# ── Test 5 ──────────────────────────────────────────────────────────


def test_validate_projection_safety_clean():
    """validate_projection_safety returns empty list for clean projection."""
    clean = {
        "proposal_id": "p5",
        "root_cause": "test",
        "risk": "LOW",
    }
    failures = validate_projection_safety(clean)
    assert failures == []


# ── Test 6 ──────────────────────────────────────────────────────────


def test_projection_to_safe_dict_includes_all_allowed_fields():
    """projection_to_safe_dict includes all allowed fields."""
    proj = RepairProposalProjection(
        proposal_id="p6",
        gate_id="g6",
        job_id="j6",
        stage_index=1,
        command_id="c6",
        failure_source="test",
        failure_summary="summary",
        error_summary="errors",
        root_cause="rc",
        fix_strategy="fix",
        changed_files=("x.txt",),
        diff_preview="--- a\n+++ b",
        reviewed_diff_artifact_ref="ref",
        reviewed_diff_artifact_checksum="cs",
        risk="MEDIUM",
        confidence=0.75,
        reviewer_decision="revise",
        reviewer_notes=("n1",),
        policy_status="warn",
        policy_reason="reason",
        policy_checksum="pc",
        gate_checksum="gc",
        allowed_actions=("approve",),
        context_pack_checksum="cc",
        base_repo_state_checksum="bc",
        primary_output_checksum="poc",
        reviewer_output_checksum="roc",
        cycle_number=1,
        remaining_attempts=2,
        deterministic_artifact_checksum="dac",
    )
    safe = projection_to_safe_dict(proj)
    allowed_fields = [
        "proposal_id", "gate_id", "job_id", "stage_index", "command_id",
        "failure_source", "failure_summary", "error_summary", "root_cause",
        "fix_strategy", "changed_files", "diff_preview",
        "reviewed_diff_artifact_ref", "reviewed_diff_artifact_checksum",
        "risk", "confidence", "reviewer_decision", "reviewer_notes",
        "policy_status", "policy_reason", "policy_checksum", "gate_checksum",
        "allowed_actions", "context_pack_checksum", "base_repo_state_checksum",
        "primary_output_checksum", "reviewer_output_checksum",
        "cycle_number", "remaining_attempts", "deterministic_artifact_checksum",
    ]
    for field in allowed_fields:
        assert field in safe, f"allowed field {field!r} missing from safe dict"


# ── Test 7 ──────────────────────────────────────────────────────────


def test_safe_diff_preview_truncates_long_diff():
    """_safe_diff_preview truncates long diff to max_lines."""
    long_diff = "\n".join(f"line {i}" for i in range(100))
    preview = _safe_diff_preview(long_diff, max_lines=5)
    assert len(preview.splitlines()) == 5


# ── Test 8 ──────────────────────────────────────────────────────────


def test_allowed_actions_include_approve_reject_revise():
    """Allowed actions include approve/reject/revise."""
    chain = {
        "reviewer_decision": "revise",
    }
    proj = build_repair_projection_from_review_chain(
        allowed_actions=("approve", "reject", "revise"),
        review_chain=chain,
    )
    assert "approve" in proj.allowed_actions
    assert "reject" in proj.allowed_actions
    assert "revise" in proj.allowed_actions


# ── Test 9 ──────────────────────────────────────────────────────────


def test_gate_checksum_present_in_projection():
    """Gate checksum is present in projection."""
    proj = build_repair_projection_from_review_chain(
        gate_checksum="sha256:abc",
    )
    assert proj.gate_checksum == "sha256:abc"


# ── Test 10 ─────────────────────────────────────────────────────────


def test_reviewer_decision_present_in_projection():
    """Reviewer decision is present in projection."""
    chain = {"reviewer_decision": "reject"}
    proj = build_repair_projection_from_review_chain(review_chain=chain)
    assert proj.reviewer_decision == "reject"


# ── Test 11 ─────────────────────────────────────────────────────────


def test_diff_preview_bounded():
    """Diff preview is bounded to max_lines when present."""
    long_diff = "\n".join(f"line {i}" for i in range(50))
    chain = {"diff_preview": long_diff}
    proj = build_repair_projection_from_review_chain(review_chain=chain)
    assert len(proj.diff_preview.splitlines()) <= 20


# ── Test 12 ─────────────────────────────────────────────────────────


def test_changed_files_present_in_projection():
    """Changed files are present in projection."""
    chain = {"changed_files": ["A.java", "B.java"]}
    proj = build_repair_projection_from_review_chain(review_chain=chain)
    assert proj.changed_files == ("A.java", "B.java")


# ── Test 13 ─────────────────────────────────────────────────────────


def test_risk_and_confidence_present_in_projection():
    """Risk and confidence are present in projection."""
    chain = {"risk": "HIGH", "confidence": 0.99}
    proj = build_repair_projection_from_review_chain(review_chain=chain)
    assert proj.risk == "HIGH"
    assert proj.confidence == 0.99


# ── Test 14 ─────────────────────────────────────────────────────────


def test_policy_status_and_reason_present_in_projection():
    """Policy status and reason are present in projection."""
    chain = {
        "policy_status": "blocked",
        "policy_reason": "exceeds allowed changes",
    }
    proj = build_repair_projection_from_review_chain(review_chain=chain)
    assert proj.policy_status == "blocked"
    assert proj.policy_reason == "exceeds allowed changes"


# ── Test 15 ─────────────────────────────────────────────────────────


def test_forbidden_keys_redacted_from_safe_dict_even_if_present():
    """projection_to_safe_dict redacts forbidden keys via the defense-in-depth pop loop."""
    proj = RepairProposalProjection(proposal_id="p15")
    safe = projection_to_safe_dict(proj)
    for forbidden in FORBIDDEN_PROJECTION_KEYS:
        assert forbidden not in safe, (
            f"forbidden key {forbidden!r} should be redacted from safe dict"
        )
