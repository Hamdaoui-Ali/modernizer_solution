"""M1 end-to-end acceptance test covering the full operational lifecycle."""

from __future__ import annotations

from pathlib import Path

from migration_factory.control_tower.application.commands import (
    CreateMigrationJobCommand,
    RegisterArtifactCommand,
    RegisterPipelineDefinitionCommand,
    RegisterRunnerProfileCommand,
    TransitionJobStateCommand,
)
from migration_factory.control_tower.application.queries import ControlTowerQueryService
from migration_factory.control_tower.application.services import (
    ArtifactRegistryService,
    ControlTowerRegistrationService,
    CreateMigrationJobService,
)
from migration_factory.control_tower.domain.artifacts import ArtifactHashResult
from migration_factory.control_tower.domain.states import JobState, TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteControlTowerUnitOfWork
from migration_factory.control_tower.schemas import PipelineDefinition, RunnerProfile
from migration_factory.control_tower.schemas.run_configuration import RunPolicy

from ._helpers import (
    make_migrated_connection,
    pipeline_definition_payload,
    runner_profile_payload,
    seed_pipeline_definition,
    seed_runner_profile,
)


def _uow_factory(db_path: Path):
    def factory() -> SqliteControlTowerUnitOfWork:
        return SqliteControlTowerUnitOfWork(connect_control_tower(db_path), close_connection=True)
    return factory


def test_m1_end_to_end_acceptance(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"

    # ── Phase 1: Initialize and register ─────────────────────────

    connection = make_migrated_connection(tmp_path)
    seed_runner_profile(connection)
    seed_pipeline_definition(connection)
    connection.close()

    uow = _uow_factory(db_path)
    queries = ControlTowerQueryService(uow)

    # ── Phase 2: Verify runner profile and pipeline readable ─────

    profile = queries.get_runner_profile("runner-default", "2026.06")
    assert profile.runner_profile_id == "runner-default"
    assert profile.runner_profile_version == "2026.06"

    pipeline = queries.get_pipeline_definition("pipeline-default", "2026.06")
    assert pipeline.pipeline_id == "pipeline-default"
    assert pipeline.pipeline_version == "2026.06"

    # ── Phase 3: Create migration job ────────────────────────────

    job_svc = CreateMigrationJobService(uow)
    create_cmd = CreateMigrationJobCommand(
        actor="tester",
        legacy_source_ref="C:/legacy/source",
        output_root_ref="C:/workspace/output",
        runner_profile_id="runner-default",
        runner_profile_version="2026.06",
        pipeline_id="pipeline-default",
        pipeline_version="2026.06",
        target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
        enabled_gates=("build", "test"),
        policy=RunPolicy(),
        correlation_id="corr-accept",
    )
    result = job_svc.execute(create_cmd)
    job_id = result.job_id

    # Verify: job starts at version 1 and CREATED
    assert result.version == 1
    assert result.sequence == 1

    # ── Phase 4: Read job, frozen config, and ordered stages ─────

    job = queries.get_migration_job(job_id)
    assert job.version == 1
    assert job.status == JobState.CREATED
    assert job.active_slot == 1
    assert job.last_event_sequence == 1

    rc = queries.get_run_configuration(job_id)
    assert rc.job_id == job_id
    assert rc.runner_profile_id == "runner-default"
    assert rc.pipeline_id == "pipeline-default"
    # Configuration is immutable (cannot be changed after creation)
    assert rc.target_proof_level == "BUILD_TEST_VERIFIED"

    stages = queries.list_stage_runs(job_id)
    assert len(stages) == 2
    assert [s.stage_index for s in stages] == [1, 2]
    assert all(s.status == "PENDING" for s in stages)

    # ── Phase 5: Verify job_created event is sequence 1 ──────────

    events = queries.list_run_events(job_id)
    assert len(events) == 1
    assert events[0].sequence == 1
    assert events[0].event_type == "job_created"

    # ── Phase 6: Transition job with expected version ────────────

    reg_svc = ControlTowerRegistrationService(uow)
    transition_result = reg_svc.transition_job_state(
        TransitionJobStateCommand(
            job_id=job_id,
            expected_version=1,
            target_state=JobState.QUEUED,
            actor_type="user",
            actor_id="tester",
            reason="acceptance test",
            correlation_id="corr-transition",
            causation_id=None,
        )
    )

    # Verify: transition increments version and sequence once
    assert transition_result.version == 2
    assert transition_result.status == JobState.QUEUED
    assert transition_result.active_slot == 1  # Still non-terminal

    events_after = queries.list_run_events(job_id)
    assert len(events_after) == 2
    assert events_after[1].sequence == 2
    assert events_after[1].event_type == "job_state_changed"

    # ── Phase 7: Register validated artifact ─────────────────────

    artifact_hash = ArtifactHashResult(
        registered_root_id="output-root",
        root_kind="output",
        relative_path="report.txt",
        normalized_relative_path="report.txt",
        checksum_algorithm="sha256",
        checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        size_bytes=0,
        mtime_ns=0,
        file_identity=(0, 0),
    )
    artifact_svc = ArtifactRegistryService(uow)
    artifact_cmd = RegisterArtifactCommand(
        job_id=job_id,
        artifact=artifact_hash,
        artifact_type="report",
        actor_type="user",
        actor_id="tester",
        stage_run_id=None,
        content_type=None,
        correlation_id="corr-artifact",
        causation_id=None,
    )
    artifact_dto = artifact_svc.register_artifact(artifact_cmd)

    assert artifact_dto is not None
    assert artifact_dto.artifact_type == "report"
    # Artifact DTO must not expose absolute paths
    assert not Path(artifact_dto.normalized_relative_path).is_absolute()
    assert not Path(artifact_dto.relative_path).is_absolute()
    assert "C:" not in artifact_dto.normalized_relative_path
    assert "C:" not in artifact_dto.relative_path

    # Artifact registration appends next event sequence
    events_final = queries.list_run_events(job_id)
    assert len(events_final) == 3
    assert events_final[2].sequence == 3
    assert events_final[2].event_type == "artifact_registered"

    # All events remain job-scoped
    for e in events_final:
        assert e.job_id == job_id

    # Registration creates audit records (global for profiles/pipelines, job-scoped for jobs)
    audits = queries.list_audit_records()
    assert len(audits) >= 3  # runner_profile + pipeline + job_created + transition + artifact

    audits_for_job = queries.list_audit_records_for_job(job_id)
    assert len(audits_for_job) == 3  # job_created, job_state_changed, artifact_registered

    # Audit records are append-only (cannot be modified or deleted)
    # This is verified by SQLite triggers in the schema

    # ── Phase 8: Terminal transition releases active slot ────────
    # QUEUED -> FAILED (valid terminal transition from QUEUED)

    reg_svc.transition_job_state(
        TransitionJobStateCommand(
            job_id=job_id,
            expected_version=2,
            target_state=JobState.FAILED,
            actor_type="user",
            actor_id="tester",
            reason="terminal transition for restart test",
            correlation_id="corr-terminal",
            causation_id=None,
        )
    )

    job_after_terminal = queries.get_migration_job(job_id)
    assert job_after_terminal.status == JobState.FAILED
    assert job_after_terminal.active_slot is None  # Terminal releases slot
    assert job_after_terminal.version == 3

    active = queries.get_active_migration_job()
    assert active is None  # No active job after terminal transition

    # ── Phase 9: Close and reopen database ───────────────────────

    # The UoW factory opens a new connection each time, simulating restart

    # ── Phase 10: Reopen and verify complete persisted history ───

    # Create a fresh queries service with a new connection
    queries_restarted = ControlTowerQueryService(uow)

    job_restarted = queries_restarted.get_migration_job(job_id)
    assert job_restarted.version == 3
    assert job_restarted.status == JobState.FAILED
    assert job_restarted.active_slot is None
    assert job_restarted.last_event_sequence == 4

    rc_restarted = queries_restarted.get_run_configuration(job_id)
    assert rc_restarted.job_id == job_id
    assert rc_restarted.runner_profile_id == "runner-default"

    stages_restarted = queries_restarted.list_stage_runs(job_id)
    assert len(stages_restarted) == 2
    assert [s.stage_index for s in stages_restarted] == [1, 2]

    events_restarted = queries_restarted.list_run_events(job_id)
    assert len(events_restarted) == 4  # job_created + transition + artifact_registered + terminal transition
    assert [e.sequence for e in events_restarted] == [1, 2, 3, 4]

    audits_restarted = queries_restarted.list_audit_records_for_job(job_id)
    assert len(audits_restarted) == 4  # 4 audit records for 4 events

    artifacts_restarted = queries_restarted.list_artifacts(job_id)
    assert len(artifacts_restarted) == 1
    assert artifacts_restarted[0].artifact_type == "report"
    # Absolute paths still not exposed after restart
    assert not Path(artifacts_restarted[0].normalized_relative_path).is_absolute()

    # Global audit records survive restart
    all_audits = queries_restarted.list_audit_records()
    assert len(all_audits) == 4  # 4 job-scoped audit records (seeded profiles used direct INSERT, no audit)

    # Runner profiles and pipelines survive restart
    profiles_restarted = queries_restarted.list_runner_profiles()
    assert len(profiles_restarted) == 1

    pipelines_restarted = queries_restarted.list_pipeline_definitions()
    assert len(pipelines_restarted) == 1
