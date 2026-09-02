"""Focused tests for V1-00D: Define redaction and forbidden-path baseline."""

from __future__ import annotations

import pytest

from migration_factory.control_tower.application.redaction import (
    FORBIDDEN_FILE_EXTENSIONS,
    FORBIDDEN_PATH_PREFIXES,
    SENSITIVE_ENV_VARS,
    contains_forbidden_path,
    is_forbidden_file,
    is_sensitive_env_var,
    redact_absolute_paths,
    redact_audit_payload,
    redact_deployment_identifiers,
    redact_env_assignments,
    redact_model_summary,
    redact_public_value,
    redact_raw_prompts,
    redact_secret_keys,
    redact_sensitive_env_vars,
    validate_not_forbidden,
)


# ── Absolute path redaction tests ────────────────────────────────────


class TestRedactAbsolutePaths:
    def test_redacts_posix_absolute_path(self) -> None:
        result = redact_absolute_paths("Found at /home/user/.ssh/id_rsa")
        assert "/home/user/.ssh/id_rsa" not in result
        assert "[redacted-path]" in result or "[redacted-home-path]" in result

    def test_redacts_windows_absolute_path(self) -> None:
        result = redact_absolute_paths("Config at C:\\Users\\admin\\.env")
        assert "[redacted-windows-path]" in result
        assert "C:\\Users\\admin" not in result

    def test_redacts_windows_forward_slash_path(self) -> None:
        result = redact_absolute_paths("Log at D:/data/out/file.txt")
        assert "[redacted-windows-path]" in result

    def test_preserves_url(self) -> None:
        result = redact_absolute_paths("See https://example.com/api/v1/models")
        assert "https://example.com/api/v1/models" in result

    def test_redacts_home_dir(self) -> None:
        result = redact_absolute_paths("Path /home/user/secrets")
        assert "[redacted-home-path]" in result or "[redacted-path]" in result

    def test_no_change_for_clean_text(self) -> None:
        result = redact_absolute_paths("Processing migration job abc-123")
        assert result == "Processing migration job abc-123"


# ── Environment variable redaction tests ─────────────────────────────


class TestRedactEnvAssignments:
    def test_redacts_env_assignment(self) -> None:
        result = redact_env_assignments("MY_SECRET=supersecret")
        assert "[redacted-env]" in result

    def test_redacts_uppercase_env(self) -> None:
        result = redact_env_assignments("AZURE_KEY=abc123")
        assert "[redacted-env]" in result


class TestRedactSensitiveEnvVars:
    def test_redacts_azure_key(self) -> None:
        result = redact_sensitive_env_vars("AZURE_OPENAI_KEY=sk-abc123")
        assert "[redacted-sensitive-env]" in result

    def test_redacts_openai_api_key(self) -> None:
        result = redact_sensitive_env_vars("OPENAI_API_KEY=sk-proj-xyz")
        assert "[redacted-sensitive-env]" in result

    def test_no_change_for_innocent_var(self) -> None:
        result = redact_sensitive_env_vars("MY_VAR=hello")
        assert result == "MY_VAR=hello"


# ── Secret key redaction tests ───────────────────────────────────────


class TestRedactSecretKeys:
    def test_redacts_secret_keyword(self) -> None:
        result = redact_secret_keys("The secret is safe")
        assert "redacted" in result
        assert "secret" not in result.lower() or "redacted" in result

    def test_redacts_token_keyword(self) -> None:
        result = redact_secret_keys("Token=abc123")
        assert "redacted" in result

    def test_redacts_password_keyword(self) -> None:
        result = redact_secret_keys("Password=abc123")
        assert "redacted" in result


# ── Deployment identifier redaction tests ────────────────────────────


class TestRedactDeploymentIdentifiers:
    def test_redacts_deployment_id(self) -> None:
        result = redact_deployment_identifiers("deployment_id: dep-abc-123")
        assert "[redacted-deployment-id]" in result

    def test_redacts_model_id(self) -> None:
        result = redact_deployment_identifiers("model_id: gpt-4-0613")
        assert "[redacted-deployment-id]" in result


# ── Raw prompt redaction tests ────────────────────────────────────────


class TestRedactRawPrompts:
    def test_redacts_long_quoted_block(self) -> None:
        text = '"' * 3 + "This is a long raw prompt that should be redacted because it exceeds 50 chars and is quoted" + '"' * 3
        result = redact_raw_prompts(text)
        assert "[redacted-prompt]" in result


# ── Model summary redaction tests ────────────────────────────────────


class TestRedactModelSummary:
    def test_redacts_paths_and_secrets(self) -> None:
        summary = (
            "Analyzed file at /etc/config/secret.key "
            "with AZURE_OPENAI_KEY=sk-123 deployment_id: dep-abc"
        )
        result = redact_model_summary(summary)
        assert "/etc/config/secret.key" not in result
        assert "AZURE_OPENAI_KEY=sk-123" not in result
        assert "deployment_id: dep-abc" not in result

    def test_redacts_windows_path_in_summary(self) -> None:
        summary = "Processed C:\\Users\\admin\\.ssh\\id_rsa"
        result = redact_model_summary(summary)
        assert "C:\\Users\\admin" not in result

    def test_preserves_non_sensitive_text(self) -> None:
        summary = "Successfully analyzed 3 migration scripts"
        result = redact_model_summary(summary)
        assert result == "Successfully analyzed 3 migration scripts"


# ── Public value redaction tests ─────────────────────────────────────


class TestRedactPublicValue:
    def test_redacts_string(self) -> None:
        result = redact_public_value("Path /etc/passwd")
        assert "[redacted-path]" in result

    def test_redacts_dict_with_secret_key(self) -> None:
        data = {"api_key": "sk-abc123", "name": "test"}
        result = redact_public_value(data)
        assert result["api_key"] == "[redacted]"
        assert result["name"] == "test"

    def test_redacts_list(self) -> None:
        data = ["/etc/passwd", "/var/log/syslog"]
        result = redact_public_value(data)
        assert "[redacted-path]" in result[0]
        assert "[redacted-path]" in result[1]

    def test_redacts_nested_dict(self) -> None:
        data = {"config": {"path": "/etc/secret.key", "value": "safe"}}
        result = redact_public_value(data)
        assert "[redacted-path]" in str(result["config"]["path"])


# ── Audit payload redaction tests ────────────────────────────────────


class TestRedactAuditPayload:
    def test_redacts_sensitive_audit_fields(self) -> None:
        payload = {
            "action": "model_invocation",
            "profile_id": "default-fake",
            "model_name": "gpt-4",
            "summary": "Analyzed /etc/config/ with AZURE_KEY=abc",
            "token_count": 150,
        }
        result = redact_audit_payload(payload)
        assert "/etc/config/" not in str(result)
        assert "AZURE_KEY=abc" not in str(result)
        assert result["action"] == "model_invocation"
        assert result["token_count"] == 150


# ── Forbidden path tests ─────────────────────────────────────────────


class TestContainsForbiddenPath:
    def test_etc_prefix_is_forbidden(self) -> None:
        assert contains_forbidden_path("/etc/passwd")

    def test_var_prefix_is_forbidden(self) -> None:
        assert contains_forbidden_path("/var/log/auth.log")

    def test_proc_prefix_is_forbidden(self) -> None:
        assert contains_forbidden_path("/proc/self/environ")

    def test_pem_extension_is_forbidden(self) -> None:
        assert contains_forbidden_path("/home/user/cert.pem")

    def test_key_extension_is_forbidden(self) -> None:
        assert contains_forbidden_path("/home/user/private.key")

    def test_env_file_is_forbidden(self) -> None:
        assert contains_forbidden_path("/app/.env")

    def test_normal_project_path_is_allowed(self) -> None:
        assert not contains_forbidden_path("/home/user/project/src/main.java")

    def test_normal_output_path_is_allowed(self) -> None:
        assert not contains_forbidden_path("/tmp/output/migration-result.txt")

    def test_windows_forbidden_path(self) -> None:
        assert contains_forbidden_path(r"C:\Windows\System32\config")

    def test_windows_users_path(self) -> None:
        assert contains_forbidden_path(r"C:\Users\admin\.ssh\id_rsa")


class TestValidateNotForbidden:
    def test_raises_for_forbidden_path(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_not_forbidden("/etc/passwd")

    def test_passes_for_safe_path(self) -> None:
        validate_not_forbidden("/home/user/project/target/file.java")


class TestIsForbiddenFile:
    def test_forbidden_extension(self) -> None:
        assert is_forbidden_file("/home/user/secret.pem")

    def test_allowed_extension(self) -> None:
        assert not is_forbidden_file("/home/user/file.java")


class TestIsSensitiveEnvVar:
    def test_azure_openai_key(self) -> None:
        assert is_sensitive_env_var("AZURE_OPENAI_KEY")

    def test_case_insensitive(self) -> None:
        assert is_sensitive_env_var("azure_openai_key")

    def test_non_sensitive_var(self) -> None:
        assert not is_sensitive_env_var("MY_APP_CONFIG")


# ── Constants contracts tests ────────────────────────────────────────


class TestForbiddenPathConstants:
    def test_forbidden_path_prefixes_are_non_empty(self) -> None:
        assert len(FORBIDDEN_PATH_PREFIXES) >= 5

    def test_forbidden_file_extensions_include_pem(self) -> None:
        assert ".pem" in FORBIDDEN_FILE_EXTENSIONS

    def test_sensitive_env_vars_include_azure_key(self) -> None:
        assert "AZURE_OPENAI_KEY" in SENSITIVE_ENV_VARS
