"""Focused tests for F15 jobs 019-020 — gate DTOs and audit trail."""

from __future__ import annotations

from migration_factory.control_tower.application.dto import (
    ArtifactRevisionDto,
    GateDecisionDto,
    GateDto,
)
from migration_factory.control_tower.domain.gate_audit import (
    GateAuditAction,
    GateAuditPayload,
    build_gate_audit_payload,
)


# ── GateDto ──────────────────────────────────────────────────────────


def test_gate_dto_no_path_fields() -> None:
    """GateDto must not expose file paths or raw targets."""
    dto = GateDto(
        gate_id="g1", job_id="j1", gate_phase="analysis_review",
        stage_index=1, gate_status="open", gate_decision="pending",
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=("a1", "a2"),
        created_at="2026-06-17T12:00:00Z",
    )
    # Verify no path-like attributes
    assert not hasattr(dto, "sandbox_path")
    assert not hasattr(dto, "argv")
    assert not hasattr(dto, "command")


def test_gate_dto_resolved_fields() -> None:
    dto = GateDto(
        gate_id="g1", job_id="j1", gate_phase="analysis_review",
        stage_index=1, gate_status="resolved", gate_decision="continue",
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=("a1",),
        created_at="2026-06-17T12:00:00Z",
        resolved_at="2026-06-17T13:00:00Z",
        resolved_by="user-1",
    )
    assert dto.resolved_at == "2026-06-17T13:00:00Z"
    assert dto.resolved_by == "user-1"


# ── GateDecisionDto ──────────────────────────────────────────────────


def test_decision_dto_no_path_fields() -> None:
    dto = GateDecisionDto(
        decision_id="d1", gate_id="g1", action="continue",
        expected_gate_checksum="sha256:abc", idempotency_key="ik1",
        decided_by="u1", decided_at="2026-06-17T14:00:00Z",
    )
    assert not hasattr(dto, "sandbox_path")
    assert not hasattr(dto, "command_argv")


def test_decision_dto_result_refs() -> None:
    dto = GateDecisionDto(
        decision_id="d1", gate_id="g1", action="reanalyze",
        expected_gate_checksum="sha256:abc", idempotency_key="ik1",
        decided_by="u1", decided_at="2026-06-17T14:00:00Z",
        result_gate_id="gate-new", result_command_id="cmd-x",
    )
    assert dto.result_gate_id == "gate-new"
    assert dto.result_command_id == "cmd-x"
    assert dto.result_revision_id is None


# ── ArtifactRevisionDto ─────────────────────────────────────────────


def test_revision_dto_no_path_fields() -> None:
    dto = ArtifactRevisionDto(
        revision_id="r1", job_id="j1", stage_index=1,
        revision_kind="analysis", revision_status="draft",
        revision_order=0, evidence_checksum="sha256:abc",
        artifact_refs=("a1",), created_at="2026-06-17T12:00:00Z",
        created_by="system",
    )
    assert not hasattr(dto, "sandbox_path")
    assert not hasattr(dto, "filesystem_target")


def test_revision_dto_accepted() -> None:
    dto = ArtifactRevisionDto(
        revision_id="r1", job_id="j1", stage_index=1,
        revision_kind="analysis", revision_status="accepted",
        revision_order=0, evidence_checksum="sha256:abc",
        artifact_refs=(), created_at="2026-06-17T12:00:00Z",
        created_by="system", accepted_at="2026-06-17T13:00:00Z",
        accepted_by="user-1",
    )
    assert dto.revision_status == "accepted"
    assert dto.accepted_by == "user-1"


# ── GateAuditPayload ────────────────────────────────────────────────


def test_audit_payload_no_paths() -> None:
    payload = build_gate_audit_payload(
        gate_id="g1", gate_phase="analysis_review", stage_index=1,
        action=GateAuditAction.GATE_CREATED,
    )
    json_str = payload.to_json()
    assert "path" not in json_str.lower()
    assert "sandbox" not in json_str.lower()
    assert "argv" not in json_str.lower()


def test_audit_payload_deterministic_checksum() -> None:
    a = build_gate_audit_payload(
        gate_id="g1", gate_phase="analysis_review", stage_index=1,
        action=GateAuditAction.GATE_CREATED,
    )
    b = build_gate_audit_payload(
        gate_id="g1", gate_phase="analysis_review", stage_index=1,
        action=GateAuditAction.GATE_CREATED,
    )
    assert a.to_checksum() == b.to_checksum()


def test_audit_payload_different_action_different_checksum() -> None:
    a = build_gate_audit_payload(
        gate_id="g1", action=GateAuditAction.GATE_CREATED,
    )
    b = build_gate_audit_payload(
        gate_id="g1", action=GateAuditAction.GATE_RESOLVED,
    )
    assert a.to_checksum() != b.to_checksum()


def test_audit_payload_json_excludes_none() -> None:
    payload = GateAuditPayload(gate_id="g1")
    json_str = payload.to_json()
    assert '"gate_phase"' not in json_str
    assert "null" not in json_str


def test_audit_action_constants_prefixed() -> None:
    for attr in dir(GateAuditAction):
        if attr.isupper():
            val = getattr(GateAuditAction, attr)
            assert val.startswith("f15_"), f"{val} must start with f15_"
