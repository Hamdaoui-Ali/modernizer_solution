"""Focused tests for V1-15B: Snapshot sandbox before patch.

Covers:
- take_and_record_sandbox_snapshot orchestration
- ensure_snapshot_exists_before_write invariant
- Idempotent snapshot recording
- Stage validation (1-3 only)
- Missing snapshot detection for writes
- Stage mismatch detection
"""

from __future__ import annotations

from uuid import uuid4
from unittest.mock import MagicMock

import pytest

from migration_factory.control_tower.application.dto import (
    CommandExecutionDto,
    SandboxSnapshotDto,
)
from migration_factory.control_tower.application.patch_policy import PatchPolicyService
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.entities import (
    V1SandboxSnapshotRecord,
)
from migration_factory.control_tower.domain.errors import (
    NotFoundError,
    PatchContentMismatchError,
    PatchSnapshotNotFoundError,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_uow():
    uow = MagicMock()
    uow.v1_sandbox_snapshots = MagicMock()
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
def snapshot_params():
    return {
        "command_id": f"cmd-{uuid4().hex}",
        "job_id": f"job-{uuid4().hex}",
        "stage_index": 1,
        "sandbox_artifact_id": f"art-{uuid4().hex}",
        "sandbox_checksum": "abc123def456",
        "actor_type": "system",
        "actor_id": "controller",
    }


@pytest.fixture
def mock_command_execution():
    return CommandExecutionDto(
        command_id=f"cmd-{uuid4().hex}",
        job_id=f"job-{uuid4().hex}",
        operation="maven_compile",
        status=CommandState.RUNNING,
        created_at=utc_now_text(),
        updated_at=utc_now_text(),
        correlation_id=None,
        causation_id=None,
    )


# ------------------------------------------------------------------
# take_and_record_sandbox_snapshot
# ------------------------------------------------------------------


class TestTakeAndRecordSandboxSnapshot:
    def test_takes_snapshot_successfully(
        self, service, snapshot_params, mock_uow, mock_command_execution
    ):
        mock_uow.command_executions.get.return_value = mock_command_execution
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = None

        result = service.take_and_record_sandbox_snapshot(**snapshot_params)

        assert isinstance(result, SandboxSnapshotDto)
        assert result.command_id == snapshot_params["command_id"]
        assert result.job_id == snapshot_params["job_id"]
        assert result.stage_index == snapshot_params["stage_index"]
        assert result.sandbox_artifact_id == snapshot_params["sandbox_artifact_id"]
        assert result.sandbox_checksum == snapshot_params["sandbox_checksum"]
        assert result.snapshot_id.startswith("snp-")

        # Verify insert was called
        mock_uow.v1_sandbox_snapshots.insert.assert_called_once()
        mock_uow.audit_records.append_global_audit.assert_called_once()

    def test_validates_command_exists(
        self, service, snapshot_params, mock_uow
    ):
        mock_uow.command_executions.get.return_value = None

        with pytest.raises(NotFoundError, match="not found"):
            service.take_and_record_sandbox_snapshot(**snapshot_params)

        # No snapshot should be inserted if command not found
        mock_uow.v1_sandbox_snapshots.insert.assert_not_called()

    def test_validates_stage_index_below_1(
        self, service, snapshot_params, mock_uow
    ):
        snapshot_params["stage_index"] = 0
        with pytest.raises(PatchContentMismatchError, match="out of valid range"):
            service.take_and_record_sandbox_snapshot(**snapshot_params)

        mock_uow.v1_sandbox_snapshots.insert.assert_not_called()

    def test_validates_stage_index_above_3(
        self, service, snapshot_params, mock_uow
    ):
        snapshot_params["stage_index"] = 4
        with pytest.raises(PatchContentMismatchError, match="out of valid range"):
            service.take_and_record_sandbox_snapshot(**snapshot_params)

        mock_uow.v1_sandbox_snapshots.insert.assert_not_called()

    def test_accepts_stage_2(self, service, snapshot_params, mock_uow, mock_command_execution):
        mock_uow.command_executions.get.return_value = mock_command_execution
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = None
        snapshot_params["stage_index"] = 2

        result = service.take_and_record_sandbox_snapshot(**snapshot_params)
        assert result.stage_index == 2

    def test_accepts_stage_3(self, service, snapshot_params, mock_uow, mock_command_execution):
        mock_uow.command_executions.get.return_value = mock_command_execution
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = None
        snapshot_params["stage_index"] = 3

        result = service.take_and_record_sandbox_snapshot(**snapshot_params)
        assert result.stage_index == 3

    def test_idempotent_existing_snapshot_returned(
        self, service, snapshot_params, mock_uow, mock_command_execution
    ):
        mock_uow.command_executions.get.return_value = mock_command_execution
        existing_record = V1SandboxSnapshotRecord(
            snapshot_id=f"snp-{uuid4().hex}",
            command_id=snapshot_params["command_id"],
            job_id=snapshot_params["job_id"],
            stage_index=snapshot_params["stage_index"],
            sandbox_artifact_id=snapshot_params["sandbox_artifact_id"],
            sandbox_checksum=snapshot_params["sandbox_checksum"],
            actor_type=snapshot_params["actor_type"],
            actor_id=snapshot_params["actor_id"],
            created_at=utc_now_text(),
        )
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = existing_record

        result = service.take_and_record_sandbox_snapshot(**snapshot_params)

        # Should return existing without inserting a new one
        assert result.snapshot_id == existing_record.snapshot_id
        mock_uow.v1_sandbox_snapshots.insert.assert_not_called()

    def test_recorded_with_audit_event(
        self, service, snapshot_params, mock_uow, mock_command_execution
    ):
        mock_uow.command_executions.get.return_value = mock_command_execution
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = None

        service.take_and_record_sandbox_snapshot(**snapshot_params)

        audit_call = mock_uow.audit_records.append_global_audit
        audit_call.assert_called_once()
        kwargs = audit_call.call_args[1]
        assert kwargs["action"] == "sandbox_snapshot_recorded"

    def test_with_correlation_id(
        self, service, snapshot_params, mock_uow, mock_command_execution
    ):
        mock_uow.command_executions.get.return_value = mock_command_execution
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = None
        snapshot_params["correlation_id"] = "corr-abc-123"
        snapshot_params["causation_id"] = "cause-def-456"

        result = service.take_and_record_sandbox_snapshot(**snapshot_params)
        assert result.correlation_id == "corr-abc-123"
        assert result.causation_id == "cause-def-456"


# ------------------------------------------------------------------
# ensure_snapshot_exists_before_write
# ------------------------------------------------------------------


class TestEnsureSnapshotBeforeWrite:
    def test_snapshot_exists_passes(self, service, mock_uow):
        existing = V1SandboxSnapshotRecord(
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
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = existing

        result = service.ensure_snapshot_exists_before_write(
            command_id="cmd-123",
            job_id="job-456",
            stage_index=1,
        )

        assert result is not None
        assert result.snapshot_id == existing.snapshot_id
        assert result.stage_index == 1

    def test_no_snapshot_raises_error(self, service, mock_uow):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = None

        with pytest.raises(PatchSnapshotNotFoundError, match="No sandbox snapshot found"):
            service.ensure_snapshot_exists_before_write(
                command_id="cmd-123",
                job_id="job-456",
                stage_index=1,
            )

    def test_stage_mismatch_raises_error(self, service, mock_uow):
        existing = V1SandboxSnapshotRecord(
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
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = existing

        with pytest.raises(PatchContentMismatchError, match="does not match"):
            service.ensure_snapshot_exists_before_write(
                command_id="cmd-123",
                job_id="job-456",
                stage_index=2,  # mismatched stage
            )

    def test_snapshot_stage_2_matches(self, service, mock_uow):
        existing = V1SandboxSnapshotRecord(
            snapshot_id=f"snp-{uuid4().hex}",
            command_id="cmd-123",
            job_id="job-456",
            stage_index=2,
            sandbox_artifact_id="art-sandbox",
            sandbox_checksum="abc123",
            actor_type="system",
            actor_id="controller",
            created_at=utc_now_text(),
        )
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = existing

        result = service.ensure_snapshot_exists_before_write(
            command_id="cmd-123",
            job_id="job-456",
            stage_index=2,
        )
        assert result.stage_index == 2
