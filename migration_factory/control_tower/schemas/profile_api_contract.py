"""Safe API field contracts for profile selection endpoints (AMF-266 / F3-T5).

Defines which fields are safe to expose in public API contracts and
which fields must be excluded (provider, model, deployment, env refs,
sandbox_path, argv, env, raw commands, filesystem targets).
"""

from __future__ import annotations

from typing import Any

# ── Allowed fields in profile-related API contracts ───────────────────
# These are the ONLY fields that may appear in profile-related
# request/response payloads.

ALLOWED_PROFILE_API_FIELDS: frozenset[str] = frozenset(
    {
        "source_profile",
        "target_profile",
        "validation_status",
        "validation_reason",
        "included_stages",
        "excluded_stages",
        "skipped_stages",
        "route_steps",
        "job_id",
        "setup_id",
        "setup_checksum",
        "pipeline_id",
        "stages",
        "stage_index",
        "stage_run_id",
        "pipeline_stage",
        "input_source_kind",
        "chain_status",
        "created_at",
        "stage_continuation_policy",
        "run_configuration_id",
        "policy",
        "route_step_index",
        "runtime_profile",
        "catalog",
        "execution_jdk",
        "approval_gate_id",
        "artifact_refs",
        "evidence_refs",
    }
)

# ── Forbidden fields that must never appear in public API ─────────────
# Provider, model, deployment, env ref, sandbox_path, argv, env,
# raw command, filesystem target, and internal runtime identifiers
# are disallowed from public profile-related API contracts.

FORBIDDEN_PROFILE_API_FIELDS: frozenset[str] = frozenset(
    {
        "provider",
        "model",
        "model_id",
        "deployment",
        "endpoint",
        "env_ref",
        "sandbox_path",
        "argv",
        "env",
        "raw_command",
        "filesystem_target",
        "filesystem_root",
        "output_root",
        "report_root",
        "run_root",
        "sandbox_root",
        "ai_hub_path",
        "ai_hub_root",
        "java_home",
        "java11_home",
        "java17_home",
        "java21_home",
        "maven_cmd",
        "azure_endpoint",
        "azure_api_key",
        "api_key",
        "access_token",
    }
)

# ── Validation helpers ───────────────────────────────────────────────


def validate_profile_api_payload(payload: dict[str, Any], *, source: str = "payload") -> None:
    """Raise ValueError if *payload* contains forbidden fields.

    Strict check: any key that appears in FORBIDDEN_PROFILE_API_FIELDS
    causes rejection.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"{source} must be a JSON object")

    forbidden_found: list[str] = []
    for key in payload:
        if key in FORBIDDEN_PROFILE_API_FIELDS:
            forbidden_found.append(key)

    if forbidden_found:
        raise ValueError(
            f"{source} contains forbidden field(s): "
            f"{', '.join(sorted(forbidden_found))}"
        )


def redact_forbidden_profile_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *data* with forbidden fields replaced by [redacted].

    Does NOT modify the original dictionary. Removes forbidden keys
    entirely from the output rather than masking them in-place.
    """
    if not isinstance(data, dict):
        return data

    return {
        key: redact_forbidden_profile_fields(value) if isinstance(value, dict) else value
        for key, value in data.items()
        if key not in FORBIDDEN_PROFILE_API_FIELDS
    }
