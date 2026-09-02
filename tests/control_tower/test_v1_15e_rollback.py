"""Focused tests for V1-15E: Roll back failed repair.

Covers:
- rollback_failed_repair happy path from failed Maven validation
- Rollback rejected when no snapshot exists
- Rollback rejected when no patch application exists
- Rollback rejected when Maven validation passed
- Rollback rejected when no Maven validation exists
- Rollback output is redacted
- Rollback record persisted deterministically
- Query methods (get_rollback, get_rollback_for_command, get_rollback_for_application)
- No arbitrary shell/raw Maven goal path exists
"""

from __future__ import annotations

from uuid import uuid4
from unittest.mock import MagicMock

import pytest

from migration_factory.control_tower.application.dto import (
    PatchRollbackDto,
)
from migration_factory.control_tower.application.patch_policy import PatchPolicyService
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.entities import (
    V1PatchApplicationRecord,
    V1PatchMavenValidationRecord,
    V1PatchRollbackRecord,
    V1SandboxSnapshotRecord,
)
from migration_factory.control_tower.domain.errors import (
    PatchRollbackError,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_uow():
    uow = MagicMock()
    uow.v1_sandbox_snapshots = MagicMock()
    uow.v1_patch_applications = MagicMock()
    uow.v1_patch_maven_validations = MagicMock()
    uow.v1_patch_rollbacks = MagicMock()
    uow.audit_records = MagicMock()
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
def snapshot_record():
    return V1SandboxSnapshotRecord(
        snapshot_id=f"snp-{uuid4().hex}",
        command_id="cmd-rollback-001",
        job_id="job-rollback-001",
        stage_index=2,
        sandbox_artifact_id=f"art-{uuid4().hex}",
        sandbox_checksum="abc123def456",
        actor_type="system",
        actor_id="controller",
        created_at=utc_now_text(),
    )


@pytest.fixture
def application_record():
    return V1PatchApplicationRecord(
        application_id=f"ppa-{uuid4().hex}",
        command_id="cmd-rollback-001",
        job_id="job-rollback-001",
        validation_id=f"ppv-{uuid4().hex}",
        snapshot_id=f"snp-{uuid4().hex}",
        stage_index=2,
        target_path_hash="sha256-target-path-hash",
        patch_size_bytes=512,
        applied_by="controller",
        applied_at=utc_now_text(),
        status="applied",
    )


@pytest.fixture
def failed_maven_validation(application_record):
    return V1PatchMavenValidationRecord(
        maven_validation_id=f"pmv-{uuid4().hex}",
        application_id=application_record.application_id,
        command_id="cmd-rollback-001",
        job_id="job-rollback-001",
        maven_goal="compile",
        passed=False,
        result_summary="BUILD FAILURE: compilation error in src/main/Foo.java",
        actor_type="system",
        actor_id="controller",
        created_at=utc_now_text(),
    )


@pytest.fixture
def rollback_params():
    return {
        "command_id": "cmd-rollback-001",
        "job_id": "job-rollback-001",
        "actor_type": "system",
        "actor_id": "controller",
    }


# ------------------------------------------------------------------
# Happy path
# ------------------------------------------------------------------


class TestRollbackSuccess:
    def test_rollback_succeeds_from_failed_maven(
        self, service, rollback_params, mock_uow,
        snapshot_record, application_record, failed_maven_validation,
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = snapshot_record
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record
        mock_uow.v1_patch_maven_validations.get_for_application.return_value = failed_maven_validation

        result = service.rollback_failed_repair(**rollback_params)

        assert isinstance(result, PatchRollbackDto)
        assert result.rollback_id.startswith("prb-")
        assert result.command_id == "cmd-rollback-001"
        assert result.job_id == "job-rollback-001"
        assert result.application_id == application_record.application_id
        assert result.snapshot_id == snapshot_record.snapshot_id
        assert result.maven_validation_id == failed_maven_validation.maven_validation_id
        assert result.stage_index == 2
        assert result.reason_code == "maven_validation_failed"
        assert result.redacted_summary != ""

        # Verify persistence
        mock_uow.v1_patch_rollbacks.insert.assert_called_once()
        mock_uow.audit_records.append_global_audit.assert_called_once()

    def test_rollback_is_redacted(
        self, service, rollback_params, mock_uow,
        snapshot_record, application_record, failed_maven_validation,
    ):
        """Verify rollback output contains no raw paths, content, or commands."""
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = snapshot_record
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record
        mock_uow.v1_patch_maven_validations.get_for_application.return_value = failed_maven_validation

        result = service.rollback_failed_repair(**rollback_params)

        # Redacted summary should be deterministic and not contain raw commands
        assert "rm " not in result.redacted_summary
        assert "mvn " not in result.redacted_summary
        assert "exec" not in result.redacted_summary
        assert "shell" not in result.redacted_summary.lower()
        assert result.target_path_hash != ""
        # target_path_hash is a hash, not a raw path
        assert "/" not in result.target_path_hash
        assert "\\" not in result.target_path_hash

    def test_rollback_persists_record(
        self, service, rollback_params, mock_uow,
        snapshot_record, application_record, failed_maven_validation,
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = snapshot_record
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record
        mock_uow.v1_patch_maven_validations.get_for_application.return_value = failed_maven_validation

        result = service.rollback_failed_repair(**rollback_params)

        call_args = mock_uow.v1_patch_rollbacks.insert.call_args
        assert call_args is not None
        record = call_args[0][0]
        assert isinstance(record, V1PatchRollbackRecord)
        assert record.rollback_id == result.rollback_id
        assert record.command_id == "cmd-rollback-001"
        assert record.reason_code == "maven_validation_failed"

    def test_rollback_creates_audit_event(
        self, service, rollback_params, mock_uow,
        snapshot_record, application_record, failed_maven_validation,
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = snapshot_record
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record
        mock_uow.v1_patch_maven_validations.get_for_application.return_value = failed_maven_validation

        service.rollback_failed_repair(**rollback_params)

        audit_call = mock_uow.audit_records.append_global_audit.call_args
        assert audit_call is not None
        kwargs = audit_call[1]
        assert kwargs["action"] == "patch_rollback_recorded"
        assert "rollback_id" in kwargs["payload_json"]
        assert "command_id" in kwargs["payload_json"]

    def test_rollback_with_correlation_id(
        self, service, rollback_params, mock_uow,
        snapshot_record, application_record, failed_maven_validation,
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = snapshot_record
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record
        mock_uow.v1_patch_maven_validations.get_for_application.return_value = failed_maven_validation
        rollback_params["correlation_id"] = "corr-rb-001"
        rollback_params["causation_id"] = "cause-rb-001"

        result = service.rollback_failed_repair(**rollback_params)
        assert result.correlation_id == "corr-rb-001"
        assert result.causation_id == "cause-rb-001"


# ------------------------------------------------------------------
# Rejection tests
# ------------------------------------------------------------------


class TestRollbackRejection:
    def test_no_snapshot_rejected(
        self, service, rollback_params, mock_uow,
        application_record, failed_maven_validation,
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = None
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record
        mock_uow.v1_patch_maven_validations.get_for_application.return_value = failed_maven_validation

        with pytest.raises(PatchRollbackError, match="no sandbox snapshot exists"):
            service.rollback_failed_repair(**rollback_params)

        mock_uow.v1_patch_rollbacks.insert.assert_not_called()

    def test_no_patch_application_rejected(
        self, service, rollback_params, mock_uow,
        snapshot_record,
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = snapshot_record
        mock_uow.v1_patch_applications.get_for_command.return_value = None

        with pytest.raises(PatchRollbackError, match="no patch application exists"):
            service.rollback_failed_repair(**rollback_params)

        mock_uow.v1_patch_rollbacks.insert.assert_not_called()

    def test_no_maven_validation_rejected(
        self, service, rollback_params, mock_uow,
        snapshot_record, application_record,
    ):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = snapshot_record
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record
        mock_uow.v1_patch_maven_validations.get_for_application.return_value = None

        with pytest.raises(PatchRollbackError, match="no Maven validation exists"):
            service.rollback_failed_repair(**rollback_params)

        mock_uow.v1_patch_rollbacks.insert.assert_not_called()

    def test_successful_maven_validation_rejected(
        self, service, rollback_params, mock_uow,
        snapshot_record, application_record,
    ):
        passed_maven = V1PatchMavenValidationRecord(
            maven_validation_id=f"pmv-{uuid4().hex}",
            application_id=application_record.application_id,
            command_id="cmd-rollback-001",
            job_id="job-rollback-001",
            maven_goal="compile",
            passed=True,
            result_summary="BUILD SUCCESS",
            actor_type="system",
            actor_id="controller",
            created_at=utc_now_text(),
        )
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = snapshot_record
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record
        mock_uow.v1_patch_maven_validations.get_for_application.return_value = passed_maven

        with pytest.raises(PatchRollbackError, match="passed; rollback requires a failed validation"):
            service.rollback_failed_repair(**rollback_params)

        mock_uow.v1_patch_rollbacks.insert.assert_not_called()


# ------------------------------------------------------------------
# Query methods
# ------------------------------------------------------------------


class TestRollbackQuery:
    def test_get_rollback_found(self, service, mock_uow):
        record = _make_rollback_record()
        mock_uow.v1_patch_rollbacks.get.return_value = record

        result = service.get_rollback(record.rollback_id)
        assert result is not None
        assert result.rollback_id == record.rollback_id
        assert result.reason_code == "maven_validation_failed"

    def test_get_rollback_not_found(self, service, mock_uow):
        mock_uow.v1_patch_rollbacks.get.return_value = None
        result = service.get_rollback("nonexistent")
        assert result is None

    def test_get_rollback_for_command_found(self, service, mock_uow):
        record = _make_rollback_record()
        mock_uow.v1_patch_rollbacks.get_for_command.return_value = record

        result = service.get_rollback_for_command("cmd-rollback-001")
        assert result is not None
        assert result.command_id == "cmd-rollback-001"

    def test_get_rollback_for_command_not_found(self, service, mock_uow):
        mock_uow.v1_patch_rollbacks.get_for_command.return_value = None
        result = service.get_rollback_for_command("cmd-missing")
        assert result is None

    def test_get_rollback_for_application_found(self, service, mock_uow):
        record = _make_rollback_record()
        mock_uow.v1_patch_rollbacks.get_for_application.return_value = record

        result = service.get_rollback_for_application(record.application_id)
        assert result is not None
        assert result.application_id == record.application_id

    def test_get_rollback_for_application_not_found(self, service, mock_uow):
        mock_uow.v1_patch_rollbacks.get_for_application.return_value = None
        result = service.get_rollback_for_application("ppa-nonexistent")
        assert result is None


# ------------------------------------------------------------------
# Security invariants
# ------------------------------------------------------------------


class TestRollbackSecurity:
    def test_no_shell_metacharacters_in_params(self):
        """Ensure the service does not accept shell-like inputs."""
        # The rollback method only accepts typed IDs and booleans.
        # No shell command, raw Maven goal, or path is accepted.
        import inspect
        sig = inspect.signature(PatchPolicyService.rollback_failed_repair)
        params = list(sig.parameters.keys())
        # No shell, maven_goal, target_path, or working_directory params
        assert "shell" not in params
        assert "maven_goal" not in params
        assert "target_path" not in params
        assert "working_directory" not in params
        assert "patch_content" not in params
        assert "executable" not in params

    def test_no_raw_path_in_rollback_dto(self):
        """Verify the PatchRollbackDto has no raw path field."""
        import dataclasses
        fields = {f.name for f in dataclasses.fields(PatchRollbackDto)}
        # The DTO should not expose raw paths
        assert "target_path" not in fields
        assert "working_directory" not in fields
        # It only exposes a hash
        assert "target_path_hash" in fields


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_rollback_record() -> V1PatchRollbackRecord:
    return V1PatchRollbackRecord(
        rollback_id=f"prb-{uuid4().hex}",
        command_id="cmd-rollback-001",
        job_id="job-rollback-001",
        application_id=f"ppa-{uuid4().hex}",
        snapshot_id=f"snp-{uuid4().hex}",
        maven_validation_id=f"pmv-{uuid4().hex}",
        stage_index=2,
        target_path_hash="sha256-target-path-hash",
        rolled_back_by="controller",
        rolled_back_at=utc_now_text(),
        reason_code="maven_validation_failed",
        redacted_summary="Rolled back patch application ppa-... for command cmd-rollback-001 on stage 2.",
    )
