from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migration_factory.approval import (
    check_approval_decision,
    check_approved_plan_lock,
    read_approval_decision,
)


TRANSFORMATION_PLAN_SCHEMA_VERSION = "1.3"
TRANSFORMATION_DIR_NAME = "transformation"
TRANSFORMATION_EXECUTION_PLAN = "transformation_execution_plan.yaml"


class TransformationExecutionPlanError(ValueError):
    """Raised when approved planning artifacts cannot be adapted for Transformer."""


def write_transformation_execution_plan(
    modernized_app_path: str | Path,
    run_id: str,
) -> Path:
    app_path = Path(modernized_app_path).expanduser().resolve()
    run_dir = app_path / ".migration" / "runs" / run_id

    _ensure_approved(run_dir, run_id)

    migration_plan = _read_yaml_mapping(run_dir / "planning" / "migration_plan.yaml")
    migration_units = _read_yaml_mapping(run_dir / "planning" / "migration_units.yaml")
    assessment_report = _read_json_mapping(run_dir / "assessment" / "assessment_report.json")
    rewrite_plugin_plan = _read_optional_json_mapping(run_dir / "analysis" / "rewrite_plugin_plan.json")

    payload = _build_transformer_plan(
        app_path=app_path,
        run_id=run_id,
        migration_plan=migration_plan,
        migration_units=migration_units,
        assessment_report=assessment_report,
        rewrite_plugin_plan=rewrite_plugin_plan,
    )

    output_path = run_dir / TRANSFORMATION_DIR_NAME / TRANSFORMATION_EXECUTION_PLAN
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_dump_yaml(payload), encoding="utf-8")
    return output_path


def _ensure_approved(run_dir: Path, run_id: str) -> None:
    decision_errors = check_approval_decision(run_dir, expected_run_id=run_id)
    if decision_errors:
        raise TransformationExecutionPlanError("; ".join(decision_errors))

    lock_errors = check_approved_plan_lock(run_dir, expected_run_id=run_id)
    if lock_errors:
        raise TransformationExecutionPlanError("; ".join(lock_errors))

    decision = read_approval_decision(run_dir).get("decision")
    if decision != "approved":
        raise TransformationExecutionPlanError(
            f"approval_decision.json decision must be approved, got {decision!r}"
        )


def _build_transformer_plan(
    *,
    app_path: Path,
    run_id: str,
    migration_plan: dict[str, Any],
    migration_units: dict[str, Any],
    assessment_report: dict[str, Any],
    rewrite_plugin_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    units = migration_units.get("units")
    if not isinstance(units, list) or not units:
        raise TransformationExecutionPlanError("planning/migration_units.yaml must contain units")

    active_recipes = _string_list((rewrite_plugin_plan or {}).get("active_recipes"))
    recipe_artifacts = _string_list((rewrite_plugin_plan or {}).get("recipe_artifacts"))
    first_write_unit = _first_write_unit_id(units) if active_recipes else None
    first_write_index = _first_write_unit_index(units) if active_recipes else -1
    profile = migration_plan.get("profile") or assessment_report.get("profile")

    return {
        "schema_version": TRANSFORMATION_PLAN_SCHEMA_VERSION,
        "migration": {
            "id": run_id,
            "name": str(profile or run_id),
        },
        "workspaces": {
            "target": {
                "path": str(app_path),
                "migration_dir": ".migration",
                "ledger_file": ".migration/ledger.json",
            }
        },
        "migration_units": [
            _adapt_unit(
                unit,
                active_recipes=active_recipes,
                recipe_artifacts=recipe_artifacts if index == first_write_index else None,
                first_write_unit=first_write_unit,
            )
            for index, unit in enumerate(units)
        ],
    }


def _adapt_unit(
    raw_unit: Any,
    *,
    active_recipes: list[str],
    recipe_artifacts: list[str] | None = None,
    first_write_unit: str | None,
) -> dict[str, Any]:
    if not isinstance(raw_unit, dict):
        raise TransformationExecutionPlanError("planning/migration_units.yaml units must be mappings")

    unit_id = raw_unit.get("id")
    if not unit_id:
        raise TransformationExecutionPlanError("planning/migration_units.yaml unit missing id")
    unit_id = str(unit_id)

    transformations: list[dict[str, Any]] = []
    if active_recipes and unit_id == first_write_unit:
        openrewrite_transformation = {"type": "openrewrite", "active_recipes": active_recipes}
        if recipe_artifacts:
            openrewrite_transformation["recipe_artifacts"] = recipe_artifacts
        transformations.append(openrewrite_transformation)
    transformations.append(
        {
            "type": "custom_code_change",
            "description": str(raw_unit.get("goal") or raw_unit.get("title") or unit_id),
        }
    )

    return {
        "id": unit_id,
        "title": raw_unit.get("goal") or raw_unit.get("title"),
        "expected_files": _expected_files(raw_unit),
        "transformations": transformations,
        "checks": _checks(raw_unit),
    }


def _expected_files(unit: dict[str, Any]) -> list[str]:
    for key in ("expected_files", "expected_source_files", "expected_artifacts"):
        values = unit.get(key)
        if values is not None:
            return _string_list(values)
    return []


def _checks(unit: dict[str, Any]) -> list[dict[str, Any]]:
    validation = _string_list(unit.get("validation"))
    if not validation:
        return []

    return [
        {
            "id": "validation",
            "command": " ".join(validation),
            "required": unit.get("required") != "auto",
        }
    ]


def _first_write_unit_id(units: list[Any]) -> str | None:
    for unit in units:
        if isinstance(unit, dict) and unit.get("writes_source") is True and unit.get("id"):
            return str(unit["id"])
    for unit in units:
        if isinstance(unit, dict) and unit.get("id"):
            return str(unit["id"])
    return None


def _first_write_unit_index(units: list[Any]) -> int:
    for index, unit in enumerate(units):
        if isinstance(unit, dict) and unit.get("writes_source") is True and unit.get("id"):
            return index
    return 0


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return [str(value)]


def _read_json_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise TransformationExecutionPlanError(f"Missing required artifact: {path}")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TransformationExecutionPlanError(f"Invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise TransformationExecutionPlanError(f"Artifact must be a JSON object: {path}")
    return loaded


def _read_optional_json_mapping(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json_mapping(path)


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise TransformationExecutionPlanError(f"Missing required artifact: {path}")
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise TransformationExecutionPlanError("PyYAML is required to adapt planning artifacts") from exc

    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TransformationExecutionPlanError(f"Artifact must be a YAML mapping: {path}")
    return loaded


def _dump_yaml(payload: dict[str, Any]) -> str:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise TransformationExecutionPlanError("PyYAML is required to write Transformer plan") from exc

    return yaml.safe_dump(payload, sort_keys=False)
