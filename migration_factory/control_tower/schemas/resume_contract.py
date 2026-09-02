"""F1-T8 Resume contract — defines backend-owned resume behavior that
continues from valid checkpoints without accepting user-supplied execution
details.

The Resume contract bridges:
  - ``PhaseGate`` — the stop/wait mechanism the checkpoint is bound to
  - ``AnalysisCheckpoint`` / ``PlanningCheckpoint`` — the checkpoint models
  - ``ArtifactRevision`` — the evidence co-versioned with the checkpoint
  - ``V2StageProgression`` — backend-owned next-stage resolution
  - ``RunConfiguration`` — policy-driven continuation rules

Design invariants:
  - Never exposes sandbox_path, argv, env, raw commands, provider,
    deployment, or endpoint fields.
  - Resume inputs are checkpoint-scoped: checkpoint ID, artifact refs,
    checksums, decision, and comments. No filesystem targets.
  - The backend owns next-stage resolution — the user never chooses
    the target stage or provides execution parameters.
  - Stale, foreign, incompatible, or terminal checkpoints are rejected
    before any downstream work begins.
  - Resume is idempotent: repeating the same resume request with the
    same idempotency key returns the cached outcome.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from .common import NonEmptyString, StrictModel, require_non_empty_string


# ── Resume outcomes ─────────────────────────────────────────────────────

class ResumeOutcome(str, Enum):
    """Possible outcomes of a resume attempt.

    These are backend-owned — the user makes a request, and the backend
    determines which outcome results after validation.
    """

    RESUMED = "resumed"
    """Checkpoint was valid and the next stage has been started."""

    REJECTED_STALE = "rejected_stale"
    """Artifact checksums do not match — the checkpoint is stale."""

    REJECTED_FOREIGN = "rejected_foreign"
    """The checkpoint belongs to a different job or profile pair."""

    REJECTED_INCOMPATIBLE = "rejected_incompatible"
    """The profile or checkpoint type is incompatible with resume."""

    REJECTED_TERMINAL = "rejected_terminal"
    """The checkpoint has already reached a terminal outcome."""

    IDEMPOTENT = "idempotent"
    """The same resume request was already processed — cached outcome."""

    FAILED_CLOSED = "failed_closed"
    """An unrecoverable backend error prevented resume."""


# Terminal resume outcomes: once reached, no further resume action possible.
_TERMINAL_RESUME_OUTCOMES: frozenset[ResumeOutcome] = frozenset({
    ResumeOutcome.REJECTED_TERMINAL,
    ResumeOutcome.FAILED_CLOSED,
})

# Outcomes that indicate a successful resume.
_SUCCESSFUL_RESUME_OUTCOMES: frozenset[ResumeOutcome] = frozenset({
    ResumeOutcome.RESUMED,
    ResumeOutcome.IDEMPOTENT,
})


def is_terminal_resume(outcome: ResumeOutcome) -> bool:
    """Return True if *outcome* means no further resume is possible."""
    return outcome in _TERMINAL_RESUME_OUTCOMES


def is_successful_resume(outcome: ResumeOutcome) -> bool:
    """Return True if *outcome* means the resume was accepted."""
    return outcome in _SUCCESSFUL_RESUME_OUTCOMES


# ── Rejection detail codes ──────────────────────────────────────────────

class ResumeRejectionCode(str, Enum):
    """Fine-grained rejection reasons for diagnostic use.

    These are machine-readable codes. The user-facing message is derived
    from the code but never exposes internal details (paths, checksums,
    environment values, etc.).
    """

    CHECKSUM_MISMATCH = "checksum_mismatch"
    """One or more required artifact checksums do not match the checkpoint."""

    ARTIFACT_COUNT_MISMATCH = "artifact_count_mismatch"
    """The number of submitted artifact refs differs from the checkpoint."""

    MISSING_REQUIRED_ARTIFACT = "missing_required_artifact"
    """A required artifact type is absent from the submitted refs."""

    FOREIGN_JOB = "foreign_job"
    """The checkpoint belongs to a different job_id."""

    FOREIGN_PROFILE = "foreign_profile"
    """The checkpoint's profile pair differs from the current run."""

    INCOMPATIBLE_CHECKPOINT_TYPE = "incompatible_checkpoint_type"
    """The checkpoint type (e.g. Analysis vs Planning) cannot be resumed."""

    ALREADY_TERMINAL = "already_terminal"
    """The checkpoint outcome is already terminal (ACCEPTED/STOPPED/FAILED_CLOSED)."""

    GATE_NOT_OPEN = "gate_not_open"
    """The backing gate is not in OPEN status — it may be RESOLVED or SUPERSEDED."""

    GATE_NOT_FOUND = "gate_not_found"
    """The backing gate for this checkpoint was not found."""

    INVALID_DECISION = "invalid_decision"
    """The submitted decision is not valid for this checkpoint's current outcome."""

    BACKEND_FAILURE = "backend_failure"
    """An internal backend error prevented the resume."""


# Rejection codes that should prevent any further auto-retry.
_TERMINAL_REJECTION_CODES: frozenset[ResumeRejectionCode] = frozenset({
    ResumeRejectionCode.ALREADY_TERMINAL,
    ResumeRejectionCode.BACKEND_FAILURE,
})


# ── Resume request — what the user submits ──────────────────────────────

class ResumeRequest(StrictModel):
    """A user-initiated request to resume from a checkpoint.

    All input is checkpoint-scoped: checkpoint_id, artifact refs,
    checksums, decision, and optional comments.  The user NEVER supplies
    a target stage, execution parameters, filesystem targets, or
    environment variables — the backend resolves all of those from
    the stored checkpoint state and run configuration.
    """

    checkpoint_id: NonEmptyString
    """The checkpoint to resume from (e.g. acp-... or pcp-...)."""

    job_id: NonEmptyString
    """The migration job this checkpoint belongs to."""

    decision: str = Field(
        default="continue",
        min_length=1,
        max_length=64,
        description="The user's decision (continue, stop, request_modification, etc.)",
    )

    artifact_refs: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=32,
        description="Artifact IDs the user reviewed — must match checkpoint requirements.",
    )

    artifact_checksums: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of artifact_id → sha256:... checksum for binding.",
    )

    comment_text: str = Field(
        default="",
        max_length=2000,
        description="Optional comment (e.g. modification instructions).",
    )

    idempotency_key: NonEmptyString
    """Client-generated key for idempotent resume requests.
    The same (checkpoint_id, idempotency_key) pair MUST return the
    same cached outcome if the resume was already processed."""

    @field_validator("checkpoint_id", "job_id", "idempotency_key", mode="after")
    @classmethod
    def _validate_required_strings(cls, value: str, info) -> str:
        return require_non_empty_string(value, info.field_name)

    @field_validator("decision", mode="after")
    @classmethod
    def _validate_decision(cls, value: str) -> str:
        return require_non_empty_string(value, "decision")

    @model_validator(mode="after")
    def _artifact_refs_and_checksums_must_be_consistent(self) -> "ResumeRequest":
        """Every artifact_ref must have a corresponding checksum entry."""
        for ref in self.artifact_refs:
            if ref not in self.artifact_checksums:
                raise ValueError(
                    f"Artifact ref {ref!r} is missing a checksum entry"
                )
        # Checksums without a corresponding ref are extra but not invalid.
        return self

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a safe dictionary."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "job_id": self.job_id,
            "decision": self.decision,
            "artifact_refs": list(self.artifact_refs),
            "artifact_checksums": dict(self.artifact_checksums),
            "comment_text": self.comment_text,
            "idempotency_key": self.idempotency_key,
        }

    def to_json(self) -> str:
        """Serialize to compact JSON."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResumeRequest":
        """Deserialize from a dictionary.

        Database NULL columns appear as present-but-None keys.
        We guard every extraction with ``is not None`` before casting.
        """
        _checkpoint_id = data.get("checkpoint_id", "")
        checkpoint_id = str(_checkpoint_id) if _checkpoint_id is not None else ""

        _job_id = data.get("job_id", "")
        job_id = str(_job_id) if _job_id is not None else ""

        _decision = data.get("decision", "continue")
        decision = str(_decision) if _decision is not None else "continue"

        _idempotency_key = data.get("idempotency_key", "")
        idempotency_key = str(_idempotency_key) if _idempotency_key is not None else ""

        refs_raw = data.get("artifact_refs", [])
        artifact_refs: tuple[str, ...] = ()
        if isinstance(refs_raw, list):
            artifact_refs = tuple(str(r) for r in refs_raw if r is not None)

        checksums_raw = data.get("artifact_checksums", {})
        artifact_checksums: dict[str, str] = {}
        if isinstance(checksums_raw, dict):
            artifact_checksums = {
                str(k): str(v)
                for k, v in checksums_raw.items()
                if k is not None and v is not None
            }

        _comment_text = data.get("comment_text", "")
        comment_text = str(_comment_text) if _comment_text is not None else ""

        return cls(
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            decision=decision,
            artifact_refs=artifact_refs,
            artifact_checksums=artifact_checksums,
            comment_text=comment_text,
            idempotency_key=idempotency_key,
        )


# ── Resume response — what the backend returns ──────────────────────────

class ResumeResponse(StrictModel):
    """Backend-owned response to a resume request.

    The response communicates the outcome, the next stage (if resumed),
    and any rejection reason.  It NEVER exposes filesystem paths,
    sandbox details, or internal execution parameters.
    """

    request_id: NonEmptyString
    """Opaque ID for this resume request (for logging/tracing)."""

    checkpoint_id: NonEmptyString
    """The checkpoint that was the target of the resume attempt."""

    job_id: NonEmptyString
    """The migration job this resume belongs to."""

    outcome: ResumeOutcome
    """The result of the resume attempt."""

    rejection_code: ResumeRejectionCode | None = Field(
        default=None,
        description="If rejected, the machine-readable rejection code.",
    )

    rejection_detail: str = Field(
        default="",
        max_length=500,
        description="Sanitized human-readable detail. Never exposes paths or secrets.",
    )

    next_gate_id: str = Field(
        default="",
        description="If RESUMED, the gate_id of the next stage's PhaseGate.",
    )

    next_stage: str = Field(
        default="",
        description="If RESUMED, the stage name the backend resolved (e.g. 'planning', 'build').",
    )

    idempotency_key: str = ""
    """The idempotency key that produced this response."""

    is_cached: bool = False
    """True if this response was returned from a cached prior outcome."""

    resolved_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 timestamp of when the resume was resolved.",
    )

    @field_validator("request_id", "checkpoint_id", "job_id", mode="after")
    @classmethod
    def _validate_required_strings(cls, value: str, info) -> str:
        return require_non_empty_string(value, info.field_name)

    @model_validator(mode="after")
    def _rejected_must_have_rejection_code(self) -> "ResumeResponse":
        """Rejected outcomes MUST include a rejection_code."""
        if self.outcome not in _SUCCESSFUL_RESUME_OUTCOMES:
            if self.rejection_code is None:
                raise ValueError(
                    f"Outcome {self.outcome.value!r} requires a rejection_code"
                )
        return self

    @model_validator(mode="after")
    def _resumed_must_have_next_stage(self) -> "ResumeResponse":
        """A successful resume MUST identify the next stage."""
        if self.outcome == ResumeOutcome.RESUMED:
            if not self.next_stage or not self.next_stage.strip():
                raise ValueError(
                    "Outcome 'resumed' requires a non-empty next_stage"
                )
        return self

    @property
    def is_rejected(self) -> bool:
        """True if the resume was rejected for any reason."""
        return self.outcome not in _SUCCESSFUL_RESUME_OUTCOMES

    @property
    def is_resumed(self) -> bool:
        """True if the resume succeeded and a next stage was resolved."""
        return self.outcome == ResumeOutcome.RESUMED

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a safe dictionary for API responses."""
        return {
            "request_id": self.request_id,
            "checkpoint_id": self.checkpoint_id,
            "job_id": self.job_id,
            "outcome": self.outcome.value,
            "rejection_code": self.rejection_code.value if self.rejection_code else None,
            "rejection_detail": self.rejection_detail,
            "next_gate_id": self.next_gate_id,
            "next_stage": self.next_stage,
            "idempotency_key": self.idempotency_key,
            "is_cached": self.is_cached,
            "resolved_at": self.resolved_at,
        }

    def to_json(self) -> str:
        """Serialize to compact JSON."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResumeResponse":
        """Deserialize from a dictionary.

        Database NULL columns appear as present-but-None keys.
        We guard every extraction with ``is not None`` before casting.
        """
        _request_id = data.get("request_id", "")
        request_id = str(_request_id) if _request_id is not None else ""

        _checkpoint_id = data.get("checkpoint_id", "")
        checkpoint_id = str(_checkpoint_id) if _checkpoint_id is not None else ""

        _job_id = data.get("job_id", "")
        job_id = str(_job_id) if _job_id is not None else ""

        outcome_raw = data.get("outcome", "failed_closed")
        if outcome_raw is None:
            outcome_raw = "failed_closed"
        outcome = (
            ResumeOutcome(outcome_raw)
            if isinstance(outcome_raw, str)
            else outcome_raw
        )

        rejection_code = None
        rc_raw = data.get("rejection_code")
        if rc_raw is not None and isinstance(rc_raw, str):
            try:
                rejection_code = ResumeRejectionCode(rc_raw)
            except ValueError:
                rejection_code = None

        # Fallback: rejected outcomes require a rejection_code.
        # When outcome is non-successful and no valid rejection_code was
        # resolved, default to BACKEND_FAILURE for fail-closed safety.
        if outcome not in _SUCCESSFUL_RESUME_OUTCOMES and rejection_code is None:
            rejection_code = ResumeRejectionCode.BACKEND_FAILURE

        _rejection_detail = data.get("rejection_detail", "")
        rejection_detail = str(_rejection_detail) if _rejection_detail is not None else ""

        _next_gate_id = data.get("next_gate_id", "")
        next_gate_id = str(_next_gate_id) if _next_gate_id is not None else ""

        _next_stage = data.get("next_stage", "")
        next_stage = str(_next_stage) if _next_stage is not None else ""

        _idempotency_key = data.get("idempotency_key", "")
        idempotency_key = str(_idempotency_key) if _idempotency_key is not None else ""

        _resolved_at = data.get("resolved_at", "")
        resolved_at = str(_resolved_at) if _resolved_at is not None else ""

        return cls(
            request_id=request_id,
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            outcome=outcome,
            rejection_code=rejection_code,
            rejection_detail=rejection_detail,
            next_gate_id=next_gate_id,
            next_stage=next_stage,
            idempotency_key=idempotency_key,
            is_cached=bool(data.get("is_cached", False)),
            resolved_at=resolved_at,
        )

    @classmethod
    def idempotent(
        cls,
        request_id: str,
        checkpoint_id: str,
        job_id: str,
        idempotency_key: str,
        prior_response: "ResumeResponse",
    ) -> "ResumeResponse":
        """Factory for an idempotent (cached) response returning a prior outcome."""
        return cls(
            request_id=request_id,
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            outcome=ResumeOutcome.IDEMPOTENT,
            rejection_code=prior_response.rejection_code,
            rejection_detail=prior_response.rejection_detail,
            next_gate_id=prior_response.next_gate_id,
            next_stage=prior_response.next_stage,
            idempotency_key=idempotency_key,
            is_cached=True,
        )

    @classmethod
    def rejected(
        cls,
        request_id: str,
        checkpoint_id: str,
        job_id: str,
        outcome: ResumeOutcome,
        rejection_code: ResumeRejectionCode,
        rejection_detail: str = "",
        idempotency_key: str = "",
    ) -> "ResumeResponse":
        """Factory for a rejected resume response."""
        return cls(
            request_id=request_id,
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            outcome=outcome,
            rejection_code=rejection_code,
            rejection_detail=rejection_detail,
            idempotency_key=idempotency_key,
        )


# ── Safe resume fields ──────────────────────────────────────────────────

RESUME_FIELDS: frozenset[str] = frozenset({
    # Request fields
    "checkpoint_id",
    "job_id",
    "decision",
    "artifact_refs",
    "artifact_checksums",
    "comment_text",
    "idempotency_key",
    # Response fields
    "request_id",
    "outcome",
    "rejection_code",
    "rejection_detail",
    "next_gate_id",
    "next_stage",
    "is_cached",
    "resolved_at",
})

# Verify there is zero overlap with forbidden/dangerous fields.
assert RESUME_FIELDS.isdisjoint({
    "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
    "provider", "model", "deployment", "endpoint", "secret", "token",
    "password", "api_key", "client_secret", "command",
}), "RESUME_FIELDS must not contain dangerous fields"


# ── Validation helpers ──────────────────────────────────────────────────

def is_valid_resume_outcome(outcome: str) -> bool:
    """Return True if *outcome* is a known ResumeOutcome value."""
    try:
        ResumeOutcome(outcome)
        return True
    except ValueError:
        return False


def is_valid_rejection_code(code: str) -> bool:
    """Return True if *code* is a known ResumeRejectionCode value."""
    try:
        ResumeRejectionCode(code)
        return True
    except ValueError:
        return False


def is_valid_idempotency_key_format(key: str) -> bool:
    """Return True if the idempotency key looks well-formed.

    Idempotency keys must be non-empty, non-whitespace strings.
    The backend may apply additional uniqueness checks.
    """
    return bool(key and key.strip())


def is_terminal_rejection(code: ResumeRejectionCode) -> bool:
    """Return True if *code* means no further auto-retry should be attempted."""
    return code in _TERMINAL_REJECTION_CODES
