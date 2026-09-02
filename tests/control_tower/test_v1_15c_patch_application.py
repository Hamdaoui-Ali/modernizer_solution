"""Focused tests for V1-15C: Apply approved patch in sandbox.

Covers:
- apply_approved_patch happy path
- Rejection when policy validation fails
- Rejection when snapshot missing
- Patch application record persistence
- Audit event creation
- Query methods
"""

from __future__ import annotations

import hashlib
from uuid import uuid4
from unittest.mock import MagicMock, PropertyMock

import pytest

from migration_factory.control_tower.application.dto import (
    CommandExecutionDto,
    PatchApplicationDto,
    PatchPolicyValidationDto,
    SandboxSnapshotDto,
)
from migration_factory.control_tower.application.patch_policy import PatchPolicyService
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.entities import (
    V1PatchApplicationRecord,
    V1PatchPolicyValidationRecord,
    V1SandboxSnapshotRecord,
)
from migration_factory.control_tower.domain.errors import (
    PatchNotApprovedError,
    PatchSnapshotNotFoundError,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_uow():
    uow = MagicMock()
    uow.v1_patch_policy_validations = MagicMock()
    uow.v1_sandbox_snapshots = MagicMock()
    uow.v1_patch_applications = MagicMock()
    uow.audit_records = MagicMock()
    uow.command_executions = MagicMock()
    return uow


@pytest.fixture
def uow_factory(mock_uow):
    factory = MagicMock()
    factory.return_value.__enter__.return_value = mock_uow
    factory.return_value.__exit__.return_value = None
    return factory


@pytest.fixture
def service(uow_factory):
    return PatchPolicyService(uow_factory)


@pytest.fixture
def valid_validation_record():
    return V1PatchPolicyValidationRecord(
        validation_id=f"ppv-{uuid4().hex}",
        command_id="cmd-123",
        job_id="job-456",
        approved=True,
        validation_code="APPROVED",
        reason_code="policy_pass",
        target_path_hash=hashlib.sha256(b"src/main/java/App.java").hexdigest(),
        patch_size_bytes=42,
        metacharacter_hits=0,
        policy_version="v1.0",
        actor_type="system",
        actor_id="controller",
        created_at=utc_now_text(),
    )


@pytest.fixture
def valid_snapshot_record():
    return V1SandboxSnapshotRecord(
        snapshot_id=f"snp-{uuid4().hex}",
        command_id="cmd-123",
        job_id="job-456",
        stage_index=1,
        sandbox_artifact_id="art-sandbox",
        sandbox_checksum="abc123",
        actor_type="system",
        actor_id="controller",
        created_at=utc_now_text(),
    )


@pytest.fixture
def mock_command():
    return CommandExecutionDto(
        command_id="cmd-123",
        job_id="job-456",
        operation="patch_apply",
        status=CommandState.RUNNING,
        created_at=utc_now_text(),
        updated_at=utc_now_text(),
        correlation_id=None,
        causation_id=None,
    )


@pytest.fixture
def patch_params():
    return {
        "command_id": "cmd-123",
        "job_id": "job-456",
        "target_path": "src/main/java/App.java",
        "patch_content": "- old line\n+ new line",
        "patch_size_bytes": 42,
        "stage_index": 1,
        "approval_id": f"apr-{uuid4().hex}",
        "actor_type": "system",
        "actor_id": "controller",
    }


# ------------------------------------------------------------------
# Happy path
# ------------------------------------------------------------------


class TestApplyApprovedPatch:
    def test_apply_approved_patch_success(
        self, service, patch_params, mock_uow, valid_validation_record, valid_snapshot_record
    ):
        # Setup: validation passes, snapshot exists
        mock_uow.v1_patch_policy_validations.insert.side_effect = None
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = valid_snapshot_record
        mock_uow.v1_patch_applications.insert.side_effect = None

        result = service.apply_approved_patch(**patch_params)

        assert isinstance(result, PatchApplicationDto)
        assert result.command_id == patch_params["command_id"]
        assert result.job_id == patch_params["job_id"]
        assert result.stage_index == patch_params["stage_index"]
        assert result.status == "applied"
        assert result.application_id.startswith("ppa-")

        # Verify patch application was persisted
        mock_uow.v1_patch_applications.insert.assert_called_once()

        # Verify audit event
        mock_uow.audit_records.append_global_audit.assert_called()

    def test_apply_persists_application_record(
        self, service, patch_params, mock_uow, valid_validation_record, valid_snapshot_record
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = valid_snapshot_record

        result = service.apply_approved_patch(**patch_params)

        call_args = mock_uow.v1_patch_applications.insert.call_args
        assert call_args is not None
        record = call_args[0][0]
        assert isinstance(record, V1PatchApplicationRecord)
        assert record.application_id == result.application_id
        assert record.status == "applied"
        assert record.validation_id is not None
        assert record.snapshot_id is not None

    def test_apply_creates_audit_event(
        self, service, patch_params, mock_uow, valid_validation_record, valid_snapshot_record
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = valid_snapshot_record

        service.apply_approved_patch(**patch_params)

        # At least one audit event for patch_applied
        audit_calls = [
            call for call in mock_uow.audit_records.append_global_audit.call_args_list
            if call[1].get("action") == "patch_applied"
        ]
        assert len(audit_calls) >= 1

    def test_apply_with_correlation_id(
        self, service, patch_params, mock_uow, valid_validation_record, valid_snapshot_record
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = valid_snapshot_record
        patch_params["correlation_id"] = "corr-test-789"
        patch_params["causation_id"] = "cause-test-789"

        result = service.apply_approved_patch(**patch_params)
        assert result.correlation_id == "corr-test-789"
        assert result.causation_id == "cause-test-789"


# ------------------------------------------------------------------
# Rejection tests
# ------------------------------------------------------------------


class TestApplyRejected:
    def test_no_snapshot_rejected(
        self, service, patch_params, mock_uow
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = None

        with pytest.raises(PatchSnapshotNotFoundError, match="No sandbox snapshot found"):
            service.apply_approved_patch(**patch_params)

        # No patch application should be recorded
        mock_uow.v1_patch_applications.insert.assert_not_called()

    def test_invalid_path_rejected(
        self, service, patch_params, mock_uow, valid_snapshot_record
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = valid_snapshot_record
        patch_params["target_path"] = "/etc/passwd"

        with pytest.raises(Exception, match="absolute"):
            service.apply_approved_patch(**patch_params)

        mock_uow.v1_patch_applications.insert.assert_not_called()

    def test_missing_approval_rejected(
        self, service, patch_params, mock_uow, valid_snapshot_record
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = valid_snapshot_record
        patch_params["approval_id"] = None

        with pytest.raises(Exception, match="no prior approval"):
            service.apply_approved_patch(**patch_params)

        mock_uow.v1_patch_applications.insert.assert_not_called()

    def test_metacharacter_in_content_rejected(
        self, service, patch_params, mock_uow, valid_snapshot_record
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = valid_snapshot_record
        patch_params["patch_content"] = "- old\n+ `rm -rf /`"

        with pytest.raises(Exception, match="shell metacharacter"):
            service.apply_approved_patch(**patch_params)

        mock_uow.v1_patch_applications.insert.assert_not_called()

    def test_stage_index_out_of_range_rejected(
        self, service, patch_params, mock_uow, valid_snapshot_record
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = valid_snapshot_record
        patch_params["stage_index"] = 0

        with pytest.raises(Exception, match="out of valid range"):
            service.apply_approved_patch(**patch_params)

        mock_uow.v1_patch_applications.insert.assert_not_called()


# ------------------------------------------------------------------
# Query methods
# ------------------------------------------------------------------


class TestQueryPatchApplication:
    def test_get_patch_application_found(self, service, mock_uow):
        record = _make_application_record()
        mock_uow.v1_patch_applications.get.return_value = record

        result = service.get_patch_application(record.application_id)
        assert result is not None
        assert result.application_id == record.application_id
        assert result.status == "applied"

    def test_get_patch_application_not_found(self, service, mock_uow):
        mock_uow.v1_patch_applications.get.return_value = None
        result = service.get_patch_application("nonexistent")
        assert result is None

    def test_get_patch_application_for_command_found(self, service, mock_uow):
        record = _make_application_record()
        mock_uow.v1_patch_applications.get_for_command.return_value = record

        result = service.get_patch_application_for_command("cmd-123")
        assert result is not None
        assert result.command_id == "cmd-123"

    def test_get_patch_application_for_command_not_found(self, service, mock_uow):
        mock_uow.v1_patch_applications.get_for_command.return_value = None
        result = service.get_patch_application_for_command("cmd-404")
        assert result is None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_application_record() -> V1PatchApplicationRecord:
    return V1PatchApplicationRecord(
        application_id=f"ppa-{uuid4().hex}",
        command_id="cmd-123",
        job_id="job-456",
        validation_id=f"ppv-{uuid4().hex}",
        snapshot_id=f"snp-{uuid4().hex}",
        stage_index=1,
        target_path_hash=hashlib.sha256(b"src/main/java/App.java").hexdigest(),
        patch_size_bytes=42,
        applied_by="controller",
        applied_at=utc_now_text(),
        status="applied",
    )
