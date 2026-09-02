"""Focused tests for V1-07A: Persist Control Tower approvals.

This test file covers:
- Approval creation is idempotent by interrupt/checksum.
- Approval resume queues a command, never direct resume.
- Approval persistence and listing.
- V1 invariant preservation.
- Browser/LLM restrictions.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from migration_factory.control_tower.application.commands import (
    QueueApprovalResumeCommand,
    RecordApprovalCommand,
)
from migration_factory.control_tower.application.services import ApprovalService
from migration_factory.control_tower.domain.entities import ApprovalRecord, ApprovalResumeRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_approval_command(**overrides) -> RecordApprovalCommand:
    kwargs = dict(
        job_id="job-test-approval-001",
        interrupt_id="interrupt-test-001",
        request_checksum="sha256-test-checksum-001",
        decision="approved",
        approved_by="user-test",
        approval_comments="Looks good, proceed.",
        actor_type="api",
        actor_id="api-user",
        correlation_id="corr-test-001",
        causation_id="cause-test-001",
    )
    kwargs.update(overrides)
    return RecordApprovalCommand(**kwargs)


def _make_queue_resume_command(**overrides) -> QueueApprovalResumeCommand:
    kwargs = dict(
        approval_id="approval-test-001",
        job_id="job-test-queue-001",
        command_type="test_resume",
        command_payload_json=json.dumps({"test": True}),
        correlation_id="corr-queue-001",
        causation_id="cause-queue-001",
    )
    kwargs.update(overrides)
    return QueueApprovalResumeCommand(**kwargs)


class FakeApprovalRepo:
    def __init__(self):
        self._approvals: dict[str, ApprovalRecord] = {}
        self._by_interrupt: dict[tuple[str, str], ApprovalRecord] = {}

    def insert(self, approval: ApprovalRecord) -> None:
        self._approvals[approval.approval_id] = approval
        self._by_interrupt[(approval.interrupt_id, approval.request_checksum)] = approval

    def get(self, approval_id: str) -> ApprovalRecord | None:
        return self._approvals.get(approval_id)

    def get_by_interrupt(self, interrupt_id: str, request_checksum: str) -> ApprovalRecord | None:
        return self._by_interrupt.get((interrupt_id, request_checksum))

    def list_for_job(self, job_id: str) -> tuple[ApprovalRecord, ...]:
        return tuple(a for a in self._approvals.values() if a.job_id == job_id)


class FakeResumeRepo:
    def __init__(self):
        self._resumes: dict[str, ApprovalResumeRecord] = {}

    def insert(self, resume: ApprovalResumeRecord) -> None:
        self._resumes[resume.resume_id] = resume

    def list_pending(self) -> tuple[ApprovalResumeRecord, ...]:
        return tuple(r for r in self._resumes.values() if r.status == "pending")

    def list_for_approval(self, approval_id: str) -> tuple[ApprovalResumeRecord, ...]:
        return tuple(r for r in self._resumes.values() if r.approval_id == approval_id)

    def update_status(self, resume_id: str, status: str, executed_at: str | None = None,
                      failure_reason: str | None = None) -> None:
        old = self._resumes.get(resume_id)
        if old:
            self._resumes[resume_id] = ApprovalResumeRecord(
                resume_id=old.resume_id,
                approval_id=old.approval_id,
                job_id=old.job_id,
                command_type=old.command_type,
                command_payload_json=old.command_payload_json,
                status=status,
                created_at=old.created_at,
                executed_at=executed_at or old.executed_at,
                failure_reason=failure_reason or old.failure_reason,
                correlation_id=old.correlation_id,
                causation_id=old.causation_id,
            )


class FakeAuditRepo:
    def __init__(self):
        self.audits: list[dict] = []

    def append_global_audit(self, **kwargs) -> None:
        self.audits.append(kwargs)


class FakeUnitOfWork:
    def __init__(self):
        self.v1_approvals = FakeApprovalRepo()
        self.v1_approval_resume = FakeResumeRepo()
        self.audit_records = FakeAuditRepo()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return None


# ---------------------------------------------------------------------------
# Approval creation tests
# ---------------------------------------------------------------------------


class TestApprovalCreation:
    """Verify approval creation and persistence."""

    def test_record_approval_creates_record(self):
        """ApprovalService.record_approval must create an ApprovalRecord."""
        uow_factory = MagicMock(return_value=FakeUnitOfWork())
        service = ApprovalService(uow_factory)
        cmd = _make_approval_command()
        result = service.record_approval(cmd)
        assert isinstance(result, ApprovalRecord)
        assert result.approval_id.startswith("approval-")
        assert result.decision == "approved"
        assert result.interrupt_id == "interrupt-test-001"
        assert result.request_checksum == "sha256-test-checksum-001"

    def test_record_approval_sets_payload(self):
        """ApprovalRecord must include a checksummed payload."""
        uow_factory = MagicMock(return_value=FakeUnitOfWork())
        service = ApprovalService(uow_factory)
        cmd = _make_approval_command()
        result = service.record_approval(cmd)
        assert result.payload_json
        assert result.payload_checksum
        payload = json.loads(result.payload_json)
        assert payload["job_id"] == cmd.job_id
        assert payload["interrupt_id"] == cmd.interrupt_id
        assert payload["decision"] == cmd.decision

    def test_record_approval_queues_resume(self):
        """ApprovalService.record_approval must queue a resume command."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)
        service = ApprovalService(uow_factory)
        cmd = _make_approval_command()
        result = service.record_approval(cmd)
        # Verify a resume record was created
        resumes = uow.v1_approval_resume.list_for_approval(result.approval_id)
        assert len(resumes) == 1
        assert resumes[0].status == "pending"
        assert resumes[0].command_type == "approval_resume"
        assert resumes[0].approval_id == result.approval_id

    def test_record_approval_creates_audit(self):
        """ApprovalService.record_approval must create an audit record."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)
        service = ApprovalService(uow_factory)
        cmd = _make_approval_command()
        service.record_approval(cmd)
        assert len(uow.audit_records.audits) >= 1
        assert any(
            a.get("action") == "approval_recorded" for a in uow.audit_records.audits
        )

    def test_record_approval_rejected_decision(self):
        """Record approval with 'rejected' decision."""
        uow_factory = MagicMock(return_value=FakeUnitOfWork())
        service = ApprovalService(uow_factory)
        cmd = _make_approval_command(decision="rejected")
        result = service.record_approval(cmd)
        assert result.decision == "rejected"

    def test_record_approval_replan_decision(self):
        """Record approval with 'replan_required' decision."""
        uow_factory = MagicMock(return_value=FakeUnitOfWork())
        service = ApprovalService(uow_factory)
        cmd = _make_approval_command(decision="replan_required")
        result = service.record_approval(cmd)
        assert result.decision == "replan_required"


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


class TestApprovalIdempotency:
    """Verify approval creation is idempotent by interrupt/checksum."""

    def test_idempotent_by_interrupt_checksum(self):
        """Same (interrupt_id, request_checksum) must return existing record."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)
        service = ApprovalService(uow_factory)

        cmd = _make_approval_command(
            interrupt_id="interrupt-idemp-001",
            request_checksum="cs-idemp-001",
        )
        first = service.record_approval(cmd)
        second = service.record_approval(cmd)

        assert first.approval_id == second.approval_id
        assert first.decision == second.decision
        assert first.created_at == second.created_at

    def test_idempotent_does_not_create_duplicate_resume(self):
        """Idempotent approval must not create duplicate resume commands."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)
        service = ApprovalService(uow_factory)

        cmd = _make_approval_command(
            interrupt_id="interrupt-resume-idemp-001",
            request_checksum="cs-resume-idemp-001",
        )
        first = service.record_approval(cmd)
        service.record_approval(cmd)

        resumes = uow.v1_approval_resume.list_for_approval(first.approval_id)
        assert len(resumes) == 1, "Idempotent call must not create duplicate resume"

    def test_different_interrupt_id_new_approval(self):
        """Different interrupt_id must create a new approval."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)
        service = ApprovalService(uow_factory)

        cmd1 = _make_approval_command(
            interrupt_id="interrupt-diff-001",
            request_checksum="cs-diff-001",
        )
        cmd2 = _make_approval_command(
            interrupt_id="interrupt-diff-002",
            request_checksum="cs-diff-002",
        )
        a1 = service.record_approval(cmd1)
        a2 = service.record_approval(cmd2)

        assert a1.approval_id != a2.approval_id
        assert a1.interrupt_id != a2.interrupt_id


# ---------------------------------------------------------------------------
# Approval listing tests
# ---------------------------------------------------------------------------


class TestApprovalListing:
    """Verify approval listing and retrieval."""

    def test_get_approval_by_id(self):
        """Get approval by approval_id must return the record."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)
        service = ApprovalService(uow_factory)

        cmd = _make_approval_command()
        created = service.record_approval(cmd)
        fetched = service.get_approval(created.approval_id)

        assert fetched is not None
        assert fetched.approval_id == created.approval_id
        assert fetched.decision == created.decision

    def test_get_approval_returns_none_for_missing(self):
        """get_approval must return None for nonexistent approval."""
        uow_factory = MagicMock(return_value=FakeUnitOfWork())
        service = ApprovalService(uow_factory)
        assert service.get_approval("nonexistent") is None

    def test_list_approvals_for_job(self):
        """list_approvals_for_job must return all approvals for a job."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)
        service = ApprovalService(uow_factory)

        service.record_approval(_make_approval_command(
            job_id="job-list-001", interrupt_id="interrupt-list-a", request_checksum="cs-list-a"
        ))
        service.record_approval(_make_approval_command(
            job_id="job-list-001", interrupt_id="interrupt-list-b", request_checksum="cs-list-b"
        ))
        service.record_approval(_make_approval_command(
            job_id="job-list-002", interrupt_id="interrupt-list-c", request_checksum="cs-list-c"
        ))

        approvals = service.list_approvals_for_job("job-list-001")
        assert len(approvals) == 2

        approvals2 = service.list_approvals_for_job("job-list-002")
        assert len(approvals2) == 1


# ---------------------------------------------------------------------------
# Resume queue tests
# ---------------------------------------------------------------------------


class TestResumeQueue:
    """Verify approval resume queues commands, never direct resume."""

    def test_queue_resume_creates_record(self):
        """queue_resume must create a pending resume record."""
        uow_factory = MagicMock(return_value=FakeUnitOfWork())
        service = ApprovalService(uow_factory)
        cmd = _make_queue_resume_command()
        result = service.queue_resume(cmd)
        assert isinstance(result, ApprovalResumeRecord)
        assert result.status == "pending"
        assert result.command_type == "test_resume"

    def test_queue_resume_has_no_direct_execution(self):
        """ApprovalService must not have direct resume methods."""
        assert not hasattr(ApprovalService, "execute_resume"), \
            "ApprovalService must not have direct resume execution"
        assert not hasattr(ApprovalService, "resume_directly"), \
            "ApprovalService must not have direct resume"
        assert not hasattr(ApprovalService, "approve"), \
            "ApprovalService must not have approve method"

    def test_approval_record_includes_resume_queue(self):
        """record_approval must queue a resume with type 'approval_resume'."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)
        service = ApprovalService(uow_factory)
        cmd = _make_approval_command()
        result = service.record_approval(cmd)
        resumes = uow.v1_approval_resume.list_for_approval(result.approval_id)
        assert len(resumes) == 1
        assert resumes[0].command_type == "approval_resume"
        # Verify the resume payload contains the decision
        payload = json.loads(resumes[0].command_payload_json)
        assert payload["decision"] == cmd.decision
        assert payload["approved_by"] == cmd.approved_by


# ---------------------------------------------------------------------------
# V1 invariant preservation tests
# ---------------------------------------------------------------------------


class TestV1Invariants:
    """Preserve all V1 pipeline invariants."""

    def test_no_boot4_in_approval_contract(self):
        """Approval contract must not reference Boot 4."""
        cmd = _make_approval_command()
        assert not hasattr(cmd, "boot_version")
        assert not hasattr(cmd, "boot4")

    def test_no_browser_controlled_paths(self):
        """Approval command must not allow browser-controlled paths."""
        cmd = _make_approval_command()
        assert not hasattr(cmd, "executable_path")
        assert not hasattr(cmd, "shell_command")
        assert not hasattr(cmd, "maven_goal")
        assert not hasattr(cmd, "working_directory")
        assert not hasattr(cmd, "model_deployment_id")


# ---------------------------------------------------------------------------
# Browser restriction tests
# ---------------------------------------------------------------------------


class TestBrowserRestrictions:
    """Verify browser cannot control execution paths through approvals."""

    def test_approval_decision_only(self):
        """Approval endpoint must only accept decision fields, not execution config."""
        cmd = _make_approval_command()
        assert cmd.decision in ("approved", "rejected", "replan_required")
        assert not hasattr(cmd, "argv")
        assert not hasattr(cmd, "env")
        assert not hasattr(cmd, "raw_path")


# ---------------------------------------------------------------------------
# LLM restriction tests
# ---------------------------------------------------------------------------


class TestLlmRestrictions:
    """Verify LLM cannot execute, approve, or write files via approvals."""

    def test_no_llm_execute_on_approval_service(self):
        """ApprovalService must not have LLM execution capabilities."""
        assert not hasattr(ApprovalService, "llm_execute")
        assert not hasattr(ApprovalService, "llm_approve")
        assert not hasattr(ApprovalService, "llm_write")

    def test_no_llm_fields_on_record(self):
        """ApprovalRecord must not carry LLM authority fields."""
        cmd = _make_approval_command()
        assert not hasattr(cmd, "llm_execute")
        assert not hasattr(cmd, "llm_approve")
        assert not hasattr(cmd, "llm_write")
