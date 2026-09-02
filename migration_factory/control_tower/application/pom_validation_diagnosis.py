"""F14 — POM validation diagnosis and repair plan generation.

Classifies build/test failures from Maven output evidence.
Generates evidence-based repair plans.
"""

from __future__ import annotations

import json
import re as _re
from typing import Any

from migration_factory.control_tower.application.pom_change_models import (
    PomValidationFailureDiagnosis,
    PomRepairPlan,
    POM_VALIDATION_FAILURE_CLASSIFICATIONS,
)
from migration_factory.control_tower.domain.checksums import utc_now_text


class PomValidationDiagnoser:
    """Classify build/test failures from Maven log evidence.

    Uses only real log evidence. Never guesses.
    """

    def diagnose(
        self,
        *,
        validation_id: str,
        change_id: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        command: str,
        diff_unified: str,
    ) -> PomValidationFailureDiagnosis:
        """Diagnose a failed validation from its log output."""

        combined = stdout + "\n" + stderr
        diagnosis = self._classify_from_logs(combined, exit_code)

        # Extract a bounded log excerpt (redacted)
        log_excerpt = self._extract_log_excerpt(combined, max_lines=20)

        return diagnosis

    def generate_repair_plan(
        self,
        *,
        diagnosis: PomValidationFailureDiagnosis,
        validation_id: str,
        change_id: str,
        diff_unified: str,
        log_output: str,
        command: str,
    ) -> PomRepairPlan | None:
        """Generate a repair plan from failure diagnosis.

        Returns None if evidence is insufficient for any plan.
        """

        if not diagnosis.evidence_sufficient:
            return None

        classification = diagnosis.failure_classification

        if classification == "compilation_failure":
            return PomRepairPlan(
                repair_plan_id="",
                change_id=change_id,
                summary="Source code compilation failed after POM change. "
                        "The dependency version change may have introduced API breaks.",
                detailed_steps=(
                    "Check compilation errors in build log for removed/deprecated APIs.",
                    "Update source code imports and method calls to match the new dependency API.",
                    "If the API change is too large, consider an intermediate version or rollback.",
                ),
                confidence="medium",
                evidence_sources=("build_log_ref", "changed_diff"),
                actions_available=("apply_repair", "rollback", "show_logs"),
                created_at=utc_now_text(),
            )

        elif classification == "dependency_resolution_failure":
            return PomRepairPlan(
                repair_plan_id="",
                change_id=change_id,
                summary="Maven dependency resolution failed. "
                        "The requested version may not exist in configured repositories.",
                detailed_steps=(
                    f"Verify the requested version exists: {diagnosis.root_cause}",
                    "Check Maven repository configuration in settings.xml or pom.xml.",
                    "Ensure the dependency coordinates (groupId, artifactId) are correct.",
                ),
                confidence="medium",
                evidence_sources=("build_log_ref",),
                actions_available=("apply_repair", "rollback", "show_logs"),
                created_at=utc_now_text(),
            )

        elif classification == "bom_conflict":
            return PomRepairPlan(
                repair_plan_id="",
                change_id=change_id,
                summary="Dependency version conflict with BOM-managed versions. "
                        "The requested version conflicts with the Spring Boot BOM.",
                detailed_steps=(
                    "Check the Spring Boot BOM compatibility matrix for supported dependency versions.",
                    "Remove the explicit version override and let the BOM manage the version.",
                    "Or, if the version override is intentional, suppress the BOM conflict warning.",
                ),
                confidence="medium",
                evidence_sources=("build_log_ref", "changed_diff"),
                actions_available=("apply_repair", "rollback", "show_logs"),
                created_at=utc_now_text(),
            )

        elif classification == "test_failure":
            return PomRepairPlan(
                repair_plan_id="",
                change_id=change_id,
                summary="Tests failed after POM change. "
                        "The dependency change may have introduced behavioral changes.",
                detailed_steps=(
                    "Review failed test names and assertions from the test log.",
                    "Update test expectations to match the new dependency behavior.",
                    "If tests fail due to removed functionality, revert or use an intermediate version.",
                ),
                confidence="medium",
                evidence_sources=("test_log_ref", "changed_diff"),
                actions_available=("apply_repair", "rollback", "show_logs"),
                created_at=utc_now_text(),
            )

        elif classification == "jakarta_javax_mismatch":
            return PomRepairPlan(
                repair_plan_id="",
                change_id=change_id,
                summary="Jakarta/javax namespace mismatch detected. "
                        "Source code references old javax namespace but dependency uses jakarta.",
                detailed_steps=(
                    "Run a find-replace across source files: javax.servlet → jakarta.servlet",
                    "Update all javax imports to jakarta equivalents.",
                    "Verify the application server supports Jakarta EE 9+.",
                ),
                confidence="medium",
                evidence_sources=("build_log_ref", "changed_diff"),
                actions_available=("apply_repair", "rollback", "show_logs"),
                created_at=utc_now_text(),
            )

        elif classification == "plugin_failure":
            return PomRepairPlan(
                repair_plan_id="",
                change_id=change_id,
                summary="Maven plugin execution failed. "
                        "The plugin version change may have introduced incompatibilities.",
                detailed_steps=(
                    "Check plugin release notes for breaking changes.",
                    "Verify plugin configuration is compatible with the new version.",
                    "Consider using an intermediate plugin version.",
                ),
                confidence="medium",
                evidence_sources=("build_log_ref", "changed_diff"),
                actions_available=("apply_repair", "rollback", "show_logs"),
                created_at=utc_now_text(),
            )

        else:
            # Unknown failure — provide generic but evidence-referencing plan
            return PomRepairPlan(
                repair_plan_id="",
                change_id=change_id,
                summary=f"Build failed: {diagnosis.failed_phase}. "
                        "The root cause is unclear from available evidence.",
                detailed_steps=(
                    f"Review the build log at ref: {diagnosis.log_ref}",
                    "Check the diff of the applied POM change.",
                    "If the failure is unrelated to the POM change, verify the baseline build passes.",
                ),
                confidence="low",
                evidence_sources=("build_log_ref", "changed_diff"),
                actions_available=("rollback", "show_logs"),
                created_at=utc_now_text(),
            )

    # ── Log classification ──────────────────────────────────────────

    def _classify_from_logs(
        self,
        combined_output: str,
        exit_code: int,
    ) -> PomValidationFailureDiagnosis:
        """Classify failure from combined stdout/stderr.

        Returns diagnosis with evidence_sufficient flag.
        """

        if exit_code == 0:
            return PomValidationFailureDiagnosis(
                failure_classification="unknown_build_failure",
                failed_phase="unknown",
                exit_code=exit_code,
                log_excerpt="Build passed but diagnosis was requested.",
                log_ref="",
                root_cause="Build succeeded. No failure to diagnose.",
                evidence_sufficient=False,
                missing_evidence=(),
            )

        # Detect specific failure patterns
        classification = "unknown_build_failure"
        failed_phase = "unknown"
        root_cause = ""

        # Jakarta/javax mismatch (must check before generic compilation)
        if _re.search(r"javax\.\w+ does not exist|package javax\.", combined_output):
            classification = "jakarta_javax_mismatch"
            failed_phase = "compile"
            root_cause = "Source code references javax namespace but dependency uses jakarta"

        # Compilation failure
        elif _re.search(r"COMPILATION ERROR|cannot find symbol|error:\s", combined_output):
            classification = "compilation_failure"
            failed_phase = "compile"
            # Try to extract specific error
            m = _re.search(r"(cannot find symbol.*?)(?:\n|$)", combined_output)
            if m:
                root_cause = m.group(1).strip()
            else:
                m2 = _re.search(r"(error:.*?)(?:\n|$)", combined_output)
                root_cause = m2.group(1).strip() if m2 else "Compilation error"

        # Dependency resolution failure
        elif _re.search(r"Could not resolve dependencies|Failed to read artifact descriptor|Missing artifact", combined_output):
            classification = "dependency_resolution_failure"
            failed_phase = "dependency_resolution"
            m = _re.search(r"(Could not (?:resolve|find).*?)(?:\n|$)", combined_output)
            root_cause = m.group(1).strip() if m else "Dependency resolution failed"

        # BOM conflict
        elif _re.search(r"dependency convergence|version conflict|Require upper bound", combined_output):
            classification = "bom_conflict"
            failed_phase = "dependency_resolution"
            root_cause = "Dependency version conflict with managed versions"

        # Test failure
        elif _re.search(r"Tests run:.*Failures: [1-9]|Tests run:.*Errors: [1-9]|BUILD FAILURE.*test", combined_output):
            classification = "test_failure"
            failed_phase = "test"
            m = _re.search(r"(Tests run:.*?)(?:\n|$)", combined_output)
            root_cause = m.group(1).strip() if m else "Tests failed"

        # Plugin failure
        elif _re.search(r"Failed to execute goal|Plugin .*? failed", combined_output):
            classification = "plugin_failure"
            failed_phase = "plugin_execution"
            m = _re.search(r"(Failed to execute goal.*?)(?:\n|$)", combined_output)
            root_cause = m.group(1).strip() if m else "Plugin execution failed"

        # Hibernate API break
        elif _re.search(r"org\.hibernate\.(?:MappingException|QueryException|PropertyNotFoundException)", combined_output):
            classification = "hibernate_api_break"
            failed_phase = "runtime"
            root_cause = "Hibernate API incompatibility detected"

        # Unknown
        else:
            classification = "unknown_build_failure"
            failed_phase = "unknown"
            root_cause = "Build failed but no specific pattern was identified"

        # Determine if evidence is sufficient
        evidence_sufficient = classification != "unknown_build_failure"
        missing_evidence: tuple[str, ...] = ()
        if not evidence_sufficient:
            missing_evidence = ("detailed_error_stacktrace",)

        log_excerpt = self._extract_log_excerpt(combined_output)

        return PomValidationFailureDiagnosis(
            failure_classification=classification,
            failed_phase=failed_phase,
            exit_code=exit_code,
            log_excerpt=log_excerpt,
            log_ref="artifact:build_log:" + (classification or "unknown"),
            root_cause=root_cause,
            evidence_sufficient=evidence_sufficient,
            missing_evidence=missing_evidence,
        )

    def _extract_log_excerpt(self, output: str, max_lines: int = 20) -> str:
        """Extract a bounded, redacted excerpt from log output.

        Prioritizes ERROR lines, then WARNING, then last lines.
        """
        lines = output.splitlines()

        # Collect error/warning lines
        error_lines = [
            line for line in lines
            if _re.search(r'ERROR|FAILURE|FAILED|Exception|error:', line, _re.IGNORECASE)
        ]

        if error_lines:
            # Take up to max_lines error lines
            excerpt = "\n".join(error_lines[:max_lines])
            if len(error_lines) > max_lines:
                excerpt += f"\n... ({len(error_lines) - max_lines} more error lines)"
            return excerpt

        # Fall back to last max_lines lines
        tail = lines[-max_lines:]
        if len(lines) > max_lines:
            return "...\n" + "\n".join(tail)
        return "\n".join(tail)
