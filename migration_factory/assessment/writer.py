from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from migration_factory.contracts.assessment_artifacts import (
    OPTIONAL_ASSESSMENT_INPUT_ARTIFACTS,
    REQUIRED_ASSESSMENT_INPUT_ARTIFACTS,
)
from migration_factory.contracts.constants import SCHEMA_VERSION
from migration_factory.contracts.schema_validation import validate_against_schema


@dataclass(frozen=True)
class AssessmentWriteResult:
    report_path: Path
    summary_path: Path
    report: dict[str, Any]


class AssessmentArtifactError(ValueError):
    def __init__(self, missing: list[str]) -> None:
        self.missing = tuple(missing)
        super().__init__("Missing required assessment input artifacts: " + ", ".join(missing))


def write_assessment_artifacts(modernized_app_path: str | Path, run_id: str) -> AssessmentWriteResult:
    base_dir = Path(modernized_app_path) / ".migration" / "runs" / run_id
    analysis_dir = base_dir / "analysis"
    planning_dir = base_dir / "planning"
    assessment_dir = base_dir / "assessment"

    missing = _missing_required(analysis_dir, planning_dir)
    if missing:
        raise AssessmentArtifactError(missing)

    analysis_report = _load_json(analysis_dir / "analysis_report.json")
    rewrite_impact = _load_optional_json(analysis_dir / "rewrite_impact_summary.json")
    read_only = _load_optional_json(analysis_dir / "read_only_verification.json")
    migration_plan = _load_yaml(planning_dir / "migration_plan.yaml")
    migration_units = _load_yaml(planning_dir / "migration_units.yaml")
    approval = _load_json(planning_dir / "approval_request.json")
    validation = _load_json(planning_dir / "plan_validation_report.json")
    copilot = _load_optional_json(planning_dir / "copilot_assist.json")
    schema_blockers = _validate_required_input_schemas(
        analysis_report=analysis_report,
        migration_plan=migration_plan,
        migration_units=migration_units,
        approval=approval,
    )

    report = _build_report(
        run_id=run_id,
        analysis_report=analysis_report,
        rewrite_impact=rewrite_impact,
        read_only=read_only,
        migration_plan=migration_plan,
        migration_units=migration_units,
        approval=approval,
        validation=validation,
        copilot=copilot,
        schema_blockers=schema_blockers,
    )

    assessment_dir.mkdir(parents=True, exist_ok=True)
    report_path = assessment_dir / "assessment_report.json"
    summary_path = assessment_dir / "assessment_summary.md"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_path.write_text(_render_summary(report), encoding="utf-8")
    return AssessmentWriteResult(report_path=report_path, summary_path=summary_path, report=report)


def _missing_required(analysis_dir: Path, planning_dir: Path) -> list[str]:
    missing: list[str] = []
    for artifact_name in REQUIRED_ASSESSMENT_INPUT_ARTIFACTS:
        artifact_dir = analysis_dir if artifact_name in _analysis_artifacts() else planning_dir
        if not (artifact_dir / artifact_name).exists():
            missing.append(f"{artifact_dir.name}/{artifact_name}")
    return missing


def _analysis_artifacts() -> set[str]:
    return {
        "analysis_report.json",
        "dependency_graph.json",
        "test_inventory.json",
        "analysis_summary.md",
        "config_inventory.json",
        "rewrite_plugin_plan.json",
        "rewrite_preview.json",
        "rewrite_dry_run.patch",
        "rewrite_impact_summary.json",
        "read_only_verification.json",
    }


def _build_report(
    *,
    run_id: str,
    analysis_report: dict[str, Any],
    rewrite_impact: dict[str, Any] | None,
    read_only: dict[str, Any] | None,
    migration_plan: dict[str, Any],
    migration_units: dict[str, Any],
    approval: dict[str, Any],
    validation: dict[str, Any],
    copilot: dict[str, Any] | None,
    schema_blockers: list[str],
) -> dict[str, Any]:
    blockers = _dedupe_strings(
        [
            *schema_blockers,
            *_as_string_list(approval.get("blockers")),
            *_as_string_list(migration_plan.get("blockers")),
            *_as_string_list(validation.get("reasons")),
        ]
    )
    warnings = _dedupe_strings(
        [
            *_as_string_list(approval.get("warnings")),
            *_as_string_list(migration_plan.get("warnings")),
        ]
    )
    if read_only and read_only.get("source_modified") is True:
        blockers = _dedupe_strings([*blockers, "Analysis read-only verification failed."])

    approval_ready = (
        not blockers
        and analysis_report.get("status") == "PASS"
        and validation.get("status") == "PASS"
        and (not read_only or read_only.get("status") == "PASS")
    )
    status = "PASS" if approval_ready else "FAIL" if blockers else "WARNING"
    units = _as_unit_list(migration_units.get("units"))

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "agent": "assessment",
        "phase": "assessment",
        "status": status,
        "profile": migration_plan.get("profile") or approval.get("profile"),
        "overall_risk": _overall_risk(migration_plan, rewrite_impact, blockers),
        "source_stack": migration_plan.get("source_stack") or _source_stack_from_analysis(analysis_report),
        "target_stack": migration_plan.get("target_stack") or {},
        "analysis": {
            "status": analysis_report.get("status", "UNKNOWN"),
            "artifact_ref": "../analysis/analysis_report.json",
        },
        "planning": {
            "status": "PASS" if not blockers and validation.get("status") == "PASS" else "FAIL",
            "validation_status": validation.get("status", "UNKNOWN"),
            "executable": bool(migration_plan.get("executable")),
            "artifact_ref": "../planning/migration_plan.yaml",
        },
        "openrewrite_dry_run": _openrewrite_section(rewrite_impact),
        "enterprise_compatibility": _enterprise_compatibility_section(
            analysis_report=analysis_report,
            migration_plan=migration_plan,
        ),
        "migration_units": {
            "count": len(units),
            "units": units,
            "artifact_ref": "../planning/migration_units.yaml",
        },
        "blockers": blockers,
        "warnings": warnings,
        "copilot": {
            "status": copilot.get("status", "SKIPPED") if copilot else "SKIPPED",
            "artifact_ref": "../planning/copilot_assist.json" if copilot else None,
        },
        "approval_readiness": {
            "status": "READY_FOR_REVIEW" if approval_ready else "BLOCKED",
            "requires_human_approval": approval.get("requires_human_approval") is True,
            "recommended_decision": approval.get("recommended_decision"),
            "artifact_ref": "../planning/approval_request.json",
        },
        "read_only_verification": {
            "status": read_only.get("status", "SKIPPED") if read_only else "SKIPPED",
            "source_modified": bool(read_only.get("source_modified")) if read_only else None,
            "artifact_ref": "../analysis/read_only_verification.json" if read_only else None,
        },
        "next_recommended_phase": "human_approval" if approval_ready else "resolve_assessment_blockers",
        "execution_claims": {
            "transformation_executed": False,
            "openrewrite_apply_executed": False,
            "migrated_build_executed": False,
            "migrated_tests_executed": False,
            "final_migration_executed": False,
        },
        "artifact_refs": _artifact_refs(read_only=read_only is not None, copilot=copilot is not None),
    }


def _openrewrite_section(rewrite_impact: dict[str, Any] | None) -> dict[str, Any]:
    if not rewrite_impact:
        return {
            "status": "SKIPPED",
            "overall_impact": "UNKNOWN",
            "counts": {},
            "artifact_ref": None,
        }
    changed_files = rewrite_impact.get("changed_files")
    high_risk_files = rewrite_impact.get("high_risk_files")
    blocked_reasons = rewrite_impact.get("blocked_reasons")
    counts = dict(rewrite_impact.get("counts") or {})
    counts.setdefault("changed_files", len(changed_files) if isinstance(changed_files, list) else 0)
    counts.setdefault("high_risk_files", len(high_risk_files) if isinstance(high_risk_files, list) else 0)
    counts.setdefault("blocked_reasons", len(blocked_reasons) if isinstance(blocked_reasons, list) else 0)
    return {
        "status": rewrite_impact.get("status", "PASS"),
        "overall_impact": rewrite_impact.get("overall_impact", "UNKNOWN"),
        "counts": counts,
        "artifact_ref": "../analysis/rewrite_impact_summary.json",
    }


def _enterprise_compatibility_section(
    *,
    analysis_report: dict[str, Any],
    migration_plan: dict[str, Any],
) -> dict[str, Any]:
    findings = _enterprise_findings(analysis_report, migration_plan)
    return {
        "status": "REVIEW_REQUIRED" if findings else "CLEAR",
        "findings": findings,
    }


def _enterprise_findings(analysis_report: dict[str, Any], migration_plan: dict[str, Any]) -> list[dict[str, str]]:
    text = json.dumps({"analysis": analysis_report, "plan": migration_plan}, sort_keys=True).lower()
    findings: list[dict[str, str]] = []
    checks = (
        (
            "old_spring_security_config",
            ("websecurityconfigureradapter", "antmatchers", "authorizerequests", "spring-security-config:5."),
            "Old Spring Security configuration detected; review SecurityFilterChain and authorizeHttpRequests migration.",
        ),
        (
            "javax_to_jakarta",
            ("javax.", "javax/", "javax.persistence", "javax.servlet"),
            "javax imports or dependencies detected; Jakarta namespace migration is required before Boot 3.",
        ),
        (
            "jpa_hibernate_risk",
            ("hibernate-core:5.", "javax.persistence", "spring-boot-starter-data-jpa", "entitymanager"),
            "JPA/Hibernate usage detected; review Hibernate 6 behavior, dialects, naming, and query compatibility.",
        ),
        (
            "maven_plugin_risk",
            ("maven-compiler-plugin:3.1", "maven-surefire-plugin:2.", "spring-boot-maven-plugin:2.1"),
            "Older Maven plugin versions detected; validate compiler, surefire/failsafe, and Boot plugin compatibility.",
        ),
        (
            "internal_corporate_dependencies",
            ("com.company", "com.mycorp", "corp.", "internal", "snapshot"),
            "Internal or corporate dependencies detected; verify Java 17, Jakarta, and Spring Boot 3 compatibility.",
        ),
        (
            "unsupported_bytecode",
            ("bytecode 52", "major version 52", "target=1.8", "source=1.8", "java 8 bytecode"),
            "Unsupported or old Java bytecode risk detected; verify dependency bytecode with Java 17.",
        ),
        (
            "missing_tests_or_smoke_tests",
            ("\"tests\": []", "\"test_count\": 0", "no tests", "missing smoke"),
            "Missing tests or smoke tests detected; add at least build, context-load, and smoke validation before approval.",
        ),
    )
    for code, needles, message in checks:
        if any(needle in text for needle in needles):
            findings.append({"code": code, "severity": "WARNING", "message": message})
    return findings


def _artifact_refs(*, read_only: bool, copilot: bool) -> dict[str, str]:
    refs = {
        "self": "assessment_report.json",
        "summary": "assessment_summary.md",
        "analysis_report": "../analysis/analysis_report.json",
        "dependency_graph": "../analysis/dependency_graph.json",
        "test_inventory": "../analysis/test_inventory.json",
        "analysis_summary": "../analysis/analysis_summary.md",
        "migration_plan": "../planning/migration_plan.yaml",
        "migration_units": "../planning/migration_units.yaml",
        "plan_summary": "../planning/plan_summary.md",
        "approval_request": "../planning/approval_request.json",
        "plan_validation_report": "../planning/plan_validation_report.json",
    }
    for artifact_name in OPTIONAL_ASSESSMENT_INPUT_ARTIFACTS:
        if artifact_name == "read_only_verification.json" and read_only:
            refs["read_only_verification"] = "../analysis/read_only_verification.json"
        elif artifact_name == "copilot_assist.json" and copilot:
            refs["copilot_assist"] = "../planning/copilot_assist.json"
    return refs


def _overall_risk(
    migration_plan: dict[str, Any],
    rewrite_impact: dict[str, Any] | None,
    blockers: list[str],
) -> str:
    if blockers:
        return "BLOCKED"
    impact = rewrite_impact.get("overall_impact") if rewrite_impact else None
    if impact in {"LOW", "MEDIUM", "HIGH"}:
        return str(impact)
    risks = _as_string_list(migration_plan.get("risks"))
    if any("[BLOCKER]" in risk for risk in risks):
        return "BLOCKED"
    if any("[WARNING]" in risk for risk in risks):
        return "MEDIUM"
    return "LOW" if risks else "UNKNOWN"


def _source_stack_from_analysis(analysis_report: dict[str, Any]) -> dict[str, Any]:
    inventory = analysis_report.get("inventory")
    if not isinstance(inventory, dict):
        return {}
    return {
        "build_tool": inventory.get("build_tool"),
        "java": inventory.get("java_version"),
        "spring_boot": inventory.get("spring_boot_version"),
    }


def _as_unit_list(units: Any) -> list[dict[str, Any]]:
    if not isinstance(units, list):
        return []
    result = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        result.append(
            {
                "id": unit.get("id"),
                "goal": unit.get("goal"),
                "writes_source": bool(unit.get("writes_source")),
                "required": bool(unit.get("required")),
            }
        )
    return result


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _validate_required_input_schemas(
    *,
    analysis_report: dict[str, Any],
    migration_plan: dict[str, Any],
    migration_units: dict[str, Any],
    approval: dict[str, Any],
) -> list[str]:
    artifacts = (
        ("analysis/analysis_report.json", analysis_report, "analysis_report.schema.json"),
        ("planning/migration_plan.yaml", migration_plan, "migration_plan.schema.json"),
        ("planning/migration_units.yaml", migration_units, "migration_units.schema.json"),
        ("planning/approval_request.json", approval, "approval_request.schema.json"),
    )
    blockers: list[str] = []
    for artifact_name, payload, schema_name in artifacts:
        for error in validate_against_schema(payload, schema_name):
            blockers.append(f"Schema validation failed for {artifact_name}: {error}")
    return blockers


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be a JSON object.")
    return payload


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _load_json(path)


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be a YAML mapping.")
    return payload


def _render_summary(report: dict[str, Any]) -> str:
    lines = [
        f"# Assessment Summary - {report['run_id']}",
        "",
        f"- Status: {report['status']}",
        f"- Profile: {report.get('profile') or 'unknown'}",
        f"- Analysis: {report['analysis']['status']}",
        f"- Planning: {report['planning']['status']}",
        f"- OpenRewrite dry-run: {report['openrewrite_dry_run']['status']} "
        f"({report['openrewrite_dry_run']['overall_impact']})",
        f"- Enterprise compatibility: {report['enterprise_compatibility']['status']}",
        f"- Migration units: {report['migration_units']['count']}",
        f"- Copilot advisory: {report['copilot']['status']}",
        f"- Read-only verification: {report['read_only_verification']['status']}",
        f"- Approval readiness: {report['approval_readiness']['status']}",
        f"- Next recommended phase: {report['next_recommended_phase']}",
        "",
        "## Blockers",
        *_markdown_list(report["blockers"]),
        "",
        "## Warnings",
        *_markdown_list(report["warnings"]),
        "",
        "## Enterprise Compatibility",
        *_markdown_list([finding["message"] for finding in report["enterprise_compatibility"]["findings"]]),
        "",
        "## Not Executed",
        "- Transformation was not executed.",
        "- OpenRewrite apply was not executed.",
        "- Migrated build and tests were not executed.",
        "- Final migration was not executed.",
        "",
    ]
    return "\n".join(lines)


def _markdown_list(values: list[str]) -> list[str]:
    if not values:
        return ["- None"]
    return [f"- {value}" for value in values]
