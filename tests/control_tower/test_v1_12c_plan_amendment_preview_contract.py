from __future__ import annotations

from migration_factory.control_tower.application.plan_amendments import PlanAmendmentService, PlanChange
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import migrated_connection, seed_job


def _service(connection):
    return PlanAmendmentService(lambda: SqliteUnitOfWork(connection))


def test_preview_returns_deterministic_output_for_same_input(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_12c_contract.db")
    seed_job(connection)
    service = _service(connection)

    first = service.preview_amendment(
        job_id="job-1",
        source_kind="manual",
        title="Safe V1 plan",
        summary="Planning only",
        notes=("safe",),
        changes=(PlanChange(stage_index=1, change_type="documentation", description="Clarify plan text"),),
    )
    second = service.preview_amendment(
        job_id="job-1",
        source_kind="manual",
        title="Safe V1 plan",
        summary="Planning only",
        notes=("safe",),
        changes=(PlanChange(stage_index=1, change_type="documentation", description="Clarify plan text"),),
    )

    assert first.payload_checksum == second.payload_checksum
    assert first.redacted_summary == second.redacted_summary
    assert first.validation_status == second.validation_status == "PASS"
    assert first.preview_persisted is False
    assert first.preview_applied is False


def test_preview_output_is_redacted(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_12c_redaction.db")
    seed_job(connection)
    preview = _service(connection).preview_amendment(
        job_id="job-1",
        source_kind="manual",
        title=r"See C:\Users\secret\file.txt",
        summary="TOKEN=abc123 secret=xyz",
        notes=("safe",),
        changes=(PlanChange(stage_index=1, change_type="documentation", description="Clarify plan text"),),
    )

    blob = str(preview.redacted_summary) + preview.title + preview.summary
    assert r"C:\Users\secret\file.txt" not in blob
    assert "TOKEN=abc123" not in blob
    assert "secret=xyz" not in blob
    assert "[redacted" in blob


def test_preview_does_not_mutate_route_or_stage_chain_invariants(tmp_path) -> None:
    connection = migrated_connection(tmp_path, "v1_12c_invariants.db")
    seed_job(connection)
    before = connection.execute(
        "SELECT pipeline_id, pipeline_version, runner_profile_id, runner_profile_version FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()

    preview = _service(connection).preview_amendment(
        job_id="job-1",
        source_kind="manual",
        title="Safe V1 plan",
        summary="Planning only",
        notes=("safe",),
        changes=(PlanChange(stage_index=1, change_type="documentation", description="Clarify plan text"),),
    )

    after = connection.execute(
        "SELECT pipeline_id, pipeline_version, runner_profile_id, runner_profile_version FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()
    assert tuple(before) == tuple(after)
    assert preview.affected_stage_indexes == (1,)
    assert preview.change_types == ("documentation",)
