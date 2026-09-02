from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml

from migration_factory.contracts import SCHEMA_VERSION


NOT_CAPTURED = "not_captured"
NOT_APPLICABLE = "not_applicable"
NOT_RUN = "not_run"
REDACTED = "[REDACTED]"

_SECRET_KEY_PARTS = (
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "credential",
    "authorization",
    "auth_output",
    "private_key",
    "environment",
    "env",
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

_ARTIFACTS: dict[str, str] = {
    "analysis_report": "analysis/analysis_report.json",
    "rewrite_plugin_plan": "analysis/rewrite_plugin_plan.json",
    "rewrite_impact_summary": "analysis/rewrite_impact_summary.json",
    "read_only_verification": "analysis/read_only_verification.json",
    "migration_plan": "planning/migration_plan.yaml",
    "target_dependency_plan": "planning/target_dependency_plan.json",
    "migration_units": "planning/migration_units.yaml",
    "approval_request": "planning/approval_request.json",
    "assessment_report": "assessment/assessment_report.json",
    "dependency_policy_report": "assessment/dependency_policy_report.json",
    "dependency_policy_summary": "assessment/dependency_policy_report.md",
    "dependency_copilot_request": "assessment/dependency_copilot_request.json",
    "dependency_copilot_response": "assessment/dependency_copilot_response.json",
    "dependency_repair_plan": "assessment/dependency_repair_plan.md",
    "approval_decision": "approval/approval_decision.json",
    "approved_plan_lock": "approval/approved_plan_lock.json",
    "transformation_execution_plan": "transformation/transformation_execution_plan.yaml",
    "openrewrite_plugin_xml": "transformation/openrewrite-plugin.xml",
    "migration_ledger": "workspaces/sandbox/.migration/ledger.json",
    "phase2_log": "logs/phase2_transform.log",
    "post_transform_test_report": "test/post_transform/test_report.json",
    "post_transform_test_summary": "test/post_transform/test_summary.md",
    "post_transform_test_log": "test/post_transform/test_agent.log",
    "orchestration_summary": "orchestration/orchestration_summary.json",
    "timing_report": "performance/timing_report.json",
    "timing_summary": "performance/timing_summary.md",
    "final_migration_report": "final/migration_report.json",
    "final_migration_summary": "final/migration_summary.md",
    "ai_trace": "final/ai_trace.json",
    "report_context": "final/report_context.json",
}


def write_report_context(run_dir: str | Path) -> Path:
    run_path = Path(run_dir)
    final_dir = run_path / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    output_path = final_dir / "report_context.json"
    payload = build_report_context(run_path)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def build_report_context(run_dir: str | Path) -> dict[str, Any]:
    run_path = Path(run_dir)
    warnings: list[str] = []
    artifacts = _load_artifacts(run_path, warnings)
    data = artifacts["data"]
    artifact_refs = artifacts["refs"]

    final_report = _obj(data.get("final_migration_report"))
    orchestration = _obj(data.get("orchestration_summary"))
    approval = _obj(data.get("approval_decision"))
    lock = _obj(data.get("approved_plan_lock"))
    migration_plan = _obj(data.get("migration_plan"))
    assessment = _obj(data.get("assessment_report"))
    analysis = _obj(data.get("analysis_report"))
    rewrite_plan = _obj(data.get("rewrite_plugin_plan"))
    transform_plan = _obj(data.get("transformation_execution_plan"))
    ledger = _obj(data.get("migration_ledger"))
    test_report = _obj(data.get("post_transform_test_report"))
    dependency_policy_report = _obj(data.get("dependency_policy_report"))

    provenance: dict[str, Any] = {}
    context = {
        "schema_version": SCHEMA_VERSION,
        "run_id": _fact(
            final_report.get("run_id"),
            orchestration.get("run_id"),
            approval.get("run_id"),
            _fallback=NOT_CAPTURED,
            _provenance=provenance,
            _key="run_id",
            _refs=[
                _ref("final/migration_report.json", "/run_id"),
                _ref("orchestration/orchestration_summary.json", "/run_id"),
                _ref("approval/approval_decision.json", "/run_id"),
            ],
        ),
        "profile": _profile(migration_plan, assessment, orchestration, final_report, provenance),
        "statuses": _statuses(orchestration, final_report, test_report, provenance),
        "approval": _approval(approval, final_report, lock, provenance),
        "migration_units": _migration_units(transform_plan, data.get("migration_units"), ledger, provenance),
        "openrewrite": _openrewrite(rewrite_plan, transform_plan, data.get("openrewrite_plugin_xml"), provenance),
        "transformation": _transformation(final_report, orchestration, transform_plan, ledger, run_path, provenance),
        "build": _build(final_report, orchestration, ledger, test_report, provenance),
        "tests": _tests(final_report, orchestration, test_report, provenance),
        "ai_trace": _ai_trace(final_report, data.get("ai_trace"), artifact_refs, run_path, provenance),
        "dependency_policy": _dependency_policy(final_report, dependency_policy_report, artifact_refs, provenance),
        "security": _security(analysis, assessment, final_report, provenance),
        "scope_limits": _scope_limits(final_report, provenance),
        "artifact_refs": artifact_refs,
        "provenance": provenance,
        "warnings": _dedupe(
            [
                *_list(orchestration.get("warnings")),
                *_list(final_report.get("warnings")),
                *warnings,
            ]
        ),
        "blockers": _dedupe([*_list(orchestration.get("blockers")), *_list(final_report.get("blockers"))]),
        "errors": _dedupe([*_list(orchestration.get("errors")), *_list(final_report.get("errors"))]),
    }
    return _redact(_relativize_paths(context, run_path))


def _load_artifacts(run_dir: Path, warnings: list[str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    refs: dict[str, str] = {}
    for name, rel_path in _ARTIFACTS.items():
        if name == "report_context":
            refs[name] = rel_path
            continue
        path = run_dir / rel_path
        if path.is_file():
            refs[name] = rel_path
            data[name] = _read_artifact(path, warnings)
    for source_name in ("final_migration_report", "orchestration_summary"):
        source = _obj(data.get(source_name))
        for name, raw_ref in _obj(source.get("artifact_refs")).items():
            rel_ref = _normalize_artifact_ref(raw_ref, run_dir)
            if rel_ref:
                refs.setdefault(str(name), rel_ref)
    return {"data": data, "refs": dict(sorted(refs.items()))}


def _read_artifact(path: Path, warnings: list[str]) -> Any:
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if path.suffix.lower() == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        if path.suffix.lower() == ".xml":
            return path.read_text(encoding="utf-8")
        return NOT_APPLICABLE
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        warnings.append(f"unable to read artifact {path.name}: {_redact(str(exc))}")
        return NOT_CAPTURED


def _profile(
    migration_plan: dict[str, Any],
    assessment: dict[str, Any],
    orchestration: dict[str, Any],
    final_report: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    governance = _obj(migration_plan.get("profile_governance"))
    return {
        "id": _fact(
            migration_plan.get("profile"),
            assessment.get("profile"),
            orchestration.get("profile_id"),
            _fallback=NOT_CAPTURED,
            _provenance=provenance,
            _key="profile.id",
            _refs=[
                _ref("planning/migration_plan.yaml", "/profile"),
                _ref("assessment/assessment_report.json", "/profile"),
            ],
        ),
        "risk_level": _fact(
            final_report.get("risk_level"),
            governance.get("risk_level"),
            migration_plan.get("risk"),
            assessment.get("overall_risk"),
            _fallback=NOT_CAPTURED,
            _provenance=provenance,
            _key="profile.risk_level",
            _refs=[
                _ref("final/migration_report.json", "/risk_level"),
                _ref("planning/migration_plan.yaml", "/risk"),
            ],
        ),
        "source_stack": _fact(
            final_report.get("source_stack"),
            assessment.get("source_stack"),
            _fallback={},
            _provenance=provenance,
            _key="profile.source_stack",
            _refs=[
                _ref("final/migration_report.json", "/source_stack"),
                _ref("assessment/assessment_report.json", "/source_stack"),
            ],
        ),
        "target_stack": _fact(
            final_report.get("target_stack"),
            assessment.get("target_stack"),
            migration_plan.get("target_stack"),
            _fallback={},
            _provenance=provenance,
            _key="profile.target_stack",
            _refs=[
                _ref("final/migration_report.json", "/target_stack"),
                _ref("planning/migration_plan.yaml", "/target_stack"),
            ],
        ),
        "strategy": _value_or(governance.get("strategy"), final_report.get("strategy"), fallback=NOT_CAPTURED),
        "fallback_profile": _value_or(governance.get("fallback_profile"), final_report.get("fallback_profile"), fallback=NOT_APPLICABLE),
        "production_allowed": _value_or(final_report.get("production_allowed"), governance.get("production_allowed"), fallback=NOT_CAPTURED),
        "requires_human_approval": _value_or(
            final_report.get("requires_human_approval"),
            migration_plan.get("requires_human_approval"),
            fallback=NOT_CAPTURED,
        ),
    }


def _statuses(
    orchestration: dict[str, Any],
    final_report: dict[str, Any],
    test_report: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    fields = {
        "final": ("final_status",),
        "orchestration": ("orchestration_status",),
        "analysis": ("analysis_status",),
        "planning": ("planning_status",),
        "assessment": ("assessment_status",),
        "approval": ("approval_status",),
        "transform": ("transform_status",),
        "build": ("build_status",),
        "tests": ("test_status",),
    }
    result: dict[str, Any] = {}
    for key, names in fields.items():
        name = names[0]
        value = _value_or(orchestration.get(name), final_report.get(name), test_report.get(name), fallback=NOT_RUN)
        result[key] = value
        provenance[f"statuses.{key}"] = [
            _ref("orchestration/orchestration_summary.json", f"/{name}"),
            _ref("final/migration_report.json", f"/{name}"),
        ]
    return result


def _approval(
    approval: dict[str, Any],
    final_report: dict[str, Any],
    lock: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    final_approval = _obj(final_report.get("approval"))
    provenance["approval.decision"] = [
        _ref("approval/approval_decision.json", "/decision"),
        _ref("final/migration_report.json", "/approval/decision"),
    ]
    return {
        "status": _value_or(final_approval.get("status"), fallback=NOT_RUN),
        "decision": _value_or(approval.get("decision"), final_approval.get("decision"), fallback=NOT_CAPTURED),
        "decided_by": _value_or(approval.get("decided_by"), approval.get("approved_by"), final_approval.get("approved_by"), fallback=NOT_CAPTURED),
        "decided_at": _value_or(approval.get("decided_at"), fallback=NOT_CAPTURED),
        "plan_lock_ref": _value_or(approval.get("plan_lock_ref"), final_approval.get("approval_ref"), fallback=NOT_CAPTURED),
        "lock_status": "LOCKED" if lock else NOT_CAPTURED,
    }


def _migration_units(
    transform_plan: dict[str, Any],
    planning_units: Any,
    ledger: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    raw_units = _list(transform_plan.get("migration_units")) or _list(_obj(planning_units).get("units"))
    completed = set(str(unit) for unit in _list(ledger.get("completed_units")))
    blocked = str(ledger.get("blocked_unit") or ledger.get("current_unit") or "")
    units: list[dict[str, Any]] = []
    for index, unit in enumerate(raw_units):
        if not isinstance(unit, dict):
            continue
        unit_id = str(unit.get("id") or f"unit-{index}")
        transformations = [
            {
                "type": _value_or(_obj(item).get("type"), fallback=NOT_CAPTURED),
                "status": _value_or(_obj(item).get("status"), fallback=NOT_CAPTURED),
            }
            for item in _list(unit.get("transformations"))
        ]
        units.append(
            {
                "id": unit_id,
                "goal": _value_or(unit.get("goal"), unit.get("title"), fallback=NOT_CAPTURED),
                "writes_source": _value_or(unit.get("writes_source"), fallback=NOT_CAPTURED),
                "status": "completed" if unit_id in completed else ("blocked" if unit_id == blocked else NOT_CAPTURED),
                "validation": _value_or(unit.get("validation"), unit.get("checks"), fallback=NOT_CAPTURED),
                "expected_artifacts": _value_or(unit.get("expected_artifacts"), fallback=[]),
                "transformations": transformations,
            }
        )
    provenance["migration_units.units"] = [_ref("transformation/transformation_execution_plan.yaml", "/migration_units")]
    return {"count": len(units), "units": units, "source": "transformation_execution_plan" if raw_units else NOT_CAPTURED}


def _openrewrite(
    rewrite_plan: dict[str, Any],
    transform_plan: dict[str, Any],
    plugin_xml: Any,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    transform_recipe_items: list[str] = []
    unit_id = NOT_CAPTURED
    apply_goal = _value_or(rewrite_plan.get("apply_goal"), fallback=NOT_CAPTURED)
    apply_maven_args = _value_or(rewrite_plan.get("apply_maven_args"), fallback=[])
    for unit in _list(transform_plan.get("migration_units")):
        if not isinstance(unit, dict):
            continue
        for transformation in _list(unit.get("transformations")):
            item = _obj(transformation)
            if item.get("type") == "openrewrite":
                unit_id = str(unit.get("id") or NOT_CAPTURED)
                transform_recipe_items.extend(str(recipe) for recipe in _list(item.get("active_recipes")))
                apply_goal = _value_or(item.get("apply_goal"), item.get("goal"), apply_goal, fallback=NOT_CAPTURED)
                apply_maven_args = _value_or(item.get("apply_maven_args"), apply_maven_args, fallback=[])
    active_recipes = transform_recipe_items or [str(recipe) for recipe in _list(rewrite_plan.get("active_recipes"))]
    plugin = _value_or(rewrite_plan.get("plugin"), _plugin_coordinate(plugin_xml), fallback=NOT_CAPTURED)
    provenance["openrewrite.active_recipes"] = [
        _ref("transformation/transformation_execution_plan.yaml", "/migration_units"),
        _ref("analysis/rewrite_plugin_plan.json", "/active_recipes"),
    ]
    return {
        "status": "used" if active_recipes else NOT_RUN,
        "unit_id": unit_id if active_recipes else NOT_APPLICABLE,
        "plugin": plugin,
        "recipe_artifacts": _value_or(rewrite_plan.get("recipe_artifacts"), fallback=[]),
        "active_recipes": active_recipes,
        "apply_goal": apply_goal if active_recipes else NOT_APPLICABLE,
        "apply_maven_args": apply_maven_args if active_recipes else NOT_APPLICABLE,
    }


def _transformation(
    final_report: dict[str, Any],
    orchestration: dict[str, Any],
    transform_plan: dict[str, Any],
    ledger: dict[str, Any],
    run_dir: Path,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    sandbox = _value_or(final_report.get("sandbox_path"), orchestration.get("sandbox_path"), fallback=NOT_CAPTURED)
    provenance["transformation.status"] = [
        _ref("final/migration_report.json", "/transform_status"),
        _ref("orchestration/orchestration_summary.json", "/transform_status"),
    ]
    return {
        "status": _value_or(final_report.get("transform_status"), orchestration.get("transform_status"), fallback=NOT_RUN),
        "sandbox_path": _normalize_artifact_ref(sandbox, run_dir) or _redact(sandbox),
        "plan_target": _redact(_value_or(transform_plan.get("target_path"), transform_plan.get("sandbox_target_path"), fallback=NOT_CAPTURED)),
        "current_unit": _value_or(ledger.get("current_unit"), fallback=NOT_CAPTURED),
        "blocked_unit": _value_or(ledger.get("blocked_unit"), fallback=NOT_APPLICABLE),
        "next_unit_index": _value_or(ledger.get("next_unit_index"), fallback=NOT_CAPTURED),
    }


def _build(
    final_report: dict[str, Any],
    orchestration: dict[str, Any],
    ledger: dict[str, Any],
    test_report: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    provenance["build.status"] = [
        _ref("final/migration_report.json", "/build_status"),
        _ref("orchestration/orchestration_summary.json", "/build_status"),
    ]
    return {
        "status": _value_or(final_report.get("build_status"), orchestration.get("build_status"), fallback=NOT_RUN),
        "command": _value_or(test_report.get("command"), _obj(ledger.get("build_validation")).get("command"), fallback=NOT_CAPTURED),
        "validations": _value_or(ledger.get("build_validations"), ledger.get("build_validation"), fallback=NOT_CAPTURED),
        "log_ref": _value_or(orchestration.get("log_path"), final_report.get("build_log_path"), fallback=NOT_CAPTURED),
    }


def _tests(
    final_report: dict[str, Any],
    orchestration: dict[str, Any],
    test_report: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    provenance["tests.totals"] = [
        _ref("test/post_transform/test_report.json", "/totals"),
        _ref("final/migration_report.json", "/test_totals"),
    ]
    return {
        "status": _value_or(test_report.get("test_status"), final_report.get("test_status"), orchestration.get("test_status"), fallback=NOT_RUN),
        "totals": _value_or(test_report.get("totals"), final_report.get("test_totals"), orchestration.get("test_totals"), fallback={}),
        "execution_owner": _value_or(test_report.get("execution_owner"), fallback=NOT_CAPTURED),
        "execution_mode": _value_or(test_report.get("execution_mode"), fallback=NOT_CAPTURED),
        "report_paths": _value_or(test_report.get("report_paths"), fallback=[]),
    }


def _ai_trace(
    final_report: dict[str, Any],
    ai_trace_artifact: Any,
    artifact_refs: dict[str, str],
    run_dir: Path,
    provenance: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_records = final_report.get("ai_trace")
    if not isinstance(raw_records, list):
        raw_records = ai_trace_artifact
    if not isinstance(raw_records, list):
        return []
    provenance["ai_trace"] = [
        _ref("final/migration_report.json", "/ai_trace"),
        _ref(artifact_refs.get("ai_trace", "final/ai_trace.json"), ""),
    ]
    return [
        normalized
        for record in raw_records
        if isinstance(record, dict)
        for normalized in [_normalize_ai_trace_record(record, run_dir)]
        if _ai_trace_has_record(normalized)
    ]


def _normalize_ai_trace_record(record: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    normalized = {
        "event": _value_or(record.get("event"), record.get("event_type"), fallback=NOT_CAPTURED),
        "agent": _value_or(record.get("agent"), record.get("agent_name"), record.get("model_invocation_id"), fallback=NOT_CAPTURED),
        "evidence_refs": _list(record.get("evidence_refs")),
        "context_pack_checksum": _value_or(record.get("context_pack_checksum"), fallback=NOT_CAPTURED),
        "diagnosis": _value_or(record.get("diagnosis"), record.get("diagnosis_id"), record.get("failure_type"), fallback=NOT_CAPTURED),
        "proposal_ref": _value_or(record.get("proposal_ref"), record.get("repair_proposal_id"), record.get("proposal_id"), fallback=NOT_CAPTURED),
        "proposal_checksum": _value_or(record.get("proposal_checksum"), fallback=NOT_CAPTURED),
        "reviewer_verdict": _value_or(record.get("reviewer_verdict"), record.get("reviewer_decision"), record.get("decision"), fallback=NOT_CAPTURED),
        "human_decision": _value_or(record.get("human_decision"), record.get("approval_decision"), fallback=NOT_CAPTURED),
        "validation_result": _value_or(record.get("validation_result"), record.get("validation_status"), fallback=NOT_CAPTURED),
        "ledger_ref": _value_or(record.get("ledger_ref"), fallback=NOT_CAPTURED),
    }
    return _redact(_relativize_paths(normalized, run_dir))


def _ai_trace_has_record(record: dict[str, Any]) -> bool:
    for value in record.values():
        if isinstance(value, list) and value:
            return True
        if isinstance(value, str) and value not in {"", NOT_CAPTURED, NOT_APPLICABLE, NOT_RUN}:
            return True
    return False


def _dependency_policy(
    final_report: dict[str, Any],
    dependency_policy_report: dict[str, Any],
    artifact_refs: dict[str, str],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    final_policy = _obj(final_report.get("dependency_policy"))
    risks = _list(dependency_policy_report.get("risks"))
    provenance["dependency_policy.status"] = [
        _ref("final/migration_report.json", "/dependency_policy/status"),
        _ref("assessment/dependency_policy_report.json", "/status"),
    ]
    return {
        "target_dependency_plan_ref": _value_or(
            final_report.get("target_dependency_plan_ref"),
            final_policy.get("target_plan_ref"),
            artifact_refs.get("target_dependency_plan"),
            fallback=NOT_CAPTURED,
        ),
        "report_ref": _value_or(
            final_report.get("dependency_policy_report_ref"),
            final_policy.get("report_ref"),
            artifact_refs.get("dependency_policy_report"),
            fallback=NOT_CAPTURED,
        ),
        "status": _value_or(
            final_report.get("dependency_policy_status"),
            final_policy.get("status"),
            dependency_policy_report.get("status"),
            fallback=NOT_RUN,
        ),
        "risks_count": _value_or(
            final_report.get("dependency_policy_risks_count"),
            final_policy.get("risks_count"),
            len(risks),
            fallback=0,
        ),
        "blockers_count": _value_or(
            final_report.get("dependency_policy_blockers_count"),
            final_policy.get("blockers_count"),
            fallback=0,
        ),
        "copilot_advisory_status": _value_or(
            final_report.get("copilot_dependency_advisory_status"),
            final_policy.get("copilot_advisory_status"),
            fallback=NOT_RUN,
        ),
        "policy_patch_applied": _value_or(
            final_report.get("policy_patch_applied"),
            final_policy.get("policy_patch_applied"),
            fallback=False,
        ),
        "unresolved_v2_dependency_risks": _value_or(
            final_report.get("unresolved_v2_dependency_risks"),
            final_policy.get("unresolved_v2_dependency_risks"),
            fallback=[],
        ),
    }


def _security(
    analysis: dict[str, Any],
    assessment: dict[str, Any],
    final_report: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    warnings = [str(item) for item in _list(final_report.get("warnings")) + _list(assessment.get("warnings"))]
    security_warnings = [item for item in warnings if "security" in item.lower() or "auth" in item.lower()]
    provenance["security.warnings"] = [
        _ref("final/migration_report.json", "/warnings"),
        _ref("assessment/assessment_report.json", "/warnings"),
    ]
    return {
        "status": "review_required" if security_warnings else NOT_CAPTURED,
        "security_config_touched": _value_or(
            _obj(analysis.get("migration_signals")).get("security_config_touched"),
            fallback=NOT_CAPTURED,
        ),
        "warnings": security_warnings,
    }


def _scope_limits(final_report: dict[str, Any], provenance: dict[str, Any]) -> list[str]:
    provenance["scope_limits"] = [_ref("final/migration_report.json", "/limitations")]
    return _value_or(final_report.get("limitations"), fallback=[
        "No production promotion performed.",
        "No pull request creation performed.",
        "No deployment performed.",
        "No automatic merge performed.",
    ])


def _plugin_coordinate(plugin_xml: Any) -> str:
    if not isinstance(plugin_xml, str) or not plugin_xml.strip():
        return NOT_CAPTURED
    try:
        root = ET.fromstring(plugin_xml)
    except ET.ParseError:
        return NOT_CAPTURED
    artifact = root.find(".//artifactId")
    group = root.find(".//groupId")
    version = root.find(".//version")
    parts = [item.text.strip() for item in (group, artifact, version) if item is not None and item.text]
    return ":".join(parts) if parts else NOT_CAPTURED


def _fact(*values: Any, _fallback: Any, _provenance: dict[str, Any], _key: str, _refs: list[str]) -> Any:
    _provenance[_key] = _refs
    return _value_or(*values, fallback=_fallback)


def _value_or(*values: Any, fallback: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        return value
    return fallback


def _ref(path: str, pointer: str = "") -> str:
    return f"{path}#{pointer}" if pointer else path


def _normalize_artifact_ref(value: Any, run_dir: Path) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(run_dir.resolve()).as_posix()
        except ValueError:
            return _redact(raw)
    return raw.replace("\\", "/")


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dedupe(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


def _redact(value: Any, key: str = "") -> Any:
    if any(part in key.lower() for part in _SECRET_KEY_PARTS):
        return REDACTED
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_VALUE_PATTERNS:
            redacted = pattern.sub(REDACTED, redacted)
        return _redact_user_home_path(redacted)
    return value


def _relativize_paths(value: Any, run_dir: Path) -> Any:
    if isinstance(value, dict):
        return {key: _relativize_paths(item, run_dir) for key, item in value.items()}
    if isinstance(value, list):
        return [_relativize_paths(item, run_dir) for item in value]
    if not isinstance(value, str):
        return value
    if "://" in value:
        return value
    path = Path(value)
    if not path.is_absolute():
        return value
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return value


def _redact_user_home_path(text: str) -> str:
    home = str(Path.home())
    if home and home not in {".", "/"}:
        text = text.replace(home, "%USERPROFILE%")
        text = text.replace(home.replace("\\", "/"), "%USERPROFILE%")
    return text
