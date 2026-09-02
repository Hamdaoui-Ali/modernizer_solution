"""AMF-271 / F4-T3 focused tests for skipped-stage ledger metadata."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from migration_factory.control_tower.application.v2_stage_progression import (
    build_skipped_stage_ledger,
    compute_profile_route,
    route_checksum,
    route_to_dict,
)
from migration_factory.control_tower.schemas.profile_checkpoint_metadata import (
    SkippedStageLedgerEntry,
)


def test_builds_checksum_bound_ledger_for_skipped_older_stages() -> None:
    route = compute_profile_route("springboot-3.5-java17", "springboot-4.0-java21")

    ledger = build_skipped_stage_ledger(
        route,
        job_id="job-123",
        evidence_ref="artifact:source-profile-detection",
        evidence_checksum="sha256:detection",
        artifact_checksum="sha256:artifact",
        created_at="2026-06-27T10:00:00Z",
    )

    assert len(ledger) == 1
    entry = ledger[0]
    assert entry.job_id == "job-123"
    assert entry.source_profile == "springboot-3.5-java17"
    assert entry.target_profile == "springboot-4.0-java21"
    assert entry.skipped_stage_index == 2
    assert entry.skipped_stage_name == "Stage 2"
    assert entry.skipped_stage_profile == "springboot-2.7-to-3.5-java17"
    assert entry.reason
    assert entry.evidence_ref == "artifact:source-profile-detection"
    assert entry.evidence_checksum == "sha256:detection"
    assert entry.route_checksum == route_checksum(route)
    assert entry.artifact_checksum == "sha256:artifact"
    assert entry.created_at == "2026-06-27T10:00:00Z"


def test_ledger_is_empty_for_routes_without_skipped_stages() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java17")

    assert build_skipped_stage_ledger(route, job_id="job-123") == ()


def test_ledger_is_empty_for_invalid_route() -> None:
    route = compute_profile_route("springboot-3.5-java21", "springboot-3.5-java17")

    assert route.valid is False
    assert build_skipped_stage_ledger(route, job_id="job-123") == ()


def test_ledger_projection_excludes_forbidden_execution_fields() -> None:
    route = compute_profile_route("springboot-3.5-java17", "springboot-4.0-java21")
    entry = build_skipped_stage_ledger(
        route,
        job_id="job-123",
        evidence_ref="artifact:source-profile-detection",
        evidence_checksum="sha256:detection",
        created_at="2026-06-27T10:00:00Z",
    )[0]

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
    }
    assert forbidden.isdisjoint(entry.to_dict())


def test_schema_rejects_extra_forbidden_fields() -> None:
    with pytest.raises(ValidationError):
        SkippedStageLedgerEntry(
            job_id="job-123",
            source_profile="springboot-3.5-java17",
            target_profile="springboot-4.0-java21",
            skipped_stage_index=2,
            skipped_stage_name="Stage 2",
            skipped_stage_profile="springboot-2.7-to-3.5-java17",
            reason="skipped",
            evidence_ref="artifact:source-profile-detection",
            evidence_checksum="sha256:detection",
            route_checksum="sha256:route",
            created_at="2026-06-27T10:00:00Z",
            sandbox_path="/tmp/not-public",
        )


def test_ledger_records_multiple_old_stages_for_java21_source() -> None:
    route = compute_profile_route("springboot-3.5-java21", "springboot-4.0-java21")

    ledger = build_skipped_stage_ledger(route, job_id="job-java21")

    assert [entry.skipped_stage_index for entry in ledger] == [2, 3]
    assert [entry.skipped_stage_profile for entry in ledger] == [
        "springboot-2.7-to-3.5-java17",
        "springboot-3.5-java17-to-java21",
    ]
    assert all(entry.job_id == "job-java21" for entry in ledger)
    assert all("springboot-3.5-java21" in entry.reason for entry in ledger)
    assert all(entry.route_checksum == route_checksum(route) for entry in ledger)


def test_route_metadata_includes_safe_skipped_stage_ledger() -> None:
    route = compute_profile_route("springboot-3.5-java21", "springboot-4.0-java21")

    metadata = route_to_dict(route, job_id="job-java21")

    assert metadata["skipped_stages"] == [2, 3]
    assert [entry["skipped_stage_index"] for entry in metadata["skipped_stage_ledger"]] == [2, 3]
    for entry in metadata["skipped_stage_ledger"]:
        assert entry["job_id"] == "job-java21"
        assert entry["reason"]
        assert entry["route_checksum"] == metadata["route_checksum"]
        assert "sandbox_path" not in entry
        assert "argv" not in entry
        assert "env" not in entry
        assert "raw_command" not in entry
