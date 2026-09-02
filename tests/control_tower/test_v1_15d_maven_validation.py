"""Focused tests for V1-15D: Validate patch with typed Maven operation.

Covers:
- validate_patch_with_maven happy path (compile, test-compile)
- Rejection of disallowed Maven goals
- Rejection when no patch application exists
- Persistence and audit events
- Query methods
"""

from __future__ import annotations

from uuid import uuid4
from unittest.mock import MagicMock

import pytest

from migration_factory.control_tower.application.dto import (
    PatchMavenValidationDto,
)
from migration_factory.control_tower.application.patch_policy import PatchPolicyService
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.entities import (
    V1PatchApplicationRecord,
    V1PatchMavenValidationRecord,
)
from migration_factory.control_tower.domain.errors import (
    NotFoundError,
    PatchContentMismatchError,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_uow():
    uow = MagicMock()
    uow.v1_patch_maven_validations = MagicMock()
    uow.v1_patch_applications = MagicMock()
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
def application_record():
    return V1PatchApplicationRecord(
        application_id=f"ppa-{uuid4().hex}",
        command_id="cmd-123",
        job_id="job-456",
        validation_id=f"ppv-{uuid4().hex}",
        snapshot_id=f"snp-{uuid4().hex}",
        stage_index=1,
        target_path_hash="abc123",
        patch_size_bytes=42,
        applied_by="controller",
        applied_at=utc_now_text(),
        status="applied",
    )


@pytest.fixture
def maven_params():
    return {
        "command_id": "cmd-123",
        "job_id": "job-456",
        "maven_goal": "compile",
        "passed": True,
        "result_summary": "BUILD SUCCESS",
        "actor_type": "system",
        "actor_id": "controller",
    }


# ------------------------------------------------------------------
# Happy path
# ------------------------------------------------------------------


class TestMavenValidation:
    def test_compile_goal_success(
        self, service, maven_params, mock_uow, application_record
    ):
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record

        result = service.validate_patch_with_maven(**maven_params)

        assert isinstance(result, PatchMavenValidationDto)
        assert result.maven_goal == "compile"
        assert result.passed is True
        assert result.result_summary == "BUILD SUCCESS"
        assert result.application_id == application_record.application_id
        assert result.maven_validation_id.startswith("pmv-")

        # Verify persistence
        mock_uow.v1_patch_maven_validations.insert.assert_called_once()
        mock_uow.audit_records.append_global_audit.assert_called_once()

    def test_test_compile_goal_success(
        self, service, maven_params, mock_uow, application_record
    ):
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record
        maven_params["maven_goal"] = "test-compile"
        maven_params["passed"] = True
        maven_params["result_summary"] = "Tests passed"

        result = service.validate_patch_with_maven(**maven_params)
        assert result.maven_goal == "test-compile"
        assert result.passed is True

    def test_failed_maven_passed_false(
        self, service, maven_params, mock_uow, application_record
    ):
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record
        maven_params["passed"] = False
        maven_params["result_summary"] = "BUILD FAILURE"

        result = service.validate_patch_with_maven(**maven_params)
        assert result.passed is False
        assert result.result_summary == "BUILD FAILURE"

    def test_with_correlation_id(
        self, service, maven_params, mock_uow, application_record
    ):
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record
        maven_params["correlation_id"] = "corr-mvn-001"
        maven_params["causation_id"] = "cause-mvn-001"

        result = service.validate_patch_with_maven(**maven_params)
        assert result.correlation_id == "corr-mvn-001"
        assert result.causation_id == "cause-mvn-001"

    def test_persists_validation_record(
        self, service, maven_params, mock_uow, application_record
    ):
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record

        result = service.validate_patch_with_maven(**maven_params)

        call_args = mock_uow.v1_patch_maven_validations.insert.call_args
        assert call_args is not None
        record = call_args[0][0]
        assert isinstance(record, V1PatchMavenValidationRecord)
        assert record.maven_validation_id == result.maven_validation_id
        assert record.maven_goal == "compile"
        assert record.passed is True

    def test_creates_audit_event(
        self, service, maven_params, mock_uow, application_record
    ):
        mock_uow.v1_patch_applications.get_for_command.return_value = application_record

        service.validate_patch_with_maven(**maven_params)

        audit_call = mock_uow.audit_records.append_global_audit.call_args
        assert audit_call is not None
        kwargs = audit_call[1]
        assert kwargs["action"] == "patch_maven_validation_recorded"


# ------------------------------------------------------------------
# Rejection tests
# ------------------------------------------------------------------


class TestMavenRejection:
    def test_disallowed_goal_rejected(
        self, service, maven_params, mock_uow
    ):
        maven_params["maven_goal"] = "install"
        with pytest.raises(PatchContentMismatchError, match="not allowed"):
            service.validate_patch_with_maven(**maven_params)
        mock_uow.v1_patch_maven_validations.insert.assert_not_called()

    def test_raw_maven_goal_rejected(
        self, service, maven_params, mock_uow
    ):
        maven_params["maven_goal"] = "deploy"
        with pytest.raises(PatchContentMismatchError, match="not allowed"):
            service.validate_patch_with_maven(**maven_params)
        mock_uow.v1_patch_maven_validations.insert.assert_not_called()

    def test_blank_goal_rejected(
        self, service, maven_params, mock_uow
    ):
        maven_params["maven_goal"] = ""
        with pytest.raises(PatchContentMismatchError, match="not allowed"):
            service.validate_patch_with_maven(**maven_params)
        mock_uow.v1_patch_maven_validations.insert.assert_not_called()

    def test_shell_command_as_goal_rejected(
        self, service, maven_params, mock_uow
    ):
        maven_params["maven_goal"] = "rm -rf /"
        with pytest.raises(PatchContentMismatchError, match="not allowed"):
            service.validate_patch_with_maven(**maven_params)
        mock_uow.v1_patch_maven_validations.insert.assert_not_called()

    def test_no_patch_application_rejected(
        self, service, maven_params, mock_uow
    ):
        mock_uow.v1_patch_applications.get_for_command.return_value = None
        with pytest.raises(NotFoundError, match="No patch application found"):
            service.validate_patch_with_maven(**maven_params)
        mock_uow.v1_patch_maven_validations.insert.assert_not_called()


# ------------------------------------------------------------------
# Query methods
# ------------------------------------------------------------------


class TestMavenQuery:
    def test_get_maven_validation_found(self, service, mock_uow):
        record = _make_maven_record()
        mock_uow.v1_patch_maven_validations.get.return_value = record

        result = service.get_maven_validation(record.maven_validation_id)
        assert result is not None
        assert result.maven_validation_id == record.maven_validation_id
        assert result.maven_goal == "compile"

    def test_get_maven_validation_not_found(self, service, mock_uow):
        mock_uow.v1_patch_maven_validations.get.return_value = None
        result = service.get_maven_validation("nonexistent")
        assert result is None

    def test_get_maven_validation_for_application_found(self, service, mock_uow):
        record = _make_maven_record()
        mock_uow.v1_patch_maven_validations.get_for_application.return_value = record

        result = service.get_maven_validation_for_application("ppa-123")
        assert result is not None
        assert result.application_id == record.application_id

    def test_get_maven_validation_for_application_not_found(self, service, mock_uow):
        mock_uow.v1_patch_maven_validations.get_for_application.return_value = None
        result = service.get_maven_validation_for_application("ppa-404")
        assert result is None

    def test_allowed_goals_listed(self):
        """Verify only compile and test-compile are allowed."""
        assert "compile" in PatchPolicyService._ALLOWED_MAVEN_GOALS
        assert "test-compile" in PatchPolicyService._ALLOWED_MAVEN_GOALS
        assert "install" not in PatchPolicyService._ALLOWED_MAVEN_GOALS
        assert "deploy" not in PatchPolicyService._ALLOWED_MAVEN_GOALS
        assert "clean" not in PatchPolicyService._ALLOWED_MAVEN_GOALS
        assert "test" not in PatchPolicyService._ALLOWED_MAVEN_GOALS
        assert "package" not in PatchPolicyService._ALLOWED_MAVEN_GOALS


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_maven_record() -> V1PatchMavenValidationRecord:
    return V1PatchMavenValidationRecord(
        maven_validation_id=f"pmv-{uuid4().hex}",
        application_id=f"ppa-{uuid4().hex}",
        command_id="cmd-123",
        job_id="job-456",
        maven_goal="compile",
        passed=True,
        result_summary="BUILD SUCCESS",
        actor_type="system",
        actor_id="controller",
        created_at=utc_now_text(),
    )
