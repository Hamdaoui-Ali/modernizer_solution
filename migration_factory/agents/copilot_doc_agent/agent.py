from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import json
import subprocess
from typing import Any

import yaml

from migration_factory.copilot_cli import resolve_copilot_cli_executable


DOC_DIR_NAME = "copilot_docs"
DOC_ARTIFACTS = (
    "migration_overview.md",
    "technical_changes.md",
    "validation_evidence.md",
    "risks_and_warnings.md",
    "copilot_review.md",
)
INPUT_MANIFEST_NAME = "input_manifest.json"
CLI_STATUS_NAME = "copilot_cli_status.json"
CLI_OUTPUT_PREVIEW_LIMIT = 2000

REQUIRED_ARTIFACT_REFS: dict[str, tuple[str, ...]] = {
    "analysis_report": ("analysis_report", "analysis_report.json"),
    "migration_plan": ("migration_plan", "migration_plan.yaml"),
    "approval_decision": ("approval_decision",),
    "approved_plan_lock": ("approved_plan_lock",),
    "transformation_execution_plan": ("transformation_execution_plan",),
    "migration_ledger": ("migration_ledger",),
    "post_transform_test_report": ("post_transform_test_report",),
    "orchestration_summary": ("orchestration_summary",),
    "final_migration_report": ("final_migration_report",),
}

ADVISORY_GUARDRAILS = {
    "provider": "github_copilot",
    "adapter": "local_documentation_agent",
    "advisory_only": True,
    "can_mutate_source": False,
    "can_mutate_approval": False,
    "can_mutate_plan": False,
    "can_promote": False,
    "can_create_pr": False,
    "can_override_status": False,
}


@dataclass(frozen=True)
class CopilotDocAgentResult:
    artifact_refs: dict[str, str]
    blockers: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class CopilotDocConfig:
    enabled: bool = True
    cli_enabled: bool = False
    command: str = "copilot"
    timeout_seconds: int = 180
    fallback_enabled: bool = True


def generate_copilot_documentation_package(state: dict[str, Any]) -> CopilotDocAgentResult:
    config = load_copilot_doc_config(state.get("ai_hub_path"))
    if not config.enabled:
        return CopilotDocAgentResult(artifact_refs={}, blockers=[], warnings=[])
    if config.cli_enabled:
        return _generate_cli_documentation_package(state, config)
    return _generate_local_documentation_package(state)


def load_copilot_doc_config(ai_hub_path: str | Path | None = None) -> CopilotDocConfig:
    config_data = _load_config_data(ai_hub_path)
    cli_config = config_data.get("cli", {})
    if not isinstance(cli_config, dict):
        cli_config = {}

    enabled = _bool_value(config_data.get("enabled"), True)
    cli_enabled = _bool_value(cli_config.get("enabled"), False)
    command = str(cli_config.get("command") or "copilot").strip() or "copilot"
    timeout_seconds = _positive_int(cli_config.get("timeout_seconds"), 180)
    fallback_enabled = _bool_value(config_data.get("fallback_enabled"), True)
    if "fallback_adapter" in config_data:
        fallback_enabled = str(config_data.get("fallback_adapter") or "").strip() == "local_documentation_agent"

    env_docs_enabled = os.getenv("AI_MIGRATION_COPILOT_DOCS_ENABLED", "").strip()
    env_cli_enabled = os.getenv("AI_MIGRATION_COPILOT_CLI_ENABLED", "").strip()
    env_command = os.getenv("AI_MIGRATION_COPILOT_CLI_PATH", "").strip()
    env_timeout = os.getenv("AI_MIGRATION_COPILOT_DOCS_TIMEOUT_SECONDS", "").strip()
    env_fallback = os.getenv("AI_MIGRATION_COPILOT_DOCS_FALLBACK_ENABLED", "").strip()
    if env_docs_enabled:
        enabled = _bool_value(env_docs_enabled, enabled)
    if env_cli_enabled:
        cli_enabled = _bool_value(env_cli_enabled, cli_enabled)
    if env_command:
        command = env_command
    if env_timeout:
        timeout_seconds = _positive_int(env_timeout, timeout_seconds)
    if env_fallback:
        fallback_enabled = _bool_value(env_fallback, fallback_enabled)

    return CopilotDocConfig(
        enabled=enabled,
        cli_enabled=cli_enabled,
        command=command,
        timeout_seconds=timeout_seconds,
        fallback_enabled=fallback_enabled,
    )


def _generate_local_documentation_package(state: dict[str, Any]) -> CopilotDocAgentResult:
    artifact_refs = dict(state.get("artifact_refs", {}) or {})
    run_dir = Path(str(state.get("run_dir") or ""))
    resolved_refs = _resolve_required_refs(artifact_refs)
    blockers = _missing_required_refs(resolved_refs)
    warnings: list[str] = []
    if blockers:
        return CopilotDocAgentResult(artifact_refs={}, blockers=blockers, warnings=warnings)

    docs_dir = run_dir / "final" / DOC_DIR_NAME
    docs_dir.mkdir(parents=True, exist_ok=True)

    trace_refs = {**artifact_refs, **resolved_refs}
    sources = _load_sources(trace_refs, warnings)
    report_refs = {
        "copilot_migration_overview": str(docs_dir / "migration_overview.md"),
        "copilot_technical_changes": str(docs_dir / "technical_changes.md"),
        "copilot_validation_evidence": str(docs_dir / "validation_evidence.md"),
        "copilot_risks_and_warnings": str(docs_dir / "risks_and_warnings.md"),
        "copilot_review": str(docs_dir / "copilot_review.md"),
    }

    _write_doc(Path(report_refs["copilot_migration_overview"]), _migration_overview(state, sources))
    _write_doc(Path(report_refs["copilot_technical_changes"]), _technical_changes(state, sources))
    _write_doc(Path(report_refs["copilot_validation_evidence"]), _validation_evidence(state, sources))
    _write_doc(Path(report_refs["copilot_risks_and_warnings"]), _risks_and_warnings(state, sources))
    _write_doc(Path(report_refs["copilot_review"]), _copilot_review(state, sources))

    return CopilotDocAgentResult(
        artifact_refs=report_refs,
        blockers=[],
        warnings=warnings,
    )


def _generate_cli_documentation_package(
    state: dict[str, Any],
    config: CopilotDocConfig,
) -> CopilotDocAgentResult:
    artifact_refs = dict(state.get("artifact_refs", {}) or {})
    run_dir = Path(str(state.get("run_dir") or ""))
    resolved_refs = _resolve_required_refs(artifact_refs)
    blockers = _missing_required_refs(resolved_refs)
    warnings: list[str] = []
    if blockers:
        return CopilotDocAgentResult(artifact_refs={}, blockers=blockers, warnings=warnings)

    docs_dir = run_dir / "final" / DOC_DIR_NAME
    docs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = docs_dir / INPUT_MANIFEST_NAME
    status_path = docs_dir / CLI_STATUS_NAME
    report_refs = _copilot_report_refs(docs_dir)
    manifest_ref = {"copilot_input_manifest": str(manifest_path)}
    status_ref = {"copilot_cli_status": str(status_path)}
    status = _new_cli_status(config)
    command = resolve_copilot_cli_executable(config.command)
    status["command"] = command or ""

    trace_refs = {**artifact_refs, **resolved_refs}
    _write_manifest(manifest_path, state, run_dir, docs_dir, trace_refs)
    protected_before = _snapshot_protected_paths(state, run_dir, docs_dir, trace_refs)

    if not command:
        version = {"available": False, "exit_code": None, "timeout": False, "error": "Copilot executable path was not resolved"}
    else:
        version = _run_copilot_version(command, config, docs_dir)
    status["version_check"] = version
    if not version["available"]:
        warnings.append("copilot documentation CLI unavailable; using local fallback")
        return _fallback_after_cli_attempt(
            state,
            config,
            warnings,
            status,
            manifest_ref,
            status_ref,
            status_path,
        )

    try:
        completed = subprocess.run(
            [command],
            input=_cli_prompt(manifest_path, docs_dir),
            cwd=docs_dir,
            timeout=config.timeout_seconds,
            capture_output=True,
            text=True,
        )
        status["exit_code"] = completed.returncode
        status["stdout_preview"] = _bounded_output(completed.stdout)
        status["stderr_preview"] = _bounded_output(completed.stderr)
    except subprocess.TimeoutExpired as exc:
        status["timeout"] = True
        status["stdout_preview"] = _bounded_output(exc.stdout)
        status["stderr_preview"] = _bounded_output(exc.stderr)
        warnings.append("copilot documentation CLI timed out; using local fallback")
        return _fallback_after_cli_attempt(
            state,
            config,
            warnings,
            status,
            manifest_ref,
            status_ref,
            status_path,
        )
    except OSError as exc:
        warnings.append(f"copilot documentation CLI unavailable; using local fallback: {exc}")
        return _fallback_after_cli_attempt(
            state,
            config,
            warnings,
            status,
            manifest_ref,
            status_ref,
            status_path,
        )

    outside_changes = _protected_path_changes(protected_before, state, run_dir, docs_dir, trace_refs)
    if outside_changes:
        _restore_protected_paths(protected_before, outside_changes)
        warnings.extend(f"copilot documentation CLI wrote outside docs boundary: {path}" for path in outside_changes)
    if completed.returncode != 0:
        warnings.append("copilot documentation CLI exited nonzero; using local fallback")
    invalid_outputs = _invalid_doc_outputs(report_refs, docs_dir)
    if invalid_outputs:
        warnings.extend(f"copilot documentation CLI produced invalid output: {path}" for path in invalid_outputs)

    if completed.returncode == 0 and not invalid_outputs and not outside_changes:
        generated_refs = {**report_refs, **manifest_ref, **status_ref}
        status["fallback_status"] = "not_used"
        status["warnings"] = warnings
        status["generated_refs"] = generated_refs
        _write_status(status_path, status)
        return CopilotDocAgentResult(artifact_refs=generated_refs, blockers=[], warnings=warnings)

    return _fallback_after_cli_attempt(
        state,
        config,
        warnings,
        status,
        manifest_ref,
        status_ref,
        status_path,
    )


def _fallback_after_cli_attempt(
    state: dict[str, Any],
    config: CopilotDocConfig,
    warnings: list[str],
    status: dict[str, Any],
    manifest_ref: dict[str, str],
    status_ref: dict[str, str],
    status_path: Path,
) -> CopilotDocAgentResult:
    if not config.fallback_enabled:
        warnings.append("copilot documentation local fallback disabled; no Copilot docs generated")
        generated_refs = {**manifest_ref, **status_ref}
        status["fallback_status"] = "disabled"
        status["warnings"] = warnings
        status["generated_refs"] = generated_refs
        _write_status(status_path, status)
        return CopilotDocAgentResult(artifact_refs=generated_refs, blockers=[], warnings=warnings)

    fallback = _generate_local_documentation_package(state)
    warnings = [*warnings, *fallback.warnings]
    generated_refs = {**fallback.artifact_refs, **manifest_ref, **status_ref}
    status["fallback_status"] = "used"
    status["warnings"] = warnings
    status["generated_refs"] = generated_refs
    _write_status(status_path, status)
    return CopilotDocAgentResult(
        artifact_refs=generated_refs,
        blockers=fallback.blockers,
        warnings=warnings,
    )


def _resolve_required_refs(artifact_refs: dict[str, str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for canonical_name, aliases in REQUIRED_ARTIFACT_REFS.items():
        for alias in aliases:
            ref = artifact_refs.get(alias)
            if ref:
                resolved[canonical_name] = ref
                break
    return resolved


def _missing_required_refs(artifact_refs: dict[str, str]) -> list[str]:
    blockers: list[str] = []
    for ref_name in REQUIRED_ARTIFACT_REFS:
        ref = artifact_refs.get(ref_name)
        if not ref:
            blockers.append(f"missing required artifact ref for Copilot docs: {ref_name}")
            continue
        if not Path(ref).is_file():
            blockers.append(f"missing required artifact file for Copilot docs: {ref_name}")
    return blockers


def _load_sources(artifact_refs: dict[str, str], warnings: list[str]) -> dict[str, Any]:
    return {
        "analysis": _read_json(artifact_refs["analysis_report"], warnings),
        "plan": _read_yaml(artifact_refs["migration_plan"], warnings),
        "approval": _read_json(artifact_refs["approval_decision"], warnings),
        "lock": _read_json(artifact_refs["approved_plan_lock"], warnings),
        "execution_plan": _read_yaml(artifact_refs["transformation_execution_plan"], warnings),
        "ledger": _read_json(artifact_refs["migration_ledger"], warnings),
        "test_report": _read_json(artifact_refs["post_transform_test_report"], warnings),
        "orchestration": _read_json(artifact_refs["orchestration_summary"], warnings),
        "final_report": _read_json(artifact_refs["final_migration_report"], warnings),
        "refs": artifact_refs,
    }


def _load_config_data(ai_hub_path: str | Path | None) -> dict[str, Any]:
    if not ai_hub_path:
        return {}
    path = Path(ai_hub_path) / "agents" / "copilot-doc-agent.yaml"
    if not path.is_file():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _bool_value(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return fallback


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _copilot_report_refs(docs_dir: Path) -> dict[str, str]:
    return {
        "copilot_migration_overview": str(docs_dir / "migration_overview.md"),
        "copilot_technical_changes": str(docs_dir / "technical_changes.md"),
        "copilot_validation_evidence": str(docs_dir / "validation_evidence.md"),
        "copilot_risks_and_warnings": str(docs_dir / "risks_and_warnings.md"),
        "copilot_review": str(docs_dir / "copilot_review.md"),
    }


def _write_manifest(
    path: Path,
    state: dict[str, Any],
    run_dir: Path,
    docs_dir: Path,
    artifact_refs: dict[str, str],
) -> None:
    payload = {
        "run_id": state.get("run_id", ""),
        "output_dir": _display_path(docs_dir, docs_dir),
        "required_outputs": list(DOC_ARTIFACTS),
        "read_only_artifacts": {
            name: _display_path(Path(ref), docs_dir)
            for name, ref in artifact_refs.items()
            if name in REQUIRED_ARTIFACT_REFS
        },
        "guardrails": ADVISORY_GUARDRAILS,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _new_cli_status(config: CopilotDocConfig) -> dict[str, Any]:
    return {
        "provider": "github_copilot",
        "adapter": "copilot_cli",
        "command": config.command,
        "timeout_seconds": config.timeout_seconds,
        "version_check": {},
        "exit_code": None,
        "timeout": False,
        "fallback_status": "not_attempted",
        "warnings": [],
        "generated_refs": {},
    }


def _run_copilot_version(command: str, config: CopilotDocConfig, cwd: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [command, "--version"],
            cwd=cwd,
            timeout=min(config.timeout_seconds, 30),
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "available": False,
            "exit_code": None,
            "timeout": True,
            "stdout_preview": _bounded_output(exc.stdout),
            "stderr_preview": _bounded_output(exc.stderr),
        }
    except OSError as exc:
        return {
            "available": False,
            "exit_code": None,
            "timeout": False,
            "error": str(exc),
        }
    return {
        "available": completed.returncode == 0,
        "exit_code": completed.returncode,
        "timeout": False,
        "stdout_preview": _bounded_output(completed.stdout),
        "stderr_preview": _bounded_output(completed.stderr),
    }


def _cli_prompt(manifest_path: Path, run_dir: Path) -> str:
    return "\n".join(
        [
            "You are the GitHub Copilot documentation adapter for AI Migration Factory.",
            "Read only the artifact paths listed in the manifest.",
            "Write exactly the required Markdown outputs under the manifest output_dir.",
            "Do not modify source, sandbox, approval, plan, ledger, summary, promotion, PR, deployment, or status files.",
            f"Manifest: {_display_path(manifest_path, run_dir)}",
            "",
        ]
    )


def _invalid_doc_outputs(report_refs: dict[str, str], docs_dir: Path) -> list[str]:
    invalid: list[str] = []
    docs_root = docs_dir.resolve()
    for ref in report_refs.values():
        path = Path(ref)
        try:
            resolved = path.resolve()
        except OSError:
            invalid.append(str(path))
            continue
        if not _is_relative_to(resolved, docs_root):
            invalid.append(str(path))
            continue
        try:
            content = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            content = ""
        if path.name not in DOC_ARTIFACTS or not content.strip():
            invalid.append(str(path))
    return invalid


def _snapshot_protected_paths(
    state: dict[str, Any],
    run_dir: Path,
    docs_dir: Path,
    artifact_refs: dict[str, str],
) -> dict[str, bytes | None]:
    paths: set[Path] = {Path(ref) for ref in artifact_refs.values() if ref}
    paths.update(_files_under(run_dir, exclude_dir=docs_dir))
    for key in ("legacy_app_path", "sandbox_path"):
        value = state.get(key)
        if value:
            paths.update(_files_under(Path(str(value)), exclude_dir=docs_dir))
    snapshot: dict[str, bytes | None] = {}
    for path in paths:
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        snapshot[resolved] = path.read_bytes() if path.is_file() else None
    return snapshot


def _protected_path_changes(
    before: dict[str, bytes | None],
    state: dict[str, Any],
    run_dir: Path,
    docs_dir: Path,
    artifact_refs: dict[str, str],
) -> list[str]:
    after = _snapshot_protected_paths(state, run_dir, docs_dir, artifact_refs)
    changed: list[str] = []
    for path, before_bytes in before.items():
        if after.get(path) != before_bytes:
            changed.append(path)
    for path in after:
        if path not in before:
            changed.append(path)
    return sorted(changed)


def _restore_protected_paths(before: dict[str, bytes | None], changed: list[str]) -> None:
    for path_text in changed:
        path = Path(path_text)
        original = before.get(path_text)
        try:
            if original is None:
                if path.is_file():
                    path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original)
        except OSError:
            continue


def _files_under(root: Path, *, exclude_dir: Path) -> set[Path]:
    if not root.exists():
        return set()
    try:
        exclude_root = exclude_dir.resolve()
    except OSError:
        exclude_root = exclude_dir
    result: set[Path] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if _is_relative_to(resolved, exclude_root):
            continue
        result.add(path)
    return result


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(_to_json_safe(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _bounded_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value)
    if len(text) <= CLI_OUTPUT_PREVIEW_LIMIT:
        return text
    return text[:CLI_OUTPUT_PREVIEW_LIMIT] + "\n...[truncated]"


def _display_path(path: Path, run_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(run_dir.resolve()))
    except (OSError, ValueError):
        return str(path)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _migration_overview(state: dict[str, Any], sources: dict[str, Any]) -> str:
    final_report = _obj(sources.get("final_report"))
    approval = _obj(sources.get("approval"))
    source_stack = _obj(final_report.get("source_stack"))
    target_stack = _obj(final_report.get("target_stack"))
    lines = [
        "# Migration Overview",
        "",
        *_guardrail_lines(),
        "",
        f"- Run ID: {state.get('run_id', final_report.get('run_id', ''))}",
        f"- Approval decision: {approval.get('decision', final_report.get('approval', {}).get('decision', ''))}",
        f"- Source Java: {source_stack.get('java', 'unknown')}",
        f"- Source Spring Boot: {source_stack.get('spring_boot', 'unknown')}",
        f"- Target Java: {target_stack.get('java', 'unknown')}",
        f"- Target Spring Boot: {target_stack.get('spring_boot', 'unknown')}",
        f"- Transform status: {final_report.get('transform_status', state.get('transform_status', ''))}",
        f"- Build status: {final_report.get('build_status', state.get('build_status', ''))}",
        f"- Test status: {final_report.get('test_status', state.get('test_status', ''))}",
        "",
        *_source_lines(
            sources,
            "analysis_report",
            "migration_plan",
            "approval_decision",
            "final_migration_report",
        ),
    ]
    return "\n".join(lines) + "\n"


def _technical_changes(state: dict[str, Any], sources: dict[str, Any]) -> str:
    final_report = _obj(sources.get("final_report"))
    plan = _obj(sources.get("plan"))
    execution_plan = _obj(sources.get("execution_plan"))
    ledger = _obj(sources.get("ledger"))
    recipes = list(final_report.get("recipes", []) or [])
    units = _plan_units(plan)
    ledger_units = _obj(ledger.get("units"))
    lines = [
        "# Technical Changes",
        "",
        *_guardrail_lines(),
        "",
        "## Executed Recipes",
        "",
        *(_bullet_lines(recipes) or ["- none recorded"]),
        "",
        "## Migration Units",
        "",
        *(_bullet_lines(units) or ["- none recorded"]),
        "",
        "## Ledger Unit Statuses",
        "",
        *(_ledger_lines(ledger_units) or ["- none recorded"]),
        "",
        "## Execution Plan Summary",
        "",
        f"- Transformation unit count: {len(execution_plan.get('migration_units', []) or [])}",
        f"- Sandbox path: {state.get('sandbox_path', final_report.get('sandbox_path', ''))}",
        "",
        *_source_lines(
            sources,
            "migration_plan",
            "transformation_execution_plan",
            "migration_ledger",
            "final_migration_report",
        ),
    ]
    return "\n".join(lines) + "\n"


def _validation_evidence(state: dict[str, Any], sources: dict[str, Any]) -> str:
    test_report = _obj(sources.get("test_report"))
    orchestration = _obj(sources.get("orchestration"))
    final_report = _obj(sources.get("final_report"))
    totals = _obj(test_report.get("totals") or final_report.get("test_totals"))
    lines = [
        "# Validation Evidence",
        "",
        *_guardrail_lines(),
        "",
        f"- Orchestration status: {orchestration.get('orchestration_status', state.get('orchestration_status', ''))}",
        f"- Transform status: {final_report.get('transform_status', state.get('transform_status', ''))}",
        f"- Build status: {final_report.get('build_status', state.get('build_status', ''))}",
        f"- Test status: {test_report.get('test_status', state.get('test_status', ''))}",
        f"- Tests: {totals.get('tests', 0)}",
        f"- Passed: {totals.get('passed', 0)}",
        f"- Failures: {totals.get('failures', 0)}",
        f"- Errors: {totals.get('errors', 0)}",
        f"- Skipped: {totals.get('skipped', 0)}",
        f"- Execution owner: {test_report.get('execution_owner', 'build-agent')}",
        f"- Execution mode: {test_report.get('execution_mode', 'parse_existing_surefire')}",
        "",
        *_source_lines(
            sources,
            "post_transform_test_report",
            "orchestration_summary",
            "final_migration_report",
            "phase2_log",
        ),
    ]
    return "\n".join(lines) + "\n"


def _risks_and_warnings(state: dict[str, Any], sources: dict[str, Any]) -> str:
    final_report = _obj(sources.get("final_report"))
    orchestration = _obj(sources.get("orchestration"))
    warnings = _dedupe(
        [
            *list(final_report.get("warnings", []) or []),
            *list(final_report.get("boot4_warnings", []) or []),
            *list(orchestration.get("warnings", []) or []),
            *list(state.get("warnings", []) or []),
        ]
    )
    limitations = list(final_report.get("limitations", []) or [])
    lines = [
        "# Risks And Warnings",
        "",
        *_guardrail_lines(),
        "",
        "## Warnings",
        "",
        *(_bullet_lines(warnings) or ["- none recorded"]),
        "",
        "## Limitations",
        "",
        *(_bullet_lines(limitations) or ["- none recorded"]),
        "",
        *_source_lines(
            sources,
            "analysis_report",
            "assessment_report",
            "orchestration_summary",
            "final_migration_report",
        ),
    ]
    return "\n".join(lines) + "\n"


def _copilot_review(state: dict[str, Any], sources: dict[str, Any]) -> str:
    final_report = _obj(sources.get("final_report"))
    approval = _obj(sources.get("approval"))
    lock = _obj(sources.get("lock"))
    lines = [
        "# Copilot Review",
        "",
        *_guardrail_lines(),
        "",
        "GitHub Copilot is integrated here as an advisory documentation agent. "
        "It consumes deterministic AI Migration Factory artifacts after sandbox validation and writes human-readable documentation only.",
        "",
        "## Deterministic Control Boundary",
        "",
        f"- Approval decision observed: {approval.get('decision', final_report.get('approval', {}).get('decision', ''))}",
        f"- Approved plan lock observed: {'yes' if lock is not None else 'unknown'}",
        f"- Final status observed: {final_report.get('transform_status', state.get('final_status', ''))}",
        "- Copilot did not approve the run.",
        "- Copilot did not modify the migration plan.",
        "- Copilot did not modify legacy or sandbox source.",
        "- Copilot did not promote, deploy, merge, or create a pull request.",
        "",
        *_source_lines(
            sources,
            "approval_decision",
            "approved_plan_lock",
            "orchestration_summary",
            "final_migration_report",
        ),
    ]
    return "\n".join(lines) + "\n"


def _guardrail_lines() -> list[str]:
    return [
        "> Advisory documentation only. Copilot cannot mutate source, approvals, plans, gates, PRs, deployments, or promotion state.",
    ]


def _source_lines(sources: dict[str, Any], *names: str) -> list[str]:
    refs = _obj(sources.get("refs"))
    lines = ["## Source Artifacts", ""]
    for name in names:
        ref = refs.get(name)
        lines.append(f"- {name}: {ref or 'not recorded'}")
    return lines


def _plan_units(plan: dict[str, Any]) -> list[str]:
    units = plan.get("units") or plan.get("migration_units") or []
    result: list[str] = []
    for unit in units:
        if isinstance(unit, dict):
            unit_id = str(unit.get("id") or "unknown")
            goal = str(unit.get("goal") or unit.get("title") or "")
            result.append(f"{unit_id}: {goal}".rstrip(": "))
        else:
            result.append(str(unit))
    return result


def _ledger_lines(units: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for unit_id, unit in units.items():
        if isinstance(unit, dict):
            lines.append(f"- {unit_id}: {unit.get('status', 'unknown')}")
        else:
            lines.append(f"- {unit_id}: {unit}")
    return lines


def _bullet_lines(values: list[Any]) -> list[str]:
    return [f"- {value}" for value in values if str(value).strip()]


def _dedupe(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _write_doc(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_json(path: str, warnings: list[str]) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"unable to read Copilot doc source {path}: {exc}")
        return None
    return payload if isinstance(payload, dict) else {}


def _read_yaml(path: str, warnings: list[str]) -> dict[str, Any] | None:
    try:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        warnings.append(f"unable to read Copilot doc source {path}: {exc}")
        return None
    return payload if isinstance(payload, dict) else {}


def _to_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_to_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
