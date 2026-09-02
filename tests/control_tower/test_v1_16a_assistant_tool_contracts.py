"""Focused tests for V1-16A: Read-only assistant tool contracts."""

from __future__ import annotations

import json

import pytest

from migration_factory.control_tower.application.assistant_tools import (
    AssistantToolCallBuilder,
    AssistantToolCallRecord,
    AssistantToolCategory,
    AssistantToolDef,
    AssistantToolParamSchema,
    ASSISTANT_TOOL_ALLOWLIST,
    get_assistant_tool_def,
    get_tool_param_schema,
    is_assistant_tool_allowed,
    list_allowed_tool_names,
    TOOL_PARAM_SCHEMAS,
)


# ── Allowlist tests ────────────────────────────────────────────────


class TestAssistantToolAllowlist:
    """ASSISTANT_TOOL_ALLOWLIST integrity."""

    def test_all_tool_names_are_unique(self) -> None:
        names = [tool.name for tool in ASSISTANT_TOOL_ALLOWLIST]
        assert len(names) == len(set(names))

    def test_all_tools_are_read_only(self) -> None:
        non_read = [
            tool.name for tool in ASSISTANT_TOOL_ALLOWLIST
            if tool.category not in (
                AssistantToolCategory.CONTEXT_PACK,
                AssistantToolCategory.JOB_STATUS,
                AssistantToolCategory.COMMAND_OUTPUT,
                AssistantToolCategory.ARTIFACT_INFO,
                AssistantToolCategory.MODEL_INVOCATION,
                AssistantToolCategory.PIPELINE_INFO,
                AssistantToolCategory.STAGE_CHAIN,
                AssistantToolCategory.AUDIT_LOG,
                AssistantToolCategory.EVIDENCE_RETRIEVAL,
            )
        ]
        assert not non_read, f"Non-read-only tools found: {non_read}"

    def test_get_assistant_tool_def_returns_known(self) -> None:
        tool = get_assistant_tool_def("get_job_status")
        assert tool is not None
        assert tool.name == "get_job_status"

    def test_get_assistant_tool_def_returns_none_for_unknown(self) -> None:
        assert get_assistant_tool_def("execute_command") is None

    def test_is_assistant_tool_allowed(self) -> None:
        assert is_assistant_tool_allowed("get_context_pack")
        assert not is_assistant_tool_allowed("write_file")
        assert not is_assistant_tool_allowed("execute_shell")

    def test_list_allowed_tool_names_contains_expected(self) -> None:
        names = list_allowed_tool_names()
        assert "get_job_status" in names
        assert "get_context_pack" in names
        assert "list_context_packs" in names
        assert "get_command_output_window" in names
        assert "list_artifacts" in names
        assert "list_model_invocations" in names
        assert "get_pipeline_info" in names
        assert "get_stage_chain" in names
        assert "list_audit_records" in names
        assert "retrieve_evidence" in names


# ── Tool call builder tests ───────────────────────────────────────


class TestAssistantToolCallBuilder:
    """AssistantToolCallBuilder validation behavior."""

    def test_build_with_valid_tool(self) -> None:
        builder = AssistantToolCallBuilder()
        record = builder.with_tool("get_job_status").with_parameter("job_id", "job-123").build(
            raw_output='{"status": "running"}',
        )
        assert isinstance(record, AssistantToolCallRecord)
        assert record.tool_name == "get_job_status"
        assert record.error_message is None
        assert record.output_chars > 0
        assert not record.truncated

    def test_rejects_unknown_tool(self) -> None:
        builder = AssistantToolCallBuilder()
        with pytest.raises(ValueError, match="not in the assistant tool allowlist"):
            builder.with_tool("execute_command")

    def test_rejects_rejected_tool_names(self) -> None:
        builder = AssistantToolCallBuilder()
        with pytest.raises(ValueError, match="not in the assistant tool allowlist"):
            builder.with_tool("write_file")

    def test_build_with_error(self) -> None:
        builder = AssistantToolCallBuilder()
        record = builder.with_tool("get_job_status").with_error("Job not found").build()
        assert record.error_message == "Job not found"
        assert record.redacted_output == ""
        assert record.output_chars == 0

    def test_build_with_redacted_output(self) -> None:
        builder = AssistantToolCallBuilder()
        record = builder.with_tool("get_context_pack").with_parameter("manifest_id", "cp-001").build(
            raw_output="Contents from /home/user/project with KEY=secret",
        )
        # Paths and env assignments should be redacted
        assert "/home/user/project" not in record.redacted_output
        assert "KEY=secret" not in record.redacted_output

    def test_build_truncates_long_output(self) -> None:
        builder = AssistantToolCallBuilder()
        # get_job_status has max_output_chars=5000
        record = builder.with_tool("get_job_status").with_parameter("job_id", "j-1").build(
            raw_output="x" * 10_000,
        )
        assert record.truncated
        assert record.output_chars == 5_000  # max_output_chars for get_job_status

    def test_build_without_tool_raises(self) -> None:
        builder = AssistantToolCallBuilder()
        with pytest.raises(ValueError, match="Tool name must be set"):
            builder.build()

    def test_build_tool_call_record_is_frozen(self) -> None:
        builder = AssistantToolCallBuilder()
        record = builder.with_tool("get_job_status").with_parameter("job_id", "j-1").build()
        with pytest.raises(AttributeError):
            record.tool_name = "changed"  # type: ignore[misc]

    def test_parameter_key_too_long(self) -> None:
        builder = AssistantToolCallBuilder()
        with pytest.raises(ValueError, match="Parameter key too long"):
            builder.with_parameter("x" * 101, "value")

    def test_parameter_value_too_long(self) -> None:
        builder = AssistantToolCallBuilder()
        with pytest.raises(ValueError, match="exceeds max length"):
            builder.with_parameter("key", "x" * 10_000)

    def test_record_has_slots(self) -> None:
        builder = AssistantToolCallBuilder()
        record = builder.with_tool("get_job_status").with_parameter("job_id", "j-1").build()
        assert hasattr(record, "__slots__")

    def test_parameters_json_deterministic(self) -> None:
        builder = AssistantToolCallBuilder()
        record = builder.with_tool("get_job_status").with_parameters({"job_id": "j-1"}).build()
        parsed = json.loads(record.parameters_json)
        assert parsed == {"job_id": "j-1"}


# ── Tool parameter schema tests ────────────────────────────────────


class TestToolParamSchemas:
    """TOOL_PARAM_SCHEMAS integrity."""

    def test_all_tools_have_schemas(self) -> None:
        for tool in ASSISTANT_TOOL_ALLOWLIST:
            schema = get_tool_param_schema(tool.name)
            assert len(schema) > 0, f"Tool {tool.name} has no parameter schema"

    def test_get_command_output_window_has_expected_params(self) -> None:
        schema = get_tool_param_schema("get_command_output_window")
        names = {s.name for s in schema}
        assert "command_id" in names
        assert "stream" in names
        assert "after_offset" in names
        assert "max_bytes" in names

    def test_stream_allowed_values(self) -> None:
        schema = get_tool_param_schema("get_command_output_window")
        stream_schema = next(s for s in schema if s.name == "stream")
        assert stream_schema.allowed_values == ("stdout", "stderr")

    def test_max_bytes_has_max_value(self) -> None:
        schema = get_tool_param_schema("get_command_output_window")
        max_bytes_schema = next(s for s in schema if s.name == "max_bytes")
        assert max_bytes_schema.max_value == 1_000_000
        assert max_bytes_schema.min_value == 1

    def test_retrieve_evidence_has_bounds(self) -> None:
        schema = get_tool_param_schema("retrieve_evidence")
        max_files_schema = next((s for s in schema if s.name == "max_files"), None)
        assert max_files_schema is not None
        assert max_files_schema.max_value == 50

        max_depth_schema = next((s for s in schema if s.name == "max_depth"), None)
        assert max_depth_schema is not None
        assert max_depth_schema.max_value == 10


# ── Schema dataclass tests ─────────────────────────────────────────


class TestAssistantToolDef:
    """AssistantToolDef dataclass behavior."""

    def test_tool_def_has_required_fields(self) -> None:
        tool = AssistantToolDef(
            name="test_tool",
            category=AssistantToolCategory.CONTEXT_PACK,
            description="A test tool",
        )
        assert tool.name == "test_tool"
        assert tool.category == AssistantToolCategory.CONTEXT_PACK
        assert tool.max_output_chars == 10_000
        assert tool.redact_output is True

    def test_tool_def_is_frozen(self) -> None:
        tool = AssistantToolDef(
            name="frozen_tool",
            category=AssistantToolCategory.JOB_STATUS,
        )
        with pytest.raises(AttributeError):
            tool.name = "changed"  # type: ignore[misc]

    def test_tool_def_has_slots(self) -> None:
        tool = AssistantToolDef(
            name="slotted_tool",
            category=AssistantToolCategory.JOB_STATUS,
        )
        assert hasattr(tool, "__slots__")


class TestAssistantToolParamSchema:
    """AssistantToolParamSchema dataclass behavior."""

    def test_schema_with_optional_field(self) -> None:
        schema = AssistantToolParamSchema(
            name="job_id",
            param_type="string",
            required=False,
            description="Optional job ID",
        )
        assert schema.required is False
        assert schema.description == "Optional job ID"

    def test_schema_with_bounds(self) -> None:
        schema = AssistantToolParamSchema(
            name="max_bytes",
            param_type="integer",
            min_value=1,
            max_value=1_000_000,
        )
        assert schema.min_value == 1
        assert schema.max_value == 1_000_000
