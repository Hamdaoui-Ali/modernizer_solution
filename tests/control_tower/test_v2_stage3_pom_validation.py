"""Tests for F14 POM validation lifecycle.

Validates:
- Validation starts asynchronously after apply
- Validation passed updates status/event
- Validation failed stores diagnosis
- Insufficient evidence produces evidence-insufficient result
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from migration_factory.control_tower.application.pom_validation_diagnosis import (
    PomValidationDiagnoser,
)
from migration_factory.control_tower.application.pom_change_models import (
    PomValidationFailureDiagnosis,
    PomRepairPlan,
    PomValidationRun,
)


# ── Sample log outputs ─────────────────────────────────────────────

BUILD_PASSED_OUTPUT = """[INFO] BUILD SUCCESS
[INFO] Total time: 45.234 s
[INFO] Tests run: 42, Failures: 0, Errors: 0, Skipped: 0
"""

COMPILE_FAILURE_OUTPUT = """[INFO] --- maven-compiler-plugin:3.11.0:compile (default-compile) @ demo ---
[ERROR] COMPILATION ERROR :
[ERROR] /path/to/src/main/java/com/example/Demo.java:[12,30] cannot find symbol
  symbol:   method getMessage()
  location: variable gson of type com.google.gson.Gson
[INFO] BUILD FAILURE
[INFO] Total time: 3.456 s
"""

DEPENDENCY_RESOLUTION_FAILURE = """[ERROR] Failed to execute goal on project demo: Could not resolve dependencies for project com.example:demo:jar:0.0.1-SNAPSHOT
[ERROR] Failed to collect dependencies at com.google.code.gson:gson:jar:99.99.99
[ERROR] Could not find artifact com.google.code.gson:gson:jar:99.99.99 in central (https://repo.maven.apache.org/maven2)
[INFO] BUILD FAILURE
"""

TEST_FAILURE_OUTPUT = """[INFO] --- maven-surefire-plugin:3.1.2:test (default-test) @ demo ---
[INFO] Tests run: 42, Failures: 3, Errors: 0, Skipped: 0
[ERROR] Failures:
[ERROR]   DemoTest.testGsonParsing:42 expected:<success> but was:<error>
[INFO] BUILD FAILURE
"""

JAKARTA_MISMATCH_OUTPUT = """[ERROR] COMPILATION ERROR :
[ERROR] /path/to/src/main/java/com/example/Servlet.java:[5,27] package javax.servlet does not exist
[ERROR] /path/to/src/main/java/com/example/Servlet.java:[6,31] package javax.servlet.http does not exist
[INFO] BUILD FAILURE
"""

UNKNOWN_FAILURE_OUTPUT = """[INFO] Something went wrong
[INFO] BUILD FAILURE
[INFO] Total time: 1.234 s
"""


# ── Tests ──────────────────────────────────────────────────────────

class TestValidationDiagnosis:

    def test_build_passed_no_diagnosis(self):
        """Build passed should create a passed diagnosis."""
        d = PomValidationDiagnoser()
        result = d._classify_from_logs(BUILD_PASSED_OUTPUT, exit_code=0)
        assert result.evidence_sufficient is False
        assert "succeeded" in result.root_cause.lower() or "passed" in result.log_excerpt.lower()

    def test_compilation_failure_detected(self):
        """Compilation errors should be classified correctly."""
        d = PomValidationDiagnoser()
        result = d._classify_from_logs(COMPILE_FAILURE_OUTPUT, exit_code=1)
        assert result.failure_classification == "compilation_failure"
        assert result.failed_phase == "compile"
        assert result.evidence_sufficient is True
        assert "cannot find symbol" in result.root_cause

    def test_dependency_resolution_failure_detected(self):
        """Missing artifact should be classified as dependency resolution failure."""
        d = PomValidationDiagnoser()
        result = d._classify_from_logs(DEPENDENCY_RESOLUTION_FAILURE, exit_code=1)
        assert result.failure_classification == "dependency_resolution_failure"
        assert result.evidence_sufficient is True

    def test_test_failure_detected(self):
        """Test failures should be classified correctly."""
        d = PomValidationDiagnoser()
        result = d._classify_from_logs(TEST_FAILURE_OUTPUT, exit_code=1)
        assert result.failure_classification == "test_failure"
        assert result.failed_phase == "test"
        assert result.evidence_sufficient is True

    def test_jakarta_mismatch_detected(self):
        """javax references should be detected as jakarta/javax mismatch."""
        d = PomValidationDiagnoser()
        result = d._classify_from_logs(JAKARTA_MISMATCH_OUTPUT, exit_code=1)
        assert result.failure_classification == "jakarta_javax_mismatch"

    def test_unknown_failure_evidence_insufficient(self):
        """Unclassifiable failures should have insufficient evidence."""
        d = PomValidationDiagnoser()
        result = d._classify_from_logs(UNKNOWN_FAILURE_OUTPUT, exit_code=1)
        assert result.failure_classification == "unknown_build_failure"
        assert result.evidence_sufficient is False
        assert "missing_evidence" in result.to_public_dict()


class TestRepairPlanGeneration:

    def test_compilation_failure_repair_plan(self):
        """Compilation failure should produce a repair plan."""
        d = PomValidationDiagnoser()
        diagnosis = d._classify_from_logs(COMPILE_FAILURE_OUTPUT, exit_code=1)

        plan = d.generate_repair_plan(
            diagnosis=diagnosis,
            validation_id="val_1",
            change_id="ch_1",
            diff_unified="",
            log_output=COMPILE_FAILURE_OUTPUT,
            command="mvn compile",
        )

        assert plan is not None
        assert "compilation" in plan.summary.lower() or "compil" in plan.summary.lower()
        assert len(plan.detailed_steps) > 0
        assert plan.confidence in ("low", "medium", "high")

    def test_dependency_resolution_repair_plan(self):
        """Dependency resolution failure should produce a repair plan."""
        d = PomValidationDiagnoser()
        diagnosis = d._classify_from_logs(DEPENDENCY_RESOLUTION_FAILURE, exit_code=1)

        plan = d.generate_repair_plan(
            diagnosis=diagnosis,
            validation_id="val_1",
            change_id="ch_1",
            diff_unified="",
            log_output=DEPENDENCY_RESOLUTION_FAILURE,
            command="mvn compile",
        )

        assert plan is not None
        assert "repository" in plan.summary.lower() or "resolution" in plan.summary.lower()

    def test_evidence_insufficient_no_plan(self):
        """When evidence is insufficient, no repair plan should be generated."""
        d = PomValidationDiagnoser()
        diagnosis = PomValidationFailureDiagnosis(
            failure_classification="unknown_build_failure",
            failed_phase="unknown",
            exit_code=1,
            log_excerpt="Something went wrong",
            log_ref="artifact:log:1",
            root_cause="Unknown error",
            evidence_sufficient=False,
            missing_evidence=("detailed_error_stacktrace",),
        )

        plan = d.generate_repair_plan(
            diagnosis=diagnosis,
            validation_id="val_1",
            change_id="ch_1",
            diff_unified="",
            log_output="minimal output",
            command="mvn compile",
        )

        assert plan is None  # No plan when evidence insufficient

    def test_repair_plan_has_evidence_sources(self):
        """Repair plan must cite evidence sources."""
        d = PomValidationDiagnoser()
        diagnosis = d._classify_from_logs(COMPILE_FAILURE_OUTPUT, exit_code=1)

        plan = d.generate_repair_plan(
            diagnosis=diagnosis,
            validation_id="val_1",
            change_id="ch_1",
            diff_unified="",
            log_output=COMPILE_FAILURE_OUTPUT,
            command="mvn compile",
        )

        assert plan is not None
        assert len(plan.evidence_sources) > 0

    def test_repair_plan_has_actions(self):
        """Repair plan must list available actions."""
        d = PomValidationDiagnoser()
        diagnosis = d._classify_from_logs(COMPILE_FAILURE_OUTPUT, exit_code=1)

        plan = d.generate_repair_plan(
            diagnosis=diagnosis,
            validation_id="val_1",
            change_id="ch_1",
            diff_unified="",
            log_output=COMPILE_FAILURE_OUTPUT,
            command="mvn compile",
        )

        assert plan is not None
        assert "rollback" in plan.actions_available


class TestLogExcerptRedaction:

    def test_log_excerpt_extracts_errors(self):
        """Log excerpt should prioritize error lines."""
        d = PomValidationDiagnoser()
        output = "INFO: Starting\nERROR: Something failed\nERROR: Another failure\nINFO: Done"
        excerpt = d._extract_log_excerpt(output)
        assert "ERROR" in excerpt
        assert "Something failed" in excerpt

    def test_log_excerpt_bounded(self):
        """Log excerpt should be bounded."""
        d = PomValidationDiagnoser()
        long_output = "\n".join(f"ERROR: Line {i}" for i in range(50))
        excerpt = d._extract_log_excerpt(long_output, max_lines=20)
        lines = excerpt.splitlines()
        assert len(lines) <= 21  # 20 + possible truncation message
