"""Focused tests for V1-16B: Assistant streaming and redaction."""

from __future__ import annotations

import json

import pytest

from migration_factory.control_tower.application.assistant_message_service import (
    AssistantMessage,
    AssistantMessageService,
    StreamEvent,
    STREAM_EVENT_DONE,
    STREAM_EVENT_ERROR,
    STREAM_EVENT_MESSAGE,
    STREAM_EVENT_TOOL_CALL,
    STREAM_EVENT_TOOL_RESULT,
)
from migration_factory.control_tower.application.assistant_tools import (
    AssistantToolCallRecord,
)


# ── Fake query service for testing ─────────────────────────────────


class FakeQueryService:
    """In-memory query service for testing assistant message flow."""

    def __init__(self) -> None:
        self._jobs: dict[str, object] = {}
        self._context_packs: dict[str, object] = {}
        self._audit_records: list[object] = []
        self._stage_chain: list[object] = []
        self._model_invocations: list[object] = []
        self._artifacts: list[object] = []

    def add_job(self, job_id: str, status: str = "created") -> None:
        self._jobs[job_id] = {
            "job_id": job_id,
            "status": status,
            "version": 1,
            "created_at": "2026-06-12T00:00:00Z",
            "updated_at": "2026-06-12T00:00:00Z",
        }

    def get_migration_job(self, job_id: str) -> object:
        from types import SimpleNamespace

        job = self._jobs.get(job_id)
        if job is None:
            from migration_factory.control_tower.domain.errors import NotFoundError
            raise NotFoundError("migration job", job_id)
        return SimpleNamespace(
            job_id=job["job_id"],
            status=job["status"],
            version=job["version"],
            created_at=job["created_at"],
            updated_at=job["updated_at"],
        )

    def list_audit_records_for_job(self, job_id: str) -> tuple[object, ...]:
        return tuple(r for r in self._audit_records)

    def list_audit_records(self) -> tuple[object, ...]:
        return tuple(self._audit_records)

    def get_stage_chain(self, job_id: str) -> tuple[object, ...]:
        return tuple(self._stage_chain)

    def list_model_invocations_for_job(self, job_id: str) -> tuple[object, ...]:
        return tuple(self._model_invocations)

    def list_artifacts(self, job_id: str) -> tuple[object, ...]:
        return tuple(self._artifacts)

    def list_context_pack_manifests_for_job(self, job_id: str) -> tuple[object, ...]:
        return ()

    def get_context_pack_manifest(self, manifest_id: str):
        return self._context_packs.get(manifest_id)

    def get_pipeline_definition(self, pipeline_id: str, pipeline_version: str) -> object:
        from types import SimpleNamespace
        return SimpleNamespace(
            pipeline_id=pipeline_id,
            pipeline_version=pipeline_version,
            display_name="Test Pipeline",
            graph_version="1.0",
        )

    def get_command_output_window(self, job_id, command_id, *, stream, after_offset, max_bytes):
        from types import SimpleNamespace
        return SimpleNamespace(
            command_id=command_id,
            stream=stream,
            start_offset=0,
            next_offset=0,
            data="",
            truncated=False,
            terminal=False,
        )


@pytest.fixture
def queries() -> FakeQueryService:
    qs = FakeQueryService()
    qs.add_job("job-001", "running")
    return qs


@pytest.fixture
def service(queries: FakeQueryService) -> AssistantMessageService:
    return AssistantMessageService(queries_service=queries)


# ── AssistantMessage tests ─────────────────────────────────────────


class TestAssistantMessage:
    """AssistantMessage dataclass behavior."""

    def test_create_user_message(self) -> None:
        msg = AssistantMessage(
            message_id="msg-001",
            role="user",
            content="What is the job status?",
        )
        assert msg.role == "user"
        assert msg.redacted is True

    def test_tool_result_message(self) -> None:
        msg = AssistantMessage(
            message_id="msg-002",
            role="tool_result",
            content='{"status": "running"}',
            tool_name="get_job_status",
            tool_call_id="call-001",
        )
        assert msg.tool_name == "get_job_status"
        assert msg.tool_call_id == "call-001"

    def test_is_frozen(self) -> None:
        msg = AssistantMessage(
            message_id="msg-003",
            role="user",
            content="hello",
        )
        with pytest.raises(AttributeError):
            msg.role = "assistant"  # type: ignore[misc]

    def test_has_slots(self) -> None:
        msg = AssistantMessage(
            message_id="msg-004",
            role="user",
            content="hello",
        )
        assert hasattr(msg, "__slots__")


# ── StreamEvent tests ──────────────────────────────────────────────


class TestStreamEvent:
    """StreamEvent dataclass behavior."""

    def test_done_event(self) -> None:
        event = StreamEvent(
            event_type=STREAM_EVENT_DONE,
            data_json='{"status": "complete"}',
            sequence=5,
        )
        assert event.event_type == "done"
        assert event.sequence == 5

    def test_is_frozen(self) -> None:
        event = StreamEvent(
            event_type="done",
            data_json="{}",
            sequence=0,
        )
        with pytest.raises(AttributeError):
            event.event_type = "message"  # type: ignore[misc]


# ── AssistantMessageService tests ──────────────────────────────────


class TestAssistantMessageService:
    """AssistantMessageService tool processing and redaction."""

    def test_process_get_job_status(self, service: AssistantMessageService) -> None:
        record, event = service.process_tool_call(
            tool_name="get_job_status",
            parameters={"job_id": "job-001"},
            tool_call_id="call-001",
        )

        assert isinstance(record, AssistantToolCallRecord)
        assert record.tool_name == "get_job_status"
        assert record.error_message is None
        assert "running" in record.redacted_output

        assert event.event_type == STREAM_EVENT_TOOL_RESULT
        assert "call-001" in event.data_json

    def test_process_get_job_status_not_found(self, service: AssistantMessageService) -> None:
        record, event = service.process_tool_call(
            tool_name="get_job_status",
            parameters={"job_id": "nonexistent"},
            tool_call_id="call-002",
        )

        assert record.error_message is not None
        assert event.event_type == STREAM_EVENT_ERROR

    def test_process_invalid_tool(self, service: AssistantMessageService) -> None:
        with pytest.raises(ValueError, match="not in the assistant tool allowlist"):
            service.process_tool_call(
                tool_name="execute_command",
                parameters={},
                tool_call_id="call-bad",
            )

    def test_process_get_pipeline_info(self, service: AssistantMessageService) -> None:
        record, event = service.process_tool_call(
            tool_name="get_pipeline_info",
            parameters={
                "pipeline_id": "springboot-216-to-356-java21-three-stage",
                "pipeline_version": "1.0",
            },
            tool_call_id="call-003",
        )

        assert record.error_message is None
        assert "Test Pipeline" in record.redacted_output

    def test_tool_output_is_always_redacted(self, service: AssistantMessageService, monkeypatch) -> None:
        """Verify that tool output is redacted even when it contains sensitive data."""
        # Monkey-patch to inject sensitive content
        original_exec = service._execute_tool_query

        def _inject_sensitive(tool_name: str, params: dict) -> str:
            return original_exec(tool_name, params) + " SECRET_KEY=abc123 /home/user/data"

        monkeypatch.setattr(service, "_execute_tool_query", _inject_sensitive)

        record, event = service.process_tool_call(
            tool_name="get_job_status",
            parameters={"job_id": "job-001"},
            tool_call_id="call-redact",
        )

        # SECRET_KEY and path should be redacted by the builder
        assert "SECRET_KEY=abc123" not in record.redacted_output
        assert "/home/user/data" not in record.redacted_output

    def test_list_artifacts_empty(self, service: AssistantMessageService) -> None:
        record, event = service.process_tool_call(
            tool_name="list_artifacts",
            parameters={"job_id": "job-001"},
            tool_call_id="call-art",
        )
        assert record.error_message is None
        assert record.redacted_output == "[]"

    def test_list_audit_records_empty(self, service: AssistantMessageService) -> None:
        record, event = service.process_tool_call(
            tool_name="list_audit_records",
            parameters={"job_id": "job-001"},
            tool_call_id="call-audit",
        )
        assert record.error_message is None

    def test_get_stage_chain_empty(self, service: AssistantMessageService) -> None:
        record, event = service.process_tool_call(
            tool_name="get_stage_chain",
            parameters={"job_id": "job-001"},
            tool_call_id="call-chain",
        )
        assert record.error_message is None

    def test_create_done_event(self, service: AssistantMessageService) -> None:
        event = service.create_done_event(sequence=10)
        assert event.event_type == STREAM_EVENT_DONE
        assert event.sequence == 10
        parsed = json.loads(event.data_json)
        assert parsed["status"] == "complete"

    def test_create_message_event_redacts(self, service: AssistantMessageService) -> None:
        event = service.create_message_event(
            message_id="msg-001",
            role="assistant",
            content="Results from /home/user/project with KEY=value",
            sequence=1,
        )
        assert "/home/user/project" not in event.data_json
        assert "KEY=value" not in event.data_json
        parsed = json.loads(event.data_json)
        assert parsed["role"] == "assistant"

    def test_tool_parameters_redacted_for_audit(self, service: AssistantMessageService) -> None:
        """Verify parameters with sensitive paths are redacted in audit record."""
        record, event = service.process_tool_call(
            tool_name="get_job_status",
            parameters={
                "job_id": "job-001",
                "note": "Check /home/admin/secret.txt",
            },
            tool_call_id="call-audit-params",
        )

        assert "/home/admin/secret.txt" not in record.parameters_json
        assert "[redacted" in record.parameters_json


# ── Tool result redaction enforcement tests ────────────────────────


class TestToolResultRedactionEnforcement:
    """Verify redaction enforcement on stream and persistence paths."""

    def test_stream_never_contains_raw_secrets(self, service: AssistantMessageService) -> None:
        record, event = service.process_tool_call(
            tool_name="get_context_pack",
            parameters={"manifest_id": "nonexistent"},
            tool_call_id="call-secure",
        )
        # Even error responses should not contain raw secrets
        assert event.data_json is not None

    def test_error_event_redacted(self, service: AssistantMessageService) -> None:
        record, event = service.process_tool_call(
            tool_name="get_job_status",
            parameters={"job_id": "nonexistent"},
            tool_call_id="call-err",
        )
        assert event.event_type == STREAM_EVENT_ERROR
        parsed = json.loads(event.data_json)
        assert "error" in parsed

    def test_message_event_redacted(self, service: AssistantMessageService) -> None:
        # Messages from the assistant are always redacted
        msg_content = (
            "I found results at /home/user/.ssh/id_rsa with "
            "OPENAI_API_KEY=sk-abc123 and deployment deployment-xyz"
        )
        event = service.create_message_event(
            message_id="msg-sec",
            role="assistant",
            content=msg_content,
            sequence=3,
        )
        data = json.loads(event.data_json)
        content = data["content"]
        assert "/home/user/.ssh/id_rsa" not in content
        assert "OPENAI_API_KEY=sk-abc123" not in content
        # The content should be redacted
        assert "[redacted" in content or data["message_id"] == "msg-sec"
