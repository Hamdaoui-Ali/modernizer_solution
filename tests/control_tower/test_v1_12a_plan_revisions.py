from __future__ import annotations

import sqlite3

import pytest

from migration_factory.control_tower.application.plan_amendments import (
    PlanAmendmentService,
    PlanChange,
)
from migration_factory.control_tower.domain.checksums import canonical_json_text, sha256_canonical_json
from migration_factory.control_tower.domain.errors import PlanRevisionConflictError, StorageIntegrityError
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import migrated_connection, seed_job


def _service(conn: sqlite3.Connection) -> PlanAmendmentService:
    return PlanAmendmentService(lambda: SqliteUnitOfWork(conn))


def _create_amendment(conn: sqlite3.Connection) -> str:
    service = _service(conn)
    amendment = service.create_amendment(
        job_id="job-1",
        source_kind="manual",
        title="Refine stage plan",
        summary="Safe planning only",
        changes=(
            PlanChange(
                stage_index=1,
                change_type="instruction",
                description="Refine stage 1 wording",
            ),
        ),
        created_by="tester",
    )
    return amendment.amendment_id


def test_valid_plan_revision_persists(tmp_path) -> None:
    conn = migrated_connection(tmp_path)
    seed_job(conn)
    service = _service(conn)
    amendment_id = _create_amendment(conn)

    revision = service.create_revision(
        amendment_id=amendment_id,
        source_kind="manual",
        title="Revision one",
        summary="Safe revision",
        changes=(
            PlanChange(
                stage_index=1,
                change_type="instruction",
                description="Refine wording",
            ),
        ),
        created_by="tester",
    )

    assert revision.revision_order == 1
    stored = conn.execute(
        "SELECT revision_order FROM v1_plan_revisions WHERE revision_id = ?",
        (revision.revision_id,),
    ).fetchone()
    assert stored is not None
    assert stored["revision_order"] == 1


def test_plan_revisions_are_ordered(tmp_path) -> None:
    conn = migrated_connection(tmp_path)
    seed_job(conn)
    service = _service(conn)
    amendment_id = _create_amendment(conn)
    first = service.create_revision(
        amendment_id=amendment_id,
        source_kind="manual",
        title="Revision one",
        summary="First",
        changes=(PlanChange(stage_index=1, change_type="instruction", description="First"),),
        created_by="tester",
    )
    second = service.create_revision(
        amendment_id=amendment_id,
        source_kind="manual",
        title="Revision two",
        summary="Second",
        changes=(PlanChange(stage_index=2, change_type="documentation", description="Second"),),
        created_by="tester",
    )
    assert first.revision_order == 1
    assert second.revision_order == 2


def test_revision_checksum_is_deterministic_and_canonical(tmp_path) -> None:
    conn = migrated_connection(tmp_path)
    seed_job(conn)
    service = _service(conn)
    amendment_id = _create_amendment(conn)
    changes = (
        PlanChange(stage_index=3, change_type="validation", description="Check output", rationale="Safety"),
    )
    revision = service.create_revision(
        amendment_id=amendment_id,
        source_kind="manual",
        title="Canonical",
        summary="Deterministic",
        notes=("one", "two"),
        changes=changes,
        created_by="tester",
    )
    expected_payload = {
        "title": "Canonical",
        "summary": "Deterministic",
        "notes": ("one", "two"),
        "changes": (
            {
                "stage_index": 3,
                "change_type": "validation",
                "description": "Check output",
                "rationale": "Safety",
            },
        ),
    }
    assert revision.payload_json == canonical_json_text(expected_payload)
    assert revision.payload_checksum == sha256_canonical_json(expected_payload)


def test_duplicate_conflicting_revision_order_is_rejected(tmp_path) -> None:
    conn = migrated_connection(tmp_path)
    seed_job(conn)
    service = _service(conn)
    amendment_id = _create_amendment(conn)
    service.create_revision(
        amendment_id=amendment_id,
        source_kind="manual",
        title="One",
        summary="One",
        changes=(PlanChange(stage_index=1, change_type="instruction", description="One"),),
        created_by="tester",
        revision_order=1,
    )

    with pytest.raises(StorageIntegrityError):
        service.create_revision(
            amendment_id=amendment_id,
            source_kind="manual",
            title="Duplicate",
            summary="Duplicate",
            changes=(PlanChange(stage_index=2, change_type="instruction", description="Two"),),
            created_by="tester",
            revision_order=1,
        )


def test_revision_cannot_be_mutated_after_decision(tmp_path) -> None:
    conn = migrated_connection(tmp_path)
    seed_job(conn)
    service = _service(conn)
    amendment_id = _create_amendment(conn)
    revision = service.create_revision(
        amendment_id=amendment_id,
        source_kind="manual",
        title="Accepted",
        summary="Accepted",
        changes=(PlanChange(stage_index=1, change_type="instruction", description="Accept"),),
        created_by="tester",
        revision_state="accepted",
    )

    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="append-only"):
        conn.execute(
            "UPDATE v1_plan_revisions SET payload_checksum = 'bad' WHERE revision_id = ?",
            (revision.revision_id,),
        )


def test_terminal_revision_blocks_further_revisions(tmp_path) -> None:
    conn = migrated_connection(tmp_path)
    seed_job(conn)
    service = _service(conn)
    amendment_id = _create_amendment(conn)
    service.create_revision(
        amendment_id=amendment_id,
        source_kind="manual",
        title="Accepted",
        summary="Done",
        changes=(PlanChange(stage_index=1, change_type="instruction", description="Done"),),
        created_by="tester",
        revision_state="accepted",
    )

    with pytest.raises(PlanRevisionConflictError, match="terminal"):
        service.create_revision(
            amendment_id=amendment_id,
            source_kind="manual",
            title="Late revision",
            summary="Should fail",
            changes=(PlanChange(stage_index=2, change_type="documentation", description="Late"),),
            created_by="tester",
        )
