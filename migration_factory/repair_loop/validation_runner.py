from __future__ import annotations

from dataclasses import dataclass, fields
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from migration_factory.agents.build_agent import run_build_agent
from migration_factory.agents.h2_runtime_startup_agent import build_h2_startup_report, write_h2_startup_report
from migration_factory.agents.test_agent.agent import capture_surefire_report_index, run_test_agent


BUILD_PASSED = "BUILD_PASSED_IN_SANDBOX"
BUILD_FAILED = "BUILD_FAILED_IN_SANDBOX"
TEST_PASSED = "TEST_PASSED"
TEST_PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
TESTS_NOT_FOUND = "TESTS_NOT_FOUND"
TEST_BLOCKED = "TEST_BLOCKED"
TEST_NOT_EXECUTED = "TEST_NOT_EXECUTED"
H2_SKIPPED = "H2_STARTUP_SKIPPED"


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    build_status: str
    test_status: str
    h2_status: str
    validation_commands: list[list[str]]
    artifact_refs: dict[str, str]
    warnings: list[str]
    errors: list[str]


@dataclass(frozen=True)
class ValidationExecutionContext:
    """Server-owned description of the validation contract that originally failed.

    The repair path may replace only ``sandbox_path``.  All other execution
    inputs are carried forward so repair validation cannot silently switch to
    a different Maven/Gradle/profile command.
    """

    job_id: str = ""
    command_id: str = ""
    stage_index: int | None = None
    route_step_index: int | None = None
    sandbox_path: str = ""
    validation_command: tuple[str, ...] = ()
    validation_unit_id: str = ""
    source_changing_unit: bool = True
    module: str | None = None
    main_class: str | None = None
    source_jdk_home_env: str | None = None
    target_jdk_home_env: str | None = None
    build_timeout_seconds: int | None = None
    stop_after_start: bool = False
    require_test_reports: bool = False
    h2_required: bool = False
    h2_enabled: bool = False
    source_profile: str = ""
    target_profile: str = ""
    runtime_profile: str = ""
    working_directory: str = ""
    wrapper: str = ""
    tool: str = ""

    @classmethod
    def from_mapping(cls, value: Any | None) -> "ValidationExecutionContext":
        if isinstance(value, cls):
            return value
        raw = value if isinstance(value, dict) else {}
        names = {field.name for field in fields(cls)}
        data = {key: raw[key] for key in names if key in raw}
        if "validation_command" in data:
            command = data["validation_command"]
            if isinstance(command, str):
                command = (command,)
            data["validation_command"] = tuple(str(item) for item in command or ())
        return cls(**data)


def run_validation_after_patch(
    *,
    run_id: str,
    run_dir: str | Path,
    sandbox_path: str | Path,
    attempt: int,
    h2_required: bool = False,
    h2_enabled: bool = False,
    build_timeout_seconds: int | None = None,
    validation_context: ValidationExecutionContext | None = None,
    execution_env: Mapping[str, str] | None = None,
    observer: Callable[[str, dict[str, Any]], None] | None = None,
) -> ValidationResult:
    context = ValidationExecutionContext.from_mapping(validation_context)
    run_path = Path(run_dir)
    sandbox = Path(sandbox_path)
    if not context.validation_command:
        return ValidationResult(
            passed=False,
            build_status=BUILD_FAILED,
            test_status=TESTS_NOT_FOUND,
            h2_status=H2_SKIPPED,
            validation_commands=[],
            artifact_refs={},
            warnings=[],
            errors=["validation execution context did not contain the original validation command"],
        )
    if execution_env is None:
        return ValidationResult(
            passed=False,
            build_status=BUILD_FAILED,
            test_status=TESTS_NOT_FOUND,
            h2_status=H2_SKIPPED,
            validation_commands=[],
            artifact_refs={},
            warnings=[],
            errors=[
                "authoritative stage execution environment was unavailable; "
                "repair validation refused to inherit the backend environment "
                f"(job_id={context.job_id!r}, command_id={context.command_id!r}, "
                f"stage_index={context.stage_index!r}, "
                f"route_step_index={context.route_step_index!r}, "
                f"runtime_profile={context.runtime_profile!r})"
            ],
        )
    output_dir = run_path / "build" / f"repair_attempt_{attempt}"
    pre_surefire_snapshot = capture_surefire_report_index(sandbox / "target" / "surefire-reports")
    if observer is not None:
        observer("build_started", {"attempt": attempt, "run_id": run_id})
    build_result = run_build_agent(
        sandbox,
        output_dir=output_dir,
        stream_output=False,
        stop_after_start=context.stop_after_start,
        timeout_seconds=context.build_timeout_seconds if context.build_timeout_seconds is not None else build_timeout_seconds,
        validation_unit_id=context.validation_unit_id or f"repair-attempt-{attempt}",
        source_changing_unit=context.source_changing_unit,
        validation_command=list(context.validation_command) or None,
        module=context.module,
        main_class=context.main_class,
        source_jdk_home_env=context.source_jdk_home_env,
        target_jdk_home_env=context.target_jdk_home_env,
        execution_env=execution_env,
    )
    validation_commands = [list(build_result.command or context.validation_command)] if (build_result.command or context.validation_command) else []
    artifact_refs: dict[str, str] = {}
    errors: list[str] = []
    warnings: list[str] = list(build_result.warnings or [])
    if build_result.error_contract_path:
        artifact_refs["repair_build_error_contract"] = str(build_result.error_contract_path)

    build_status = BUILD_PASSED if build_result.succeeded else BUILD_FAILED
    if observer is not None:
        observer(
            "build_passed" if build_result.succeeded else "build_failed",
            {
                "attempt": attempt,
                "run_id": run_id,
                "build_status": build_status,
                "exit_code": build_result.exit_code,
            },
        )

    if observer is not None and build_result.succeeded:
        observer("test_started", {"attempt": attempt, "run_id": run_id})
    test_result = run_test_agent(
        sandbox_path=sandbox,
        run_dir=run_path,
        run_id=run_id,
        source_log_path=build_result.error_contract_path or (run_path / "logs" / "phase2_transform.log"),
        command=build_result.command,
        cwd=str(build_result.cwd) if build_result.cwd else str(sandbox),
        build_status=build_status,
        build_exit_code=build_result.exit_code,
        require_test_reports=context.require_test_reports,
        pre_snapshot=pre_surefire_snapshot,
    )
    test_status = (
        TEST_BLOCKED
        if not build_result.succeeded and build_result.result_kind == "compilation_error"
        else TEST_NOT_EXECUTED
        if not build_result.succeeded
        else test_result.test_status
    )
    if observer is not None:
        if not build_result.succeeded:
            observer("test_blocked", {
                "attempt": attempt,
                "run_id": run_id,
                # The persisted ValidationResult is authoritative. A build
                # failure blocks execution, but does not imply missing tests.
                "test_status": test_status,
                "reason": "build_failed",
            })
        else:
            observer(
                "test_passed" if test_status in {TEST_PASSED, TEST_PASS_WITH_WARNINGS, TESTS_NOT_FOUND} else "test_failed",
                {"attempt": attempt, "run_id": run_id, "test_status": test_status},
            )
    artifact_refs.update(
        {
            "repair_test_report": str(test_result.report_path),
            "repair_test_summary": str(test_result.summary_path),
            "repair_test_log": str(test_result.log_path),
        }
    )

    h2_status = H2_SKIPPED
    if h2_required or h2_enabled:
        h2_report = build_h2_startup_report(
            run_id=run_id,
            run_dir=run_path,
            sandbox_path=sandbox,
            required=h2_required,
        )
        h2_path = write_h2_startup_report(run_dir=run_path, report=h2_report)
        h2_status = str(h2_report.get("h2_status") or "H2_STARTUP_FAILED")
        artifact_refs["repair_h2_startup_report"] = str(h2_path)
        command = h2_report.get("command")
        if isinstance(command, list):
            validation_commands.append([str(item) for item in command])

    if build_status != BUILD_PASSED:
        errors.append("build validation failed after repair patch")
        if build_result.result_kind == "compilation_error" and not test_result.report_paths:
            errors.append("test execution was blocked because compilation failed")
        elif test_result.report_paths and test_status not in {TEST_PASSED, TEST_PASS_WITH_WARNINGS}:
            errors.append("test validation failed after repair patch")
    elif test_status not in {TEST_PASSED, TEST_PASS_WITH_WARNINGS, TESTS_NOT_FOUND}:
        errors.append("test validation failed after repair patch")
    if h2_required and h2_status != "H2_STARTUP_PASSED":
        errors.append("required H2 startup failed after repair patch")

    return ValidationResult(
        passed=not errors,
        build_status=build_status,
        test_status=test_status,
        h2_status=h2_status,
        validation_commands=validation_commands,
        artifact_refs=artifact_refs,
        warnings=warnings,
        errors=errors,
    )


