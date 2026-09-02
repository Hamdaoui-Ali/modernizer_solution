from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from migration_factory.agents.planning_agent.paths import get_run_planning_dir
from migration_factory.contracts.schema_validation import validate_against_schema
from migration_factory.contracts.constants import APPROVAL_DECISION_VALUES


REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "migration_plan.yaml",
    "migration_units.yaml",
    "approval_request.json",
    "plan_summary.md",
)
REQUIRED_UNIT_FIELDS: tuple[str, ...] = (
    "id",
    "goal",
    "tools",
    "validation",
    "writes_source",
    "required",
    "expected_artifacts",
    "rollback_strategy",
    "blocking_gate",
    "assist_policy",
)
REQUIRED_UNIT_ORDER: tuple[str, ...] = (
    "baseline",
    "java-17",
    "spring-boot-3-5-14",
    "jakarta",
    "dependency-cleanup",
    "existing-test-migration",
)
ALLOWED_UNIT_ORDERS: tuple[tuple[str, ...], ...] = (
    REQUIRED_UNIT_ORDER,
    (
        "baseline",
        "java-17",
        "spring-boot-3-5",
        "jakarta",
        "dependency-cleanup",
        "existing-test-migration",
    ),
    (
        "baseline",
        "java-21-runtime-validation",
    ),
    (
        "baseline",
        "java-21",
        "spring-boot-4-0",
        "jakarta",
        "dependency-cleanup",
        "existing-test-migration",
    ),
    (
        "baseline",
        "spring-boot-2-7",
        "dependency-cleanup",
        "existing-test-migration",
    ),
    (
        "baseline",
        "spring-boot-4-0",
        "jakarta",
        "dependency-cleanup",
        "existing-test-migration",
    ),
)
APPROVAL_OPTIONS = APPROVAL_DECISION_VALUES


@dataclass(frozen=True)
class PlanValidationResult:
    status: str
    reasons: tuple[str, ...]
    report_path: Path


def validate_planning_outputs(modernized_app_path: str, run_id: str) -> PlanValidationResult:
    planning_dir = get_run_planning_dir(modernized_app_path, run_id)
    planning_dir.mkdir(parents=True, exist_ok=True)

    reasons: list[str] = []
    artifact_paths = {name: planning_dir / name for name in REQUIRED_ARTIFACTS}

    for name, artifact_path in artifact_paths.items():
        if not artifact_path.exists():
            reasons.append(f"Missing required output artifact: {name}")

    if not reasons:
        _validate_plan_yaml(artifact_paths["migration_plan.yaml"], run_id, reasons)
        _validate_units_yaml(artifact_paths["migration_units.yaml"], run_id, reasons)
        _validate_approval_json(artifact_paths["approval_request.json"], run_id, reasons)

    status = "PASS" if not reasons else "FAIL"
    report_path = planning_dir / "plan_validation_report.json"
    report_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": status,
                "reasons": reasons,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return PlanValidationResult(status=status, reasons=tuple(reasons), report_path=report_path)


def _validate_plan_yaml(path: Path, run_id: str, reasons: list[str]) -> None:
    payload = _load_yaml(path, reasons)
    if not isinstance(payload, dict):
        reasons.append("migration_plan.yaml must be YAML mapping")
        return
    _append_schema_reasons("migration_plan.yaml", payload, "migration_plan.schema.json", reasons)

    required_fields = (
        "schema_version",
        "run_id",
        "status",
        "risk",
        "artifact_refs",
        "executable",
        "requires_human_approval",
        "risks",
        "unit_references",
    )
    for field in required_fields:
        if field not in payload:
            reasons.append(f"migration_plan.yaml missing field: {field}")

    if payload.get("schema_version") != "1.0.0":
        reasons.append("migration_plan.yaml schema_version must be 1.0.0")
    if payload.get("run_id") != run_id:
        reasons.append("migration_plan.yaml run_id mismatch")
    if payload.get("status") not in {"PASS", "FAIL", "WARNING", "SKIPPED"}:
        reasons.append("migration_plan.yaml status must be a supported status")
    if payload.get("risk") not in {"LOW", "MEDIUM", "HIGH", "BLOCKED", "UNKNOWN"}:
        reasons.append("migration_plan.yaml risk must be a supported risk")
    if not isinstance(payload.get("artifact_refs"), dict):
        reasons.append("migration_plan.yaml artifact_refs must be object")
    if not isinstance(payload.get("executable"), bool):
        reasons.append("migration_plan.yaml executable must be boolean")
    if payload.get("requires_human_approval") is not True:
        reasons.append("migration_plan.yaml requires_human_approval must be true")
    if not isinstance(payload.get("risks"), list):
        reasons.append("migration_plan.yaml risks must be list")
    if not isinstance(payload.get("unit_references"), list):
        reasons.append("migration_plan.yaml unit_references must be list")


def _validate_units_yaml(path: Path, run_id: str, reasons: list[str]) -> None:
    payload = _load_yaml(path, reasons)
    if not isinstance(payload, dict):
        reasons.append("migration_units.yaml must be YAML mapping")
        return
    _append_schema_reasons("migration_units.yaml", payload, "migration_units.schema.json", reasons)

    for field in ("schema_version", "run_id", "status", "artifact_refs", "units"):
        if field not in payload:
            reasons.append(f"migration_units.yaml missing field: {field}")
    if payload.get("schema_version") != "1.0.0":
        reasons.append("migration_units.yaml schema_version must be 1.0.0")
    if payload.get("run_id") != run_id:
        reasons.append("migration_units.yaml run_id mismatch")
    if payload.get("status") not in {"PASS", "FAIL", "WARNING", "SKIPPED"}:
        reasons.append("migration_units.yaml status must be a supported status")
    if not isinstance(payload.get("artifact_refs"), dict):
        reasons.append("migration_units.yaml artifact_refs must be object")

    units = payload.get("units")
    if not isinstance(units, list):
        reasons.append("migration_units.yaml units must be list")
        return

    ordered_ids: list[str] = []
    for idx, unit in enumerate(units):
        if not isinstance(unit, dict):
            reasons.append(f"units[{idx}] must be mapping")
            continue
        ordered_ids.append(str(unit.get("id", "")))
        for field in REQUIRED_UNIT_FIELDS:
            if field not in unit:
                reasons.append(f"units[{idx}] missing field: {field}")
        tools = unit.get("tools")
        if not isinstance(tools, list):
            reasons.append(f"units[{idx}].tools must be list")
            continue
        for tool in tools:
            if "copilot" in str(tool).lower() or "llm" in str(tool).lower():
                reasons.append(f"units[{idx}].tools contains forbidden token: {tool}")

    if tuple(ordered_ids) not in ALLOWED_UNIT_ORDERS:
        reasons.append(
            "migration_units.yaml unit order mismatch; expected one supported profile order"
        )


def _validate_approval_json(path: Path, run_id: str, reasons: list[str]) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        reasons.append(f"approval_request.json invalid: {exc}")
        return

    if not isinstance(payload, dict):
        reasons.append("approval_request.json must be object")
        return
    _append_schema_reasons(
        "approval_request.json", payload, "approval_request.schema.json", reasons
    )

    if payload.get("run_id") != run_id:
        reasons.append("approval_request.json run_id mismatch")
    if payload.get("requires_human_approval") is not True:
        reasons.append("approval_request.json requires_human_approval must be true")

    required_fields = (
        "schema_version",
        "run_id",
        "agent",
        "phase",
        "status",
        "profile",
        "requires_human_approval",
        "decision_options",
        "recommended_decision",
        "units_to_execute",
        "blockers",
        "warnings",
        "artifact_refs",
    )
    for field in required_fields:
        if field not in payload:
            reasons.append(f"approval_request.json missing field: {field}")

    if payload.get("agent") != "planning_agent":
        reasons.append("approval_request.json agent must be planning_agent")
    if payload.get("phase") != "approval":
        reasons.append("approval_request.json phase must be approval")
    if payload.get("recommended_decision") is not None:
        reasons.append("approval_request.json recommended_decision must be null")
    if "decision" in payload and payload.get("decision") not in APPROVAL_OPTIONS:
        reasons.append("approval_request.json decision must be a supported approval decision")
    if not isinstance(payload.get("units_to_execute"), list):
        reasons.append("approval_request.json units_to_execute must be list")
    if not isinstance(payload.get("blockers"), list):
        reasons.append("approval_request.json blockers must be list")
    if not isinstance(payload.get("warnings"), list):
        reasons.append("approval_request.json warnings must be list")
    if not isinstance(payload.get("artifact_refs"), dict):
        reasons.append("approval_request.json artifact_refs must be object")

    options = payload.get("decision_options")
    if options != list(APPROVAL_OPTIONS):
        reasons.append("approval_request.json decision_options must match required exact order")


def _load_yaml(path: Path, reasons: list[str]) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        reasons.append(f"{path.name} invalid: {exc}")
        return None


def _append_schema_reasons(
    artifact_name: str,
    payload: object,
    schema_name: str,
    reasons: list[str],
) -> None:
    for error in validate_against_schema(payload, schema_name):
        reasons.append(f"{artifact_name} schema violation: {error}")
