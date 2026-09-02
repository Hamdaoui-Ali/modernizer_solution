"""Tests for V2 env block parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.application.env_parser import (
    ALLOWLISTED_ENV_KEYS,
    BLOCKED_KEY_PREFIXES,
    IGNORED_KEYS,
    EnvParseResult,
    ParsedJavaHomes,
    ParsedMigrationFlags,
    parse_env_block,
    parse_result_to_dict,
)


# ── Sample env blocks ────────────────────────────────────────────────

SAMPLE_FULL_BLOCK = """$env:PYTHONPATH = "."
$env:JAVA11_HOME = "C:\\Tools\\jdk-11"
$env:JAVA17_HOME = "C:\\Tools\\jdk-17"
$env:JAVA21_HOME = "C:\\Tools\\jdk-21"
$env:MAVEN_CMD = "C:\\Tools\\apache-maven-3.9.15\\bin\\mvn.cmd"
$AI_HUB = "C:\\Users\\me\\modernizer-solution\\modernizer-solution-ai-hub"
$legacy = "C:\\work\\apps\\legacy-service"
$outputParent = "C:\\work\\modernized"
$runName = "legacy-service-v2"
$env:AI_MIGRATION_PROOF_LEVEL = "build_test_verified"
$env:AI_MIGRATION_SKIP_ENDPOINT_SMOKE = "true"
"""

SAMPLE_BLOCK_AZURE_BLOCKED = """$env:AZURE_OPENAI_KEY = "sk-xxx"
$env:AZURE_OPENAI_ENDPOINT = "https://example.openai.azure.com"
$env:JAVA11_HOME = "C:\\Tools\\jdk-11"
$env:AZURE_FOUNDRY_PROPOSER_DEPLOYMENT = "gpt-4"
"""

SAMPLE_BLOCK_WITH_QUOTES = """$env:JAVA11_HOME = 'C:\\Tools\\jdk-11'
$env:MAVEN_CMD = "C:\\Tools\\mvn.cmd"
$runName = "my-run-name"
"""

SAMPLE_BLOCK_WITH_UNKNOWN_KEYS = """$env:JAVA11_HOME = "C:\\Tools\\jdk-11"
$env:MY_CUSTOM_VAR = "some-value"
$env:ANOTHER_UNKNOWN = "another"
$env:PYTHONPATH = "/some/path"
"""

SAMPLE_EMPTY_BLOCK = ""

SAMPLE_INVALID_SYNTAX = """$env:JAVA11_HOME = "C:\\Tools\\jdk-11"
this is not valid powershell
$env:MAVEN_CMD = "C:\\Tools\\mvn.cmd"
"""


# ── Core parse tests ────────────────────────────────────────────────


def test_parse_full_block() -> None:
    result = parse_env_block(SAMPLE_FULL_BLOCK)

    assert result.run_name == "legacy-service-v2"
    assert result.legacy_app_path == "C:\\work\\apps\\legacy-service"
    assert result.output_parent_path == "C:\\work\\modernized"
    assert result.ai_hub_path == "C:\\Users\\me\\modernizer-solution\\modernizer-solution-ai-hub"

    assert result.java_homes.java11 == "C:\\Tools\\jdk-11"
    assert result.java_homes.java17 == "C:\\Tools\\jdk-17"
    assert result.java_homes.java21 == "C:\\Tools\\jdk-21"

    assert result.maven_cmd == "C:\\Tools\\apache-maven-3.9.15\\bin\\mvn.cmd"

    assert result.migration_flags.proof_level == "build_test_verified"
    assert result.migration_flags.skip_endpoint_smoke is True

    assert "PYTHONPATH" in result.ignored_keys
    assert len(result.blocked_keys) == 0


def test_parse_full_block_to_dict() -> None:
    result = parse_env_block(SAMPLE_FULL_BLOCK)
    d = parse_result_to_dict(result)

    assert d["parsed"]["run_name"] == "legacy-service-v2"
    assert d["parsed"]["legacy_app_path"] == "C:\\work\\apps\\legacy-service"
    assert d["parsed"]["java_homes"]["java11"] == "C:\\Tools\\jdk-11"
    assert d["parsed"]["maven_cmd"] == "C:\\Tools\\apache-maven-3.9.15\\bin\\mvn.cmd"
    assert d["parsed"]["migration_flags"]["proof_level"] == "build_test_verified"
    assert d["parsed"]["migration_flags"]["skip_endpoint_smoke"] is True
    assert "PYTHONPATH" in d["ignored_keys"]
    assert isinstance(d["blocked_keys"], list)


def test_parse_blocked_azure_keys() -> None:
    result = parse_env_block(SAMPLE_BLOCK_AZURE_BLOCKED)

    # JAVA11_HOME should still be parsed
    assert result.java_homes.java11 == "C:\\Tools\\jdk-11"

    # Azure keys should be blocked (not in parsed fields)
    assert "AZURE_OPENAI_KEY" in result.blocked_keys or "AZURE_OPENAI_KEY".upper() in [k.upper() for k in result.blocked_keys]
    assert len(result.blocked_keys) >= 1

    # Blocked keys should not appear in any parsed value
    assert "sk-xxx" not in result.java_homes.java11


def test_parse_handles_different_quote_styles() -> None:
    result = parse_env_block(SAMPLE_BLOCK_WITH_QUOTES)

    assert result.java_homes.java11 == "C:\\Tools\\jdk-11"
    assert result.maven_cmd == "C:\\Tools\\mvn.cmd"
    assert result.run_name == "my-run-name"


def test_parse_ignores_unknown_keys() -> None:
    result = parse_env_block(SAMPLE_BLOCK_WITH_UNKNOWN_KEYS)

    # Known keys still parse
    assert result.java_homes.java11 == "C:\\Tools\\jdk-11"

    # Unknown keys and PYTHONPATH should be in ignored
    assert "PYTHONPATH" in result.ignored_keys
    assert "MY_CUSTOM_VAR" in [k.upper() for k in result.ignored_keys]


def test_parse_empty_block() -> None:
    result = parse_env_block(SAMPLE_EMPTY_BLOCK)

    assert result.run_name == ""
    assert result.legacy_app_path == ""
    assert result.java_homes.java11 == ""
    assert result.maven_cmd == ""
    assert result.ignored_keys == ()
    assert result.blocked_keys == ()


def test_parse_handles_noise() -> None:
    """Parser should gracefully handle non-PowerShell lines."""
    result = parse_env_block(SAMPLE_INVALID_SYNTAX)

    assert result.java_homes.java11 == "C:\\Tools\\jdk-11"
    assert result.maven_cmd == "C:\\Tools\\mvn.cmd"
    assert result.run_name == ""


def test_parse_no_execution() -> None:
    """Parser must never execute the pasted block."""
    dangerous = """$env:JAVA11_HOME = "C:\\Tools\\jdk-11"
    ; rm -rf /
    | echo "injected"
    `command injection`
    $(cat /etc/passwd)
    """
    result = parse_env_block(dangerous)

    # Only allowlisted keys are extracted; no execution happens
    assert result.java_homes.java11 == "C:\\Tools\\jdk-11"
    assert result.run_name == ""
    assert result.legacy_app_path == ""


def test_parse_flag_values() -> None:
    """Parser should validate flag values."""
    block = """$env:AI_MIGRATION_PROOF_LEVEL = "invalid_value"
    $env:AI_MIGRATION_SKIP_ENDPOINT_SMOKE = "true"
    """
    result = parse_env_block(block)

    # Invalid proof level should be empty
    assert result.migration_flags.proof_level == ""

    # Skip endpoint smoke should be parsed
    assert result.migration_flags.skip_endpoint_smoke is True


def test_parse_boolean_flags() -> None:
    """Various boolean representations should be handled."""
    block = """$env:AI_MIGRATION_SKIP_ENDPOINT_SMOKE = "true"
    """
    result = parse_env_block(block)
    assert result.migration_flags.skip_endpoint_smoke is True

    block_false = """$env:AI_MIGRATION_SKIP_ENDPOINT_SMOKE = "false"
    """
    result = parse_env_block(block_false)
    assert result.migration_flags.skip_endpoint_smoke is False

    block_1 = """$env:AI_MIGRATION_SKIP_ENDPOINT_SMOKE = "1"
    """
    result = parse_env_block(block_1)
    assert result.migration_flags.skip_endpoint_smoke is True


def test_parse_allowlist_is_exhaustive() -> None:
    """All allowlisted keys should be properly parseable."""
    env_keys = """$env:JAVA11_HOME = "/jdk11"
    $env:JAVA17_HOME = "/jdk17"
    $env:JAVA21_HOME = "/jdk21"
    $env:MAVEN_CMD = "/mvn"
    $env:AI_MIGRATION_PROOF_LEVEL = "build_test_verified"
    $env:AI_MIGRATION_SKIP_ENDPOINT_SMOKE = "true"
    """
    result = parse_env_block(env_keys)
    assert result.java_homes.java11 == "/jdk11"
    assert result.java_homes.java17 == "/jdk17"
    assert result.java_homes.java21 == "/jdk21"
    assert result.maven_cmd == "/mvn"
    assert result.migration_flags.proof_level == "build_test_verified"
    assert result.migration_flags.skip_endpoint_smoke is True

    var_keys = """$AI_HUB = "/hub"
    $legacy = "/legacy"
    $outputParent = "/output"
    $runName = "test-run"
    """
    result = parse_env_block(var_keys)
    assert result.ai_hub_path == "/hub"
    assert result.legacy_app_path == "/legacy"
    assert result.output_parent_path == "/output"
    assert result.run_name == "test-run"


def test_blocked_keys_never_have_values_in_parsed() -> None:
    """Blocked keys' values must never appear in parsed output."""
    block = """$env:AZURE_OPENAI_API_KEY = "sk-secret-key-value"
    $env:AZURE_OPENAI_DEPLOYMENT = "gpt-4-deployment"
    $env:JAVA11_HOME = "C:\\jdk11"
    """
    result = parse_env_block(block)
    dict_result = parse_result_to_dict(result)
    json_str = json.dumps(dict_result)

    # Blocked key values must not appear in the response
    assert "sk-secret-key-value" not in json_str
    assert "gpt-4-deployment" not in json_str

    # Allowlisted values still present
    assert "jdk11" in json_str


def test_parse_result_frozen() -> None:
    """EnvParseResult should be immutable."""
    result = parse_env_block(SAMPLE_FULL_BLOCK)
    with pytest.raises(AttributeError):
        result.run_name = "new-name"  # type: ignore[misc]


# ── API contract tests ──────────────────────────────────────────────


def _client_with_mutation_headers(tmp_path: Path, app=None):
    """Create a test client with mutation-ready headers for POST requests."""
    from migration_factory.control_tower.adapters.fastapi import create_app
    from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    import sqlite3
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID

    connection = sqlite3.connect(
        tmp_path / "ctrl_test_parse.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    apply_pending_migrations(connection)

    app = app or create_app(lambda: SqliteUnitOfWork(connection))
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    return client, connection


def _mutation_headers():
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID
    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def test_parse_env_endpoint_returns_projection(tmp_path: Path) -> None:
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID
    client, _ = _client_with_mutation_headers(tmp_path)

    response = client.post(
        "/v1/migration-setups/parse-env",
        json={"env_block": SAMPLE_FULL_BLOCK},
        headers=_mutation_headers(),
    )

    assert response.status_code == 200
    body = response.json()

    assert "parsed" in body
    assert "ignored_keys" in body
    assert "blocked_keys" in body

    assert body["parsed"]["run_name"] == "legacy-service-v2"
    assert body["parsed"]["java_homes"]["java11"] == "C:\\Tools\\jdk-11"
    assert body["parsed"]["migration_flags"]["skip_endpoint_smoke"] is True
    assert "PYTHONPATH" in body["ignored_keys"]


def test_parse_env_endpoint_rejects_extra_fields(tmp_path: Path) -> None:
    client, _ = _client_with_mutation_headers(tmp_path)

    response = client.post(
        "/v1/migration-setups/parse-env",
        json={
            "env_block": SAMPLE_FULL_BLOCK,
            "extra_field": "should-not-be-allowed",
        },
        headers=_mutation_headers(),
    )

    assert response.status_code == 422


def test_parse_env_endpoint_blocked_keys_in_response(tmp_path: Path) -> None:
    client, _ = _client_with_mutation_headers(tmp_path)

    response = client.post(
        "/v1/migration-setups/parse-env",
        json={"env_block": SAMPLE_BLOCK_AZURE_BLOCKED},
        headers=_mutation_headers(),
    )
    body = response.json()

    assert response.status_code == 200
    assert len(body["blocked_keys"]) >= 1
    assert any("AZURE" in key.upper() for key in body["blocked_keys"])


def test_parse_env_endpoint_empty_block(tmp_path: Path) -> None:
    client, _ = _client_with_mutation_headers(tmp_path)

    response = client.post(
        "/v1/migration-setups/parse-env",
        json={"env_block": ""},
        headers=_mutation_headers(),
    )
    body = response.json()

    assert response.status_code == 200
    assert body["parsed"]["run_name"] == ""
    assert body["parsed"]["java_homes"]["java11"] == ""
    assert body["ignored_keys"] == []
    assert body["blocked_keys"] == []


# ── Constants tests ──────────────────────────────────────────────────


def test_allowlist_includes_required_keys() -> None:
    assert "JAVA11_HOME" in ALLOWLISTED_ENV_KEYS
    assert "JAVA17_HOME" in ALLOWLISTED_ENV_KEYS
    assert "JAVA21_HOME" in ALLOWLISTED_ENV_KEYS
    assert "MAVEN_CMD" in ALLOWLISTED_ENV_KEYS


def test_blocked_prefixes_include_azure() -> None:
    prefixes_lower = [p.lower() for p in BLOCKED_KEY_PREFIXES]
    assert "azure_" in " ".join(prefixes_lower)
    assert "openai_" in " ".join(prefixes_lower)


def test_pythonpath_is_ignored() -> None:
    assert "PYTHONPATH" in IGNORED_KEYS
