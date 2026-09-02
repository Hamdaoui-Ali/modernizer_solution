"""AMF-272 / F4-T4 profile pair validation coverage."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.adapters.fastapi.app import CreateV2JobRequest
from migration_factory.control_tower.application.v2_stage_progression import (
    V2StageProgressionService,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.entities import RunConfigurationRecord
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.repositories import (
    SqliteRunConfigurationRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)
from migration_factory.control_tower.schemas.profile_validation import (
    ProfilePairErrorType,
    validate_profile_pair,
)
from migration_factory.control_tower.schemas.run_configuration import (
    RunConfiguration,
    RunPolicy,
)


def test_same_profile_requires_explicit_noop() -> None:
    rejected = validate_profile_pair(
        "springboot-3.5-java17",
        "springboot-3.5-java17",
    )
    assert rejected.valid is False
    assert rejected.error_type == ProfilePairErrorType.SAME_PROFILE

    allowed = validate_profile_pair(
        "springboot-3.5-java17",
        "springboot-3.5-java17",
        allow_same_profile=True,
    )
    assert allowed.valid is True
    assert allowed.error_type == ProfilePairErrorType.VALID
    assert "no-op" in allowed.reason


def test_directional_profile_misuse_is_rejected() -> None:
    source_only_target = validate_profile_pair(
        "springboot-4.0-java21",
        "springboot-4.0-java21",
    )
    assert source_only_target.valid is False
    assert source_only_target.error_type == ProfilePairErrorType.SOURCE_NOT_SELECTABLE

    target_only_source = validate_profile_pair(
        "springboot-3.5-java17",
        "springboot-2.1-java11",
    )
    assert target_only_source.valid is False
    assert target_only_source.error_type == ProfilePairErrorType.TARGET_NOT_SELECTABLE


def test_run_configuration_rejects_overridden_reversed_pair() -> None:
    with pytest.raises(ValidationError, match="target profile must be higher"):
        _run_configuration(
            source_profile="springboot-3.5-java21",
            target_profile="springboot-3.5-java17",
        )


def test_create_v2_job_request_rejects_invalid_profile_pair() -> None:
    with pytest.raises(ValidationError, match="invalid profile pair"):
        CreateV2JobRequest(
            setup_id="setup-1",
            source_profile="springboot-3.5-java17",
            target_profile="springboot-3.5-java17",
        )


def test_stage_progression_uses_persisted_profile_pair_for_continuation(
    tmp_path: Path,
) -> None:
    conn = sqlite3.connect(
        str(tmp_path / "profile-pair.sqlite3"),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)

    job_id = "job-profile-pair"
    command_repo = SqliteV2CommandRepository(conn)
    run_config_repo = SqliteRunConfigurationRepository(conn)
    _save_stage_output(command_repo, job_id=job_id, stage_index=1)
    _save_run_configuration(
        run_config_repo,
        job_id=job_id,
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java17",
    )

    service = V2StageProgressionService(
        SqliteV2SetupRepository(conn),
        command_repo,
        run_config_repo=run_config_repo,
    )
    result = service.queue_next_stage_from_persisted(
        job_id=job_id,
        setup_id="missing-setup-is-not-consulted-for-invalid-route",
        current_stage=1,
    )

    assert result.status == "blocked"
    assert result.reason == "profile_incompatible"
    assert result.argv == ()
    assert result.command_id is None


def _run_configuration(
    *,
    source_profile: str,
    target_profile: str,
) -> RunConfiguration:
    return RunConfiguration(
        schema_version="1.0.0",
        run_configuration_id="run-config-test",
        job_id="job-test",
        runner_profile_id="runner-default",
        runner_profile_version="2026.06",
        pipeline_id="pipeline-default",
        pipeline_version="2026.06",
        target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
        enabled_gates=(),
        policy=RunPolicy(),
        source_profile=source_profile,
        target_profile=target_profile,
    )


def _save_stage_output(
    command_repo: SqliteV2CommandRepository,
    *,
    job_id: str,
    stage_index: int,
) -> None:
    now = utc_now_text()
    command_repo.save(
        V2StageCommandRecord(
            command_id=f"cmd-stage{stage_index}",
            job_id=job_id,
            stage_index=stage_index,
            manifest_checksum=f"checksum-stage{stage_index}",
            argv_json=json.dumps(["backend-owned"]),
            env_json="{}",
            status="completed",
            created_at=now,
            updated_at=now,
            result_json=json.dumps({"sandbox_path": f"/tmp/stage{stage_index}"}),
        )
    )


def _save_run_configuration(
    run_config_repo: SqliteRunConfigurationRepository,
    *,
    job_id: str,
    source_profile: str,
    target_profile: str,
) -> None:
    payload = {
        "source_profile": source_profile,
        "target_profile": target_profile,
        "policy": RunPolicy().model_dump(mode="json"),
    }
    run_config_repo.insert(
        RunConfigurationRecord(
            run_configuration_id=f"run-config-{job_id}",
            job_id=job_id,
            schema_version="1.0.0",
            runner_profile_id="runner-default",
            runner_profile_version="2026.06",
            pipeline_id="pipeline-default",
            pipeline_version="2026.06",
            target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
            enabled_gates_json="[]",
            policy_json=json.dumps(payload["policy"], separators=(",", ":")),
            payload_json=json.dumps(payload, separators=(",", ":")),
            payload_checksum="checksum",
            created_at=utc_now_text(),
        )
    )
