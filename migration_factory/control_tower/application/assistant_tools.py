"""Read-only assistant tool contracts for V1-16A.

Defines bounded, read-only assistant tool schemas, allowlists, and
limits. Assistant tools may only query/read bounded data. No writes,
no process launch, no approvals, no file mutations, no command
execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from migration_factory.control_tower.application.context_pack_redaction import (
    redact_manifest_field,
)
from migration_factory.control_tower.application.redaction import (
    redact_absolute_paths,
    redact_deployment_identifiers,
    redact_env_assignments,
    redact_model_summary,
    redact_public_value,
)


# ── Tool categories ──────────────────────────────────────────────


class AssistantToolCategory(Enum):
    """Categories of read-only assistant tools."""

    CONTEXT_PACK = "context_pack"        # Read context pack manifests
    JOB_STATUS = "job_status"            # Read migration job status
    COMMAND_OUTPUT = "command_output"    # Read command output windows
    ARTIFACT_INFO = "artifact_info"      # Read artifact metadata
    MODEL_INVOCATION = "model_invocation"  # Read model invocation audit (redacted)
    PIPELINE_INFO = "pipeline_info"      # Read pipeline definitions
    STAGE_CHAIN = "stage_chain"          # Read stage chain ledger
    AUDIT_LOG = "audit_log"              # Read audit records
    EVIDENCE_RETRIEVAL = "evidence_retrieval"  # Perform bounded evidence retrieval


# ── Tool allowlist ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AssistantToolDef:
    """Definition of a read-only assistant tool.

    Attributes:
        name: Canonical tool name used in assistant calls.
        category: Tool category for grouping and authorization.
        description: Human-readable description for the assistant.
        max_output_chars: Maximum character count in the output.
        redact_output: If True, apply redaction to the tool output.
        requires_job_id: If True, the tool requires a job_id parameter.
    """

    name: str
    category: AssistantToolCategory
    description: str = ""
    max_output_chars: int = 10_000
    redact_output: bool = True
    requires_job_id: bool = True


# The canonical allowlist of read-only assistant tools.
# Only tools listed here may be invoked by the assistant.
ASSISTANT_TOOL_ALLOWLIST: tuple[AssistantToolDef, ...] = (
    AssistantToolDef(
        name="get_context_pack",
        category=AssistantToolCategory.CONTEXT_PACK,
        description="Retrieve a context pack manifest by ID.",
        max_output_chars=20_000,
    ),
    AssistantToolDef(
        name="list_context_packs",
        category=AssistantToolCategory.CONTEXT_PACK,
        description="List context pack manifests for a migration job.",
        max_output_chars=50_000,
    ),
    AssistantToolDef(
        name="get_job_status",
        category=AssistantToolCategory.JOB_STATUS,
        description="Get the current status of a migration job.",
        max_output_chars=5_000,
    ),
    AssistantToolDef(
        name="get_command_output_window",
        category=AssistantToolCategory.COMMAND_OUTPUT,
        description="Read a bounded window from a command's output stream.",
        max_output_chars=50_000,
    ),
    AssistantToolDef(
        name="list_artifacts",
        category=AssistantToolCategory.ARTIFACT_INFO,
        description="List artifacts for a migration job.",
        max_output_chars=50_000,
    ),
    AssistantToolDef(
        name="list_model_invocations",
        category=AssistantToolCategory.MODEL_INVOCATION,
        description="List redacted model invocations for a migration job.",
        max_output_chars=50_000,
    ),
    AssistantToolDef(
        name="get_pipeline_info",
        category=AssistantToolCategory.PIPELINE_INFO,
        description="Get the pipeline definition for a migration job.",
        max_output_chars=10_000,
    ),
    AssistantToolDef(
        name="get_stage_chain",
        category=AssistantToolCategory.STAGE_CHAIN,
        description="Get the stage chain ledger for a migration job.",
        max_output_chars=20_000,
    ),
    AssistantToolDef(
        name="list_audit_records",
        category=AssistantToolCategory.AUDIT_LOG,
        description="List audit records for a migration job.",
        max_output_chars=50_000,
    ),
    AssistantToolDef(
        name="retrieve_evidence",
        category=AssistantToolCategory.EVIDENCE_RETRIEVAL,
        description="Perform bounded evidence retrieval from artifacts. Supports resolve_and_retrieve.",
        max_output_chars=100_000,
    ),
)

# Indexed by name for fast lookup
_ASSISTANT_TOOL_MAP: dict[str, AssistantToolDef] = {
    tool.name: tool for tool in ASSISTANT_TOOL_ALLOWLIST
}


def get_assistant_tool_def(name: str) -> AssistantToolDef | None:
    """Look up a tool definition by name from the allowlist.

    Returns None if the tool name is not in the allowlist.
    """
    return _ASSISTANT_TOOL_MAP.get(name)


def is_assistant_tool_allowed(name: str) -> bool:
    """Check if a tool name is in the read-only allowlist."""
    return name in _ASSISTANT_TOOL_MAP


def list_allowed_tool_names() -> tuple[str, ...]:
    """Return the canonical list of allowed tool names."""
    return tuple(tool.name for tool in ASSISTANT_TOOL_ALLOWLIST)


# ── Tool call record ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AssistantToolCallRecord:
    """Immutable record of a single assistant tool call.

    Captures the tool name, bounded parameters, redacted output,
    and deterministic metadata for audit.
    """

    tool_name: str
    parameters_json: str           # Canonical JSON of tool parameters
    redacted_output: str           # Redacted tool output
    output_chars: int              # Actual output character count
    truncated: bool                # True if output was truncated to max_output_chars
    error_message: str | None = None  # Error message if the call failed
    duration_ms: int = 0           # Approximate duration in milliseconds


# ── Tool call builder ─────────────────────────────────────────────


class AssistantToolCallBuilder:
    """Builds and validates assistant tool call records.

    Ensures:
    - Tool name is in the allowlist.
    - Parameters are properly bounded (no large blobs).
    - Output is redacted.
    - Max output chars are respected.
    """

    MAX_PARAM_JSON_CHARS = 5_000

    def __init__(self) -> None:
        self._tool_name: str | None = None
        self._parameters: dict[str, Any] = {}
        self._error_message: str | None = None

    def with_tool(self, tool_name: str) -> AssistantToolCallBuilder:
        """Set the tool name. Returns self for chaining."""
        if not is_assistant_tool_allowed(tool_name):
            raise ValueError(
                f"Tool {tool_name!r} is not in the assistant tool allowlist. "
                f"Allowed tools: {list_allowed_tool_names()}"
            )
        self._tool_name = tool_name
        return self

    def with_parameter(self, key: str, value: Any) -> AssistantToolCallBuilder:
        """Add a bounded parameter. Returns self for chaining."""
        if len(key) > 100:
            raise ValueError(f"Parameter key too long: {key[:50]}...")

        if isinstance(value, str) and len(value) > self.MAX_PARAM_JSON_CHARS:
            raise ValueError(
                f"Parameter {key!r} value exceeds max length of {self.MAX_PARAM_JSON_CHARS}"
            )

        self._parameters[key] = value
        return self

    def with_parameters(self, params: dict[str, Any]) -> AssistantToolCallBuilder:
        """Set all parameters at once. Returns self for chaining."""
        for key, value in params.items():
            self.with_parameter(key, value)
        return self

    def with_error(self, message: str) -> AssistantToolCallBuilder:
        """Set an error message. Returns self for chaining."""
        self._error_message = message
        return self

    def build(self, raw_output: str | None = None, duration_ms: int = 0) -> AssistantToolCallRecord:
        """Build the tool call record.

        Args:
            raw_output: The raw tool output before redaction (None if error).
            duration_ms: Approximate execution duration.

        Returns:
            An immutable AssistantToolCallRecord with redacted output.
        """
        if self._tool_name is None:
            raise ValueError("Tool name must be set before building")

        tool_def = get_assistant_tool_def(self._tool_name)
        if tool_def is None:
            raise ValueError(f"Unknown tool: {self._tool_name!r}")

        import json

        params_json = json.dumps(self._parameters, separators=(",", ":"), sort_keys=True)

        if self._error_message:
            return AssistantToolCallRecord(
                tool_name=self._tool_name,
                parameters_json=params_json,
                redacted_output="",
                output_chars=0,
                truncated=False,
                error_message=self._error_message,
                duration_ms=duration_ms,
            )

        if raw_output is None:
            raw_output = ""

        # Apply redaction if the tool definition requires it
        if tool_def.redact_output:
            redacted = _redact_tool_output(raw_output)
        else:
            redacted = raw_output

        # Truncate to max output chars
        truncated = len(redacted) > tool_def.max_output_chars
        if truncated:
            redacted = redacted[: tool_def.max_output_chars]

        return AssistantToolCallRecord(
            tool_name=self._tool_name,
            parameters_json=params_json,
            redacted_output=redacted,
            output_chars=len(redacted),
            truncated=truncated,
            duration_ms=duration_ms,
        )


# ── Redaction for tool output ────────────────────────────────────


def _redact_tool_output(output: str) -> str:
    """Redact assistant tool output for safe consumption.

    Applies all redaction primitives: paths, env vars, secret keys,
    deployment IDs, and raw prompts.
    """
    result = output
    result = redact_absolute_paths(result)
    result = redact_env_assignments(result)
    result = redact_deployment_identifiers(result)
    result = redact_model_summary(result)
    return result


# ── Tool parameter schemas (for structured validation) ────────────


@dataclass(frozen=True, slots=True)
class AssistantToolParamSchema:
    """Schema for a single tool parameter."""

    name: str
    param_type: str  # "string", "integer", "boolean"
    required: bool = True
    description: str = ""
    max_length: int | None = None
    min_value: int | None = None
    max_value: int | None = None
    allowed_values: tuple[str, ...] = ()


# Parameter schemas indexed by tool name
TOOL_PARAM_SCHEMAS: dict[str, tuple[AssistantToolParamSchema, ...]] = {
    "get_context_pack": (
        AssistantToolParamSchema("manifest_id", "string", description="Context pack manifest ID"),
    ),
    "list_context_packs": (
        AssistantToolParamSchema("job_id", "string", description="Migration job ID"),
    ),
    "get_job_status": (
        AssistantToolParamSchema("job_id", "string", description="Migration job ID"),
    ),
    "get_command_output_window": (
        AssistantToolParamSchema("job_id", "string", description="Migration job ID"),
        AssistantToolParamSchema("command_id", "string", description="Command execution ID"),
        AssistantToolParamSchema(
            "stream", "string",
            description="Stream name (stdout or stderr)",
            allowed_values=("stdout", "stderr"),
        ),
        AssistantToolParamSchema(
            "after_offset", "integer",
            description="Byte offset to start reading from",
            min_value=0,
        ),
        AssistantToolParamSchema(
            "max_bytes", "integer",
            description="Maximum bytes to read",
            min_value=1,
            max_value=1_000_000,
        ),
    ),
    "list_artifacts": (
        AssistantToolParamSchema("job_id", "string", description="Migration job ID"),
    ),
    "list_model_invocations": (
        AssistantToolParamSchema("job_id", "string", description="Migration job ID"),
    ),
    "get_pipeline_info": (
        AssistantToolParamSchema("pipeline_id", "string", description="Pipeline ID"),
        AssistantToolParamSchema("pipeline_version", "string", description="Pipeline version"),
    ),
    "get_stage_chain": (
        AssistantToolParamSchema("job_id", "string", description="Migration job ID"),
    ),
    "list_audit_records": (
        AssistantToolParamSchema("job_id", "string", description="Migration job ID", required=False),
    ),
    "retrieve_evidence": (
        AssistantToolParamSchema("source_type", "string", description="Evidence source type (artifact, command_output)"),
        AssistantToolParamSchema("source_id", "string", description="Source identifier"),
        AssistantToolParamSchema("prefix", "string", description="Path prefix for resolve_and_retrieve"),
        AssistantToolParamSchema("max_files", "integer", description="Maximum files to retrieve", max_value=50, required=False),
        AssistantToolParamSchema("max_depth", "integer", description="Maximum directory depth", max_value=10, required=False),
    ),
}


def get_tool_param_schema(tool_name: str) -> tuple[AssistantToolParamSchema, ...]:
    """Get the parameter schema for a tool.

    Returns an empty tuple if the tool has no defined schema.
    """
    return TOOL_PARAM_SCHEMAS.get(tool_name, ())
