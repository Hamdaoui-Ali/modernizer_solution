from __future__ import annotations

import sqlite3

import pytest

from migration_factory.control_tower.application.plan_amendments import PlanAmendmentService, PlanChange
from migration_factory.control_tower.application.plan_reviews import PlanReviewService
from migration_factory.control_tower.domain.errors import (
    PlanReviewChecksumMismatchError,
    PlanReviewConflictError,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import migrated_connection, seed_job


def _amendment_and_revision(connection: sqlite3.Connection) -> tuple[str, str, str]:
    service = PlanAmendmentService(lambda: SqliteUnitOfWork(connection))
    amendment = service.create_amendment(
        job_id="job-1",
        source_kind="manual",
        title="Plan request",
        summary="Plan only",
        notes=("safe",),
        changes=(PlanChange(stage_index=1, change_type="documentation", description="Clarify plan"),),
        created_by="tester",
    )
    revision = service.create_revision(
        amendment_id=amendment.amendment_id,
        source_kind="fake_provider",
        title="Proposal",
        summary="Candidate revision",
        notes=("safe",),
        changes=(PlanChange(stage_index=1, change_type="documentation", description="Clarify plan"),),
        created_by="tester",
    )
    return amendment.amendment_id, revision.revision_id, revision.payload_checksum


def _review_service(connection: sqlite3.Connection) -> PlanReviewService:
    return PlanReviewService(lambda: SqliteUnitOfWork(connection))


def test_unapproved_candidate_is_not_eligible(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_13_pending.db")
    seed_job(connection)
    _, revision_id, checksum = _amendment_and_revision(connection)

    status = _review_service(connection).get_review_status(revision_id)

    assert status.payload_checksum == checksum
    assert status.review_required is True
    assert status.eligible_for_downstream is False
    assert status.status == "pending_review"


def test_approved_candidate_becomes_eligible(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_13_approved.db")
    seed_job(connection)
    _, revision_id, checksum = _amendment_and_revision(connection)

    decision = _review_service(connection).record_review_decision(
        revision_id=revision_id,
        expected_checksum=checksum,
        decision="approved",
        review_summary="Safe approval",
        actor_type="user",
        actor_id="tester",
    )
    status = _review_service(connection).get_review_status(revision_id)

    assert decision.decision == "approved"
    assert status.eligible_for_downstream is True
    assert status.status == "approved"


def test_rejected_candidate_is_not_eligible(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_13_rejected.db")
    seed_job(connection)
    _, revision_id, checksum = _amendment_and_revision(connection)

    _review_service(connection).record_review_decision(
        revision_id=revision_id,
        expected_checksum=checksum,
        decision="rejected",
        review_summary="Needs revision",
        actor_type="user",
        actor_id="tester",
    )
    status = _review_service(connection).get_review_status(revision_id)

    assert status.eligible_for_downstream is False
    assert status.status == "rejected"
    assert status.decision == "rejected"


def test_stale_checksum_approval_is_rejected(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_13_stale.db")
    seed_job(connection)
    _, revision_id, _ = _amendment_and_revision(connection)

    with pytest.raises(PlanReviewChecksumMismatchError):
        _review_service(connection).record_review_decision(
            revision_id=revision_id,
            expected_checksum="stale-checksum",
            decision="approved",
            review_summary="Safe approval",
            actor_type="user",
            actor_id="tester",
        )


def test_duplicate_same_review_is_idempotent_and_conflicting_second_review_rejected(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_13_duplicate.db")
    seed_job(connection)
    _, revision_id, checksum = _amendment_and_revision(connection)
    service = _review_service(connection)

    first = service.record_review_decision(
        revision_id=revision_id,
        expected_checksum=checksum,
        decision="approved",
        review_summary="Safe approval",
        actor_type="user",
        actor_id="tester",
    )
    second = service.record_review_decision(
        revision_id=revision_id,
        expected_checksum=checksum,
        decision="approved",
        review_summary="Safe approval",
        actor_type="user",
        actor_id="tester",
    )

    assert second.review_decision_id == first.review_decision_id

    with pytest.raises(PlanReviewConflictError):
        service.record_review_decision(
            revision_id=revision_id,
            expected_checksum=checksum,
            decision="rejected",
            review_summary="Conflicting review",
            actor_type="user",
            actor_id="tester",
        )


def test_review_decisions_are_append_only(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_13_append_only.db")
    seed_job(connection)
    _, revision_id, checksum = _amendment_and_revision(connection)
    decision = _review_service(connection).record_review_decision(
        revision_id=revision_id,
        expected_checksum=checksum,
        decision="approved",
        review_summary="Safe approval",
        actor_type="user",
        actor_id="tester",
    )

    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="append-only"):
        connection.execute(
            "UPDATE v1_plan_review_decisions SET decision = 'rejected' WHERE review_decision_id = ?",
            (decision.review_decision_id,),
        )
