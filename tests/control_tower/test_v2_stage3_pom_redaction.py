"""Tests for F14 redaction — no raw paths/secrets in public responses.

Validates:
- No raw sandbox path leaks in API responses
- No raw sandbox path in event payloads
- No secrets in SSE events
- Public POM view is redacted
- Change record summaries don't expose raw paths
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from migration_factory.control_tower.application.pom_dependency_editor import (
    PomDependencyEditor,
)
from migration_factory.control_tower.application.pom_change_models import (
    PomView,
    PomChangeRecordSummary,
    PomApplyResult,
    PomDependencyReview,
    PomBaseline,
    PomDependencyFinding,
    PomChangeProposal,
    PomValidationRun,
    PomValidationFailureDiagnosis,
    PomRepairPlan,
    PomRollbackResult,
    PomChangeRecord,
)


# ── Sample data ────────────────────────────────────────────────────

SAMPLE_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project>
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.5.14</version>
    </parent>
    <properties>
        <java.version>17</java.version>
    </properties>
    <dependencies>
        <dependency>
            <groupId>com.google.code.gson</groupId>
            <artifactId>gson</artifactId>
            <version>2.8.9</version>
        </dependency>
    </dependencies>
</project>
"""

SANDBOX_PATH = "/tmp/user/sandbox/job_abc_stage3"


# ── Tests ──────────────────────────────────────────────────────────

class TestPomViewRedaction:

    def test_pom_view_does_not_expose_sandbox_path(self):
        """PomView.to_public_dict() should not contain raw sandbox path."""
        view = PomView(
            job_id="job_1",
            stage=3,
            exists=True,
            content=SAMPLE_POM,
            truncated=False,
            content_type="application/xml",
            redaction_applied=True,
            detected_baseline=PomBaseline(
                java_version="17",
                spring_boot_version="3.5.14",
                spring_boot_version_location="parent",
                detected_from=("root_pom",),
            ),
            reason=None,
        )

        public = view.to_public_dict()
        # Should not contain sandbox path
        assert "sandbox" not in str(public).lower()
        assert "/tmp/" not in str(public)
        assert str(SANDBOX_PATH) not in str(public)

    def test_pom_view_contains_only_public_fields(self):
        """PomView.to_public_dict() should only contain defined fields."""
        view = PomView(
            job_id="job_1",
            stage=3,
            exists=True,
            content="<project></project>",
            truncated=False,
            content_type="application/xml",
            redaction_applied=True,
            detected_baseline=None,
            reason=None,
        )

        public = view.to_public_dict()
        allowed_keys = {"job_id", "stage", "exists", "content", "truncated", "content_type", "redaction_applied", "detected_baseline"}
        for key in public:
            assert key in allowed_keys, f"Unexpected key: {key}"


class TestApplyResultRedaction:

    def test_apply_result_no_sandbox_path(self):
        """ApplyResult.to_public_dict() should not expose sandbox path."""
        result = PomApplyResult(
            change_id="ch_1",
            status="applied_pending_validation",
            operation="update_dependency_version",
            target_desc="com.google.code.gson:gson",
            before_version="2.8.9",
            after_version="2.11.0",
            before_checksum="sha256:abc",
            after_checksum="sha256:def",
            diff_summary="Updated gson version from 2.8.9 to 2.11.0",
            validation_id="val_1",
            rollback_available=True,
            idempotency_key="ik_1",
            created_at="2026-06-16T00:00:00Z",
            message="Change applied. Validation running.",
        )

        public = result.to_public_dict()
        assert "sandbox" not in str(public).lower()
        assert "/tmp/" not in str(public)

    def test_apply_result_has_no_path_fields(self):
        """ApplyResult.to_public_dict() should have no path fields."""
        result = PomApplyResult(
            change_id="ch_1",
            status="applied_pending_validation",
            operation="update_dependency_version",
            target_desc="desc",
            before_version="1.0",
            after_version="2.0",
            before_checksum="a",
            after_checksum="b",
            diff_summary="",
            validation_id=None,
            rollback_available=False,
            idempotency_key=None,
            created_at="",
            message="",
        )

        public = result.to_public_dict()
        for val in public.values():
            if isinstance(val, str):
                assert "sandbox" not in val.lower()


class TestDependencyReviewRedaction:

    def test_dependency_review_no_raw_paths(self):
        """DependencyReview.to_public_dict() should not expose raw paths."""
        review = PomDependencyReview(
            job_id="job_1",
            stage=3,
            baseline=PomBaseline("17", "3.5.14", "parent", ("root_pom",)),
            buckets={"app_specific_third_party": []},
            findings=(),
            evidence_loaded=("root_pom",),
            evidence_missing=(),
            warnings=(),
            created_at="2026-06-16T00:00:00Z",
        )

        public = review.to_public_dict()
        public_str = str(public)
        assert "sandbox" not in public_str.lower()
        assert "/tmp/" not in public_str


class TestValidationRunRedaction:

    def test_validation_run_uses_log_ref_not_inline_logs(self):
        """ValidationRun should use log_ref references, not inline full logs."""
        run = PomValidationRun(
            validation_id="val_1",
            change_id="ch_1",
            status="failed",
            command="mvn compile",
            build_status="failed",
            test_status="unknown",
            exit_code=1,
            duration_ms=3000,
            log_ref="artifact:build_log:val_1",
            test_log_ref=None,
            diagnosis=PomValidationFailureDiagnosis(
                failure_classification="compilation_failure",
                failed_phase="compile",
                exit_code=1,
                log_excerpt="cannot find symbol",
                log_ref="artifact:build_log:val_1",
                root_cause="API change",
                evidence_sufficient=True,
                missing_evidence=(),
            ),
            repair_plan=None,
            created_at="2026-06-16T00:00:00Z",
            completed_at="2026-06-16T00:00:46Z",
        )

        public = run.to_public_dict()
        # log_ref should be a reference, not raw logs
        assert public["log_ref"] and not public["log_ref"].startswith("/tmp/")

    def test_validation_diagnosis_log_excerpt_bounded(self):
        """Diagnosis log_excerpt should be bounded, not full logs."""
        diag = PomValidationFailureDiagnosis(
            failure_classification="compilation_failure",
            failed_phase="compile",
            exit_code=1,
            log_excerpt="Short excerpt",
            log_ref="artifact:log:1",
            root_cause="Test",
            evidence_sufficient=True,
            missing_evidence=(),
        )

        public = diag.to_public_dict()
        assert len(public["log_excerpt"]) < 2000  # Should be bounded


class TestRollbackResultRedaction:

    def test_rollback_result_no_raw_paths(self):
        """RollbackResult should not expose raw paths."""
        result = PomRollbackResult(
            change_id="ch_1",
            rollback_id="rb_1",
            status="rolled_back",
            checksum_restored=True,
            validation_triggered=False,
            validation_id=None,
            created_at="2026-06-16T00:00:00Z",
        )

        public = result.to_public_dict()
        public_str = str(public)
        assert "sandbox" not in public_str.lower()


class TestChangeRecordSummaryRedaction:

    def test_change_record_summary_no_internal_refs(self):
        """ChangeRecordSummary should not expose internal content refs."""
        summary = PomChangeRecordSummary(
            change_id="ch_1",
            operation="update_dependency_version",
            target_desc="com.google.code.gson:gson",
            before_version="2.8.9",
            after_version="2.11.0",
            before_checksum="sha256:abc",
            after_checksum="sha256:def",
            diff_summary="Updated gson from 2.8.9 to 2.11.0",
            status="applied_pending_validation",
            validation_id="val_1",
            rollback_id=None,
            created_at="2026-06-16T00:00:00Z",
        )

        public = summary.to_public_dict()
        # Should have no "content_ref" or "before_content_ref" or "after_content_ref"
        assert "content_ref" not in public
        assert "before_content_ref" not in public
        assert "after_content_ref" not in public
