"""Focused tests for DEMO3 F4-T2 source-profile override decisions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_gate_action_service import (
    V2GateActionService,
)
from migration_factory.control_tower.application.v2_phase_gate_service import (
    CreateGateRequest,
    V2PhaseGateService,
)
from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.application.v2_stage_progression import (
    V2StageProgressionService,
    route_to_dict,
)
from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.domain.entities import RunConfigurationRecord
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.repositories import (
    SqliteRunConfigurationRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_artifact_revision_repository import (
    SqliteArtifactRevisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_gate_decision_repository import (
    SqliteGateDecisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)


def _connection(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(tmp_path / "source_profile_override.sqlite3"),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


def _setup(
    tmp_path: Path,
) -> tuple[
    V2GateActionService,
    V2PhaseGateService,
    SqliteGateDecisionRepository,
    SqliteArtifactRevisionRepository,
]:
    conn = _connection(tmp_path)
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    revision_repo = SqliteArtifactRevisionRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)
    action_service = V2GateActionService(
        gate_repo,
        decision_repo,
        gate_service,
        revision_repo=revision_repo,
    )
    return action_service, gate_service, decision_repo, revision_repo


def _create_analysis_gate(
    gate_service: V2PhaseGateService,
    *,
    job_id: str = "job-f4",
    phase: str = "analysis_review",
    checksum: str = "sha256:detection",
    refs: tuple[str, ...] = ("source-detection:1",),
) -> tuple[str, str]:
    result = gate_service.create_gate(
        CreateGateRequest(
            job_id=job_id,
            gate_phase=phase,
            stage_index=1,
            source_artifact_checksum=checksum,
            source_artifact_refs=refs,
        )
    )
    assert result.status == "created"
    return result.gate_id, result.gate_checksum


def _valid_override_payload(gate_id: str, gate_checksum: str) -> dict[str, str]:
    return {
        "gate_id": gate_id,
        "job_id": "job-f4",
        "detection_artifact_ref": "source-detection:1",
        "detected_source_profile": "springboot-2.7-java11",
        "requested_source_profile": "springboot-3.5-java17",
        "target_profile": "springboot-4.0-java21",
        "expected_gate_checksum": gate_checksum,
        "expected_detection_artifact_checksum": "sha256:detection",
        "reason": "Detection confidence was low.",
        "comments": "pom.xml and build logs show the app already runs Boot 3.5 on Java 17.",
        "decided_by": "operator-1",
    }


def test_source_profile_override_persists_checksum_bound_decision_and_revision(
    tmp_path: Path,
) -> None:
    action_service, gate_service, decision_repo, revision_repo = _setup(tmp_path)
    gate_id, gate_checksum = _create_analysis_gate(gate_service)

    result = action_service.override_source_profile(**_valid_override_payload(gate_id, gate_checksum))

    assert result.status == "executed"
    assert result.action == "override_source_profile"
    assert result.result_revision_id

    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.action == "override_source_profile"
    assert decision.reason == "Detection confidence was low."
    assert decision.request_checksum
    assert decision.result_revision_id == result.result_revision_id

    revision = revision_repo.get(result.result_revision_id)
    assert revision is not None
    assert revision.revision_kind == "source_profile_override"
    assert revision.revision_status == "accepted"
    assert revision.evidence_checksum == decision.request_checksum
    assert revision.accepted_at_gate_id == gate_id
    artifact = json.loads(revision.artifact_refs_json)
    assert artifact["detected_source_profile"] == "springboot-2.7-java11"
    assert artifact["requested_source_profile"] == "springboot-3.5-java17"
    assert artifact["target_profile"] == "springboot-4.0-java21"
    assert artifact["profile_validation"]["valid"] is True
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
    assert forbidden.isdisjoint(artifact)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reason", " "),
        ("comments", ""),
        ("requested_source_profile", "springboot-4.0-java21"),
        ("target_profile", "springboot-2.7-java11"),
    ],
)
def test_source_profile_override_rejects_invalid_required_fields(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    action_service, gate_service, _, _ = _setup(tmp_path)
    gate_id, gate_checksum = _create_analysis_gate(gate_service)
    payload = _valid_override_payload(gate_id, gate_checksum)
    payload[field] = value

    result = action_service.override_source_profile(**payload)

    assert result.status == "invalid_source_profile_override"


def test_source_profile_override_rejects_noop_reversed_stale_and_wrong_gate(
    tmp_path: Path,
) -> None:
    action_service, gate_service, _, _ = _setup(tmp_path)
    gate_id, gate_checksum = _create_analysis_gate(gate_service)

    same_payload = _valid_override_payload(gate_id, gate_checksum)
    same_payload["requested_source_profile"] = same_payload["detected_source_profile"]
    assert action_service.override_source_profile(**same_payload).status == (
        "invalid_source_profile_override"
    )

    reversed_payload = _valid_override_payload(gate_id, gate_checksum)
    reversed_payload["requested_source_profile"] = "springboot-3.5-java21"
    reversed_payload["target_profile"] = "springboot-3.5-java17"
    assert action_service.override_source_profile(**reversed_payload).status == (
        "invalid_source_profile_override"
    )

    stale_payload = _valid_override_payload(gate_id, gate_checksum)
    stale_payload["expected_detection_artifact_checksum"] = "sha256:old"
    assert action_service.override_source_profile(**stale_payload).status == "stale_checksum"

    artifact_payload = _valid_override_payload(gate_id, gate_checksum)
    artifact_payload["detection_artifact_ref"] = "other-artifact"
    assert action_service.override_source_profile(**artifact_payload).status == (
        "artifact_ref_mismatch"
    )

    wrong_job_payload = _valid_override_payload(gate_id, gate_checksum)
    wrong_job_payload["job_id"] = "job-other"
    assert action_service.override_source_profile(**wrong_job_payload).status == (
        "gate_job_mismatch"
    )


def test_source_profile_override_rejects_wrong_phase_and_non_human_actor(
    tmp_path: Path,
) -> None:
    action_service, gate_service, _, _ = _setup(tmp_path)
    gate_id, gate_checksum = _create_analysis_gate(gate_service, phase="planning_review")
    payload = _valid_override_payload(gate_id, gate_checksum)

    assert action_service.override_source_profile(**payload).status == "invalid_decision"

    gate_id, gate_checksum = _create_analysis_gate(gate_service, job_id="job-human")
    assistant_payload = _valid_override_payload(gate_id, gate_checksum)
    assistant_payload["job_id"] = "job-human"
    assistant_payload["actor_type"] = "assistant"
    assert action_service.override_source_profile(**assistant_payload).status == (
        "actor_not_authoritative"
    )


def test_source_profile_override_changes_effective_route_and_skipped_stage_ledger(
    tmp_path: Path,
) -> None:
    conn = _connection(tmp_path)
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    revision_repo = SqliteArtifactRevisionRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_repo = SqliteV2SetupRepository(conn)
    run_config_repo = SqliteRunConfigurationRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)
    action_service = V2GateActionService(
        gate_repo,
        decision_repo,
        gate_service,
        revision_repo=revision_repo,
    )
    setup_id = V2SetupService(setup_repo).create_setup(
        CreateSetupRequest(
            run_name="override-route",
            legacy_app_path="/tmp/legacy",
            output_parent_path="/tmp/output",
            ai_hub_path="/tmp/ai-hub",
            java11_home="/usr/lib/jvm/java-11",
            java17_home="/usr/lib/jvm/java-17",
            java21_home="/usr/lib/jvm/java-21",
            maven_cmd="/usr/bin/mvn",
        )
    ).setup_id
    _insert_run_config(
        run_config_repo,
        job_id="job-f4",
        source_profile="springboot-2.7-java11",
        target_profile="springboot-4.0-java21",
    )
    gate_id, gate_checksum = _create_analysis_gate(
        gate_service,
        checksum="sha256:detection-artifact",
        refs=("source-profile-detection:1",),
    )
    payload = _valid_override_payload(gate_id, gate_checksum)
    payload["detection_artifact_ref"] = "source-profile-detection:1"
    payload["expected_detection_artifact_checksum"] = "sha256:detection-artifact"

    result = action_service.override_source_profile(**payload)

    assert result.status == "executed"
    revision = revision_repo.get(result.result_revision_id or "")
    assert revision is not None
    override_artifact = json.loads(revision.artifact_refs_json)
    service = V2StageProgressionService(
        setup_repo,
        command_repo,
        artifact_revision_repo=revision_repo,
        run_config_repo=run_config_repo,
    )

    route = service.compute_route_for_job("job-f4")
    queued = service.queue_next_stage(
        job_id="job-f4",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1",
        profile_route=route,
    )
    metadata = route_to_dict(
        route,
        job_id="job-f4",
        evidence_ref=override_artifact["detection_artifact_ref"],
        evidence_checksum=override_artifact["expected_detection_artifact_checksum"],
        artifact_checksum=revision.evidence_checksum,
        created_at="2026-06-27T10:00:00Z",
    )

    assert route.source_profile == "springboot-3.5-java17"
    assert route.target_profile == "springboot-4.0-java21"
    assert route.included_stages == (3, 4)
    assert route.skipped_stages == (2,)
    assert queued.status == "queued"
    assert queued.to_stage == 3
    assert "springboot-3.5-java17-to-java21" in " ".join(queued.argv)
    assert command_repo.list_by_job_and_stage("job-f4", 2) == ()
    assert len(command_repo.list_by_job_and_stage("job-f4", 3)) == 1
    assert metadata["source_profile"] == "springboot-3.5-java17"
    assert metadata["source_profile"] != "springboot-2.7-java11"
    assert metadata["skipped_stages"] == [2]
    assert metadata["skipped_stage_ledger"][0]["skipped_stage_index"] == 2
    assert metadata["skipped_stage_ledger"][0]["evidence_ref"] == (
        "source-profile-detection:1"
    )
    assert metadata["skipped_stage_ledger"][0]["evidence_checksum"] == (
        "sha256:detection-artifact"
    )
    assert metadata["skipped_stage_ledger"][0]["route_checksum"] == metadata["route_checksum"]
    assert metadata["skipped_stage_ledger"][0]["artifact_checksum"] == revision.evidence_checksum
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
    public_payload = json.dumps({"override": override_artifact, "route": metadata})
    for field in forbidden:
        assert field not in public_payload


def _insert_run_config(
    repo: SqliteRunConfigurationRepository,
    *,
    job_id: str,
    source_profile: str,
    target_profile: str,
) -> None:
    payload = {
        "source_profile": source_profile,
        "target_profile": target_profile,
    }
    repo.insert(
        RunConfigurationRecord(
            run_configuration_id=f"rc-{job_id}",
            job_id=job_id,
            schema_version="1",
            runner_profile_id="demo3",
            runner_profile_version="1",
            pipeline_id="demo3",
            pipeline_version="1",
            target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
            enabled_gates_json="[]",
            policy_json="{}",
            payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            payload_checksum=sha256_canonical_json(payload),
            created_at=utc_now_text(),
        )
    )
