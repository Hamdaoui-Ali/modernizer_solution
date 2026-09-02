from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

from migration_factory.contracts.migration import (
    BuildValidationStatus,
    LedgerError,
    LedgerStatus,
    initialize_ledger,
    load_ledger,
    mark_unit_awaiting_build,
    mark_unit_in_progress,
    save_ledger,
)

from .executor import CommandResult, run_command
from .plan import MigrationPlan, MigrationUnit, load_migration_plan
from .pom_patches import (
    patch_batch_config_flat_file_item_reader_constructor,
    detect_spring_boot_version,
    patch_maven_enforcer_java_version,
    patch_pom_property,
    patch_security_config_authorize_http_requests,
    patch_spring_boot_version,
    is_stable_spring_boot_35_version,
)
from .rewrite import (
    build_rewrite_run_command,
    resolve_rewrite_apply_goal,
    rewrite_plugin_version_from_xml,
)


class TransformationAgentError(Exception):
    pass


@dataclass(frozen=True)
class TransformationRunResult:
    ledger_file: Path
    status: str
    completed_units: list[str]
    blocked_unit: str | None = None


def run_transformation_agent(
    modernized_app_path: str | Path,
    openrewrite_plugin_txt: str | Path,
    migration_plan_path: str | Path,
    *,
    start_unit: str | None = None,
    dry_run: bool = False,
    stream_output: bool = True,
    wait_for_continue: bool = True,
) -> TransformationRunResult:
    plan = load_migration_plan(migration_plan_path, modernized_app_path)
    _ensure_target_workspace(plan.target_path)
    _ensure_ledger(plan)
    plugin_version = rewrite_plugin_version_from_xml(openrewrite_plugin_txt)

    start_index = _resolve_start_index(plan, start_unit)
    ledger = load_ledger(plan.ledger_file)
    if start_unit is None:
        start_index = max(start_index, int(ledger.get("next_unit_index", 0)))

    for unit_index in range(start_index, len(plan.units)):
        unit = plan.units[unit_index]
        _run_unit(
            plan=plan,
            unit=unit,
            unit_index=unit_index,
            plugin_version=plugin_version,
            dry_run=dry_run,
            stream_output=stream_output,
        )

        if wait_for_continue:
            input(
                f"\nUnit {unit.id} is awaiting Build Agent validation.\n"
                f"Run Build Agent with --ledger-file {plan.ledger_file} in another terminal.\n"
                "Press Enter to continue to the next migration unit..."
            )

        validation = _verify_build_validation(plan.ledger_file, unit.id)
        if validation != BuildValidationStatus.PASSED:
            ledger = load_ledger(plan.ledger_file)
            return _result_from_ledger(plan.ledger_file, ledger)

    ledger = load_ledger(plan.ledger_file)
    ledger["status"] = LedgerStatus.COMPLETED
    ledger["current_unit"] = None
    ledger["build_validation"] = {
        "required": False,
        "status": BuildValidationStatus.NOT_REQUIRED,
    }
    save_ledger(plan.ledger_file, ledger)
    return _result_from_ledger(plan.ledger_file, ledger)


def _run_unit(
    *,
    plan: MigrationPlan,
    unit: MigrationUnit,
    unit_index: int,
    plugin_version: str,
    dry_run: bool,
    stream_output: bool,
) -> None:
    unit_started = time.monotonic()
    print(f"\nStarting {unit.id}: {unit.title or ''}".rstrip())
    mark_unit_in_progress(plan.ledger_file, unit_id=unit.id, unit_index=unit_index, title=unit.title)

    command_results: list[dict[str, Any]] = []
    recorded_transformations: list[dict[str, Any]] = []

    for transformation in unit.transformations:
        transformation_type = transformation.get("type")
        if transformation_type == "openrewrite":
            active_recipes = [str(item) for item in transformation.get("active_recipes", [])]
            recipe_artifacts = [str(item) for item in transformation.get("recipe_artifacts", [])]
            configured_apply_goal = str(transformation.get("apply_goal") or "run")
            apply_goal = resolve_rewrite_apply_goal(plan.target_path, configured_apply_goal)
            if apply_goal != configured_apply_goal:
                print(
                    "OpenRewrite standalone reactor detected; using lifecycle-forking "
                    f"goal={apply_goal} instead of configured goal={configured_apply_goal}"
                )
            command = build_rewrite_run_command(
                active_recipes,
                recipe_artifacts=recipe_artifacts,
                plugin_version=plugin_version,
                apply_goal=apply_goal,
                maven_args=[str(item) for item in transformation.get("apply_maven_args", [])],
            )
            apply_maven_args = [str(item) for item in transformation.get("apply_maven_args", [])]
            print(
                f"OpenRewrite apply unit={unit.id} openrewrite_goal={apply_goal} "
                f"apply_maven_args={apply_maven_args}"
            )
            if dry_run:
                command_results.append({"command": command, "dry_run": True, "exit_code": 0})
                continue
            result = run_command(command, cwd=plan.target_path, stream_output=stream_output)
            command_results.append(_command_result_to_dict(result))
            if not result.succeeded:
                _mark_unit_blocked(plan, unit, f"OpenRewrite command failed: {command}", command_results)
                raise TransformationAgentError(f"OpenRewrite command failed for {unit.id}: {command}")
            continue

        if transformation_type == "maven_enforcer_java_version":
            target_range = str(transformation.get("target_range") or "[21,)")
            patches = [] if dry_run else patch_maven_enforcer_java_version(
                plan.target_path,
                unit_id=unit.id,
                target_range=target_range,
            )
            required = transformation.get("required", True) is not False
            if required and not dry_run and not patches:
                _mark_unit_blocked(
                    plan,
                    unit,
                    "REQUIRED_POM_PATCH_NOT_APPLIED maven_enforcer_java_version",
                    command_results,
                    recorded_transformations=recorded_transformations,
                )
                raise TransformationAgentError(
                    "REQUIRED_POM_PATCH_NOT_APPLIED maven_enforcer_java_version"
                )
            for patch in patches:
                print(
                    f"unit={patch.unit} patch=maven_enforcer_java_version "
                    f"file={patch.file} old_range={patch.old_range} "
                    f"new_range={patch.new_range}"
                )
            recorded_transformations.append(
                {
                    "type": transformation_type,
                    "status": "applied" if patches else "not_applicable",
                    "file": "pom.xml",
                    "patches": [
                        {
                            "file": patch.file,
                            "old_range": patch.old_range,
                            "new_range": patch.new_range,
                            "unit": patch.unit,
                        }
                        for patch in patches
                    ],
                }
            )
            continue

        if transformation_type == "pom_property":
            property_name = str(transformation.get("property") or "")
            old_value = str(transformation.get("old_value") or "")
            new_value = str(transformation.get("new_value") or "")
            patches = [] if dry_run else patch_pom_property(
                plan.target_path,
                unit_id=unit.id,
                property_name=property_name,
                old_value=old_value,
                new_value=new_value,
            )
            required = transformation.get("required", True) is not False
            if required and not dry_run and not patches:
                _mark_unit_blocked(
                    plan,
                    unit,
                    f"REQUIRED_POM_PATCH_NOT_APPLIED pom_property {property_name}",
                    command_results,
                    recorded_transformations=recorded_transformations,
                )
                raise TransformationAgentError(
                    f"REQUIRED_POM_PATCH_NOT_APPLIED pom_property {property_name}"
                )
            for patch in patches:
                print(
                    f"unit={patch.unit} patch=pom_property file={patch.file} "
                    f"property={patch.property} old_value={patch.old_value} "
                    f"new_value={patch.new_value}"
                )
            recorded_transformations.append(
                {
                    "type": transformation_type,
                    "status": "applied" if patches else "not_applicable",
                    "file": "pom.xml",
                    "patches": [
                        {
                            "file": patch.file,
                            "property": patch.property,
                            "old_value": patch.old_value,
                            "new_value": patch.new_value,
                            "unit": patch.unit,
                        }
                        for patch in patches
                    ],
                }
            )
            continue

        if transformation_type == "spring_boot_version":
            old_value = str(transformation.get("old_value") or "")
            new_value = str(transformation.get("new_value") or "")
            patches = [] if dry_run else patch_spring_boot_version(
                plan.target_path,
                unit_id=unit.id,
                old_value=old_value,
                new_value=new_value,
            )
            required = transformation.get("required", True) is not False
            if required and not dry_run:
                detection = detect_spring_boot_version(plan.target_path)
                if detection is None:
                    reason = (
                        "REQUIRED_POM_PATCH_NOT_APPLIED spring_boot_version "
                        "detected_locations=[] expected_target_line=3.5.x"
                    )
                    _mark_unit_blocked(
                        plan,
                        unit,
                        reason,
                        command_results,
                        recorded_transformations=recorded_transformations,
                    )
                    raise TransformationAgentError(reason)
                if not is_stable_spring_boot_35_version(detection.version):
                    reason = (
                        "REQUIRED_POM_PATCH_NOT_APPLIED spring_boot_version "
                        f"detected_version={detection.version} expected_target_line=3.5.x "
                        f"detected_location={detection.location}"
                    )
                    _mark_unit_blocked(
                        plan,
                        unit,
                        reason,
                        command_results,
                        recorded_transformations=recorded_transformations,
                    )
                    raise TransformationAgentError(reason)
                print(
                    "spring_boot_version_status=satisfied "
                    f"spring_boot_version_detected={detection.version} "
                    "spring_boot_version_target=3.5.x "
                    f"spring_boot_version_location={detection.location}"
                )
                recorded_transformations.append(
                    {
                        "type": transformation_type,
                        "status": "satisfied",
                        "file": "pom.xml",
                        "spring_boot_version_status": "satisfied",
                        "spring_boot_version_detected": detection.version,
                        "spring_boot_version_target": "3.5.x",
                        "spring_boot_version_location": detection.location,
                        "patches": [
                            {
                                "file": patch.file,
                                "location": patch.location,
                                "old_value": patch.old_value,
                                "new_value": patch.new_value,
                                "unit": patch.unit,
                            }
                            for patch in patches
                        ],
                    }
                )
                continue
            for patch in patches:
                print(
                    f"unit={patch.unit} patch=spring_boot_version file={patch.file} "
                    f"location={patch.location} old_value={patch.old_value} "
                    f"new_value={patch.new_value}"
                )
            recorded_transformations.append(
                {
                    "type": transformation_type,
                    "status": "applied" if patches else "not_applicable",
                    "file": "pom.xml",
                    "patches": [
                        {
                            "file": patch.file,
                            "location": patch.location,
                            "old_value": patch.old_value,
                            "new_value": patch.new_value,
                            "unit": patch.unit,
                        }
                        for patch in patches
                    ],
                }
            )
            continue

        if transformation_type == "security_authorize_http_requests":
            patches = [] if dry_run else patch_security_config_authorize_http_requests(
                plan.target_path,
                unit_id=unit.id,
            )
            for patch in patches:
                print(f"unit={patch.unit} patch={patch.patch} file={patch.file}")
            recorded_transformations.append(
                {
                    "type": transformation_type,
                    "status": "applied" if patches else "not_applicable",
                    "patches": [
                        {"file": patch.file, "patch": patch.patch, "unit": patch.unit}
                        for patch in patches
                    ],
                }
            )
            continue

        if transformation_type == "batch_flat_file_item_reader_constructor":
            patches = [] if dry_run else patch_batch_config_flat_file_item_reader_constructor(
                plan.target_path,
                unit_id=unit.id,
            )
            for patch in patches:
                print(f"unit={patch.unit} patch={patch.patch} file={patch.file}")
            recorded_transformations.append(
                {
                    "type": transformation_type,
                    "status": "applied" if patches else "not_applicable",
                    "patches": [
                        {"file": patch.file, "patch": patch.patch, "unit": patch.unit}
                        for patch in patches
                    ],
                }
            )
            continue

        recorded_transformations.append(
            {
                "type": transformation_type,
                "status": "recorded_not_executed",
                "description": transformation.get("description"),
            }
        )

    ledger = mark_unit_awaiting_build(
        plan.ledger_file,
        unit_id=unit.id,
        expected_files=unit.expected_files,
        checks=unit.checks,
    )
    ledger["units"][unit.id]["transformations"] = recorded_transformations
    ledger["units"][unit.id]["commands"] = command_results
    ledger["units"][unit.id]["unit_duration_seconds"] = round(time.monotonic() - unit_started, 6)
    save_ledger(plan.ledger_file, ledger)


def _verify_build_validation(ledger_file: Path, unit_id: str) -> str:
    ledger = load_ledger(ledger_file)
    validation = ledger.get("build_validation", {})
    status = validation.get("status")
    validation_unit = validation.get("unit_id")

    if status == BuildValidationStatus.PASSED and validation_unit == unit_id:
        print(f"Build validation passed for {unit_id}.")
        return BuildValidationStatus.PASSED

    if status == BuildValidationStatus.FAILED and validation_unit == unit_id:
        print(f"Build validation failed for {unit_id}. Transformation is blocked.")
        return BuildValidationStatus.FAILED

    print(f"Build validation is still pending for {unit_id}. Transformation is blocked.")
    return BuildValidationStatus.PENDING


def _ensure_ledger(plan: MigrationPlan) -> None:
    if plan.ledger_file.is_file():
        return
    initialize_ledger(
        plan.ledger_file,
        migration_id=plan.migration_id,
        migration_name=plan.migration_name,
        total_units=len(plan.units),
        target_path=plan.target_path,
    )


def _ensure_target_workspace(target_path: Path) -> None:
    if not target_path.is_dir():
        raise TransformationAgentError(f"Modernized app path does not exist or is not a directory: {target_path}")


def _resolve_start_index(plan: MigrationPlan, start_unit: str | None) -> int:
    if start_unit is None:
        return 0
    for index, unit in enumerate(plan.units):
        if unit.id == start_unit:
            return index
    raise TransformationAgentError(f"Unknown migration unit: {start_unit}")


def _mark_unit_blocked(
    plan: MigrationPlan,
    unit: MigrationUnit,
    reason: str,
    command_results: list[dict[str, Any]],
    recorded_transformations: list[dict[str, Any]] | None = None,
) -> None:
    ledger = load_ledger(plan.ledger_file)
    ledger["status"] = LedgerStatus.BLOCKED
    ledger["blocked_unit"] = unit.id
    ledger["units"].setdefault(unit.id, {})["status"] = LedgerStatus.BLOCKED
    ledger["units"][unit.id]["blocking_reason"] = reason
    ledger["units"][unit.id]["commands"] = command_results
    if recorded_transformations is not None:
        ledger["units"][unit.id]["transformations"] = recorded_transformations
    save_ledger(plan.ledger_file, ledger)


def _command_result_to_dict(result: CommandResult) -> dict[str, Any]:
    return {
        "command": result.command,
        "exit_code": result.exit_code,
        "duration_seconds": round(float(result.duration_seconds), 6),
        "stdout_tail": result.stdout[-40:],
        "stderr_tail": result.stderr[-40:],
    }


def _result_from_ledger(ledger_file: Path, ledger: dict[str, Any]) -> TransformationRunResult:
    return TransformationRunResult(
        ledger_file=ledger_file,
        status=str(ledger.get("status")),
        completed_units=[str(item) for item in ledger.get("completed_units", [])],
        blocked_unit=ledger.get("blocked_unit"),
    )
