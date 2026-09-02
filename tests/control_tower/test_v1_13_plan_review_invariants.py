from __future__ import annotations

from migration_factory.control_tower.application.plan_amendments import PlanAmendmentService, PlanChange
from migration_factory.control_tower.application.plan_reviews import PlanReviewService
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import migrated_connection, seed_job


def _create_revision(connection):
    amendments = PlanAmendmentService(lambda: SqliteUnitOfWork(connection))
    amendment = amendments.create_amendment(
        job_id="job-1",
        source_kind="manual",
        title="Plan request",
        summary="Plan only",
        notes=("safe",),
        changes=(PlanChange(stage_index=1, change_type="documentation", description="Clarify plan"),),
        created_by="tester",
    )
    revision = amendments.create_revision(
        amendment_id=amendment.amendment_id,
        source_kind="fake_provider",
        title="Proposal",
        summary="Candidate revision",
        notes=("safe",),
        changes=(PlanChange(stage_index=1, change_type="documentation", description="Clarify plan"),),
        created_by="tester",
    )
    return revision


def test_route_and_stage_chain_invariants_unchanged_by_review(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_13_invariants.db")
    seed_job(connection)
    revision = _create_revision(connection)
    before_job = connection.execute(
        "SELECT pipeline_id, pipeline_version, runner_profile_id, runner_profile_version, status, version, last_event_sequence FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()

    PlanReviewService(lambda: SqliteUnitOfWork(connection)).record_review_decision(
        revision_id=revision.revision_id,
        expected_checksum=revision.payload_checksum,
        decision="approved",
        review_summary="Safe approval",
        actor_type="user",
        actor_id="tester",
    )

    after_job = connection.execute(
        "SELECT pipeline_id, pipeline_version, runner_profile_id, runner_profile_version, status, version, last_event_sequence FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()
    assert tuple(before_job) == tuple(after_job)


def test_unreviewed_and_rejected_revisions_do_not_unblock_downstream(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_13_blocked.db")
    seed_job(connection)
    revision = _create_revision(connection)
    service = PlanReviewService(lambda: SqliteUnitOfWork(connection))

    pending = service.get_review_status(revision.revision_id)
    assert pending.eligible_for_downstream is False

    service.record_review_decision(
        revision_id=revision.revision_id,
        expected_checksum=revision.payload_checksum,
        decision="rejected",
        review_summary="Needs work",
        actor_type="user",
        actor_id="tester",
    )
    rejected = service.get_review_status(revision.revision_id)
    assert rejected.eligible_for_downstream is False
