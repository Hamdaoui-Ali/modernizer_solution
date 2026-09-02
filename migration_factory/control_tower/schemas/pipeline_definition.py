"""Pipeline definition schemas for Control Tower configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import NonEmptyString, StrictModel, require_non_empty_string


StageInputSourceKind = Literal["legacy_source", "previous_stage"]
KNOWN_CONTINUATION_POLICY_IDS = frozenset(
    {
        "default",
        "stage1-build-test-policy",
        "final-build-test-policy",
    }
)


class PipelineInputSource(StrictModel):
    kind: StageInputSourceKind
    previous_stage_index: int | None = None


class PipelineTarget(StrictModel):
    spring_boot: str | None = None
    java: int


class PipelineStage(StrictModel):
    stage_index: int
    stage_id: NonEmptyString
    profile_id: NonEmptyString
    command_jdk: NonEmptyString
    input_source: PipelineInputSource
    continuation_policy_id: NonEmptyString
    target: PipelineTarget

    @field_validator("stage_id", "profile_id", "command_jdk", "continuation_policy_id", mode="after")
    @classmethod
    def _validate_required_strings(cls, value: str, info):
        value = require_non_empty_string(value, info.field_name)
        if info.field_name == "continuation_policy_id" and value not in KNOWN_CONTINUATION_POLICY_IDS:
            raise ValueError("unknown continuation policy")
        return value


class PipelineDefinition(StrictModel):
    schema_version: NonEmptyString
    pipeline_id: NonEmptyString
    pipeline_version: NonEmptyString
    display_name: NonEmptyString
    graph_version: NonEmptyString
    graph_state_schema_version: NonEmptyString
    stages: tuple[PipelineStage, ...] = Field(min_length=1)

    @field_validator(
        "schema_version",
        "pipeline_id",
        "pipeline_version",
        "display_name",
        "graph_version",
        "graph_state_schema_version",
        mode="after",
    )
    @classmethod
    def _validate_required_strings(cls, value: str, info):
        return require_non_empty_string(value, info.field_name)

    @model_validator(mode="after")
    def _validate_stages(self) -> "PipelineDefinition":
        expected_indexes = list(range(1, len(self.stages) + 1))
        stage_indexes = [stage.stage_index for stage in self.stages]
        if stage_indexes != expected_indexes:
            raise ValueError("stage indexes must be contiguous and start at 1")

        first_stage = self.stages[0]
        if first_stage.input_source.kind != "legacy_source":
            raise ValueError("stage 1 must read the legacy source")
        if first_stage.input_source.previous_stage_index is not None:
            raise ValueError("stage 1 must not declare previous_stage_index")

        for stage in self.stages[1:]:
            if stage.input_source.kind != "previous_stage":
                raise ValueError("stage 2+ must explicitly read a previous stage")
            if stage.input_source.previous_stage_index != stage.stage_index - 1:
                raise ValueError("previous_stage_index must point to the immediately previous stage")

        return self


StageInputSource = PipelineInputSource
PipelineStageDefinition = PipelineStage
