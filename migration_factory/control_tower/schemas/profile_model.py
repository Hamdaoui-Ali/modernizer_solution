"""Shared migration profile models for Control Tower profile selection."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .common import (
    NonEmptyString,
    StrictModel,
    reject_secret_like_value,
    require_non_empty_string,
)


MigrationProfileId = Literal[
    "springboot-2.1-java11",
    "springboot-2.7-java11",
    "springboot-3.5-java17",
    "springboot-3.5-java21",
    "springboot-4.0-java21",
]


class MigrationProfile(StrictModel):
    profile_id: MigrationProfileId
    display_name: str
    order_index: int
    java_version: int
    spring_boot_line: str
    stage_index: int
    selectable_as_source: bool = True
    selectable_as_target: bool = True


RouteStepStatus = Literal["pending", "queued", "running", "blocked", "completed", "failed"]


class RouteStepProfile(StrictModel):
    """Backend-owned route-step projection for route-driven migration execution."""

    route_step_index: int = Field(ge=1)
    stage_index: int = Field(ge=1)
    source_profile: MigrationProfileId
    target_profile: MigrationProfileId
    runtime_profile: str
    catalog: str
    execution_jdk: str
    status: RouteStepStatus = "pending"
    approval_gate_id: str = ""
    artifact_refs: tuple[str, ...] = Field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("runtime_profile", "catalog", "execution_jdk", "approval_gate_id", mode="after")
    @classmethod
    def _route_step_text_is_safe(cls, value: str, info) -> str:
        return reject_secret_like_value(value, info.field_name)

    @field_validator("artifact_refs", "evidence_refs", mode="after")
    @classmethod
    def _route_step_refs_are_safe(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        for ref in value:
            require_non_empty_string(ref, info.field_name)
            reject_secret_like_value(ref, info.field_name)
        return value


_PROFILE_SEQUENCE: tuple[MigrationProfile, ...] = (
    MigrationProfile(
        profile_id="springboot-2.1-java11",
        display_name="Spring Boot 2.1 / Java 11",
        order_index=0,
        java_version=11,
        spring_boot_line="2.1",
        stage_index=0,
        selectable_as_source=True,
        selectable_as_target=False,
    ),
    MigrationProfile(
        profile_id="springboot-2.7-java11",
        display_name="Spring Boot 2.7 / Java 11",
        order_index=1,
        java_version=11,
        spring_boot_line="2.7",
        stage_index=1,
        selectable_as_source=True,
        selectable_as_target=True,
    ),
    MigrationProfile(
        profile_id="springboot-3.5-java17",
        display_name="Spring Boot 3.5 / Java 17",
        order_index=2,
        java_version=17,
        spring_boot_line="3.5",
        stage_index=2,
    ),
    MigrationProfile(
        profile_id="springboot-3.5-java21",
        display_name="Spring Boot 3.5 / Java 21",
        order_index=3,
        java_version=21,
        spring_boot_line="3.5",
        stage_index=3,
    ),
    MigrationProfile(
        profile_id="springboot-4.0-java21",
        display_name="Spring Boot 4.0 / Java 21",
        order_index=4,
        java_version=21,
        spring_boot_line="4.0",
        stage_index=4,
        selectable_as_source=False,
        selectable_as_target=True,
    ),
)


@lru_cache(maxsize=1)
def _profiles_by_id() -> dict[str, MigrationProfile]:
    return {profile.profile_id: profile for profile in _PROFILE_SEQUENCE}


def list_migration_profiles() -> tuple[MigrationProfile, ...]:
    return _PROFILE_SEQUENCE


def get_migration_profile(profile_id: str) -> MigrationProfile | None:
    return _profiles_by_id().get(profile_id)


def is_known_migration_profile(profile_id: str) -> bool:
    return get_migration_profile(profile_id) is not None


def is_selectable_source_profile(profile_id: str) -> bool:
    profile = get_migration_profile(profile_id)
    return bool(profile and profile.selectable_as_source)


def is_selectable_target_profile(profile_id: str) -> bool:
    profile = get_migration_profile(profile_id)
    return bool(profile and profile.selectable_as_target)


def default_source_profile_id() -> MigrationProfileId:
    return "springboot-2.7-java11"


def default_target_profile_id() -> MigrationProfileId:
    return "springboot-4.0-java21"


SourceProfileEvidenceType = Literal[
    "maven_root_pom",
    "maven_module_pom",
    "analysis_artifact",
    "profile_definition",
]


class SourceProfileEvidenceRef(StrictModel):
    """Safe evidence reference used by source-profile detection.

    The reference is an artifact/key identifier, not a local filesystem path.
    """

    evidence_ref: NonEmptyString
    evidence_type: SourceProfileEvidenceType
    checksum: NonEmptyString
    description: str = ""

    @field_validator("evidence_ref", "checksum", mode="after")
    @classmethod
    def _non_empty_required(cls, value: str, info) -> str:
        return require_non_empty_string(value, info.field_name)

    @field_validator("evidence_ref", "checksum", "description", mode="after")
    @classmethod
    def _reject_secret_like_text(cls, value: str, info) -> str:
        return reject_secret_like_value(value, info.field_name)


class SourceProfileFacts(StrictModel):
    """Normalized safe facts extracted during Analysis."""

    java_version: str = "unknown"
    spring_boot_version: str = "unknown"
    build_tool: str = "unknown"
    module_count: int = Field(default=0, ge=0)
    modules: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("java_version", "spring_boot_version", "build_tool", mode="after")
    @classmethod
    def _facts_are_safe_text(cls, value: str, info) -> str:
        return reject_secret_like_value(value, info.field_name)

    @field_validator("modules", mode="after")
    @classmethod
    def _modules_are_safe_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for module in value:
            require_non_empty_string(module, "modules")
            reject_secret_like_value(module, "modules")
        return value


class SourceProfileSignal(StrictModel):
    """One signal contributing to source-profile detection."""

    signal_name: NonEmptyString
    value: NonEmptyString
    evidence_ref: NonEmptyString
    confidence_weight: float = Field(ge=0.0, le=1.0)

    @field_validator("signal_name", "value", "evidence_ref", mode="after")
    @classmethod
    def _signal_text_is_safe(cls, value: str, info) -> str:
        require_non_empty_string(value, info.field_name)
        return reject_secret_like_value(value, info.field_name)


SOURCE_PROFILE_DETECTION_FIELDS: frozenset[str] = frozenset({
    "artifact_id",
    "artifact_kind",
    "artifact_ref",
    "artifact_checksum",
    "job_id",
    "stage_index",
    "checkpoint_id",
    "artifact_revision_id",
    "detected_source_profile",
    "target_profile",
    "confidence",
    "uncertainty_notes",
    "evidence_refs",
    "evidence_checksums",
    "profile_signals",
    "profile_facts",
    "created_at",
    "produced_by",
})


class SourceProfileDetectionArtifact(StrictModel):
    """Artifact emitted by Analysis after detecting the current app profile."""

    artifact_id: NonEmptyString
    artifact_kind: Literal["source_profile_detection"] = "source_profile_detection"
    artifact_ref: NonEmptyString
    artifact_checksum: NonEmptyString

    job_id: NonEmptyString
    stage_index: Literal[1] = 1
    checkpoint_id: str | None = None
    artifact_revision_id: str | None = None

    detected_source_profile: MigrationProfileId
    target_profile: MigrationProfileId | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty_notes: tuple[str, ...] = Field(default_factory=tuple)

    evidence_refs: tuple[SourceProfileEvidenceRef, ...] = Field(min_length=1)
    evidence_checksums: tuple[str, ...] = Field(min_length=1)
    profile_signals: tuple[SourceProfileSignal, ...] = Field(min_length=1)
    profile_facts: SourceProfileFacts

    created_at: NonEmptyString
    produced_by: NonEmptyString = "analysis"

    @field_validator(
        "artifact_id",
        "artifact_ref",
        "artifact_checksum",
        "job_id",
        "created_at",
        "produced_by",
        mode="after",
    )
    @classmethod
    def _required_text_is_safe(cls, value: str, info) -> str:
        require_non_empty_string(value, info.field_name)
        return reject_secret_like_value(value, info.field_name)

    @field_validator("checkpoint_id", "artifact_revision_id", mode="after")
    @classmethod
    def _optional_text_is_safe(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        require_non_empty_string(value, info.field_name)
        return reject_secret_like_value(value, info.field_name)

    @field_validator("uncertainty_notes", mode="after")
    @classmethod
    def _uncertainty_notes_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for note in value:
            require_non_empty_string(note, "uncertainty_notes")
            reject_secret_like_value(note, "uncertainty_notes")
        return value

    @model_validator(mode="after")
    def _detection_is_consistent(self) -> "SourceProfileDetectionArtifact":
        if not is_selectable_source_profile(self.detected_source_profile):
            raise ValueError("detected_source_profile must be a selectable source profile")

        evidence_checksums = {ref.checksum for ref in self.evidence_refs}
        supplied_checksums = set(self.evidence_checksums)
        if evidence_checksums != supplied_checksums:
            raise ValueError("evidence_checksums must match evidence_refs checksums")
        return self

    def to_safe_metadata(self) -> dict[str, Any]:
        """Return the safe checkpoint/artifact metadata projection."""

        return {
            "source_profile_detection_ref": self.artifact_ref,
            "source_profile_detection_checksum": self.artifact_checksum,
            "source_profile_detection_confidence": self.confidence,
            "source_profile_detection_uncertainty_notes": list(self.uncertainty_notes),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)
