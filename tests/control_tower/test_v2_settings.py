"""Tests for V2 settings, env ref projection, and redacted settings display."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.redaction import (
    contains_forbidden_path,
    redact_absolute_paths,
    redact_local_mode_path,
    redact_allowed_roots_for_display,
    env_ref_or_none,
)
from migration_factory.control_tower.application.v2_settings import (
    ControlTowerSettings,
    EnvRefStatus,
    DeploymentRoleStatus,
    AzureFoundryProjection,
    LocalModeProjection,
    SettingsProjection,
    build_settings_projection,
    settings_projection_to_dict,
    is_env_var_configured,
    is_path_allowed,
)


# ── ControlTowerSettings tests ───────────────────────────────────────


def test_settings_loads_defaults() -> None:
    """Settings should load without env vars and use defaults."""
    settings = ControlTowerSettings()
    assert settings.bind_host == "127.0.0.1"
    assert settings.bind_port == 8000
    assert settings.local_mode is True
    assert settings.azure_foundry_provider == "azure_openai"
    assert settings.azure_foundry_endpoint_env == "AZURE_OPENAI_ENDPOINT"
    assert settings.azure_foundry_api_key_env == "AZURE_OPENAI_API_KEY"
    assert settings.azure_foundry_fallback_enabled is False


def test_settings_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_TOWER_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("CONTROL_TOWER_BIND_PORT", "9000")
    monkeypatch.setenv("CONTROL_TOWER_LOCAL_MODE", "false")
    monkeypatch.setenv("CONTROL_TOWER_ALLOWED_SOURCE_ROOTS", "/home/user/apps")
    monkeypatch.setenv("CONTROL_TOWER_AZURE_FOUNDRY_API_VERSION", "2026-01-01")

    settings = ControlTowerSettings()

    assert settings.bind_host == "0.0.0.0"
    assert settings.bind_port == 9000
    assert settings.local_mode is False
    assert settings.allowed_source_roots == "/home/user/apps"
    assert settings.azure_foundry_api_version == "2026-01-01"


def test_settings_prefix_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings should only load CONTROL_TOWER_ prefixed env vars."""
    monkeypatch.setenv("CONTROL_TOWER_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("UNRELATED_VAR", "should-not-leak")
    settings = ControlTowerSettings()

    assert settings.bind_host == "127.0.0.1"
    # Trying to access unrelated attributes should fail
    with pytest.raises(AttributeError):
        _ = settings.unrelated_var  # type: ignore[attr-defined]


# ── Env ref helper tests ─────────────────────────────────────────────


def test_is_env_var_configured_returns_true_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_V2_SETTINGS_VAR", "some-value")
    assert is_env_var_configured("TEST_V2_SETTINGS_VAR") is True


def test_is_env_var_configured_returns_false_when_unset() -> None:
    assert is_env_var_configured("THIS_VAR_SHOULD_NOT_EXIST") is False


def test_is_env_var_configured_returns_false_when_none() -> None:
    assert is_env_var_configured(None) is False


def test_is_env_var_configured_returns_false_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_V2_SETTINGS_EMPTY", "")
    assert is_env_var_configured("TEST_V2_SETTINGS_EMPTY") is False


def test_is_env_var_configured_returns_false_for_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_V2_SETTINGS_WS", "   ")
    assert is_env_var_configured("TEST_V2_SETTINGS_WS") is False


# ── Settings projection tests ────────────────────────────────────────


def test_settings_projection_contains_no_secret_values() -> None:
    """The settings projection should contain env ref names, not values."""
    settings = ControlTowerSettings()
    projection = build_settings_projection(settings)

    # The projection uses env ref names, not values
    assert projection.azure.endpoint.env_ref == "AZURE_OPENAI_ENDPOINT"
    assert projection.azure.provider == "azure_openai"
    assert projection.azure.auth_mode == "api_key_or_entra"
    assert projection.azure.profile_id == "azure-foundry-v2"

    # Auth mode contains the literal string "api_key_or_entra" which is a mode
    # identifier, not an actual API key value. That's fine.
    # What matters is that no field returns key VALUEs.
    # The env_ref for the API key is the env name, never the actual key value.
    endpoint = projection.azure.endpoint
    assert endpoint.env_ref == "AZURE_OPENAI_ENDPOINT"
    assert isinstance(endpoint.configured, bool)


def test_settings_projection_api_version_flag() -> None:
    settings = ControlTowerSettings(azure_foundry_api_version="2026-01-01")
    projection = build_settings_projection(settings)

    assert projection.azure.api_version_configured is True

    settings2 = ControlTowerSettings(azure_foundry_api_version="")
    projection2 = build_settings_projection(settings2)

    assert projection2.azure.api_version_configured is False


def test_settings_projection_roles() -> None:
    settings = ControlTowerSettings()
    projection = build_settings_projection(settings)

    assert "proposer" in projection.azure.roles
    assert "reviewer" in projection.azure.roles
    assert "assistant" in projection.azure.roles
    assert "fallback" in projection.azure.roles

    assert projection.azure.roles["proposer"].deployment_label == "proposer"
    assert projection.azure.roles["fallback"].enabled is False


def test_settings_projection_fallback_disabled_by_default() -> None:
    settings = ControlTowerSettings()
    projection = build_settings_projection(settings)

    assert projection.azure.roles["fallback"].enabled is False


def test_settings_projection_fallback_enabled() -> None:
    settings = ControlTowerSettings(azure_foundry_fallback_enabled=True)
    projection = build_settings_projection(settings)

    assert projection.azure.roles["fallback"].enabled is True


def test_settings_projection_local_mode() -> None:
    settings = ControlTowerSettings()
    projection = build_settings_projection(settings)

    assert projection.local_mode.enabled is True
    assert isinstance(projection.local_mode.allowed_source_roots, tuple)
    assert isinstance(projection.local_mode.allowed_output_roots, tuple)


def test_settings_projection_to_dict_no_secret_values() -> None:
    """The public dict projection should contain only statuses and booleans."""
    settings = ControlTowerSettings()
    projection = build_settings_projection(settings)
    result = settings_projection_to_dict(projection)

    # Check top-level keys
    assert "azure" in result
    assert "local_mode" in result

    azure = result["azure"]
    assert azure["profile_id"] == "azure-foundry-v2"
    assert "provider" not in azure
    assert "endpoint" not in azure
    assert isinstance(azure["connection_configured"], bool)
    assert azure["status"] == "not_configured"
    assert "api_version_configured" in azure

    # Roles should have only product-safe status fields.
    for role_name in ("proposer", "reviewer", "assistant", "fallback"):
        role = azure["roles"][role_name]
        assert "env_ref" not in role
        assert "configured" in role
        assert isinstance(role["configured"], bool)
        assert "deployment_label" not in role
        assert "enabled" in role

    serialized = json.dumps(result)
    for forbidden in ("provider", "env_ref", "endpoint", "deployment"):
        assert forbidden not in serialized

    # Check local_mode
    local_mode = result["local_mode"]
    assert local_mode["enabled"] is True
    assert isinstance(local_mode["allowed_source_roots"], list)
    assert isinstance(local_mode["allowed_output_roots"], list)
    assert "default_ai_hub_path" in local_mode


def test_settings_projection_with_paths() -> None:
    """Allowed roots should appear as parsable strings in the dict projection."""
    settings = ControlTowerSettings(
        allowed_source_roots="C:\\work\\apps",
        allowed_output_roots="/home/user/output",
    )
    projection = build_settings_projection(settings)
    result = settings_projection_to_dict(projection)

    # Paths are included as parsed string values (redacted for display where needed)
    assert len(result["local_mode"]["allowed_source_roots"]) >= 1


# ── Redaction helper tests ───────────────────────────────────────────


def test_redact_local_mode_path_redacts_forbidden_paths() -> None:
    assert redact_local_mode_path("/etc/passwd") == "[redacted-path]"
    assert redact_local_mode_path("/proc/self/mem") == "[redacted-path]"
    assert redact_local_mode_path("C:\\Windows\\System32") == "[redacted-path]"


def test_redact_local_mode_path_redacts_env_files() -> None:
    assert redact_local_mode_path("/home/user/.env") == "[redacted-path]"
    assert redact_local_mode_path("C:\\Users\\me\\.env.local") == "[redacted-path]"


def test_redact_local_mode_path_handles_normal_paths() -> None:
    """Normal absolute paths should get the standard redaction treatment."""
    result = redact_local_mode_path("/home/user/apps/my-app")
    assert isinstance(result, str)


def test_redact_allowed_roots_for_display() -> None:
    roots = ("/home/user/apps", "/etc/passwd", "/var/log")
    redacted = redact_allowed_roots_for_display(roots)

    assert len(redacted) == 3
    assert "[redacted-path]" in redacted[1] or "[redacted-path]" in redacted[2]


def test_env_ref_or_none() -> None:
    assert env_ref_or_none("AZURE_OPENAI_ENDPOINT") == "AZURE_OPENAI_ENDPOINT"
    assert env_ref_or_none(None) == ""
    assert env_ref_or_none("") == ""


# ── Path validation tests ────────────────────────────────────────────


def test_is_path_allowed_within_roots(tmp_path: Path) -> None:
    allowed = (str(tmp_path),)
    subdir = tmp_path / "subdir"
    subdir.mkdir()

    # subdir is within tmp_path, so it should be allowed
    result = is_path_allowed(str(subdir), allowed)
    assert result is True

    # other is also within tmp_path
    result = is_path_allowed(str(tmp_path / "other"), allowed)
    assert result is True


def test_is_path_allowed_outside_roots(tmp_path: Path) -> None:
    # Create a subdir as the allowed root
    allowed_root = tmp_path / "subdir"
    allowed_root.mkdir()
    allowed = (str(allowed_root),)

    # sibling is not within allowed_root
    sibling = tmp_path / "other"
    sibling.mkdir()
    assert is_path_allowed(str(sibling), allowed) is False


# ── API integration tests ────────────────────────────────────────────


def test_ai_settings_endpoint_returns_projection(tmp_path: Path) -> None:
    """The /v1/settings/ai endpoint should return a redacted projection."""
    from fastapi.testclient import TestClient
    from migration_factory.control_tower.adapters.fastapi import create_app
    from migration_factory.control_tower.adapters.fastapi.app import EventReplayConfig
    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations

    connection = sqlite3.connect(
        tmp_path / "ctrl_test.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    apply_pending_migrations(connection)

    app = create_app(lambda: SqliteUnitOfWork(connection))
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    response = client.get("/v1/settings/ai")

    assert response.status_code == 200
    body = response.json()
    assert "azure" in body
    assert "local_mode" in body
    assert "profile_id" in body["azure"]
    assert "endpoint" not in body["azure"]
    assert body["azure"]["connection_configured"] is False
    assert body["azure"]["status"] == "not_configured"
    # Roles present
    assert "proposer" in body["azure"]["roles"]
    assert "reviewer" in body["azure"]["roles"]
    assert "assistant" in body["azure"]["roles"]
    assert "fallback" in body["azure"]["roles"]
    serialized = json.dumps(body)
    for forbidden in ("provider", "env_ref", "endpoint", "deployment"):
        assert forbidden not in serialized


def test_ai_settings_endpoint_no_secret_values(tmp_path: Path) -> None:
    """The /v1/settings/ai endpoint must never return secret values."""
    import sqlite3
    from fastapi.testclient import TestClient
    from migration_factory.control_tower.adapters.fastapi import create_app
    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations

    connection = sqlite3.connect(
        tmp_path / "ctrl_test2.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    apply_pending_migrations(connection)

    app = create_app(lambda: SqliteUnitOfWork(connection))
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    response = client.get("/v1/settings/ai")
    body_str = str(response.json())

    # Ensure no actual secret values are present
    forbidden_patterns = [
        "AZURE_OPENAI_API_KEY=",  # value assignment
        "api_key=",
        "sk-",  # common OpenAI key prefix
        "endpoint=",
        "deployment_id=",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in body_str.lower(), f"Found forbidden pattern in settings: {pattern}"

    assert "AZURE_OPENAI_ENDPOINT" not in body_str
    assert "AZURE_OPENAI_PROPOSER_DEPLOYMENT" not in body_str
