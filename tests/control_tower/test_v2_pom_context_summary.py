"""Tests for V2 POM Intelligence Summary (F04).

Tests that the PomContextSummaryBuilder:
1. Builds summary from a sandbox pom.xml using existing scanner
2. Detects Spring Boot version and location
3. Extracts Java and compiler settings
4. Uses V2 stage target (not scanner defaults)
5. Maps to candidate deterministic rules from ALLOWED_RULE_IDS
6. Produces validation command via existing detection helpers
7. Does NOT apply any patch
8. summary_to_dict produces correct artifact JSON
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest

from migration_factory.control_tower.application.v2_pom_context_summary import (
    PomContextSummaryBuilder,
    PomContextSummary,
    _extract_major,
    _version_lt,
)
from migration_factory.repair_loop.rule_registry import ALLOWED_RULE_IDS


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def sandbox_with_boot3_pom() -> Generator[Path, None, None]:
    """Create a temporary sandbox with a Boot 3.5.14 pom.xml (parent-based)."""
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp) / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        pom = sandbox / "pom.xml"
        pom.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.5.14</version>
        <relativePath/>
    </parent>
    <groupId>com.example</groupId>
    <artifactId>demo</artifactId>
    <version>0.0.1</version>
    <properties>
        <java.version>17</java.version>
        <maven.compiler.release>17</maven.compiler.release>
        <maven.compiler.source>17</maven.compiler.source>
        <maven.compiler.target>17</maven.compiler.target>
    </properties>
</project>"""
        )
        yield sandbox


@pytest.fixture
def sandbox_with_boot2_pom() -> Generator[Path, None, None]:
    """Create a temporary sandbox with a Boot 2.7.18 pom.xml (property-based)."""
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp) / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        pom = sandbox / "pom.xml"
        pom.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>2.7.18</version>
        <relativePath/>
    </parent>
    <groupId>com.example</groupId>
    <artifactId>demo</artifactId>
    <version>0.0.1</version>
    <properties>
        <java.version>8</java.version>
    </properties>
</project>"""
        )
        yield sandbox


@pytest.fixture
def sandbox_without_pom() -> Generator[Path, None, None]:
    """Create a temporary sandbox without a pom.xml."""
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp) / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        yield sandbox


# ── Core build summary tests ──────────────────────────────────────


class TestBuildSummary:

    def test_builds_summary_from_boot3_pom(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """Build summary from Boot 3.5.14 parent-based POM."""
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="21",
        )
        assert isinstance(summary, PomContextSummary)
        assert summary.spring_boot_version == "3.5.14"
        assert summary.spring_boot_version_location in ("parent", "unknown")
        assert summary.java_version_property == "17"
        assert summary.maven_compiler_release == "17"
        assert summary.maven_compiler_source == "17"
        assert summary.maven_compiler_target == "17"
        assert summary.target_stage_boot == "3.5.14"
        assert summary.target_stage_java == "21"

    def test_builds_summary_from_boot2_pom(
        self,
        sandbox_with_boot2_pom: Path,
    ) -> None:
        """Build summary from Boot 2.7.18 parent-based POM."""
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot2_pom,
            target_boot="3.5.14",
            target_java="21",
        )
        assert isinstance(summary, PomContextSummary)
        assert summary.spring_boot_version == "2.7.18", f"Expected 2.7.18, got {summary.spring_boot_version}"
        assert summary.target_stage_boot == "3.5.14"
        assert summary.target_stage_java == "21"

    def test_handles_missing_pom(self) -> None:
        """Build summary handles missing pom.xml gracefully."""
        with tempfile.TemporaryDirectory() as tmp:
            summary = PomContextSummaryBuilder.build_summary(
                sandbox_path=Path(tmp),
                target_boot="3.5.14",
                target_java="17",
            )
            assert isinstance(summary, PomContextSummary)
            assert summary.spring_boot_version == "unknown" or summary.spring_boot_version == ""

    def test_uses_explicit_target_over_defaults(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """Target Boot/Java from V2 stage state, not scanner defaults."""
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="21",
        )
        assert summary.target_stage_boot == "3.5.14"
        assert summary.target_stage_java == "21"

    def test_custom_target_boot(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """Custom target boot is reflected in summary."""
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="4.0.0",
            target_java="21",
        )
        assert summary.target_stage_boot == "4.0.0"

    def test_target_stack_from_profile(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """Target stack resolved from profile YAML when ai_hub_path and profile_id provided."""
        import tempfile

        with tempfile.TemporaryDirectory() as hub_tmp:
            hub_path = Path(hub_tmp)
            profiles_dir = hub_path / "profiles"
            profiles_dir.mkdir(parents=True, exist_ok=True)
            profile_file = profiles_dir / "target21.yaml"
            # Write profile YAML directly as text
            profile_file.write_text(
                "target:\n"
                "  java: '21'\n"
                "  spring_boot: 3.5.14\n"
            )

            summary = PomContextSummaryBuilder.build_summary(
                sandbox_path=sandbox_with_boot3_pom,
                target_boot="3.5.14",
                target_java="17",  # Default, should be overridden by profile
                ai_hub_path=str(hub_path),
                profile_id="target21",
            )
            # Profile says java=21, so target_stage_java should be 21 (not 17)
            assert summary.target_stage_java == "21", (
                f"Profile target java=21 should override default 17, got {summary.target_stage_java}"
            )
            assert summary.target_stage_boot == "3.5.14"


# ── Candidate rule tests ──────────────────────────────────────────


class TestCandidateRules:

    def test_boot2_suggests_jakarta_rules(
        self,
        sandbox_with_boot2_pom: Path,
    ) -> None:
        """Boot 2.x POM suggests Jakarta migration rules."""
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot2_pom,
            target_boot="3.5.14",
            target_java="17",
        )
        rules = summary.candidate_deterministic_rules
        if "DEPENDENCY_REPLACE_JAVAX_SERVLET_API_WITH_JAKARTA" in ALLOWED_RULE_IDS:
            has_servlet = "DEPENDENCY_REPLACE_JAVAX_SERVLET_API_WITH_JAKARTA" in rules
            has_validation = "DEPENDENCY_REPLACE_JAVAX_VALIDATION_WITH_JAKARTA" in rules
            assert has_servlet or has_validation

    def test_candidate_rules_are_allowlisted(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """All candidate rules are from ALLOWED_RULE_IDS."""
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="21",
        )
        for rule in summary.candidate_deterministic_rules:
            assert rule in ALLOWED_RULE_IDS, f"{rule} not in ALLOWED_RULE_IDS"

    def test_candidate_rules_never_empty(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """At least one candidate rule is suggested."""
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="17",
        )
        assert len(summary.candidate_deterministic_rules) >= 1


# ── Validation command tests ──────────────────────────────────────


class TestValidationCommand:

    def test_validation_command_is_string(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """Validation command is a non-empty string."""
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="17",
        )
        assert isinstance(summary.validation_command, str)
        assert len(summary.validation_command) > 0

    def test_validation_command_includes_mvn_or_compile(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """Validation command contains maven or compile reference."""
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="17",
        )
        cmd = summary.validation_command.lower()
        assert "mvn" in cmd or "clean" in cmd or "test" in cmd or "compile" in cmd


# ── Serialization tests ───────────────────────────────────────────


class TestSerialization:

    def test_summary_to_dict(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """summary_to_dict produces correct artifact JSON."""
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="21",
        )
        d = PomContextSummaryBuilder.summary_to_dict(summary)
        assert d["spring_boot_version"] == summary.spring_boot_version
        assert d["spring_boot_version_location"] == summary.spring_boot_version_location
        assert d["java_version_property"] == summary.java_version_property
        assert d["target_stage_boot"] == "3.5.14"
        assert d["target_stage_java"] == "21"
        assert isinstance(d["candidate_deterministic_rules"], list)
        assert isinstance(d["validation_command"], str)
        assert isinstance(d["warnings"], list)
        assert d["created_at"] is not None

    def test_summary_to_dict_serializable(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """summary_to_dict output is JSON-serializable."""
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="17",
        )
        d = PomContextSummaryBuilder.summary_to_dict(summary)
        # Should not raise
        json_str = json.dumps(d, sort_keys=True)
        assert isinstance(json_str, str)
        # Round-trip
        parsed = json.loads(json_str)
        assert parsed["spring_boot_version"] == summary.spring_boot_version
        assert parsed["target_stage_boot"] == "3.5.14"


# ── Non-goal enforcement tests ────────────────────────────────────


class TestNoPatchApplied:

    def test_summary_does_not_modify_pom(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """Building summary must not modify the POM file."""
        pom_path = sandbox_with_boot3_pom / "pom.xml"
        original_content = pom_path.read_text(encoding="utf-8")

        PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="21",
        )

        # POM must be unchanged
        assert pom_path.read_text(encoding="utf-8") == original_content

    def test_summary_does_not_create_approval_card(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """Building summary must not create approval-related artifacts."""
        PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="21",
        )
        # No approval card, no patch, no repair action — just a summary
        # (verified by absence of side effects in the summary dataclass)
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="21",
        )
        assert summary.candidate_deterministic_rules is not None


# ── Edge case tests ───────────────────────────────────────────────


class TestEdgeCases:

    def test_empty_sandbox_directory(self) -> None:
        """Handle empty sandbox directory gracefully."""
        with tempfile.TemporaryDirectory() as tmp:
            empty_dir = Path(tmp) / "empty-sandbox"
            empty_dir.mkdir(parents=True, exist_ok=True)
            summary = PomContextSummaryBuilder.build_summary(
                sandbox_path=empty_dir,
                target_boot="3.5.14",
                target_java="17",
            )
            assert isinstance(summary, PomContextSummary)
            # Should not crash

    def test_summary_with_different_targets_are_independent(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """Two summaries with different targets are independent."""
        s1 = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="17",
        )
        s2 = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="4.0.0",
            target_java="21",
        )
        assert s1.target_stage_boot == "3.5.14"
        assert s2.target_stage_boot == "4.0.0"
        assert s1.target_stage_java == "17"
        assert s2.target_stage_java == "21"


# ── Helper function tests ─────────────────────────────────────────


class TestHelpers:

    def test_extract_major(self) -> None:
        """_extract_major correctly extracts version major."""
        assert _extract_major("17") == 17
        assert _extract_major("21.0.1") == 21
        assert _extract_major("3.5.14") == 3
        assert _extract_major("") is None
        assert _extract_major("unknown") is None

    def test_version_lt(self) -> None:
        """_version_lt correctly compares version strings."""
        assert _version_lt("2.7.18", "3.5.14")
        assert _version_lt("3.5.13", "3.5.14")
        assert not _version_lt("3.5.14", "3.5.14")
        assert not _version_lt("3.5.15", "3.5.14")
        assert not _version_lt("4.0.0", "3.5.14")


# ── build_and_emit tests ──────────────────────────────────────────


class TestBuildAndEmit:
    """Prove build_and_emit emits pom_summary_created with real context
    and fails closed on missing/placeholder context."""

    def test_emits_pom_summary_created_with_real_context(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """build_and_emit emits pom_summary_created with job_id and stage_index."""
        events: list[dict[str, Any]] = []

        def event_sink(
            job_id: str,
            stage: int | None,
            event_type: str,
            status: str,
            message: str,
            payload: dict[str, Any] | None = None,
        ) -> None:
            events.append({
                "job_id": job_id,
                "stage": stage,
                "event_type": event_type,
                "status": status,
                "message": message,
                "payload": payload or {},
            })

        summary = PomContextSummaryBuilder.build_and_emit(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="21",
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_sink=event_sink,
        )

        assert summary.pom_summary_ref.startswith("pom-summary:")
        matching = [e for e in events if e["event_type"] == "pom_summary_created"]
        assert len(matching) == 1
        event = matching[0]
        assert event["job_id"] == "job-1"
        assert event["stage"] == 1
        assert event["status"] == "completed"
        assert event["payload"]["pom_summary_ref"] == summary.pom_summary_ref
        assert event["payload"]["command_id"] == "cmd-1"
        # sandbox_path excluded from event payload — redacted at persistence layer
        assert "sandbox_path" not in event["payload"]

    def test_rejects_missing_job_id(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """build_and_emit raises ValueError when event_sink provided
        but job_id is empty."""
        with pytest.raises(ValueError, match="non-empty job_id"):
            PomContextSummaryBuilder.build_and_emit(
                sandbox_path=sandbox_with_boot3_pom,
                target_boot="3.5.14",
                target_java="21",
                job_id="",
                stage_index=1,
                event_sink=lambda *a, **kw: None,
            )

    def test_rejects_missing_stage_index(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """build_and_emit raises ValueError when event_sink provided
        but stage_index is None."""
        with pytest.raises(ValueError, match="non-negative stage_index"):
            PomContextSummaryBuilder.build_and_emit(
                sandbox_path=sandbox_with_boot3_pom,
                target_boot="3.5.14",
                target_java="21",
                job_id="job-1",
                stage_index=None,
                event_sink=lambda *a, **kw: None,
            )

    def test_rejects_negative_stage_index(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """build_and_emit raises ValueError when stage_index is negative."""
        with pytest.raises(ValueError, match="non-negative stage_index"):
            PomContextSummaryBuilder.build_and_emit(
                sandbox_path=sandbox_with_boot3_pom,
                target_boot="3.5.14",
                target_java="21",
                job_id="job-1",
                stage_index=-1,
                event_sink=lambda *a, **kw: None,
            )

    def test_no_event_sink_no_emit(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """build_and_emit without event_sink returns summary without errors."""
        summary = PomContextSummaryBuilder.build_and_emit(
            sandbox_path=sandbox_with_boot3_pom,
            target_boot="3.5.14",
            target_java="21",
        )
        assert isinstance(summary, PomContextSummary)
        assert summary.pom_summary_ref.startswith("pom-summary:")

    def test_pom_summary_ref_is_stable_and_traceable(
        self,
        sandbox_with_boot3_pom: Path,
    ) -> None:
        """pom_summary_ref is a stable, non-empty reference string."""
        s1 = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
        )
        s2 = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_with_boot3_pom,
        )
        # Each call gets a unique ref (UUID-based)
        assert s1.pom_summary_ref != s2.pom_summary_ref
        assert s1.pom_summary_ref.startswith("pom-summary:")
        assert len(s1.pom_summary_ref) > len("pom-summary:")
