from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migration_factory.contracts import SCHEMA_VERSION
from migration_factory.copilot_repair.response_validator import validate_copilot_repair_response
from migration_factory.maven import resolve_maven_executable


FALLBACK_STATUS = "FALLBACK_REPAIR_PLAN"


def generate_deterministic_fallback(
    *,
    run_dir: str | Path,
    run_id: str,
    failure_classification: dict[str, Any] | None = None,
    failure_classification_path: str | Path | None = None,
    h2_startup_report: dict[str, Any] | None = None,
    h2_report_path: str | Path | None = None,
    artifact_refs: dict[str, str] | None = None,
    raw_copilot_output: Any = None,
    fallback_reason: str = "COPILOT_INVALID_RESPONSE",
    auto_apply: bool = False,
    maven_cmd: str | None = None,
) -> dict[str, Any]:
    if auto_apply:
        raise ValueError("deterministic fallback repair planning requires auto_apply=false")

    run_path = Path(run_dir)
    failures_dir = run_path / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)

    refs = dict(artifact_refs or {})
    classification_path = Path(failure_classification_path) if failure_classification_path else _path_from_ref(refs, "failure_classification")
    classification = dict(failure_classification or _read_json(classification_path) or {})
    classification = _normalize_classification(classification)

    resolved_h2_path = resolve_h2_report_path(
        run_dir=run_path,
        artifact_refs=refs,
        explicit_path=h2_report_path,
    )
    h2_report = dict(h2_startup_report or _read_json(resolved_h2_path) or {})
    if resolved_h2_path is not None:
        refs["h2_startup_report"] = str(resolved_h2_path)

    response = _build_response(
        classification=classification,
        h2_report=h2_report,
        maven_cmd=maven_cmd or resolve_maven_executable(),
    )
    valid, errors = validate_copilot_repair_response(response)
    if not valid:
        raise ValueError("deterministic fallback response failed schema validation: " + "; ".join(errors))
    if response.get("patch_proposals") != []:
        raise ValueError("deterministic fallback must not include patch proposals")

    response_path = failures_dir / "deterministic_repair_response.json"
    plan_path = failures_dir / "deterministic_repair_plan.md"
    response_path.write_text(json.dumps(response, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    plan_path.write_text(_markdown_plan(response, classification, h2_report, maven_cmd or resolve_maven_executable()), encoding="utf-8")

    rejected_path = refs.get("copilot_repair_response")
    refs.update(
        {
            "deterministic_repair_response": str(response_path),
            "deterministic_repair_plan": str(plan_path),
        }
    )
    if classification_path is not None:
        refs["failure_classification"] = str(classification_path)

    return {
        "status": FALLBACK_STATUS,
        "fallback_generated": True,
        "fallback_reason": fallback_reason,
        "fallback_response": response,
        "artifact_refs": refs,
        "deterministic_fallback_response_ref": str(response_path),
        "deterministic_fallback_plan_ref": str(plan_path),
        "copilot_response_ref": rejected_path or "",
        "raw_copilot_output": raw_copilot_output,
        "state_updates": {
            "repair_loop_status": FALLBACK_STATUS,
            "repair_fallback_generated": True,
            "final_status": FALLBACK_STATUS,
            "final_proof_level": "not_verified",
            "copilot_invocation_status": "INVALID_RESPONSE",
            "failure_classification_status": "COMPLETED",
            "repair_safe_patch_applied": False,
            "repair_human_review_required": False,
            "artifact_refs": refs,
        },
    }


def resolve_h2_report_path(
    *,
    run_dir: str | Path,
    artifact_refs: dict[str, str] | None = None,
    explicit_path: str | Path | None = None,
) -> Path | None:
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    ref = (artifact_refs or {}).get("h2_startup_report", "")
    if ref:
        candidates.append(Path(ref))
    run_path = Path(run_dir)
    candidates.extend(
        [
            run_path / "runtime" / "h2_startup_report.json",
            run_path / "failures" / "h2_startup_report.json",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _build_response(*, classification: dict[str, Any], h2_report: dict[str, Any], maven_cmd: str) -> dict[str, Any]:
    failure_type = str(classification.get("failure_classification") or classification.get("failure_type") or "")
    root_cause = str(classification.get("root_cause") or "")
    evidence_text = json.dumps({"classification": classification, "h2_report": h2_report}, sort_keys=True)
    is_h2_config = (
        failure_type == "H2_STARTUP_FAILURE"
        or "H2_STARTUP_FAILED" in evidence_text
        or "cachingConfig" in evidence_text
        or "Properties.get(Object) returned null" in evidence_text
    )
    if is_h2_config:
        failure_type = "H2_STARTUP_FAILURE"
    if is_h2_config and ("Properties.get(Object) returned null" in evidence_text or "cachingConfig" in evidence_text):
        root_cause = "RUNTIME_CONFIG_MISSING_PROPERTY"
    related = list(classification.get("related_warnings", []) or [])
    if "JWTValidator" in evidence_text and "SECURITY_ENV_WARNING" not in related:
        related.append("SECURITY_ENV_WARNING")

    summary = (
        "The migrated sandbox builds and tests pass, but required H2 runtime startup failed. "
        "The direct runtime blocker appears to be missing smoke/runtime configuration for cachingConfig, "
        "where Properties.get(Object) returned null during bean initialization. "
        "JWTValidator/common config warning is related security environment evidence and must not be addressed "
        "by weakening Spring Security."
        if is_h2_config and root_cause == "RUNTIME_CONFIG_MISSING_PROPERTY"
        else "Copilot repair output was not usable, so the factory generated a deterministic proposal-only repair plan from available failure evidence."
    )

    limitations = [
        "Copilot advisory output was rejected or unavailable; this deterministic plan is based only on run artifacts.",
        "No patches were generated or applied because auto-apply is disabled.",
        "Rerun H2 smoke with the resolved Maven command after adding smoke-only configuration: " + maven_cmd,
    ]
    if related:
        limitations.append("Related warnings: " + ", ".join(str(item) for item in related))

    return {
        "schema_version": "1.0.0",
        "repair_summary": summary,
        "failure_classification": failure_type or "UNKNOWN_MIGRATION_FAILURE",
        "skills_claimed": ["deterministic-fallback-repair-planning"],
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
        "confidence": "MEDIUM" if is_h2_config else "LOW",
        "refusals": [],
        "limitations": limitations,
    }


def _markdown_plan(
    response: dict[str, Any],
    classification: dict[str, Any],
    h2_report: dict[str, Any],
    maven_cmd: str,
) -> str:
    related = list(classification.get("related_warnings", []) or [])
    if "JWTValidator" in json.dumps(h2_report, sort_keys=True) and "SECURITY_ENV_WARNING" not in related:
        related.append("SECURITY_ENV_WARNING")
    root_cause = str(classification.get("root_cause") or "")
    if "cachingConfig" in json.dumps({"classification": classification, "h2_report": h2_report}, sort_keys=True):
        root_cause = "RUNTIME_CONFIG_MISSING_PROPERTY"
    lines = [
        "# Deterministic Repair Plan",
        "",
        str(response.get("repair_summary", "")),
        "",
        f"- Failure classification: {response.get('failure_classification', '')}",
        f"- Root cause: {root_cause or 'UNKNOWN'}",
        f"- Related warnings: {', '.join(str(item) for item in related) if related else 'none'}",
        "- Auto-apply: false",
        "- Patch proposals: 0",
        "",
        "## Recommended next steps",
        "",
        "1. Inspect sandbox source for cachingConfig required property keys.",
        "2. Identify the exact key passed to Properties.get(...).",
        "3. Add smoke-only runtime values to generated H2 config under the run directory.",
        "4. Do not modify legacy source.",
        "5. Do not change production config.",
        "6. Do not weaken Spring Security/JWT/keystore.",
        "7. If common-utils/JWTValidator requires external common config, provide smoke-only safe dummy config path/value under generated H2 runtime config.",
        f"8. Rerun H2 smoke using resolved Maven command: `{maven_cmd}`.",
        "9. Keep SQL Server/prod DB/endpoints/deployment/PR out of scope.",
        "",
    ]
    return "\n".join(lines)


def _normalize_classification(classification: dict[str, Any]) -> dict[str, Any]:
    result = dict(classification)
    failure_type = str(result.get("failure_type") or result.get("failure_classification") or "")
    if failure_type:
        result["failure_type"] = failure_type
        result["failure_classification"] = failure_type
    result.setdefault("schema_version", SCHEMA_VERSION)
    return result


def _path_from_ref(refs: dict[str, str], key: str) -> Path | None:
    value = refs.get(key, "")
    return Path(value) if value else None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
