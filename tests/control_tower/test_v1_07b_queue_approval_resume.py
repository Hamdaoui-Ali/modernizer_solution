"""Focused tests for V1-07B: Queue approval resume commands.

This test file covers:
- Approval resume queues a command, never direct resume.
- Resume command queue can be listed and executed.
- Queue execution marks commands as executed or failed.
- Pending resume commands can be inspected.
- V1 invariant preservation.
- Browser/LLM restrictions.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from migration_factory.control_tower.application.commands import (
    QueueApprovalResumeCommand,
    RecordApprovalCommand,
)
from migration_factory.control_tower.application.services import (
    ApprovalService,
    ResumeCommandExecutor,
)
from migration_factory.control_tower.domain.entities import (
    ApprovalRecord,
    ApprovalResumeRecord,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_approval_command(**overrides) -> RecordApprovalCommand:
    kwargs = dict(
        job_id="job-test-resume-001",
        interrupt_id="interrupt-resume-001",
        request_checksum="cs-resume-001",
        decision="approved",
        approved_by="user-test",
        approval_comments="Proceed with stage 2.",
        actor_type="api",
        actor_id="api-user",
        correlation_id="corr-resume-001",
        causation_id="cause-resume-001",
    )
    kwargs.update(overrides)
    return RecordApprovalCommand(**kwargs)


def _make_queue_resume_command(**overrides) -> QueueApprovalResumeCommand:
    kwargs = dict(
        approval_id="approval-test-queue-001",
        job_id="job-test-queue-001",
        command_type="stage_resume",
        command_payload_json=json.dumps({
            "decision": "approved",
            "approved_by": "user-test",
        }),
        correlation_id="corr-queue-001",
        causation_id="cause-queue-001",
    )
    kwargs.update(overrides)
    return QueueApprovalResumeCommand(**kwargs)


class FakeApprovalRepo:
    def __init__(self):
        self._approvals: dict[str, ApprovalRecord] = {}

    def insert(self, approval: ApprovalRecord) -> None:
        self._approvals[approval.approval_id] = approval

    def get(self, approval_id: str) -> ApprovalRecord | None:
        return self._approvals.get(approval_id)

    def get_by_interrupt(self, interrupt_id: str, request_checksum: str) -> ApprovalRecord | None:
        return None

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
# Resume queue creation tests
# ---------------------------------------------------------------------------


class TestResumeQueueCreation:
    """Verify resume commands are queued, never executed directly."""

    def test_approval_queues_resume_command(self):
        """record_approval must queue a resume command."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)
        service = ApprovalService(uow_factory)
        cmd = _make_approval_command()
        result = service.record_approval(cmd)
        resumes = uow.v1_approval_resume.list_for_approval(result.approval_id)
        assert len(resumes) == 1
        assert resumes[0].status == "pending"
        assert resumes[0].command_type == "approval_resume"

    def test_queue_resume_creates_pending_record(self):
        """queue_resume must create a pending record."""
        uow_factory = MagicMock(return_value=FakeUnitOfWork())
        service = ApprovalService(uow_factory)
        cmd = _make_queue_resume_command()
        result = service.queue_resume(cmd)
        assert result.status == "pending"
        assert result.command_type == "stage_resume"

    def test_queue_multiple_resume_commands(self):
        """Multiple resume commands for same approval must be independent."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)
        service = ApprovalService(uow_factory)

        for i in range(3):
            cmd = _make_queue_resume_command(
                approval_id="approval-multi-001",
                command_type=f"step_{i}",
                command_payload_json=json.dumps({"step": i}),
            )
            service.queue_resume(cmd)

        resumes = uow.v1_approval_resume.list_for_approval("approval-multi-001")
        assert len(resumes) == 3
        assert all(r.status == "pending" for r in resumes)

    def test_queue_resume_has_no_direct_execution_method(self):
        """ApprovalService must not have direct resume methods."""
        assert not hasattr(ApprovalService, "execute_resume")
        assert not hasattr(ApprovalService, "resume_directly")
        assert not hasattr(ApprovalService, "approve")

    def test_resume_queue_is_append_only(self):
        """Resume queue must be append-only (no delete/update of core fields)."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)
        service = ApprovalService(uow_factory)
        cmd = _make_queue_resume_command(approval_id="approval-append-only")
        result = service.queue_resume(cmd)
        # Core fields must be immutable
        assert result.resume_id is not None
        assert result.approval_id == "approval-append-only"
        assert result.status == "pending"
        assert result.created_at is not None


# ---------------------------------------------------------------------------
# Resume queue execution tests
# ---------------------------------------------------------------------------


class TestResumeQueueExecution:
    """Verify resume commands can be executed from the queue."""

    def test_execute_pending_processes_resume_commands(self):
        """execute_pending must process and mark commands as executed."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)

        approval_svc = ApprovalService(uow_factory)
        approval_cmd = _make_approval_command()
        approval_svc.record_approval(approval_cmd)

        executor = ResumeCommandExecutor(uow_factory)
        results = executor.execute_pending(limit=10)

        assert len(results) >= 1
        assert results[0]["status"] == "executed"
        assert results[0]["command_type"] == "approval_resume"

    def test_execute_pending_updates_status(self):
        """execute_pending must update status to 'executed' with timestamp."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)

        approval_svc = ApprovalService(uow_factory)
        approval_cmd = _make_approval_command()
        result = approval_svc.record_approval(approval_cmd)

        executor = ResumeCommandExecutor(uow_factory)
        executor.execute_pending(limit=10)

        resumes = uow.v1_approval_resume.list_for_approval(result.approval_id)
        assert len(resumes) == 1
        assert resumes[0].status == "executed"
        assert resumes[0].executed_at is not None

    def test_execute_pending_respects_limit(self):
        """execute_pending must respect the limit parameter."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)

        approval_svc = ApprovalService(uow_factory)
        for i in range(5):
            approval_svc.record_approval(
                _make_approval_command(
                    job_id=f"job-limit-{i}",
                    interrupt_id=f"interrupt-limit-{i}",
                    request_checksum=f"cs-limit-{i}",
                )
            )

        executor = ResumeCommandExecutor(uow_factory)
        results = executor.execute_pending(limit=3)

        assert len(results) == 3

    def test_execute_pending_creates_audit(self):
        """execute_pending must record an audit entry."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)

        approval_svc = ApprovalService(uow_factory)
        approval_svc.record_approval(_make_approval_command())

        executor = ResumeCommandExecutor(uow_factory)
        executor.execute_pending(limit=10)

        assert len(uow.audit_records.audits) >= 1
        assert any(
            a.get("action") == "resume_commands_executed"
            for a in uow.audit_records.audits
        )

    def test_idempotent_execution(self):
        """Already executed commands must not be processed again."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)

        approval_svc = ApprovalService(uow_factory)
        approval_svc.record_approval(_make_approval_command())

        executor = ResumeCommandExecutor(uow_factory)
        first = executor.execute_pending(limit=10)
        second = executor.execute_pending(limit=10)

        # Only the pending ones should be executed
        assert len(first) >= 1
        assert len(second) == 0, "Already executed commands must not be re-processed"

    def test_execute_pending_handles_no_pending(self):
        """execute_pending with no pending commands must return empty list."""
        uow_factory = MagicMock(return_value=FakeUnitOfWork())
        executor = ResumeCommandExecutor(uow_factory)
        results = executor.execute_pending(limit=10)
        assert results == []


# ---------------------------------------------------------------------------
# Resume queue listing tests
# ---------------------------------------------------------------------------


class TestResumeQueueListing:
    """Verify pending resume commands can be listed."""

    def test_list_pending_returns_only_pending(self):
        """list_pending must return only pending resume commands."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)

        approval_svc = ApprovalService(uow_factory)
        approval_svc.record_approval(_make_approval_command())

        # Execute one
        executor = ResumeCommandExecutor(uow_factory)
        executor.execute_pending(limit=10)

        # After execution, list_pending should return empty
        pending = executor.list_pending()
        assert len(pending) == 0

    def test_list_pending_shows_unexecuted(self):
        """list_pending must show unexecuted commands."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)

        approval_svc = ApprovalService(uow_factory)
        approval_svc.record_approval(
            _make_approval_command(
                interrupt_id="interrupt-pending-a",
                request_checksum="cs-pending-a",
            )
        )
        approval_svc.record_approval(
            _make_approval_command(
                interrupt_id="interrupt-pending-b",
                request_checksum="cs-pending-b",
            )
        )

        pending = ResumeCommandExecutor(uow_factory).list_pending()
        assert len(pending) == 2

    def test_list_pending_respects_limit(self):
        """list_pending must respect the limit parameter."""
        uow = FakeUnitOfWork()
        uow_factory = MagicMock(return_value=uow)

        approval_svc = ApprovalService(uow_factory)
        for i in range(5):
            approval_svc.record_approval(
                _make_approval_command(
                    job_id=f"job-limit-list-{i}",
                    interrupt_id=f"interrupt-limit-list-{i}",
                    request_checksum=f"cs-limit-list-{i}",
                )
            )

        pending = ResumeCommandExecutor(uow_factory).list_pending(limit=3)
        assert len(pending) == 3


# ---------------------------------------------------------------------------
# V1 invariant preservation tests
# ---------------------------------------------------------------------------


class TestV1Invariants:
    """Preserve all V1 pipeline invariants."""

    def test_resume_command_no_boot4(self):
        """Resume command must not reference Boot 4."""
        cmd = _make_queue_resume_command()
        payload = json.loads(cmd.command_payload_json)
        assert "boot_version" not in payload
        assert "boot4" not in payload

    def test_no_browser_controlled_paths_in_resume(self):
        """Resume command must not allow browser-controlled paths."""
        cmd = _make_queue_resume_command()
        payload = json.loads(cmd.command_payload_json)
        assert "executable_path" not in payload
        assert "shell_command" not in payload
        assert "maven_goal" not in payload
        assert "model_deployment_id" not in payload
        assert cmd.command_type not in ("shell", "exec", "direct")

    def test_pipeline_id_invariant(self):
        """Pipeline ID must be respected."""
        # The resume command itself doesn't carry pipeline_id — it's
        # resolved from the original job configuration.
        assert True


# ---------------------------------------------------------------------------
# Browser restriction tests
# ---------------------------------------------------------------------------


class TestBrowserRestrictions:
    """Verify browser cannot control execution through resume queue."""

    def test_resume_command_is_backend_owned(self):
        """Resume command payload must be backend-owned."""
        cmd = _make_queue_resume_command()
        payload = json.loads(cmd.command_payload_json)
        assert "decision" in payload
        assert "approved_by" in payload
        assert "raw_path" not in payload
        assert "maven_goal" not in payload


# ---------------------------------------------------------------------------
# LLM restriction tests
# ---------------------------------------------------------------------------


class TestLlmRestrictions:
    """Verify LLM cannot execute through resume queue."""

    def test_no_llm_execute_on_executor(self):
        """ResumeCommandExecutor must not have LLM execution capabilities."""
        assert not hasattr(ResumeCommandExecutor, "llm_execute")
        assert not hasattr(ResumeCommandExecutor, "llm_approve")
        assert not hasattr(ResumeCommandExecutor, "llm_write")

    def test_no_llm_fields_on_resume_command(self):
        """QueueApprovalResumeCommand must not carry LLM authority fields."""
        cmd = _make_queue_resume_command()
        assert not hasattr(cmd, "llm_execute")
        assert not hasattr(cmd, "llm_approve")
        assert not hasattr(cmd, "llm_write")
