"""Focused tests for F15 job003 — PhaseGate domain model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.schemas.phase_gate import (
    GateDecision,
    GatePhase,
    GateStatus,
    PhaseGate,
    is_valid_decision_for_phase,
)


# ── helpers ───────────────────────────────────────────────────────────


def _valid_gate(**overrides) -> PhaseGate:
    defaults = {
        "gate_id": "gate-001",
        "job_id": "job-abc",
        "gate_phase": GatePhase.ANALYSIS_REVIEW,
        "stage_index": 1,
        "gate_status": GateStatus.OPEN,
        "gate_decision": GateDecision.PENDING,
        "source_artifact_checksum": "abc123",
        "source_artifact_refs": ("artifact-1", "artifact-2"),
        "created_at": "2026-06-17T12:00:00Z",
    }
    defaults.update(overrides)
    return PhaseGate(**defaults)


# ── construction / basic fields ──────────────────────────────────────


def test_construct_open_analysis_review_gate() -> None:
    gate = _valid_gate()
    assert gate.gate_id == "gate-001"
    assert gate.job_id == "job-abc"
    assert gate.gate_phase == GatePhase.ANALYSIS_REVIEW
    assert gate.stage_index == 1
    assert gate.gate_status == GateStatus.OPEN
    assert gate.gate_decision == GateDecision.PENDING
    assert gate.is_open is True
    assert gate.is_resolved is False


def test_construct_with_string_enums_coerced() -> None:
    gate = _valid_gate(
        gate_phase="planning_review",
        gate_status="open",
        gate_decision="pending",
    )
    assert gate.gate_phase == GatePhase.PLANNING_REVIEW
    assert gate.gate_status == GateStatus.OPEN
    assert gate.gate_decision == GateDecision.PENDING


def test_stage_index_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        _valid_gate(stage_index=0)
    with pytest.raises(ValidationError):
        _valid_gate(stage_index=4)


# ── stage 1 / 2 / 3 support ──────────────────────────────────────────


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_phase_gate_supports_stages_1_2_3(stage: int) -> None:
    gate = _valid_gate(stage_index=stage)
    assert gate.stage_index == stage


def test_all_gate_phases_across_stages() -> None:
    """Every gate phase can be created for every valid stage."""
    for phase in GatePhase:
        for stage in (1, 2, 3):
            gate = _valid_gate(gate_phase=phase, stage_index=stage)
            assert gate.gate_phase == phase
            assert gate.stage_index == stage


# ── resolved gate immutability contract ───────────────────────────────


def test_resolved_gate_valid() -> None:
    gate = _valid_gate(
        gate_status=GateStatus.RESOLVED,
        gate_decision=GateDecision.CONTINUE,
        resolved_at="2026-06-17T13:00:00Z",
        resolved_by="user-1",
    )
    assert gate.is_resolved is True
    assert gate.is_open is False


def test_resolved_gate_rejects_pending_decision() -> None:
    with pytest.raises(ValidationError, match="non-pending decision"):
        _valid_gate(
            gate_status=GateStatus.RESOLVED,
            gate_decision=GateDecision.PENDING,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="user-1",
        )


def test_resolved_gate_requires_resolved_at() -> None:
    with pytest.raises(ValidationError, match="resolved_at"):
        _valid_gate(
            gate_status=GateStatus.RESOLVED,
            gate_decision=GateDecision.CONTINUE,
            resolved_by="user-1",
        )


def test_resolved_gate_requires_resolved_by() -> None:
    with pytest.raises(ValidationError, match="resolved_by"):
        _valid_gate(
            gate_status=GateStatus.RESOLVED,
            gate_decision=GateDecision.CONTINUE,
            resolved_at="2026-06-17T13:00:00Z",
        )


def test_open_gate_rejects_resolution_fields() -> None:
    with pytest.raises(ValidationError, match="must not have resolved_at"):
        _valid_gate(
            gate_status=GateStatus.OPEN,
            resolved_at="2026-06-17T13:00:00Z",
        )
    with pytest.raises(ValidationError, match="must not have resolved_at or resolved_by"):
        _valid_gate(
            gate_status=GateStatus.OPEN,
            resolved_by="user-1",
        )


# ── superseded gates ─────────────────────────────────────────────────


def test_superseded_gate_does_not_require_decision() -> None:
    """A superseded gate may remain with PENDING decision — it was
    replaced before being resolved."""
    gate = _valid_gate(
        gate_status=GateStatus.SUPERSEDED,
        gate_decision=GateDecision.PENDING,
    )
    assert gate.gate_status == GateStatus.SUPERSEDED


# ── open gate uniqueness key ─────────────────────────────────────────


def test_open_gate_key() -> None:
    gate = _valid_gate(job_id="job-1", gate_phase=GatePhase.PLANNING_REVIEW, stage_index=2)
    assert gate.open_gate_key == ("job-1", GatePhase.PLANNING_REVIEW, 2)


def test_different_gates_have_different_keys() -> None:
    a = _valid_gate(job_id="job-1", gate_phase=GatePhase.ANALYSIS_REVIEW, stage_index=1)
    b = _valid_gate(job_id="job-1", gate_phase=GatePhase.ANALYSIS_REVIEW, stage_index=2)
    c = _valid_gate(job_id="job-1", gate_phase=GatePhase.PLANNING_REVIEW, stage_index=1)
    d = _valid_gate(job_id="job-2", gate_phase=GatePhase.ANALYSIS_REVIEW, stage_index=1)

    keys = {a.open_gate_key, b.open_gate_key, c.open_gate_key, d.open_gate_key}
    assert len(keys) == 4


# ── decision-per-phase validation ────────────────────────────────────


@pytest.mark.parametrize(
    "phase, decision, expected",
    [
        (GatePhase.ANALYSIS_REVIEW, GateDecision.CONTINUE, True),
        (GatePhase.ANALYSIS_REVIEW, GateDecision.REANALYZE, True),
        (GatePhase.ANALYSIS_REVIEW, GateDecision.APPROVE, False),
        (GatePhase.ANALYSIS_REVIEW, GateDecision.REJECT, False),
        (GatePhase.ANALYSIS_REVIEW, GateDecision.REVISE, False),
        (GatePhase.PLANNING_REVIEW, GateDecision.CONTINUE, True),
        (GatePhase.PLANNING_REVIEW, GateDecision.REVISE, True),
        (GatePhase.PLANNING_REVIEW, GateDecision.APPROVE, False),
        (GatePhase.APPROVAL_REVIEW, GateDecision.APPROVE, True),
        (GatePhase.APPROVAL_REVIEW, GateDecision.REJECT, True),
        (GatePhase.APPROVAL_REVIEW, GateDecision.CONTINUE, False),
        (GatePhase.REPAIR_REVIEW, GateDecision.CONTINUE, True),
        (GatePhase.REPAIR_REVIEW, GateDecision.REANALYZE, True),
        (GatePhase.REPAIR_REVIEW, GateDecision.REVISE, True),
        (GatePhase.REPAIR_REVIEW, GateDecision.REJECT, True),
        (GatePhase.STAGE_COMPLETION_REVIEW, GateDecision.CONTINUE, True),
        (GatePhase.STAGE_COMPLETION_REVIEW, GateDecision.REJECT, False),
    ],
)
def test_is_valid_decision_for_phase(
    phase: GatePhase, decision: GateDecision, expected: bool
) -> None:
    assert is_valid_decision_for_phase(phase, decision) == expected


# ── checksum & artifact refs ─────────────────────────────────────────


def test_checksum_fields_persist() -> None:
    gate = _valid_gate(
        source_artifact_checksum="sha256:deadbeef",
        resolved_artifact_checksum=None,
    )
    assert gate.source_artifact_checksum == "sha256:deadbeef"
    assert gate.resolved_artifact_checksum is None


def test_source_artifact_refs_ordered() -> None:
    gate = _valid_gate(source_artifact_refs=("b", "a", "c"))
    assert gate.source_artifact_refs == ("b", "a", "c")


def test_default_source_artifact_refs_empty() -> None:
    gate = PhaseGate(
        gate_id="g1",
        job_id="j1",
        gate_phase=GatePhase.ANALYSIS_REVIEW,
        stage_index=1,
        created_at="2026-06-17T12:00:00Z",
    )
    assert gate.source_artifact_refs == ()


# ── immutability (StrictModel frozen=True, extra="forbid") ───────────


def test_phase_gate_rejects_unknown_fields() -> None:
    """StrictModel extra='forbid' rejects anti-patterns like sandbox_path."""
    with pytest.raises(ValidationError):
        PhaseGate(
            gate_id="g1",
            job_id="j1",
            gate_phase="analysis_review",
            stage_index=1,
            created_at="2026-06-17T12:00:00Z",
            sandbox_path="/tmp/evil",  # blocked F15 anti-pattern
        )


# ── Entity record round-trip ──────────────────────────────────────────

def test_entity_record_from_gate() -> None:
    from migration_factory.control_tower.domain.entities import PhaseGateRecord

    gate = _valid_gate(
        gate_id="gate-e1",
        job_id="job-e1",
        gate_phase=GatePhase.APPROVAL_REVIEW,
        stage_index=2,
        gate_status=GateStatus.RESOLVED,
        gate_decision=GateDecision.APPROVE,
        source_artifact_checksum="chk1",
        resolved_artifact_checksum="chk2",
        source_artifact_refs=("a1", "a2"),
        created_at="2026-06-17T12:00:00Z",
        resolved_at="2026-06-17T13:00:00Z",
        resolved_by="approver-1",
    )

    record = PhaseGateRecord(
        gate_id=gate.gate_id,
        job_id=gate.job_id,
        gate_phase=gate.gate_phase.value,
        stage_index=gate.stage_index,
        gate_status=gate.gate_status.value,
        gate_decision=gate.gate_decision.value,
        source_artifact_checksum=gate.source_artifact_checksum,
        resolved_artifact_checksum=gate.resolved_artifact_checksum,
        source_artifact_refs_json='["a1","a2"]',
        created_at=gate.created_at,
        resolved_at=gate.resolved_at,
        resolved_by=gate.resolved_by,
    )

    assert record.gate_id == "gate-e1"
    assert record.job_id == "job-e1"
    assert record.gate_phase == "approval_review"
    assert record.stage_index == 2
    assert record.gate_status == "resolved"
    assert record.gate_decision == "approve"
    assert record.source_artifact_checksum == "chk1"
    assert record.resolved_artifact_checksum == "chk2"
    assert record.source_artifact_refs_json == '["a1","a2"]'
    assert record.created_at == "2026-06-17T12:00:00Z"
    assert record.resolved_at == "2026-06-17T13:00:00Z"
    assert record.resolved_by == "approver-1"


def test_entity_record_is_frozen() -> None:
    from migration_factory.control_tower.domain.entities import PhaseGateRecord
    import dataclasses

    record = PhaseGateRecord(
        gate_id="g1",
        job_id="j1",
        gate_phase="analysis_review",
        stage_index=1,
        gate_status="open",
        gate_decision="pending",
        source_artifact_checksum="",
        resolved_artifact_checksum=None,
        source_artifact_refs_json="[]",
        created_at="2026-06-17T12:00:00Z",
    )
    assert dataclasses.is_dataclass(record)
    # frozen dataclass: mutation should raise
    with pytest.raises(Exception):
        record.gate_status = "resolved"  # type: ignore[misc]
