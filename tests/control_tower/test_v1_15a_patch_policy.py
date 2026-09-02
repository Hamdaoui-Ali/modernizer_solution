"""Focused tests for V1-15A: Validate patch policy.

Covers:
- PatchPolicyService.validate_patch() approval/rejection
- Shell metacharacter detection in paths and content
- Path traversal blocking
- Absolute path rejection
- Forbidden path token rejection
- Oversize rejection
- Missing approval rejection
- Unsafe content rejection
- Sandbox snapshot recording
- Standalone validation functions
"""

from __future__ import annotations

import json
import hashlib
from uuid import uuid4
from unittest.mock import MagicMock, create_autospec

import pytest

from migration_factory.control_tower.application.dto import (
    PatchPolicyValidationDto,
    SandboxSnapshotDto,
)
from migration_factory.control_tower.application.patch_policy import (
    MAX_PATCH_SIZE_BYTES,
    PatchPolicyService,
    validate_patch_target_path,
    validate_patch_size,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.entities import (
    V1PatchPolicyValidationRecord,
    V1SandboxSnapshotRecord,
)
from migration_factory.control_tower.domain.errors import (
    PatchContentEscapeError,
    PatchContentMismatchError,
    PatchContentOversizeError,
    PatchNotApprovedError,
    PatchPolicyValidationError,
    PatchRollbackError,
    PatchSnapshotNotFoundError,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_uow():
    """Create a mock unit of work with patch_policy_validations and sandbox_snapshots."""
    uow = MagicMock()
    uow.v1_patch_policy_validations = MagicMock()
    uow.v1_sandbox_snapshots = MagicMock()
    uow.audit_records = MagicMock()
    return uow


@pytest.fixture
def uow_factory(mock_uow):
    """Factory that returns the mock UoW."""
    factory = MagicMock()
    factory.return_value.__enter__.return_value = mock_uow
    factory.return_value.__exit__.return_value = None
    return factory


@pytest.fixture
def service(uow_factory):
    """PatchPolicyService with mocked UoW."""
    return PatchPolicyService(uow_factory)


@pytest.fixture
def valid_patch_params():
    """Valid patch parameters that should pass all checks."""
    return {
        "command_id": f"cmd-{uuid4().hex}",
        "job_id": f"job-{uuid4().hex}",
        "target_path": "src/main/java/com/example/App.java",
        "patch_content": "- old line\n+ new line",
        "patch_size_bytes": 42,
        "approval_id": f"apr-{uuid4().hex}",
        "actor_type": "system",
        "actor_id": "controller",
    }


# ------------------------------------------------------------------
# Happy path
# ------------------------------------------------------------------


class TestValidatePatchHappy:
    def test_valid_patch_returns_approved(self, service, valid_patch_params, mock_uow):
        result = service.validate_patch(**valid_patch_params)

        assert isinstance(result, PatchPolicyValidationDto)
        assert result.approved is True
        assert result.validation_code == "APPROVED"
        assert result.reason_code == "policy_pass"
        assert result.command_id == valid_patch_params["command_id"]
        assert result.job_id == valid_patch_params["job_id"]
        assert result.patch_size_bytes == valid_patch_params["patch_size_bytes"]
        assert result.policy_version == "v1.0"
        assert result.target_path_hash == hashlib.sha256(
            valid_patch_params["target_path"].encode("utf-8")
        ).hexdigest()
        assert result.metacharacter_hits == 0
        assert result.validation_id.startswith("ppv-")

        # Verify persistence
        mock_uow.v1_patch_policy_validations.insert.assert_called_once()
        mock_uow.audit_records.append_global_audit.assert_called_once()

    def test_valid_patch_persists_record(self, service, valid_patch_params, mock_uow):
        service.validate_patch(**valid_patch_params)

        # Verify the record was inserted
        call_args = mock_uow.v1_patch_policy_validations.insert.call_args
        assert call_args is not None
        record = call_args[0][0]
        assert isinstance(record, V1PatchPolicyValidationRecord)
        assert record.approved is True
        assert record.validation_code == "APPROVED"

    def test_valid_patch_creates_audit_event(self, service, valid_patch_params, mock_uow):
        service.validate_patch(**valid_patch_params)

        call_args = mock_uow.audit_records.append_global_audit.call_args
        assert call_args is not None
        payload = json.loads(call_args[1]["payload_json"])
        assert payload["approved"] is True

    def test_valid_patch_test_path_prefix(self, service, valid_patch_params):
        valid_patch_params["target_path"] = "pom.xml"
        result = service.validate_patch(**valid_patch_params)
        assert result.approved is True

    def test_valid_patch_test_dir_prefix(self, service, valid_patch_params):
        valid_patch_params["target_path"] = "test/java/com/example/AppTest.java"
        result = service.validate_patch(**valid_patch_params)
        assert result.approved is True

    def test_valid_patch_gradle_dir(self, service, valid_patch_params):
        valid_patch_params["target_path"] = "gradle/wrapper/gradle-wrapper.properties"
        result = service.validate_patch(**valid_patch_params)
        assert result.approved is True

    def test_valid_patch_build_gradle(self, service, valid_patch_params):
        valid_patch_params["target_path"] = "build.gradle"
        result = service.validate_patch(**valid_patch_params)
        assert result.approved is True


# ------------------------------------------------------------------
# Shell metacharacter checks
# ------------------------------------------------------------------


class TestShellMetacharacters:
    @pytest.mark.parametrize(
        "meta", ["`", "$(", "${", "|", ";", "&", "&&", "||", ">", "<"]
    )
    def test_path_metacharacter_rejected(self, service, valid_patch_params, meta):
        valid_patch_params["target_path"] = f"src/main/{meta}evil.java"
        with pytest.raises(PatchContentEscapeError, match="shell metacharacter"):
            service.validate_patch(**valid_patch_params)

    @pytest.mark.parametrize(
        "meta", ["`", "$(", "${", "|", ";", ">", "<"]
    )
    def test_content_metacharacter_rejected(self, service, valid_patch_params, meta):
        valid_patch_params["patch_content"] = f"- old line\n+ {meta}evil"
        with pytest.raises(PatchContentEscapeError, match="shell metacharacter"):
            service.validate_patch(**valid_patch_params)


# ------------------------------------------------------------------
# Path safety checks
# ------------------------------------------------------------------


class TestPathSafety:
    @pytest.mark.parametrize(
        "traversal_path",
        [
            "../src/Main.java",
            "src/../../etc/passwd",
            "test/../../../etc/passwd",
        ],
    )
    def test_path_traversal_rejected(self, service, valid_patch_params, traversal_path):
        valid_patch_params["target_path"] = traversal_path
        with pytest.raises(PatchContentEscapeError, match="path traversal"):
            service.validate_patch(**valid_patch_params)

    @pytest.mark.parametrize(
        "abs_path",
        [
            "/etc/passwd",
            "/usr/local/bin/script.sh",
            "C:/Windows/system32/config",
            "//server/share/file",
        ],
    )
    def test_absolute_path_rejected(self, service, valid_patch_params, abs_path):
        valid_patch_params["target_path"] = abs_path
        with pytest.raises(PatchContentMismatchError, match="absolute"):
            service.validate_patch(**valid_patch_params)

    def test_unallowed_prefix_rejected(self, service, valid_patch_params):
        valid_patch_params["target_path"] = "node_modules/express/app.js"
        with pytest.raises(PatchContentMismatchError, match="not in allowed path"):
            service.validate_patch(**valid_patch_params)

    def test_forbidden_token_secret_rejected(self, service, valid_patch_params):
        valid_patch_params["target_path"] = "src/secrets/credentials.txt"
        with pytest.raises(PatchContentMismatchError, match="forbidden token"):
            service.validate_patch(**valid_patch_params)

    def test_forbidden_token_env_rejected(self, service, valid_patch_params):
        valid_patch_params["target_path"] = "src/.env"
        with pytest.raises(PatchContentMismatchError, match="forbidden token"):
            service.validate_patch(**valid_patch_params)

    def test_forbidden_token_deployment_rejected(self, service, valid_patch_params):
        valid_patch_params["target_path"] = "src/deployment_config.py"
        with pytest.raises(PatchContentMismatchError, match="forbidden token"):
            service.validate_patch(**valid_patch_params)


# ------------------------------------------------------------------
# Oversize checks
# ------------------------------------------------------------------


class TestOversize:
    def test_zero_size_rejected(self, service, valid_patch_params):
        valid_patch_params["patch_size_bytes"] = 0
        with pytest.raises(PatchContentOversizeError, match="must be positive"):
            service.validate_patch(**valid_patch_params)

    def test_negative_size_rejected(self, service, valid_patch_params):
        valid_patch_params["patch_size_bytes"] = -1
        with pytest.raises(PatchContentOversizeError, match="must be positive"):
            service.validate_patch(**valid_patch_params)

    def test_exceeds_max_size_rejected(self, service, valid_patch_params):
        valid_patch_params["patch_size_bytes"] = MAX_PATCH_SIZE_BYTES + 1
        with pytest.raises(PatchContentOversizeError, match="exceeds limit"):
            service.validate_patch(**valid_patch_params)

    def test_max_size_accepted(self, service, valid_patch_params):
        valid_patch_params["patch_size_bytes"] = MAX_PATCH_SIZE_BYTES
        result = service.validate_patch(**valid_patch_params)
        assert result.approved is True


# ------------------------------------------------------------------
# Approval checks
# ------------------------------------------------------------------


class TestApproval:
    def test_missing_approval_rejected(self, service, valid_patch_params):
        valid_patch_params["approval_id"] = None
        with pytest.raises(PatchNotApprovedError, match="no prior approval"):
            service.validate_patch(**valid_patch_params)

    def test_empty_approval_rejected(self, service, valid_patch_params):
        valid_patch_params["approval_id"] = ""
        with pytest.raises(PatchNotApprovedError, match="no prior approval"):
            service.validate_patch(**valid_patch_params)


# ------------------------------------------------------------------
# Unsafe content checks
# ------------------------------------------------------------------


class TestUnsafeContent:
    @pytest.mark.parametrize(
        "unsafe_pattern",
        [
            "subprocess.call(['rm', '-rf'])",
            "subprocess.Popen(['bash', 'script.sh'])",
            "os.system('sudo rm -rf /')",
            "eval('os.system')",
            "exec('import os')",
            "__import__('os').system",
            "chmod(0o777, 'file')",
            "rm -rf /tmp",
            "sudo rm -rf",
        ],
    )
    def test_unsafe_content_rejected(self, service, valid_patch_params, unsafe_pattern):
        valid_patch_params["patch_content"] = f"- old\n+ {unsafe_pattern}"
        with pytest.raises(PatchContentEscapeError, match="unsafe pattern|shell metacharacter"):
            service.validate_patch(**valid_patch_params)


# ------------------------------------------------------------------
# Rejection recording
# ------------------------------------------------------------------


class TestRejectionRecording:
    def test_validate_patch_and_reject(self, service, valid_patch_params, mock_uow):
        result = service.validate_patch_and_reject(
            command_id=valid_patch_params["command_id"],
            job_id=valid_patch_params["job_id"],
            target_path=valid_patch_params["target_path"],
            patch_content=valid_patch_params["patch_content"],
            patch_size_bytes=valid_patch_params["patch_size_bytes"],
            rejection_reason="manual_block",
            approval_id=valid_patch_params["approval_id"],
        )

        assert isinstance(result, PatchPolicyValidationDto)
        assert result.approved is False
        assert result.validation_code == "REJECTED"
        assert result.reason_code == "manual_block"
        mock_uow.v1_patch_policy_validations.insert.assert_called_once()


# ------------------------------------------------------------------
# Query methods
# ------------------------------------------------------------------


class TestQueryMethods:
    def test_get_validation_found(self, service, uow_factory, mock_uow):
        validation_id = f"ppv-{uuid4().hex}"
        record = _make_validation_record(validation_id=validation_id, approved=True)
        mock_uow.v1_patch_policy_validations.get.return_value = record

        result = service.get_validation(validation_id)
        assert result is not None
        assert result.validation_id == validation_id
        assert result.approved is True

    def test_get_validation_not_found(self, service, mock_uow):
        mock_uow.v1_patch_policy_validations.get.return_value = None
        result = service.get_validation("nonexistent")
        assert result is None

    def test_get_latest_validation_for_command(
        self, service, mock_uow
    ):
        record = _make_validation_record(approved=True)
        mock_uow.v1_patch_policy_validations.get_latest_for_command.return_value = record

        result = service.get_latest_validation_for_command("cmd-123")
        assert result is not None
        assert result.approved is True

    def test_get_latest_validation_for_command_not_found(
        self, service, mock_uow
    ):
        mock_uow.v1_patch_policy_validations.get_latest_for_command.return_value = None
        result = service.get_latest_validation_for_command("cmd-404")
        assert result is None

    def test_list_validations_for_command(self, service, mock_uow):
        records = [
            _make_validation_record(validation_id=f"ppv-{uuid4().hex}", approved=True),
            _make_validation_record(validation_id=f"ppv-{uuid4().hex}", approved=False),
        ]
        mock_uow.v1_patch_policy_validations.list_for_command.return_value = records

        results = service.list_validations_for_command("cmd-123")
        assert len(results) == 2
        assert all(isinstance(r, PatchPolicyValidationDto) for r in results)


# ------------------------------------------------------------------
# Sandbox snapshot
# ------------------------------------------------------------------


class TestSandboxSnapshot:
    def test_record_snapshot(self, service, mock_uow):
        result = service.record_sandbox_snapshot(
            command_id="cmd-123",
            job_id="job-456",
            stage_index=1,
            sandbox_artifact_id="art-sandbox-v1",
            sandbox_checksum="abc123",
        )

        assert isinstance(result, SandboxSnapshotDto)
        assert result.command_id == "cmd-123"
        assert result.job_id == "job-456"
        assert result.stage_index == 1
        assert result.sandbox_artifact_id == "art-sandbox-v1"
        assert result.sandbox_checksum == "abc123"
        assert result.snapshot_id.startswith("snp-")
        mock_uow.v1_sandbox_snapshots.insert.assert_called_once()
        mock_uow.audit_records.append_global_audit.assert_called_once()

    def test_get_snapshot_for_command_found(self, service, mock_uow):
        record = _make_snapshot_record()
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = record

        result = service.get_sandbox_snapshot_for_command("cmd-123")
        assert result is not None
        assert result.snapshot_id == record.snapshot_id

    def test_get_snapshot_for_command_not_found(self, service, mock_uow):
        mock_uow.v1_sandbox_snapshots.get_for_command.return_value = None
        result = service.get_sandbox_snapshot_for_command("cmd-404")
        assert result is None


# ------------------------------------------------------------------
# Standalone validation functions
# ------------------------------------------------------------------


class TestStandaloneFunctions:
    def test_validate_patch_target_path_valid(self):
        validate_patch_target_path("src/main/java/App.java")

    def test_validate_patch_target_path_escape(self):
        with pytest.raises(PatchContentEscapeError):
            validate_patch_target_path("src/main/`evil`.java")

    def test_validate_patch_target_path_traversal(self):
        with pytest.raises(PatchContentEscapeError):
            validate_patch_target_path("../src/Main.java")

    def test_validate_patch_target_path_absolute(self):
        with pytest.raises(PatchContentMismatchError):
            validate_patch_target_path("/etc/passwd")

    def test_validate_patch_size_valid(self):
        validate_patch_size(100)

    def test_validate_patch_size_zero(self):
        with pytest.raises(PatchContentOversizeError):
            validate_patch_size(0)

    def test_validate_patch_size_oversize(self):
        with pytest.raises(PatchContentOversizeError):
            validate_patch_size(MAX_PATCH_SIZE_BYTES + 1)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_validation_record(
    validation_id: str | None = None,
    approved: bool = True,
    command_id: str | None = None,
    job_id: str | None = None,
) -> V1PatchPolicyValidationRecord:
    return V1PatchPolicyValidationRecord(
        validation_id=validation_id or f"ppv-{uuid4().hex}",
        command_id=command_id or f"cmd-{uuid4().hex}",
        job_id=job_id or f"job-{uuid4().hex}",
        approved=approved,
        validation_code="APPROVED" if approved else "REJECTED",
        reason_code="policy_pass" if approved else "manual_block",
        target_path_hash=hashlib.sha256(b"src/main/java/App.java").hexdigest(),
        patch_size_bytes=42,
        metacharacter_hits=0,
        policy_version="v1.0",
        actor_type="system",
        actor_id="controller",
        created_at=utc_now_text(),
    )


def _make_snapshot_record() -> V1SandboxSnapshotRecord:
    return V1SandboxSnapshotRecord(
        snapshot_id=f"snp-{uuid4().hex}",
        command_id="cmd-123",
        job_id="job-456",
        stage_index=1,
        sandbox_artifact_id="art-sandbox-v1",
        sandbox_checksum="abc123",
        actor_type="system",
        actor_id="controller",
        created_at=utc_now_text(),
    )
