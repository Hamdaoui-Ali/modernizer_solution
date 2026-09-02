"""F15 ArtifactRevision domain schemas — versioned evidence for governed stages.

ArtifactRevisions track the evolving output of analysis, planning,
approval, and repair phases. Downstream phases consume only accepted
revisions. Superseded revisions remain queryable for audit.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator, model_validator

from .common import NonEmptyString, StrictModel, require_non_empty_string


# ── revision enums ────────────────────────────────────────────────────


class ArtifactRevisionKind(str, Enum):
    """The phase that produced this evidence revision."""

    ANALYSIS = "analysis"
    PLANNING = "planning"
    APPROVAL = "approval"
    REPAIR = "repair"


class ArtifactRevisionStatus(str, Enum):
    """Lifecycle status of a revision."""

    DRAFT = "draft"          # In progress, not yet accepted
    ACCEPTED = "accepted"    # Terminal — downstream phases may consume
    SUPERSEDED = "superseded"  # Replaced by a newer revision


# ── ArtifactRevision pydantic model ──────────────────────────────────


class ArtifactRevision(StrictModel):
    """Versioned evidence produced by a governed phase.

    Each revision is checksum-bound to its artifact content.
    Once accepted, a revision is terminal and must not change.
    Superseded revisions remain queryable via revision_id.
    """

    revision_id: NonEmptyString
    job_id: NonEmptyString
    stage_index: int = Field(ge=1, le=3)
    revision_kind: ArtifactRevisionKind
    revision_status: ArtifactRevisionStatus = ArtifactRevisionStatus.DRAFT
    revision_order: int = Field(ge=0)

    # ── checksum binding ──────────────────────────────────────────
    evidence_checksum: NonEmptyString
    prior_revision_checksum: str | None = None

    # ── artifact references ────────────────────────────────────────
    artifact_refs: tuple[str, ...] = Field(default_factory=tuple)

    # ── lineage ────────────────────────────────────────────────────
    # Points to the previous revision this one replaces, if any.
    prior_revision_id: str | None = None
    # Set when a newer revision supersedes this one.
    superseded_by_revision_id: str | None = None

    # ── gate binding ───────────────────────────────────────────────
    # The gate where this revision was accepted (if applicable).
    accepted_at_gate_id: str | None = None

    # ── profile metadata (F3-T6) ────────────────────────────────────
    # Source and target profiles in effect when this artifact revision
    # was produced. None when profile routing is not yet configured.
    source_profile: str | None = None
    target_profile: str | None = None

    # ── timestamps & actor ─────────────────────────────────────────
    created_at: str
    created_by: NonEmptyString
    accepted_at: str | None = None
    accepted_by: str | None = None

    # ── validation ─────────────────────────────────────────────────

    @field_validator("revision_id", "job_id", "evidence_checksum",
                     "created_at", "created_by", mode="after")
    @classmethod
    def _validate_required_strings(cls, value: str, info) -> str:
        return require_non_empty_string(value, info.field_name)

    @field_validator("revision_kind", mode="before")
    @classmethod
    def _coerce_kind(cls, value) -> ArtifactRevisionKind:
        if isinstance(value, ArtifactRevisionKind):
            return value
        return ArtifactRevisionKind(value)

    @field_validator("revision_status", mode="before")
    @classmethod
    def _coerce_status(cls, value) -> ArtifactRevisionStatus:
        if isinstance(value, ArtifactRevisionStatus):
            return value
        return ArtifactRevisionStatus(value)

    @model_validator(mode="after")
    def _accepted_must_have_accepted_fields(self) -> "ArtifactRevision":
        if self.revision_status == ArtifactRevisionStatus.ACCEPTED:
            if not self.accepted_at or not self.accepted_by:
                raise ValueError(
                    "An accepted revision must set accepted_at and accepted_by"
                )
        return self

    @model_validator(mode="after")
    def _draft_must_not_have_accepted_fields(self) -> "ArtifactRevision":
        if self.revision_status == ArtifactRevisionStatus.DRAFT:
            if self.accepted_at is not None or self.accepted_by is not None:
                raise ValueError(
                    "A draft revision must not have accepted_at or accepted_by"
                )
        return self

    @model_validator(mode="after")
    def _superseded_consistency(self) -> "ArtifactRevision":
        if self.revision_status == ArtifactRevisionStatus.SUPERSEDED:
            if self.superseded_by_revision_id is None:
                raise ValueError(
                    "A superseded revision must set superseded_by_revision_id"
                )
        return self

    @model_validator(mode="after")
    def _non_superseded_no_superseded_by(self) -> "ArtifactRevision":
        if self.revision_status != ArtifactRevisionStatus.SUPERSEDED:
            if self.superseded_by_revision_id is not None:
                raise ValueError(
                    "Only superseded revisions may set superseded_by_revision_id"
                )
        return self

    @property
    def is_accepted(self) -> bool:
        return self.revision_status == ArtifactRevisionStatus.ACCEPTED

    @property
    def is_draft(self) -> bool:
        return self.revision_status == ArtifactRevisionStatus.DRAFT

    @property
    def is_superseded(self) -> bool:
        return self.revision_status == ArtifactRevisionStatus.SUPERSEDED


# ── helpers ───────────────────────────────────────────────────────────

# Which downstream kind depends on which upstream kind being accepted.
_REVISION_KIND_DEPENDENCIES: dict[ArtifactRevisionKind, ArtifactRevisionKind | None] = {
    ArtifactRevisionKind.ANALYSIS: None,        # no upstream dependency
    ArtifactRevisionKind.PLANNING: ArtifactRevisionKind.ANALYSIS,
    ArtifactRevisionKind.APPROVAL: ArtifactRevisionKind.PLANNING,
    ArtifactRevisionKind.REPAIR: None,           # repair happens on failure, not gated
}


def get_upstream_kind(kind: ArtifactRevisionKind) -> ArtifactRevisionKind | None:
    """Return the revision kind that must be ACCEPTED before *kind* may proceed."""
    return _REVISION_KIND_DEPENDENCIES.get(kind)
