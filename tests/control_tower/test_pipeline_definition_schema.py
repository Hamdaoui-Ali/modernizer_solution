from __future__ import annotations

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.schemas.pipeline_definition import PipelineDefinition


def _pipeline_payload(stages: tuple[dict, ...]) -> dict:
    return {
        "schema_version": "1.0.0",
        "pipeline_id": "pipeline-default",
        "pipeline_version": "2026.06",
        "display_name": "Default pipeline",
        "graph_version": "1.0",
        "graph_state_schema_version": "1.0",
        "stages": stages,
    }


def test_valid_one_stage_pipeline() -> None:
    pipeline = PipelineDefinition.model_validate(
        _pipeline_payload(
            (
                {
                    "stage_index": 1,
                    "stage_id": "analyze",
                    "profile_id": "analysis-profile",
                    "command_jdk": "jdk-17",
                    "input_source": {"kind": "legacy_source"},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 17},
                },
            )
        )
    )

    assert pipeline.stages[0].input_source.kind == "legacy_source"


def test_valid_two_stage_pipeline() -> None:
    pipeline = PipelineDefinition.model_validate(
        _pipeline_payload(
            (
                {
                    "stage_index": 1,
                    "stage_id": "analyze",
                    "profile_id": "analysis-profile",
                    "command_jdk": "jdk-17",
                    "input_source": {"kind": "legacy_source"},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 17},
                },
                {
                    "stage_index": 2,
                    "stage_id": "transform",
                    "profile_id": "transform-profile",
                    "command_jdk": "jdk-17",
                    "input_source": {"kind": "previous_stage", "previous_stage_index": 1},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 17},
                },
            )
        )
    )

    assert pipeline.stages[1].input_source.previous_stage_index == 1


def test_unknown_field_rejected() -> None:
    payload = _pipeline_payload(
        (
            {
                "stage_index": 1,
                "stage_id": "analyze",
                "profile_id": "analysis-profile",
                "command_jdk": "jdk-17",
                "input_source": {"kind": "legacy_source"},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.14", "java": 17},
            },
        )
    )
    payload["unexpected"] = "value"

    with pytest.raises(ValidationError):
        PipelineDefinition.model_validate(payload)


def test_pipeline_definition_is_immutable() -> None:
    pipeline = PipelineDefinition.model_validate(
        _pipeline_payload(
            (
                {
                    "stage_index": 1,
                    "stage_id": "analyze",
                    "profile_id": "analysis-profile",
                    "command_jdk": "jdk-17",
                    "input_source": {"kind": "legacy_source"},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 17},
                },
            )
        )
    )

    with pytest.raises(ValidationError):
        pipeline.display_name = "Changed"


def test_pipeline_stage_definition_is_immutable() -> None:
    pipeline = PipelineDefinition.model_validate(
        _pipeline_payload(
            (
                {
                    "stage_index": 1,
                    "stage_id": "analyze",
                    "profile_id": "analysis-profile",
                    "command_jdk": "jdk-17",
                    "input_source": {"kind": "legacy_source"},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 17},
                },
            )
        )
    )

    with pytest.raises(ValidationError):
        pipeline.stages[0].command_jdk = "jdk-21"


def test_stage_indexes_must_start_at_1() -> None:
    payload = _pipeline_payload(
        (
            {
                "stage_index": 2,
                "stage_id": "analyze",
                "profile_id": "analysis-profile",
                "command_jdk": "jdk-17",
                "input_source": {"kind": "legacy_source"},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.14", "java": 17},
            },
        )
    )

    with pytest.raises(ValidationError):
        PipelineDefinition.model_validate(payload)


def test_stage_indexes_must_be_contiguous() -> None:
    payload = _pipeline_payload(
        (
            {
                "stage_index": 1,
                "stage_id": "analyze",
                "profile_id": "analysis-profile",
                "command_jdk": "jdk-17",
                "input_source": {"kind": "legacy_source"},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.14", "java": 17},
            },
            {
                "stage_index": 3,
                "stage_id": "transform",
                "profile_id": "transform-profile",
                "command_jdk": "jdk-17",
                "input_source": {"kind": "previous_stage", "previous_stage_index": 2},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.14", "java": 17},
            },
        )
    )

    with pytest.raises(ValidationError):
        PipelineDefinition.model_validate(payload)


def test_stage_1_must_use_legacy_source() -> None:
    payload = _pipeline_payload(
        (
            {
                "stage_index": 1,
                "stage_id": "analyze",
                "profile_id": "analysis-profile",
                "command_jdk": "jdk-17",
                "input_source": {"kind": "previous_stage", "previous_stage_index": 0},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.14", "java": 17},
            },
        )
    )

    with pytest.raises(ValidationError):
        PipelineDefinition.model_validate(payload)


def test_stage_2_and_later_must_use_previous_stage() -> None:
    payload = _pipeline_payload(
        (
            {
                "stage_index": 1,
                "stage_id": "analyze",
                "profile_id": "analysis-profile",
                "command_jdk": "jdk-17",
                "input_source": {"kind": "legacy_source"},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.14", "java": 17},
            },
            {
                "stage_index": 2,
                "stage_id": "transform",
                "profile_id": "transform-profile",
                "command_jdk": "jdk-17",
                "input_source": {"kind": "legacy_source"},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.14", "java": 17},
            },
        )
    )

    with pytest.raises(ValidationError):
        PipelineDefinition.model_validate(payload)


def test_command_jdk_must_be_non_empty() -> None:
    payload = _pipeline_payload(
        (
            {
                "stage_index": 1,
                "stage_id": "analyze",
                "profile_id": "analysis-profile",
                "command_jdk": "   ",
                "input_source": {"kind": "legacy_source"},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.14", "java": 17},
            },
        )
    )

    with pytest.raises(ValidationError):
        PipelineDefinition.model_validate(payload)


def test_unknown_continuation_policy_rejected() -> None:
    payload = _pipeline_payload(
        (
            {
                "stage_index": 1,
                "stage_id": "analyze",
                "profile_id": "analysis-profile",
                "command_jdk": "jdk-17",
                "input_source": {"kind": "legacy_source"},
                "continuation_policy_id": "bogus-policy",
                "target": {"spring_boot": "3.5.14", "java": 17},
            },
        )
    )

    with pytest.raises(ValidationError):
        PipelineDefinition.model_validate(payload)


def test_stage_index_rejects_string_input() -> None:
    payload = _pipeline_payload(
        (
            {
                "stage_index": "1",
                "stage_id": "analyze",
                "profile_id": "analysis-profile",
                "command_jdk": "jdk-17",
                "input_source": {"kind": "legacy_source"},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.14", "java": 17},
            },
        )
    )

    with pytest.raises(ValidationError):
        PipelineDefinition.model_validate(payload)


def test_stages_collection_is_immutable() -> None:
    pipeline = PipelineDefinition.model_validate(
        _pipeline_payload(
            (
                {
                    "stage_index": 1,
                    "stage_id": "analyze",
                    "profile_id": "analysis-profile",
                    "command_jdk": "jdk-17",
                    "input_source": {"kind": "legacy_source"},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 17},
                },
            )
        )
    )

    with pytest.raises(AttributeError):
        pipeline.stages.append("invalid")
