"""F4-T6 tests for already-modernized app start profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_stage_progression import (
    build_skipped_stage_ledger,
    compute_profile_route,
    next_required_stage,
)
from migration_factory.control_tower.schemas.profile_model import (
    SourceProfileDetectionArtifact,
    SourceProfileEvidenceRef,
    SourceProfileFacts,
    SourceProfileSignal,
)


FORBIDDEN_PUBLIC_FIELDS = {
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


@dataclass(frozen=True)
class AlreadyModernizedApp:
    name: str
    source_profile: str
    target_profile: str
    spring_boot_version: str
    java_version: str
    expected_first_stage: int
    expected_included_stages: tuple[int, ...]
    expected_skipped_stages: tuple[int, ...]


@pytest.fixture(params=[
    AlreadyModernizedApp(
        name="springboot-27-java11",
        source_profile="springboot-2.7-java11",
        target_profile="springboot-3.5-java17",
        spring_boot_version="2.7.18",
        java_version="11",
        expected_first_stage=2,
        expected_included_stages=(2,),
        expected_skipped_stages=(),
    ),
    AlreadyModernizedApp(
        name="springboot-35-java17",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
        spring_boot_version="3.5.0",
        java_version="17",
        expected_first_stage=3,
        expected_included_stages=(3,),
        expected_skipped_stages=(2,),
    ),
    AlreadyModernizedApp(
        name="springboot-35-java21",
        source_profile="springboot-3.5-java21",
        target_profile="springboot-4.0-java21",
        spring_boot_version="3.5.0",
        java_version="21",
        expected_first_stage=4,
        expected_included_stages=(4,),
        expected_skipped_stages=(2, 3),
    ),
])
def already_modernized_app(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> tuple[AlreadyModernizedApp, SourceProfileDetectionArtifact]:
    app = request.param
    app_root = tmp_path / app.name
    app_root.mkdir()
    (app_root / "pom.xml").write_text(
        _pom_xml(app.spring_boot_version, app.java_version),
        encoding="utf-8",
    )
    detection = _detection_artifact(app)
    return app, detection


def test_already_modernized_app_starts_from_current_profile_path(
    already_modernized_app: tuple[AlreadyModernizedApp, SourceProfileDetectionArtifact],
) -> None:
    app, detection = already_modernized_app

    route = compute_profile_route(
        detection.detected_source_profile,
        app.target_profile,
    )

    assert route.valid is True
    assert route.included_stages == app.expected_included_stages
    assert route.skipped_stages == app.expected_skipped_stages
    assert next_required_stage(route, current_stage=1) == app.expected_first_stage
    assert all(stage not in route.included_stages for stage in route.skipped_stages)


def test_already_modernized_detection_artifact_is_safe(
    already_modernized_app: tuple[AlreadyModernizedApp, SourceProfileDetectionArtifact],
) -> None:
    app, detection = already_modernized_app

    assert detection.detected_source_profile == app.source_profile
    assert detection.profile_facts.spring_boot_version == app.spring_boot_version
    assert detection.profile_facts.java_version == app.java_version
    assert detection.evidence_checksums == tuple(
        ref.checksum for ref in detection.evidence_refs
    )
    _assert_forbidden_fields_absent(detection.to_dict())


def test_skipped_stage_ledger_is_bound_to_detection_evidence(
    already_modernized_app: tuple[AlreadyModernizedApp, SourceProfileDetectionArtifact],
) -> None:
    app, detection = already_modernized_app
    route = compute_profile_route(app.source_profile, app.target_profile)

    ledger = build_skipped_stage_ledger(
        route,
        evidence_ref=detection.artifact_ref,
        evidence_checksum=detection.artifact_checksum,
    )

    assert tuple(entry.skipped_stage_index for entry in ledger) == app.expected_skipped_stages
    for entry in ledger:
        assert entry.evidence_ref == detection.artifact_ref
        assert entry.evidence_checksum == detection.artifact_checksum
        assert app.source_profile in entry.reason
        assert "Skipped" in entry.reason
        assert entry.route_checksum
        _assert_forbidden_fields_absent(entry.to_dict())


def test_incompatible_already_modernized_source_target_pair_fails() -> None:
    route = compute_profile_route("springboot-3.5-java21", "springboot-3.5-java17")

    assert route.valid is False
    assert route.reason == "target profile must be higher than source profile"
    assert next_required_stage(route, current_stage=1) is None
    assert build_skipped_stage_ledger(route) == ()


def _detection_artifact(app: AlreadyModernizedApp) -> SourceProfileDetectionArtifact:
    evidence = SourceProfileEvidenceRef(
        evidence_ref=f"analysis:{app.name}:pom",
        evidence_type="maven_root_pom",
        checksum=f"sha256:{app.name}",
        description=(
            f"Spring Boot {app.spring_boot_version} and Java {app.java_version} "
            "from Maven metadata"
        ),
    )
    return SourceProfileDetectionArtifact(
        artifact_id=f"source-profile-{app.name}",
        artifact_ref=f"artifact:source-profile:{app.name}",
        artifact_checksum=f"sha256:source-profile:{app.name}",
        job_id=f"job-{app.name}",
        detected_source_profile=app.source_profile,  # type: ignore[arg-type]
        target_profile=app.target_profile,  # type: ignore[arg-type]
        confidence=0.98,
        uncertainty_notes=(),
        evidence_refs=(evidence,),
        evidence_checksums=(evidence.checksum,),
        profile_signals=(
            SourceProfileSignal(
                signal_name="spring_boot_version",
                value=app.spring_boot_version,
                evidence_ref=evidence.evidence_ref,
                confidence_weight=0.6,
            ),
            SourceProfileSignal(
                signal_name="java_version",
                value=app.java_version,
                evidence_ref=evidence.evidence_ref,
                confidence_weight=0.4,
            ),
        ),
        profile_facts=SourceProfileFacts(
            java_version=app.java_version,
            spring_boot_version=app.spring_boot_version,
            build_tool="maven",
            module_count=1,
            modules=("app",),
        ),
        created_at="2026-06-27T00:00:00Z",
    )


def _pom_xml(spring_boot_version: str, java_version: str) -> str:
    return f"""<project>
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>{spring_boot_version}</version>
  </parent>
  <properties>
    <java.version>{java_version}</java.version>
  </properties>
</project>
"""


def _assert_forbidden_fields_absent(value: object) -> None:
    if isinstance(value, dict):
        assert FORBIDDEN_PUBLIC_FIELDS.isdisjoint(value.keys())
        for child in value.values():
            _assert_forbidden_fields_absent(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_forbidden_fields_absent(child)
