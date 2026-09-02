from __future__ import annotations

import json
from typing import Any

from migration_factory.contracts.schema_validation import validate_against_schema
from migration_factory.repair_loop.patch_gate import (
    extract_touched_paths,
    is_unified_diff,
    security_patch_reason,
)

REQUIRED_TOP_LEVEL_FIELDS = {
    "schema_version",
    "repair_summary",
    "failure_classification",
    "skills_claimed",
    "wrapper_checklist",
    "patch_proposals",
    "security_review_required",
    "confidence",
    "refusals",
    "limitations",
}
ALLOWED_TOP_LEVEL_FIELDS = set(REQUIRED_TOP_LEVEL_FIELDS)


def parse_copilot_stdout(stdout: str) -> tuple[dict[str, Any] | None, list[str]]:
    text = stdout.strip()
    if not text:
        return None, ["Copilot stdout was empty."]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        extracted = _extract_single_json_object(text)
        if extracted is None:
            return None, [f"Copilot stdout was not valid JSON: {exc}"]
        payload = extracted
    if not isinstance(payload, dict):
        return None, ["Copilot JSON response must be an object."]
    return payload, []


def _extract_single_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    found: list[tuple[int, int, dict[str, Any]]] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        found.append((index, index + end, payload))
    maximal = [
        item
        for item in found
        if not any(other_start < item[0] and item[1] <= other_end for other_start, other_end, _ in found)
    ]
    return maximal[0][2] if len(maximal) == 1 else None


def validate_copilot_repair_response(payload: Any) -> tuple[bool, list[str]]:
    if not isinstance(payload, dict):
        return False, ["response must be a JSON object"]
    errors = _top_level_shape_errors(payload)
    errors.extend(validate_against_schema(payload, "copilot_repair_response.schema.json"))
    checklist = payload.get("wrapper_checklist")
    if isinstance(checklist, dict):
        for key, expected in {
            "legacy_source_not_modified": True,
            "sandbox_only": True,
            "no_deployment": True,
            "no_pr_creation": True,
            "no_security_weakening": True,
            "h2_only_runtime_scope": True,
            "sql_server_out_of_scope": True,
            "endpoint_smoke_out_of_scope": True,
        }.items():
            if checklist.get(key) is not expected:
                errors.append(f"wrapper_checklist.{key}: must be true")
    else:
        errors.append("wrapper_checklist: is required")
    errors.extend(_patch_proposal_errors(payload))
    return not errors, errors


def _top_level_shape_errors(payload: dict[str, Any]) -> list[str]:
    missing = sorted(REQUIRED_TOP_LEVEL_FIELDS - set(payload))
    unexpected = sorted(set(payload) - ALLOWED_TOP_LEVEL_FIELDS)
    errors: list[str] = []
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    if unexpected:
        errors.append(f"unexpected top-level fields: {', '.join(unexpected)}")
    if missing or unexpected:
        errors.append("Copilot ignored response template/schema.")
    return errors


def _patch_proposal_errors(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    proposals = payload.get("patch_proposals", [])
    if not isinstance(proposals, list):
        return ["patch_proposals: must be an array"]
    response_claims = " ".join(str(item) for item in payload.get("limitations", []) or []).lower()
    for claim in ("sql server validated", "production db validated", "endpoint validated", "endpoint smoke validated"):
        if claim in response_claims:
            errors.append(f"response claims out-of-scope validation: {claim}")
    for index, proposal in enumerate(proposals):
        prefix = f"patch_proposals[{index}]"
        if not isinstance(proposal, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        rule_id = str(proposal.get("deterministic_rule_id") or "")
        if not rule_id:
            errors.append(f"{prefix}.deterministic_rule_id: is required")
        diff = str(proposal.get("unified_diff") or "")
        if not is_unified_diff(diff):
            errors.append(f"{prefix}.unified_diff: must be unified diff text")
            continue
        paths, path_errors = extract_touched_paths(diff)
        errors.extend(f"{prefix}.unified_diff: {error}" for error in path_errors)
        for path in paths:
            normalized = path.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/") or ":" in normalized.split("/", 1)[0]:
                errors.append(f"{prefix}.unified_diff: unsafe path {path}")
            if normalized.startswith("legacy/") or "/legacy/" in normalized:
                errors.append(f"{prefix}.unified_diff: touches legacy path {path}")
            if any(part in {".git", ".migration", "target", "build", "node_modules"} for part in normalized.split("/")):
                errors.append(f"{prefix}.unified_diff: touches blocked path {path}")
        security_reason = security_patch_reason(paths, diff)
        if security_reason and not bool(proposal.get("requires_human_review", False)):
            errors.append(f"{prefix}: {security_reason}")
        proposal_claims = " ".join(
            str(value)
            for value in (
                proposal.get("description", ""),
                proposal.get("expected_validation", []),
                proposal.get("limitations", []),
            )
        ).lower()
        for claim in ("sql server validated", "production db validated", "endpoint validated", "endpoint smoke validated"):
            if claim in proposal_claims:
                errors.append(f"{prefix}: claims out-of-scope validation: {claim}")
    return errors


def failed_response_payload(*, reason: str, stdout: str = "", stderr: str = "") -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "repair_summary": "Copilot repair proposal was not accepted.",
        "failure_classification": "UNKNOWN_MIGRATION_FAILURE",
        "skills_claimed": [],
        "wrapper_checklist": {
            "legacy_source_not_modified": True,
            "sandbox_only": True,
            "no_deployment": True,
            "no_pr_creation": True,
            "no_security_weakening": True,
            "h2_only_runtime_scope": True,
            "sql_server_out_of_scope": True,
            "endpoint_smoke_out_of_scope": True,
        },
        "patch_proposals": [],
        "security_review_required": False,
        "confidence": "LOW",
        "refusals": [reason],
        "limitations": [_tail(stdout), _tail(stderr)],
        "status": "FAILED",
    }


def _tail(text: str, max_chars: int = 1000) -> str:
    clean = str(text or "")
    return clean[-max_chars:] if len(clean) > max_chars else clean
