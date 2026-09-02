"""F1-T2 User decision contract — defines safe user decisions that bind to
checkpoint artifacts without accepting execution authority from the user or
chatbot.

The user decision contract is the unifying schema for all user-facing
decisions at checkpoints. It bridges the gap between the user's expressed
intent (via chatbot or frontend) and the backend-owned gate/resolution
mechanisms.

This contract bridges:
  - ``PhaseGate`` — the stop/wait mechanism that gates govern
  - ``GateDecision`` — the typed decisions the backend processes
  - ``AnalysisCheckpoint`` / ``PlanningCheckpoint`` — the checkpoint
    state models
  - ``ResumeRequest`` — the resume contract for checkpoint continuation

Design invariants:
  - Never exposes sandbox_path, argv, env, raw commands, provider,
    deployment, endpoint, or secret fields.
  - Decisions are user-visible intents — the backend owns resolution,
    stage progression, and gate transitions.
  - Required fields bind to checkpoint artifacts via IDs and checksums,
    never raw filesystem paths.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator, model_validator

from .common import NonEmptyString, StrictModel, require_non_empty_string


# ── User decision enum ─────────────────────────────────────────────────


class UserDecision(str, Enum):
    """All possible user decisions at any governed checkpoint.

    These are user-visible intents. The backend maps each to the
    appropriate GateDecision or system action:

      - CONTINUE → GateDecision.CONTINUE (proceed to next stage)
      - STOP → terminal stop (no further action)
      - REQUEST_ANALYSIS_MODIFICATION → GateDecision.REANALYZE
      - REQUEST_PLANNING_MODIFICATION → GateDecision.REVISE
      - DOWNLOAD_ARTIFACT → read-only artifact access (no gate change)
      - RESUME → GateDecision.CONTINUE (backend resolves next stage
        from stored checkpoint state)
    """

    CONTINUE = "continue"
    STOP = "stop"
    REQUEST_ANALYSIS_MODIFICATION = "request_analysis_modification"
    REQUEST_PLANNING_MODIFICATION = "request_planning_modification"
    DOWNLOAD_ARTIFACT = "download_artifact"
    RESUME = "resume"


# Terminal decisions: once made, no further user action is possible.
TERMINAL_USER_DECISIONS: frozenset[UserDecision] = frozenset({
    UserDecision.STOP,
    UserDecision.CONTINUE,
})

# Read-only decisions: do not change gate state or checkpoint outcome.
READ_ONLY_USER_DECISIONS: frozenset[UserDecision] = frozenset({
    UserDecision.DOWNLOAD_ARTIFACT,
})

# Modification decisions: request a re-analysis or re-planning iteration.
MODIFICATION_USER_DECISIONS: frozenset[UserDecision] = frozenset({
    UserDecision.REQUEST_ANALYSIS_MODIFICATION,
    UserDecision.REQUEST_PLANNING_MODIFICATION,
})

# Decisions that require a reason or comment text.
DECISIONS_REQUIRING_REASON: frozenset[UserDecision] = frozenset({
    UserDecision.STOP,
    UserDecision.REQUEST_ANALYSIS_MODIFICATION,
    UserDecision.REQUEST_PLANNING_MODIFICATION,
})


# ── Mapping: user decision → gate decision ─────────────────────────────


# Maps each user decision to the corresponding GateDecision (if any).
# Read-only decisions (DOWNLOAD_ARTIFACT) and terminal STOP have no
# gate-level counterpart — they are system-level actions.
USER_DECISION_TO_GATE_DECISION: dict[UserDecision, str | None] = {
    UserDecision.CONTINUE: "continue",
    UserDecision.STOP: None,
    UserDecision.REQUEST_ANALYSIS_MODIFICATION: "reanalyze",
    UserDecision.REQUEST_PLANNING_MODIFICATION: "revise",
    UserDecision.DOWNLOAD_ARTIFACT: None,
    UserDecision.RESUME: "continue",
}

# Inverse mapping: gate decision → user decision(s).
# A gate decision may map to multiple user decisions.
GATE_DECISION_TO_USER_DECISIONS: dict[str, frozenset[UserDecision]] = {
    "continue": frozenset({UserDecision.CONTINUE, UserDecision.RESUME}),
    "reanalyze": frozenset({UserDecision.REQUEST_ANALYSIS_MODIFICATION}),
    "revise": frozenset({UserDecision.REQUEST_PLANNING_MODIFICATION}),
    "approve": frozenset(),
    "reject": frozenset(),
    "pending": frozenset(),
}


# ── User decision outcome ──────────────────────────────────────────────


class UserDecisionOutcome(str, Enum):
    """Backend-resolved outcome after processing a user decision.

    These outcomes are owned by the backend — the user never selects
    an outcome directly. The backend resolves the outcome based on
    the decision, checkpoint state, and gate validity.
    """

    DECISION_ACCEPTED = "decision_accepted"
    DECISION_REJECTED = "decision_rejected"
    DECISION_STALE = "decision_stale"
    DECISION_IDEMPOTENT = "decision_idempotent"
    DECISION_TERMINAL = "decision_terminal"


# Successful outcomes: the decision was processed and applied.
SUCCESSFUL_USER_DECISION_OUTCOMES: frozenset[UserDecisionOutcome] = frozenset({
    UserDecisionOutcome.DECISION_ACCEPTED,
    UserDecisionOutcome.DECISION_IDEMPOTENT,
})


# ── User decision rejection codes ──────────────────────────────────────


class UserDecisionRejectionCode(str, Enum):
    """Why a user decision was rejected by the backend."""

    CHECKPOINT_NOT_FOUND = "checkpoint_not_found"
    CHECKPOINT_ALREADY_RESOLVED = "checkpoint_already_resolved"
    CHECKPOINT_STALE = "checkpoint_stale"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    INVALID_DECISION = "invalid_decision"
    INVALID_REVISION = "invalid_revision"
    MISSING_REASON = "missing_reason"
    UNAUTHORIZED = "unauthorized"
    BACKEND_FAILURE = "backend_failure"
    FORBIDDEN_FIELD_PRESENT = "forbidden_field_present"


# ── Safe user decision fields ──────────────────────────────────────────


# Fields that are safe to include in user decision requests and responses.
# These are decision-specific fields.
USER_DECISION_FIELDS: frozenset[str] = frozenset({
    "decision_id",
    "checkpoint_id",
    "job_id",
    "revision_id",
    "checksum",
    "decision",
    "reason",
    "comment_text",
    "outcome",
    "gate_decision",
    "rejection_code",
    "message",
    "next_stage",
    "idempotency_key",
    "correlation_id",
    "causation_id",
    "decided_at",
    "decided_by",
    "created_at",
})

# Verify there is zero overlap with forbidden/dangerous fields.
assert USER_DECISION_FIELDS.isdisjoint({
    "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
    "provider", "model", "deployment", "endpoint", "secret", "token",
    "password", "api_key", "client_secret", "command",
}), "USER_DECISION_FIELDS must not contain forbidden/dangerous fields"


# ── User decision request ──────────────────────────────────────────────


class UserDecisionRequest(StrictModel):
    """Backend-validated user decision request.

    This is the input contract for user decisions at any checkpoint.
    The chatbot or frontend expresses the user's intent; the backend
    validates the request against the current checkpoint state and
    processes the decision.

    Rejected fields (never accepted):
      - sandbox_path, argv, env, raw_command, filesystem_target,
        provider, endpoint, deployment, env refs — these are
        backend-owned and must never be user-supplied.
    """

    checkpoint_id: NonEmptyString
    job_id: NonEmptyString
    revision_id: NonEmptyString
    checksum: NonEmptyString
    decision: UserDecision
    reason: str = ""
    comment_text: str = Field(
        default="",
        max_length=2000,
        description="User comment or modification request detail"
    )
    idempotency_key: NonEmptyString

    # ── correlation / causation ────────────────────────────────────
    correlation_id: str | None = None
    causation_id: str | None = None

    @field_validator("decision", mode="before")
    @classmethod
    def _coerce_decision(cls, value) -> UserDecision:
        if isinstance(value, UserDecision):
            return value
        return UserDecision(value)

    @field_validator(
        "checkpoint_id", "job_id", "revision_id",
        "checksum", "idempotency_key", mode="after",
    )
    @classmethod
    def _validate_required_strings(cls, value: str, info) -> str:
        return require_non_empty_string(value, info.field_name)

    @model_validator(mode="after")
    def _modification_requires_comment(self) -> "UserDecisionRequest":
        if self.decision in MODIFICATION_USER_DECISIONS:
            if not self.comment_text.strip():
                raise ValueError(
                    f"Decision '{self.decision.value}' requires "
                    "comment_text explaining what should be changed"
                )
        return self

    @model_validator(mode="after")
    def _stop_requires_reason(self) -> "UserDecisionRequest":
        if self.decision in DECISIONS_REQUIRING_REASON:
            if not self.reason.strip() and not self.comment_text.strip():
                raise ValueError(
                    f"Decision '{self.decision.value}' requires "
                    "a reason or comment_text"
                )
        return self

    @property
    def is_terminal(self) -> bool:
        """True when this decision terminates further checkpoint actions."""
        return self.decision in TERMINAL_USER_DECISIONS

    @property
    def is_read_only(self) -> bool:
        """True when this decision does not change checkpoint state."""
        return self.decision in READ_ONLY_USER_DECISIONS

    @property
    def is_modification(self) -> bool:
        """True when this decision requests a modification iteration."""
        return self.decision in MODIFICATION_USER_DECISIONS

    @property
    def gate_decision(self) -> str | None:
        """The GateDecision this user decision maps to, if any."""
        return USER_DECISION_TO_GATE_DECISION.get(self.decision)


# ── User decision response ─────────────────────────────────────────────


class UserDecisionResponse(StrictModel):
    """Backend-owned result of a processed user decision.

    All result fields are backend-owned. The frontend/chatbot reads
    this response to inform the user; it never mutates it.

    Idempotency: duplicate (idempotency_key, checksum) requests
    return the same response. A different checksum under the same
    key is rejected.
    """

    decision_id: NonEmptyString
    checkpoint_id: NonEmptyString
    job_id: NonEmptyString
    decision: UserDecision
    outcome: UserDecisionOutcome
    gate_decision: str | None = None
    rejection_code: UserDecisionRejectionCode | None = None
    message: str = ""
    next_stage: str | None = Field(
        default=None,
        description="Resolved next stage (backend-owned, never user-supplied)"
    )

    # ── idempotency tracking ───────────────────────────────────────
    idempotency_key: NonEmptyString

    # ── timestamps ─────────────────────────────────────────────────
    decided_at: str = ""
    decided_by: str = ""

    # ── correlation ────────────────────────────────────────────────
    correlation_id: str | None = None
    causation_id: str | None = None

    @field_validator("decision", mode="before")
    @classmethod
    def _coerce_decision(cls, value) -> UserDecision:
        if isinstance(value, UserDecision):
            return value
        return UserDecision(value)

    @field_validator("outcome", mode="before")
    @classmethod
    def _coerce_outcome(cls, value) -> UserDecisionOutcome:
        if isinstance(value, UserDecisionOutcome):
            return value
        return UserDecisionOutcome(value)

    @field_validator("rejection_code", mode="before")
    @classmethod
    def _coerce_rejection_code(cls, value) -> UserDecisionRejectionCode | None:
        if value is None:
            return None
        if isinstance(value, UserDecisionRejectionCode):
            return value
        return UserDecisionRejectionCode(value)

    @field_validator(
        "decision_id", "checkpoint_id", "job_id",
        "idempotency_key", mode="after",
    )
    @classmethod
    def _validate_required_strings(cls, value: str, info) -> str:
        return require_non_empty_string(value, info.field_name)

    @model_validator(mode="after")
    def _rejected_must_have_rejection_code(self) -> "UserDecisionResponse":
        if self.outcome == UserDecisionOutcome.DECISION_REJECTED:
            if self.rejection_code is None:
                raise ValueError(
                    "A rejected decision must have a rejection_code"
                )
        return self

    @model_validator(mode="after")
    def _accepted_must_not_have_rejection_code(self) -> "UserDecisionResponse":
        if self.outcome in SUCCESSFUL_USER_DECISION_OUTCOMES:
            if self.rejection_code is not None:
                raise ValueError(
                    "A successful decision must not have a rejection_code"
                )
        return self

    # ── factory methods ────────────────────────────────────────────

    @classmethod
    def idempotent(
        cls,
        decision_id: str,
        checkpoint_id: str,
        job_id: str,
        decision: UserDecision,
        idempotency_key: str,
        existing_outcome: UserDecisionOutcome,
        existing_gate_decision: str | None = None,
        message: str = "",
        next_stage: str | None = None,
        decided_at: str = "",
        decided_by: str = "",
        correlation_id: str | None = None,
    ) -> "UserDecisionResponse":
        """Return an idempotent response for a duplicate decision."""
        return cls(
            decision_id=decision_id,
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            decision=decision,
            outcome=UserDecisionOutcome.DECISION_IDEMPOTENT,
            gate_decision=existing_gate_decision,
            message=message or "Duplicate decision — existing result returned",
            next_stage=next_stage,
            idempotency_key=idempotency_key,
            decided_at=decided_at,
            decided_by=decided_by,
            correlation_id=correlation_id,
        )

    @classmethod
    def accepted(
        cls,
        decision_id: str,
        checkpoint_id: str,
        job_id: str,
        decision: UserDecision,
        idempotency_key: str,
        gate_decision: str | None = None,
        message: str = "",
        next_stage: str | None = None,
        decided_at: str = "",
        decided_by: str = "",
        correlation_id: str | None = None,
    ) -> "UserDecisionResponse":
        """Return an accepted decision result."""
        return cls(
            decision_id=decision_id,
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            decision=decision,
            outcome=UserDecisionOutcome.DECISION_ACCEPTED,
            gate_decision=gate_decision,
            message=message or f"Decision '{decision.value}' accepted",
            next_stage=next_stage,
            idempotency_key=idempotency_key,
            decided_at=decided_at,
            decided_by=decided_by,
            correlation_id=correlation_id,
        )

    @classmethod
    def rejected(
        cls,
        decision_id: str,
        checkpoint_id: str,
        job_id: str,
        decision: UserDecision,
        idempotency_key: str,
        rejection_code: UserDecisionRejectionCode,
        message: str = "",
        decided_at: str = "",
        decided_by: str = "",
        correlation_id: str | None = None,
    ) -> "UserDecisionResponse":
        """Return a rejected decision result."""
        return cls(
            decision_id=decision_id,
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            decision=decision,
            outcome=UserDecisionOutcome.DECISION_REJECTED,
            rejection_code=rejection_code,
            message=message or f"Decision rejected: {rejection_code.value}",
            idempotency_key=idempotency_key,
            decided_at=decided_at,
            decided_by=decided_by,
            correlation_id=correlation_id,
        )

    # ── from_dict ──────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict) -> "UserDecisionResponse":
        """Build a UserDecisionResponse from a raw dictionary.

        Guards against None → \"None\" string conversion. Provides
        sensible fallback values for optional fields.
        """
        decision_id: str = data.get("decision_id") or "unknown"
        checkpoint_id: str = data.get("checkpoint_id") or "unknown"
        job_id: str = data.get("job_id") or "unknown"
        idempotency_key: str = data.get("idempotency_key") or "unknown"

        # Decision coercion
        decision_raw = data.get("decision", "")
        decision: UserDecision
        if isinstance(decision_raw, UserDecision):
            decision = decision_raw
        else:
            try:
                decision = UserDecision(decision_raw)
            except (ValueError, TypeError):
                decision = UserDecision.CONTINUE

        # Outcome coercion
        outcome_raw = data.get("outcome", "")
        outcome: UserDecisionOutcome
        if isinstance(outcome_raw, UserDecisionOutcome):
            outcome = outcome_raw
        else:
            try:
                outcome = UserDecisionOutcome(outcome_raw)
            except (ValueError, TypeError):
                outcome = UserDecisionOutcome.DECISION_REJECTED

        # Rejection code coercion with fallback
        rejection_code: UserDecisionRejectionCode | None = None
        rej_raw = data.get("rejection_code")
        if rej_raw is not None:
            if isinstance(rej_raw, UserDecisionRejectionCode):
                rejection_code = rej_raw
            else:
                try:
                    rejection_code = UserDecisionRejectionCode(rej_raw)
                except (ValueError, TypeError):
                    rejection_code = None

        # Fallback: if outcome is rejected but rejection_code is None,
        # default to BACKEND_FAILURE.
        if outcome == UserDecisionOutcome.DECISION_REJECTED and rejection_code is None:
            rejection_code = UserDecisionRejectionCode.BACKEND_FAILURE

        # Nil-guarded string fields
        gate_decision_val = data.get("gate_decision")
        gate_decision: str | None = (
            gate_decision_val if gate_decision_val is not None else None
        )
        message: str = data.get("message") or ""
        next_stage_val = data.get("next_stage")
        next_stage: str | None = (
            next_stage_val if next_stage_val is not None else None
        )
        decided_at: str = data.get("decided_at") or ""
        decided_by: str = data.get("decided_by") or ""
        correlation_id_val = data.get("correlation_id")
        correlation_id: str | None = (
            correlation_id_val if correlation_id_val is not None else None
        )
        causation_id_val = data.get("causation_id")
        causation_id: str | None = (
            causation_id_val if causation_id_val is not None else None
        )

        return cls(
            decision_id=decision_id,
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            decision=decision,
            outcome=outcome,
            gate_decision=gate_decision,
            rejection_code=rejection_code,
            message=message,
            next_stage=next_stage,
            idempotency_key=idempotency_key,
            decided_at=decided_at,
            decided_by=decided_by,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    # ── derived properties ─────────────────────────────────────────

    @property
    def is_successful(self) -> bool:
        """True when the decision was accepted or idempotent."""
        return self.outcome in SUCCESSFUL_USER_DECISION_OUTCOMES

    @property
    def is_rejected(self) -> bool:
        """True when the decision was rejected."""
        return self.outcome == UserDecisionOutcome.DECISION_REJECTED

    @property
    def is_terminal_outcome(self) -> bool:
        """True when the decision outcome terminates further actions."""
        return self.outcome == UserDecisionOutcome.DECISION_TERMINAL


# ── Validation helpers ─────────────────────────────────────────────────


def is_valid_decision(decision_str: str) -> bool:
    """Return True if *decision_str* is a valid UserDecision value."""
    try:
        UserDecision(decision_str)
        return True
    except ValueError:
        return False


def is_valid_outcome(outcome_str: str) -> bool:
    """Return True if *outcome_str* is a valid UserDecisionOutcome value."""
    try:
        UserDecisionOutcome(outcome_str)
        return True
    except ValueError:
        return False


def is_valid_rejection_code(code_str: str) -> bool:
    """Return True if *code_str* is a valid UserDecisionRejectionCode."""
    try:
        UserDecisionRejectionCode(code_str)
        return True
    except ValueError:
        return False


def is_terminal_decision(decision: UserDecision) -> bool:
    """Return True when *decision* terminates further checkpoint actions."""
    return decision in TERMINAL_USER_DECISIONS


def is_modification_decision(decision: UserDecision) -> bool:
    """Return True when *decision* requests a modification iteration."""
    return decision in MODIFICATION_USER_DECISIONS


def is_read_only_decision(decision: UserDecision) -> bool:
    """Return True when *decision* does not change checkpoint state."""
    return decision in READ_ONLY_USER_DECISIONS


def get_gate_decision(user_decision: UserDecision) -> str | None:
    """Return the GateDecision mapped from *user_decision*, or None."""
    return USER_DECISION_TO_GATE_DECISION.get(user_decision)


def validate_user_decision_fields(data: dict) -> tuple[bool, list[str]]:
    """Validate that *data* contains no forbidden/dangerous fields.

    Returns (is_valid, list_of_forbidden_fields_found).
    """
    forbidden = {
        "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
        "provider", "model", "deployment", "endpoint", "secret", "token",
        "password", "api_key", "client_secret", "command",
    }
    found = [k for k in data if k in forbidden]
    return len(found) == 0, found
