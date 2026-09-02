"""F1-T4 Planning checkpoint contract — defines the Planning checkpoint
outcomes, user-visible fields, artifact requirements, and comment flow.

The Planning checkpoint is the second governed stop in the migration
pipeline. After Planning completes and the reviewer LLM produces a
reviewed artifact, the pipeline stops at this checkpoint to wait for
a user decision before transformation/build/test steps.

This contract bridges:
  - ``PhaseGate`` (PLANNING_REVIEW phase) — the stop/wait mechanism
  - ``ArtifactRevision`` (PLANNING kind) — the evidence versioning
  - ``CheckpointProfileMetadata`` — profile routing metadata
  - ``StopCondition`` (planning_checkpoint) — condition and allowed actions

Design invariants:
  - Never exposes sandbox_path, argv, env, raw commands, provider,
    deployment, or endpoint fields.
  - Decision flows through backend-owned PhaseGate/ArtifactRevision
    resolution — the frontend/chatbot only expresses intent.
  - Comments are persisted as structured metadata and bound to a
    future revision request without bypassing backend validation.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from .common import NonEmptyString, StrictModel, require_non_empty_string
from .profile_checkpoint_metadata import CheckpointProfileMetadata


# ── Planning checkpoint outcomes ──────────────────────────────────────

class PlanningOutcome(str, Enum):
    """Possible outcomes at the Planning checkpoint.

    These map to the PhaseGate decisions defined in phase_gate.py:
      - ACCEPTED → gate closed, pipeline proceeds
      - MODIFICATION_REQUESTED → GateDecision.REANALYZE (re-plan)
      - STOPPED → terminal user stop
      - STALE → artifact checksum mismatch (needs regeneration)
      - FAILED_CLOSED → planning or reviewer failure
    """

    WAITING = "waiting"
    ACCEPTED = "accepted"
    MODIFICATION_REQUESTED = "modification_requested"
    STOPPED = "stopped"
    STALE = "stale"
    FAILED_CLOSED = "failed_closed"


# Terminal outcomes: once reached, no further user action is possible.
TERMINAL_PLANNING_OUTCOMES: frozenset[PlanningOutcome] = frozenset({
    PlanningOutcome.ACCEPTED,
    PlanningOutcome.STOPPED,
    PlanningOutcome.FAILED_CLOSED,
})


# ── User-visible actions ──────────────────────────────────────────────

class PlanningCheckpointAction(str, Enum):
    """Actions a user may request at the Planning checkpoint.

    These are user-visible intents. The backend maps them to the
    appropriate GateDecision or system action:
      - CONTINUE → gate closed, transformation proceeds
      - REQUEST_MODIFICATION → GateDecision.REANALYZE (re-plan)
      - STOP → terminal stop
      - DOWNLOAD_ARTIFACT → artifact preview/download
    """

    CONTINUE = "continue"
    REQUEST_MODIFICATION = "request_modification"
    STOP = "stop"
    DOWNLOAD_ARTIFACT = "download_artifact"


# ── Required Planning artifact specification ──────────────────────────

# The artifact types that must be present before the Planning checkpoint
# can be opened for review. These match the Planning Agent's output
# contract defined in migration_factory/contracts/constants.py.
REQUIRED_PLANNING_ARTIFACTS: tuple[str, ...] = (
    "migration_plan_yaml",
    "migration_units_yaml",
    "plan_summary_md",
    "approval_request_json",
    "plan_validation_report_json",
)

# Fields required in every Planning checkpoint artifact reference.
REQUIRED_PLANNING_ARTIFACT_FIELDS: frozenset[str] = frozenset({
    "artifact_id",
    "artifact_type",
    "checksum",
    "path",
    "kind",
    "revision_id",
})


# ── Safe API fields ───────────────────────────────────────────────────

# All fields that may appear in Planning checkpoint serialized output.
# The module-level assertion guarantees zero intersection with dangerous
# fields (sandbox_path, argv, env, provider, etc.).
PLANNING_CHECKPOINT_FIELDS: frozenset[str] = frozenset({
    "checkpoint_id",
    "job_id",
    "run_id",
    "stage_index",
    "gate_id",
    "gate_phase",
    "gate_checksum",
    "outcome",
    "summary_text",
    "preview_artifact_refs",
    "latest_download_artifact_ref",
    "artifact_refs",
    "artifact_types",
    "checksums",
    "comment_count",
    "comments",
    "stale_reason",
    "stale_at",
    "resolved_at",
    "resolved_by",
    "terminal",
    "is_terminal",
    "modification_feedback",
    "profile_metadata",
})

# Safety assertion: Planning checkpoint fields must NEVER include
# dangerous fields that could leak execution details.
_DANGEROUS_FIELDS: frozenset[str] = frozenset({
    "sandbox_path",
    "argv",
    "env",
    "raw_command",
    "provider",
    "model",
    "deployment",
    "endpoint",
    "secret",
    "token",
    "password",
    "command",
})

assert PLANNING_CHECKPOINT_FIELDS.isdisjoint(_DANGEROUS_FIELDS), (
    "PLANNING_CHECKPOINT_FIELDS must not contain dangerous fields"
)


# ── Planning checkpoint comment ───────────────────────────────────────

class PlanningCheckpointComment(StrictModel):
    """A structured comment attached to the Planning checkpoint.

    Comments are stored per-section so the re-planning agent can
    consume targeted feedback. They are advisory only and cannot
    bypass backend validation.
    """

    comment_id: str = Field(
        ...,
        min_length=1,
        description="Unique comment identifier within this checkpoint.",
    )
    section: str = Field(
        ...,
        min_length=1,
        description="Target section for this comment (e.g. migration_plan, "
        "migration_units, risks, summary).",
    )
    author: str = Field(
        "",
        min_length=0,
        description="Author of the comment (empty for anonymous).",
    )
    text: str = Field(
        ...,
        min_length=1,
        max_length=8192,
        description="Comment body text.",
    )
    is_resolved: bool = Field(
        False,
        description="Whether this comment has been addressed.",
    )
    created_at: str = Field(
        "",
        description="ISO 8601 timestamp when the comment was created.",
    )

    @field_validator("comment_id")
    @classmethod
    def _comment_id_not_empty(cls, v: str) -> str:
        return require_non_empty_string(v, "comment_id")


# ── Planning checkpoint ──────────────────────────────────────────────

class PlanningCheckpoint(StrictModel):
    """The Planning checkpoint contract.

    This is a governed stop point after the Planning Agent completes
    and the reviewer LLM produces reviewed artifacts. The checkpoint
    waits for a user decision before transformation/build/test steps
    proceed.

    Integration points:
      - ``gate_id`` / ``gate_checksum`` bind to the PhaseGate in
        PLANNING_REVIEW phase (stage_index=2).
      - ``artifact_refs`` / ``checksums`` tie to ArtifactRevision
        records of kind PLANNING.
      - ``profile_metadata`` carries the CheckpointProfileMetadata
        for the current source/target profile routing.
      - ``comments`` are stored as structured section-targeted
        items that the re-planning agent can consume.
    """

    # ── Gateway binding ─────────────────────────────────────────────

    checkpoint_id: str = Field(
        "",
        description="Checkpoint identifier (set by backend).",
    )
    job_id: str = Field(
        ...,
        min_length=1,
        description="Job identifier this checkpoint belongs to.",
    )
    run_id: str = Field(
        "",
        description="Run identifier (set by backend).",
    )
    stage_index: int = Field(
        2,
        le=2,
        ge=2,
        description="Always 2 for the Planning checkpoint.",
    )
    gate_id: str = Field(
        "",
        description="PhaseGate identifier (PLANNING_REVIEW phase).",
    )
    gate_phase: str = Field(
        "planning_review",
        description="The gate phase: always planning_review.",
    )
    gate_checksum: str = Field(
        "",
        description="SHA-256 checksum of the gate payload.",
    )

    # ── Outcome ─────────────────────────────────────────────────────

    outcome: PlanningOutcome = Field(
        PlanningOutcome.WAITING,
        description="Current outcome state of the checkpoint.",
    )

    # ── Summary and preview ──────────────────────────────────────────

    summary_text: str = Field(
        "",
        max_length=4096,
        description="Human-readable summary of the Planning output.",
    )
    preview_artifact_refs: tuple[str, ...] = Field(
        (),
        description="Artifact references available for preview.",
    )
    latest_download_artifact_ref: str = Field(
        "",
        description="Most recent artifact reference for download.",
    )

    # ── Artifact binding ─────────────────────────────────────────────

    artifact_refs: tuple[str, ...] = Field(
        (),
        description="Planning artifact references bound to this checkpoint.",
    )
    artifact_types: tuple[str, ...] = Field(
        (),
        description="Artifact types present at this checkpoint.",
    )
    checksums: tuple[str, ...] = Field(
        (),
        description="Checksums of the Planning artifacts.",
    )

    # ── Comment handling ─────────────────────────────────────────────

    comments: tuple[PlanningCheckpointComment, ...] = Field(
        (),
        description="Structured section-targeted comments.",
    )

    # ── Stale tracking ──────────────────────────────────────────────

    stale_reason: str = Field(
        "",
        description="Reason this checkpoint is marked stale.",
    )
    stale_at: str = Field(
        "",
        description="ISO 8601 timestamp when the checkpoint became stale.",
    )

    # ── Resolution tracking ─────────────────────────────────────────

    resolved_at: str = Field(
        "",
        description="ISO 8601 timestamp when the checkpoint was resolved.",
    )
    resolved_by: str = Field(
        "",
        description="Actor who resolved the checkpoint.",
    )

    # ── Profile metadata ────────────────────────────────────────────

    profile_metadata: CheckpointProfileMetadata | None = Field(
        None,
        description="Source/target profile routing metadata.",
    )

    # ── Lifecycle validators ────────────────────────────────────────

    @field_validator("outcome", mode="before")
    @classmethod
    def _coerce_outcome(cls, value: Any) -> PlanningOutcome:
        if isinstance(value, PlanningOutcome):
            return value
        return PlanningOutcome(value)

    @field_validator("job_id", "checkpoint_id", "gate_phase")
    @classmethod
    def _non_empty_required(cls, v: str, info: Any) -> str:
        if info.field_name == "checkpoint_id":
            return v
        if info.field_name == "gate_phase":
            if v != "planning_review":
                raise ValueError("gate_phase must be planning_review")
            return v
        return require_non_empty_string(v, info.field_name)

    @field_validator("stage_index")
    @classmethod
    def _stage_index_must_be_two(cls, v: int) -> int:
        if v != 2:
            raise ValueError("stage_index must be 2 for Planning checkpoint")
        return v

    @model_validator(mode="after")
    def _validate_terminal_state(self) -> "PlanningCheckpoint":
        """Terminal outcomes must have resolved_at and resolved_by set."""
        if self.outcome in TERMINAL_PLANNING_OUTCOMES:
            if not self.resolved_at or not self.resolved_by:
                raise ValueError(
                    "A terminal planning checkpoint must set "
                    "resolved_at and resolved_by"
                )
        return self

    @model_validator(mode="after")
    def _waiting_must_not_have_resolved_fields(self) -> "PlanningCheckpoint":
        """WAITING checkpoints must not have resolved_at or resolved_by."""
        if self.outcome == PlanningOutcome.WAITING:
            if self.resolved_at or self.resolved_by:
                raise ValueError(
                    "A waiting planning checkpoint must not have "
                    "resolved_at or resolved_by set"
                )
        return self

    @model_validator(mode="after")
    def _validate_modification_requested(self) -> "PlanningCheckpoint":
        """MODIFICATION_REQUESTED outcome requires at least one comment."""
        if self.outcome == PlanningOutcome.MODIFICATION_REQUESTED:
            if len(self.comments) == 0:
                raise ValueError(
                    "MODIFICATION_REQUESTED requires at least one comment"
                )
        return self

    @model_validator(mode="after")
    def _validate_stale(self) -> "PlanningCheckpoint":
        """STALE outcome requires a stale_reason."""
        if self.outcome == PlanningOutcome.STALE:
            if not self.stale_reason:
                raise ValueError("STALE outcome requires stale_reason")
        return self

    # ── Derived properties ──────────────────────────────────────────

    @property
    def is_terminal(self) -> bool:
        """True if the checkpoint is in a terminal outcome."""
        return self.outcome in TERMINAL_PLANNING_OUTCOMES

    @property
    def comment_count(self) -> int:
        """Number of comments attached to this checkpoint."""
        return len(self.comments)

    @property
    def modification_feedback(self) -> dict[str, list[str]]:
        """Aggregate comments by section for the re-planning agent.

        Returns a dict mapping section name to list of comment texts.
        This feeds the re-planning cycle without exposing raw backend
        internals.
        """
        feedback: dict[str, list[str]] = {}
        for comment in self.comments:
            feedback.setdefault(comment.section, []).append(comment.text)
        return feedback

    # ── Serialization ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for API responses and persistence."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "stage_index": self.stage_index,
            "gate_id": self.gate_id,
            "gate_phase": self.gate_phase,
            "gate_checksum": self.gate_checksum,
            "outcome": self.outcome.value,
            "summary_text": self.summary_text,
            "preview_artifact_refs": list(self.preview_artifact_refs),
            "latest_download_artifact_ref": self.latest_download_artifact_ref,
            "artifact_refs": list(self.artifact_refs),
            "artifact_types": list(self.artifact_types),
            "checksums": list(self.checksums),
            "comments": [
                {
                    "comment_id": c.comment_id,
                    "section": c.section,
                    "author": c.author,
                    "text": c.text,
                    "is_resolved": c.is_resolved,
                    "created_at": c.created_at,
                }
                for c in self.comments
            ],
            "comment_count": self.comment_count,
            "stale_reason": self.stale_reason,
            "stale_at": self.stale_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "is_terminal": self.is_terminal,
            "modification_feedback": self.modification_feedback,
            "profile_metadata": (
                self.profile_metadata.to_dict()
                if self.profile_metadata
                else None
            ),
        }

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanningCheckpoint":
        """Deserialize from a plain dict with None-value guards.

        Database NULL columns appear as present-but-None keys.
        We guard every extraction with ``is not None`` before casting.
        """
        outcome_raw = data.get("outcome")
        outcome = (
            PlanningOutcome(outcome_raw)
            if outcome_raw is not None
            else PlanningOutcome.WAITING
        )

        profile_meta_raw = data.get("profile_metadata")
        profile_metadata = (
            CheckpointProfileMetadata.from_dict(profile_meta_raw)
            if profile_meta_raw is not None
            else None
        )

        comments_raw = data.get("comments")
        if comments_raw is not None and isinstance(comments_raw, list):
            comments = tuple(
                PlanningCheckpointComment(
                    comment_id=str(c.get("comment_id", "")),
                    section=str(c.get("section", "")),
                    author=str(c.get("author", "")),
                    text=str(c.get("text", "")),
                    is_resolved=bool(c.get("is_resolved", False)),
                    created_at=str(c.get("created_at", "")),
                )
                for c in comments_raw
            )
        else:
            comments = ()

        stage_index_raw = data.get("stage_index")
        stage_index = int(stage_index_raw) if stage_index_raw is not None else 2

        preview_raw = data.get("preview_artifact_refs")
        if preview_raw is not None:
            preview_artifact_refs = tuple(preview_raw) if isinstance(preview_raw, (list, tuple)) else ()
        else:
            preview_artifact_refs = ()

        artifact_refs_raw = data.get("artifact_refs")
        if artifact_refs_raw is not None:
            artifact_refs = tuple(artifact_refs_raw) if isinstance(artifact_refs_raw, (list, tuple)) else ()
        else:
            artifact_refs = ()

        artifact_types_raw = data.get("artifact_types")
        if artifact_types_raw is not None:
            artifact_types = tuple(artifact_types_raw) if isinstance(artifact_types_raw, (list, tuple)) else ()
        else:
            artifact_types = ()

        checksums_raw = data.get("checksums")
        if checksums_raw is not None:
            checksums = tuple(checksums_raw) if isinstance(checksums_raw, (list, tuple)) else ()
        else:
            checksums = ()

        rat = str(data.get("resolved_at", "")) if data.get("resolved_at") is not None else ""
        rby = str(data.get("resolved_by", "")) if data.get("resolved_by") is not None else ""
        gate_phase_val = str(data.get("gate_phase", "planning_review")) if data.get("gate_phase") is not None else "planning_review"

        return cls(
            checkpoint_id=str(data.get("checkpoint_id", "")) if data.get("checkpoint_id") is not None else "",
            job_id=str(data.get("job_id", "")) if data.get("job_id") is not None else "",
            run_id=str(data.get("run_id", "")) if data.get("run_id") is not None else "",
            stage_index=stage_index,
            gate_id=str(data.get("gate_id", "")) if data.get("gate_id") is not None else "",
            gate_phase=gate_phase_val,
            gate_checksum=str(data.get("gate_checksum", "")) if data.get("gate_checksum") is not None else "",
            outcome=outcome,
            summary_text=str(data.get("summary_text", "")) if data.get("summary_text") is not None else "",
            preview_artifact_refs=preview_artifact_refs,
            latest_download_artifact_ref=str(data.get("latest_download_artifact_ref", "")) if data.get("latest_download_artifact_ref") is not None else "",
            artifact_refs=artifact_refs,
            artifact_types=artifact_types,
            checksums=checksums,
            comments=comments,
            stale_reason=str(data.get("stale_reason", "")) if data.get("stale_reason") is not None else "",
            stale_at=str(data.get("stale_at", "")) if data.get("stale_at") is not None else "",
            resolved_at=rat,
            resolved_by=rby,
            profile_metadata=profile_metadata,
        )

    @classmethod
    def from_json(cls, json_str: str) -> "PlanningCheckpoint":
        """Deserialize from a JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)


# ── Allowed decisions per outcome ────────────────────────────────────

# Maps PlanningOutcome to the GateDecision values that are valid
# when the checkpoint is in that outcome state.
_ALLOWED_GATE_DECISIONS_BY_OUTCOME: dict[PlanningOutcome, frozenset[str]] = {
    PlanningOutcome.WAITING: frozenset({"continue", "reanalyze"}),
    PlanningOutcome.ACCEPTED: frozenset(),
    PlanningOutcome.MODIFICATION_REQUESTED: frozenset({"continue", "reanalyze"}),
    PlanningOutcome.STOPPED: frozenset(),
    PlanningOutcome.STALE: frozenset({"reanalyze"}),
    PlanningOutcome.FAILED_CLOSED: frozenset(),
}

# Maps PlanningCheckpointAction values to GateDecision values.
_ACTION_TO_GATE_DECISION: dict[PlanningCheckpointAction, str] = {
    PlanningCheckpointAction.CONTINUE: "continue",
    PlanningCheckpointAction.REQUEST_MODIFICATION: "reanalyze",  # re-plan
    PlanningCheckpointAction.STOP: "",          # terminal stop — no gate decision
    PlanningCheckpointAction.DOWNLOAD_ARTIFACT: "",  # read-only — no gate decision
}


def is_valid_planning_gate_decision_for_outcome(
    outcome: PlanningOutcome,
    gate_decision: str,
) -> bool:
    """Return True if *gate_decision* is valid at *outcome*.

    *gate_decision* is a GateDecision string value (e.g. "continue",
    "reanalyze").  This is the low-level hook for gate service integration.
    """
    allowed = _ALLOWED_GATE_DECISIONS_BY_OUTCOME.get(outcome, frozenset())
    return gate_decision in allowed


def is_valid_planning_action_for_outcome(
    outcome: PlanningOutcome,
    action: PlanningCheckpointAction,
) -> bool:
    """Return True if *action* is valid when the checkpoint is in *outcome*.

    This is the user-facing API: callers pass a ``PlanningCheckpointAction``
    enum value and the function maps it to the equivalent GateDecision before
    consulting the allowed-decision matrix.

    Actions that have no corresponding gate decision (STOP,
    DOWNLOAD_ARTIFACT) are always valid regardless of outcome.
    """
    gate_decision = _ACTION_TO_GATE_DECISION.get(action)
    if gate_decision is None:
        return False
    if gate_decision == "":
        # Terminal stop and download are always available to the user.
        return True
    return is_valid_planning_gate_decision_for_outcome(outcome, gate_decision)


# ── Planning artifact specification helpers ──────────────────────────

def get_required_planning_artifact_types() -> tuple[str, ...]:
    """Return the artifact types that must be present before Planning
    checkpoint review can begin.

    These match the Planning Agent's output contract defined in
    ``migration_factory/contracts/constants.py``.
    """
    return REQUIRED_PLANNING_ARTIFACTS


def validate_planning_artifact_refs(
    artifact_types: tuple[str, ...],
) -> bool:
    """Return True if *artifact_types* includes all required Planning artifacts."""
    return set(REQUIRED_PLANNING_ARTIFACTS).issubset(set(artifact_types))
