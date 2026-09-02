"""F1-T3 Analysis checkpoint contract — defines the Analysis checkpoint
outcomes, user-visible fields, artifact requirements, and comment flow.

The Analysis checkpoint is the first governed stop in the migration
pipeline. After Analysis completes and the reviewer LLM produces a
reviewed artifact, the pipeline stops at this checkpoint to wait for
a user decision.

This contract bridges:
  - ``PhaseGate`` (ANALYSIS_REVIEW phase) — the stop/wait mechanism
  - ``ArtifactRevision`` (ANALYSIS kind) — the evidence versioning
  - ``CheckpointProfileMetadata`` — profile routing metadata
  - ``StopCondition`` (analysis_checkpoint) — condition and allowed actions

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


# ── Analysis checkpoint outcomes ─────────────────────────────────────

class AnalysisOutcome(str, Enum):
    """Possible outcomes at the Analysis checkpoint.

    These map to the PhaseGate decisions defined in phase_gate.py:
      - ACCEPTED → GateDecision.CONTINUE
      - MODIFICATION_REQUESTED → GateDecision.REANALYZE
      - STOPPED → terminal user stop
      - STALE → artifact checksum mismatch (needs regeneration)
      - FAILED_CLOSED → analysis or reviewer failure
    """

    WAITING = "waiting"
    ACCEPTED = "accepted"
    MODIFICATION_REQUESTED = "modification_requested"
    STOPPED = "stopped"
    STALE = "stale"
    FAILED_CLOSED = "failed_closed"


# Terminal outcomes: once reached, no further user action is possible.
TERMINAL_ANALYSIS_OUTCOMES: frozenset[AnalysisOutcome] = frozenset({
    AnalysisOutcome.ACCEPTED,
    AnalysisOutcome.STOPPED,
    AnalysisOutcome.FAILED_CLOSED,
})


# ── User-visible actions ─────────────────────────────────────────────

class AnalysisCheckpointAction(str, Enum):
    """Actions a user may request at the Analysis checkpoint.

    These are user-visible intents. The backend maps them to the
    appropriate GateDecision or system action:
      - CONTINUE → GateDecision.CONTINUE
      - REQUEST_MODIFICATION → GateDecision.REANALYZE
      - STOP → terminal stop
      - DOWNLOAD_ARTIFACT → artifact preview/download
    """

    CONTINUE = "continue"
    REQUEST_MODIFICATION = "request_modification"
    STOP = "stop"
    DOWNLOAD_ARTIFACT = "download_artifact"


# ── Required Analysis artifact specification ─────────────────────────

# The artifact types that must be present before the Analysis checkpoint
# can be opened for review.
REQUIRED_ANALYSIS_ARTIFACTS: tuple[str, ...] = (
    "analysis_report_json",
    "dependency_graph_json",
    "test_inventory_json",
    "config_inventory_json",
    "analysis_summary_md",
)

# Fields required in every Analysis checkpoint artifact reference.
REQUIRED_ANALYSIS_ARTIFACT_FIELDS: frozenset[str] = frozenset({
    "artifact_id",
    "artifact_type",
    "checksum",
    "revision_id",
})


# ── Safe Analysis checkpoint fields ──────────────────────────────────

# Fields that are safe to include in Analysis checkpoint metadata.
# These are checkpoint-specific fields PLUS the profile checkpoint
# fields inherited from CheckpointProfileMetadata.
ANALYSIS_CHECKPOINT_FIELDS: frozenset[str] = frozenset({
    "checkpoint_id",
    "job_id",
    "stage_index",
    "outcome",
    "gate_id",
    "revision_id",
    "source_artifact_checksum",
    "artifact_refs",
    "summary",
    "comments",
    "comment_count",
    "created_at",
    "resolved_at",
    "resolved_by",
    "decision_reason",
    "is_stale",
    "stale_reason",
})

# Verify there is zero overlap with forbidden/dangerous fields.
assert ANALYSIS_CHECKPOINT_FIELDS.isdisjoint({
    "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
    "provider", "model", "deployment", "endpoint", "secret", "token",
    "password", "api_key", "client_secret", "command",
})


# ── Analysis checkpoint comment ──────────────────────────────────────

class AnalysisCheckpointComment(StrictModel):
    """A single user comment attached to an Analysis checkpoint.

    Comments are persisted as structured metadata and bound to a
    later revision request. They do NOT bypass backend validation —
    the re-analysis agent reads them from the checkpoint, not from
    user-supplied input fields.
    """

    comment_id: NonEmptyString
    checkpoint_id: NonEmptyString
    text: str = Field(default="", min_length=0, max_length=2000)
    section: str = Field(
        default="general",
        description="Which section of the analysis this comment targets"
    )
    created_at: str = ""
    created_by: str = ""
    revision_id: str | None = Field(
        default=None,
        description="The revision created to address this comment, if any"
    )

    @field_validator("comment_id", "checkpoint_id", mode="after")
    @classmethod
    def _validate_required_ids(cls, value: str, info) -> str:
        return require_non_empty_string(value, info.field_name)


# ── Analysis checkpoint contract ─────────────────────────────────────

class AnalysisCheckpoint(StrictModel):
    """Analysis checkpoint — the governed stop after Analysis completes.

    This is the user-visible contract for the Analysis checkpoint.
    It bridges the PhaseGate (stop/wait mechanism) and ArtifactRevision
    (evidence versioning) with user-facing summary, preview references,
    and comment handling.

    Design invariants:
      - All artifact references are backend-owned (artifact IDs and
        checksums, never raw filesystem paths).
      - preview_refs returns artifact references, not sandbox paths.
      - Comments are bound to a later MODIFICATION_REQUESTED outcome
        and fed to the re-analysis agent by the backend.
      - Once terminal (ACCEPTED/STOPPED/FAILED_CLOSED), the checkpoint
        is immutable.
    """

    checkpoint_id: NonEmptyString
    job_id: NonEmptyString
    stage_index: int = Field(default=1, ge=1, le=1, frozen=True)

    outcome: AnalysisOutcome = AnalysisOutcome.WAITING

    # ── backing gate and revision ───────────────────────────────────
    gate_id: str = ""
    revision_id: str = ""

    # ── checksum binding ────────────────────────────────────────────
    # Ties the checkpoint to exact analysis artifact content.
    source_artifact_checksum: str = ""

    # ── artifact references ─────────────────────────────────────────
    # Ordered tuple of analysis artifact IDs this checkpoint reviews.
    artifact_refs: tuple[str, ...] = Field(default_factory=tuple)

    # ── user-visible summary ────────────────────────────────────────
    # Plain-text summary of analysis findings for the user to review.
    # This is derived from the analysis agent output, not the raw
    # artifact content.
    summary: str = ""

    # ── preview/download references ─────────────────────────────────
    # Artifact IDs that the user may preview or download. These are
    # artifact references (IDs), NOT filesystem paths. The backend
    # resolves them through the artifact resolver.
    preview_refs: tuple[str, ...] = Field(default_factory=tuple)

    # ── profile metadata ────────────────────────────────────────────
    profile_metadata: CheckpointProfileMetadata = Field(
        default_factory=CheckpointProfileMetadata
    )

    # ── stale tracking ──────────────────────────────────────────────
    is_stale: bool = False
    stale_reason: str = ""

    # ── decision metadata ───────────────────────────────────────────
    decision_reason: str = ""

    # ── timestamps and actor ────────────────────────────────────────
    created_at: str
    resolved_at: str | None = None
    resolved_by: str | None = None

    # ── comments ────────────────────────────────────────────────────
    comments: tuple[AnalysisCheckpointComment, ...] = Field(
        default_factory=tuple
    )

    # ── validation ──────────────────────────────────────────────────

    @field_validator("checkpoint_id", "job_id", "created_at", mode="after")
    @classmethod
    def _validate_required_strings(cls, value: str, info) -> str:
        return require_non_empty_string(value, info.field_name)

    @field_validator("outcome", mode="before")
    @classmethod
    def _coerce_outcome(cls, value) -> AnalysisOutcome:
        if isinstance(value, AnalysisOutcome):
            return value
        return AnalysisOutcome(value)

    @model_validator(mode="after")
    def _terminal_must_have_resolved_fields(self) -> "AnalysisCheckpoint":
        if self.outcome in TERMINAL_ANALYSIS_OUTCOMES:
            if not self.resolved_at or not self.resolved_by:
                raise ValueError(
                    "A terminal analysis checkpoint must set "
                    "resolved_at and resolved_by"
                )
        return self

    @model_validator(mode="after")
    def _waiting_must_not_have_resolved_fields(self) -> "AnalysisCheckpoint":
        if self.outcome == AnalysisOutcome.WAITING:
            if self.resolved_at is not None or self.resolved_by is not None:
                raise ValueError(
                    "A waiting analysis checkpoint must not have "
                    "resolved_at or resolved_by set"
                )
        return self

    @model_validator(mode="after")
    def _modification_requested_requires_comments(self) -> "AnalysisCheckpoint":
        if self.outcome == AnalysisOutcome.MODIFICATION_REQUESTED:
            if len(self.comments) == 0:
                raise ValueError(
                    "A modification_requested outcome requires at least "
                    "one comment explaining what should be changed"
                )
        return self

    @model_validator(mode="after")
    def _stale_must_have_reason(self) -> "AnalysisCheckpoint":
        if self.is_stale and not self.stale_reason:
            raise ValueError(
                "A stale checkpoint must provide a stale_reason"
            )
        return self

    @model_validator(mode="after")
    def _stage_index_must_be_one(self) -> "AnalysisCheckpoint":
        if self.stage_index != 1:
            raise ValueError(
                "Analysis checkpoint stage_index must be 1"
            )
        return self

    # ── derived properties ──────────────────────────────────────────

    @property
    def is_terminal(self) -> bool:
        """True when the checkpoint has reached a terminal outcome."""
        return self.outcome in TERMINAL_ANALYSIS_OUTCOMES

    @property
    def is_waiting(self) -> bool:
        """True when the checkpoint is waiting for a user decision."""
        return self.outcome == AnalysisOutcome.WAITING

    @property
    def comment_count(self) -> int:
        """Number of user comments attached to this checkpoint."""
        return len(self.comments)

    @property
    def has_artifacts(self) -> bool:
        """True when artifact refs are bound to this checkpoint."""
        return len(self.artifact_refs) > 0

    @property
    def modification_feedback(self) -> str:
        """Aggregated user feedback for the re-analysis agent.

        Concatenates all comment texts, one per line. The re-analysis
        agent reads this to understand what the user wants changed.
        """
        if not self.comments:
            return ""
        return "\n".join(
            f"[{c.section}] {c.text}" for c in self.comments if c.text
        )

    # ── serialization ───────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON storage or API responses."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "job_id": self.job_id,
            "stage_index": self.stage_index,
            "outcome": self.outcome.value,
            "gate_id": self.gate_id,
            "revision_id": self.revision_id,
            "source_artifact_checksum": self.source_artifact_checksum,
            "artifact_refs": list(self.artifact_refs),
            "summary": self.summary,
            "preview_refs": list(self.preview_refs),
            "profile_metadata": self.profile_metadata.to_dict(),
            "is_stale": self.is_stale,
            "stale_reason": self.stale_reason,
            "decision_reason": self.decision_reason,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "comments": [
                {
                    "comment_id": c.comment_id,
                    "checkpoint_id": c.checkpoint_id,
                    "text": c.text,
                    "section": c.section,
                    "created_at": c.created_at,
                    "created_by": c.created_by,
                    "revision_id": c.revision_id,
                }
                for c in self.comments
            ],
        }

    def to_json(self) -> str:
        """Serialize to a JSON string for artifact_refs_json storage."""
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisCheckpoint":
        """Deserialize from a plain dict.

        Guards against None values because dict[str, Any] callers may
        pass keys with None values (e.g. from database NULL columns).
        """
        comments_raw = data.get("comments")
        profile_raw = data.get("profile_metadata")

        comments: tuple[AnalysisCheckpointComment, ...] = ()
        if comments_raw is not None:
            if isinstance(comments_raw, list):
                comments = tuple(
                    AnalysisCheckpointComment(
                        comment_id=str(c.get("comment_id", "")),
                        checkpoint_id=str(c.get("checkpoint_id", "")),
                        text=str(c.get("text", "")),
                        section=str(c.get("section", "general")),
                        created_at=str(c.get("created_at", "")),
                        created_by=str(c.get("created_by", "")),
                        revision_id=c.get("revision_id"),
                    )
                    for c in comments_raw
                )

        profile = CheckpointProfileMetadata()
        if profile_raw is not None and isinstance(profile_raw, dict):
            profile = CheckpointProfileMetadata.from_dict(profile_raw)

        # Extract values with None guards — database NULL columns
        # appear as present-but-None in dicts.
        cid = data.get("checkpoint_id")
        jid = data.get("job_id")
        si = data.get("stage_index")
        out = data.get("outcome")
        gid = data.get("gate_id")
        rid = data.get("revision_id")
        sac = data.get("source_artifact_checksum")
        aref = data.get("artifact_refs")
        sum_text = data.get("summary")
        prev = data.get("preview_refs")
        stale_flag = data.get("is_stale")
        stale_msg = data.get("stale_reason")
        dec_reason = data.get("decision_reason")
        cat = data.get("created_at")
        rat = data.get("resolved_at")
        rby = data.get("resolved_by")

        return cls(
            checkpoint_id=str(cid) if cid is not None else "",
            job_id=str(jid) if jid is not None else "",
            stage_index=int(si) if si is not None else 1,
            outcome=(
                AnalysisOutcome(str(out))
                if out is not None else AnalysisOutcome.WAITING
            ),
            gate_id=str(gid) if gid is not None else "",
            revision_id=str(rid) if rid is not None else "",
            source_artifact_checksum=str(sac) if sac is not None else "",
            artifact_refs=(
                tuple(str(a) for a in aref)
                if aref is not None else ()
            ),
            summary=str(sum_text) if sum_text is not None else "",
            preview_refs=(
                tuple(str(a) for a in prev)
                if prev is not None else ()
            ),
            profile_metadata=profile,
            is_stale=bool(stale_flag) if stale_flag is not None else False,
            stale_reason=str(stale_msg) if stale_msg is not None else "",
            decision_reason=str(dec_reason) if dec_reason is not None else "",
            created_at=str(cat) if cat is not None else "",
            resolved_at=rat,
            resolved_by=rby,
            comments=comments,
        )

    @classmethod
    def from_json(cls, json_str: str) -> "AnalysisCheckpoint":
        """Deserialize from a JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)


# ── Allowed decisions per outcome ────────────────────────────────────

# Maps AnalysisOutcome to the GateDecision values that are valid
# when the checkpoint is in that outcome state.
_ALLOWED_GATE_DECISIONS_BY_OUTCOME: dict[AnalysisOutcome, frozenset[str]] = {
    AnalysisOutcome.WAITING: frozenset({"continue", "reanalyze"}),
    AnalysisOutcome.ACCEPTED: frozenset(),
    AnalysisOutcome.MODIFICATION_REQUESTED: frozenset({"continue", "reanalyze"}),
    AnalysisOutcome.STOPPED: frozenset(),
    AnalysisOutcome.STALE: frozenset({"reanalyze"}),
    AnalysisOutcome.FAILED_CLOSED: frozenset(),
}

# Maps AnalysisCheckpointAction values to GateDecision values.
_ACTION_TO_GATE_DECISION: dict[AnalysisCheckpointAction, str] = {
    AnalysisCheckpointAction.CONTINUE: "continue",
    AnalysisCheckpointAction.REQUEST_MODIFICATION: "reanalyze",
    AnalysisCheckpointAction.STOP: "",          # terminal stop — no gate decision
    AnalysisCheckpointAction.DOWNLOAD_ARTIFACT: "",  # read-only — no gate decision
}


def is_valid_gate_decision_for_outcome(
    outcome: AnalysisOutcome,
    gate_decision: str,
) -> bool:
    """Return True if *gate_decision* is valid at *outcome*.

    *gate_decision* is a GateDecision string value (e.g. "continue",
    "reanalyze").  This is the low-level hook for gate service integration.
    """
    allowed = _ALLOWED_GATE_DECISIONS_BY_OUTCOME.get(outcome, frozenset())
    return gate_decision in allowed


def is_valid_action_for_outcome(
    outcome: AnalysisOutcome,
    action: AnalysisCheckpointAction,
) -> bool:
    """Return True if *action* is valid when the checkpoint is in *outcome*.

    This is the user-facing API: callers pass an ``AnalysisCheckpointAction``
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
    return is_valid_gate_decision_for_outcome(outcome, gate_decision)


# ── Analysis artifact specification helpers ──────────────────────────

def get_required_analysis_artifact_types() -> tuple[str, ...]:
    """Return the artifact types that must be present before Analysis
    checkpoint review can begin.

    These match the Analysis Agent's output contract defined in
    ``migration_factory/agents/analysis_agent/agents.md``.
    """
    return REQUIRED_ANALYSIS_ARTIFACTS


def validate_analysis_artifact_refs(
    artifact_types: tuple[str, ...],
) -> bool:
    """Return True if *artifact_types* includes all required Analysis artifacts."""
    return set(REQUIRED_ANALYSIS_ARTIFACTS).issubset(set(artifact_types))
