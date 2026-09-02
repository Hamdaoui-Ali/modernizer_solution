"""Tests for safe API field contracts (AMF-266 / F3-T5)."""

from __future__ import annotations

import pytest

from migration_factory.control_tower.schemas.profile_api_contract import (
    ALLOWED_PROFILE_API_FIELDS,
    FORBIDDEN_PROFILE_API_FIELDS,
    validate_profile_api_payload,
    redact_forbidden_profile_fields,
)


# ── field definitions ──────────────────────────────────────────────

def test_allowed_fields_are_non_empty() -> None:
    assert len(ALLOWED_PROFILE_API_FIELDS) > 0


def test_forbidden_fields_are_non_empty() -> None:
    assert len(FORBIDDEN_PROFILE_API_FIELDS) > 0


def test_no_overlap_between_allowed_and_forbidden() -> None:
    overlap = ALLOWED_PROFILE_API_FIELDS & FORBIDDEN_PROFILE_API_FIELDS
    assert not overlap, f"Overlapping fields: {overlap}"


def test_source_and_target_in_allowed_fields() -> None:
    assert "source_profile" in ALLOWED_PROFILE_API_FIELDS
    assert "target_profile" in ALLOWED_PROFILE_API_FIELDS


def test_route_fields_in_allowed() -> None:
    for field in ("validation_status", "validation_reason",
                  "included_stages", "excluded_stages", "skipped_stages"):
        assert field in ALLOWED_PROFILE_API_FIELDS, f"Missing allowed: {field}"


def test_forbidden_fields_include_runtime_details() -> None:
    for field in (
        "provider", "model", "deployment", "env_ref",
        "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
    ):
        assert field in FORBIDDEN_PROFILE_API_FIELDS, f"Missing forbidden: {field}"


# ── validation ─────────────────────────────────────────────────────

def test_clean_payload_passes_validation() -> None:
    payload = {
        "source_profile": "springboot-2.7-java11",
        "target_profile": "springboot-3.5-java17",
        "validation_status": "valid",
        "included_stages": [2],
        "excluded_stages": [3, 4],
    }
    validate_profile_api_payload(payload)


def test_forbidden_provider_rejected() -> None:
    payload = {"source_profile": "springboot-2.7-java11", "provider": "azure"}
    with pytest.raises(ValueError, match="forbidden field"):
        validate_profile_api_payload(payload)


def test_forbidden_model_rejected() -> None:
    payload = {"model": "gpt-4"}
    with pytest.raises(ValueError, match="forbidden field"):
        validate_profile_api_payload(payload)


def test_forbidden_deployment_rejected() -> None:
    payload = {"deployment": "prod-east"}
    with pytest.raises(ValueError, match="forbidden field"):
        validate_profile_api_payload(payload)


def test_forbidden_sandbox_path_rejected() -> None:
    payload = {"sandbox_path": "/tmp/sandbox"}
    with pytest.raises(ValueError, match="forbidden field"):
        validate_profile_api_payload(payload)


def test_forbidden_argv_rejected() -> None:
    payload = {"argv": ["--profile", "springboot-3.5"]}
    with pytest.raises(ValueError, match="forbidden field"):
        validate_profile_api_payload(payload)


def test_forbidden_env_ref_rejected() -> None:
    payload = {"env_ref": "JAVA17_HOME"}
    with pytest.raises(ValueError, match="forbidden field"):
        validate_profile_api_payload(payload)


def test_forbidden_raw_command_rejected() -> None:
    payload = {"raw_command": "mvn clean install"}
    with pytest.raises(ValueError, match="forbidden field"):
        validate_profile_api_payload(payload)


def test_multiple_forbidden_found_in_error_message() -> None:
    payload = {"provider": "azure", "model": "gpt-4", "source_profile": "sb2"}
    with pytest.raises(ValueError) as exc_info:
        validate_profile_api_payload(payload)
    assert "provider" in str(exc_info.value)
    assert "model" in str(exc_info.value)


def test_non_dict_payload_rejected() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        validate_profile_api_payload("not-a-dict")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="JSON object"):
        validate_profile_api_payload(None)  # type: ignore[arg-type]


# ── redaction ──────────────────────────────────────────────────────

def test_redaction_strips_forbidden_fields() -> None:
    data = {
        "source_profile": "springboot-2.7-java11",
        "target_profile": "springboot-3.5-java17",
        "provider": "azure",
        "model": "gpt-4",
        "deployment": "prod-east",
        "sandbox_path": "/tmp/sandbox",
    }
    result = redact_forbidden_profile_fields(data)
    assert "source_profile" in result
    assert "target_profile" in result
    assert "provider" not in result
    assert "model" not in result
    assert "deployment" not in result
    assert "sandbox_path" not in result


def test_redaction_preserves_clean_payload() -> None:
    data = {
        "source_profile": "springboot-2.7-java11",
        "target_profile": "springboot-3.5-java17",
        "validation_status": "valid",
    }
    result = redact_forbidden_profile_fields(data)
    assert result == data


def test_redaction_does_not_mutate_original() -> None:
    data = {"source_profile": "sb2", "provider": "azure"}
    redact_forbidden_profile_fields(data)
    assert "provider" in data  # original untouched


def test_redaction_handles_empty_dict() -> None:
    assert redact_forbidden_profile_fields({}) == {}


def test_redaction_returns_non_dict_as_is() -> None:
    assert redact_forbidden_profile_fields("string") == "string"
    assert redact_forbidden_profile_fields(42) == 42


# ── integration: CreateV2JobRequest forbids extra fields ────────────

def test_create_v2_job_request_rejects_forbidden_extra_field() -> None:
    from pydantic import ValidationError
    from migration_factory.control_tower.adapters.fastapi.app import CreateV2JobRequest

    with pytest.raises(ValidationError):
        CreateV2JobRequest(
            setup_id="setup-123",
            source_profile="springboot-2.7-java11",
            target_profile="springboot-3.5-java17",
            provider="azure",  # not in the model -> rejected by extra="forbid"
        )


def test_create_v2_job_request_rejects_invalid_profile_pair() -> None:
    from pydantic import ValidationError
    from migration_factory.control_tower.adapters.fastapi.app import CreateV2JobRequest

    with pytest.raises(ValidationError, match="invalid profile pair"):
        CreateV2JobRequest(
            setup_id="setup-123",
            source_profile="springboot-3.5-java21",
            target_profile="springboot-3.5-java17",
        )


def test_create_v2_job_request_rejects_sandbox_path() -> None:
    from pydantic import ValidationError
    from migration_factory.control_tower.adapters.fastapi.app import CreateV2JobRequest

    with pytest.raises(ValidationError):
        CreateV2JobRequest(
            setup_id="setup-123",
            sandbox_path="/tmp/sandbox",
        )


def test_stage_progress_request_rejects_sandbox_path_and_argv() -> None:
    from pydantic import ValidationError
    from migration_factory.control_tower.adapters.fastapi.app import StageProgressRequest

    with pytest.raises(ValidationError):
        StageProgressRequest(
            setup_id="setup-123",
            current_stage=1,
            sandbox_path="/tmp/sandbox",
        )

    with pytest.raises(ValidationError):
        StageProgressRequest(
            setup_id="setup-123",
            current_stage=1,
            argv=["python", "-m", "migration_factory.orchestrator.runner"],
        )


def test_stage_continuation_public_projection_redacts_execution_details(tmp_path) -> None:
    import sqlite3
    from migration_factory.control_tower.application.v2_stage_progression import (
        V2StageProgressionService,
    )
    from migration_factory.control_tower.infrastructure.sqlite.migrations import (
        apply_pending_migrations,
    )
    from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
        SqliteV2SetupRepository,
    )
    from migration_factory.control_tower.schemas.run_configuration import (
        StageContinuationPolicy,
    )

    conn = sqlite3.connect(str(tmp_path / "profile_api.sqlite3"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    service = V2StageProgressionService(repo)
    result = service.queue_next_stage(
        job_id="job-1",
        setup_id="missing",
        current_stage=1,
        sandbox_path="/tmp/sandbox",
        stage_continuation_policy=StageContinuationPolicy.MANUAL,
    )

    public = service.continuation_to_public_dict(result)
    assert "sandbox_path" not in public
    assert "argv" not in public
    assert set(public).issubset({
        "continuation_id",
        "job_id",
        "from_stage",
        "to_stage",
        "status",
        "reason",
        "command_id",
    })
