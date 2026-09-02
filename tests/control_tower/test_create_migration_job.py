from __future__ import annotations

from pathlib import Path

import pytest

from migration_factory.control_tower.application.commands import CreateMigrationJobCommand
from migration_factory.control_tower.application.services import CreateMigrationJobService
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.errors import CompatibilityError, NotFoundError
from migration_factory.control_tower.domain.states import JobState, TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteControlTowerUnitOfWork
from migration_factory.control_tower.schemas.run_configuration import (
    RunConfiguration,
    RunPolicy,
)

from ._helpers import (
    canonical_json,
    make_migrated_connection,
    pipeline_definition_payload,
    runner_profile_payload,
    seed_pipeline_definition,
    seed_runner_profile,
    sha256_json,
)


def test_create_migration_job_writes_everything_atomically(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    seed_runner_profile(connection)
    seed_pipeline_definition(connection)
    connection.close()

    service = _service_for(db_path)
    command = _create_command()
    result = service.execute(command)

    with connect_control_tower(db_path) as verification_connection:
        job_row = verification_connection.execute(
            """
            SELECT job_id, version, status, active_slot, last_event_sequence,
                   runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
                   target_proof_level, achieved_proof_level, legacy_source_ref, output_root_ref,
                   created_at, updated_at, started_at, finished_at, created_by
            FROM migration_jobs
            WHERE job_id = ?
            """,
            (result.job_id,),
        ).fetchone()
        assert job_row is not None
        assert job_row["version"] == 1
        assert job_row["status"] == JobState.CREATED.value
        assert job_row["active_slot"] == 1
        assert job_row["last_event_sequence"] == 1
        assert job_row["target_proof_level"] == TargetProofLevel.BUILD_TEST_VERIFIED.value
        assert job_row["achieved_proof_level"] is None
        assert job_row["legacy_source_ref"] == command.legacy_source_ref
        assert job_row["output_root_ref"] == command.output_root_ref

        run_configuration_row = verification_connection.execute(
            """
            SELECT run_configuration_id, job_id, schema_version, runner_profile_id,
                   runner_profile_version, pipeline_id, pipeline_version, target_proof_level,
                   enabled_gates_json, policy_json, payload_json, payload_checksum, created_at
            FROM run_configurations
            WHERE job_id = ?
            """,
            (result.job_id,),
        ).fetchone()
        assert run_configuration_row is not None

        expected_run_configuration = {
            "schema_version": "1.0.0",
            "run_configuration_id": result.run_configuration_id,
            "job_id": result.job_id,
            "runner_profile_id": command.runner_profile_id,
            "runner_profile_version": command.runner_profile_version,
            "pipeline_id": command.pipeline_id,
            "pipeline_version": command.pipeline_version,
            "source_profile": None,
            "target_profile": None,
            "target_proof_level": command.target_proof_level.value,
            "enabled_gates": list(command.enabled_gates),
            "policy": {
                "continue_after_warning": False,
                "enable_build_repair": True,
                "enable_runtime_gate": False,
                "enable_endpoint_gate": False,
                "enable_llm_repair_proposal": True,
                "max_repair_attempts": 3,
                "repair_scope": "build_only",
                "stage_continuation_policy": "auto_on_green",
            },
        }
        assert run_configuration_row["payload_json"] == canonical_json(expected_run_configuration)
        assert run_configuration_row["payload_checksum"] == sha256_json(expected_run_configuration)

        stage_rows = verification_connection.execute(
            """
            SELECT stage_index, stage_id, status, input_source_json
            FROM stage_runs
            WHERE job_id = ?
            ORDER BY stage_index
            """,
            (result.job_id,),
        ).fetchall()
        assert [row["stage_index"] for row in stage_rows] == [1, 2]
        assert [row["status"] for row in stage_rows] == ["PENDING", "PENDING"]
        assert stage_rows[0]["input_source_json"] == canonical_json({"kind": "legacy_source", "previous_stage_index": None})
        assert stage_rows[1]["input_source_json"] == canonical_json({"kind": "previous_stage", "previous_stage_index": 1})

        event_row = verification_connection.execute(
            """
            SELECT sequence, event_type, actor_type, actor_id, correlation_id,
                   causation_id, payload_json, payload_checksum
            FROM run_events
            WHERE job_id = ?
            """,
            (result.job_id,),
        ).fetchone()
        assert event_row is not None
        assert event_row["sequence"] == 1
        assert event_row["event_type"] == "job_created"
        assert event_row["actor_type"] == "user"
        assert event_row["actor_id"] == command.actor
        assert event_row["correlation_id"] == command.correlation_id

        audit_row = verification_connection.execute(
            """
            SELECT action, actor_type, actor_id, prior_state, new_state, job_version,
                   correlation_id, causation_id, payload_json
            FROM audit_records
            WHERE job_id = ?
            """,
            (result.job_id,),
        ).fetchone()
        assert audit_row is not None
        assert audit_row["action"] == "job_created"
        assert audit_row["actor_type"] == "user"
        assert audit_row["actor_id"] == command.actor
        assert audit_row["prior_state"] is None
        assert audit_row["new_state"] == JobState.CREATED.value
        assert audit_row["job_version"] == 1
        assert audit_row["causation_id"] == result.event_id

    assert result.version == 1
    assert result.sequence == 1
    assert len(result.stage_run_ids) == 2


def test_missing_runner_profile_raises_not_found(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    seed_pipeline_definition(connection)
    connection.close()

    service = _service_for(db_path)

    with pytest.raises(NotFoundError):
        service.execute(_create_command())

    with connect_control_tower(db_path) as verification_connection:
        counts = {
            table: verification_connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("migration_jobs", "run_configurations", "stage_runs", "run_events", "audit_records")
        }
    assert counts == {
        "migration_jobs": 0,
        "run_configurations": 0,
        "stage_runs": 0,
        "run_events": 0,
        "audit_records": 0,
    }


def test_create_migration_job_can_persist_manual_stage_continuation_policy(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    seed_runner_profile(connection)
    seed_pipeline_definition(connection)
    connection.close()

    service = _service_for(db_path)
    command = _create_command(policy=RunPolicy.f15_manual())
    result = service.execute(command)

    with connect_control_tower(db_path) as verification_connection:
        row = verification_connection.execute(
            """
            SELECT policy_json, payload_json
            FROM run_configurations
            WHERE job_id = ?
            """,
            (result.job_id,),
        ).fetchone()

    assert row is not None
    assert row["policy_json"] == canonical_json(
        {
            "continue_after_warning": False,
            "enable_runtime_gate": False,
            "enable_endpoint_gate": False,
            "enable_build_repair": True,
            "enable_llm_repair_proposal": True,
            "max_repair_attempts": 3,
            "repair_scope": "build_only",
            "stage_continuation_policy": "manual",
        }
    )
    assert '"stage_continuation_policy":"manual"' in row["payload_json"]


def test_run_configuration_rejects_invalid_profile_pair_for_job_creation() -> None:
    with pytest.raises(ValueError, match="target profile must be higher"):
        RunConfiguration(
            schema_version="1.0.0",
            run_configuration_id="run-config-invalid",
            job_id="job-invalid",
            runner_profile_id="runner-default",
            runner_profile_version="2026.06",
            pipeline_id="pipeline-default",
            pipeline_version="2026.06",
            target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
            enabled_gates=(),
            policy=RunPolicy(),
            source_profile="springboot-3.5-java21",
            target_profile="springboot-3.5-java17",
        )


def test_pipeline_runner_compatibility_failure_rolls_back_everything(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    _seed_incompatible_runner_profile(connection)
    _seed_incompatible_pipeline(connection)
    connection.close()

    service = _service_for(db_path)

    with pytest.raises(CompatibilityError):
        service.execute(_create_command())

    with connect_control_tower(db_path) as verification_connection:
        counts = {
            table: verification_connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("migration_jobs", "run_configurations", "stage_runs", "run_events", "audit_records")
        }
    assert counts == {
        "migration_jobs": 0,
        "run_configurations": 0,
        "stage_runs": 0,
        "run_events": 0,
        "audit_records": 0,
    }


def _service_for(db_path: Path) -> CreateMigrationJobService:
    def factory() -> SqliteControlTowerUnitOfWork:
        return SqliteControlTowerUnitOfWork(connect_control_tower(db_path), close_connection=True)

    return CreateMigrationJobService(factory)


def _create_command(policy: RunPolicy | None = None) -> CreateMigrationJobCommand:
    return CreateMigrationJobCommand(
        actor="tester",
        legacy_source_ref="C:/legacy/source",
        output_root_ref="C:/workspace/output",
        runner_profile_id="runner-default",
        runner_profile_version="2026.06",
        pipeline_id="pipeline-default",
        pipeline_version="2026.06",
        target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
        enabled_gates=("build", "test"),
        policy=policy or RunPolicy(),
        correlation_id="corr-1",
    )


def _seed_incompatible_runner_profile(connection) -> None:
    payload = runner_profile_payload()
    payload["jdks"] = (
        {
            "jdk_id": "jdk-17",
            "java_home": "C:/jdks/temurin-17",
            "expected_major": 17,
            "role": "source",
        },
    )
    connection.execute(
        """
        INSERT INTO runner_profiles (
            runner_profile_id, runner_profile_version, display_name, schema_version,
            payload_json, payload_checksum, created_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["runner_profile_id"],
            payload["runner_profile_version"],
            payload["display_name"],
            payload["schema_version"],
            canonical_json(payload),
            sha256_json(payload),
            utc_now_text(),
            "tester",
        ),
    )


def _seed_incompatible_pipeline(connection) -> None:
    payload = pipeline_definition_payload()
    payload["stages"] = (
        {
            "stage_index": 1,
            "stage_id": "analyze",
            "profile_id": "analysis-profile",
            "command_jdk": "jdk-99",
            "input_source": {"kind": "legacy_source"},
            "continuation_policy_id": "default",
            "target": {"spring_boot": "3.5.14", "java": 17},
        },
    )
    connection.execute(
        """
        INSERT INTO pipeline_definitions (
            pipeline_id, pipeline_version, display_name, schema_version,
            graph_version, graph_state_schema_version, payload_json, payload_checksum,
            created_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["pipeline_id"],
            payload["pipeline_version"],
            payload["display_name"],
            payload["schema_version"],
            payload["graph_version"],
            payload["graph_state_schema_version"],
            canonical_json(payload),
            sha256_json(payload),
            utc_now_text(),
            "tester",
        ),
    )
