"""Focused tests for V1 contract fixture module (V1-00A).

Verifies pipeline, runner, and job fixtures enforce the V1 route contract.
"""

from __future__ import annotations

from migration_factory.control_tower.schemas.pipeline_definition import PipelineDefinition
from migration_factory.control_tower.schemas.runner_profile import RunnerProfile
from tests.control_tower.v1_fixtures import make_v1_job_payload
from tests.control_tower.v1_fixtures import make_v1_pipeline_definition
from tests.control_tower.v1_fixtures import make_v1_runner_profile

# ---------------------------------------------------------------------------
# Pipeline fixture
# ---------------------------------------------------------------------------


def test_v1_pipeline_definition_has_correct_id() -> None:
    payload = make_v1_pipeline_definition()
    assert payload["pipeline_id"] == "springboot-216-to-356-java21-three-stage"


def test_v1_pipeline_definition_has_three_stages() -> None:
    payload = make_v1_pipeline_definition()
    assert len(payload["stages"]) == 3


def test_v1_pipeline_definition_validates_against_schema() -> None:
    payload = make_v1_pipeline_definition()
    pipeline = PipelineDefinition.model_validate(payload)
    assert pipeline.pipeline_id == "springboot-216-to-356-java21-three-stage"


def test_v1_pipeline_stage1_java11_boot2718_legacy_source() -> None:
    payload = make_v1_pipeline_definition()
    stage = payload["stages"][0]

    assert stage["stage_index"] == 1
    assert stage["command_jdk"] == "java11"
    assert stage["target"]["spring_boot"] == "2.7.18"
    assert stage["target"]["java"] == 11
    assert stage["input_source"]["kind"] == "legacy_source"
    assert "previous_stage_index" not in stage["input_source"] or stage["input_source"]["previous_stage_index"] is None  # noqa: E501


def test_v1_pipeline_stage2_java17_boot356_previous_stage() -> None:
    payload = make_v1_pipeline_definition()
    stage = payload["stages"][1]

    assert stage["stage_index"] == 2
    assert stage["command_jdk"] == "java17"
    assert stage["target"]["spring_boot"] == "3.5.6"
    assert stage["target"]["java"] == 17
    assert stage["input_source"]["kind"] == "previous_stage"
    assert stage["input_source"]["previous_stage_index"] == 1


def test_v1_pipeline_stage3_java21_boot356_previous_stage() -> None:
    payload = make_v1_pipeline_definition()
    stage = payload["stages"][2]

    assert stage["stage_index"] == 3
    assert stage["command_jdk"] == "java21"
    assert stage["target"]["spring_boot"] == "3.5.6"
    assert stage["target"]["java"] == 21
    assert stage["input_source"]["kind"] == "previous_stage"
    assert stage["input_source"]["previous_stage_index"] == 2


# ---------------------------------------------------------------------------
# Boot 4 and 3.5.14 must NOT be present as execution targets
# ---------------------------------------------------------------------------


def test_v1_pipeline_no_boot4_in_targets() -> None:
    """Boot 4 must not appear as a target in any V1 stage."""
    payload = make_v1_pipeline_definition()
    for stage in payload["stages"]:
        target_boot = stage["target"].get("spring_boot", "")
        assert "4." not in target_boot, f"Boot 4 found in stage {stage['stage_index']}: {target_boot}"


def test_v1_pipeline_no_3514_in_targets() -> None:
    """3.5.14 must not appear as a target in any V1 stage."""
    payload = make_v1_pipeline_definition()
    for stage in payload["stages"]:
        target_boot = stage["target"].get("spring_boot", "")
        assert target_boot != "3.5.14", f"3.5.14 found in stage {stage['stage_index']}"


# ---------------------------------------------------------------------------
# Runner fixture
# ---------------------------------------------------------------------------


def test_v1_runner_profile_validates_against_schema() -> None:
    payload = make_v1_runner_profile()
    profile = RunnerProfile.model_validate(payload)
    assert profile.runner_profile_id == "runner-v1"


def test_v1_runner_profile_no_raw_browser_paths() -> None:
    """Runner fixture should contain backend-owned fields only.

    The test verifies that the runner payload does not contain any field that
    would let the browser choose arbitrary raw paths, Maven goals, shell
    commands, working directories, or model deployment IDs.
    """
    payload = make_v1_runner_profile()

    # No shell commands or Maven goals in the payload
    assert "shell_command" not in payload
    assert "maven_goals" not in payload
    assert "working_directory" not in payload
    assert "model_deployment_id" not in payload


def test_v1_runner_profile_has_three_jdks() -> None:
    payload = make_v1_runner_profile()
    assert len(payload["jdks"]) == 3


def test_v1_runner_profile_jdk_ids_match_pipeline_command_jdks() -> None:
    payload = make_v1_runner_profile()
    jdk_ids = {jdk["jdk_id"] for jdk in payload["jdks"]}
    assert "java11" in jdk_ids
    assert "java17" in jdk_ids
    assert "java21" in jdk_ids


# ---------------------------------------------------------------------------
# Job payload fixture
# ---------------------------------------------------------------------------


def test_v1_job_payload_has_correct_structure() -> None:
    payload = make_v1_job_payload()
    assert payload["pipeline_id"] == "springboot-216-to-356-java21-three-stage"
    assert payload["runner_profile_id"] == "runner-v1"


def test_v1_job_payload_no_browser_controllable_fields() -> None:
    """Job payload must not contain fields the browser could choose."""
    payload = make_v1_job_payload()

    assert "shell_command" not in payload
    assert "maven_goals" not in payload
    assert "working_directory" not in payload
    assert "model_deployment_id" not in payload
    assert "raw_path" not in payload
