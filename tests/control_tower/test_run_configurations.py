from __future__ import annotations

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.schemas.run_configuration import (
    RunConfiguration,
    RunPolicy,
    StageContinuationPolicy,
)


def _run_configuration_payload() -> dict:
    return {
        "schema_version": "1.0.0",
        "run_configuration_id": "run-config-default",
        "job_id": "job-123",
        "runner_profile_id": "runner-default",
        "runner_profile_version": "2026.06",
        "pipeline_id": "pipeline-default",
        "pipeline_version": "2026.06",
        "target_proof_level": "BUILD_TEST_VERIFIED",
        "enabled_gates": ("build", "test"),
        "policy": {},
    }


def test_valid_run_configuration() -> None:
    configuration = RunConfiguration.model_validate(_run_configuration_payload())

    assert configuration.job_id == "job-123"
    assert configuration.target_proof_level == "BUILD_TEST_VERIFIED"
    assert configuration.policy == RunPolicy()


def test_unknown_field_rejected() -> None:
    payload = _run_configuration_payload()
    payload["unexpected"] = "value"

    with pytest.raises(ValidationError):
        RunConfiguration.model_validate(payload)


def test_run_configuration_is_immutable() -> None:
    configuration = RunConfiguration.model_validate(_run_configuration_payload())

    with pytest.raises(ValidationError):
        configuration.job_id = "job-456"


def test_run_policy_is_immutable() -> None:
    configuration = RunConfiguration.model_validate(_run_configuration_payload())

    with pytest.raises(ValidationError):
        configuration.policy.enable_endpoint_gate = True


def test_run_policy_defaults_include_repair_support() -> None:
    assert RunPolicy() == RunPolicy(
        continue_after_warning=False,
        enable_runtime_gate=False,
        enable_endpoint_gate=False,
        enable_build_repair=True,
        enable_llm_repair_proposal=True,
        max_repair_attempts=3,
        repair_scope="build_only",
        stage_continuation_policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )


def test_stage_continuation_policy_defaults_to_auto_on_green() -> None:
    configuration = RunConfiguration.model_validate(_run_configuration_payload())

    assert configuration.policy.stage_continuation_policy == StageContinuationPolicy.AUTO_ON_GREEN
    assert configuration.model_dump(mode="json")["policy"]["stage_continuation_policy"] == "auto_on_green"
    assert configuration.policy.enable_build_repair is True
    assert configuration.policy.enable_llm_repair_proposal is True
    assert configuration.policy.max_repair_attempts == 3
    assert configuration.policy.repair_scope == "build_only"


def test_stage_continuation_policy_accepts_manual() -> None:
    payload = _run_configuration_payload()
    payload["policy"] = {"stage_continuation_policy": "manual"}

    configuration = RunConfiguration.model_validate(payload)

    assert configuration.policy.stage_continuation_policy == StageContinuationPolicy.MANUAL


def test_f15_manual_policy_factory_defaults_to_manual() -> None:
    assert RunPolicy.f15_manual().stage_continuation_policy == StageContinuationPolicy.MANUAL


def test_invalid_stage_continuation_policy_rejected() -> None:
    payload = _run_configuration_payload()
    payload["policy"] = {"stage_continuation_policy": "skip_stages"}

    with pytest.raises(ValidationError):
        RunConfiguration.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "profile_id"),
    [
        ("source_profile", "springboot-2.7-java11"),
        ("target_profile", "springboot-3.5-java17"),
    ],
)
def test_supported_profiles_are_accepted(field_name: str, profile_id: str) -> None:
    payload = _run_configuration_payload()
    payload[field_name] = profile_id

    configuration = RunConfiguration.model_validate(payload)

    assert getattr(configuration, field_name) == profile_id


def test_blank_profiles_are_normalized_to_none() -> None:
    payload = _run_configuration_payload()
    payload["source_profile"] = "   "
    payload["target_profile"] = ""

    configuration = RunConfiguration.model_validate(payload)

    assert configuration.source_profile is None
    assert configuration.target_profile is None


@pytest.mark.parametrize("field_name", ["source_profile", "target_profile"])
def test_unknown_profiles_are_rejected(field_name: str) -> None:
    payload = _run_configuration_payload()
    payload[field_name] = "unsupported-profile"

    with pytest.raises(ValidationError):
        RunConfiguration.model_validate(payload)


def test_strict_booleans_reject_string_values() -> None:
    payload = _run_configuration_payload()
    payload["policy"] = {"enable_runtime_gate": "true"}

    with pytest.raises(ValidationError):
        RunConfiguration.model_validate(payload)


def test_invalid_target_proof_level_rejected() -> None:
    payload = _run_configuration_payload()
    payload["target_proof_level"] = "INVALID"

    with pytest.raises(ValidationError):
        RunConfiguration.model_validate(payload)


def test_production_ready_rejected() -> None:
    payload = _run_configuration_payload()
    payload["target_proof_level"] = "PRODUCTION_READY"

    with pytest.raises(ValidationError):
        RunConfiguration.model_validate(payload)


def test_enabled_gates_collection_is_immutable() -> None:
    configuration = RunConfiguration.model_validate(_run_configuration_payload())

    with pytest.raises(AttributeError):
        configuration.enabled_gates.append("runtime")


@pytest.mark.parametrize(
    "field",
    [
        "runner_profile_id",
        "runner_profile_version",
        "pipeline_id",
        "pipeline_version",
    ],
)
def test_runner_and_pipeline_references_are_required(field: str) -> None:
    payload = _run_configuration_payload()
    payload.pop(field)

    with pytest.raises(ValidationError):
        RunConfiguration.model_validate(payload)


def test_job_id_is_required() -> None:
    payload = _run_configuration_payload()
    payload.pop("job_id")

    with pytest.raises(ValidationError):
        RunConfiguration.model_validate(payload)


# ── profile pair validation (AMF-263 / F3-T2) ──────────────────────

def test_invalid_profile_pair_reversed_rejected() -> None:
    """Reversed pair (target lower than source) must be rejected at schema level."""
    payload = _run_configuration_payload()
    payload["source_profile"] = "springboot-3.5-java21"
    payload["target_profile"] = "springboot-3.5-java17"

    with pytest.raises(ValidationError, match="invalid profile pair"):
        RunConfiguration.model_validate(payload)


def test_invalid_profile_pair_same_rejected() -> None:
    """Same source and target must be rejected."""
    payload = _run_configuration_payload()
    payload["source_profile"] = "springboot-3.5-java17"
    payload["target_profile"] = "springboot-3.5-java17"

    with pytest.raises(ValidationError, match="invalid profile pair"):
        RunConfiguration.model_validate(payload)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("springboot-2.1-java11", "springboot-2.7-java11"),
        ("springboot-2.1-java11", "springboot-3.5-java17"),
        ("springboot-2.1-java11", "springboot-3.5-java21"),
        ("springboot-2.1-java11", "springboot-4.0-java21"),
        ("springboot-2.7-java11", "springboot-3.5-java17"),
        ("springboot-2.7-java11", "springboot-3.5-java21"),
        ("springboot-2.7-java11", "springboot-4.0-java21"),
        ("springboot-3.5-java17", "springboot-3.5-java21"),
        ("springboot-3.5-java17", "springboot-4.0-java21"),
        ("springboot-3.5-java21", "springboot-4.0-java21"),
        (None, None),
    ],
)
def test_valid_profile_pairs_accepted(source: str | None, target: str | None) -> None:
    payload = _run_configuration_payload()
    if source is not None:
        payload["source_profile"] = source
    if target is not None:
        payload["target_profile"] = target

    configuration = RunConfiguration.model_validate(payload)
    assert configuration.source_profile == source
    assert configuration.target_profile == target


def test_source_not_selectable_rejected() -> None:
    """springboot-4.0-java21 is not selectable as source."""
    payload = _run_configuration_payload()
    payload["source_profile"] = "springboot-4.0-java21"
    payload["target_profile"] = "springboot-4.0-java21"

    with pytest.raises(ValidationError):
        RunConfiguration.model_validate(payload)


def test_target_not_selectable_rejected() -> None:
    """springboot-2.1-java11 is not selectable as target."""
    payload = _run_configuration_payload()
    payload["source_profile"] = "springboot-3.5-java17"
    payload["target_profile"] = "springboot-2.1-java11"

    with pytest.raises(ValidationError):
        RunConfiguration.model_validate(payload)
