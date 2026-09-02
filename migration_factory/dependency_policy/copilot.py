from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from migration_factory.contracts import SCHEMA_VERSION
from migration_factory.dependency_policy.models import PolicyReport

DependencyCopilotInvoker = Callable[[dict[str, Any]], dict[str, Any]]


def build_dependency_copilot_request(
    *,
    run_dir: str | Path,
    sandbox_path: str | Path,
    target_plan: dict[str, Any],
    policy_report: PolicyReport,
    dependency_tree_excerpt: str = "",
    openrewrite_summary: dict[str, Any] | None = None,
    build_test_error: str = "",
) -> dict[str, Any]:
    pom_text = _read_text(Path(sandbox_path) / "pom.xml")
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "dependency_policy_advisory",
        "target_dependency_plan": target_plan,
        "pom_xml": pom_text,
        "dependency_policy_report": policy_report.to_dict(),
        "dependency_tree_excerpt": dependency_tree_excerpt[:12000],
        "openrewrite_summary": openrewrite_summary or {},
        "build_test_error": build_test_error,
        "guardrails": {
            "proposal_only": True,
            "no_auto_apply": True,
            "auto_apply_copilot_allowed": False,
            "sandbox_only": True,
            "no_runtime_h2": True,
            "no_sql_server": True,
            "no_endpoint_smoke": True,
            "no_deployment": True,
            "no_pr_creation": True,
        },
        "required_response_schema": {
            "schema_version": "string",
            "summary": "string",
            "dependency_findings": "array",
            "proposed_changes": "array",
            "risks": "array or string",
            "confidence": "string",
            "limitations": "array",
            "no_auto_apply_ack": True,
        },
    }


def invoke_dependency_copilot_advisory(
    *,
    run_dir: str | Path,
    sandbox_path: str | Path,
    target_plan: dict[str, Any],
    policy_report: PolicyReport,
    invoker: DependencyCopilotInvoker | None = None,
    dependency_tree_excerpt: str = "",
    openrewrite_summary: dict[str, Any] | None = None,
    build_test_error: str = "",
) -> dict[str, Any]:
    run_path = Path(run_dir)
    assessment_dir = run_path / "assessment"
    assessment_dir.mkdir(parents=True, exist_ok=True)
    request = build_dependency_copilot_request(
        run_dir=run_path,
        sandbox_path=sandbox_path,
        target_plan=target_plan,
        policy_report=policy_report,
        dependency_tree_excerpt=dependency_tree_excerpt,
        openrewrite_summary=openrewrite_summary,
        build_test_error=build_test_error,
    )
    request_path = assessment_dir / "dependency_copilot_request.json"
    response_path = assessment_dir / "dependency_copilot_response.json"
    plan_path = assessment_dir / "dependency_repair_plan.md"
    request_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if invoker is None:
        payload = fallback_dependency_copilot_response(
            reason="No dependency Copilot invoker configured; deterministic fallback written.",
            policy_report=policy_report,
        )
        status = "FALLBACK"
    else:
        raw = invoker(request)
        valid, errors = validate_dependency_copilot_response(raw)
        payload = raw if valid else fallback_dependency_copilot_response(
            reason="Invalid dependency Copilot response: " + "; ".join(errors),
            policy_report=policy_report,
            raw=raw,
        )
        status = "USED" if valid else "FALLBACK"
    response_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    plan_path.write_text(_repair_plan_markdown(payload), encoding="utf-8")
    return {
        "status": status,
        "artifact_refs": {
            "dependency_copilot_request": str(request_path),
            "dependency_copilot_response": str(response_path),
            "dependency_repair_plan": str(plan_path),
        },
        "response": payload,
    }


def validate_dependency_copilot_response(payload: Any) -> tuple[bool, list[str]]:
    if not isinstance(payload, dict):
        return False, ["response must be a JSON object"]
    required = {
        "schema_version",
        "summary",
        "dependency_findings",
        "proposed_changes",
        "risks",
        "confidence",
        "limitations",
        "no_auto_apply_ack",
    }
    missing = sorted(required - set(payload))
    errors = [f"missing required fields: {', '.join(missing)}"] if missing else []
    if payload.get("no_auto_apply_ack") is not True:
        errors.append("no_auto_apply_ack must be true")
    if not isinstance(payload.get("dependency_findings"), list):
        errors.append("dependency_findings must be an array")
    if not isinstance(payload.get("proposed_changes"), list):
        errors.append("proposed_changes must be an array")
    for index, change in enumerate(payload.get("proposed_changes") if isinstance(payload.get("proposed_changes"), list) else []):
        if not isinstance(change, dict):
            errors.append(f"proposed_changes[{index}] must be an object")
            continue
        if change.get("safe_to_auto_apply") is not False:
            errors.append(f"proposed_changes[{index}].safe_to_auto_apply must be false")
    return not errors, errors


def fallback_dependency_copilot_response(
    *,
    reason: str,
    policy_report: PolicyReport,
    raw: Any = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": "Dependency policy risks require human review; no Copilot patch was applied.",
        "dependency_findings": [
            {
                "rule_id": risk.rule_id,
                "severity": risk.severity,
                "evidence": risk.evidence,
                "suggested_fix": risk.suggested_fix,
            }
            for risk in policy_report.risks
            if risk.severity in {"WARNING", "ERROR", "BLOCKER"}
        ],
        "proposed_changes": [],
        "risks": [reason],
        "confidence": "LOW",
        "limitations": [
            "Generated deterministically because Copilot advisory was unavailable or invalid.",
            "No source, POM, runtime, SQL Server, endpoint, deployment, or PR changes were made.",
        ],
        "no_auto_apply_ack": True,
        "raw_response": raw if raw is not None else {},
    }


def _repair_plan_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Dependency Repair Proposal",
        "",
        str(payload.get("summary", "")),
        "",
        "- Proposal only: true",
        "- Auto-apply allowed: false",
        f"- Confidence: {payload.get('confidence', '')}",
    ]
    changes = payload.get("proposed_changes")
    if isinstance(changes, list) and changes:
        lines.extend(["", "## Proposed Changes", ""])
        for change in changes:
            if isinstance(change, dict):
                lines.append(f"- {change.get('rule_id', '')}: {change.get('reason', '')}")
    return "\n".join(lines) + "\n"


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")
