"""Focused tests: V1 pipeline/runner registration via service layer (V1-00B).

Verifies that the canonical V1 pipeline definition and runner profile can be
registered through ControlTowerRegistrationService, that Boot 4 and 3.5.14 are
not present as execution targets, and that browser-controllable fields (raw
paths, Maven goals, shell commands, working directories, model deployments) are
rejected.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.commands import (
    RegisterPipelineDefinitionCommand,
    RegisterRunnerProfileCommand,
)
from migration_factory.control_tower.application.services import ControlTowerRegistrationService
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.v1_fixtures import make_v1_pipeline_definition, make_v1_runner_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(connection: sqlite3.Connection) -> ControlTowerRegistrationService:
    return ControlTowerRegistrationService(lambda: SqliteUnitOfWork(connection))


def _migrated_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    apply_pending_migrations(connection)
    return connection


def _pipeline_command(
    pipeline: dict | None = None,
    actor_id: str = "tester",
) -> RegisterPipelineDefinitionCommand:
    return RegisterPipelineDefinitionCommand(
        pipeline=make_v1_pipeline_definition() if pipeline is None else pipeline,
        actor_type="user",
        actor_id=actor_id,
    )


def _runner_command(
    profile: dict | None = None,
    actor_id: str = "tester",
) -> RegisterRunnerProfileCommand:
    return RegisterRunnerProfileCommand(
        profile=make_v1_runner_profile() if profile is None else profile,
        actor_type="user",
        actor_id=actor_id,
    )


# ---------------------------------------------------------------------------
# V1 pipeline registration
# ---------------------------------------------------------------------------


def test_register_v1_pipeline_definition_successfully(tmp_path: Path) -> None:
    """V1 pipeline definition can be registered through the service layer."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        dto = svc.register_pipeline_definition(_pipeline_command())

        assert dto.pipeline_id == "springboot-216-to-356-java21-three-stage"
        assert dto.pipeline_version == "2026.06"
        assert dto.display_name == "Spring Boot 2.1.6 → 3.5.6 · Java 21 · Three-Stage"
        assert dto.schema_version == "1.0.0"
        assert dto.graph_version == "1.0"
        assert dto.graph_state_schema_version == "1.0"
        assert dto.created_by == "tester"
    finally:
        connection.close()


def test_register_v1_pipeline_idempotent(tmp_path: Path) -> None:
    """Re-registering the same pipeline payload is idempotent."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        first = svc.register_pipeline_definition(_pipeline_command(actor_id="creator"))
        second = svc.register_pipeline_definition(_pipeline_command(actor_id="other"))

        assert second == first
        assert second.created_by == "creator"
    finally:
        connection.close()


def test_register_v1_pipeline_has_v1_stage_targets(tmp_path: Path) -> None:
    """Registered V1 pipeline contains the correct stage targets."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        dto = svc.register_pipeline_definition(_pipeline_command())

        stages = dto.payload.get("stages", ())
        assert len(stages) == 3

        # Stage 1: Java 11 / Boot 2.7.18
        assert stages[0]["stage_index"] == 1
        assert stages[0]["command_jdk"] == "java11"
        assert stages[0]["target"]["spring_boot"] == "2.7.18"
        assert stages[0]["target"]["java"] == 11
        assert stages[0]["input_source"]["kind"] == "legacy_source"

        # Stage 2: Java 17 / Boot 3.5.6 from Stage 1
        assert stages[1]["stage_index"] == 2
        assert stages[1]["command_jdk"] == "java17"
        assert stages[1]["target"]["spring_boot"] == "3.5.6"
        assert stages[1]["target"]["java"] == 17
        assert stages[1]["input_source"]["kind"] == "previous_stage"
        assert stages[1]["input_source"]["previous_stage_index"] == 1

        # Stage 3: Java 21 / Boot 3.5.6 from Stage 2
        assert stages[2]["stage_index"] == 3
        assert stages[2]["command_jdk"] == "java21"
        assert stages[2]["target"]["spring_boot"] == "3.5.6"
        assert stages[2]["target"]["java"] == 21
        assert stages[2]["input_source"]["kind"] == "previous_stage"
        assert stages[2]["input_source"]["previous_stage_index"] == 2
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Boot 4 and 3.5.14 must NOT be present as execution targets
# ---------------------------------------------------------------------------


def test_register_v1_pipeline_no_boot4_in_targets(tmp_path: Path) -> None:
    """Boot 4 must not appear as a target in any V1 stage."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        dto = svc.register_pipeline_definition(_pipeline_command())

        for stage in dto.payload.get("stages", ()):
            target_boot = stage.get("target", {}).get("spring_boot", "")
            assert "4." not in target_boot, (
                f"Boot 4 found in stage {stage['stage_index']}: {target_boot}"
            )
    finally:
        connection.close()


def test_register_v1_pipeline_no_3514_in_targets(tmp_path: Path) -> None:
    """3.5.14 must not appear as a target in any V1 stage."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        dto = svc.register_pipeline_definition(_pipeline_command())

        for stage in dto.payload.get("stages", ()):
            assert stage["target"]["spring_boot"] != "3.5.14", (
                f"3.5.14 found in stage {stage['stage_index']}"
            )
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# V1 runner profile registration
# ---------------------------------------------------------------------------


def test_register_v1_runner_profile_successfully(tmp_path: Path) -> None:
    """V1 runner profile can be registered through the service layer."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        dto = svc.register_runner_profile(_runner_command())

        assert dto.runner_profile_id == "runner-v1"
        assert dto.runner_profile_version == "2026.06"
        assert dto.display_name == "V1 runner"
        assert dto.schema_version == "1.0.0"
        assert dto.created_by == "tester"
    finally:
        connection.close()


def test_register_v1_runner_profile_has_three_jdks(tmp_path: Path) -> None:
    """Registered V1 runner profile contains three JDK entries for java11, java17, java21."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        dto = svc.register_runner_profile(_runner_command())

        jdks = dto.payload.get("jdks", ())
        jdk_ids = {j["jdk_id"] for j in jdks}
        assert len(jdks) == 3
        assert "java11" in jdk_ids
        assert "java17" in jdk_ids
        assert "java21" in jdk_ids
    finally:
        connection.close()


def test_register_v1_runner_profile_jdk_homes_present(tmp_path: Path) -> None:
    """Each JDK in the V1 runner profile has a non-empty java_home."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        dto = svc.register_runner_profile(_runner_command())

        for jdk in dto.payload.get("jdks", ()):
            java_home = jdk.get("java_home", "")
            assert java_home, f"java_home is empty for {jdk.get('jdk_id')}"
    finally:
        connection.close()


def test_register_v1_runner_profile_idempotent(tmp_path: Path) -> None:
    """Re-registering the same runner profile payload is idempotent."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        first = svc.register_runner_profile(_runner_command(actor_id="creator"))
        second = svc.register_runner_profile(_runner_command(actor_id="other"))

        assert second == first
        assert second.created_by == "creator"
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Browser-controllable fields must be rejected
# ---------------------------------------------------------------------------


def test_register_v1_pipeline_rejects_shell_command(tmp_path: Path) -> None:
    """Pipeline payload containing shell_command must fail schema validation."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        payload = make_v1_pipeline_definition()
        payload["shell_command"] = "rm -rf /"
        with pytest.raises(Exception):
            svc.register_pipeline_definition(_pipeline_command(pipeline=payload))
    finally:
        connection.close()


def test_register_v1_pipeline_rejects_maven_goals(tmp_path: Path) -> None:
    """Pipeline payload containing maven_goals must fail schema validation."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        payload = make_v1_pipeline_definition()
        payload["maven_goals"] = "clean install"
        with pytest.raises(Exception):
            svc.register_pipeline_definition(_pipeline_command(pipeline=payload))
    finally:
        connection.close()


def test_register_v1_pipeline_rejects_working_directory(tmp_path: Path) -> None:
    """Pipeline payload containing working_directory must fail schema validation."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        payload = make_v1_pipeline_definition()
        payload["working_directory"] = "/tmp/evil"
        with pytest.raises(Exception):
            svc.register_pipeline_definition(_pipeline_command(pipeline=payload))
    finally:
        connection.close()


def test_register_v1_pipeline_rejects_model_deployment_id(tmp_path: Path) -> None:
    """Pipeline payload containing model_deployment_id must fail schema validation."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        payload = make_v1_pipeline_definition()
        payload["model_deployment_id"] = "gpt-42"
        with pytest.raises(Exception):
            svc.register_pipeline_definition(_pipeline_command(pipeline=payload))
    finally:
        connection.close()


def test_register_v1_runner_rejects_shell_command(tmp_path: Path) -> None:
    """Runner payload containing shell_command must fail schema validation."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        payload = make_v1_runner_profile()
        payload["shell_command"] = "rm -rf /"
        with pytest.raises(Exception):
            svc.register_runner_profile(_runner_command(profile=payload))
    finally:
        connection.close()


def test_register_v1_runner_rejects_maven_goals(tmp_path: Path) -> None:
    """Runner payload containing maven_goals must fail schema validation."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        payload = make_v1_runner_profile()
        payload["maven_goals"] = "clean install"
        with pytest.raises(Exception):
            svc.register_runner_profile(_runner_command(profile=payload))
    finally:
        connection.close()


def test_register_v1_runner_rejects_working_directory(tmp_path: Path) -> None:
    """Runner payload containing working_directory must fail schema validation."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        payload = make_v1_runner_profile()
        payload["working_directory"] = "/tmp/evil"
        with pytest.raises(Exception):
            svc.register_runner_profile(_runner_command(profile=payload))
    finally:
        connection.close()


def test_register_v1_runner_rejects_model_deployment_id(tmp_path: Path) -> None:
    """Runner payload containing model_deployment_id must fail schema validation."""
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)
        payload = make_v1_runner_profile()
        payload["model_deployment_id"] = "gpt-42"
        with pytest.raises(Exception):
            svc.register_runner_profile(_runner_command(profile=payload))
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Combined pipeline+runner compatibility (read-after-write)
# ---------------------------------------------------------------------------


def test_v1_pipeline_and_runner_registered_together_support_create_migration_job(
    tmp_path: Path,
) -> None:
    """Both V1 pipeline and runner can be registered and used together.

    This exercises the pipeline+runner compatibility check (JDK matching) that
    CreateMigrationJobService performs.
    """
    connection = _migrated_connection(tmp_path)
    try:
        svc = _service(connection)

        # Register V1 pipeline
        pipeline_dto = svc.register_pipeline_definition(_pipeline_command())
        assert pipeline_dto.pipeline_id == "springboot-216-to-356-java21-three-stage"

        # Register V1 runner profile
        runner_dto = svc.register_runner_profile(_runner_command())
        assert runner_dto.runner_profile_id == "runner-v1"

        # Verify both are retrievable
        fetched_pipeline = svc.get_pipeline_definition(
            "springboot-216-to-356-java21-three-stage", "2026.06"
        )
        assert fetched_pipeline == pipeline_dto

        fetched_runner = svc.get_runner_profile("runner-v1", "2026.06")
        assert fetched_runner == runner_dto

        # Verify the runner JDKs satisfy the pipeline's command_jdk requirements:
        # pipeline needs java11, java17, java21
        pipeline_jdks = {s["command_jdk"] for s in pipeline_dto.payload.get("stages", ())}
        runner_jdks = {j["jdk_id"] for j in runner_dto.payload.get("jdks", ())}
        missing = pipeline_jdks - runner_jdks
        assert not missing, f"Runner profile missing JDKs required by pipeline: {missing}"
    finally:
        connection.close()
