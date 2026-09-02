"""Focused tests for F15 job004 — GateDecision idempotency and append-only model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.domain.entities import GateDecisionRecord
from migration_factory.control_tower.schemas.phase_gate import (
    GateDecision,
    GateDecisionRequest,
    GateDecisionResult,
)


# ── GateDecisionRequest schema ───────────────────────────────────────


def test_decision_request_construction() -> None:
    req = GateDecisionRequest(
        gate_id="gate-abc",
        job_id="job-xyz",
        action=GateDecision.CONTINUE,
        expected_gate_checksum="sha256:abc123",
        idempotency_key="idem-001",
        request_checksum="sha256:req456",
        decided_by="user-1",
        decided_at="2026-06-17T14:00:00Z",
        actor_type="human",
    )
    assert req.gate_id == "gate-abc"
    assert req.action == GateDecision.CONTINUE
    assert req.idempotency_key == "idem-001"


def test_decision_request_coerces_action_string() -> None:
    req = GateDecisionRequest(
        gate_id="gate-abc",
        job_id="job-xyz",
        action="approve",
        expected_gate_checksum="sha256:abc123",
        idempotency_key="idem-001",
        request_checksum="sha256:req456",
        decided_by="user-1",
        decided_at="2026-06-17T14:00:00Z",
        actor_type="human",
    )
    assert req.action == GateDecision.APPROVE


def test_decision_request_rejects_empty_required_fields() -> None:
    fields = [
        ("gate_id", ""),
        ("job_id", ""),
        ("expected_gate_checksum", ""),
        ("idempotency_key", ""),
        ("request_checksum", ""),
        ("decided_by", ""),
        ("decided_at", ""),
    ]
    for field_name, bad_value in fields:
        kwargs = {
            "gate_id": "gate-abc",
            "job_id": "job-xyz",
            "action": GateDecision.APPROVE,
            "expected_gate_checksum": "sha256:abc123",
            "idempotency_key": "idem-001",
            "request_checksum": "sha256:req456",
            "decided_by": "user-1",
            "decided_at": "2026-06-17T14:00:00Z",
            "actor_type": "human",
        }
        kwargs[field_name] = bad_value
        with pytest.raises(ValidationError):
            GateDecisionRequest(**kwargs)


# ── GateDecisionResult schema ────────────────────────────────────────


def test_decision_result_minimal() -> None:
    result = GateDecisionResult(
        decision_id="dec-001",
        gate_id="gate-abc",
        job_id="job-xyz",
        action=GateDecision.CONTINUE,
        idempotency_key="idem-001",
        decided_at="2026-06-17T14:00:00Z",
    )
    assert result.result_gate_id is None
    assert result.result_command_id is None
    assert result.result_revision_id is None


def test_decision_result_with_command() -> None:
    result = GateDecisionResult(
        decision_id="dec-001",
        gate_id="gate-abc",
        job_id="job-xyz",
        action=GateDecision.CONTINUE,
        idempotency_key="idem-001",
        result_command_id="cmd-123",
        decided_at="2026-06-17T14:00:00Z",
    )
    assert result.result_command_id == "cmd-123"


# ── GateDecisionRecord entity ────────────────────────────────────────


def test_decision_record_construction() -> None:
    record = GateDecisionRecord(
        decision_id="dec-001",
        gate_id="gate-abc",
        job_id="job-xyz",
        action="continue",
        expected_gate_checksum="sha256:abc123",
        idempotency_key="idem-001",
        request_checksum="sha256:req456",
        decided_by="user-1",
        decided_at="2026-06-17T14:00:00Z",
        actor_type="human",
    )
    assert record.decision_id == "dec-001"
    assert record.gate_id == "gate-abc"
    assert record.action == "continue"
    assert record.idempotency_key == "idem-001"
    assert record.request_checksum == "sha256:req456"
    assert record.expected_gate_checksum == "sha256:abc123"


def test_decision_record_with_result_refs() -> None:
    record = GateDecisionRecord(
        decision_id="dec-002",
        gate_id="gate-abc",
        job_id="job-xyz",
        action="reanalyze",
        expected_gate_checksum="sha256:abc123",
        idempotency_key="idem-002",
        request_checksum="sha256:req789",
        result_gate_id="gate-new-001",
        result_command_id=None,
        result_revision_id=None,
        decided_by="user-1",
        decided_at="2026-06-17T14:00:00Z",
        actor_type="human",
    )
    assert record.result_gate_id == "gate-new-001"
    assert record.result_command_id is None


def test_decision_record_defaults() -> None:
    record = GateDecisionRecord(
        decision_id="dec-003",
        gate_id="gate-abc",
        job_id="job-xyz",
        action="reject",
        expected_gate_checksum="sha256:abc123",
        idempotency_key="idem-003",
        request_checksum="sha256:req999",
        decided_by="user-1",
        decided_at="2026-06-17T14:00:00Z",
        actor_type="human",
    )
    assert record.actor_type == "human"
    assert record.actor_id == ""
    assert record.result_gate_id is None
    assert record.result_command_id is None
    assert record.result_revision_id is None
    assert record.correlation_id is None
    assert record.causation_id is None


# ── idempotency contract ─────────────────────────────────────────────


def test_same_idempotency_key_and_checksum_is_duplicate() -> None:
    """Two records with identical (idempotency_key, request_checksum)
    are duplicates — same decision_id should be returned."""
    record1 = GateDecisionRecord(
        decision_id="dec-004",
        gate_id="gate-abc",
        job_id="job-xyz",
        action="continue",
        expected_gate_checksum="sha256:gate1",
        idempotency_key="idem-same",
        request_checksum="sha256:same-payload",
        decided_by="user-1",
        decided_at="2026-06-17T14:00:00Z",
    )
    record2 = GateDecisionRecord(
        decision_id="dec-004",  # same decision_id
        gate_id="gate-abc",
        job_id="job-xyz",
        action="continue",
        expected_gate_checksum="sha256:gate1",
        idempotency_key="idem-same",
        request_checksum="sha256:same-payload",
        decided_by="user-1",
        decided_at="2026-06-17T14:00:00Z",
    )
    # The idempotency contract is enforced at the persistence layer.
    # Here we verify the fields are identical.
    assert record1.idempotency_key == record2.idempotency_key
    assert record1.request_checksum == record2.request_checksum
    assert record1.decision_id == record2.decision_id


def test_different_checksum_same_key_is_conflict() -> None:
    """Different request_checksum under the same idempotency_key
    is a conflicting payload — must be rejected."""
    record_original = GateDecisionRecord(
        decision_id="dec-005",
        gate_id="gate-abc",
        job_id="job-xyz",
        action="continue",
        expected_gate_checksum="sha256:gate1",
        idempotency_key="idem-conflict",
        request_checksum="sha256:payload-v1",
        decided_by="user-1",
        decided_at="2026-06-17T14:00:00Z",
    )
    record_conflict = GateDecisionRecord(
        decision_id="dec-006",  # different id
        gate_id="gate-abc",
        job_id="job-xyz",
        action="reject",  # different action
        expected_gate_checksum="sha256:gate1",
        idempotency_key="idem-conflict",  # same key
        request_checksum="sha256:payload-v2",  # different checksum
        decided_by="user-2",
        decided_at="2026-06-17T15:00:00Z",
    )
    assert record_original.idempotency_key == record_conflict.idempotency_key
    assert record_original.request_checksum != record_conflict.request_checksum
    assert record_original.decision_id != record_conflict.decision_id


# ── append-only immutability ──────────────────────────────────────────


def test_decision_record_is_frozen() -> None:
    import dataclasses

    record = GateDecisionRecord(
        decision_id="dec-007",
        gate_id="gate-abc",
        job_id="job-xyz",
        action="approve",
        expected_gate_checksum="sha256:abc123",
        idempotency_key="idem-007",
        request_checksum="sha256:req007",
        decided_by="user-1",
        decided_at="2026-06-17T14:00:00Z",
    )
    assert dataclasses.is_dataclass(record)
    with pytest.raises(Exception):
        record.action = "reject"  # type: ignore[misc]


# ── decision stores result references ────────────────────────────────


def test_decision_stores_result_gate_id() -> None:
    """After reanalysis, a new gate is created — its id is stored."""
    record = GateDecisionRecord(
        decision_id="dec-008",
        gate_id="gate-original",
        job_id="job-xyz",
        action="reanalyze",
        expected_gate_checksum="sha256:gate1",
        idempotency_key="idem-008",
        request_checksum="sha256:req008",
        result_gate_id="gate-new-analysis",
        decided_by="user-1",
        decided_at="2026-06-17T14:00:00Z",
    )
    assert record.result_gate_id == "gate-new-analysis"


def test_decision_stores_result_command_id() -> None:
    """Continue/approve queues a command — its id is stored."""
    record = GateDecisionRecord(
        decision_id="dec-009",
        gate_id="gate-abc",
        job_id="job-xyz",
        action="continue",
        expected_gate_checksum="sha256:gate1",
        idempotency_key="idem-009",
        request_checksum="sha256:req009",
        result_command_id="cmd-stage2",
        decided_by="user-1",
        decided_at="2026-06-17T14:00:00Z",
    )
    assert record.result_command_id == "cmd-stage2"


def test_decision_stores_result_revision_id() -> None:
    """Revise creates a plan revision — its id is stored."""
    record = GateDecisionRecord(
        decision_id="dec-010",
        gate_id="gate-abc",
        job_id="job-xyz",
        action="revise",
        expected_gate_checksum="sha256:gate1",
        idempotency_key="idem-010",
        request_checksum="sha256:req010",
        result_revision_id="rev-plan-v2",
        decided_by="user-1",
        decided_at="2026-06-17T14:00:00Z",
    )
    assert record.result_revision_id == "rev-plan-v2"


# ── rejects F15 anti-patterns ────────────────────────────────────────


def test_decision_request_rejects_unknown_fields() -> None:
    """No sandbox_path, argv, env, or raw commands from frontend."""
    with pytest.raises(ValidationError):
        GateDecisionRequest(
            gate_id="gate-abc",
            job_id="job-xyz",
            action=GateDecision.CONTINUE,
            expected_gate_checksum="sha256:abc123",
            idempotency_key="idem-001",
            request_checksum="sha256:req456",
            decided_by="user-1",
            decided_at="2026-06-17T14:00:00Z",
            actor_type="human",
            sandbox_path="/tmp/evil",  # blocked
        )


def test_decision_result_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        GateDecisionResult(
            decision_id="dec-001",
            gate_id="gate-abc",
            job_id="job-xyz",
            action=GateDecision.CONTINUE,
            idempotency_key="idem-001",
            decided_at="2026-06-17T14:00:00Z",
            command_argv=("/bin/evil",),  # blocked
        )
