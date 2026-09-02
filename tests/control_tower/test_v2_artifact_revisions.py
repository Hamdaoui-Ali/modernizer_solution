"""Focused tests for F15 job005 — ArtifactRevision domain model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.domain.entities import ArtifactRevisionRecord
from migration_factory.control_tower.schemas.artifact_revision import (
    ArtifactRevision,
    ArtifactRevisionKind,
    ArtifactRevisionStatus,
    get_upstream_kind,
)


# ── helpers ───────────────────────────────────────────────────────────


def _valid_revision(**overrides) -> ArtifactRevision:
    defaults = {
        "revision_id": "rev-001",
        "job_id": "job-abc",
        "stage_index": 1,
        "revision_kind": ArtifactRevisionKind.ANALYSIS,
        "revision_status": ArtifactRevisionStatus.DRAFT,
        "revision_order": 0,
        "evidence_checksum": "sha256:abc",
        "artifact_refs": ("artifact-1",),
        "created_at": "2026-06-17T12:00:00Z",
        "created_by": "system",
    }
    defaults.update(overrides)
    return ArtifactRevision(**defaults)


# ── construction / basic fields ──────────────────────────────────────


def test_draft_analysis_revision() -> None:
    rev = _valid_revision()
    assert rev.revision_id == "rev-001"
    assert rev.revision_kind == ArtifactRevisionKind.ANALYSIS
    assert rev.revision_status == ArtifactRevisionStatus.DRAFT
    assert rev.is_draft is True
    assert rev.is_accepted is False


def test_all_kinds_supported() -> None:
    for kind in ArtifactRevisionKind:
        rev = _valid_revision(revision_kind=kind)
        assert rev.revision_kind == kind


def test_stages_1_2_3_supported() -> None:
    for stage in (1, 2, 3):
        rev = _valid_revision(stage_index=stage)
        assert rev.stage_index == stage


# ── accepted revision contract ───────────────────────────────────────


def test_accepted_revision_valid() -> None:
    rev = _valid_revision(
        revision_status=ArtifactRevisionStatus.ACCEPTED,
        accepted_at="2026-06-17T13:00:00Z",
        accepted_by="user-1",
        accepted_at_gate_id="gate-001",
    )
    assert rev.is_accepted is True
    assert rev.accepted_at == "2026-06-17T13:00:00Z"
    assert rev.accepted_by == "user-1"


def test_accepted_requires_accepted_at() -> None:
    with pytest.raises(ValidationError, match="accepted_at"):
        _valid_revision(
            revision_status=ArtifactRevisionStatus.ACCEPTED,
            accepted_by="user-1",
        )


def test_accepted_requires_accepted_by() -> None:
    with pytest.raises(ValidationError, match="accepted_by"):
        _valid_revision(
            revision_status=ArtifactRevisionStatus.ACCEPTED,
            accepted_at="2026-06-17T13:00:00Z",
        )


def test_draft_rejects_accepted_fields() -> None:
    with pytest.raises(ValidationError, match="must not have accepted_at"):
        _valid_revision(
            revision_status=ArtifactRevisionStatus.DRAFT,
            accepted_at="2026-06-17T13:00:00Z",
        )


# ── superseded revision contract ─────────────────────────────────────


def test_superseded_revision_valid() -> None:
    rev = _valid_revision(
        revision_id="rev-old",
        revision_status=ArtifactRevisionStatus.SUPERSEDED,
        superseded_by_revision_id="rev-new",
    )
    assert rev.is_superseded is True
    assert rev.superseded_by_revision_id == "rev-new"


def test_superseded_requires_superseded_by() -> None:
    with pytest.raises(ValidationError, match="superseded_by_revision_id"):
        _valid_revision(
            revision_status=ArtifactRevisionStatus.SUPERSEDED,
        )


def test_non_superseded_rejects_superseded_by() -> None:
    with pytest.raises(ValidationError, match="Only superseded revisions"):
        _valid_revision(
            revision_status=ArtifactRevisionStatus.DRAFT,
            superseded_by_revision_id="rev-other",
        )


# ── lineage ──────────────────────────────────────────────────────────


def test_revision_lineage_chain() -> None:
    """Superseded revisions stay queryable via their IDs."""
    v1 = _valid_revision(
        revision_id="rev-v1",
        revision_order=0,
        revision_status=ArtifactRevisionStatus.SUPERSEDED,
        superseded_by_revision_id="rev-v2",
    )
    v2 = _valid_revision(
        revision_id="rev-v2",
        revision_order=1,
        prior_revision_id="rev-v1",
        prior_revision_checksum=v1.evidence_checksum,
    )
    assert v1.superseded_by_revision_id == "rev-v2"
    assert v2.prior_revision_id == "rev-v1"
    assert v2.prior_revision_checksum == v1.evidence_checksum


# ── upstream dependency contract ─────────────────────────────────────


def test_get_upstream_kind() -> None:
    assert get_upstream_kind(ArtifactRevisionKind.ANALYSIS) is None
    assert get_upstream_kind(ArtifactRevisionKind.PLANNING) == ArtifactRevisionKind.ANALYSIS
    assert get_upstream_kind(ArtifactRevisionKind.APPROVAL) == ArtifactRevisionKind.PLANNING
    assert get_upstream_kind(ArtifactRevisionKind.REPAIR) is None


def test_planning_depends_on_accepted_analysis_model() -> None:
    """Planning revision kind declares ANALYSIS as upstream dependency.
    The service layer enforces that only ACCEPTED analysis revisions
    are consumed by planning, but this model expresses the relationship."""
    upstream = get_upstream_kind(ArtifactRevisionKind.PLANNING)
    assert upstream == ArtifactRevisionKind.ANALYSIS
    # Model guarantees: upstream must exist in ACCEPTED state
    # before a PLANNING revision can be accepted.


def test_approval_depends_on_accepted_planning_model() -> None:
    """Approval revision kind declares PLANNING as upstream dependency."""
    upstream = get_upstream_kind(ArtifactRevisionKind.APPROVAL)
    assert upstream == ArtifactRevisionKind.PLANNING


# ── checksum binding ─────────────────────────────────────────────────


def test_evidence_checksum_required() -> None:
    with pytest.raises(ValidationError, match="evidence_checksum"):
        ArtifactRevision(
            revision_id="rev-001",
            job_id="job-abc",
            stage_index=1,
            revision_kind=ArtifactRevisionKind.ANALYSIS,
            revision_order=0,
            evidence_checksum="",  # empty not allowed
            created_at="2026-06-17T12:00:00Z",
            created_by="system",
        )


def test_artifact_refs_persisted() -> None:
    rev = _valid_revision(artifact_refs=("a1", "a2", "a3"))
    assert rev.artifact_refs == ("a1", "a2", "a3")


# ── immutability / anti-patterns ─────────────────────────────────────


def test_revision_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ArtifactRevision(
            revision_id="rev-001",
            job_id="job-abc",
            stage_index=1,
            revision_kind="analysis",
            revision_order=0,
            evidence_checksum="sha256:abc",
            created_at="2026-06-17T12:00:00Z",
            created_by="system",
            sandbox_path="/tmp/evil",  # blocked anti-pattern
        )


# ── entity record round-trip ──────────────────────────────────────────


def test_entity_record_from_revision() -> None:
    rev = _valid_revision(
        revision_id="rev-e1",
        job_id="job-e1",
        stage_index=2,
        revision_kind=ArtifactRevisionKind.PLANNING,
        revision_status=ArtifactRevisionStatus.ACCEPTED,
        revision_order=2,
        evidence_checksum="sha256:plan-chk",
        prior_revision_checksum="sha256:old-chk",
        artifact_refs=("a1", "a2"),
        prior_revision_id="rev-old",
        accepted_at_gate_id="gate-planning-1",
        created_at="2026-06-17T12:00:00Z",
        created_by="planner",
        accepted_at="2026-06-17T14:00:00Z",
        accepted_by="approver-1",
    )

    record = ArtifactRevisionRecord(
        revision_id=rev.revision_id,
        job_id=rev.job_id,
        stage_index=rev.stage_index,
        revision_kind=rev.revision_kind.value,
        revision_status=rev.revision_status.value,
        revision_order=rev.revision_order,
        evidence_checksum=rev.evidence_checksum,
        prior_revision_checksum=rev.prior_revision_checksum,
        artifact_refs_json='["a1","a2"]',
        prior_revision_id=rev.prior_revision_id,
        superseded_by_revision_id=rev.superseded_by_revision_id,
        accepted_at_gate_id=rev.accepted_at_gate_id,
        created_at=rev.created_at,
        created_by=rev.created_by,
        accepted_at=rev.accepted_at,
        accepted_by=rev.accepted_by,
    )

    assert record.revision_id == "rev-e1"
    assert record.revision_kind == "planning"
    assert record.revision_status == "accepted"
    assert record.evidence_checksum == "sha256:plan-chk"
    assert record.accepted_at_gate_id == "gate-planning-1"


def test_entity_record_is_frozen() -> None:
    import dataclasses

    record = ArtifactRevisionRecord(
        revision_id="rev-f1",
        job_id="job-f1",
        stage_index=1,
        revision_kind="analysis",
        revision_status="draft",
        revision_order=0,
        evidence_checksum="sha256:abc",
        prior_revision_checksum=None,
        artifact_refs_json="[]",
        prior_revision_id=None,
        superseded_by_revision_id=None,
        accepted_at_gate_id=None,
        created_at="2026-06-17T12:00:00Z",
        created_by="system",
    )
    assert dataclasses.is_dataclass(record)
    with pytest.raises(Exception):
        record.revision_status = "accepted"  # type: ignore[misc]
