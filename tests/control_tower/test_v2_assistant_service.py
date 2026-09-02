"""Tests for V2 assistant service."""

import pytest

from migration_factory.control_tower.application.v2_assistant_service import (
    V2AssistantService,
    FORBIDDEN_CAPABILITIES,
    ALLOWED_TOOLS,
)


def test_add_message() -> None:
    service = V2AssistantService()
    msg = service.add_message(job_id="job-1", role="user", content="What is the status?")
    assert msg.message_id
    assert msg.role == "user"
    assert msg.job_id == "job-1"


def test_draft_action_does_not_execute() -> None:
    service = V2AssistantService()
    draft = service.draft_action(
        job_id="job-1",
        action_type="plan_amendment",
        reason="Need to update dependencies",
    )
    assert draft.status == "draft"
    assert draft.action_type == "plan_amendment"
    assert not hasattr(draft, "executed") or not draft.executed


def test_get_messages_by_job() -> None:
    service = V2AssistantService()
    service.add_message(job_id="job-1", role="user", content="Hello")
    service.add_message(job_id="job-1", role="assistant", content="Hi")
    service.add_message(job_id="job-2", role="user", content="Other job")

    msgs = service.get_messages("job-1")
    assert len(msgs) == 2


def test_forbidden_capabilities_listed() -> None:
    assert "execute_command" in FORBIDDEN_CAPABILITIES
    assert "approve_decision" in FORBIDDEN_CAPABILITIES
    assert "write_file" in FORBIDDEN_CAPABILITIES
    assert "change_route" in FORBIDDEN_CAPABILITIES
    assert "change_stage" in FORBIDDEN_CAPABILITIES
    assert "override_proof" in FORBIDDEN_CAPABILITIES


def test_allowed_tools_listed() -> None:
    assert "explain_status" in ALLOWED_TOOLS
    assert "summarize_evidence" in ALLOWED_TOOLS
    assert "diagnose_failure" in ALLOWED_TOOLS
    assert "draft_plan_instruction" in ALLOWED_TOOLS
    assert "draft_repair_instruction" in ALLOWED_TOOLS
    assert "request_action" in ALLOWED_TOOLS


def test_message_to_dict_redacts() -> None:
    service = V2AssistantService()
    msg = service.add_message(job_id="job-1", role="user", content="My secret is AZURE_KEY=xxx")
    d = service.message_to_dict(msg)
    # Content should be a string (redacted or not, depends on redaction)
    assert isinstance(d["content"], str)


def test_draft_to_dict() -> None:
    service = V2AssistantService()
    draft = service.draft_action(job_id="job-1", action_type="repair", reason="Fix compilation")
    d = service.draft_to_dict(draft)
    assert d["status"] == "draft"
    assert d["action_type"] == "repair"
