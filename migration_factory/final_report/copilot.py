from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from migration_factory.copilot_cli import resolve_copilot_cli_executable, _is_windows


PROVIDER = "github_copilot"
ADAPTER = "local_deterministic_template"
CLI_ADAPTER = "copilot_cli"
MODEL_ENV = "AI_MIGRATION_COPILOT_MODEL"
PROVIDER_ENV = "AI_MIGRATION_COPILOT_PROVIDER"
TIMEOUT_ENV = "AI_MIGRATION_COPILOT_TIMEOUT_SECONDS"
LOG_LEVEL_ENV = "AI_MIGRATION_COPILOT_LOG_LEVEL"
DEFAULT_COPILOT_MODEL = "gpt-5-mini"
DEFAULT_COPILOT_TIMEOUT_SECONDS = 300
MIN_COPILOT_TIMEOUT_SECONDS = 30
MAX_COPILOT_TIMEOUT_SECONDS = 900
DEFAULT_COPILOT_LOG_LEVEL = "error"
COPILOT_INPUT_MODE = "stdin"
DEFAULT_MANIFEST_RELATIVE_PATH = Path("templates") / "reports" / "copilot_final_migration_report_v1.yaml"
COPILOT_LOG_DIR = Path("logs") / "copilot"
COPILOT_GITHUB_CLI_LOG_DIR = COPILOT_LOG_DIR / "github-cli"
_EXTRA_CONTEXT_ARTIFACTS = (
    Path("final") / "report_context.json",
    Path("analysis") / "analysis_report.json",
    Path("analysis") / "dependency_graph.json",
    Path("analysis") / "config_inventory.json",
    COPILOT_LOG_DIR / "copilot_cli_invocation.json",
)
_ALLOWED_COPILOT_LOG_LEVELS = {"none", "error", "warning", "info", "debug", "all", "default"}
_REQUIRED_MANIFEST_FIELDS = {
    "id",
    "version",
    "type",
    "engine",
    "template_file",
    "output_file",
    "request_file",
    "response_file",
    "advisory_only",
    "requires",
    "optional",
}
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
_SECRET_KEY_PARTS = ("token", "secret", "password", "credential", "authorization", "auth_output")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?im)^(\s*authorization\s*:\s*).+$"),
    re.compile(r"(?i)\b[A-Za-z_]*(?:TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Za-z_]*\s*=\s*[^\s]+"),
)
_REQUIRED_REPORT_SECTIONS = (
    "# Copilot Final Migration Report",
    "## 1. Summary",
    "## 2. Source Of Truth",
    "## 10. Test Results",
    "## 15. Copilot Advisory Scope",
    "## 18. Final Verdict",
)
_FORBIDDEN_COPILOT_CLAIMS = (
    re.compile(r"(?i)\bcopilot\s+(approved|transformed|tested|deployed|merged)\b"),
    re.compile(r"(?i)\bcopilot\s+(created|opened)\s+(a\s+)?(pull request|pr)\b"),
)


@dataclass(frozen=True)
class CopilotReportManifest:
    id: str
    version: str
    type: str
    engine: str
    template_path: Path
    output_file: Path
    request_file: Path
    response_file: Path
    advisory_only: bool
    requires: tuple[Path, ...]
    optional: tuple[Path, ...]

    def output_path(self, run_dir: str | Path) -> Path:
        return Path(run_dir) / self.output_file

    def request_path(self, run_dir: str | Path) -> Path:
        return Path(run_dir) / self.request_file

    def response_path(self, run_dir: str | Path) -> Path:
        return Path(run_dir) / self.response_file


@dataclass(frozen=True)
class CopilotAdapterStatus:
    provider: str = PROVIDER
    model: str = "unknown"
    connectivity: str = "not_configured"
    report_status: str = "skipped"
    adapter: str = ADAPTER
    auth_status: str = "unknown"
    cli_status: str = "not_installed"
    resolved_executable_path: str = ""

    @property
    def resolved_executable(self) -> str:
        return self.resolved_executable_path

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "adapter": self.adapter,
            "model": self.model,
            "connectivity": self.connectivity,
            "report_status": self.report_status,
            "auth_status": self.auth_status,
            "cli_status": self.cli_status,
            "resolved_executable_basename": _safe_executable_basename(self.resolved_executable_path),
        }


@dataclass(frozen=True)
class CopilotReportRequest:
    payload: dict[str, Any]
    warnings: list[str]
    missing_required: list[str]
    missing_optional: list[str]


@dataclass(frozen=True)
class CopilotCliRunResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    started_at: str
    ended_at: str
    elapsed_seconds: float
    command: list[str]


def load_copilot_report_manifest(ai_hub_path: str | Path) -> CopilotReportManifest:
    manifest_path = Path(ai_hub_path) / DEFAULT_MANIFEST_RELATIVE_PATH
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"unable to read Copilot report manifest: {manifest_path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Copilot report manifest must be a mapping")

    missing = sorted(_REQUIRED_MANIFEST_FIELDS - set(raw))
    if missing:
        raise ValueError(f"Copilot report manifest missing required fields: {', '.join(missing)}")

    requires = raw["requires"]
    optional = raw["optional"]
    if not isinstance(requires, list) or not all(isinstance(item, str) for item in requires):
        raise ValueError("Copilot report manifest requires must be a list of paths")
    if not isinstance(optional, list) or not all(isinstance(item, str) for item in optional):
        raise ValueError("Copilot report manifest optional must be a list of paths")
    if raw["engine"] != PROVIDER:
        raise ValueError("Copilot report manifest engine must be github_copilot")
    if raw["advisory_only"] is not True:
        raise ValueError("Copilot report manifest must be advisory_only")

    manifest_dir = manifest_path.parent
    template_path = manifest_dir / str(raw["template_file"])
    if not template_path.is_file():
        raise ValueError(f"Copilot report template is missing: {template_path}")

    return CopilotReportManifest(
        id=str(raw["id"]),
        version=str(raw["version"]),
        type=str(raw["type"]),
        engine=str(raw["engine"]),
        template_path=template_path,
        output_file=Path(str(raw["output_file"])),
        request_file=Path(str(raw["request_file"])),
        response_file=Path(str(raw["response_file"])),
        advisory_only=bool(raw["advisory_only"]),
        requires=tuple(Path(item) for item in requires),
        optional=tuple(Path(item) for item in optional),
    )


def detect_copilot_cli_status(
    *,
    timeout_seconds: float = 15.0,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
) -> CopilotAdapterStatus:
    """Read-only availability/auth probe for status displays.

    This intentionally never sends a prompt to Copilot. GitHub CLI auth is only
    a weak local signal that the user has GitHub credentials configured.
    """

    effective_env = env or os.environ
    model = _configured_model(effective_env)
    try:
        copilot_path = _find_copilot_command(timeout_seconds)
        if not copilot_path:
            return CopilotAdapterStatus(
                model=model,
                connectivity="not_configured",
                adapter=ADAPTER,
                auth_status="unknown",
                cli_status="not_installed",
            )

        _copilot_version_proves_cli(copilot_path, timeout_seconds, cwd=_copilot_probe_cwd(cwd))

        auth_status = _detect_gh_auth_status(timeout_seconds)
        connectivity = "connected" if auth_status == "authenticated" else "unavailable"
        return CopilotAdapterStatus(
            model=model,
            connectivity=connectivity,
            adapter=CLI_ADAPTER,
            auth_status=auth_status,
            cli_status="installed",
            resolved_executable_path=copilot_path,
        )
    except Exception:
        return CopilotAdapterStatus(
            model=model,
            connectivity="unavailable",
            adapter=ADAPTER,
            auth_status="unknown",
            cli_status="error",
        )


def build_copilot_report_request(
    run_dir: str | Path,
    manifest: CopilotReportManifest,
    *,
    context: dict[str, Any] | None = None,
    status: CopilotAdapterStatus | None = None,
) -> CopilotReportRequest:
    run_path = Path(run_dir)
    warnings: list[str] = []
    missing_required = [path.as_posix() for path in manifest.requires if not (run_path / path).is_file()]
    missing_optional = [path.as_posix() for path in manifest.optional if not (run_path / path).is_file()]
    warnings.extend(f"missing optional Copilot report artifact: {path}" for path in missing_optional)

    optional_artifacts = tuple(dict.fromkeys((*manifest.optional, *_EXTRA_CONTEXT_ARTIFACTS)))
    artifacts = {
        "required": {
            path.as_posix(): _safe_read_artifact(run_path / path, warnings)
            for path in manifest.requires
            if (run_path / path).is_file()
        },
        "optional": {
            path.as_posix(): _safe_read_artifact(run_path / path, warnings)
            for path in optional_artifacts
            if (run_path / path).is_file()
        },
    }
    supplied_context = {
        **(context or {}),
        "missing_optional_inputs": ", ".join(missing_optional),
        "missing_required_inputs": ", ".join(missing_required),
    }
    base_context = _build_template_context(
        artifacts,
        supplied_context,
        manifest,
        status or CopilotAdapterStatus(),
    )
    payload = {
        "manifest": {
            "id": manifest.id,
            "version": manifest.version,
            "type": manifest.type,
            "engine": manifest.engine,
            "advisory_only": manifest.advisory_only,
            "template_file": manifest.template_path.name,
        },
        "guardrails": {
            "advisory_only": True,
            "can_approve": False,
            "can_transform": False,
            "can_mutate_source": False,
            "can_change_gates": False,
            "can_override_status": False,
            "can_create_pr": False,
            "can_deploy": False,
            "can_decide_success": False,
        },
        "paths": {
            "output_file": manifest.output_file.as_posix(),
            "request_file": manifest.request_file.as_posix(),
            "response_file": manifest.response_file.as_posix(),
        },
        "artifacts": artifacts,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "warnings": warnings,
        "template_context": base_context,
        "adapter_status": (status or CopilotAdapterStatus()).to_dict(),
        "created_at": _utc_now(),
    }
    return CopilotReportRequest(
        payload=_redact(payload),
        warnings=warnings,
        missing_required=missing_required,
        missing_optional=missing_optional,
    )


def write_copilot_report_request(run_dir: str | Path, manifest: CopilotReportManifest, payload: dict[str, Any]) -> Path:
    request_path = manifest.request_path(run_dir)
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return request_path


def render_copilot_report_template(template_path: str | Path, context: dict[str, Any]) -> str:
    template = Path(template_path).read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        value = context.get(match.group(1), "")
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True)
        return str(value)

    return _PLACEHOLDER_RE.sub(replace, template)


def generate_copilot_report_skeleton(
    run_dir: str | Path,
    ai_hub_path: str | Path,
    *,
    context: dict[str, Any] | None = None,
    status: CopilotAdapterStatus | None = None,
) -> dict[str, Any]:
    manifest = load_copilot_report_manifest(ai_hub_path)
    effective_status = status or CopilotAdapterStatus(connectivity="not_configured", report_status="generated")
    request = build_copilot_report_request(
        run_dir,
        manifest,
        context=context,
        status=effective_status,
    )
    if request.missing_required:
        response_path = _write_copilot_report_response(
            run_dir,
            manifest,
            _build_response_payload(
                effective_status,
                manifest,
                report_status="skipped",
                warnings=[
                    *request.warnings,
                    "missing required Copilot report artifacts: "
                    + ", ".join(request.missing_required),
                ],
            ),
        )
        write_copilot_report_request(run_dir, manifest, request.payload)
        raise ValueError(
            "missing required Copilot report artifacts: " + ", ".join(request.missing_required)
        )

    request_path = write_copilot_report_request(run_dir, manifest, request.payload)
    report_path = manifest.output_path(run_dir)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_copilot_report_template(manifest.template_path, request.payload["template_context"])
    _validate_copilot_markdown(markdown, request_payload=request.payload)
    report_path.write_text(markdown, encoding="utf-8")
    response_payload = _build_response_payload(
        effective_status,
        manifest,
        report_status="generated",
        warnings=request.warnings,
    )
    response_path = _write_copilot_report_response(run_dir, manifest, response_payload)
    return {
        "artifact_refs": {
            "copilot_report_request": str(request_path),
            "copilot_report_response": str(response_path),
            "copilot_migration_report": str(report_path),
        },
        "warnings": request.warnings,
        "response": response_payload,
    }


def generate_copilot_report(
    run_dir: str | Path,
    ai_hub_path: str | Path,
    *,
    context: dict[str, Any] | None = None,
    status: CopilotAdapterStatus | None = None,
    timeout_seconds: float = DEFAULT_COPILOT_TIMEOUT_SECONDS,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    effective_env = env or os.environ
    provider = str(effective_env.get(PROVIDER_ENV, "")).strip().lower()
    if provider != CLI_ADAPTER:
        return generate_copilot_report_skeleton(run_dir, ai_hub_path, context=context, status=status)
    return _generate_copilot_cli_report(
        run_dir,
        ai_hub_path,
        context=context,
        status=status,
        timeout_seconds=timeout_seconds,
        env=effective_env,
    )


def write_failed_copilot_report_response(
    run_dir: str | Path,
    ai_hub_path: str | Path,
    *,
    warning: str,
    report_status: str = "failed",
) -> dict[str, Any]:
    manifest = load_copilot_report_manifest(ai_hub_path)
    response_payload = _build_response_payload(
        CopilotAdapterStatus(connectivity="not_configured", report_status=report_status),
        manifest,
        report_status=report_status,
        warnings=[warning],
    )
    response_path = _write_copilot_report_response(run_dir, manifest, response_payload)
    return {
        "artifact_refs": {"copilot_report_response": str(response_path)},
        "warnings": [warning],
        "response": response_payload,
    }


def _build_template_context(
    artifacts: dict[str, Any],
    supplied: dict[str, Any],
    manifest: CopilotReportManifest,
    status: CopilotAdapterStatus,
) -> dict[str, Any]:
    final_report = _artifact_payload(artifacts, "final/migration_report.json")
    orchestration = _artifact_payload(artifacts, "orchestration/orchestration_summary.json")
    approval = _artifact_payload(artifacts, "approval/approval_decision.json")
    approved_lock = _artifact_payload(artifacts, "approval/approved_plan_lock.json")
    test_report = _artifact_payload(artifacts, "test/post_transform/test_report.json")
    timing = _artifact_payload(artifacts, "performance/timing_report.json")
    analysis_report = _artifact_payload(artifacts, "analysis/analysis_report.json")
    dependency_graph = _artifact_payload(artifacts, "analysis/dependency_graph.json")
    config_inventory = _artifact_payload(artifacts, "analysis/config_inventory.json")
    transformation_plan = _artifact_payload(artifacts, "transformation/transformation_execution_plan.yaml")
    ledger = _artifact_payload(artifacts, "workspaces/sandbox/.migration/ledger.json")
    copilot_invocation = _artifact_payload(artifacts, "logs/copilot/copilot_cli_invocation.json")
    source_stack = _dict(final_report.get("source_stack"))
    target_stack = _dict(final_report.get("target_stack"))
    test_totals = _dict(final_report.get("test_totals") or test_report.get("totals"))
    approval_info = _dict(final_report.get("approval"))
    warnings = list(final_report.get("warnings", []) or [])
    blockers = list(orchestration.get("blockers", []) or [])
    missing_optional = supplied.get("missing_optional_inputs", "")
    legacy_app_path = str(supplied.get("legacy_app_path") or final_report.get("legacy_app_path") or "").strip()
    profile_id = str(supplied.get("profile_id") or final_report.get("profile_id") or "").strip()
    mode = str(supplied.get("mode") or final_report.get("mode") or "").strip()
    application_name = (
        str(supplied.get("application_name") or final_report.get("application_name") or "").strip()
        or _path_name_for_report(legacy_app_path)
    )
    enriched_source_stack = _enriched_source_stack(
        source_stack,
        target_stack,
        profile_id,
        analysis_report,
        dependency_graph,
        config_inventory,
    )
    enriched_target_stack = _enriched_target_stack(target_stack)
    live_generation = status.adapter == CLI_ADAPTER and status.report_status == "generated"
    fallback_used = status.report_status == "generated_with_fallback"
    strategy = str(final_report.get("strategy") or "").strip()
    if not strategy and mode == "full_sandbox_migration":
        strategy = "controlled full sandbox migration"
    patch_rows, patch_summary = _deterministic_patch_rows(ledger)
    blocker_rows = _blocker_rows(blockers)
    review_focus = _review_focus(warnings, transformation_plan, ledger)

    context = {
        "run_id": final_report.get("run_id") or orchestration.get("run_id") or supplied.get("run_id", ""),
        "application_name": application_name,
        "profile_id": profile_id,
        "mode": mode,
        "legacy_app_path": legacy_app_path,
        "sandbox_path": final_report.get("sandbox_path") or supplied.get("sandbox_path", ""),
        "final_verdict": supplied.get("final_verdict") or final_report.get("final_status", ""),
        "orchestration_status": orchestration.get("orchestration_status") or final_report.get("orchestration_status", ""),
        "generated_at": _utc_now(),
        "preflight_status": supplied.get("preflight_status", ""),
        "analysis_status": supplied.get("analysis_status", ""),
        "planning_status": supplied.get("planning_status", ""),
        "assessment_status": supplied.get("assessment_status", ""),
        "approval_status": approval_info.get("status") or final_report.get("approval_status", ""),
        "transform_status": final_report.get("transform_status", ""),
        "build_status": final_report.get("build_status", ""),
        "test_status": final_report.get("test_status") or test_report.get("test_status", ""),
        "final_report_status": "generated",
        "source_java_version": enriched_source_stack.get("java", ""),
        "target_java_version": enriched_target_stack.get("java", ""),
        "source_spring_boot_version": enriched_source_stack.get("spring_boot", ""),
        "target_spring_boot_version": enriched_target_stack.get("spring_boot", ""),
        "source_spring_framework_version": enriched_source_stack.get("spring_framework", ""),
        "target_spring_framework_version": enriched_target_stack.get("spring_framework", ""),
        "source_build_tool": enriched_source_stack.get("build_tool", ""),
        "target_build_tool": enriched_target_stack.get("build_tool") or enriched_target_stack.get("build", ""),
        "source_packaging": enriched_source_stack.get("packaging", ""),
        "target_packaging": enriched_target_stack.get("packaging", ""),
        "migration_type": "sandbox",
        "profile_risk_level": final_report.get("risk_level", ""),
        "strategy": strategy,
        "requires_human_approval": final_report.get("requires_human_approval", ""),
        "production_allowed": _none_if_absent(final_report.get("production_allowed")),
        "fallback_profile": _none_if_absent(final_report.get("fallback_profile")),
        "approval_decision": approval.get("decision") or approval_info.get("decision", ""),
        "approved_by": approval.get("approved_by") or approval_info.get("approved_by", ""),
        "decided_at": approval.get("decided_at", ""),
        "approval_source": approval.get("source", ""),
        "approval_comments": approval.get("comments", ""),
        "approval_summary": approval.get("summary", ""),
        "source_mutation_status": "not_mutated",
        "openrewrite_status": final_report.get("transform_status", ""),
        "deterministic_patch_status": final_report.get("transform_status", ""),
        "ledger_status": "present" if ledger else "missing",
        "tests_run": test_totals.get("tests", 0),
        "tests_passed": test_totals.get("passed", 0),
        "tests_failed": test_totals.get("failures", 0),
        "test_errors": test_totals.get("errors", 0),
        "tests_skipped": test_totals.get("skipped", 0),
        "copilot_input_package_status": "ready",
        "missing_optional_inputs": missing_optional,
        "missing_required_inputs": "",
        "copilot_provider": status.provider,
        "copilot_adapter": status.adapter,
        "copilot_connectivity": status.connectivity,
        "copilot_model": _normalize_model(status.model),
        "copilot_model_source": _model_source(status.model),
        "copilot_prompt_template_id": manifest.id,
        "copilot_prompt_template_version": manifest.version,
        "copilot_enabled": "true",
        "copilot_auth_status": status.auth_status,
        "copilot_cli_status": status.cli_status,
        "copilot_available": "true" if live_generation else "false",
        "copilot_live_generation": "true" if live_generation else "false",
        "copilot_fallback_used": "true" if fallback_used else "false",
        "copilot_fallback_reason": (
            "local deterministic template used after live Copilot CLI failure"
            if fallback_used
            else ""
        ),
        "approved_plan_lock_status": "present" if approved_lock else "missing",
        "orchestration_artifacts_valid": orchestration.get("orchestration_artifacts_valid", ""),
        "final_stop_reason": "sandbox migration candidate only",
        "final_conclusion": supplied.get("final_conclusion", ""),
        "recommended_next_step": supplied.get("recommended_next_step", "manual review"),
        "total_machine_duration": _total_machine_duration(timing),
        "risk_summary": "; ".join(str(item) for item in warnings[:3]),
        "manual_review_notes": "",
        "test_notes": "",
        "build_notes": "",
        "deterministic_patch_rows": patch_rows,
        "deterministic_patch_summary": patch_summary,
        "blocker_rows": blocker_rows,
    }
    context.update(_risk_area_context(context, warnings, transformation_plan, ledger))
    for index, warning in enumerate(warnings[:3], start=1):
        context[f"warning_{index}_code"] = str(warning)
        context[f"warning_{index}_impact"] = "review"
        context[f"warning_{index}_action"] = "manual review"
    for index, blocker in enumerate(blockers[:2], start=1):
        context[f"blocker_{index}"] = str(blocker)
        context[f"blocker_{index}_status"] = "open"
    for index, focus in enumerate(review_focus[:4], start=1):
        context[f"review_focus_{index}"] = focus
    context.update(_timing_template_context(timing))
    context.update(_build_command_context(timing, test_report, final_report))
    context.update(_copilot_duration_context(copilot_invocation))
    patch_defaults = _empty_patch_template_defaults()
    context.update({_key: _template_default_value(_key) for _key in _template_only_defaults() if _key not in context})
    context.update({key: value for key, value in patch_defaults.items() if context.get(key) == "not_available"})
    context.update(
        {
            key: value
            for key, value in supplied.items()
            if key not in {"missing_optional_inputs"} and not _is_absent_value(value)
        }
    )
    return _redact({key: _display_value(value) for key, value in context.items()})


def _build_response_payload(
    status: CopilotAdapterStatus,
    manifest: CopilotReportManifest,
    *,
    report_status: str,
    warnings: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _redact(
        {
            "provider": PROVIDER,
            "adapter": status.adapter,
            "connectivity": status.connectivity,
            "model": _normalize_model(status.model),
            "auth_status": status.auth_status,
            "cli_status": status.cli_status,
            "resolved_executable_basename": _safe_executable_basename(status.resolved_executable),
            "report_status": report_status,
            "advisory_only": True,
            "can_approve": False,
            "can_transform": False,
            "can_change_gates": False,
            "can_mutate_source": False,
            "can_override_status": False,
            "can_create_pr": False,
            "can_deploy": False,
            "report_file": manifest.output_file.as_posix(),
            "request_file": manifest.request_file.as_posix(),
            "warnings": warnings,
            "created_at": _utc_now(),
            **(extra or {}),
        }
    )


def _path_name_for_report(path: str) -> str:
    value = str(path or "").strip().rstrip("\\/")
    if not value:
        return ""
    return re.split(r"[\\/]", value)[-1]


def _none_if_absent(value: Any) -> Any:
    if _is_absent_value(value):
        return "none"
    return value


def _is_absent_value(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _enriched_source_stack(
    source_stack: dict[str, Any],
    target_stack: dict[str, Any],
    profile_id: str,
    analysis_report: dict[str, Any],
    dependency_graph: dict[str, Any],
    config_inventory: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(source_stack)
    analysis_source = _dict(analysis_report.get("source_stack"))
    for key in ("java", "spring_boot", "spring_framework", "build_tool", "build", "packaging"):
        if not enriched.get(key) and analysis_source.get(key):
            enriched[key] = analysis_source[key]
    spring_framework = _find_nested_value((analysis_report, dependency_graph, config_inventory), "spring_framework")
    if not enriched.get("spring_framework") and spring_framework:
        enriched["spring_framework"] = spring_framework
    if not enriched.get("spring_framework") and profile_id == "springboot-2.7-to-3.5-java17":
        enriched["spring_framework"] = "Spring Framework 5.x"
    packaging = _find_nested_value((analysis_report, config_inventory), "packaging")
    if not enriched.get("packaging") and packaging:
        enriched["packaging"] = packaging
    if not enriched.get("packaging") and str(enriched.get("build_tool") or "").lower() == "maven":
        modules = _find_nested_value((analysis_report, config_inventory), "modules")
        if isinstance(modules, list) and len(modules) > 1:
            enriched["packaging"] = "maven multi-module project"
    if not enriched.get("build_tool") and enriched.get("build"):
        enriched["build_tool"] = enriched["build"]
    return enriched


def _enriched_target_stack(target_stack: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(target_stack)
    if not enriched.get("build_tool") and enriched.get("build"):
        enriched["build_tool"] = enriched["build"]
    return enriched


def _find_nested_value(sources: tuple[dict[str, Any], ...], key: str) -> Any:
    for source in sources:
        found = _find_nested_value_one(source, key)
        if found not in (None, ""):
            return found
    return ""


def _find_nested_value_one(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value and value[key] not in (None, ""):
            return value[key]
        for item in value.values():
            found = _find_nested_value_one(item, key)
            if found not in (None, ""):
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_nested_value_one(item, key)
            if found not in (None, ""):
                return found
    return ""


def _risk_area_context(
    context: dict[str, Any],
    warnings: list[Any],
    transformation_plan: dict[str, Any],
    ledger: dict[str, Any],
) -> dict[str, str]:
    java_gap = _version_gap(context.get("source_java_version"), context.get("target_java_version"))
    boot_gap = _version_gap(context.get("source_spring_boot_version"), context.get("target_spring_boot_version"))
    framework_gap = _version_gap(
        context.get("source_spring_framework_version"),
        context.get("target_spring_framework_version"),
    )
    jakarta = _has_jakarta_evidence(transformation_plan, ledger)
    security = _contains_text(warnings, "SECURITY_CONFIG_TOUCHED")
    tests_passed = str(context.get("test_status") or "").upper() == "TEST_PASSED"
    return {
        "java_gap_status": java_gap or "not_detected_in_current_artifacts",
        "java_gap_notes": "Derived from source and target Java versions." if java_gap else "No Java gap evidence found.",
        "boot_gap_status": boot_gap or "not_detected_in_current_artifacts",
        "boot_gap_notes": "Derived from source and target Spring Boot versions." if boot_gap else "No Spring Boot gap evidence found.",
        "framework_gap_status": framework_gap or "not_detected_in_current_artifacts",
        "framework_gap_notes": "Derived from source and target Spring Framework versions." if framework_gap else "No Spring Framework gap evidence found.",
        "jakarta_status": "review_required" if jakarta else "not_detected_in_current_artifacts",
        "jakarta_notes": "Spring Boot 3/Jakarta migration evidence found." if jakarta else "No Jakarta/javax evidence found.",
        "security_status": "review_required" if security else "not_detected_in_current_artifacts",
        "security_notes": "Security configuration warning was recorded." if security else "No security warning found.",
        "batch_status": "not_detected_in_current_artifacts",
        "batch_notes": "No Spring Batch evidence found in current artifacts.",
        "test_dependency_status": "validated_by_existing_tests" if tests_passed else "not_detected_in_current_artifacts",
        "test_dependency_notes": "Existing test suite passed." if tests_passed else "Existing tests did not provide a pass signal.",
    }


def _version_gap(source: Any, target: Any) -> str:
    left = str(source or "").strip()
    right = str(target or "").strip()
    if left and right:
        return f"{left} -> {right}"
    return ""


def _has_jakarta_evidence(transformation_plan: dict[str, Any], ledger: dict[str, Any]) -> bool:
    return _contains_text((transformation_plan, ledger), "jakarta") or _contains_text(
        (transformation_plan, ledger),
        "javax",
    ) or _contains_text((transformation_plan,), "SpringBoot3")


def _contains_text(value: Any, needle: str) -> bool:
    return needle.lower() in json.dumps(value, sort_keys=True, default=str).lower()


def _review_focus(warnings: list[Any], transformation_plan: dict[str, Any], ledger: dict[str, Any]) -> list[str]:
    warning_text = json.dumps(warnings, default=str)
    focus: list[str] = []
    if "OPENREWRITE_IMPACT_HIGH" in warning_text:
        focus.append("Review transformed files and high-impact recipe output.")
    high_risk_match = re.search(r"OPENREWRITE_HIGH_RISK_FILES:.*?(\d+)", warning_text)
    if high_risk_match:
        focus.append(f"Review the {high_risk_match.group(1)} high-risk files reported by OpenRewrite.")
    if "OPENREWRITE_SECURITY_CONFIG_TOUCHED" in warning_text:
        focus.append("Review Spring Security/auth behavior.")
    if _has_jakarta_evidence(transformation_plan, ledger) or "jakarta" in warning_text.lower() or "javax" in warning_text.lower():
        focus.append("Review javax/jakarta import and runtime behavior.")
    return focus or ["Review changed files in the sandbox candidate."]


def _blocker_rows(blockers: list[Any]) -> str:
    if not blockers:
        return "| `No blockers recorded.` | `none` |"
    return "\n".join(f"| `{str(blocker)}` | `open` |" for blocker in blockers[:4])


def _deterministic_patch_rows(ledger: dict[str, Any]) -> tuple[str, str]:
    patches = _extract_patch_records(ledger)
    if not patches:
        message = "No deterministic patches recorded for this profile."
        return f"| `{message}` | `not_applicable` | `not_applicable` | `not_applicable` |", "none"
    rows = []
    for patch in patches[:4]:
        rows.append(
            "| `{id}` | `{file}` | `{reason}` | `{status}` |".format(
                id=patch.get("id", "not_captured"),
                file=patch.get("file", "not_captured"),
                reason=patch.get("reason", "not_captured"),
                status=patch.get("status", "not_captured"),
            )
        )
    return "\n".join(rows), f"{len(patches)} deterministic patch record(s) found."


def _extract_patch_records(ledger: dict[str, Any]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    candidates = []
    for key in ("deterministic_patches", "patches", "applied_patches"):
        value = ledger.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        records.append(
            {
                "id": str(item.get("id") or item.get("patch_id") or ""),
                "file": str(item.get("file") or item.get("path") or item.get("area") or ""),
                "reason": str(item.get("reason") or item.get("description") or ""),
                "status": str(item.get("status") or "applied"),
            }
        )
    return records


def _write_copilot_report_response(
    run_dir: str | Path,
    manifest: CopilotReportManifest,
    payload: dict[str, Any],
) -> Path:
    response_path = manifest.response_path(run_dir)
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return response_path


def _template_only_defaults() -> tuple[str, ...]:
    return (
        "java_gap_status", "java_gap_notes", "boot_gap_status", "boot_gap_notes",
        "framework_gap_status", "framework_gap_notes", "jakarta_status", "jakarta_notes",
        "security_status", "security_notes", "batch_status", "batch_notes",
        "test_dependency_status", "test_dependency_notes", "preflight_started_at",
        "preflight_ended_at", "preflight_duration", "analysis_started_at", "analysis_ended_at",
        "analysis_duration", "planning_started_at", "planning_ended_at", "planning_duration",
        "assessment_started_at", "assessment_ended_at", "assessment_duration", "approval_started_at",
        "approval_ended_at", "sandbox_prep_status", "sandbox_prep_started_at", "sandbox_prep_ended_at",
        "sandbox_prep_duration", "transform_started_at", "transform_ended_at", "transform_duration",
        "build_started_at", "build_ended_at", "build_duration", "test_started_at", "test_ended_at",
        "test_duration", "final_report_started_at", "final_report_ended_at", "final_report_duration",
        "copilot_report_generation_started_at", "copilot_report_generation_ended_at",
        "copilot_report_generation_duration", "deterministic_patch_rows", "blocker_rows",
        "patch_1_id", "patch_1_file", "patch_1_reason", "patch_1_status",
        "patch_2_id", "patch_2_file", "patch_2_reason", "patch_2_status",
        "patch_3_id", "patch_3_file", "patch_3_reason", "patch_3_status",
        "patch_4_id", "patch_4_file", "patch_4_reason", "patch_4_status",
        "deterministic_patch_summary", "baseline_java_runtime", "baseline_build_command",
        "baseline_build_status", "target_java_runtime", "target_build_command", "target_build_status",
        "build_report_path", "build_log_path", "review_focus_1", "review_focus_2",
        "review_focus_3", "review_focus_4",
    )


def _empty_patch_template_defaults() -> dict[str, str]:
    message = "No deterministic patches recorded for this profile."
    values = {
        "deterministic_patch_summary": "none",
        "deterministic_patch_rows": f"| `{message}` | `not_applicable` | `not_applicable` | `not_applicable` |",
        "patch_1_id": message,
        "patch_1_file": "not_applicable",
        "patch_1_reason": "not_applicable",
        "patch_1_status": "not_applicable",
        "blocker_rows": "| `No blockers recorded.` | `none` |",
    }
    for index in range(2, 5):
        values[f"patch_{index}_id"] = " "
        values[f"patch_{index}_file"] = " "
        values[f"patch_{index}_reason"] = " "
        values[f"patch_{index}_status"] = " "
    return values


def _template_default_value(key: str) -> str:
    if key.endswith(("_started_at", "_ended_at", "_duration")):
        return "not_captured"
    if key.startswith(("review_focus_", "warning_", "blocker_")):
        return "none"
    return "not_available"


def _generate_copilot_cli_report(
    run_dir: str | Path,
    ai_hub_path: str | Path,
    *,
    context: dict[str, Any] | None,
    status: CopilotAdapterStatus | None,
    timeout_seconds: float,
    env: Mapping[str, str],
) -> dict[str, Any]:
    run_path = Path(run_dir)
    timeout_seconds = _configured_timeout_seconds(env, timeout_seconds)
    log_level = _configured_log_level(env)
    copilot_log_dir = run_path / COPILOT_LOG_DIR
    github_cli_log_dir = run_path / COPILOT_GITHUB_CLI_LOG_DIR
    copilot_log_dir.mkdir(parents=True, exist_ok=True)
    github_cli_log_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_copilot_report_manifest(ai_hub_path)
    detected_status = status or detect_copilot_cli_status(
        timeout_seconds=15.0,
        env=env,
        cwd=_copilot_cli_cwd(run_path),
    )
    cli_status = CopilotAdapterStatus(
        provider=PROVIDER,
        adapter=CLI_ADAPTER,
        model=_configured_model(env),
        connectivity=detected_status.connectivity,
        report_status="generated",
        auth_status=detected_status.auth_status,
        cli_status=detected_status.cli_status,
        resolved_executable_path=detected_status.resolved_executable_path,
    )
    request = build_copilot_report_request(run_dir, manifest, context=context, status=cli_status)
    if request.missing_required:
        write_copilot_report_request(run_dir, manifest, request.payload)
        warning = "missing required Copilot report artifacts: " + ", ".join(request.missing_required)
        response_path = _write_copilot_report_response(
            run_dir,
            manifest,
            _build_response_payload(cli_status, manifest, report_status="skipped", warnings=[*request.warnings, warning]),
        )
        raise ValueError(warning)

    request_path = write_copilot_report_request(run_dir, manifest, request.payload)
    template = manifest.template_path.read_text(encoding="utf-8")
    prompt = _build_strict_copilot_prompt(request.payload, template)
    warning = ""
    response_extra: dict[str, Any] = {
        "copilot_input_mode": COPILOT_INPUT_MODE,
        "copilot_log_dir": COPILOT_LOG_DIR.as_posix(),
    }
    validation_errors: list[str] = []
    invocation: CopilotCliRunResult | None = None
    try:
        invocation = _invoke_copilot_cli_raw(
            prompt,
            cli_status.model,
            timeout_seconds,
            cli_status.resolved_executable_path,
            cwd=_copilot_cli_cwd(run_path),
            log_dir=github_cli_log_dir,
            log_level=log_level,
        )
        _write_copilot_cli_output_files(copilot_log_dir, invocation)
        markdown = invocation.stdout.strip()
        validation_errors = _copilot_markdown_validation_errors(markdown, request_payload=request.payload)
        _write_copilot_validation_result(copilot_log_dir, validation_errors)
        if validation_errors:
            if validation_errors == ["copilot CLI returned empty output"]:
                raise RuntimeError("copilot CLI returned empty output")
            raise RuntimeError("copilot report validation failed: " + "; ".join(validation_errors))
        if invocation.exit_code != 0:
            stderr_tail = _redacted_tail(invocation.stderr)
            detail = f"; stderr_tail={stderr_tail}" if stderr_tail else ""
            raise RuntimeError(f"copilot CLI returned non-zero status{detail}")
        if not markdown:
            raise RuntimeError("copilot CLI returned empty output")
        if _looks_like_prompt_for_input(markdown) or _looks_like_prompt_for_input(invocation.stderr):
            raise RuntimeError("copilot CLI requested interactive input")
        report_status = "generated"
        response_status = cli_status
        _write_copilot_invocation_log(
            copilot_log_dir,
            cli_status,
            invocation,
            prompt,
            timeout_seconds,
            log_level,
            validation_status="passed",
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = round(float(getattr(exc, "timeout", timeout_seconds) or timeout_seconds), 3)
        now = _utc_now()
        invocation = CopilotCliRunResult(
            stdout=str(getattr(exc, "stdout", "") or ""),
            stderr=str(getattr(exc, "stderr", "") or ""),
            exit_code=None,
            timed_out=True,
            started_at=now,
            ended_at=now,
            elapsed_seconds=elapsed,
            command=_copilot_cli_command(
                cli_status.resolved_executable_path,
                cli_status.model,
                github_cli_log_dir,
                log_level,
            ),
        )
        _write_copilot_cli_output_files(copilot_log_dir, invocation)
        _write_copilot_validation_result(copilot_log_dir, [])
        _write_copilot_invocation_log(
            copilot_log_dir,
            cli_status,
            invocation,
            prompt,
            timeout_seconds,
            log_level,
            validation_status="not_run_timeout",
        )
        report_status = "generated_with_fallback"
        warning = f"copilot CLI timed out after {timeout_seconds}s; fallback used"
        response_extra.update(
            {
                "fallback_reason": "timeout",
                "timed_out": True,
                "copilot_timeout_seconds": timeout_seconds,
                "copilot_elapsed_seconds": elapsed,
                "copilot_prompt_chars": len(prompt),
            }
        )
        response_status = CopilotAdapterStatus(
            provider=PROVIDER,
            adapter=ADAPTER,
            model=cli_status.model,
            connectivity=cli_status.connectivity,
            report_status=report_status,
            auth_status=cli_status.auth_status,
            cli_status=cli_status.cli_status,
            resolved_executable_path=cli_status.resolved_executable_path,
        )
        fallback_request = build_copilot_report_request(
            run_dir,
            manifest,
            context=context,
            status=response_status,
        )
        markdown = render_copilot_report_template(
            manifest.template_path,
            fallback_request.payload["template_context"],
        )
    except Exception as exc:
        report_status = "generated_with_fallback"
        path_present = bool(cli_status.resolved_executable_path)
        if validation_errors:
            response_extra.update({"validation_failed": True, "validation_errors": validation_errors})
        warning = (
            "copilot CLI report generation failed; used deterministic fallback: "
            + _safe_exception_hint(exc)
            + f" (internal_resolved_executable_path_present={str(path_present).lower()})"
        )
        response_status = CopilotAdapterStatus(
            provider=PROVIDER,
            adapter=ADAPTER,
            model=cli_status.model,
            connectivity=cli_status.connectivity,
            report_status=report_status,
            auth_status=cli_status.auth_status,
            cli_status=cli_status.cli_status,
            resolved_executable_path=cli_status.resolved_executable_path,
        )
        fallback_request = build_copilot_report_request(
            run_dir,
            manifest,
            context=context,
            status=response_status,
        )
        markdown = render_copilot_report_template(
            manifest.template_path,
            fallback_request.payload["template_context"],
        )
        if invocation is None:
            now = _utc_now()
            invocation = CopilotCliRunResult(
                stdout="",
                stderr=_safe_exception_hint(exc),
                exit_code=None,
                timed_out=False,
                started_at=now,
                ended_at=now,
                elapsed_seconds=0.0,
                command=_copilot_cli_command(
                    cli_status.resolved_executable_path,
                    cli_status.model,
                    github_cli_log_dir,
                    log_level,
                )
                if cli_status.resolved_executable_path
                else [],
            )
        if invocation is not None:
            _write_copilot_cli_output_files(copilot_log_dir, invocation)
            _write_copilot_invocation_log(
                copilot_log_dir,
                cli_status,
                invocation,
                prompt,
                timeout_seconds,
                log_level,
                validation_status="failed" if validation_errors else "not_run",
            )

    report_path = manifest.output_path(run_dir)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    warnings = [*request.warnings, *([warning] if warning else [])]
    response_payload = _build_response_payload(
        response_status,
        manifest,
        report_status=report_status,
        warnings=warnings,
        extra=response_extra,
    )
    response_path = _write_copilot_report_response(run_dir, manifest, response_payload)
    return {
        "artifact_refs": {
            "copilot_report_request": str(request_path),
            "copilot_report_response": str(response_path),
            "copilot_migration_report": str(report_path),
        },
        "warnings": warnings,
        "response": response_payload,
    }


def _build_strict_copilot_prompt(request_payload: dict[str, Any], template: str) -> str:
    compact_context = _compact_copilot_prompt_context(request_payload)
    return "\n".join(
        [
            "Role: You are GitHub Copilot CLI generating an advisory final migration report.",
            "Use this template exactly. Preserve headings and section order. Replace placeholders only from "
            "the supplied deterministic context. Use enriched_context first. Do not output `not_available` when "
            "enriched_context provides a safer derived value. Return markdown only.",
            "Do not invent exact versions, paths, or times. Use `not_captured` for telemetry not captured, `none` "
            "for intentionally absent values, and `not_detected_in_current_artifacts` for items searched but not found.",
            "If blockers list is empty, write `No blockers recorded.` If no deterministic patches are recorded, "
            "write one row only.",
            "Do not include a preamble, explanation, code fences, session notes, or metadata.",
            "Use only the deterministic JSON context. Do not request input. Do not run commands. Do not write files.",
            "Preserve these advisory guardrails: advisory_only=true, can_approve=false, can_transform=false, "
            "can_change_gates=false, can_mutate_source=false, can_override_status=false, "
            "can_create_pr=false, can_deploy=false.",
            "Copilot must not claim it approved, transformed, tested, deployed, merged, or created pull requests.",
            "",
            "AI Hub report template:",
            template,
            "",
            "Approved compact deterministic context:",
            json.dumps(compact_context, indent=2, sort_keys=True),
        ]
    )


def _compact_copilot_prompt_context(request_payload: dict[str, Any]) -> dict[str, Any]:
    context = _dict(request_payload.get("template_context"))
    artifacts = _dict(request_payload.get("artifacts"))
    final_report = _dict(_dict(artifacts.get("required")).get("final/migration_report.json"))
    timing = _dict(_dict(artifacts.get("optional")).get("performance/timing_report.json"))
    plan = _dict(_dict(artifacts.get("optional")).get("transformation/transformation_execution_plan.yaml"))
    artifact_refs = _dict(final_report.get("artifact_refs"))
    artifact_ref_summary = {
        key: value
        for key, value in artifact_refs.items()
        if key
        in {
            "final_migration_report",
            "final_migration_summary",
            "orchestration_summary",
            "approval_decision",
            "approved_plan_lock",
            "post_transform_test_report",
            "timing_report",
            "phase2_log",
        }
    }
    return _redact(
        {
            "manifest": request_payload.get("manifest", {}),
            "guardrails": request_payload.get("guardrails", {}),
            "run_id": context.get("run_id"),
            "application_name": context.get("application_name"),
            "profile_id": context.get("profile_id"),
            "mode": context.get("mode"),
            "source_stack": {
                "java": context.get("source_java_version"),
                "spring_boot": context.get("source_spring_boot_version"),
                "spring_framework": context.get("source_spring_framework_version"),
                "build_tool": context.get("source_build_tool"),
                "packaging": context.get("source_packaging"),
            },
            "target_stack": {
                "java": context.get("target_java_version"),
                "spring_boot": context.get("target_spring_boot_version"),
                "spring_framework": context.get("target_spring_framework_version"),
                "build_tool": context.get("target_build_tool"),
                "packaging": context.get("target_packaging"),
            },
            "approval_summary": {
                "status": context.get("approval_status"),
                "decision": context.get("approval_decision"),
                "approved_by": context.get("approved_by"),
                "source": context.get("approval_source"),
                "summary": context.get("approval_summary"),
            },
            "statuses": {
                "orchestration": context.get("orchestration_status"),
                "transform": context.get("transform_status"),
                "build": context.get("build_status"),
                "test": context.get("test_status"),
                "final": context.get("final_verdict"),
            },
            "test_totals": {
                "tests": context.get("tests_run"),
                "passed": context.get("tests_passed"),
                "failures": context.get("tests_failed"),
                "errors": context.get("test_errors"),
                "skipped": context.get("tests_skipped"),
            },
            "executed_recipes": plan.get("recipes", [])[:20] if isinstance(plan.get("recipes"), list) else [],
            "warnings": final_report.get("warnings", [])[:10] if isinstance(final_report.get("warnings"), list) else [],
            "timing_summary": {
                "total_machine_duration": context.get("total_machine_duration"),
                "phase_durations_seconds": _dict(timing.get("phase_durations_seconds")),
            },
            "artifact_refs_summary": artifact_ref_summary,
            "copilot_advisory_scope": {
                "provider": context.get("copilot_provider"),
                "adapter": context.get("copilot_adapter"),
                "model": context.get("copilot_model"),
                "advisory_only": True,
                "can_approve": False,
                "can_transform": False,
                "can_mutate_source": False,
                "can_change_gates": False,
                "can_create_pr": False,
                "can_deploy": False,
            },
            "enriched_context": {
                "risk_areas": {
                    "java": context.get("java_gap_status"),
                    "spring_boot": context.get("boot_gap_status"),
                    "spring_framework": context.get("framework_gap_status"),
                    "jakarta": context.get("jakarta_status"),
                    "security": context.get("security_status"),
                    "batch": context.get("batch_status"),
                    "test_dependencies": context.get("test_dependency_status"),
                },
                "blocker_rows": context.get("blocker_rows"),
                "deterministic_patch_rows": context.get("deterministic_patch_rows"),
                "review_focus": [
                    context.get("review_focus_1"),
                    context.get("review_focus_2"),
                    context.get("review_focus_3"),
                    context.get("review_focus_4"),
                ],
            },
            "template_context": context,
            "missing_required": request_payload.get("missing_required", []),
            "missing_optional": request_payload.get("missing_optional", []),
        }
    )


def _invoke_copilot_cli(
    prompt: str,
    model: str,
    timeout_seconds: float,
    resolved_executable_path: str = "",
    *,
    cwd: str | Path | None = None,
) -> str:
    effective_cwd = Path(cwd).resolve() if cwd is not None else _copilot_probe_cwd()
    log_dir = effective_cwd / COPILOT_GITHUB_CLI_LOG_DIR
    result = _invoke_copilot_cli_raw(
        prompt,
        model,
        _clamp_timeout_seconds(timeout_seconds),
        resolved_executable_path,
        cwd=effective_cwd,
        log_dir=log_dir,
        log_level=DEFAULT_COPILOT_LOG_LEVEL,
    )
    output = (result.stdout or "").strip()
    if result.exit_code != 0:
        stderr_tail = _redacted_tail(result.stderr)
        detail = f"; stderr_tail={stderr_tail}" if stderr_tail else ""
        raise RuntimeError(f"copilot CLI returned non-zero status{detail}")
    if not output:
        raise RuntimeError("copilot CLI returned empty output")
    if _looks_like_prompt_for_input(output) or _looks_like_prompt_for_input(result.stderr):
        raise RuntimeError("copilot CLI requested interactive input")
    _validate_copilot_markdown(output)
    return _redact(output)


def _invoke_copilot_cli_raw(
    prompt: str,
    model: str,
    timeout_seconds: int,
    resolved_executable_path: str = "",
    *,
    cwd: str | Path | None = None,
    log_dir: str | Path,
    log_level: str,
) -> CopilotCliRunResult:
    command = resolved_executable_path if isinstance(resolved_executable_path, str) else ""
    if not command.strip():
        raise FileNotFoundError("Copilot executable path was not resolved for live call")
    command_args = _copilot_cli_command(command, model, Path(log_dir), log_level)
    effective_cwd = Path(cwd).resolve() if cwd is not None else _copilot_probe_cwd()
    started = time.monotonic()
    started_at = _utc_now()
    try:
        completed = subprocess.run(
            command_args,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=str(effective_cwd),
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise
    ended_at = _utc_now()
    return CopilotCliRunResult(
        stdout=_redact(completed.stdout or ""),
        stderr=_redact(completed.stderr or ""),
        exit_code=completed.returncode,
        timed_out=False,
        started_at=started_at,
        ended_at=ended_at,
        elapsed_seconds=round(time.monotonic() - started, 3),
        command=command_args,
    )


def _copilot_cli_cwd(run_dir: str | Path) -> Path:
    cwd = (Path(run_dir) / COPILOT_LOG_DIR).resolve()
    cwd.mkdir(parents=True, exist_ok=True)
    return cwd


def _copilot_cli_command(command: str, model: str, log_dir: Path, log_level: str) -> list[str]:
    return [
        command,
        "-s",
        "--no-ask-user",
        "--model",
        model or DEFAULT_COPILOT_MODEL,
        "--log-dir",
        str(log_dir),
        "--log-level",
        log_level,
    ]


def _validate_copilot_markdown(
    markdown: str,
    *,
    request_payload: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
) -> None:
    errors = _copilot_markdown_validation_errors(
        markdown,
        request_payload=request_payload,
        response_payload=response_payload,
    )
    if errors:
        if len(errors) == 1 and errors[0] == "copilot CLI returned empty output":
            raise RuntimeError(errors[0])
        raise RuntimeError("copilot report validation failed: " + "; ".join(errors))


def _copilot_markdown_validation_errors(
    markdown: str,
    *,
    request_payload: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
) -> list[str]:
    text = _redact(markdown or "").strip()
    if not text:
        return ["copilot CLI returned empty output"]
    errors: list[str] = []
    for section in _REQUIRED_REPORT_SECTIONS:
        if section not in text:
            errors.append(f"missing section {section}")
    if text.startswith("```") and text.endswith("```"):
        errors.append("report wrapped in code fence")
    if text != markdown.strip():
        errors.append("output contained redacted secret material")
    for pattern in _FORBIDDEN_COPILOT_CLAIMS:
        if pattern.search(text):
            errors.append("forbidden Copilot execution claim")
    context = _dict((request_payload or {}).get("template_context"))
    legacy_path = str(context.get("legacy_app_path") or "").strip()
    if legacy_path not in {"", "not_available"} and re.search(r"\|\s*Application\s*\|\s*`not_available`\s*\|", text):
        errors.append("application is not_available while legacy_app_path exists")
    blockers_match = re.search(r"Blockers:\s*\n(?P<body>.*?)(?:\nRisk summary:|\Z)", text, re.S)
    blockers_body = blockers_match.group("body") if blockers_match else text
    if re.search(r"\|\s*``\s*\|\s*``\s*\|", blockers_body):
        errors.append("blockers section contains empty rows")
    patches_match = re.search(r"## 8\. Deterministic Patches(?P<body>.*?)(?:\n---|\Z)", text, re.S)
    patches_body = patches_match.group("body") if patches_match else ""
    if "No deterministic patches recorded for this profile." in patches_body and re.search(
        r"\|\s*`not_available`\s*\|",
        patches_body,
    ):
        errors.append("deterministic patch section contains extra not_available rows")
    adapter_status = _dict((request_payload or {}).get("adapter_status"))
    response = response_payload or adapter_status
    live_success = (
        str(response.get("adapter") or adapter_status.get("adapter") or "") == CLI_ADAPTER
        and str(response.get("report_status") or adapter_status.get("report_status") or "") == "generated"
    )
    if live_success and re.search(r"\|\s*Fallback Used\s*\|\s*`true`\s*\|", text, re.I):
        errors.append("fallback used is true for generated copilot_cli report")
    return errors


def _looks_like_prompt_for_input(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("ask user", "continue?", "confirm", "waiting for input"))


def _write_copilot_cli_output_files(log_dir: Path, result: CopilotCliRunResult) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout = _redact(result.stdout or "").strip()
    stderr_tail = _redacted_tail(result.stderr)
    if stdout:
        (log_dir / "copilot_cli_stdout.md").write_text(stdout.rstrip() + "\n", encoding="utf-8")
    if stderr_tail:
        (log_dir / "copilot_cli_stderr.redacted.log").write_text(stderr_tail.rstrip() + "\n", encoding="utf-8")


def _write_copilot_invocation_log(
    log_dir: Path,
    status: CopilotAdapterStatus,
    result: CopilotCliRunResult,
    prompt: str,
    timeout_seconds: int,
    log_level: str,
    *,
    validation_status: str,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "input_mode": COPILOT_INPUT_MODE,
        "command_basename": _safe_executable_basename(status.resolved_executable),
        "args_without_full_path": _diagnostic_args_without_full_path(result.command),
        "cwd": str(log_dir.parents[1]) if len(log_dir.parents) > 1 else "",
        "timeout_seconds": timeout_seconds,
        "model": _normalize_model(status.model),
        "log_level": log_level,
        "started_at": result.started_at,
        "ended_at": result.ended_at,
        "elapsed_seconds": result.elapsed_seconds,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "prompt_chars": len(prompt),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "stdout_chars": len(result.stdout or ""),
        "stderr_chars": len(result.stderr or ""),
        "validation_status": validation_status,
    }
    (log_dir / "copilot_cli_invocation.json").write_text(
        json.dumps(_redact(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_copilot_validation_result(log_dir: Path, validation_errors: list[str]) -> None:
    payload = {
        "validation_failed": bool(validation_errors),
        "validation_errors": validation_errors,
        "validation_status": "failed" if validation_errors else "passed",
        "created_at": _utc_now(),
    }
    (log_dir / "copilot_validation_result.json").write_text(
        json.dumps(_redact(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _redacted_tail(text: str, *, max_chars: int = 4000) -> str:
    return str(_redact((text or "")[-max_chars:]))


def _diagnostic_args_without_full_path(command: list[str]) -> list[str]:
    args = list(command[1:]) if command else []
    sanitized: list[str] = []
    replace_next_log_dir = False
    for arg in args:
        if replace_next_log_dir:
            sanitized.append(COPILOT_GITHUB_CLI_LOG_DIR.as_posix())
            replace_next_log_dir = False
            continue
        sanitized.append(arg)
        if arg == "--log-dir":
            replace_next_log_dir = True
    return sanitized


def _artifact_payload(artifacts: dict[str, Any], relative_path: str) -> dict[str, Any]:
    required = _dict(artifacts.get("required"))
    optional = _dict(artifacts.get("optional"))
    payload = required.get(relative_path) or optional.get(relative_path) or {}
    return payload if isinstance(payload, dict) else {}


def _safe_read_artifact(path: Path, warnings: list[str]) -> Any:
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        warnings.append(f"unable to read Copilot report artifact {path.name}: {exc}")
        return {}
    return _redact(payload)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _model_source(model: str) -> str:
    return "configured" if model and model != "unknown" else "unknown"


def _configured_model(env: Mapping[str, str]) -> str:
    configured = str(env.get(MODEL_ENV, "")).strip()
    if configured:
        return _normalize_model(str(_redact(configured)))
    return DEFAULT_COPILOT_MODEL


def _configured_timeout_seconds(env: Mapping[str, str], fallback: float | int = DEFAULT_COPILOT_TIMEOUT_SECONDS) -> int:
    raw = str(env.get(TIMEOUT_ENV, "")).strip()
    value: float | int = fallback
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = DEFAULT_COPILOT_TIMEOUT_SECONDS
    return _clamp_timeout_seconds(value)


def _clamp_timeout_seconds(value: float | int) -> int:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        seconds = DEFAULT_COPILOT_TIMEOUT_SECONDS
    return max(MIN_COPILOT_TIMEOUT_SECONDS, min(MAX_COPILOT_TIMEOUT_SECONDS, seconds))


def _configured_log_level(env: Mapping[str, str]) -> str:
    value = str(env.get(LOG_LEVEL_ENV, "")).strip().lower() or DEFAULT_COPILOT_LOG_LEVEL
    return value if value in _ALLOWED_COPILOT_LOG_LEVELS else DEFAULT_COPILOT_LOG_LEVEL


def _normalize_model(model: str) -> str:
    value = str(model or "").strip()
    while value.startswith(("configured:", "detected:")):
        value = value.split(":", 1)[1].strip()
    return value or DEFAULT_COPILOT_MODEL


def _find_copilot_command(timeout_seconds: float) -> str | None:
    found = resolve_copilot_cli_executable(is_windows=_is_windows())
    if found:
        return found
    where_commands = tuple(dict.fromkeys(item for item in (shutil.which("where.exe"), shutil.which("where")) if item))
    for where_exe in where_commands:
        try:
            completed = subprocess.run(
                [where_exe, "copilot"],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, TimeoutError):
            continue
        if completed.returncode != 0:
            continue
        candidates = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
        cmd_candidate = next(
            (candidate for candidate in candidates if _path_basename(candidate).lower() == "copilot.cmd"),
            None,
        )
        if cmd_candidate and _looks_like_copilot_command(cmd_candidate):
            return cmd_candidate
        for candidate in candidates:
            if _looks_like_copilot_command(candidate):
                return candidate
    return None


def _copilot_version_proves_cli(command: str, timeout_seconds: float, *, cwd: Path) -> bool:
    try:
        completed = subprocess.run(
            [command, "version"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return False
    return _looks_like_copilot_version(completed.stdout or "")


def _copilot_probe_cwd(cwd: str | Path | None = None) -> Path:
    probe_cwd = Path(cwd) if cwd is not None else Path(tempfile.gettempdir()) / "ai-migration-copilot-probe"
    probe_cwd.mkdir(parents=True, exist_ok=True)
    return probe_cwd.resolve()


def _detect_gh_auth_status(timeout_seconds: float) -> str:
    gh_path = shutil.which("gh")
    if not gh_path:
        return "unknown"
    try:
        completed = subprocess.run(
            [gh_path, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return "unknown"
    auth_text = f"{completed.stdout or ''}\n{completed.stderr or ''}".lower()
    return "authenticated" if completed.returncode == 0 and "logged in" in auth_text else "unknown"


def _looks_like_copilot_command(path: str) -> bool:
    name = _path_basename(path).lower()
    return name in {"copilot", "copilot.exe", "copilot.cmd", "copilot.bat"} or name.startswith("copilot.")


def _looks_like_copilot_version(text: str) -> bool:
    lowered = text.lower()
    return "github copilot cli" in lowered or "copilot cli" in lowered


def _safe_executable_basename(path: str) -> str:
    return _path_basename(path) if path else ""


def _path_basename(path: str) -> str:
    return re.split(r"[\\/]", str(path))[-1]


def _safe_exception_hint(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError) and not str(exc):
        return "FileNotFoundError: Copilot executable path was not resolved for live call"
    message = str(exc).strip()
    if isinstance(exc, FileNotFoundError) and "resolved for live call" not in message:
        message = "Copilot executable path was not resolved for live call"
    if not message:
        return type(exc).__name__
    return f"{type(exc).__name__}: {_redact(message)}"


def _timing_template_context(timing: dict[str, Any]) -> dict[str, Any]:
    phase_durations = _dict(timing.get("phase_durations_seconds"))
    result: dict[str, Any] = {}
    phase_names = {
        "preflight": "preflight",
        "analysis": "analysis",
        "planning": "planning",
        "assessment": "assessment",
        "approval": "approval",
        "sandbox_prep": "sandbox_prep",
        "transform": "transform",
        "build": "build",
        "test": "test",
        "final_report": "final_report",
    }
    for phase, prefix in phase_names.items():
        if phase in phase_durations:
            result[f"{prefix}_duration"] = f"{phase_durations[phase]}s"
            result[f"{prefix}_started_at"] = "not_captured"
            result[f"{prefix}_ended_at"] = "not_captured"
    return result


def _build_command_context(timing: dict[str, Any], test_report: dict[str, Any], final_report: dict[str, Any]) -> dict[str, Any]:
    commands = [row for row in list(timing.get("commands", []) or []) if isinstance(row, dict)]
    build_command = _first_command(commands, ("build", "maven", "mvn"))
    test_command = _first_command(commands, ("test", "surefire", "mvn"))
    build_report_path = final_report.get("build_report_path", "")
    if not build_report_path and (final_report.get("build_status") or test_command):
        build_report_path = "Build validation is represented by test/post_transform/test_report.json and timing_report.json"
    return {
        "target_build_command": build_command or test_command,
        "target_build_status": final_report.get("build_status", ""),
        "target_java_runtime": test_report.get("java_runtime") or final_report.get("target_java_runtime", ""),
        "build_report_path": build_report_path,
        "build_log_path": final_report.get("build_log_path") or test_report.get("test_log_path", ""),
        "test_notes": "Command: " + test_command if test_command else "",
    }


def _copilot_duration_context(invocation: dict[str, Any]) -> dict[str, str]:
    if not invocation:
        return {
            "copilot_report_generation_started_at": "not_captured",
            "copilot_report_generation_ended_at": "not_captured",
            "copilot_report_generation_duration": "not_captured",
        }
    elapsed = invocation.get("elapsed_seconds")
    duration = f"{elapsed}s" if isinstance(elapsed, (int, float)) else str(elapsed or "not_captured")
    return {
        "copilot_report_generation_started_at": str(invocation.get("started_at") or "not_captured"),
        "copilot_report_generation_ended_at": str(invocation.get("ended_at") or "not_captured"),
        "copilot_report_generation_duration": duration,
    }


def _first_command(commands: list[dict[str, Any]], markers: tuple[str, ...]) -> str:
    for row in commands:
        label = str(row.get("label") or "").lower()
        command = " ".join(str(part) for part in list(row.get("command") or []) if str(part))
        haystack = f"{label} {command}".lower()
        if any(marker in haystack for marker in markers):
            return command
    return ""


def _total_machine_duration(timing: dict[str, Any]) -> str:
    phase_durations = _dict(timing.get("phase_durations_seconds"))
    value = phase_durations.get("total_run") or timing.get("total_machine_duration")
    return f"{value}s" if isinstance(value, (int, float)) else str(value or "")


def _display_value(value: Any) -> Any:
    if value is None:
        return "not_available"
    if isinstance(value, str):
        return value if value.strip() else "not_available"
    return value


def _redact(value: Any, key: str = "") -> Any:
    if any(part in key.lower() for part in _SECRET_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_VALUE_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        redacted = _redact_user_home_path(redacted)
        return redacted
    return value


def _redact_user_home_path(text: str) -> str:
    home = str(Path.home())
    if home and home not in {".", "/"}:
        text = text.replace(home, "%USERPROFILE%")
        text = text.replace(home.replace("\\", "/"), "%USERPROFILE%")
    return text


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def debug_status_payload(env: Mapping[str, str] | None = None) -> dict[str, str]:
    effective_env = env or os.environ
    status = detect_copilot_cli_status(timeout_seconds=15.0, env=effective_env)
    report_provider = str(effective_env.get(PROVIDER_ENV, "")).strip() or status.adapter
    return {
        "provider": CLI_ADAPTER if report_provider == CLI_ADAPTER else status.provider,
        "model": _configured_model(effective_env),
        "cli_status": status.cli_status,
        "auth_status": status.auth_status,
        "resolved_executable_basename": _safe_executable_basename(status.resolved_executable),
        "report_provider": report_provider,
    }


def _print_debug_status() -> None:
    for key, value in debug_status_payload().items():
        print(f"{key}={value}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Copilot final report diagnostics")
    parser.add_argument("--debug-status", action="store_true", help="print debug-safe Copilot report status")
    args = parser.parse_args()
    if args.debug_status:
        _print_debug_status()
        return
    parser.print_help()


if __name__ == "__main__":
    main()
