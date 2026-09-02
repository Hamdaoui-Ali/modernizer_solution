"""Focused tests for F15 job008 — F15 event taxonomy."""

from __future__ import annotations

from migration_factory.control_tower.domain.f15_events import (
    ALL_F15_EVENT_TYPES,
    DECISION_EVENTS,
    F15EventType,
    GATE_ACTION_EVENTS,
    GATE_LIFECYCLE_EVENTS,
    REVIEW_REQUIRED_EVENTS,
    REVISION_EVENTS,
)


def test_event_names_are_strings() -> None:
    for event in F15EventType:
        assert isinstance(event.value, str)
        assert len(event.value) > 0


def test_gate_lifecycle_events_defined() -> None:
    assert F15EventType.GATE_OPENED in GATE_LIFECYCLE_EVENTS
    assert F15EventType.GATE_RESOLVED in GATE_LIFECYCLE_EVENTS
    assert F15EventType.GATE_SUPERSEDED in GATE_LIFECYCLE_EVENTS
    assert len(GATE_LIFECYCLE_EVENTS) == 3


def test_gate_action_events_defined() -> None:
    assert F15EventType.GATE_ACTION_ACCEPTED in GATE_ACTION_EVENTS
    assert F15EventType.GATE_ACTION_REJECTED in GATE_ACTION_EVENTS
    assert len(GATE_ACTION_EVENTS) == 2


def test_review_required_events_defined() -> None:
    assert F15EventType.ANALYSIS_REVIEW_REQUIRED in REVIEW_REQUIRED_EVENTS
    assert F15EventType.PLANNING_REVIEW_REQUIRED in REVIEW_REQUIRED_EVENTS
    assert F15EventType.APPROVAL_REVIEW_REQUIRED in REVIEW_REQUIRED_EVENTS
    assert F15EventType.REPAIR_REVIEW_REQUIRED in REVIEW_REQUIRED_EVENTS
    assert F15EventType.STAGE_COMPLETION_REVIEW_REQUIRED in REVIEW_REQUIRED_EVENTS
    assert len(REVIEW_REQUIRED_EVENTS) == 5


def test_decision_events_defined() -> None:
    assert F15EventType.DECISION_RECORDED in DECISION_EVENTS
    assert F15EventType.DECISION_CONFLICT in DECISION_EVENTS
    assert len(DECISION_EVENTS) == 2


def test_revision_events_defined() -> None:
    assert F15EventType.REVISION_CREATED in REVISION_EVENTS
    assert F15EventType.REVISION_ACCEPTED in REVISION_EVENTS
    assert F15EventType.REVISION_SUPERSEDED in REVISION_EVENTS
    assert len(REVISION_EVENTS) == 3


def test_all_events_are_f15_prefixed() -> None:
    """All F15 event types start with 'f15_' to distinguish from V1."""
    for event in F15EventType:
        assert event.value.startswith("f15_"), f"{event.value} must start with 'f15_'"


def test_no_absolute_path_references() -> None:
    """Event type names contain no path-like strings."""
    for event in F15EventType:
        assert "/" not in event.value
        assert "\\" not in event.value


def test_all_f15_event_types_complete() -> None:
    """ALL_F15_EVENT_TYPES covers every enum member."""
    assert ALL_F15_EVENT_TYPES == frozenset(F15EventType)
    assert len(ALL_F15_EVENT_TYPES) == len(F15EventType)


def test_events_are_distinct() -> None:
    """No event type value is duplicated."""
    values = [e.value for e in F15EventType]
    assert len(values) == len(set(values))


def test_sets_are_disjoint() -> None:
    """Each convenience set contains unique categories with no overlap."""
    all_sets = [
        GATE_LIFECYCLE_EVENTS,
        GATE_ACTION_EVENTS,
        REVIEW_REQUIRED_EVENTS,
        DECISION_EVENTS,
        REVISION_EVENTS,
    ]
    union: set[F15EventType] = set()
    for s in all_sets:
        assert union.isdisjoint(s), f"Overlap found in event sets"
        union.update(s)
    # All categories together should cover all events
    assert union == set(F15EventType)
