"""Repair-cycle event ordering invariant (not run in investigation)."""

from __future__ import annotations


def assert_repair_cycle_order(events: list[dict]) -> None:
    seen_failure = False
    for event in events:
        event_type = event["event_type"]
        if event_type == "validation_failed":
            seen_failure = True
        if event_type == "next_repair_cycle_started":
            assert seen_failure, "new repair cycle cannot start before a new validation failure"


def test_new_repair_cycle_requires_new_failure() -> None:
    assert_repair_cycle_order([
        {"event_type": "proposal_ready"},
        {"event_type": "validation_failed"},
        {"event_type": "next_repair_cycle_started"},
    ])
