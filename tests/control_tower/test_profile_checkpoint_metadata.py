"""F3-T6 focused tests — profile checkpoint metadata round-trip."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.schemas.artifact_revision import (
    ArtifactRevision,
    ArtifactRevisionKind,
    ArtifactRevisionStatus,
)
from migration_factory.control_tower.schemas.profile_checkpoint_metadata import (
    PROFILE_CHECKPOINT_FIELDS,
    CheckpointProfileMetadata,
    SkippedStageLedgerEntry,
)


# ── helpers ───────────────────────────────────────────────────────────


def _valid_metadata(**overrides) -> CheckpointProfileMetadata:
    defaults = {
        "source_profile": "springboot-2.7-java11",
        "target_profile": "springboot-3.5-java17",
        "source_level": 1,
        "target_level": 2,
        "included_stages": (2, 3),
        "excluded_stages": (),
        "skipped_stages": (),
        "valid": True,
        "reason": "",
    }
    defaults.update(overrides)
    return CheckpointProfileMetadata(**defaults)


def _valid_revision(**overrides) -> ArtifactRevision:
    defaults = {
        "revision_id": "rev-001",
        "job_id": "job-abc",
        "stage_index": 1,
        "revision_kind": ArtifactRevisionKind.ANALYSIS,
        "revision_status": ArtifactRevisionStatus.DRAFT,
        "revision_order": 0,
        "evidence_checksum": "sha256:abc",
        "created_at": "2026-06-17T12:00:00Z",
        "created_by": "system",
    }
    defaults.update(overrides)
    return ArtifactRevision(**defaults)


# ══════════════════════════════════════════════════════════════════════════
# 1. PROFILE_CHECKPOINT_FIELDS contract
# ══════════════════════════════════════════════════════════════════════════


def test_profile_checkpoint_fields_are_public_safe() -> None:
    """All fields in PROFILE_CHECKPOINT_FIELDS are profile-routing metadata."""
    assert "source_profile" in PROFILE_CHECKPOINT_FIELDS
    assert "target_profile" in PROFILE_CHECKPOINT_FIELDS
    assert "included_stages" in PROFILE_CHECKPOINT_FIELDS
    assert "excluded_stages" in PROFILE_CHECKPOINT_FIELDS
    assert "skipped_stages" in PROFILE_CHECKPOINT_FIELDS
    assert "skipped_stage_ledger" in PROFILE_CHECKPOINT_FIELDS
    assert "valid" in PROFILE_CHECKPOINT_FIELDS
    assert "reason" in PROFILE_CHECKPOINT_FIELDS


def test_profile_checkpoint_fields_exclude_dangerous() -> None:
    """Forbidden fields must never appear in checkpoint metadata."""
    dangerous = {
        "provider", "model", "deployment", "sandbox_path",
        "argv", "env", "raw_command", "filesystem_target",
        "endpoint", "secret", "token", "password",
    }
    assert PROFILE_CHECKPOINT_FIELDS.isdisjoint(dangerous)


# ══════════════════════════════════════════════════════════════════════════
# 2. CheckpointProfileMetadata — construction & defaults
# ══════════════════════════════════════════════════════════════════════════


def test_default_metadata_is_safe_empty() -> None:
    """Default-constructed metadata has safe empty values."""
    m = CheckpointProfileMetadata()
    assert m.source_profile == ""
    assert m.target_profile == ""
    assert m.source_level == -1
    assert m.target_level == -1
    assert m.included_stages == ()
    assert m.excluded_stages == ()
    assert m.skipped_stages == ()
    assert m.skipped_stage_ledger == ()
    assert m.valid is False
    assert m.reason == ""
    assert m.source_profile_detection_ref == ""
    assert m.source_profile_detection_checksum == ""
    assert m.source_profile_detection_confidence is None
    assert m.source_profile_detection_uncertainty_notes == ()
    assert m.has_profiles is False
    assert m.stage_count == 0
    assert m.is_no_op is False


def test_valid_metadata_construction() -> None:
    m = _valid_metadata()
    assert m.source_profile == "springboot-2.7-java11"
    assert m.target_profile == "springboot-3.5-java17"
    assert m.source_level == 1
    assert m.target_level == 2
    assert m.included_stages == (2, 3)
    assert m.valid is True
    assert m.has_profiles is True
    assert m.stage_count == 2
    assert m.is_no_op is False


def test_no_op_detection() -> None:
    m = _valid_metadata(
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java17",
    )
    assert m.is_no_op is True
    assert m.has_profiles is True


def test_invalid_route_metadata() -> None:
    m = _valid_metadata(
        source_profile="unknown-profile",
        target_profile="springboot-4.0-java21",
        source_level=-1,
        target_level=-1,
        included_stages=(),
        valid=False,
        reason="Source profile is not selectable.",
    )
    assert m.valid is False
    assert m.reason == "Source profile is not selectable."
    assert m.stage_count == 0


def test_skipped_stages_captured() -> None:
    m = _valid_metadata(
        source_profile="springboot-3.5-java17",
        target_profile="springboot-4.0-java21",
        source_level=3,
        target_level=4,
        included_stages=(3, 4),
        skipped_stages=(2,),
    )
    assert m.skipped_stages == (2,)
    assert m.included_stages == (3, 4)


def test_skipped_stage_ledger_captured() -> None:
    entry = SkippedStageLedgerEntry(
        job_id="job-123",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-4.0-java21",
        skipped_stage_index=2,
        skipped_stage_name="Stage 2",
        skipped_stage_profile="springboot-2.7-to-3.5-java17",
        reason="Skipped because source profile starts after stage 2.",
        evidence_ref="artifact:source-profile-detection",
        evidence_checksum="sha256:detection",
        route_checksum="sha256:route",
        created_at="2026-06-27T10:00:00Z",
    )
    m = _valid_metadata(
        source_profile="springboot-3.5-java17",
        target_profile="springboot-4.0-java21",
        skipped_stages=(2,),
        skipped_stage_ledger=(entry,),
    )

    assert m.skipped_stage_ledger == (entry,)
    assert m.to_dict()["skipped_stage_ledger"][0]["job_id"] == "job-123"


def test_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CheckpointProfileMetadata(
            source_profile="s",
            target_profile="t",
            sandbox_path="/evil",  # blocked
        )


def test_immutable() -> None:
    m = _valid_metadata()
    with pytest.raises(ValidationError):
        m.source_profile = "changed"  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════
# 3. Serialization round-trip
# ══════════════════════════════════════════════════════════════════════════


def test_to_dict_round_trip() -> None:
    original = _valid_metadata()
    d = original.to_dict()
    restored = CheckpointProfileMetadata.from_dict(d)
    assert restored.source_profile == original.source_profile
    assert restored.target_profile == original.target_profile
    assert restored.included_stages == original.included_stages
    assert restored.excluded_stages == original.excluded_stages
    assert restored.skipped_stages == original.skipped_stages
    assert restored.valid == original.valid
    assert restored.reason == original.reason


def test_source_profile_detection_metadata_round_trip() -> None:
    original = _valid_metadata().with_source_profile_detection(
        type("Detection", (), {
            "artifact_ref": "analysis:source-profile-detection",
            "artifact_checksum": "sha256:detection",
            "confidence": 0.9,
            "uncertainty_notes": ("Spring Boot and Java signals agree.",),
        })()
    )

    restored = CheckpointProfileMetadata.from_dict(original.to_dict())

    assert restored.source_profile_detection_ref == "analysis:source-profile-detection"
    assert restored.source_profile_detection_checksum == "sha256:detection"
    assert restored.source_profile_detection_confidence == 0.9
    assert restored.source_profile_detection_uncertainty_notes == (
        "Spring Boot and Java signals agree.",
    )


def test_to_json_round_trip() -> None:
    original = _valid_metadata(reason="Profile pair is valid.")
    json_str = original.to_json()
    restored = CheckpointProfileMetadata.from_json(json_str)
    assert restored == original


def test_skipped_stage_ledger_json_round_trip() -> None:
    entry = SkippedStageLedgerEntry(
        job_id="job-123",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-4.0-java21",
        skipped_stage_index=2,
        skipped_stage_name="Stage 2",
        skipped_stage_profile="springboot-2.7-to-3.5-java17",
        reason="Skipped because source profile starts after stage 2.",
        evidence_ref="artifact:source-profile-detection",
        evidence_checksum="sha256:detection",
        route_checksum="sha256:route",
        created_at="2026-06-27T10:00:00Z",
    )
    original = _valid_metadata(skipped_stage_ledger=(entry,))

    restored = CheckpointProfileMetadata.from_json(original.to_json())

    assert restored.skipped_stage_ledger == (entry,)


def test_json_is_deterministic() -> None:
    m1 = _valid_metadata()
    m2 = _valid_metadata()
    assert m1.to_json() == m2.to_json()


def test_from_dict_missing_keys_defaults() -> None:
    restored = CheckpointProfileMetadata.from_dict({})
    assert restored.source_profile == ""
    assert restored.target_profile == ""
    assert restored.valid is False


def test_from_dict_partial_keys() -> None:
    restored = CheckpointProfileMetadata.from_dict({
        "source_profile": "springboot-2.7-java11",
        "target_profile": "springboot-3.5-java17",
        "valid": True,
    })
    assert restored.source_profile == "springboot-2.7-java11"
    assert restored.target_profile == "springboot-3.5-java17"
    assert restored.valid is True
    assert restored.source_level == -1  # default


def test_from_json_invalid_raises() -> None:
    with pytest.raises((json.JSONDecodeError, ValueError)):
        CheckpointProfileMetadata.from_json("not json")


def test_from_dict_handles_none_values() -> None:
    """from_dict must not corrupt or crash when keys have None values.
    This guards against database NULL columns or API fields set to null."""
    restored = CheckpointProfileMetadata.from_dict({
        "source_profile": None,
        "target_profile": None,
        "source_level": None,
        "target_level": None,
        "included_stages": None,
        "excluded_stages": None,
        "skipped_stages": None,
        "valid": None,
        "reason": None,
    })
    assert restored.source_profile == ""
    assert restored.target_profile == ""
    assert restored.source_level == -1
    assert restored.target_level == -1
    assert restored.included_stages == ()
    assert restored.excluded_stages == ()
    assert restored.skipped_stages == ()
    assert restored.skipped_stage_ledger == ()
    assert restored.valid is False
    assert restored.reason == ""
    assert restored.source_profile_detection_ref == ""
    assert restored.source_profile_detection_checksum == ""
    assert restored.source_profile_detection_confidence is None
    assert restored.source_profile_detection_uncertainty_notes == ()


def test_from_dict_handles_partial_none_values() -> None:
    """Mix of None and valid values should work correctly."""
    restored = CheckpointProfileMetadata.from_dict({
        "source_profile": "springboot-2.7-java11",
        "target_profile": None,
        "source_level": 1,
        "target_level": None,
        "included_stages": [2, 3],
        "excluded_stages": None,
        "valid": True,
        "reason": None,
    })
    assert restored.source_profile == "springboot-2.7-java11"
    assert restored.target_profile == ""  # None → default
    assert restored.source_level == 1
    assert restored.target_level == -1  # None → default
    assert restored.included_stages == (2, 3)
    assert restored.excluded_stages == ()  # None → default
    assert restored.valid is True
    assert restored.reason == ""  # None → default


# ══════════════════════════════════════════════════════════════════════════
# 4. ProfileRoute conversion
# ══════════════════════════════════════════════════════════════════════════


def test_from_profile_route_valid() -> None:
    """Simulate a ProfileRoute object being converted to metadata."""

    class FakeRoute:
        source_profile = "springboot-2.7-java11"
        target_profile = "springboot-3.5-java17"
        source_level = 1
        target_level = 2
        included_stages = (2, 3)
        excluded_stages = ()
        skipped_stages = ()
        valid = True
        reason = ""

    m = CheckpointProfileMetadata.from_profile_route(FakeRoute())
    assert m.source_profile == "springboot-2.7-java11"
    assert m.target_profile == "springboot-3.5-java17"
    assert m.included_stages == (2, 3)
    assert m.valid is True


def test_from_profile_route_accepts_skipped_stage_ledger() -> None:
    class FakeRoute:
        source_profile = "springboot-3.5-java17"
        target_profile = "springboot-4.0-java21"
        source_level = 1
        target_level = 3
        included_stages = (3, 4)
        excluded_stages = ()
        skipped_stages = (2,)
        valid = True
        reason = ""

    entry = SkippedStageLedgerEntry(
        job_id="job-123",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-4.0-java21",
        skipped_stage_index=2,
        skipped_stage_name="Stage 2",
        skipped_stage_profile="springboot-2.7-to-3.5-java17",
        reason="Skipped because source profile starts after stage 2.",
        evidence_ref="artifact:source-profile-detection",
        evidence_checksum="sha256:detection",
        route_checksum="sha256:route",
        created_at="2026-06-27T10:00:00Z",
    )

    m = CheckpointProfileMetadata.from_profile_route(
        FakeRoute(),
        skipped_stage_ledger=(entry,),
    )

    assert m.skipped_stages == (2,)
    assert m.skipped_stage_ledger == (entry,)


def test_from_profile_route_invalid() -> None:
    class FakeRoute:
        source_profile = "bad-profile"
        target_profile = "springboot-3.5-java17"
        source_level = -1
        target_level = 2
        included_stages = ()
        excluded_stages = ()
        skipped_stages = ()
        valid = False
        reason = "Source profile unknown."

    m = CheckpointProfileMetadata.from_profile_route(FakeRoute())
    assert m.valid is False
    assert m.reason == "Source profile unknown."


def test_from_profile_route_missing_attrs() -> None:
    """Gracefully handles objects with missing attributes."""

    class MinimalRoute:
        pass

    m = CheckpointProfileMetadata.from_profile_route(MinimalRoute())
    assert m.source_profile == ""
    assert m.valid is False


# ══════════════════════════════════════════════════════════════════════════
# 5. ArtifactRevision profile metadata fields
# ══════════════════════════════════════════════════════════════════════════


def test_artifact_revision_default_no_profile() -> None:
    rev = _valid_revision()
    assert rev.source_profile is None
    assert rev.target_profile is None


def test_artifact_revision_with_profile_metadata() -> None:
    rev = _valid_revision(
        source_profile="springboot-2.7-java11",
        target_profile="springboot-4.0-java21",
    )
    assert rev.source_profile == "springboot-2.7-java11"
    assert rev.target_profile == "springboot-4.0-java21"


def test_artifact_revision_profile_round_trip() -> None:
    """Profile metadata on ArtifactRevision survives serialization round-trip."""
    rev = _valid_revision(
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
    )
    data = rev.model_dump()
    restored = ArtifactRevision(**data)
    assert restored.source_profile == "springboot-3.5-java17"
    assert restored.target_profile == "springboot-3.5-java21"


def test_artifact_revision_profile_none_ok() -> None:
    """None is valid for profile fields — backward compatible."""
    rev = _valid_revision(source_profile=None, target_profile=None)
    assert rev.source_profile is None
    assert rev.target_profile is None


def test_artifact_revision_rejects_sandbox_path_even_with_profiles() -> None:
    """The 'extra=forbid' contract still holds when profile fields are set."""
    with pytest.raises(ValidationError):
        ArtifactRevision(
            revision_id="rev-001",
            job_id="job-abc",
            stage_index=1,
            revision_kind="analysis",
            revision_order=0,
            evidence_checksum="sha256:abc",
            created_at="2026-06-17T12:00:00Z",
            created_by="system",
            source_profile="springboot-2.7-java11",
            target_profile="springboot-3.5-java17",
            sandbox_path="/tmp/evil",  # blocked
        )
