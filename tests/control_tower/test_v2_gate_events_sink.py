"""Focused tests for F15-JOB-048 — Gate-aware event sink.

Verifies that gate lifecycle events (gate_opened, gate_resolved, gate_superseded)
can be emitted and contain appropriate payload without raw paths or secrets.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_event_repository import (
    SqliteV2JobEventRepository,
    V2JobEventRecord,
)
from migration_factory.control_tower.domain.f15_events import (
    F15EventType,
    GATE_LIFECYCLE_EVENTS,
    REVIEW_REQUIRED_EVENTS,
)


def _connection(tmp_path: Path, name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(tmp_path / name),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


def test_f15_gate_opened_event_can_be_saved(tmp_path: Path) -> None:
    """f15_gate_opened event can be persisted via event repository."""
    conn = _connection(tmp_path, "evt1.sqlite3")
    event_repo = SqliteV2JobEventRepository(conn)

    event_repo.save(
        job_id="job-evt-1",
        stage=1,
        event_type=F15EventType.GATE_OPENED.value,
        status="open",
        message="analysis_review gate opened for stage 1",
        payload={
            "gate_id": "gate-abc",
            "gate_checksum": "ab" * 32,
            "gate_phase": "analysis_review",
            "stage_index": 1,
        },
    )

    events = event_repo.list_by_job("job-evt-1")
    assert len(events) >= 1
    match = [e for e in events if e.type == F15EventType.GATE_OPENED.value]
    assert len(match) >= 1
    assert match[0].status == "open"


def test_f15_gate_resolved_event_can_be_saved(tmp_path: Path) -> None:
    """f15_gate_resolved event can be persisted."""
    conn = _connection(tmp_path, "evt2.sqlite3")
    event_repo = SqliteV2JobEventRepository(conn)

    event_repo.save(
        job_id="job-evt-2",
        stage=1,
        event_type=F15EventType.GATE_RESOLVED.value,
        status="resolved",
        message="analysis_review gate resolved with continue",
        payload={
            "gate_id": "gate-def",
            "gate_phase": "analysis_review",
            "decision": "continue",
            "resolved_by": "human",
        },
    )

    events = event_repo.list_by_job("job-evt-2")
    match = [e for e in events if e.type == F15EventType.GATE_RESOLVED.value]
    assert len(match) >= 1
    assert match[0].status == "resolved"


def test_gate_lifecycle_event_types_are_distinct() -> None:
    """GATE_LIFECYCLE_EVENTS are all distinct and f15-prefixed."""
    assert len(GATE_LIFECYCLE_EVENTS) == 3
    for event in GATE_LIFECYCLE_EVENTS:
        assert event.value.startswith("f15_")


def test_review_required_events_have_correct_types() -> None:
    """REVIEW_REQUIRED_EVENTS match the expected F15 event types."""
    assert F15EventType.ANALYSIS_REVIEW_REQUIRED in REVIEW_REQUIRED_EVENTS
    assert F15EventType.PLANNING_REVIEW_REQUIRED in REVIEW_REQUIRED_EVENTS
    assert F15EventType.APPROVAL_REVIEW_REQUIRED in REVIEW_REQUIRED_EVENTS
    assert F15EventType.REPAIR_REVIEW_REQUIRED in REVIEW_REQUIRED_EVENTS
    assert F15EventType.STAGE_COMPLETION_REVIEW_REQUIRED in REVIEW_REQUIRED_EVENTS
    assert len(REVIEW_REQUIRED_EVENTS) == 5


def test_gate_event_payload_has_no_raw_paths(tmp_path: Path) -> None:
    """Gate event payloads contain no absolute filesystem paths."""
    conn = _connection(tmp_path, "evt3.sqlite3")
    event_repo = SqliteV2JobEventRepository(conn)

    payload = {
        "gate_id": "gate-safe",
        "gate_checksum": "ab" * 32,
        "gate_phase": "stage_completion_review",
        "stage_index": 2,
    }

    event_repo.save(
        job_id="job-safe",
        stage=2,
        event_type=F15EventType.GATE_OPENED.value,
        status="open",
        message="Gate opened safely",
        payload=payload,
    )

    events = event_repo.list_by_job("job-safe")
    saved_payload = None
    for e in events:
        if e.type == F15EventType.GATE_OPENED.value:
            try:
                saved_payload = json.loads(e.payload_json) if isinstance(e.payload_json, str) else e.payload_json
            except (json.JSONDecodeError, TypeError):
                pass
            break

    assert saved_payload is not None
    payload_str = json.dumps(saved_payload)
    # No absolute paths (no /tmp, /home, /var, etc.)
    assert "/tmp" not in payload_str
    assert "/home" not in payload_str


def test_existing_clients_tolerate_gate_events(tmp_path: Path) -> None:
    """Gate events don't break existing event query patterns."""
    conn = _connection(tmp_path, "evt4.sqlite3")
    event_repo = SqliteV2JobEventRepository(conn)

    # Save a mix of existing and gate events
    event_repo.save(
        job_id="job-mixed",
        stage=1,
        event_type="stage_completed",
        status="completed",
        message="Stage completed",
        payload={},
    )
    event_repo.save(
        job_id="job-mixed",
        stage=1,
        event_type=F15EventType.GATE_OPENED.value,
        status="open",
        message="Gate opened",
        payload={"gate_id": "g1"},
    )

    # Both events should be retrievable
    events = event_repo.list_by_job("job-mixed")
    types = {e.type for e in events}
    assert "stage_completed" in types
    assert F15EventType.GATE_OPENED.value in types


def test_gate_events_can_be_queried_for_assistant_context(tmp_path: Path) -> None:
    """Gate events can be queried by job_id for assistant context building."""
    conn = _connection(tmp_path, "evt5.sqlite3")
    event_repo = SqliteV2JobEventRepository(conn)

    event_repo.save(
        job_id="job-assistant",
        stage=1,
        event_type=F15EventType.GATE_OPENED.value,
        status="open",
        message="analysis_review gate opened",
        payload={"gate_id": "g-assist", "gate_phase": "analysis_review"},
    )

    events = event_repo.list_by_job("job-assistant")
    gate_events = [e for e in events if e.type.startswith("f15_")]
    assert len(gate_events) >= 1
    assert gate_events[0].job_id == "job-assistant"
