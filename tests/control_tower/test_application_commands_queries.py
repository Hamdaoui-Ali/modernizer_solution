"""Tests for Control Tower read-only queries and query side-effect safety."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.commands import (
    CreateMigrationJobCommand,
    RegisterPipelineDefinitionCommand,
    RegisterRunnerProfileCommand,
    TransitionJobStateCommand,
)
from migration_factory.control_tower.application.dto import (
    ArtifactDto,
    AuditRecordDto,
    MigrationJobDto,
    RunConfigurationDto,
    RunEventDto,
    RunnerProfileDto,
    StageRunDto,
)
from migration_factory.control_tower.application.queries import ControlTowerQueryService
from migration_factory.control_tower.application.services import (
    ControlTowerRegistrationService,
    CreateMigrationJobService,
)
from migration_factory.control_tower.domain.errors import NotFoundError
from migration_factory.control_tower.domain.states import JobState, TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteControlTowerUnitOfWork
from migration_factory.control_tower.schemas.run_configuration import RunPolicy

from ._helpers import (
    make_migrated_connection,
    pipeline_definition_payload,
    runner_profile_payload,
    seed_pipeline_definition,
    seed_runner_profile,
)


def _seed_everything(connection: sqlite3.Connection) -> None:
    seed_runner_profile(connection)
    seed_pipeline_definition(connection)


def _query_service(connection: sqlite3.Connection) -> ControlTowerQueryService:
    def factory() -> SqliteControlTowerUnitOfWork:
        return SqliteControlTowerUnitOfWork(connection)
    return ControlTowerQueryService(factory)


def _create_job(connection: sqlite3.Connection) -> tuple[str, CreateMigrationJobService]:
    def factory() -> SqliteControlTowerUnitOfWork:
        return SqliteControlTowerUnitOfWork(connection)
    svc = CreateMigrationJobService(factory)
    cmd = CreateMigrationJobCommand(
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
        correlation_id="corr-1",
    )
    result = svc.execute(cmd)
    return result.job_id, svc


# ── GetMigrationJob ──────────────────────────────────────────────

def test_get_migration_job_returns_typed_dto(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    _seed_everything(connection)
    job_id, _ = _create_job(connection)
    queries = _query_service(connection)

    job = queries.get_migration_job(job_id)

    assert isinstance(job, MigrationJobDto)
    assert job.job_id == job_id
    assert job.version == 1
    assert job.status == JobState.CREATED
    assert job.active_slot == 1
    assert job.last_event_sequence == 1

    connection.close()


def test_get_migration_job_missing_raises_not_found(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    queries = _query_service(connection)

    with pytest.raises(NotFoundError):
        queries.get_migration_job("nonexistent-job")

    connection.close()


# ── GetActiveMigrationJob ────────────────────────────────────────

def test_get_active_migration_job_returns_active(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    _seed_everything(connection)
    job_id, _ = _create_job(connection)
    queries = _query_service(connection)

    active = queries.get_active_migration_job()

    assert active is not None
    assert active.job_id == job_id
    assert active.status == JobState.CREATED
    assert isinstance(active, MigrationJobDto)

    connection.close()


def test_get_active_migration_job_returns_none_when_empty(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    queries = _query_service(connection)

    active = queries.get_active_migration_job()

    assert active is None

    connection.close()


# ── ListMigrationJobs ────────────────────────────────────────────

def test_list_migration_jobs_returns_ordered_list(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    _seed_everything(connection)
    job_id_1, _ = _create_job(connection)
    # Must transition first job to terminal to create second
    queries = _query_service(connection)
    reg_svc = ControlTowerRegistrationService(
        lambda: SqliteControlTowerUnitOfWork(connection)
    )
    reg_svc.transition_job_state(
        TransitionJobStateCommand(
            job_id=job_id_1,
            expected_version=1,
            target_state=JobState.CANCELLED,
            actor_type="user",
            actor_id="tester",
            reason="test",
            correlation_id="corr-term",
            causation_id=None,
        )
    )
    job_id_2, _ = _create_job(connection)
    # Transition second to terminal as well
    reg_svc.transition_job_state(
        TransitionJobStateCommand(
            job_id=job_id_2,
            expected_version=1,
            target_state=JobState.CANCELLED,
            actor_type="user",
            actor_id="tester",
            reason="test",
            correlation_id="corr-term-2",
            causation_id=None,
        )
    )

    jobs = queries.list_migration_jobs()

    assert len(jobs) >= 2
    for j in jobs:
        assert isinstance(j, MigrationJobDto)
    # ORDER BY created_at, job_id ensures deterministic ordering
    assert jobs[0].job_id == job_id_1

    connection.close()


# ── GetRunConfiguration ──────────────────────────────────────────

def test_get_run_configuration_returns_typed_dto(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    _seed_everything(connection)
    job_id, _ = _create_job(connection)
    queries = _query_service(connection)

    rc = queries.get_run_configuration(job_id)

    assert isinstance(rc, RunConfigurationDto)
    assert rc.job_id == job_id
    assert rc.runner_profile_id == "runner-default"
    assert rc.pipeline_id == "pipeline-default"
    assert rc.target_proof_level == "BUILD_TEST_VERIFIED"
    assert isinstance(rc.enabled_gates, tuple)
    assert isinstance(rc.policy, dict)

    connection.close()


def test_get_run_configuration_missing_raises_not_found(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    queries = _query_service(connection)

    with pytest.raises(NotFoundError):
        queries.get_run_configuration("nonexistent-job")

    connection.close()


# ── ListStageRuns ────────────────────────────────────────────────

def test_list_stage_runs_returns_ordered_dtos(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    _seed_everything(connection)
    job_id, _ = _create_job(connection)
    queries = _query_service(connection)

    stages = queries.list_stage_runs(job_id)

    assert len(stages) == 2
    for s in stages:
        assert isinstance(s, StageRunDto)
    assert [s.stage_index for s in stages] == [1, 2]
    assert [s.status for s in stages] == ["PENDING", "PENDING"]
    assert stages[0].stage_id == "analyze"
    assert isinstance(stages[0].input_source, dict)

    connection.close()


# ── ListRunEvents ────────────────────────────────────────────────

def test_list_run_events_returns_ordered_dtos(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    _seed_everything(connection)
    job_id, _ = _create_job(connection)
    queries = _query_service(connection)

    events = queries.list_run_events(job_id)

    assert len(events) == 1
    e = events[0]
    assert isinstance(e, RunEventDto)
    assert e.event_type == "job_created"
    assert e.sequence == 1
    assert e.job_id == job_id

    connection.close()


# ── ListArtifacts ────────────────────────────────────────────────

def test_list_artifacts_returns_ordered_dtos(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    _seed_everything(connection)
    _create_job(connection)
    queries = _query_service(connection)

    artifacts = queries.list_artifacts("nonexistent-job")

    assert isinstance(artifacts, tuple)
    assert len(artifacts) == 0

    connection.close()


# ── ListAuditRecords ─────────────────────────────────────────────

def test_list_audit_records_returns_ordered_dtos(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    _seed_everything(connection)
    _create_job(connection)
    queries = _query_service(connection)

    audits = queries.list_audit_records()

    assert len(audits) >= 1
    for a in audits:
        assert isinstance(a, AuditRecordDto)

    connection.close()


def test_list_audit_records_for_job_returns_ordered_dtos(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    _seed_everything(connection)
    job_id, _ = _create_job(connection)
    queries = _query_service(connection)

    audits = queries.list_audit_records_for_job(job_id)

    assert len(audits) == 1
    a = audits[0]
    assert isinstance(a, AuditRecordDto)
    assert a.action == "job_created"
    assert a.job_id == job_id

    connection.close()


def test_list_audit_records_for_job_empty_for_unknown(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    queries = _query_service(connection)

    audits = queries.list_audit_records_for_job("nonexistent-job")

    assert isinstance(audits, tuple)
    assert len(audits) == 0

    connection.close()


# ── GetRunnerProfile / ListRunnerProfiles ────────────────────────

def test_get_runner_profile_returns_typed_dto(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    seed_runner_profile(connection)
    queries = _query_service(connection)

    profile = queries.get_runner_profile("runner-default", "2026.06")

    assert isinstance(profile, RunnerProfileDto)
    assert profile.runner_profile_id == "runner-default"
    assert profile.runner_profile_version == "2026.06"
    assert profile.display_name == "Default runner"

    connection.close()


def test_get_runner_profile_missing_raises_not_found(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    queries = _query_service(connection)

    with pytest.raises(NotFoundError):
        queries.get_runner_profile("nonexistent", "v1")

    connection.close()


def test_list_runner_profiles_returns_ordered_dtos(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    seed_runner_profile(connection)
    queries = _query_service(connection)

    profiles = queries.list_runner_profiles()

    assert len(profiles) == 1
    assert isinstance(profiles[0], RunnerProfileDto)

    connection.close()


# ── GetPipelineDefinition / ListPipelineDefinitions ──────────────

def test_get_pipeline_definition_returns_typed_dto(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    seed_pipeline_definition(connection)
    queries = _query_service(connection)

    pipeline = queries.get_pipeline_definition("pipeline-default", "2026.06")

    assert pipeline.pipeline_id == "pipeline-default"
    assert pipeline.pipeline_version == "2026.06"

    connection.close()


def test_get_pipeline_definition_missing_raises_not_found(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    queries = _query_service(connection)

    with pytest.raises(NotFoundError):
        queries.get_pipeline_definition("nonexistent", "v1")

    connection.close()


def test_list_pipeline_definitions_returns_ordered_dtos(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    seed_pipeline_definition(connection)
    queries = _query_service(connection)

    pipelines = queries.list_pipeline_definitions()

    assert len(pipelines) == 1
    assert pipelines[0].pipeline_id == "pipeline-default"

    connection.close()


# ── Queries produce no side effects ──────────────────────────────

def test_queries_never_create_events(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    _seed_everything(connection)
    job_id, _ = _create_job(connection)
    queries = _query_service(connection)

    initial_event_count = (
        connection.execute("SELECT COUNT(*) FROM run_events").fetchone()[0]
    )

    queries.get_migration_job(job_id)
    queries.get_active_migration_job()
    queries.list_migration_jobs()
    queries.get_run_configuration(job_id)
    queries.list_stage_runs(job_id)
    queries.list_run_events(job_id)
    queries.list_artifacts(job_id)
    queries.list_audit_records()
    queries.list_audit_records_for_job(job_id)
    queries.list_runner_profiles()
    queries.list_pipeline_definitions()

    final_event_count = (
        connection.execute("SELECT COUNT(*) FROM run_events").fetchone()[0]
    )
    assert final_event_count == initial_event_count

    connection.close()


def test_queries_never_create_audit_records(tmp_path: Path) -> None:
    connection = make_migrated_connection(tmp_path)
    _seed_everything(connection)
    job_id, _ = _create_job(connection)
    queries = _query_service(connection)

    initial_audit_count = (
        connection.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0]
    )

    queries.get_migration_job(job_id)
    queries.get_active_migration_job()
    queries.list_migration_jobs()
    queries.get_run_configuration(job_id)
    queries.list_stage_runs(job_id)
    queries.list_run_events(job_id)
    queries.list_artifacts(job_id)
    queries.list_audit_records()
    queries.list_audit_records_for_job(job_id)
    queries.list_runner_profiles()
    queries.list_pipeline_definitions()

    final_audit_count = (
        connection.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0]
    )
    assert final_audit_count == initial_audit_count

    connection.close()
