"""F15 event type registry — governed-stage gate and decision events.

Defines canonical event_type strings for all F15 gate lifecycle events.
These are persisted in RunEventRecord and streamed via SSE to the
frontend. SSE clients must ignore unknown event types safely.

Payload invariants:
  * No raw absolute paths in any event payload.
  * Every event includes job_id and (where applicable) stage_index, gate_id.
  * Event payloads are checksum-bound and never contain raw commands,
    sandbox paths, or filesystem targets.
"""

from __future__ import annotations

from enum import Enum


class F15EventType(str, Enum):
    """Canonical registry of F15 gate and decision event types.

    Each member pairs a Python-safe name with the exact event_type string
    persisted in RunEventRecord.event_type for F15-governed stages.
    """

    # ── gate lifecycle events ───────────────────────────────────────
    GATE_OPENED = "f15_gate_opened"
    GATE_RESOLVED = "f15_gate_resolved"
    GATE_SUPERSEDED = "f15_gate_superseded"

    # ── gate action events ──────────────────────────────────────────
    GATE_ACTION_ACCEPTED = "f15_gate_action_accepted"
    GATE_ACTION_REJECTED = "f15_gate_action_rejected"

    # ── review-required events (emitted when a stage completes
    #    and a manual review gate is created) ─────────────────────────
    ANALYSIS_REVIEW_REQUIRED = "f15_analysis_review_required"
    PLANNING_REVIEW_REQUIRED = "f15_planning_review_required"
    APPROVAL_REVIEW_REQUIRED = "f15_approval_review_required"
    REPAIR_REVIEW_REQUIRED = "f15_repair_review_required"
    STAGE_COMPLETION_REVIEW_REQUIRED = "f15_stage_completion_review_required"

    # ── decision lifecycle events ───────────────────────────────────
    DECISION_RECORDED = "f15_decision_recorded"
    DECISION_CONFLICT = "f15_decision_conflict"  # idempotency conflict

    # ── revision events ─────────────────────────────────────────────
    REVISION_CREATED = "f15_revision_created"
    REVISION_ACCEPTED = "f15_revision_accepted"
    REVISION_SUPERSEDED = "f15_revision_superseded"


# ── convenience sets ──────────────────────────────────────────────────

"""Events that describe gate lifecycle transitions."""
GATE_LIFECYCLE_EVENTS = frozenset({
    F15EventType.GATE_OPENED,
    F15EventType.GATE_RESOLVED,
    F15EventType.GATE_SUPERSEDED,
})

"""Events that describe gate action outcomes."""
GATE_ACTION_EVENTS = frozenset({
    F15EventType.GATE_ACTION_ACCEPTED,
    F15EventType.GATE_ACTION_REJECTED,
})

"""Events emitted when a manual review gate is created."""
REVIEW_REQUIRED_EVENTS = frozenset({
    F15EventType.ANALYSIS_REVIEW_REQUIRED,
    F15EventType.PLANNING_REVIEW_REQUIRED,
    F15EventType.APPROVAL_REVIEW_REQUIRED,
    F15EventType.REPAIR_REVIEW_REQUIRED,
    F15EventType.STAGE_COMPLETION_REVIEW_REQUIRED,
})

"""Events that describe decision recording."""
DECISION_EVENTS = frozenset({
    F15EventType.DECISION_RECORDED,
    F15EventType.DECISION_CONFLICT,
})

"""Events that describe revision lifecycle."""
REVISION_EVENTS = frozenset({
    F15EventType.REVISION_CREATED,
    F15EventType.REVISION_ACCEPTED,
    F15EventType.REVISION_SUPERSEDED,
})

"""All canonical F15 event types."""
ALL_F15_EVENT_TYPES = frozenset(F15EventType)
