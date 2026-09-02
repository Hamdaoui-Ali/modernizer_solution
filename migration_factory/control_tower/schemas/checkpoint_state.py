"""F1-T1 Checkpoint state model — defines checkpoint statuses, transitions,
and base binding so Analysis and Planning can stop safely for user decisions.

The checkpoint state model is the foundational contract that all specific
checkpoint types (analysis, planning, etc.) build upon. It defines the
generic lifecycle states, allowed transitions, terminal states, and the
minimum fields required for any governed checkpoint.

This contract bridges:
  - ``PhaseGate`` — the stop/wait mechanism (GateStatus, GateDecision)
  - ``ArtifactRevision`` — versioned evidence (ArtifactRevisionStatus)
  - ``AnalysisCheckpoint`` / ``PlanningCheckpoint`` — phase-specific
    specializations
  - ``UserDecision`` / ``UserDecisionRequest`` — user-facing decision flow
  - ``ResumeRequest`` — checkpoint continuation

Design invariants:
  - Never exposes sandbox_path, argv, env, raw commands, provider,
    deployment, endpoint, or secret fields.
  - Terminal states are immutable — once reached, no further state
    transition is allowed.
  - All transitions are backend-validated; frontend/chatbot express
    intent only.
  - Idempotent retry behavior is defined for all non-terminal states.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator, model_validator

from .common import NonEmptyString, StrictModel, require_non_empty_string


# ── Checkpoint status enum ──────────────────────────────────────────────


class CheckpointStatus(str, Enum):
    """Generic lifecycle status for any governed checkpoint.

    These states apply to analysis, planning, approval, and repair
    checkpoints. Phase-specific specializations (AnalysisOutcome,
    PlanningOutcome) map back to these base states.
    """

    WAITING = "waiting"
    """Checkpoint is open and waiting for a user decision."""

    ACCEPTED = "accepted"
    """User accepted the artifact — gate resolves, pipeline proceeds."""

    CHANGES_REQUESTED = "changes_requested"
    """User requested modifications — re-analysis or re-planning triggered."""

    REJECTED = "rejected"
    """User rejected the artifact — gate resolves, pipeline stops."""

    STOPPED = "stopped"
    """User stopped the pipeline — terminal, no further action."""

    STALE = "stale"
    """Artifact checksum no longer matches — needs regeneration."""

    FAILED_CLOSED = "failed_closed"
    """System-level failure — terminal, fail-safe stop."""


# ── Terminal states ──────────────────────────────────────────────────────


# Terminal states: once reached, no further user action or state
# transition is possible by any actor.
TERMINAL_CHECKPOINT_STATUSES: frozenset[CheckpointStatus] = frozenset({
    CheckpointStatus.ACCEPTED,
    CheckpointStatus.REJECTED,
    CheckpointStatus.STOPPED,
    CheckpointStatus.FAILED_CLOSED,
})

# Non-terminal states: the checkpoint may still receive user decisions.
NONTERMINAL_CHECKPOINT_STATUSES: frozenset[CheckpointStatus] = frozenset({
    CheckpointStatus.WAITING,
    CheckpointStatus.CHANGES_REQUESTED,
    CheckpointStatus.STALE,
})

# Successful terminal states: pipeline may proceed after these.
SUCCESSFUL_TERMINAL_STATUSES: frozenset[CheckpointStatus] = frozenset({
    CheckpointStatus.ACCEPTED,
    CheckpointStatus.CHANGES_REQUESTED,
})

# Failed terminal states: pipeline must stop after these.
FAILED_TERMINAL_STATUSES: frozenset[CheckpointStatus] = frozenset({
    CheckpointStatus.REJECTED,
    CheckpointStatus.STOPPED,
    CheckpointStatus.FAILED_CLOSED,
})


# ── Allowed state transitions ────────────────────────────────────────────


# Maps each status to the set of statuses it may transition to.
# Immutable terminal states have empty transition sets.
CHECKPOINT_TRANSITIONS: dict[CheckpointStatus, frozenset[CheckpointStatus]] = {
    CheckpointStatus.WAITING: frozenset({
        CheckpointStatus.ACCEPTED,
        CheckpointStatus.CHANGES_REQUESTED,
        CheckpointStatus.REJECTED,
        CheckpointStatus.STOPPED,
        CheckpointStatus.STALE,
        CheckpointStatus.FAILED_CLOSED,
    }),
    CheckpointStatus.CHANGES_REQUESTED: frozenset({
        CheckpointStatus.WAITING,  # After re-analysis/re-planning completes
        CheckpointStatus.STOPPED,
        CheckpointStatus.STALE,
        CheckpointStatus.FAILED_CLOSED,
    }),
    CheckpointStatus.STALE: frozenset({
        CheckpointStatus.WAITING,  # After artifact regeneration
        CheckpointStatus.FAILED_CLOSED,
    }),
    # Terminal states — no transitions allowed
    CheckpointStatus.ACCEPTED: frozenset(),
    CheckpointStatus.REJECTED: frozenset(),
    CheckpointStatus.STOPPED: frozenset(),
    CheckpointStatus.FAILED_CLOSED: frozenset(),
}


# ── Mapping: CheckpointStatus ↔ PhaseGate concepts ───────────────────────


# Maps each checkpoint status to the corresponding GateStatus.
#   WAITING/CHANGES_REQUESTED/STALE → OPEN (gate is still active)
#   ACCEPTED/REJECTED/STOPPED → RESOLVED (gate has been decided)
#   FAILED_CLOSED → RESOLVED (system-resolved fail-safe)
CHECKPOINT_STATUS_TO_GATE_STATUS: dict[CheckpointStatus, str] = {
    CheckpointStatus.WAITING: "open",
    CheckpointStatus.CHANGES_REQUESTED: "open",
    CheckpointStatus.STALE: "open",
    CheckpointStatus.ACCEPTED: "resolved",
    CheckpointStatus.REJECTED: "resolved",
    CheckpointStatus.STOPPED: "resolved",
    CheckpointStatus.FAILED_CLOSED: "resolved",
}

# Maps each checkpoint status to the corresponding GateDecision.
CHECKPOINT_STATUS_TO_GATE_DECISION: dict[CheckpointStatus, str | None] = {
    CheckpointStatus.WAITING: "pending",
    CheckpointStatus.CHANGES_REQUESTED: "pending",
    CheckpointStatus.STALE: "pending",
    CheckpointStatus.ACCEPTED: "continue",
    CheckpointStatus.REJECTED: "reject",
    CheckpointStatus.STOPPED: "reject",
    CheckpointStatus.FAILED_CLOSED: "reject",
}


# ── Mapping: CheckpointStatus ↔ ArtifactRevision concepts ────────────────


# Maps each checkpoint status to the corresponding ArtifactRevisionStatus.
CHECKPOINT_STATUS_TO_REVISION_STATUS: dict[CheckpointStatus, str] = {
    CheckpointStatus.WAITING: "draft",
    CheckpointStatus.CHANGES_REQUESTED: "draft",
    CheckpointStatus.STALE: "draft",
    CheckpointStatus.ACCEPTED: "accepted",
    CheckpointStatus.REJECTED: "accepted",
    CheckpointStatus.STOPPED: "superseded",
    CheckpointStatus.FAILED_CLOSED: "superseded",
}


# ── Checkpoint state model ───────────────────────────────────────────────


class CheckpointState(StrictModel):
    """Base state model for any governed checkpoint.

    All phase-specific checkpoints (analysis, planning, approval,
    repair) specialize this base model. It defines the minimum fields
    required for any checkpoint and enforces status transition rules.

    Design invariants:
      - checkpoint_id + job_id + stage_index form a unique identity.
      - artifact_refs and checksums bind the checkpoint to exact
        artifact content — preventing decisions on stale evidence.
      - profile_metadata captures the routing context in effect when
        the checkpoint was created.
      - Terminal states are immutable — no further transitions allowed.
      - Idempotent retries for the same (checkpoint_id, idempotency_key)
        return the existing state without mutation.
    """

    checkpoint_id: NonEmptyString
    job_id: NonEmptyString
    stage_index: int = Field(ge=1, le=3)
    status: CheckpointStatus = CheckpointStatus.WAITING

    # ── identity binding ────────────────────────────────────────────
    # Gate ID that this checkpoint wraps (PhaseGate.gate_id).
    gate_id: str = ""
    # Revision ID of the artifact under review (ArtifactRevision.revision_id).
    revision_id: str = ""

    # ── checksum binding ────────────────────────────────────────────
    # Checksum of the source artifact at checkpoint creation time.
    # Ties decisions to exact artifact content.
    source_artifact_checksum: str = ""
    # Checksum of the resolved artifact (set when status becomes terminal).
    resolved_artifact_checksum: str | None = None

    # ── artifact references ─────────────────────────────────────────
    # Ordered tuple of artifact IDs this checkpoint reviews.
    artifact_refs: tuple[str, ...] = Field(default_factory=tuple)

    # ── profile context ─────────────────────────────────────────────
    # Source and target migration profiles in effect at checkpoint
    # creation. None when profile routing is not configured.
    source_profile: str | None = None
    target_profile: str | None = None

    # ── stale tracking ──────────────────────────────────────────────
    is_stale: bool = False
    stale_reason: str = ""

    # ── timestamps & actor ──────────────────────────────────────────
    created_at: str
    created_by: str = ""
    resolved_at: str | None = None
    resolved_by: str | None = None

    # ── idempotency ─────────────────────────────────────────────────
    # Client-provided key for idempotent retry. Same key + same
    # checkpoint_id returns existing state without mutation.
    last_idempotency_key: str | None = None

    # ── validation ──────────────────────────────────────────────────

    @field_validator("checkpoint_id", "job_id", "created_at", mode="after")
    @classmethod
    def _validate_required_strings(cls, value: str, info) -> str:
        return require_non_empty_string(value, info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, value) -> CheckpointStatus:
        if isinstance(value, CheckpointStatus):
            return value
        return CheckpointStatus(value)

    @model_validator(mode="after")
    def _terminal_must_have_resolved_fields(self) -> "CheckpointState":
        if self.status in TERMINAL_CHECKPOINT_STATUSES:
            if not self.resolved_at or not self.resolved_by:
                raise ValueError(
                    f"A terminal checkpoint ({self.status.value}) must "
                    "set resolved_at and resolved_by"
                )
        return self

    @model_validator(mode="after")
    def _nonterminal_must_not_have_resolved_fields(self) -> "CheckpointState":
        if self.status in NONTERMINAL_CHECKPOINT_STATUSES:
            if self.resolved_at is not None or self.resolved_by is not None:
                raise ValueError(
                    f"A non-terminal checkpoint ({self.status.value}) must "
                    "not have resolved_at or resolved_by set"
                )
        return self

    @model_validator(mode="after")
    def _stale_must_have_reason(self) -> "CheckpointState":
        if self.is_stale and not self.stale_reason:
            raise ValueError(
                "A stale checkpoint must provide a stale_reason"
            )
        return self

    # ── derived properties ──────────────────────────────────────────

    @property
    def is_terminal(self) -> bool:
        """True when the checkpoint has reached a terminal status."""
        return self.status in TERMINAL_CHECKPOINT_STATUSES

    @property
    def is_waiting(self) -> bool:
        """True when the checkpoint is waiting for a user decision."""
        return self.status == CheckpointStatus.WAITING

    @property
    def is_nonterminal(self) -> bool:
        """True when the checkpoint may still receive user decisions."""
        return self.status in NONTERMINAL_CHECKPOINT_STATUSES

    @property
    def has_artifacts(self) -> bool:
        """True when artifact refs are bound to this checkpoint."""
        return len(self.artifact_refs) > 0

    @property
    def gate_status(self) -> str:
        """The GateStatus this checkpoint maps to."""
        return CHECKPOINT_STATUS_TO_GATE_STATUS[self.status]

    @property
    def gate_decision(self) -> str | None:
        """The GateDecision this checkpoint maps to."""
        return CHECKPOINT_STATUS_TO_GATE_DECISION[self.status]

    @property
    def revision_status(self) -> str:
        """The ArtifactRevisionStatus this checkpoint maps to."""
        return CHECKPOINT_STATUS_TO_REVISION_STATUS[self.status]

    @property
    def is_successful_terminal(self) -> bool:
        """True when terminal and the pipeline may proceed."""
        return self.status in SUCCESSFUL_TERMINAL_STATUSES

    @property
    def is_failed_terminal(self) -> bool:
        """True when terminal and the pipeline must stop."""
        return self.status in FAILED_TERMINAL_STATUSES

    # ── factory methods ─────────────────────────────────────────────

    @classmethod
    def create_waiting(
        cls,
        checkpoint_id: str,
        job_id: str,
        stage_index: int,
        gate_id: str = "",
        revision_id: str = "",
        source_artifact_checksum: str = "",
        artifact_refs: tuple[str, ...] = (),
        source_profile: str | None = None,
        target_profile: str | None = None,
        created_at: str = "",
        created_by: str = "",
    ) -> "CheckpointState":
        """Create a new checkpoint in WAITING status."""
        return cls(
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            stage_index=stage_index,
            status=CheckpointStatus.WAITING,
            gate_id=gate_id,
            revision_id=revision_id,
            source_artifact_checksum=source_artifact_checksum,
            artifact_refs=artifact_refs,
            source_profile=source_profile,
            target_profile=target_profile,
            created_at=created_at,
            created_by=created_by,
        )

    @classmethod
    def create_terminal(
        cls,
        checkpoint_id: str,
        job_id: str,
        stage_index: int,
        status: CheckpointStatus,
        gate_id: str = "",
        revision_id: str = "",
        source_artifact_checksum: str = "",
        resolved_artifact_checksum: str | None = None,
        artifact_refs: tuple[str, ...] = (),
        source_profile: str | None = None,
        target_profile: str | None = None,
        created_at: str = "",
        created_by: str = "",
        resolved_at: str = "",
        resolved_by: str = "",
    ) -> "CheckpointState":
        """Create a checkpoint already in a terminal status."""
        if status not in TERMINAL_CHECKPOINT_STATUSES:
            raise ValueError(
                f"create_terminal requires a terminal status, got "
                f"'{status.value}'"
            )
        return cls(
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            stage_index=stage_index,
            status=status,
            gate_id=gate_id,
            revision_id=revision_id,
            source_artifact_checksum=source_artifact_checksum,
            resolved_artifact_checksum=resolved_artifact_checksum,
            artifact_refs=artifact_refs,
            source_profile=source_profile,
            target_profile=target_profile,
            created_at=created_at,
            created_by=created_by,
            resolved_at=resolved_at,
            resolved_by=resolved_by,
        )


# ── Transition validation ────────────────────────────────────────────────


def is_valid_transition(
    from_status: CheckpointStatus,
    to_status: CheckpointStatus,
) -> bool:
    """Return True if transitioning from *from_status* to *to_status*
    is allowed by the checkpoint transition table.
    """
    allowed = CHECKPOINT_TRANSITIONS.get(from_status, frozenset())
    return to_status in allowed


def get_allowed_transitions(
    status: CheckpointStatus,
) -> frozenset[CheckpointStatus]:
    """Return the set of statuses *status* may transition to."""
    return CHECKPOINT_TRANSITIONS.get(status, frozenset())


def assert_valid_transition(
    checkpoint: CheckpointState,
    new_status: CheckpointStatus,
) -> None:
    """Raise ValueError if *new_status* is not a valid transition from
    the checkpoint's current status.
    """
    if checkpoint.is_terminal:
        raise ValueError(
            f"Cannot transition terminal checkpoint "
            f"'{checkpoint.checkpoint_id}' from "
            f"'{checkpoint.status.value}'"
        )
    if not is_valid_transition(checkpoint.status, new_status):
        raise ValueError(
            f"Cannot transition from '{checkpoint.status.value}' to "
            f"'{new_status.value}' — transition not allowed"
        )


def transition(
    checkpoint: CheckpointState,
    new_status: CheckpointStatus,
    resolved_at: str = "",
    resolved_by: str = "",
    resolved_artifact_checksum: str | None = None,
    idempotency_key: str | None = None,
) -> CheckpointState:
    """Return a new CheckpointState with *new_status* applied.

    Validates the transition and sets terminal fields when appropriate.
    Returns a new frozen instance — the original is unchanged.

    Idempotency: if the checkpoint is already at *new_status*, returns
    the original checkpoint unchanged.
    """
    # Idempotent: already at target status
    if checkpoint.status == new_status:
        return checkpoint

    assert_valid_transition(checkpoint, new_status)

    update: dict = {
        "status": new_status,
        "last_idempotency_key": idempotency_key,
    }

    if new_status in TERMINAL_CHECKPOINT_STATUSES:
        update["resolved_at"] = resolved_at
        update["resolved_by"] = resolved_by
        if resolved_artifact_checksum is not None:
            update["resolved_artifact_checksum"] = resolved_artifact_checksum

    return checkpoint.model_copy(update=update)


# ── Idempotent retry behavior ────────────────────────────────────────────


def is_idempotent_retry(
    checkpoint: CheckpointState,
    idempotency_key: str,
) -> bool:
    """Return True if *idempotency_key* matches the checkpoint's
    last_idempotency_key, indicating a duplicate request that should
    return the existing state.
    """
    return (
        idempotency_key is not None
        and checkpoint.last_idempotency_key is not None
        and checkpoint.last_idempotency_key == idempotency_key
    )


# ── Status validation helpers ────────────────────────────────────────────


def is_valid_checkpoint_status(status_str: str) -> bool:
    """Return True if *status_str* is a valid CheckpointStatus value."""
    try:
        CheckpointStatus(status_str)
        return True
    except ValueError:
        return False


def is_terminal_status(status: CheckpointStatus) -> bool:
    """Return True if *status* is a terminal checkpoint status."""
    return status in TERMINAL_CHECKPOINT_STATUSES


def is_nonterminal_status(status: CheckpointStatus) -> bool:
    """Return True if *status* is a non-terminal checkpoint status."""
    return status in NONTERMINAL_CHECKPOINT_STATUSES


# ── Safe checkpoint fields ───────────────────────────────────────────────


# Fields that are safe to include in any checkpoint.
CHECKPOINT_STATE_FIELDS: frozenset[str] = frozenset({
    "checkpoint_id",
    "job_id",
    "stage_index",
    "status",
    "gate_id",
    "revision_id",
    "source_artifact_checksum",
    "resolved_artifact_checksum",
    "artifact_refs",
    "source_profile",
    "target_profile",
    "is_stale",
    "stale_reason",
    "created_at",
    "created_by",
    "resolved_at",
    "resolved_by",
    "last_idempotency_key",
})

# Verify there is zero overlap with forbidden/dangerous fields.
assert CHECKPOINT_STATE_FIELDS.isdisjoint({
    "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
    "provider", "model", "deployment", "endpoint", "secret", "token",
    "password", "api_key", "client_secret", "command",
}), "CHECKPOINT_STATE_FIELDS must not contain forbidden/dangerous fields"
