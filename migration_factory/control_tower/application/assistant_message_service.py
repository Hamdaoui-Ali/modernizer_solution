"""Assistant message flow and streaming for V1-16B.

Provides redacted streaming of assistant tool results using the
V1-16A read-only assistant tool contracts. The stream endpoint
ensures all tool outputs are redacted (V1-00D/V1-11C) and that
the assistant never executes commands, approves decisions, writes
files, mutates DB state, or bypasses approval gates.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from migration_factory.control_tower.application.assistant_tools import (
    AssistantToolCallBuilder,
    AssistantToolCallRecord,
    get_assistant_tool_def,
    is_assistant_tool_allowed,
)
from migration_factory.control_tower.application.context_pack_redaction import (
    redact_manifest_field,
)
from migration_factory.control_tower.application.redaction import (
    redact_absolute_paths,
    redact_model_summary,
    redact_public_value,
)


# ── Assistant message types ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """An immutable assistant message exchanged via the stream.

    Attributes:
        message_id: Unique message identifier.
        role: Message role (user, assistant, tool_result).
        content: Message content (already redacted for assistant/tool_result).
        tool_name: Name of the tool (for tool_result messages).
        tool_call_id: ID linking the tool call to its result (for tool_result messages).
        redacted: Whether the content has been redacted.
        created_at: ISO timestamp of message creation.
    """

    message_id: str
    role: str  # "user", "assistant", "tool_result"
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    redacted: bool = True
    created_at: str = ""


# ── Stream event types ────────────────────────────────────────────


STREAM_EVENT_MESSAGE = "message"
STREAM_EVENT_TOOL_CALL = "tool_call"
STREAM_EVENT_TOOL_RESULT = "tool_result"
STREAM_EVENT_ERROR = "error"
STREAM_EVENT_DONE = "done"


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """An immutable stream event sent to the assistant stream consumer.

    Attributes:
        event_type: Type of stream event (message, tool_call, tool_result, error, done).
        data_json: Canonical JSON payload for the event (already redacted).
        sequence: Monotonic sequence number for ordering.
    """

    event_type: str
    data_json: str
    sequence: int


# ── Assistant message service ─────────────────────────────────────


class AssistantMessageService:
    """Service for managing assistant message flow with redaction.

    Processes tool calls from the assistant, routes them to the
    appropriate read-only tool, redacts the output, and delivers
    stream events.
    """

    def __init__(self, queries_service) -> None:
        self._queries = queries_service

    def process_tool_call(
        self,
        *,
        tool_name: str,
        parameters: dict[str, Any],
        tool_call_id: str,
        redact: bool = True,
    ) -> tuple[AssistantToolCallRecord, StreamEvent]:
        """Process a single assistant tool call and produce a stream event.

        Steps:
        1. Validate tool name against allowlist.
        2. Redact parameters for audit logging.
        3. Execute the read-only query through bounded queries.
        4. Redact the output.
        5. Build a StreamEvent with redacted data.

        Args:
            tool_name: The tool name from the allowlist.
            parameters: Tool parameters dict.
            tool_call_id: Unique ID for this tool call.
            redact: If True, apply redaction to output (default: True).

        Returns:
            Tuple of (AssistantToolCallRecord, StreamEvent).

        Raises:
            ValueError: If tool is not in allowlist or parameters invalid.
        """
        start_ms = int(time.time() * 1000)

        # Validate tool
        tool_def = get_assistant_tool_def(tool_name)
        if tool_def is None:
            raise ValueError(f"Tool {tool_name!r} is not in the assistant tool allowlist")

        # Redact parameters for audit
        redacted_params = self._redact_parameters(parameters)

        # Execute the query (read-only by construction)
        try:
            raw_result = self._execute_tool_query(tool_name, parameters)
        except Exception as exc:
            duration_ms = int(time.time() * 1000) - start_ms
            builder = AssistantToolCallBuilder()
            record = (
                builder.with_tool(tool_name)
                .with_parameters(redacted_params)
                .with_error(str(exc))
                .build(duration_ms=duration_ms)
            )
            error_event = StreamEvent(
                event_type=STREAM_EVENT_ERROR,
                data_json=json.dumps(
                    {"tool_call_id": tool_call_id, "error": str(exc)},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                sequence=0,
            )
            return record, error_event

        # Build redacted output
        duration_ms = int(time.time() * 1000) - start_ms
        builder = AssistantToolCallBuilder()
        record = (
            builder.with_tool(tool_name)
            .with_parameters(redacted_params)
            .build(raw_output=raw_result, duration_ms=duration_ms)
        )

        # Build stream event with redacted data
        event_data = json.dumps(
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "result": record.redacted_output,
                "truncated": record.truncated,
                "duration_ms": duration_ms,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

        event = StreamEvent(
            event_type=STREAM_EVENT_TOOL_RESULT,
            data_json=event_data,
            sequence=0,
        )

        return record, event

    def _execute_tool_query(self, tool_name: str, parameters: dict[str, Any]) -> str:
        """Execute a read-only query based on the tool name.

        All queries go through the bounded ControlTowerQueryService
        which enforces output windows, limits, and read-only access.
        """
        job_id = parameters.get("job_id")

        if tool_name == "get_job_status":
            dto = self._queries.get_migration_job(job_id)
            return json.dumps(
                {
                    "job_id": dto.job_id,
                    "status": dto.status.value if hasattr(dto.status, "value") else str(dto.status),
                    "version": dto.version,
                    "created_at": dto.created_at,
                    "updated_at": dto.updated_at,
                },
                separators=(",", ":"),
                sort_keys=True,
            )

        elif tool_name == "get_context_pack":
            manifest_id = parameters.get("manifest_id")
            record = self._queries.get_context_pack_manifest(manifest_id)
            if record is None:
                return json.dumps({"error": "Context pack not found"})
            return json.dumps(
                {
                    "manifest_id": record.manifest_id,
                    "pack_type": record.pack_type,
                    "pack_version": record.pack_version,
                    "title": record.title,
                    "description": record.description,
                    "evidence_refs_json": record.evidence_refs_json,
                    "redacted_summary": record.redacted_summary,
                    "checksum": record.checksum,
                    "created_at": record.created_at,
                },
                separators=(",", ":"),
                sort_keys=True,
            )

        elif tool_name == "list_context_packs":
            records = self._queries.list_context_pack_manifests_for_job(job_id)
            return json.dumps(
                [
                    {
                        "manifest_id": r.manifest_id,
                        "pack_type": r.pack_type,
                        "title": r.title,
                        "created_at": r.created_at,
                    }
                    for r in records
                ],
                separators=(",", ":"),
                sort_keys=True,
            )

        elif tool_name == "get_command_output_window":
            command_id = parameters.get("command_id")
            stream = parameters.get("stream", "stdout")
            after_offset = parameters.get("after_offset", 0)
            max_bytes = parameters.get("max_bytes", 10_000)
            window = self._queries.get_command_output_window(
                job_id,
                command_id,
                stream=stream,
                after_offset=after_offset,
                max_bytes=max_bytes,
            )
            return json.dumps(
                {
                    "command_id": window.command_id,
                    "stream": window.stream,
                    "start_offset": window.start_offset,
                    "next_offset": window.next_offset,
                    "data": window.data,
                    "truncated": window.truncated,
                    "terminal": window.terminal,
                },
                separators=(",", ":"),
                sort_keys=True,
            )

        elif tool_name == "list_artifacts":
            dtos = self._queries.list_artifacts(job_id)
            return json.dumps(
                [
                    {
                        "artifact_id": a.artifact_id,
                        "artifact_type": a.artifact_type,
                        "relative_path": a.relative_path,
                        "size_bytes": a.size_bytes,
                        "checksum": a.checksum,
                    }
                    for a in dtos
                ],
                separators=(",", ":"),
                sort_keys=True,
            )

        elif tool_name == "list_model_invocations":
            records = self._queries.list_model_invocations_for_job(job_id)
            return json.dumps(
                [
                    {
                        "invocation_id": r.invocation_id,
                        "model_name": r.model_name,
                        "prompt_tokens": r.prompt_tokens,
                        "completion_tokens": r.completion_tokens,
                        "total_tokens": r.total_tokens,
                        "redacted_summary": r.redacted_summary,
                        "created_at": r.created_at,
                    }
                    for r in records
                ],
                separators=(",", ":"),
                sort_keys=True,
            )

        elif tool_name == "get_pipeline_info":
            pipeline_id = parameters.get("pipeline_id")
            pipeline_version = parameters.get("pipeline_version")
            dto = self._queries.get_pipeline_definition(pipeline_id, pipeline_version)
            return json.dumps(
                {
                    "pipeline_id": dto.pipeline_id,
                    "pipeline_version": dto.pipeline_version,
                    "display_name": dto.display_name,
                    "graph_version": dto.graph_version,
                },
                separators=(",", ":"),
                sort_keys=True,
            )

        elif tool_name == "get_stage_chain":
            entries = self._queries.get_stage_chain(job_id)
            return json.dumps(
                [
                    {
                        "stage_index": e.stage_index,
                        "stage_run_id": e.stage_run_id,
                        "chain_status": e.chain_status,
                        "input_source_kind": e.input_source_kind,
                        "output_artifact_id": e.output_artifact_id,
                        "created_at": e.created_at,
                    }
                    for e in entries
                ],
                separators=(",", ":"),
                sort_keys=True,
            )

        elif tool_name == "list_audit_records":
            if job_id:
                records = self._queries.list_audit_records_for_job(job_id)
            else:
                records = self._queries.list_audit_records()
            return json.dumps(
                [
                    {
                        "audit_id": a.audit_id,
                        "action": a.action,
                        "actor_type": a.actor_type,
                        "created_at": a.created_at,
                    }
                    for a in records
                ],
                separators=(",", ":"),
                sort_keys=True,
            )

        elif tool_name == "retrieve_evidence":
            from migration_factory.control_tower.application.retrievers import (
                BoundedEvidenceRetriever,
                EvidenceBounds,
                EvidenceSourceRepository,
            )

            # For V1-16B, evidence retrieval returns a bounded ref list
            source_type = parameters.get("source_type", "artifact")
            source_id = parameters.get("source_id", "")
            prefix = parameters.get("prefix", "")
            max_files = parameters.get("max_files", 10)
            max_depth = parameters.get("max_depth", 3)

            return json.dumps(
                {
                    "source_type": source_type,
                    "source_id": source_id,
                    "prefix": prefix,
                    "status": "Evidence retrieval tool defined; "
                    "requires EvidenceSourceRepository implementation in the adapter layer.",
                },
                separators=(",", ":"),
                sort_keys=True,
            )

        else:
            raise ValueError(f"Unhandled tool: {tool_name}")

    def _redact_parameters(self, params: dict[str, Any]) -> dict[str, Any]:
        """Redact tool parameters for audit logging."""
        redacted: dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, str):
                # Redact values that look like paths or secrets
                redacted[key] = redact_absolute_paths(value)
                redacted[key] = redact_model_summary(redacted[key])
            elif isinstance(value, dict):
                redacted[key] = redact_public_value(value)
            else:
                redacted[key] = value
        return redacted

    def create_done_event(self, sequence: int) -> StreamEvent:
        """Create a 'done' stream event to signal stream completion."""
        return StreamEvent(
            event_type=STREAM_EVENT_DONE,
            data_json=json.dumps({"status": "complete"}, separators=(",", ":"), sort_keys=True),
            sequence=sequence,
        )

    def create_message_event(
        self,
        *,
        message_id: str,
        role: str,
        content: str,
        sequence: int,
        tool_call_id: str | None = None,
    ) -> StreamEvent:
        """Create a stream event from an assistant message.

        Content is always redacted before creating the event.
        """
        redacted_content = redact_absolute_paths(content)
        redacted_content = redact_model_summary(redacted_content)

        data = {
            "message_id": message_id,
            "role": role,
            "content": redacted_content,
        }
        if tool_call_id:
            data["tool_call_id"] = tool_call_id

        return StreamEvent(
            event_type=STREAM_EVENT_MESSAGE,
            data_json=json.dumps(data, separators=(",", ":"), sort_keys=True),
            sequence=sequence,
        )
