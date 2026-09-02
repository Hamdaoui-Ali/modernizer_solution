"""F5-T1: Tests for deterministic build/test failure evidence capture."""

import pytest
from migration_factory.repair_loop.failure_evidence import (
    FailureEvidence,
    FailureSource,
    NormalizedCompilerError,
    NormalizedTestFailure,
    build_failure_evidence,
    compute_failure_content_checksum,
    compute_failure_artifact_checksum,
    failure_evidence_to_dict,
)


class TestFailureSource:
    def test_build_source(self):
        assert FailureSource.BUILD.value == "build"

    def test_test_source(self):
        assert FailureSource.TEST.value == "test"

    def test_validation_source(self):
        assert FailureSource.VALIDATION.value == "validation"

    def test_transform_source(self):
        assert FailureSource.TRANSFORM.value == "transform"

    def test_unknown_source(self):
        assert FailureSource.UNKNOWN.value == "unknown"


class TestBuildFailureEvidence:
    def test_build_evidence_with_compiler_errors(self):
        errors = (
            NormalizedCompilerError("cannot find symbol", "Foo.java", 42, 10, "error"),
            NormalizedCompilerError("syntax error", "Bar.java", 15, 1, "error"),
        )
        evidence = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            stage_index=3,
            job_id="job-1",
            command_id="cmd-1",
            failure_summary="Build failed: compilation errors",
            compiler_errors=errors,
            changed_files=("Foo.java", "Bar.java"),
            source_profile="spring-boot-2",
            target_profile="spring-boot-3",
        )
        assert evidence.failure_source == FailureSource.BUILD
        assert evidence.stage_index == 3
        assert evidence.job_id == "job-1"
        assert len(evidence.compiler_errors) == 2
        assert evidence.failure_summary == "Build failed: compilation errors"

    def test_test_evidence_with_test_failures(self):
        failures = (
            NormalizedTestFailure("testFoo", "FooTest", "assertion failed", "FooTest.java"),
            NormalizedTestFailure("testBar", "BarTest", "expected 5 got 3", "BarTest.java"),
        )
        evidence = build_failure_evidence(
            failure_source=FailureSource.TEST,
            job_id="job-2",
            failure_summary="Test failed: 2/10 tests failed",
            test_failures=failures,
            changed_files=("FooTest.java", "BarTest.java"),
        )
        assert evidence.failure_source == FailureSource.TEST
        assert len(evidence.test_failures) == 2

    def test_content_checksum_stability(self):
        evidence1 = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="job-1",
            failure_summary="Build failed",
            compiler_errors=(NormalizedCompilerError("error", "Foo.java", 1, 1),),
        )
        evidence2 = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="job-1",
            failure_summary="Build failed",
            compiler_errors=(NormalizedCompilerError("error", "Foo.java", 1, 1),),
        )
        # Content checksums should be equal (same stable inputs)
        assert evidence1.content_checksum == evidence2.content_checksum

    def test_content_checksum_sensitivity(self):
        evidence1 = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="job-1",
            failure_summary="Build failed A",
        )
        evidence2 = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="job-1",
            failure_summary="Build failed B",
        )
        assert evidence1.content_checksum != evidence2.content_checksum

    def test_content_checksum_different_source(self):
        e1 = build_failure_evidence(failure_source=FailureSource.BUILD, job_id="job-1", failure_summary="fail")
        e2 = build_failure_evidence(failure_source=FailureSource.TEST, job_id="job-1", failure_summary="fail")
        assert e1.content_checksum != e2.content_checksum

    def test_artifact_checksum_includes_content(self):
        evidence = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="job-1",
            failure_summary="Build failed",
        )
        assert evidence.artifact_checksum
        assert evidence.content_checksum
        assert evidence.artifact_checksum != evidence.content_checksum

    def test_changed_files_ordered(self):
        evidence = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="job-1",
            failure_summary="fail",
            changed_files=("B.java", "A.java", "C.java"),
        )
        assert evidence.changed_files == ("A.java", "B.java", "C.java")

    def test_log_tail_truncation(self):
        long_text = "x" * 5000
        evidence = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="job-1",
            failure_summary=long_text,
            stdout_tail=long_text,
            stderr_tail=long_text,
            safe_log_preview=long_text,
        )
        assert len(evidence.failure_summary) <= FailureEvidence.MAX_LOG_TAIL_LENGTH
        assert len(evidence.stdout_tail) <= FailureEvidence.MAX_LOG_TAIL_LENGTH
        assert len(evidence.stderr_tail) <= FailureEvidence.MAX_LOG_TAIL_LENGTH
        assert len(evidence.safe_log_preview) <= FailureEvidence.MAX_LOG_TAIL_LENGTH

    def test_accepted_artifact_checksums_ordered(self):
        evidence = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="job-1",
            failure_summary="fail",
            accepted_artifact_checksums=("ccc", "aaa", "bbb"),
        )
        assert evidence.accepted_artifact_checksums == ("aaa", "bbb", "ccc")


class TestFailureEvidenceToDict:
    def test_safe_dict_no_forbidden_keys(self):
        evidence = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="job-1",
            failure_summary="fail",
        )
        d = failure_evidence_to_dict(evidence)
        forbidden = {"sandbox_path", "argv", "env", "raw_command", "provider", "endpoint", "deployment", "env_ref"}
        for key in forbidden:
            assert key not in d

    def test_compiler_errors_ordered_in_dict(self):
        errors = (
            NormalizedCompilerError("error2", "Bar.java", 1, 1),
            NormalizedCompilerError("error1", "Foo.java", 1, 1),
        )
        evidence = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="job-1",
            failure_summary="fail",
            compiler_errors=errors,
        )
        d = failure_evidence_to_dict(evidence)
        assert d["compiler_errors"][0]["file_path"] == "Bar.java"

    def test_test_failures_ordered_in_dict(self):
        failures = (
            NormalizedTestFailure("testB", "BTest"),
            NormalizedTestFailure("testA", "ATest"),
        )
        evidence = build_failure_evidence(
            failure_source=FailureSource.TEST,
            job_id="job-1",
            failure_summary="fail",
            test_failures=failures,
        )
        d = failure_evidence_to_dict(evidence)
        assert d["test_failures"][0]["test_class"] == "ATest"

    def test_all_fields_present(self):
        evidence = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="job-1",
        )
        d = failure_evidence_to_dict(evidence)
        for key in ("failure_source", "stage_index", "job_id", "content_checksum", "artifact_checksum", "schema_version"):
            assert key in d


class TestChecksumFunctions:
    def test_content_checksum_changes_on_different_compiler_errors(self):
        e1 = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="j1",
            failure_summary="fail",
            compiler_errors=(NormalizedCompilerError("err1", "A.java", 1, 1),),
        )
        e2 = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="j1",
            failure_summary="fail",
            compiler_errors=(NormalizedCompilerError("err2", "B.java", 2, 2),),
        )
        assert e1.content_checksum != e2.content_checksum

    def test_content_checksum_same_regardless_of_input_order(self):
        e1 = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="j1",
            failure_summary="fail",
            changed_files=("B.java", "A.java"),
        )
        e2 = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="j1",
            failure_summary="fail",
            changed_files=("A.java", "B.java"),
        )
        assert e1.content_checksum == e2.content_checksum

    def test_artifact_checksum_changes_on_created_at(self):
        e1 = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="j1",
            failure_summary="fail",
        )
        import time
        time.sleep(0.1)
        e2 = build_failure_evidence(
            failure_source=FailureSource.BUILD,
            job_id="j1",
            failure_summary="fail",
        )
        # Same content, different created_at - artifact checksum should differ
        assert e1.content_checksum == e2.content_checksum
        assert e1.artifact_checksum != e2.artifact_checksum
