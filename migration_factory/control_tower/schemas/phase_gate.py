"""F15 PhaseGate domain schemas — durable wait-state objects for governed stages.

PhaseGates are backend-owned objects that pause pipeline execution
at analysis, planning, approval, repair, and stage-completion review
points.  The chatbot may explain gate-bound evidence, but only the
backend may resolve, supersede, or transition a gate.

Design invariants:
  * A resolved gate is immutable in the service contract.
  * At most one open gate exists per (job_id, gate_phase, stage_index).
  * Gate explanations must read gate-bound artifact refs/checksums,
    never stale previews.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator, model_validator

from .common import NonEmptyString, StrictModel, require_non_empty_string


# ── actor type ───────────────────────────────────────────────────────


class GateActorType(str, Enum):
    """Who or what initiated the gate action."""

    HUMAN = "human"        # Real user — authoritative for all actions
    ASSISTANT = "assistant"  # AI assistant — requires user confirmation
    API = "api"            # Automated caller (scripts, webhook)
    SYSTEM = "system"      # Backend system action (auto-continue, etc.)


HUMAN_AUTHORITATIVE_ACTIONS: frozenset[str] = frozenset({
    "approve",
    "reject",
    "override_source_profile",
})


# ── gate enums ────────────────────────────────────────────────────────


class GatePhase(str, Enum):
    """Which workflow phase this gate protects."""

    ANALYSIS_REVIEW = "analysis_review"
    PLANNING_REVIEW = "planning_review"
    APPROVAL_REVIEW = "approval_review"
    REPAIR_REVIEW = "repair_review"
    STAGE_COMPLETION_REVIEW = "stage_completion_review"


class GateStatus(str, Enum):
    """Lifecycle status of a gate."""

    OPEN = "open"           # Awaiting a decision
    RESOLVED = "resolved"   # Decision made — immutable thereafter
    SUPERSEDED = "superseded"  # Replaced by a newer gate for the same phase


class GateDecision(str, Enum):
    """Possible human/chatbot-expressed decisions at a gate.

    The chatbot may express these intents flexibly in natural language;
    the backend maps them to exactly one typed decision.
    """

    PENDING = "pending"
    CONTINUE = "continue"
    REANALYZE = "reanalyze"
    REVISE = "revise"
    APPROVE = "approve"
    REJECT = "reject"
    OVERRIDE_SOURCE_PROFILE = "override_source_profile"


# ── PhaseGate pydantic model ──────────────────────────────────────────


class PhaseGate(StrictModel):
    """Durable wait-state object for a governed stage gate.

    Every gate is bound to specific artifact content via checksums.
    Once resolved, the gate must NOT be modified — the service layer
    enforces immutability by refusing updates when gate_status != OPEN.
    """

    gate_id: NonEmptyString
    job_id: NonEmptyString
    gate_phase: GatePhase
    stage_index: int = Field(ge=1, le=3)

    gate_status: GateStatus = GateStatus.OPEN
    gate_decision: GateDecision = GateDecision.PENDING

    # ── checksum binding ──────────────────────────────────────────
    # Ties the gate to exact artifact content so explanations are
    # never based on stale previews.
    source_artifact_checksum: str = ""
    resolved_artifact_checksum: str | None = None

    # ── artifact references ───────────────────────────────────────
    # Ordered tuple of evidence artifact ids this gate reviews.
    source_artifact_refs: tuple[str, ...] = Field(default_factory=tuple)

    # ── timestamps & actor ────────────────────────────────────────
    created_at: str
    resolved_at: str | None = None
    resolved_by: str | None = None

    # ── validation ────────────────────────────────────────────────

    @field_validator("gate_id", "job_id", "created_at", mode="after")
    @classmethod
    def _validate_required_strings(cls, value: str, info) -> str:
        return require_non_empty_string(value, info.field_name)

    @field_validator("gate_phase", mode="before")
    @classmethod
    def _coerce_gate_phase(cls, value) -> GatePhase:
        if isinstance(value, GatePhase):
            return value
        return GatePhase(value)

    @field_validator("gate_status", mode="before")
    @classmethod
    def _coerce_gate_status(cls, value) -> GateStatus:
        if isinstance(value, GateStatus):
            return value
        return GateStatus(value)

    @field_validator("gate_decision", mode="before")
    @classmethod
    def _coerce_gate_decision(cls, value) -> GateDecision:
        if isinstance(value, GateDecision):
            return value
        return GateDecision(value)

    @model_validator(mode="after")
    def _resolved_gate_must_have_decision(self) -> "PhaseGate":
        if self.gate_status == GateStatus.RESOLVED:
            if self.gate_decision == GateDecision.PENDING:
                raise ValueError(
                    "A resolved gate must have a non-pending decision"
                )
        return self

    @model_validator(mode="after")
    def _resolved_fields_consistency(self) -> "PhaseGate":
        if self.gate_status == GateStatus.RESOLVED:
            if not self.resolved_at or not self.resolved_by:
                raise ValueError(
                    "A resolved gate must set resolved_at and resolved_by"
                )
        return self

    @model_validator(mode="after")
    def _open_gate_no_resolution_fields(self) -> "PhaseGate":
        if self.gate_status == GateStatus.OPEN:
            if self.resolved_at is not None or self.resolved_by is not None:
                raise ValueError(
                    "An open gate must not have resolved_at or resolved_by set"
                )
        return self

    @property
    def is_open(self) -> bool:
        return self.gate_status == GateStatus.OPEN

    @property
    def is_resolved(self) -> bool:
        return self.gate_status == GateStatus.RESOLVED

    @property
    def open_gate_key(self) -> tuple[str, str, int]:
        """Uniqueness key for open gates: (job_id, gate_phase, stage_index).

        At most one gate with this key may be OPEN at any time.
        Superseded and resolved gates do not conflict.
        """
        return (self.job_id, self.gate_phase, self.stage_index)


# ── helpers ───────────────────────────────────────────────────────────

# Allowed transitions from OPEN
_ALLOWED_OPEN_TRANSITIONS: dict[GateStatus, frozenset[GateStatus]] = {
    GateStatus.OPEN: frozenset({GateStatus.RESOLVED, GateStatus.SUPERSEDED}),
    GateStatus.RESOLVED: frozenset(),   # immutable
    GateStatus.SUPERSEDED: frozenset(), # immutable
}

# Valid terminal decision for each gate phase
_VALID_PHASE_DECISIONS: dict[GatePhase, frozenset[GateDecision]] = {
    GatePhase.ANALYSIS_REVIEW: frozenset({
        GateDecision.CONTINUE,
        GateDecision.REANALYZE,
        GateDecision.OVERRIDE_SOURCE_PROFILE,
    }),
    GatePhase.PLANNING_REVIEW: frozenset({
        GateDecision.CONTINUE,
        GateDecision.REVISE,
    }),
    GatePhase.APPROVAL_REVIEW: frozenset({
        GateDecision.APPROVE,
        GateDecision.REJECT,
    }),
    GatePhase.REPAIR_REVIEW: frozenset({
        GateDecision.CONTINUE,
        GateDecision.REANALYZE,
        GateDecision.REVISE,
        GateDecision.REJECT,
    }),
    GatePhase.STAGE_COMPLETION_REVIEW: frozenset({
        GateDecision.CONTINUE,
    }),
}


def is_valid_decision_for_phase(
    gate_phase: GatePhase,
    decision: GateDecision,
) -> bool:
    """Return True if *decision* is allowed at *gate_phase*."""
    allowed = _VALID_PHASE_DECISIONS.get(gate_phase, frozenset())
    return decision in allowed


# ── GateDecision request/result schemas ───────────────────────────────


class GateDecisionRequest(StrictModel):
    """Backend-validated request to decide a gate.

    The chatbot may express the intent flexibly in natural language;
    the backend maps it to this typed model before any state change.

    Idempotency: duplicate (idempotency_key, request_checksum)
    returns the same result.  A different checksum under the same
    key is rejected.
    """

    gate_id: NonEmptyString
    job_id: NonEmptyString
    action: GateDecision
    expected_gate_checksum: NonEmptyString
    idempotency_key: NonEmptyString
    request_checksum: NonEmptyString
    decided_by: NonEmptyString
    decided_at: NonEmptyString
    actor_type: NonEmptyString
    actor_id: str = ""
    correlation_id: str | None = None
    causation_id: str | None = None

    @field_validator("action", mode="before")
    @classmethod
    def _coerce_action(cls, value) -> GateDecision:
        if isinstance(value, GateDecision):
            return value
        return GateDecision(value)

    @field_validator(
        "gate_id", "job_id", "expected_gate_checksum",
        "idempotency_key", "request_checksum",
        "decided_by", "decided_at", mode="after",
    )
    @classmethod
    def _validate_required_strings(cls, value: str, info) -> str:
        return require_non_empty_string(value, info.field_name)

    @field_validator("actor_type", mode="after")
    @classmethod
    def _validate_actor_type(cls, value: str) -> str:
        return require_non_empty_string(value, "actor_type")


class GateDecisionResult(StrictModel):
    """Result of a processed gate decision.

    Result references (result_gate_id, result_command_id,
    result_revision_id) are backend-owned and never supplied by
    the frontend/chatbot.
    """

    decision_id: NonEmptyString
    gate_id: NonEmptyString
    job_id: NonEmptyString
    action: GateDecision
    idempotency_key: NonEmptyString
    result_gate_id: str | None = None
    result_command_id: str | None = None
    result_revision_id: str | None = None
    decided_at: str = ""

    @field_validator("action", mode="before")
    @classmethod
    def _coerce_action(cls, value) -> GateDecision:
        if isinstance(value, GateDecision):
            return value
        return GateDecision(value)
