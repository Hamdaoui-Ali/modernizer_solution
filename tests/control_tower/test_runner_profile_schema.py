from __future__ import annotations

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.schemas.runner_profile import RunnerProfile


def _valid_runner_profile_payload() -> dict:
    return {
        "schema_version": "1.0.0",
        "runner_profile_id": "runner-default",
        "runner_profile_version": "2026.06",
        "display_name": "Default runner",
        "python_executable": "C:/Python313/python.exe",
        "ai_hub_path": "C:/ai-hub",
        "maven": {
            "executable_path": "C:/tools/apache-maven-3.9.9/bin/mvn.cmd",
            "expected_version": "3.9.9",
            "allow_wrapper": False,
        },
        "jdks": (
            {
                "jdk_id": "jdk-17",
                "java_home": "C:/jdks/temurin-17",
                "expected_major": 17,
                "role": "source",
            },
            {
                "jdk_id": "jdk-21",
                "java_home": "C:/jdks/temurin-21",
                "expected_major": 21,
                "role": "target",
            },
        ),
        "filesystem": {
            "roots": (
                {
                    "root_id": "source-root",
                    "kind": "source",
                    "path": "C:/workspace/source",
                },
                {
                    "root_id": "output-root",
                    "kind": "output",
                    "path": "C:/workspace/output",
                },
            )
        },
        "network": {
            "mode": "allowlisted",
            "allowed_hosts": ("repo.local",),
        },
        "ai_profile": {
            "profile_id": "azure-gpt",
        },
    }


def test_valid_minimal_runner_profile() -> None:
    profile = RunnerProfile.model_validate(_valid_runner_profile_payload())

    assert profile.runner_profile_id == "runner-default"
    assert profile.filesystem.roots[0].kind == "source"


def test_unknown_field_rejected_on_runner_profile() -> None:
    payload = _valid_runner_profile_payload()
    payload["unexpected"] = "value"

    with pytest.raises(ValidationError):
        RunnerProfile.model_validate(payload)


def test_unknown_nested_field_rejected() -> None:
    payload = _valid_runner_profile_payload()
    payload["network"]["unexpected"] = "value"

    with pytest.raises(ValidationError):
        RunnerProfile.model_validate(payload)


def test_runner_profile_is_immutable() -> None:
    profile = RunnerProfile.model_validate(_valid_runner_profile_payload())

    with pytest.raises(ValidationError):
        profile.display_name = "Changed"


def test_nested_models_are_immutable() -> None:
    profile = RunnerProfile.model_validate(_valid_runner_profile_payload())

    with pytest.raises(ValidationError):
        profile.network.mode = "offline"

    with pytest.raises(ValidationError):
        profile.jdks[0].expected_major = 21


def test_duplicate_filesystem_root_ids_are_rejected() -> None:
    payload = _valid_runner_profile_payload()
    payload["filesystem"] = {
        "roots": (
            payload["filesystem"]["roots"][0],
            {
                "root_id": "source-root",
                "kind": "output",
                "path": "C:/workspace/output-2",
            },
        )
    }

    with pytest.raises(ValidationError):
        RunnerProfile.model_validate(payload)


def test_duplicate_jdk_ids_are_rejected() -> None:
    payload = _valid_runner_profile_payload()
    payload["jdks"] = (
        payload["jdks"][0],
        {
            "jdk_id": "jdk-17",
            "java_home": "C:/jdks/temurin-21",
            "expected_major": 21,
            "role": "target",
        },
    )

    with pytest.raises(ValidationError):
        RunnerProfile.model_validate(payload)


def test_jdk_major_version_rejects_string_input() -> None:
    payload = _valid_runner_profile_payload()
    payload["jdks"] = (
        {
            "jdk_id": "jdk-17",
            "java_home": "C:/jdks/temurin-17",
            "expected_major": "17",
            "role": "source",
        },
    )

    with pytest.raises(ValidationError):
        RunnerProfile.model_validate(payload)


def test_network_policy_rejects_unknown_fields() -> None:
    payload = _valid_runner_profile_payload()
    payload["network"]["dns_check"] = True

    with pytest.raises(ValidationError):
        RunnerProfile.model_validate(payload)


def test_network_policy_rejects_string_boolean() -> None:
    payload = _valid_runner_profile_payload()
    payload["network"]["mode"] = "true"

    with pytest.raises(ValidationError):
        RunnerProfile.model_validate(payload)


def test_tuple_collections_cannot_be_mutated_like_lists() -> None:
    profile = RunnerProfile.model_validate(_valid_runner_profile_payload())

    with pytest.raises(AttributeError):
        profile.filesystem.roots.append("invalid")
