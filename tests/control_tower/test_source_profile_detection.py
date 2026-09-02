"""AMF-269 / F4-T1 source-profile detection artifact tests."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from migration_factory.agents.analysis_agent.analysis_agent.maven_scanner import (
    build_source_profile_detection_for_root_pom,
    infer_source_profile_from_stack,
)
from migration_factory.control_tower.schemas.profile_model import (
    SOURCE_PROFILE_DETECTION_FIELDS,
    SourceProfileDetectionArtifact,
    SourceProfileEvidenceRef,
    SourceProfileFacts,
    SourceProfileSignal,
)


def test_detection_artifact_from_maven_pom_detects_boot3_java21(tmp_path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.5.4</version>
  </parent>
  <properties>
    <java.version>21</java.version>
  </properties>
  <modules>
    <module>api</module>
    <module>domain</module>
  </modules>
</project>
""",
        encoding="utf-8",
    )

    artifact = build_source_profile_detection_for_root_pom(
        pom,
        job_id="job-269",
        created_at="2026-06-27T00:00:00Z",
        target_profile="springboot-4.0-java21",
        checkpoint_id="checkpoint-analysis",
        artifact_revision_id="revision-analysis",
    )

    assert artifact.artifact_kind == "source_profile_detection"
    assert artifact.job_id == "job-269"
    assert artifact.stage_index == 1
    assert artifact.checkpoint_id == "checkpoint-analysis"
    assert artifact.artifact_revision_id == "revision-analysis"
    assert artifact.detected_source_profile == "springboot-3.5-java21"
    assert artifact.target_profile == "springboot-4.0-java21"
    assert artifact.confidence == 0.9
    assert artifact.profile_facts.java_version == "21"
    assert artifact.profile_facts.spring_boot_version == "3.5.4"
    assert artifact.profile_facts.build_tool == "maven"
    assert artifact.profile_facts.modules == ("api", "domain")
    assert artifact.evidence_checksums == tuple(ref.checksum for ref in artifact.evidence_refs)
    assert artifact.evidence_refs[0].evidence_ref == "analysis:maven-root-pom"
    assert artifact.evidence_refs[0].checksum.startswith("sha256:")
    assert artifact.artifact_checksum.startswith("sha256:")


def test_detection_artifact_public_payload_does_not_expose_runtime_or_paths(tmp_path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>2.7.18</version>
  </parent>
  <properties><java.version>11</java.version></properties>
</project>
""",
        encoding="utf-8",
    )

    artifact = build_source_profile_detection_for_root_pom(
        pom,
        job_id="job-safe",
        created_at="2026-06-27T00:00:00Z",
    )
    payload = json.dumps(artifact.to_dict(), sort_keys=True)

    forbidden = {
        "sandbox_path",
        "argv",
        "env",
        "raw_command",
        "endpoint",
        "deployment",
        "env_ref",
        "filesystem_target",
        "user_supplied_file_path",
        str(pom),
    }
    for value in forbidden:
        assert value not in payload


def test_detection_artifact_rejects_non_selectable_detected_source() -> None:
    with pytest.raises(ValidationError, match="selectable source profile"):
        SourceProfileDetectionArtifact(
            artifact_id="artifact-1",
            artifact_ref="analysis:source-profile-detection",
            artifact_checksum="sha256:artifact",
            job_id="job-1",
            detected_source_profile="springboot-4.0-java21",
            confidence=0.9,
            evidence_refs=(
                SourceProfileEvidenceRef(
                    evidence_ref="analysis:maven-root-pom",
                    evidence_type="maven_root_pom",
                    checksum="sha256:pom",
                ),
            ),
            evidence_checksums=("sha256:pom",),
            profile_signals=(
                SourceProfileSignal(
                    signal_name="spring_boot_version",
                    value="4.0.0",
                    evidence_ref="analysis:maven-root-pom",
                    confidence_weight=0.55,
                ),
            ),
            profile_facts=SourceProfileFacts(
                java_version="21",
                spring_boot_version="4.0.0",
                build_tool="maven",
            ),
            created_at="2026-06-27T00:00:00Z",
        )


def test_detection_artifact_requires_evidence_checksum_binding() -> None:
    with pytest.raises(ValidationError, match="evidence_checksums"):
        SourceProfileDetectionArtifact(
            artifact_id="artifact-1",
            artifact_ref="analysis:source-profile-detection",
            artifact_checksum="sha256:artifact",
            job_id="job-1",
            detected_source_profile="springboot-2.7-java11",
            confidence=0.9,
            evidence_refs=(
                SourceProfileEvidenceRef(
                    evidence_ref="analysis:maven-root-pom",
                    evidence_type="maven_root_pom",
                    checksum="sha256:pom",
                ),
            ),
            evidence_checksums=("sha256:other",),
            profile_signals=(
                SourceProfileSignal(
                    signal_name="spring_boot_version",
                    value="2.7.18",
                    evidence_ref="analysis:maven-root-pom",
                    confidence_weight=0.55,
                ),
            ),
            profile_facts=SourceProfileFacts(
                java_version="11",
                spring_boot_version="2.7.18",
                build_tool="maven",
            ),
            created_at="2026-06-27T00:00:00Z",
        )


def test_infer_source_profile_reports_uncertainty_for_unknown_boot() -> None:
    profile, confidence, notes = infer_source_profile_from_stack(
        java_version="unknown",
        spring_boot_version="unknown",
    )

    assert profile == "springboot-2.7-java11"
    assert confidence == 0.2
    assert notes


def test_infer_source_profile_splits_boot_21_from_boot_27() -> None:
    profile_21, confidence_21, notes_21 = infer_source_profile_from_stack(
        java_version="11",
        spring_boot_version="2.1.6",
    )
    profile_27, confidence_27, notes_27 = infer_source_profile_from_stack(
        java_version="11",
        spring_boot_version="2.7.18",
    )

    assert profile_21 == "springboot-2.1-java11"
    assert confidence_21 == 0.9
    assert notes_21 == ()
    assert profile_27 == "springboot-2.7-java11"
    assert confidence_27 == 0.9
    assert notes_27 == ()


def test_source_profile_detection_fields_are_public_safe() -> None:
    dangerous = {
        "sandbox_path",
        "argv",
        "env",
        "raw_command",
        "endpoint",
        "deployment",
        "env_ref",
        "filesystem_target",
        "user_supplied_file_path",
    }

    assert "detected_source_profile" in SOURCE_PROFILE_DETECTION_FIELDS
    assert "confidence" in SOURCE_PROFILE_DETECTION_FIELDS
    assert "evidence_refs" in SOURCE_PROFILE_DETECTION_FIELDS
    assert SOURCE_PROFILE_DETECTION_FIELDS.isdisjoint(dangerous)


def test_runtime_analysis_agent_persists_source_profile_detection_artifact(
    monkeypatch,
    tmp_path,
) -> None:
    analysis_root = (
        Path(__file__).resolve().parents[2]
        / "migration_factory"
        / "agents"
        / "analysis_agent"
        / "analysis_agent"
    )
    monkeypatch.syspath_prepend(str(analysis_root))
    sys.modules.pop("main", None)
    main = importlib.import_module("main")
    context_module = importlib.import_module("context_manager")

    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()
    (legacy / "pom.xml").write_text(
        """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.5.4</version>
  </parent>
  <properties><java.version>17</java.version></properties>
</project>
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(main, "run_dependency_tree", lambda context: None)
    monkeypatch.setattr(main, "scan_java_imports", lambda root: {"javax_imports": 0})
    monkeypatch.setattr(main, "scan_config_files", lambda root: {})
    monkeypatch.setattr(main, "save_config_inventory", lambda context, inventory: None)
    monkeypatch.setattr(main, "scan_tests", lambda root: {})
    monkeypatch.setattr(main, "save_test_inventory", lambda context, inventory: None)
    monkeypatch.setattr(main, "parse_surefire_reports", lambda root: {})
    monkeypatch.setattr(main, "run_openrewrite_dryrun", lambda context, analysis_facts=None: {"status": "SKIPPED", "warnings": []})
    monkeypatch.setattr(main, "assemble_report", lambda context, maven, imports: {"source_stack": maven["source_stack"]})
    monkeypatch.setattr(main, "enrich_with_ai", lambda context, report: report)
    monkeypatch.setattr(main, "generate_summary", lambda context, maven, imports: context.get_output_path("analysis_summary.md"))
    monkeypatch.setattr(main, "snapshot_tree", lambda root: {})
    monkeypatch.setattr(
        main,
        "write_read_only_verification",
        lambda context, before_legacy, before_modernized: {"source_modified": False},
    )

    context = context_module.MigrationContext(
        "job-runtime-detection",
        str(legacy),
        str(modernized),
        None,
        "springboot-3.5-java21-to-4.0-java21",
    )

    result = main.run_analysis_agent(context)

    assert result.status == "COMPLETED"
    artifact_path = Path(result.artifact_paths["source_profile_detection"])
    assert artifact_path.name == "source_profile_detection.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["detected_source_profile"] == "springboot-3.5-java17"
    assert payload["target_profile"] == "springboot-4.0-java21"
    assert payload["confidence"] == 0.9
    assert payload["evidence_refs"]
    assert payload["evidence_checksums"] == [
        ref["checksum"] for ref in payload["evidence_refs"]
    ]
    assert payload["artifact_checksum"].startswith("sha256:")
