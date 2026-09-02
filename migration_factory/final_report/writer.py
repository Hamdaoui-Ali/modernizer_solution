from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from migration_factory.contracts import SCHEMA_VERSION


@dataclass(frozen=True)
class FinalReportResult:
    artifact_refs: dict[str, str]
    blockers: list[str]
    warnings: list[str]


_COPILOT_STATEMENT_ENV = "AI_MIGRATION_ENABLE_COPILOT_STATEMENT"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_SANDBOX_ONLY_DISCLAIMER = (
    "This is a sandbox migration candidate only; no production promotion, no PR, no deployment."
)
_AI_TRACE_GUARDRAIL = (
    "LLM proposed or reviewed migration intent only; human approval and backend sandbox "
    "repair-loop validation are the source of truth."
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?im)^(\s*authorization\s*:\s*).+$"),
    re.compile(r"(?i)\b[A-Za-z_]*(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|API_KEY)[A-Za-z_]*\s*=\s*[^\s]+"),
    re.compile(r"(?i)(jdbc:[a-z0-9:]+://)([^/\s:@]+):([^@\s/]+)@"),
    re.compile(r"(?i)([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)


def generate_final_migration_report(state: dict[str, Any]) -> FinalReportResult:
    run_dir = Path(str(state.get("run_dir") or ""))
    artifact_refs = dict(state.get("artifact_refs", {}) or {})
    blockers: list[str] = []
    warnings: list[str] = []

    if not run_dir:
        return FinalReportResult(artifact_refs={}, blockers=["run_dir is required"], warnings=[])

    required_ref_names = (
        "approval_decision",
        "approved_plan_lock",
        "transformation_execution_plan",
        "migration_ledger",
        "orchestration_summary",
        "post_transform_test_report",
    )
    for ref_name in required_ref_names:
        ref = str(artifact_refs.get(ref_name) or "")
        if not ref:
            blockers.append(f"missing required artifact ref for final report: {ref_name}")
            continue
        if not Path(ref).is_file():
            blockers.append(f"missing required artifact file for final report: {ref_name}")

    if blockers:
        return FinalReportResult(artifact_refs={}, blockers=blockers, warnings=warnings)

    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    json_path = final_dir / "migration_report.json"
    md_path = final_dir / "migration_summary.md"

    assessment_report = _read_json(run_dir / "assessment" / "assessment_report.json", warnings)
    approval_decision = _read_json(Path(artifact_refs["approval_decision"]), warnings)
    approved_plan_lock = _read_json(Path(artifact_refs["approved_plan_lock"]), warnings)
    execution_plan = _read_yaml(Path(artifact_refs["transformation_execution_plan"]), warnings)
    test_report = _read_json(Path(artifact_refs["post_transform_test_report"]), warnings)
    orchestration_summary = _read_json(Path(artifact_refs["orchestration_summary"]), warnings)
    migration_plan = _read_yaml(run_dir / "planning" / "migration_plan.yaml", warnings)
    dependency_policy_report = _read_optional_json(
        Path(str(artifact_refs.get("dependency_policy_report") or run_dir / "assessment" / "dependency_policy_report.json")),
        warnings,
    )

    test_status = str(state.get("test_status") or "")
    totals = dict(state.get("test_totals", {}) or {})
    if isinstance(test_report, dict):
        test_status = str(test_report.get("test_status") or test_status)
        report_totals = test_report.get("totals")
        if isinstance(report_totals, dict):
            totals = dict(report_totals)

    source_stack = _object_or_empty((assessment_report or {}).get("source_stack"))
    target_stack = {
        **_object_or_empty((migration_plan or {}).get("target_stack")),
        **_object_or_empty((assessment_report or {}).get("target_stack")),
    }
    recipes = _extract_recipes(execution_plan or {})
    profile_governance = _object_or_empty((migration_plan or {}).get("profile_governance"))
    boot4_warnings = _boot4_warnings(target_stack, state, assessment_report, migration_plan)
    validation_scope = _validation_scope(state)
    repair_loop = _repair_loop_context(state, artifact_refs)
    dependency_policy = _dependency_policy_context(state, artifact_refs, dependency_policy_report)
    ai_trace = _ai_trace_context(state, artifact_refs, repair_loop, run_dir)

    pipeline_history = _pipeline_history_context(state)
    total_timing = _timing_context(pipeline_history)
    full_source = source_stack
    full_target = _completed_target_stack(pipeline_history, state)
    change_summary_data = _change_summary(full_source, full_target, pipeline_history)

    report_payload = {
        "run_id": state.get("run_id", ""),
        "source_stack": source_stack,
        "target_stack": target_stack,
        "full_migration_source_stack": full_source,
        "full_migration_target_stack": full_target,
        "pipeline_history": pipeline_history,
        "total_duration_seconds": total_timing.get("total_duration_seconds", 0),
        "timing": total_timing,
        "report_summary": change_summary_data.get("description", ""),
        "change_summary": change_summary_data,
        "risk_level": profile_governance.get("risk_level") or (migration_plan or {}).get("risk", ""),
        "strategy": profile_governance.get("strategy", ""),
        "fallback_profile": profile_governance.get("fallback_profile", ""),
        "production_allowed": profile_governance.get("production_allowed"),
        "requires_human_approval": (migration_plan or {}).get("requires_human_approval", True),
        "approval": {
            "status": state.get("approval_status", ""),
            "decision": (approval_decision or {}).get("decision", state.get("approval_decision")),
            "approved_by": state.get("approved_by", ""),
            "approval_ref": artifact_refs.get("approval_decision", ""),
        },
        "lock_status": {
            "status": "LOCKED" if isinstance(approved_plan_lock, dict) else "UNKNOWN",
            "lock_ref": artifact_refs.get("approved_plan_lock", ""),
        },
        "transform_status": state.get("transform_status", ""),
        "build_status": state.get("build_status", ""),
        "test_status": test_status,
        "test_totals": totals,
        "proof": {
            "final_proof_level": validation_scope["final_proof_level"],
            "h2_startup_required": bool(state.get("h2_startup_required", False)),
            "h2_startup_status": state.get("h2_startup_status", "H2_STARTUP_SKIPPED"),
            "runtime_security_warnings": list(state.get("runtime_security_warnings", []) or []),
        },
        "validated": validation_scope["validated"],
        "not_validated": validation_scope["not_validated"],
        "repair_loop": repair_loop,
        "ai_trace": ai_trace,
        "dependency_policy": dependency_policy,
        "target_dependency_plan_ref": artifact_refs.get("target_dependency_plan", ""),
        "dependency_policy_report_ref": artifact_refs.get("dependency_policy_report", ""),
        "dependency_policy_status": dependency_policy["status"],
        "dependency_policy_risks_count": dependency_policy["risks_count"],
        "dependency_policy_blockers_count": dependency_policy["blockers_count"],
        "copilot_dependency_advisory_status": dependency_policy["copilot_advisory_status"],
        "policy_patch_applied": dependency_policy["policy_patch_applied"],
        "unresolved_v2_dependency_risks": dependency_policy["unresolved_v2_dependency_risks"],
        "recipes": recipes,
        "executed_recipes": recipes,
        "boot4_warnings": boot4_warnings,
        "artifact_refs": {
            **artifact_refs,
            "final_migration_report": str(json_path),
            "final_migration_summary": str(md_path),
        },
        "timing": {
            "timing_report": artifact_refs.get("timing_report", ""),
            "timing_summary": artifact_refs.get("timing_summary", ""),
        },
        "warnings": [*list(state.get("warnings", []) or []), *boot4_warnings],
        "limitations": [
            "No production promotion performed.",
            "No pull request creation performed.",
            "No deployment performed.",
            "No automatic merge performed.",
            "SQL Server production behavior not validated.",
            "Production DB scripts not validated.",
            "Endpoint/business behavior not validated.",
            "Production secrets/JWT/keystore validity not validated.",
            "Deployment not validated.",
            "PR creation/merge not validated.",
        ],
        "sandbox_path": state.get("sandbox_path", ""),
        "log_paths": _collect_log_paths(state, artifact_refs, test_report, orchestration_summary),
        "created_at": _utc_now(),
    }
    if warnings:
        report_payload["warnings"] = [
            *list(report_payload.get("warnings", []) or []),
            *warnings,
        ]

    generated_artifact_refs: dict[str, str] = {
        "final_migration_report": str(json_path),
        "final_migration_summary": str(md_path),
    }
    if _copilot_statement_enabled():
        try:
            copilot_artifact_refs = _generate_copilot_advisory_statement(report_payload, final_dir)
        except Exception as exc:  # pragma: no cover - exercised through monkeypatched failure
            warning = f"copilot advisory statement generation failed: {exc}"
            warnings.append(warning)
            report_payload["warnings"] = [
                *list(report_payload.get("warnings", []) or []),
                warning,
            ]
        else:
            generated_artifact_refs.update(copilot_artifact_refs)
            report_payload["artifact_refs"] = {
                **dict(report_payload.get("artifact_refs", {}) or {}),
                **copilot_artifact_refs,
            }

    json_path.write_text(json.dumps(report_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_build_markdown_summary(report_payload), encoding="utf-8")
    return FinalReportResult(
        artifact_refs=generated_artifact_refs,
        blockers=[],
        warnings=warnings,
    )


def _build_markdown_summary(payload: dict[str, Any]) -> str:
    totals = payload.get("test_totals", {}) or {}
    target_stack = dict(payload.get("target_stack", {}) or {})
    full_source = dict(payload.get("full_migration_source_stack", {}) or {})
    full_target = dict(payload.get("full_migration_target_stack", {}) or {})
    pipeline_history = list(payload.get("pipeline_history", []) or [])
    recipes = list(payload.get("recipes", []) or [])
    total_seconds = payload.get("total_duration_seconds", 0)

    lines = [
        "# Migration Summary",
        "",
        f"- Run ID: {payload.get('run_id', '')}",
        f"- Full Source: {_stack_path_text(full_source)}",
        f"- Full Target: {_stack_path_text(full_target)}",
        f"- Target Java: {target_stack.get('java', '')}",
        f"- Target Spring Boot: {target_stack.get('spring_boot', '')}",
        f"- Target Spring Framework: {target_stack.get('spring_framework', '')}",
        f"- Risk Level: {payload.get('risk_level', '')}",
        f"- Strategy: {payload.get('strategy', '')}",
        f"- Fallback Profile: {payload.get('fallback_profile', '')}",
        f"- Production Allowed: {str(payload.get('production_allowed')).lower()}",
        f"- Approval: {payload.get('approval', {}).get('decision', '')}",
        f"- Transform: {payload.get('transform_status', '')}",
        f"- Build: {payload.get('build_status', '')}",
        f"- Test: {payload.get('test_status', '')}",
        f"- Proof Level: {dict(payload.get('proof', {}) or {}).get('final_proof_level', 'not_verified')}",
        f"- Repair Loop: {dict(payload.get('repair_loop', {}) or {}).get('final_status', 'DISABLED')}",
        f"- Dependency Policy: {dict(payload.get('dependency_policy', {}) or {}).get('status', 'NOT_RUN')}",
        (
            "- Test Totals: "
            f"tests={totals.get('tests', 0)} "
            f"passed={totals.get('passed', 0)} "
            f"failures={totals.get('failures', 0)} "
            f"errors={totals.get('errors', 0)} "
            f"skipped={totals.get('skipped', 0)}"
        ),
        f"- Executed Recipes: {', '.join(str(recipe) for recipe in recipes) if recipes else 'none'}",
        "- Scope Limits: no production promotion, no PR creation, no deployment, no automatic merge",
        "",
    ]

    if pipeline_history:
        lines.extend([
            "## Stage-by-Stage Journey",
            "",
        ])
        for stage in pipeline_history:
            if not isinstance(stage, dict):
                continue
            idx = stage.get("stage_index", "")
            status = stage.get("status", "")
            profile = stage.get("profile", "")
            duration = stage.get("duration_seconds")
            duration_text = f" ({duration}s)" if isinstance(duration, (int, float)) else ""
            label = _stage_label(int(idx)) if isinstance(idx, (int, str)) and str(idx).isdigit() else f"Stage {idx}"
            lines.append(f"- **{label}**: {status}{duration_text}")
            if profile:
                lines.append(f"  - Profile: {profile}")
        lines.append("")

    lines.extend([
        "## Validated",
        "",
        *[f"- {item}" for item in list(payload.get("validated", []) or [])],
        "",
        "## Not Validated",
        "",
        *[f"- {item}" for item in list(payload.get("not_validated", []) or [])],
        "",
        "POC-ready sandbox migration artifacts are captured under this run directory.",
    ])
    boot4_warnings = list(payload.get("boot4_warnings", []) or [])
    if boot4_warnings:
        lines.extend(["", "## Boot 4 Warnings", ""])
        lines.extend(f"- {warning}" for warning in boot4_warnings)
    repair_loop = dict(payload.get("repair_loop", {}) or {})
    if repair_loop:
        lines.extend(
            [
                "",
                "## Repair Loop",
                "",
                f"- Enabled: {str(repair_loop.get('enabled', False)).lower()}",
                f"- Max Attempts: {repair_loop.get('max_attempts', 3)}",
                f"- Attempts: {repair_loop.get('attempts_count', 0)}",
                f"- Final Status: {repair_loop.get('final_status', '')}",
                f"- Ledger: {repair_loop.get('ledger_ref', '')}",
                f"- Copilot Used: {str(repair_loop.get('copilot_used', False)).lower()}",
                f"- Safe Patch Applied: {str(repair_loop.get('safe_patch_applied', False)).lower()}",
                f"- Human Review Required: {str(repair_loop.get('human_review_required', False)).lower()}",
            ]
        )
    ai_trace = list(payload.get("ai_trace", []) or [])
    if ai_trace:
        lines.extend(["", "## AI Trace", "", _AI_TRACE_GUARDRAIL, ""])
        for index, item in enumerate(ai_trace, start=1):
            row = dict(item or {})
            lines.extend(
                [
                    f"- Trace {index}: event={row.get('event', '')}; agent={row.get('agent', '')}",
                    f"  - Evidence: {', '.join(str(ref) for ref in list(row.get('evidence_refs', []) or [])) or 'not_captured'}",
                    f"  - Context Pack: {row.get('context_pack_checksum', '') or 'not_captured'}",
                    f"  - Diagnosis: {row.get('diagnosis', '') or 'not_captured'}",
                    f"  - Proposal: {row.get('proposal_ref', '') or 'not_captured'} ({row.get('proposal_checksum', '') or 'checksum not captured'})",
                    f"  - Reviewer Verdict: {row.get('reviewer_verdict', '') or 'not_captured'}",
                    f"  - Human Decision: {row.get('human_decision', '') or 'not_captured'}",
                    f"  - Validation Result: {row.get('validation_result', '') or 'not_captured'}",
                    f"  - Ledger: {row.get('ledger_ref', '') or 'not_captured'}",
                ]
            )
    dependency_policy = dict(payload.get("dependency_policy", {}) or {})
    if dependency_policy:
        lines.extend(
            [
                "",
                "## Dependency Policy",
                "",
                f"- Status: {dependency_policy.get('status', '')}",
                f"- Risks: {dependency_policy.get('risks_count', 0)}",
                f"- Blockers: {dependency_policy.get('blockers_count', 0)}",
                f"- Copilot Advisory: {dependency_policy.get('copilot_advisory_status', 'SKIPPED')}",
                f"- Policy Patch Applied: {str(dependency_policy.get('policy_patch_applied', False)).lower()}",
                f"- Report: {dependency_policy.get('report_ref', '')}",
            ]
        )
    statement = payload.get("copilot_advisory_statement")
    if isinstance(statement, dict):
        artifact_refs = statement.get("artifact_refs", {})
        json_ref = artifact_refs.get("json", "") if isinstance(artifact_refs, dict) else ""
        md_ref = artifact_refs.get("markdown", "") if isinstance(artifact_refs, dict) else ""
        lines.extend(
            [
                "",
                "## Copilot Advisory Statement",
                "",
                _SANDBOX_ONLY_DISCLAIMER,
                "",
                f"- JSON: {json_ref}",
                f"- Markdown: {md_ref}",
            ]
        )
    return "\n".join(lines) + "\n"


def _copilot_statement_enabled() -> bool:
    return False


def _validation_scope(state: dict[str, Any]) -> dict[str, Any]:
    validated: list[str] = []
    if state.get("transform_status") == "TRANSFORM_APPLIED_IN_SANDBOX":
        validated.append("sandbox transform applied")
    if state.get("build_status") == "BUILD_PASSED_IN_SANDBOX":
        validated.append("Maven build passed")
    if state.get("test_status") == "TEST_PASSED":
        validated.append("tests passed")
    h2_status = str(state.get("h2_startup_status") or "H2_STARTUP_SKIPPED")
    proof = "unit_tests_passed" if state.get("test_status") == "TEST_PASSED" else "compiled" if state.get("build_status") == "BUILD_PASSED_IN_SANDBOX" else "not_verified"
    if h2_status in {"H2_STARTUP_PASSED", "H2_STARTUP_WARNING"}:
        validated.append("H2 migration-smoke startup passed")
        proof = "h2_runtime_started"
    return {
        "final_proof_level": state.get("final_proof_level") or proof,
        "validated": validated,
        "not_validated": [
            "SQL Server production behavior",
            "production DB scripts",
            "endpoint/business behavior",
            "production secrets/JWT/keystore validity",
            "deployment",
            "PR creation/merge",
        ],
    }


def _repair_loop_context(state: dict[str, Any], artifact_refs: dict[str, str]) -> dict[str, Any]:
    return {
        "enabled": bool(state.get("repair_loop_enabled", False)),
        "max_attempts": int(state.get("repair_max_attempts") or 3),
        "ledger_ref": artifact_refs.get("repair_ledger", ""),
        "attempts_count": int(state.get("repair_attempts_count") or 0),
        "final_status": state.get("repair_loop_status", "DISABLED"),
        "copilot_used": state.get("copilot_invocation_status") == "USED",
        "copilot_unavailable": state.get("repair_loop_status") == "COPILOT_UNAVAILABLE",
        "invalid_copilot_response": state.get("repair_loop_status") == "INVALID_COPILOT_RESPONSE",
        "safe_patch_applied": bool(state.get("repair_safe_patch_applied", False)),
        "human_review_required": bool(state.get("repair_human_review_required", False)),
        "validation_after_repair": {
            "build": state.get("build_status", ""),
            "tests": state.get("test_status", ""),
            "h2": state.get("h2_startup_status", "H2_STARTUP_SKIPPED"),
        },
    }


def _ai_trace_context(
    state: dict[str, Any],
    artifact_refs: dict[str, str],
    repair_loop: dict[str, Any],
    run_dir: Path,
) -> list[dict[str, Any]]:
    raw_records = state.get("ai_trace")
    if raw_records is None:
        raw_records = state.get("ai_trace_records")
    if raw_records is None:
        raw_records = _read_ai_trace_artifact(artifact_refs)
    if not isinstance(raw_records, list):
        return []
    return [
        normalized
        for record in raw_records
        if isinstance(record, dict)
        for normalized in [_normalize_ai_trace_record(record, artifact_refs, repair_loop, run_dir)]
        if _ai_trace_has_real_record(normalized)
    ]


def _read_ai_trace_artifact(artifact_refs: dict[str, str]) -> Any:
    for key in ("ai_trace", "ai_trace_records", "final_report_ai_trace"):
        ref = str(artifact_refs.get(key) or "")
        if not ref:
            continue
        path = Path(ref)
        if path.is_file():
            return _read_json(path, [])
    return None


def _normalize_ai_trace_record(
    record: dict[str, Any],
    artifact_refs: dict[str, str],
    repair_loop: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    proposal_ref = _first_text(
        record.get("proposal_ref"),
        record.get("repair_proposal_id"),
        record.get("proposal_id"),
    )
    ledger_ref = _first_text(record.get("ledger_ref"), repair_loop.get("ledger_ref"), artifact_refs.get("repair_ledger"))
    validation_result = _first_text(
        record.get("validation_result"),
        record.get("validation_status"),
        _validation_result_from_repair_loop(repair_loop),
    )
    normalized = {
        "event": _first_text(record.get("event"), record.get("event_type")),
        "agent": _first_text(record.get("agent"), record.get("agent_name"), record.get("model_invocation_id")),
        "evidence_refs": [_safe_report_value(item, run_dir) for item in _list(record.get("evidence_refs"))],
        "context_pack_checksum": _first_text(record.get("context_pack_checksum")),
        "diagnosis": _first_text(record.get("diagnosis"), record.get("diagnosis_id"), record.get("failure_type")),
        "proposal_ref": proposal_ref,
        "proposal_checksum": _first_text(record.get("proposal_checksum")),
        "reviewer_verdict": _first_text(record.get("reviewer_verdict"), record.get("reviewer_decision"), record.get("decision")),
        "human_decision": _first_text(record.get("human_decision"), record.get("approval_decision")),
        "validation_result": validation_result,
        "ledger_ref": ledger_ref,
    }
    return {key: _safe_report_value(value, run_dir) for key, value in normalized.items()}


def _validation_result_from_repair_loop(repair_loop: dict[str, Any]) -> str:
    final_status = str(repair_loop.get("final_status") or "")
    validation = repair_loop.get("validation_after_repair")
    if isinstance(validation, dict) and any(validation.values()):
        return ", ".join(f"{key}={value}" for key, value in sorted(validation.items()) if value)
    return final_status


def _ai_trace_has_real_record(record: dict[str, Any]) -> bool:
    return any(
        record.get(key)
        for key in (
            "context_pack_checksum",
            "diagnosis",
            "proposal_ref",
            "proposal_checksum",
            "reviewer_verdict",
            "human_decision",
            "validation_result",
            "ledger_ref",
        )
    )


def _dependency_policy_context(
    state: dict[str, Any],
    artifact_refs: dict[str, str],
    report: dict[str, Any] | None,
) -> dict[str, Any]:
    report = report or {}
    risks = list(report.get("risks", []) or [])
    unresolved_runtime = [
        risk
        for risk in risks
        if isinstance(risk, dict)
        and risk.get("blocks_v2_runtime") is True
        and risk.get("severity") in {"WARNING", "ERROR", "BLOCKER"}
    ]
    return {
        "target_plan_ref": artifact_refs.get("target_dependency_plan", ""),
        "report_ref": artifact_refs.get("dependency_policy_report", ""),
        "summary_ref": artifact_refs.get("dependency_policy_summary", ""),
        "status": state.get("dependency_policy_status") or report.get("status") or "NOT_RUN",
        "risks_count": int(state.get("dependency_policy_risks_count") or len(risks)),
        "blockers_count": int(
            state.get("dependency_policy_blockers_count")
            or len([risk for risk in risks if isinstance(risk, dict) and risk.get("blocks_v1_build_test")])
        ),
        "copilot_advisory_status": state.get("copilot_dependency_advisory_status", "SKIPPED"),
        "copilot_request_ref": artifact_refs.get("dependency_copilot_request", ""),
        "copilot_response_ref": artifact_refs.get("dependency_copilot_response", ""),
        "repair_plan_ref": artifact_refs.get("dependency_repair_plan", ""),
        "policy_patch_applied": bool(state.get("policy_patch_applied", False)),
        "unresolved_v2_dependency_risks": unresolved_runtime,
    }


def _generate_copilot_advisory_statement(payload: dict[str, Any], final_dir: Path) -> dict[str, str]:
    json_path = final_dir / "copilot_migration_statement.json"
    md_path = final_dir / "copilot_migration_statement.md"
    statement_payload = _build_copilot_statement_payload(payload, json_path, md_path)
    json_path.write_text(json.dumps(statement_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_build_copilot_statement_markdown(statement_payload), encoding="utf-8")
    payload["copilot_advisory_statement"] = {
        "status": "USED",
        "provider": statement_payload["provider"],
        "adapter": statement_payload["adapter"],
        "disclaimer": statement_payload["disclaimer"],
        "artifact_refs": {
            "json": str(json_path),
            "markdown": str(md_path),
        },
    }
    return {
        "copilot_migration_statement_json": str(json_path),
        "copilot_migration_statement_md": str(md_path),
    }


def _build_copilot_statement_payload(payload: dict[str, Any], json_path: Path, md_path: Path) -> dict[str, Any]:
    artifact_refs = dict(payload.get("artifact_refs", {}) or {})
    approval = dict(payload.get("approval", {}) or {})
    timing = dict(payload.get("timing", {}) or {})
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": payload.get("run_id", ""),
        "provider": "github_copilot",
        "adapter": "local_template_stub",
        "status": "USED",
        "advisory_only": True,
        "can_approve": False,
        "can_transform": False,
        "can_change_gates": False,
        "can_mutate_source": False,
        "can_override_status": False,
        "disclaimer": _SANDBOX_ONLY_DISCLAIMER,
        "facts": {
            "approval_decision": approval.get("decision", ""),
            "approval_status": approval.get("status", ""),
            "approved_plan_lock": artifact_refs.get("approved_plan_lock", ""),
            "transform_status": payload.get("transform_status", ""),
            "build_status": payload.get("build_status", ""),
            "test_status": payload.get("test_status", ""),
            "test_totals": dict(payload.get("test_totals", {}) or {}),
            "target_versions": dict(payload.get("target_stack", {}) or {}),
            "warnings": list(payload.get("warnings", []) or []),
            "timing": {
                **timing,
                "timing_report": artifact_refs.get("timing_report", timing.get("timing_report", "")),
                "timing_summary": artifact_refs.get("timing_summary", timing.get("timing_summary", "")),
            },
            "limitations": list(payload.get("limitations", []) or []),
            "sandbox_path": payload.get("sandbox_path", ""),
        },
        "statement": _statement_text(payload),
        "artifact_refs": {
            "self": str(json_path),
            "markdown": str(md_path),
        },
        "created_at": _utc_now(),
    }


def _statement_text(payload: dict[str, Any]) -> str:
    approval = dict(payload.get("approval", {}) or {})
    totals = dict(payload.get("test_totals", {}) or {})
    target_stack = dict(payload.get("target_stack", {}) or {})
    target_versions = ", ".join(f"{key}={value}" for key, value in sorted(target_stack.items())) or "not recorded"
    return (
        "GitHub Copilot advisory review is based only on deterministic final report facts. "
        f"Approval decision is {approval.get('decision', '')}; "
        f"transform status is {payload.get('transform_status', '')}; "
        f"build status is {payload.get('build_status', '')}; "
        f"test status is {payload.get('test_status', '')} with "
        f"{totals.get('tests', 0)} tests, {totals.get('passed', 0)} passed, "
        f"{totals.get('failures', 0)} failures, {totals.get('errors', 0)} errors, "
        f"and {totals.get('skipped', 0)} skipped. "
        f"Target versions: {target_versions}. "
        f"Sandbox path: {payload.get('sandbox_path', '')}. "
        f"{_SANDBOX_ONLY_DISCLAIMER}"
    )


def _build_copilot_statement_markdown(payload: dict[str, Any]) -> str:
    facts = dict(payload.get("facts", {}) or {})
    totals = dict(facts.get("test_totals", {}) or {})
    lines = [
        "# Copilot Advisory Statement",
        "",
        str(payload.get("disclaimer", _SANDBOX_ONLY_DISCLAIMER)),
        "",
        "## Guardrails",
        "",
        f"- advisory_only: {str(payload.get('advisory_only')).lower()}",
        f"- can_approve: {str(payload.get('can_approve')).lower()}",
        f"- can_transform: {str(payload.get('can_transform')).lower()}",
        f"- can_change_gates: {str(payload.get('can_change_gates')).lower()}",
        f"- can_mutate_source: {str(payload.get('can_mutate_source')).lower()}",
        f"- can_override_status: {str(payload.get('can_override_status')).lower()}",
        "",
        "## Deterministic Facts",
        "",
        f"- Approval decision: {facts.get('approval_decision', '')}",
        f"- Approved plan lock: {facts.get('approved_plan_lock', '')}",
        f"- Transform status: {facts.get('transform_status', '')}",
        f"- Build status: {facts.get('build_status', '')}",
        f"- Test status: {facts.get('test_status', '')}",
        (
            "- Test totals: "
            f"tests={totals.get('tests', 0)} "
            f"passed={totals.get('passed', 0)} "
            f"failures={totals.get('failures', 0)} "
            f"errors={totals.get('errors', 0)} "
            f"skipped={totals.get('skipped', 0)}"
        ),
        f"- Target versions: {json.dumps(facts.get('target_versions', {}), sort_keys=True)}",
        f"- Timing summary: {dict(facts.get('timing', {}) or {}).get('timing_summary', '')}",
        f"- Sandbox path: {facts.get('sandbox_path', '')}",
        "",
        "## Advisory",
        "",
        str(payload.get("statement", "")),
    ]
    warnings = list(facts.get("warnings", []) or [])
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    limitations = list(facts.get("limitations", []) or [])
    if limitations:
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {limitation}" for limitation in limitations)
    return "\n".join(lines) + "\n"


def _collect_log_paths(
    state: dict[str, Any],
    artifact_refs: dict[str, str],
    test_report: dict[str, Any] | None,
    orchestration_summary: dict[str, Any] | None,
) -> dict[str, str]:
    log_paths: dict[str, str] = {}
    for key in ("phase2_log", "post_transform_test_log"):
        ref = str(artifact_refs.get(key) or "")
        if ref:
            log_paths[key] = ref
    transform_log = str(state.get("transform_log_path") or "")
    if transform_log:
        log_paths["transform_log_path"] = transform_log
    if isinstance(test_report, dict):
        test_log_path = str(test_report.get("test_log_path") or "")
        source_log_path = str(test_report.get("source_log_path") or "")
        if test_log_path:
            log_paths["test_log_path"] = test_log_path
        if source_log_path:
            log_paths["source_log_path"] = source_log_path
    if isinstance(orchestration_summary, dict):
        orchestration_log = str(orchestration_summary.get("log_path") or "")
        if orchestration_log:
            log_paths["orchestration_log_path"] = orchestration_log
    return log_paths


def _extract_recipes(execution_plan: dict[str, Any]) -> list[str]:
    recipes: list[str] = []
    for key in ("recipes", "openrewrite_recipes"):
        value = execution_plan.get(key)
        if isinstance(value, list):
            recipes.extend(str(item) for item in value if isinstance(item, (str, int, float)))
    if recipes:
        return recipes
    steps = execution_plan.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_recipes = step.get("recipes")
            if isinstance(step_recipes, list):
                recipes.extend(str(item) for item in step_recipes if isinstance(item, (str, int, float)))
    migration_units = execution_plan.get("migration_units")
    if isinstance(migration_units, list):
        for unit in migration_units:
            if not isinstance(unit, dict):
                continue
            transformations = unit.get("transformations")
            if not isinstance(transformations, list):
                continue
            for transformation in transformations:
                if not isinstance(transformation, dict):
                    continue
                active_recipes = transformation.get("active_recipes")
                if isinstance(active_recipes, list):
                    recipes.extend(
                        str(item) for item in active_recipes if isinstance(item, (str, int, float))
                    )
    return recipes


def _boot4_warnings(
    target_stack: dict[str, Any],
    state: dict[str, Any],
    assessment_report: dict[str, Any] | None,
    migration_plan: dict[str, Any] | None,
) -> list[str]:
    spring_boot = str(target_stack.get("spring_boot", ""))
    if not spring_boot.startswith("4."):
        return []

    collected: list[str] = []
    for source in (
        state.get("warnings", []),
        (assessment_report or {}).get("warnings", []),
        (migration_plan or {}).get("warnings", []),
    ):
        if isinstance(source, list):
            collected.extend(str(item) for item in source)
    defaults = [
        "Spring Framework 7.x is required for Spring Boot 4.",
        "Jakarta EE 11 / Servlet 6.1 baseline applies.",
        "Boot 3 deprecated APIs removed in Boot 4 must be reviewed.",
        "Spring Cloud compatibility must be reviewed.",
        "Spring Security, Spring Data, Hibernate, and custom starter risk requires human review.",
        "javax.* leftovers must be eliminated.",
        "Maven >= 3.6.3 and Java 21 runtime validation are required for this sandbox profile.",
        "Official Boot guidance prefers latest 3.5.x before Boot 4; direct migration must use the fallback profile if unstable.",
    ]
    deduped: list[str] = []
    for warning in [*collected, *defaults]:
        if warning and warning not in deduped:
            deduped.append(warning)
    return deduped


def _object_or_empty(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_report_value(value: Any, run_dir: Path) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_report_value(item, run_dir) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_report_value(item, run_dir) for item in value]
    if not isinstance(value, str):
        return value
    text = value
    if "://" not in text:
        path = Path(text)
        if path.is_absolute():
            try:
                text = path.resolve().relative_to(run_dir.resolve()).as_posix()
            except ValueError:
                pass
    text = _redact_report_text(text)
    home = str(Path.home())
    if home and home not in {".", "/"}:
        text = text.replace(home, "%USERPROFILE%")
        text = text.replace(home.replace("\\", "/"), "%USERPROFILE%")
    return text


def _redact_report_text(text: str) -> str:
    redacted = text
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _read_json(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"unable to parse {path.name}: {exc}")
        return None
    if isinstance(payload, dict):
        return payload
    warnings.append(f"unexpected payload type for {path.name}")
    return None


def _read_optional_json(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return _read_json(path, warnings)


def _read_yaml(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        warnings.append(f"unable to parse {path.name}: {exc}")
        return None
    if isinstance(payload, dict):
        return payload
    warnings.append(f"unexpected payload type for {path.name}")
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _pipeline_history_context(state: dict[str, Any]) -> list[dict[str, Any]]:
    pipeline_history = state.get("pipeline_history")
    if isinstance(pipeline_history, list):
        return pipeline_history
    history: list[dict[str, Any]] = []
    artifact_refs = dict(state.get("artifact_refs", {}) or {})
    orchestration_summary = _read_json(
        Path(str(artifact_refs.get("orchestration_summary") or "")),
        [],
    )
    if isinstance(orchestration_summary, dict):
        stages = orchestration_summary.get("stages") or orchestration_summary.get("stage_history")
        if isinstance(stages, list):
            for stage in stages:
                if isinstance(stage, dict):
                    history.append({
                        "stage_index": stage.get("stage_index", ""),
                        "status": stage.get("status", ""),
                        "profile": stage.get("profile", ""),
                        "duration_seconds": stage.get("duration_seconds"),
                    })
    if not history:
        stage_index = state.get("stage_index", state.get("stage", ""))
        history.append({
            "stage_index": stage_index,
            "status": state.get("status", ""),
            "profile": state.get("profile", ""),
            "duration_seconds": state.get("duration_seconds"),
        })
    return history


def _stack_path_text(stack: dict[str, Any]) -> str:
    java = stack.get("java", "")
    spring_boot = stack.get("spring_boot", "")
    framework = stack.get("spring_framework", "")
    parts = [f"Java {java}"] if java else []
    if spring_boot:
        parts.append(f"Spring Boot {spring_boot}")
    if framework:
        parts.append(f"Spring Framework {framework}")
    return " / ".join(parts) if parts else "not recorded"


def _stack_transition_text(source: dict[str, Any], target: dict[str, Any]) -> str:
    return f"{_stack_path_text(source)} → {_stack_path_text(target)}"


def _completed_target_stack(
    pipeline_history: list[dict[str, Any]],
    state: dict[str, Any],
) -> dict[str, Any]:
    latest = None
    for stage in reversed(pipeline_history):
        if isinstance(stage, dict) and stage.get("status") in ("PASS", "completed"):
            profile = stage.get("profile", "")
            if profile:
                latest = profile
                break
    if latest is None:
        return dict(state.get("target_stack", {}) or {})
    target_stack = dict(state.get("target_stack", {}) or {})
    return target_stack


def _latest_completed_stage(pipeline_history: list[dict[str, Any]]) -> int:
    for stage in reversed(pipeline_history):
        if isinstance(stage, dict) and stage.get("status") in ("PASS", "completed"):
            idx = stage.get("stage_index", 0)
            if isinstance(idx, int):
                return idx
    return 0


def _stage_label(stage_index: int) -> str:
    labels = {
        1: "Java 11 / Spring Boot 2.1.6 → Spring Boot 2.7 / Java 11",
        2: "Spring Boot 2.7 / Java 11 → Spring Boot 3.5 / Java 17",
        3: "Spring Boot 3.5 / Java 17 → Spring Boot 3.5 / Java 21",
        4: "Spring Boot 3.5 / Java 21 → Spring Boot 4 / Java 21",
    }
    return labels.get(stage_index, f"Stage {stage_index}")


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _report_summary(
    source_stack: dict[str, Any],
    target_stack: dict[str, Any],
    pipeline_history: list[dict[str, Any]],
) -> str:
    transition = _stack_transition_text(source_stack, target_stack)
    completed = _latest_completed_stage(pipeline_history)
    return (
        f"Migration from {transition}. "
        f"Completed through stage {completed} of 4. "
        f"{_SANDBOX_ONLY_DISCLAIMER}"
    )


def _change_summary(
    source_stack: dict[str, Any],
    target_stack: dict[str, Any],
    pipeline_history: list[dict[str, Any]],
) -> dict[str, Any]:
    changes: list[str] = []
    source_java = str(source_stack.get("java", ""))
    target_java = str(target_stack.get("java", ""))
    source_boot = str(source_stack.get("spring_boot", ""))
    target_boot = str(target_stack.get("spring_boot", ""))
    if source_java and target_java and source_java != target_java:
        changes.append(f"Java upgrade: {source_java} → {target_java}")
    if source_boot and target_boot:
        changes.append(f"Spring Boot upgrade: {source_boot} → {target_boot}")
    changes.append("Sandbox-only transform and build artifact generation")
    return {
        "description": _report_summary(source_stack, target_stack, pipeline_history),
        "changes": changes,
    }


def _timing_context(pipeline_history: list[dict[str, Any]]) -> dict[str, Any]:
    total = 0.0
    stage_timings: list[dict[str, Any]] = []
    for stage in pipeline_history:
        if not isinstance(stage, dict):
            continue
        duration = stage.get("duration_seconds")
        if isinstance(duration, (int, float)):
            total += duration
            stage_timings.append({
                "stage_index": stage.get("stage_index", ""),
                "status": stage.get("status", ""),
                "duration_seconds": duration,
            })
    return {
        "total_duration_seconds": total,
        "stages": stage_timings,
    }
