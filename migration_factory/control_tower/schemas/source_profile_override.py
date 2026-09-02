"""Safe source-profile override contract for F4 current-state start."""

from __future__ import annotations

from typing import Any

from pydantic import field_validator, model_validator

from .common import NonEmptyString, StrictModel, require_non_empty_string
from .profile_model import MigrationProfileId, is_selectable_source_profile
from .profile_validation import ProfilePairValidation, validate_profile_pair


class SourceProfileOverrideRequest(StrictModel):
    """Backend-owned manual source-profile override decision.

    This contract intentionally carries only checkpoint/profile evidence.
    It never accepts execution details such as paths, commands, argv, env,
    provider routing, deployments, or filesystem targets.
    """

    job_id: NonEmptyString
    gate_id: NonEmptyString
    detection_artifact_ref: NonEmptyString
    detected_source_profile: MigrationProfileId
    requested_source_profile: MigrationProfileId
    target_profile: MigrationProfileId
    expected_gate_checksum: NonEmptyString
    expected_detection_artifact_checksum: NonEmptyString
    reason: NonEmptyString
    comments: NonEmptyString
    decided_by: NonEmptyString
    actor_type: NonEmptyString = "human"

    @field_validator(
        "job_id",
        "gate_id",
        "detection_artifact_ref",
        "expected_gate_checksum",
        "expected_detection_artifact_checksum",
        "reason",
        "comments",
        "decided_by",
        "actor_type",
        mode="after",
    )
    @classmethod
    def _validate_required_strings(cls, value: str, info) -> str:
        return require_non_empty_string(value, info.field_name)

    @model_validator(mode="after")
    def _validate_profiles(self) -> "SourceProfileOverrideRequest":
        if self.detected_source_profile == self.requested_source_profile:
            raise ValueError("requested_source_profile must differ from detected_source_profile")
        if not is_selectable_source_profile(self.detected_source_profile):
            raise ValueError("detected_source_profile must be a supported source profile")
        if not is_selectable_source_profile(self.requested_source_profile):
            raise ValueError("requested_source_profile must be a supported source profile")
        validation = validate_profile_pair(
            self.requested_source_profile,
            self.target_profile,
        )
        if not validation.valid:
            raise ValueError(f"invalid source/target profile pair: {validation.reason}")
        return self

    def profile_pair_validation(self) -> ProfilePairValidation:
        return validate_profile_pair(self.requested_source_profile, self.target_profile)

    def to_safe_artifact(self) -> dict[str, Any]:
        validation = self.profile_pair_validation()
        return {
            "job_id": self.job_id,
            "gate_id": self.gate_id,
            "detection_artifact_ref": self.detection_artifact_ref,
            "detected_source_profile": self.detected_source_profile,
            "requested_source_profile": self.requested_source_profile,
            "target_profile": self.target_profile,
            "expected_gate_checksum": self.expected_gate_checksum,
            "expected_detection_artifact_checksum": self.expected_detection_artifact_checksum,
            "reason": self.reason,
            "comments": self.comments,
            "decided_by": self.decided_by,
            "actor_type": self.actor_type,
            "profile_validation": validation.to_dict(),
        }
