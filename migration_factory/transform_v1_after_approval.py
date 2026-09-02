from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import json
import os
import sys
from pathlib import Path
import time
from typing import Any, Callable, TextIO, TypeVar

import yaml

from migration_factory.agents.build_agent import run_build_agent
from migration_factory.agents.test_agent import (
    TEST_STATUS_ERROR,
    TEST_STATUS_FAILED,
    TEST_STATUS_PASS_WITH_WARNINGS,
    TEST_STATUS_PASSED,
    TEST_STATUS_TESTS_NOT_FOUND,
    run_test_agent,
)
from migration_factory.agents.transformation_agent import run_transformation_agent
from migration_factory.agents.transformation_agent.agent import TransformationAgentError
from migration_factory.agents.transformation_agent.execution_plan import (
    TRANSFORMATION_DIR_NAME,
    TransformationExecutionPlanError,
    write_transformation_execution_plan,
)
from migration_factory.agents.transformation_agent.pom_patches import patch_spring_boot_version
from migration_factory.agents.transformation_agent.plan import MigrationPlan, MigrationPlanError, load_migration_plan
from migration_factory.agents.transformation_agent.rewrite import (
    DEFAULT_OPENREWRITE_MAVEN_PLUGIN_VERSION,
    OPENREWRITE_MAVEN_PLUGIN,
    RewritePluginError,
)
from migration_factory.agents.transformation_agent.workspace import (
    TransformationWorkspaceError,
    prepare_sandbox_workspace,
)
from migration_factory.approval import (
    ApprovalArtifactError,
    check_approval_decision,
    check_approved_plan_lock,
    read_approval_decision,
)
from migration_factory.contracts.migration import LedgerError, LedgerStatus, load_ledger, save_ledger
from migration_factory.dependency_policy import (
    apply_policy_patches_if_enabled,
    invoke_dependency_copilot_advisory,
    scan_dependency_policy,
    write_dependency_policy_artifacts,
)
from migration_factory.dependency_policy.scanner import load_target_plan
from migration_factory.orchestrator.timing import record_command_duration, record_phase_duration, write_timing_artifacts


STATUS_APPROVED = "APPROVED_FOR_TRANSFORM"
STATUS_SANDBOX = "SANDBOX_PREPARED"
STATUS_RUNNING = "TRANSFORM_RUNNING"
STATUS_AWAITING_BUILD_AGENT = "TRANSFORM_AWAITING_BUILD_AGENT"
STATUS_APPLIED = "TRANSFORM_APPLIED_IN_SANDBOX"
STATUS_FAILED = "TRANSFORM_FAILED_IN_SANDBOX"
STATUS_BUILD_REQUIRED = "BUILD_VALIDATION_REQUIRED"
STATUS_BUILD_RUNNING = "BUILD_RUNNING_IN_SANDBOX"
STATUS_BUILD_PASSED = "BUILD_PASSED_IN_SANDBOX"
STATUS_BUILD_FAILED = "BUILD_FAILED_IN_SANDBOX"
STATUS_TEST_PASSED = TEST_STATUS_PASSED
STATUS_TEST_FAILED = TEST_STATUS_FAILED
STATUS_TEST_ERROR = TEST_STATUS_ERROR
STATUS_TEST_PASS_WITH_WARNINGS = TEST_STATUS_PASS_WITH_WARNINGS
STATUS_TESTS_NOT_FOUND = TEST_STATUS_TESTS_NOT_FOUND
STATUS_APPROVAL_FAILED = "APPROVAL_FAILED"

_T = TypeVar("_T")


class TransformV1AfterApprovalError(ValueError):
    pass


@dataclass(frozen=True)
class TransformSandboxResult:
    exit_code: int
    status: str
    message: str
    sandbox_path: Path | None
    log_file: Path
    generated_plan: Path | None = None
    plugin_xml: Path | None = None
    ledger_file: Path | None = None
    build_status: str | None = None
    test_status: str | None = None
    test_totals: dict[str, int] | None = None
    test_report_path: Path | None = None
    test_summary_path: Path | None = None
    test_log_path: Path | None = None
    test_phase: str | None = None
    dependency_policy_report_path: Path | None = None
    dependency_policy_summary_path: Path | None = None
    dependency_policy_status: str = ""
    dependency_policy_risks_count: int = 0
    dependency_policy_blockers_count: int = 0
    copilot_dependency_advisory_status: str = "SKIPPED"
    policy_patch_applied: bool = False
    dependency_policy_artifact_refs: dict[str, str] | None = None
    validation_execution_context: dict[str, Any] | None = None


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    result = apply_approved_sandbox_transform(
        run_dir=Path(args.run_dir),
        legacy_app=Path(args.legacy_app),
        modernized_app=Path(args.modernized_app),
        ai_hub=args.ai_hub,
        profile=args.profile,
        approved_by=args.approved_by,
        quiet=args.quiet,
        log_file=Path(args.log_file) if args.log_file else None,
        build_timeout_seconds=args.build_timeout,
        status_writer=print,
        error_writer=lambda line: print(line, file=sys.stderr),
    )
    return result.exit_code


def apply_approved_sandbox_transform(
    *,
    run_dir: Path,
    legacy_app: Path,
    modernized_app: Path,
    ai_hub: str,
    profile: str,
    approved_by: str,
    quiet: bool = True,
    log_file: Path | None = None,
    build_timeout_seconds: int | None = None,
    status_writer: Callable[[str], None] | None = print,
    error_writer: Callable[[str], None] | None = None,
) -> TransformSandboxResult:
    run_dir = Path(run_dir).expanduser().resolve()
    resolved_log_file = _resolve_log_file(run_dir, str(log_file) if log_file else None)
    verbose = not quiet
    emit = status_writer or (lambda line: None)

    try:
        modernized_app = Path(modernized_app).expanduser().resolve()
        legacy_app = Path(legacy_app).expanduser().resolve()
        try:
            run_id = _ensure_approved_for_transform(run_dir, approved_by=approved_by)
        except (ApprovalArtifactError, TransformV1AfterApprovalError) as exc:
            emit(STATUS_APPROVAL_FAILED)
            _print_failure_details(exc, resolved_log_file, error_writer=error_writer)
            return TransformSandboxResult(
                exit_code=1,
                status=STATUS_APPROVAL_FAILED,
                message=str(exc),
                sandbox_path=None,
                log_file=resolved_log_file,
            )
        _ensure_run_dir_matches_modernized_app(run_dir, modernized_app, run_id)
        emit(STATUS_APPROVED)
        _ensure_profile_allows_sandbox_transform(ai_hub, profile)

        generated_plan = write_transformation_execution_plan(modernized_app, run_id)
        plugin_xml = _write_openrewrite_plugin_xml(run_dir, ai_hub, profile)
        _apply_openrewrite_apply_settings(generated_plan, ai_hub, profile)
        jdk_env = _profile_jdk_env(ai_hub, profile)
        sandbox_copy_started = time.monotonic()
        sandbox = prepare_sandbox_workspace(
            legacy_app_path=legacy_app,
            modernized_app_path=modernized_app,
            run_dir=run_dir,
        )
        _record_transform_phase_timing(
            run_dir,
            phase="sandbox_copy",
            duration_seconds=time.monotonic() - sandbox_copy_started,
        )
        _force_plan_target(generated_plan, sandbox.path)
        emit(STATUS_SANDBOX)

        plan = load_migration_plan(generated_plan, sandbox.path)
        return _run_transformer_with_build_validation(
            sandbox_path=sandbox.path,
            plugin_xml=plugin_xml,
            generated_plan=generated_plan,
            plan=plan,
            run_id=run_id,
            run_dir=run_dir,
            log_file=resolved_log_file,
            build_timeout_seconds=build_timeout_seconds,
            verbose=verbose,
            status_writer=emit,
            error_writer=error_writer,
            jdk_env=jdk_env,
        )
    except ApprovalArtifactError as exc:
        emit(STATUS_APPROVAL_FAILED)
        _print_failure_details(exc, resolved_log_file, error_writer=error_writer)
        return TransformSandboxResult(1, STATUS_APPROVAL_FAILED, str(exc), None, resolved_log_file)
    except (
        MigrationPlanError,
        RewritePluginError,
        TransformationAgentError,
        TransformationExecutionPlanError,
        TransformationWorkspaceError,
        TransformV1AfterApprovalError,
    ) as exc:
        emit(STATUS_FAILED)
        _print_failure_details(exc, resolved_log_file, error_writer=error_writer)
        _write_partial_timing_artifacts(run_dir)
        return TransformSandboxResult(1, STATUS_FAILED, str(exc), None, resolved_log_file)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transform-v1-after-approval",
        description="Apply the V1 Transformer in a sandbox after human approval.",
    )
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--legacy-app", required=True)
    parser.add_argument("--modernized-app", required=True)
    parser.add_argument("--ai-hub", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--approved-by", required=True)
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--quiet",
        dest="quiet",
        action="store_true",
        default=True,
        help="Keep subprocess output out of the terminal. This is the default.",
    )
    output_group.add_argument(
        "--verbose",
        dest="quiet",
        action="store_false",
        help="Stream full subprocess output to the terminal while also writing the log file.",
    )
    parser.add_argument("--log-file", help="Path for full Phase 2 subprocess output")
    parser.add_argument(
        "--build-timeout",
        type=_positive_int,
        help="Override Build Agent timeout in seconds for sandbox build validation",
    )
    return parser


def _run_transformer_with_build_validation(
    *,
    sandbox_path: Path,
    plugin_xml: Path,
    generated_plan: Path,
    plan: MigrationPlan,
    run_id: str,
    run_dir: Path,
    log_file: Path,
    build_timeout_seconds: int | None,
    verbose: bool,
    status_writer: Callable[[str], None],
    error_writer: Callable[[str], None] | None,
    jdk_env: dict[str, str] | None = None,
) -> TransformSandboxResult:
    next_unit: str | None = None
    awaited_units: set[str] = set()
    source_units_completed: set[str] = set()
    source_unit_ids = _source_changing_unit_ids(plan)
    max_transformer_runs = len(plan.units) + 1
    build_status: str | None = None
    validation_execution_context: dict[str, Any] | None = None

    for _ in range(max_transformer_runs):
        status_writer(STATUS_RUNNING)
        result = _run_with_logged_output(
            lambda: run_transformation_agent(
                sandbox_path,
                plugin_xml,
                generated_plan,
                start_unit=next_unit,
                dry_run=False,
                stream_output=True,
                wait_for_continue=False,
            ),
            log_file=log_file,
            verbose=verbose,
        )
        if verbose:
            status_writer(f"Ledger: {result.ledger_file}")
            status_writer(f"Transformer status: {result.status}")

        if result.status == LedgerStatus.AWAITING_BUILD_AGENT:
            if verbose:
                status_writer(STATUS_AWAITING_BUILD_AGENT)
            ledger = load_ledger(result.ledger_file)
            unit_id = _awaiting_build_unit_id(ledger)
            _record_transform_unit_timings(run_dir, ledger, unit_id)
            if unit_id in awaited_units:
                raise TransformV1AfterApprovalError(
                    f"Transformer resumed to the same build-pending unit twice: {unit_id}"
                )
            awaited_units.add(unit_id)

            if verbose:
                status_writer(STATUS_BUILD_REQUIRED)
            status_writer(STATUS_BUILD_RUNNING)
            build_kwargs: dict[str, Any] = {
                "project_path": sandbox_path,
                "ledger_file": result.ledger_file,
                "output_dir": run_dir / "build",
                "stream_output": True,
                "validation_unit_id": unit_id,
                "source_changing_unit": unit_id in source_unit_ids,
                "validation_command": _validation_command_for_unit(plan, unit_id),
            }
            validation_execution_context = {
                "job_id": run_id,
                "run_dir": str(run_dir),
                "sandbox_path": str(sandbox_path),
                "validation_unit_id": unit_id,
                "validation_command": build_kwargs["validation_command"] or (),
                "source_changing_unit": unit_id in source_unit_ids,
                "source_jdk_home_env": (jdk_env or {}).get("source_jdk_home_env"),
                "target_jdk_home_env": (jdk_env or {}).get("target_jdk_home_env"),
                "build_timeout_seconds": build_timeout_seconds,
                "stop_after_start": True,
                "require_test_reports": False,
                "working_directory": str(sandbox_path),
            }
            if jdk_env:
                build_kwargs.update(jdk_env)
            if build_timeout_seconds is not None:
                build_kwargs["timeout_seconds"] = build_timeout_seconds
            build_result = _run_with_logged_output(
                lambda: run_build_agent(**build_kwargs),
                log_file=log_file,
                verbose=verbose,
            )
            if build_result.command:
                validation_execution_context["validation_command"] = list(build_result.command)
            if build_result.cwd is not None:
                validation_execution_context["working_directory"] = str(build_result.cwd)
            if build_result.command:
                _record_transform_command_timing(
                    run_dir,
                    label=f"build_validation:{unit_id}",
                    duration_seconds=build_result.command_duration_seconds or 0.0,
                    command=build_result.command,
                    cwd=str(build_result.cwd) if build_result.cwd is not None else None,
                )
            if not build_result.succeeded:
                build_status = STATUS_BUILD_FAILED
                status_writer(STATUS_BUILD_FAILED)
                message = _build_failure_message(build_result)
                _print_failure_details(
                    TransformV1AfterApprovalError(message),
                    log_file,
                    error_writer=error_writer,
                )
                _write_partial_timing_artifacts(run_dir)
                return TransformSandboxResult(
                    exit_code=1,
                    status=STATUS_BUILD_FAILED,
                    message=message,
                    sandbox_path=sandbox_path,
                    log_file=log_file,
                    generated_plan=generated_plan,
                    plugin_xml=plugin_xml,
                    ledger_file=result.ledger_file,
                    build_status=build_status,
                    validation_execution_context=validation_execution_context,
                )

            build_status = STATUS_BUILD_PASSED
            status_writer(STATUS_BUILD_PASSED)
            if verbose:
                status_writer(f"Build validated unit: {unit_id}")
            if unit_id in source_unit_ids:
                source_units_completed.add(unit_id)

            next_unit = _next_unit_after(plan, unit_id)
            if next_unit is None:
                return _finalize_with_test_validation(
                    sandbox_path=sandbox_path,
                    run_dir=run_dir,
                    run_id=run_id,
                    log_file=log_file,
                    generated_plan=generated_plan,
                    plugin_xml=plugin_xml,
                    ledger_file=result.ledger_file,
                    build_status=build_status,
                    status_writer=status_writer,
                    validation_execution_context=validation_execution_context,
                )
            continue

        if result.blocked_unit or result.status == LedgerStatus.BLOCKED:
            status_writer(STATUS_FAILED)
            if result.blocked_unit:
                message = f"Blocked unit: {result.blocked_unit}"
                _print_failure_details(
                    TransformV1AfterApprovalError(message),
                    log_file,
                    error_writer=error_writer,
                )
            else:
                message = "Transformer blocked"
                _print_failure_details(
                    TransformV1AfterApprovalError(message),
                    log_file,
                    error_writer=error_writer,
                )
            _write_partial_timing_artifacts(run_dir)
            return TransformSandboxResult(
                exit_code=1,
                status=STATUS_FAILED,
                message=message,
                sandbox_path=sandbox_path,
                log_file=log_file,
                generated_plan=generated_plan,
                plugin_xml=plugin_xml,
                ledger_file=result.ledger_file,
                build_status=build_status,
            )

        if result.status == LedgerStatus.COMPLETED:
            return _finalize_with_test_validation(
                sandbox_path=sandbox_path,
                run_dir=run_dir,
                run_id=run_id,
                log_file=log_file,
                generated_plan=generated_plan,
                plugin_xml=plugin_xml,
                ledger_file=result.ledger_file,
                build_status=build_status,
                status_writer=status_writer,
                validation_execution_context=validation_execution_context,
            )

        raise TransformV1AfterApprovalError(f"Unexpected Transformer status: {result.status}")

    raise TransformV1AfterApprovalError(
        f"Transformer resume loop exceeded {max_transformer_runs} runs for {len(plan.units)} units"
    )


class _OutputTee:
    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, text: str) -> int:
        for stream in self._streams:
            stream.write(text)
        return len(text)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


def _run_with_logged_output(callback: Callable[[], _T], *, log_file: Path, verbose: bool) -> _T:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as log_stream:
        stdout: TextIO = _OutputTee(sys.stdout, log_stream) if verbose else log_stream
        stderr: TextIO = _OutputTee(sys.stderr, log_stream) if verbose else log_stream
        with redirect_stdout(stdout), redirect_stderr(stderr):
            return callback()


def _resolve_log_file(run_dir: Path, log_file: str | None) -> Path:
    if log_file:
        return Path(log_file).expanduser().resolve()
    return run_dir / "logs" / "phase2_transform.log"


def _print_failure_details(
    exc: Exception,
    log_file: Path,
    *,
    error_writer: Callable[[str], None] | None = None,
) -> None:
    emit = error_writer or (lambda line: print(line, file=sys.stderr))
    emit(f"ERROR: {exc}")
    emit(f"log_file: {log_file}")
    _print_log_tail(log_file, error_writer=emit)


def _print_log_tail(
    log_file: Path,
    *,
    line_count: int = 30,
    error_writer: Callable[[str], None] | None = None,
) -> None:
    emit = error_writer or (lambda line: print(line, file=sys.stderr))
    if not log_file.is_file():
        emit("No log output captured.")
        return

    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        emit("No log output captured.")
        return

    emit(f"--- Last {min(line_count, len(lines))} log lines ---")
    for line in lines[-line_count:]:
        emit(line)


def _build_failure_message(build_result: Any) -> str:
    parts = [
        f"Build result kind: {build_result.result_kind}",
        f"Build message: {build_result.message}",
    ]
    if build_result.error_contract_path:
        parts.append(f"Build error contract: {build_result.error_contract_path}")
    return "; ".join(parts)


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _finalize_with_test_validation(
    *,
    sandbox_path: Path,
    run_dir: Path,
    run_id: str,
    log_file: Path,
    generated_plan: Path,
    plugin_xml: Path,
    ledger_file: Path,
    build_status: str | None,
    status_writer: Callable[[str], None],
    validation_execution_context: dict[str, Any] | None = None,
) -> TransformSandboxResult:
    dependency_policy = _run_dependency_policy_layer(
        sandbox_path=sandbox_path,
        run_dir=run_dir,
        build_passed=build_status == STATUS_BUILD_PASSED,
    )
    command, cwd = _build_command_and_cwd(ledger_file)
    build_exit_code = _build_exit_code(ledger_file)
    test_result = run_test_agent(
        sandbox_path=sandbox_path,
        run_dir=run_dir,
        run_id=run_id,
        source_log_path=log_file,
        command=command,
        cwd=cwd,
        build_status=build_status,
        build_exit_code=build_exit_code,
        require_test_reports=False,
    )
    _record_ledger_test_validation(
        ledger_file=ledger_file,
        test_status=test_result.test_status,
        totals=test_result.totals,
        report_path=test_result.report_path,
        summary_path=test_result.summary_path,
        log_path=test_result.log_path,
        parse_duration_seconds=test_result.parse_duration_seconds,
    )
    _record_transform_phase_timing(
        run_dir,
        phase="test_parse",
        duration_seconds=test_result.parse_duration_seconds,
    )

    if test_result.test_status in {STATUS_TEST_PASSED, STATUS_TEST_PASS_WITH_WARNINGS}:
        _write_partial_timing_artifacts(run_dir)
        status_writer(STATUS_APPLIED)
        status_writer("Sandbox migration candidate ready.")
        return TransformSandboxResult(
            exit_code=0,
            status=STATUS_APPLIED,
            message="Sandbox migration candidate ready.",
            sandbox_path=sandbox_path,
            log_file=log_file,
            generated_plan=generated_plan,
            plugin_xml=plugin_xml,
            ledger_file=ledger_file,
            build_status=build_status,
            test_status=test_result.test_status,
            test_totals=test_result.totals,
            test_report_path=test_result.report_path,
            test_summary_path=test_result.summary_path,
            test_log_path=test_result.log_path,
            test_phase="post_transform",
            dependency_policy_report_path=dependency_policy.get("dependency_policy_report_path"),
            dependency_policy_summary_path=dependency_policy.get("dependency_policy_summary_path"),
            dependency_policy_status=str(dependency_policy.get("dependency_policy_status") or ""),
            dependency_policy_risks_count=int(dependency_policy.get("dependency_policy_risks_count") or 0),
            dependency_policy_blockers_count=int(dependency_policy.get("dependency_policy_blockers_count") or 0),
            copilot_dependency_advisory_status=str(dependency_policy.get("copilot_dependency_advisory_status") or "SKIPPED"),
            policy_patch_applied=bool(dependency_policy.get("policy_patch_applied", False)),
            dependency_policy_artifact_refs=dict(dependency_policy.get("artifact_refs", {}) or {}),
            validation_execution_context=validation_execution_context,
        )

    status_writer(test_result.test_status)
    _write_partial_timing_artifacts(run_dir)
    return TransformSandboxResult(
        exit_code=1,
        status=test_result.test_status,
        message=f"Sandbox candidate blocked by test_status={test_result.test_status}.",
        sandbox_path=sandbox_path,
        log_file=log_file,
        generated_plan=generated_plan,
        plugin_xml=plugin_xml,
        ledger_file=ledger_file,
        build_status=build_status,
        test_status=test_result.test_status,
        test_totals=test_result.totals,
        test_report_path=test_result.report_path,
        test_summary_path=test_result.summary_path,
        test_log_path=test_result.log_path,
        test_phase="post_transform",
        dependency_policy_report_path=dependency_policy.get("dependency_policy_report_path"),
        dependency_policy_summary_path=dependency_policy.get("dependency_policy_summary_path"),
        dependency_policy_status=str(dependency_policy.get("dependency_policy_status") or ""),
        dependency_policy_risks_count=int(dependency_policy.get("dependency_policy_risks_count") or 0),
        dependency_policy_blockers_count=int(dependency_policy.get("dependency_policy_blockers_count") or 0),
        copilot_dependency_advisory_status=str(dependency_policy.get("copilot_dependency_advisory_status") or "SKIPPED"),
        policy_patch_applied=bool(dependency_policy.get("policy_patch_applied", False)),
        dependency_policy_artifact_refs=dict(dependency_policy.get("artifact_refs", {}) or {}),
        validation_execution_context=validation_execution_context,
    )


def _run_dependency_policy_layer(
    *,
    sandbox_path: Path,
    run_dir: Path,
    build_passed: bool,
) -> dict[str, Any]:
    target_plan = load_target_plan(run_dir)
    report = scan_dependency_policy(
        sandbox_path=sandbox_path,
        target_plan=target_plan,
        build_passed=build_passed,
    )
    refs = {
        key: str(path)
        for key, path in write_dependency_policy_artifacts(run_dir=run_dir, report=report).items()
    }
    patch_refs = apply_policy_patches_if_enabled(
        sandbox_path=sandbox_path,
        run_dir=run_dir,
        report=report,
        target_plan=target_plan,
    )
    refs.update({key: str(path) for key, path in patch_refs.items() if isinstance(path, Path)})
    policy_patch_applied = bool(patch_refs.get("policy_patch_applied", False))
    if policy_patch_applied:
        report = scan_dependency_policy(
            sandbox_path=sandbox_path,
            target_plan=target_plan,
            build_passed=build_passed,
        )
        refs.update(
            {
                key: str(path)
                for key, path in write_dependency_policy_artifacts(run_dir=run_dir, report=report).items()
            }
        )

    copilot_status = "SKIPPED"
    if report.copilot_advisory_required and target_plan.get("copilot_advisory_enabled", True):
        advisory = invoke_dependency_copilot_advisory(
            run_dir=run_dir,
            sandbox_path=sandbox_path,
            target_plan=target_plan,
            policy_report=report,
        )
        copilot_status = str(advisory.get("status") or "FALLBACK")
        refs.update(dict(advisory.get("artifact_refs", {}) or {}))

    blockers = [
        risk for risk in report.risks if risk.severity == "BLOCKER" or risk.blocks_v1_build_test
    ]
    return {
        "artifact_refs": refs,
        "dependency_policy_report_path": Path(refs["dependency_policy_report"]),
        "dependency_policy_summary_path": Path(refs["dependency_policy_summary"]),
        "dependency_policy_status": report.status,
        "dependency_policy_risks_count": len(report.risks),
        "dependency_policy_blockers_count": len(blockers),
        "copilot_dependency_advisory_status": copilot_status,
        "policy_patch_applied": policy_patch_applied,
    }


def _build_command_and_cwd(ledger_file: Path) -> tuple[list[str], str | None]:
    try:
        ledger = load_ledger(ledger_file)
    except LedgerError:
        return [], None
    validation = ledger.get("build_validation", {})
    command = validation.get("command")
    cwd = validation.get("cwd")
    return (list(command) if isinstance(command, list) else []), str(cwd) if cwd else None


def _build_exit_code(ledger_file: Path) -> int | None:
    try:
        ledger = load_ledger(ledger_file)
    except LedgerError:
        return None
    validation = ledger.get("build_validation", {})
    exit_code = validation.get("exit_code")
    return exit_code if isinstance(exit_code, int) else None


def _record_ledger_test_validation(
    *,
    ledger_file: Path,
    test_status: str,
    totals: dict[str, int],
    report_path: Path,
    summary_path: Path,
    log_path: Path,
    parse_duration_seconds: float,
) -> None:
    try:
        ledger = load_ledger(ledger_file)
    except LedgerError:
        return
    ledger["test_validation"] = {
        "status": test_status,
        "phase": "post_transform",
        "execution_owner": "build-agent",
        "execution_mode": "parse_existing_surefire",
        "totals": totals,
        "report_path": str(report_path),
        "summary_path": str(summary_path),
        "log_path": str(log_path),
        "parse_duration_seconds": round(float(parse_duration_seconds), 6),
    }
    save_ledger(ledger_file, ledger)


def _record_transform_phase_timing(run_dir: Path, *, phase: str, duration_seconds: float) -> None:
    state = {"run_dir": str(run_dir), "run_id": "", "timing": {"phase_durations_seconds": {}}}
    record_phase_duration(state, phase=phase, duration_seconds=duration_seconds)
    _merge_timing_into_artifact_state(run_dir, state)


def _record_transform_command_timing(
    run_dir: Path,
    *,
    label: str,
    duration_seconds: float,
    command: list[str],
    cwd: str | None,
) -> None:
    state = {"run_dir": str(run_dir), "run_id": "", "timing": {"commands": []}}
    record_command_duration(state, label=label, duration_seconds=duration_seconds, command=command, cwd=cwd)
    _merge_timing_into_artifact_state(run_dir, state)


def _record_transform_unit_timings(run_dir: Path, ledger: dict[str, Any], unit_id: str) -> None:
    units = ledger.get("units")
    if not isinstance(units, dict):
        return
    unit = units.get(unit_id)
    if not isinstance(unit, dict):
        return

    unit_duration = unit.get("unit_duration_seconds")
    if isinstance(unit_duration, (int, float)):
        _record_transform_phase_timing(
            run_dir,
            phase=f"transform_unit:{unit_id}",
            duration_seconds=float(unit_duration),
        )

    for row in list(unit.get("commands", []) or []):
        if not isinstance(row, dict):
            continue
        duration = row.get("duration_seconds")
        command = str(row.get("command") or "")
        if not isinstance(duration, (int, float)):
            continue
        _record_transform_command_timing(
            run_dir,
            label=f"openrewrite:{unit_id}",
            duration_seconds=float(duration),
            command=[command] if command else [],
            cwd=None,
        )


def _write_partial_timing_artifacts(run_dir: Path) -> None:
    state = _load_existing_timing_state(run_dir)
    if not state:
        return
    write_timing_artifacts(state)


def _merge_timing_into_artifact_state(run_dir: Path, partial_state: dict[str, Any]) -> None:
    state = _load_existing_timing_state(run_dir)
    if state is None:
        state = {"run_dir": str(run_dir), "run_id": "", "timing": {}}
    timing = dict(state.get("timing", {}) or {})
    incoming = dict(partial_state.get("timing", {}) or {})

    phase_durations = dict(timing.get("phase_durations_seconds", {}) or {})
    phase_durations.update(dict(incoming.get("phase_durations_seconds", {}) or {}))
    timing["phase_durations_seconds"] = phase_durations

    existing_commands = list(timing.get("commands", []) or [])
    existing_commands.extend(list(incoming.get("commands", []) or []))
    timing["commands"] = existing_commands

    state["timing"] = timing
    _write_timing_state(run_dir, state)


def _load_existing_timing_state(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "performance" / "timing_state.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_timing_state(run_dir: Path, state: dict[str, Any]) -> None:
    path = run_dir / "performance" / "timing_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _awaiting_build_unit_id(ledger: dict[str, Any]) -> str:
    validation = ledger.get("build_validation", {})
    unit_id = validation.get("unit_id") or ledger.get("current_unit")
    if not unit_id:
        raise TransformV1AfterApprovalError("Transformer is awaiting build validation but ledger has no unit_id")
    return str(unit_id)


def _next_unit_after(plan: MigrationPlan, unit_id: str) -> str | None:
    unit_ids = [unit.id for unit in plan.units]
    try:
        index = unit_ids.index(unit_id)
    except ValueError as exc:
        raise TransformV1AfterApprovalError(f"Build-pending unit is not in plan: {unit_id}") from exc
    next_index = index + 1
    if next_index >= len(unit_ids):
        return None
    return unit_ids[next_index]


def _source_changing_unit_ids(plan: MigrationPlan) -> set[str]:
    source_changing_types = {"openrewrite"}
    return {
        unit.id
        for unit in plan.units
        if any(str(transformation.get("type")) in source_changing_types for transformation in unit.transformations)
    }


def _validation_command_for_unit(plan: MigrationPlan, unit_id: str) -> Any | None:
    for unit in plan.units:
        if unit.id != unit_id:
            continue

        build_validation = unit.raw.get("build_validation")
        if isinstance(build_validation, dict) and build_validation.get("command"):
            return build_validation["command"]

        for check in unit.checks:
            if isinstance(check, dict) and check.get("command"):
                return check["command"]
        break

    build_validation = plan.raw.get("build_validation")
    if isinstance(build_validation, dict) and build_validation.get("command"):
        return build_validation["command"]


def _profile_jdk_env(ai_hub: str, profile: str) -> dict[str, str]:
    profile_path = Path(ai_hub).expanduser().resolve() / "profiles" / f"{profile}.yaml"
    if not profile_path.is_file():
        return {}
    payload = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return {}
    return {
        key: str(payload[key])
        for key in ("source_jdk_home_env", "target_jdk_home_env")
        if payload.get(key)
    }


def _ensure_approved_for_transform(run_dir: Path, *, approved_by: str) -> str:
    decision_errors = check_approval_decision(run_dir)
    if decision_errors:
        raise TransformV1AfterApprovalError("; ".join(decision_errors))

    decision = read_approval_decision(run_dir)
    run_id = str(decision.get("run_id") or "")
    if not run_id:
        raise TransformV1AfterApprovalError("approval_decision.json missing run_id")
    if decision.get("decision") != "approved":
        raise TransformV1AfterApprovalError(
            f"approval_decision.json decision must be approved, got {decision.get('decision')!r}"
        )
    if decision.get("decided_by") != approved_by:
        raise TransformV1AfterApprovalError(
            f"approval_decision.json decided_by must match --approved-by {approved_by!r}"
        )

    lock_errors = check_approved_plan_lock(run_dir, expected_run_id=run_id)
    if lock_errors:
        raise TransformV1AfterApprovalError("; ".join(lock_errors))
    return run_id


def _ensure_run_dir_matches_modernized_app(run_dir: Path, modernized_app: Path, run_id: str) -> None:
    expected = modernized_app / ".migration" / "runs" / run_id
    if run_dir != expected.resolve():
        raise TransformV1AfterApprovalError(
            f"--run-dir must match --modernized-app .migration/runs/{run_id}: {expected}"
        )


def _force_plan_target(plan_path: Path, sandbox_path: Path) -> None:
    payload = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise TransformV1AfterApprovalError(f"Transformer plan must be a YAML mapping: {plan_path}")
    workspaces = payload.setdefault("workspaces", {})
    if not isinstance(workspaces, dict):
        raise TransformV1AfterApprovalError("Transformer plan workspaces must be a mapping")
    target = workspaces.setdefault("target", {})
    if not isinstance(target, dict):
        raise TransformV1AfterApprovalError("Transformer plan workspaces.target must be a mapping")

    target["path"] = str(sandbox_path.resolve())
    plan_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _apply_openrewrite_apply_settings(plan_path: Path, ai_hub: str, profile: str) -> None:
    settings = _load_openrewrite_apply_settings(ai_hub, profile)
    if not settings:
        return

    payload = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise TransformV1AfterApprovalError(f"Transformer plan must be a YAML mapping: {plan_path}")
    units = payload.get("migration_units")
    if not isinstance(units, list):
        return

    for unit in units:
        if not isinstance(unit, dict):
            continue
        transformations = unit.get("transformations", []) or []
        if not isinstance(transformations, list):
            continue
        updated_transformations: list[Any] = []
        for transformation in transformations:
            updated_transformations.append(transformation)
            if not isinstance(transformation, dict) or transformation.get("type") != "openrewrite":
                continue
            if settings.get("apply_goal"):
                transformation["apply_goal"] = settings["apply_goal"]
            if settings.get("apply_maven_args"):
                transformation["apply_maven_args"] = settings["apply_maven_args"]
            post_apply_patches = settings.get("post_openrewrite_patches") or settings.get("post_apply_patches")
            if isinstance(post_apply_patches, list):
                for patch in post_apply_patches:
                    if isinstance(patch, dict):
                        updated_transformations.append(dict(patch))
        unit["transformations"] = updated_transformations

    plan_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _load_openrewrite_apply_settings(ai_hub: str, profile: str) -> dict[str, Any]:
    hub_path = Path(ai_hub).expanduser().resolve()
    profile_path = hub_path / "profiles" / f"{profile}.yaml"
    if not profile_path.is_file():
        raise TransformV1AfterApprovalError(f"AI Hub profile not found: {profile_path}")

    profile_payload = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    if not isinstance(profile_payload, dict):
        return {}
    profile_openrewrite = profile_payload.get("openrewrite") if isinstance(profile_payload.get("openrewrite"), dict) else {}
    catalog = _load_ai_hub_openrewrite_catalog_payload(hub_path, profile_payload)

    settings: dict[str, Any] = {}
    apply_goal = profile_openrewrite.get("apply_goal") or catalog.get("apply_goal")
    if apply_goal:
        settings["apply_goal"] = str(apply_goal)
    apply_maven_args = profile_openrewrite.get("apply_maven_args")
    if apply_maven_args is None:
        apply_maven_args = catalog.get("apply_maven_args")
    args = [str(item) for item in _as_list(apply_maven_args)]
    if args:
        settings["apply_maven_args"] = args
    post_apply_patches = profile_openrewrite.get("post_openrewrite_patches")
    if post_apply_patches is None:
        post_apply_patches = profile_openrewrite.get("post_apply_patches")
    if post_apply_patches is None:
        post_apply_patches = catalog.get("post_openrewrite_patches")
    if post_apply_patches is None:
        post_apply_patches = catalog.get("post_apply_patches")
    patches = [item for item in _as_list(post_apply_patches) if isinstance(item, dict)]
    if patches:
        settings["post_openrewrite_patches"] = patches
    return settings


def _ensure_profile_allows_sandbox_transform(ai_hub: str, profile: str) -> None:
    profile_payload = _load_profile_payload(ai_hub, profile)
    blockers = _profile_transform_blockers(profile_payload)
    if not blockers:
        return
    if _safe_transform_override_enabled(profile_payload):
        return
    raise TransformV1AfterApprovalError(
        "Profile guardrails block sandbox source-changing transformation: "
        + "; ".join(blockers)
    )


def _profile_transform_blockers(profile_payload: dict[str, Any]) -> list[str]:
    rules = profile_payload.get("rules") if isinstance(profile_payload.get("rules"), dict) else {}
    openrewrite = (
        profile_payload.get("openrewrite") if isinstance(profile_payload.get("openrewrite"), dict) else {}
    )
    catalog = {}
    try:
        catalog = _load_ai_hub_openrewrite_catalog_payload(
            Path(str(profile_payload.get("_ai_hub_path") or "")).resolve(),
            profile_payload,
        )
    except TransformV1AfterApprovalError:
        catalog = {}

    blockers: list[str] = []
    if profile_payload.get("production_allowed") is False and profile_payload.get("sandbox_transform_allowed") is not True:
        blockers.append("production_allowed=false")
    if profile_payload.get("dry_run_only") is True or rules.get("dry_run_only") is True:
        blockers.append("dry_run_only=true")
    if openrewrite.get("apply_allowed") is False or rules.get("openrewrite_apply_allowed") is False:
        blockers.append("openrewrite.apply_allowed=false")
    if catalog.get("dry_run_only") is True or catalog.get("rewrite_run_allowed") is False:
        blockers.append("openrewrite catalog is preview-only")
    return blockers


def _safe_transform_override_enabled(profile_payload: dict[str, Any]) -> bool:
    if profile_payload.get("sandbox_transform_allowed") is not True:
        return False
    if os.getenv("AI_MIGRATION_ALLOW_GUARDED_SANDBOX_TRANSFORM", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    return True


def _load_profile_payload(ai_hub: str, profile: str) -> dict[str, Any]:
    profile_path = Path(ai_hub).expanduser().resolve() / "profiles" / f"{profile}.yaml"
    if not profile_path.is_file():
        raise TransformV1AfterApprovalError(f"AI Hub profile not found: {profile_path}")
    payload = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return {}
    payload["_ai_hub_path"] = str(Path(ai_hub).expanduser().resolve())
    return payload


def _write_openrewrite_plugin_xml(run_dir: Path, ai_hub: str, profile: str) -> Path:
    source = _load_rewrite_plugin_source(run_dir, ai_hub, profile)
    plugin = _coordinate(source["plugin"], "plugin")
    plugin = _concrete_openrewrite_plugin(plugin)
    recipe_artifacts = [_coordinate(item, "recipe_artifacts") for item in _as_list(source.get("recipe_artifacts"))]

    plugin_xml = _plugin_xml(plugin, recipe_artifacts)
    output_path = run_dir / TRANSFORMATION_DIR_NAME / "openrewrite-plugin.xml"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(plugin_xml, encoding="utf-8")
    return output_path


def _load_rewrite_plugin_source(run_dir: Path, ai_hub: str, profile: str) -> dict[str, Any]:
    rewrite_plan_path = run_dir / "analysis" / "rewrite_plugin_plan.json"
    if rewrite_plan_path.is_file():
        try:
            payload = json.loads(rewrite_plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TransformV1AfterApprovalError(f"Invalid JSON artifact {rewrite_plan_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise TransformV1AfterApprovalError(f"Artifact must be a JSON object: {rewrite_plan_path}")
        if payload.get("plugin"):
            return payload

    catalog = _load_ai_hub_openrewrite_catalog(ai_hub, profile)
    if catalog.get("plugin"):
        return catalog
    raise TransformV1AfterApprovalError("OpenRewrite plugin coordinate not found in run artifacts or AI Hub")


def _load_ai_hub_openrewrite_catalog(ai_hub: str, profile: str) -> dict[str, Any]:
    hub_path = Path(ai_hub).expanduser().resolve()
    profile_path = hub_path / "profiles" / f"{profile}.yaml"
    if not profile_path.is_file():
        raise TransformV1AfterApprovalError(f"AI Hub profile not found: {profile_path}")

    profile_payload = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    catalog = _load_ai_hub_openrewrite_catalog_payload(hub_path, profile_payload)
    try:
        plugin = _coord_from_mapping(catalog["plugin"])
    except (KeyError, TypeError) as exc:
        raise TransformV1AfterApprovalError(f"Invalid OpenRewrite catalog plugin: {exc}") from exc
    return {
        "plugin": plugin,
        "recipe_artifacts": [_coord_from_mapping(item) for item in catalog.get("recipe_artifacts", [])],
    }


def _load_ai_hub_openrewrite_catalog_payload(hub_path: Path, profile_payload: dict[str, Any]) -> dict[str, Any]:
    try:
        catalog_rel = profile_payload["openrewrite"]["catalog_path"]
    except (KeyError, TypeError) as exc:
        raise TransformV1AfterApprovalError("Profile missing openrewrite.catalog_path") from exc

    catalog_path = (hub_path / str(catalog_rel)).resolve()
    if catalog_path != hub_path and hub_path not in catalog_path.parents:
        raise TransformV1AfterApprovalError(f"Catalog path escapes AI Hub: {catalog_rel}")
    if not catalog_path.is_file():
        raise TransformV1AfterApprovalError(f"OpenRewrite catalog not found: {catalog_path}")

    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    if not isinstance(catalog, dict):
        raise TransformV1AfterApprovalError(f"OpenRewrite catalog must be a mapping: {catalog_path}")
    return catalog


def _coord_from_mapping(value: Any) -> str:
    if not isinstance(value, dict):
        raise TransformV1AfterApprovalError("coordinate must be a mapping")
    try:
        return f"{value['group_id']}:{value['artifact_id']}:{value['version']}"
    except KeyError as exc:
        raise TransformV1AfterApprovalError(f"coordinate missing {exc.args[0]}") from exc


def _coordinate(value: Any, label: str) -> tuple[str, str, str]:
    parts = str(value).split(":")
    if len(parts) != 3 or not all(parts):
        raise TransformV1AfterApprovalError(f"{label} coordinate must be groupId:artifactId:version")
    return parts[0], parts[1], parts[2]


def _concrete_openrewrite_plugin(plugin: tuple[str, str, str]) -> tuple[str, str, str]:
    group_id, artifact_id, version = plugin
    if (group_id, artifact_id) == OPENREWRITE_MAVEN_PLUGIN and version.upper() == "RELEASE":
        return group_id, artifact_id, DEFAULT_OPENREWRITE_MAVEN_PLUGIN_VERSION
    return plugin


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _plugin_xml(plugin: tuple[str, str, str], recipe_artifacts: list[tuple[str, str, str]]) -> str:
    group_id, artifact_id, version = plugin
    lines = [
        "<plugin>",
        f"  <groupId>{group_id}</groupId>",
        f"  <artifactId>{artifact_id}</artifactId>",
        f"  <version>{version}</version>",
    ]
    if recipe_artifacts:
        lines.append("  <dependencies>")
        for dep_group, dep_artifact, dep_version in recipe_artifacts:
            lines.extend(
                [
                    "    <dependency>",
                    f"      <groupId>{dep_group}</groupId>",
                    f"      <artifactId>{dep_artifact}</artifactId>",
                    f"      <version>{dep_version}</version>",
                    "    </dependency>",
                ]
            )
        lines.append("  </dependencies>")
    lines.append("</plugin>")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
