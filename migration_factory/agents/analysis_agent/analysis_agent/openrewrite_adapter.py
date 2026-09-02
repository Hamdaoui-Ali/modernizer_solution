import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from rewrite_catalog_loader import load_rewrite_catalog
from rewrite_command_builder import build_rewrite_maven_command
from rewrite_impact_analyzer import analyze_rewrite_patch
from rewrite_plugin_plan_writer import write_rewrite_plugin_plan


def _find_dry_run_patch(project_dir: Path):
    candidates = [
        project_dir / "rewrite.patch",
        project_dir / "target" / "rewrite.patch",
        project_dir / "target" / "rewrite" / "rewrite.patch",
        project_dir / "target" / "site" / "rewrite" / "rewrite.patch",
        project_dir / "target" / "openrewrite" / "rewrite.patch",
        project_dir / "target" / "openrewrite" / "rewrite.diff",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _hash_project_sources(project_dir: Path):
    digest = hashlib.sha256()
    root_dirs = [project_dir / "src", project_dir / "pom.xml"]
    tracked = []
    for root in root_dirs:
        if root.is_file():
            tracked.append(root)
        elif root.is_dir():
            tracked.extend(sorted(p for p in root.rglob("*") if p.is_file()))

    for path in sorted(tracked):
        rel = path.relative_to(project_dir).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(path.read_bytes())

    return digest.hexdigest()


def _write_json(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4)


def _tail(text, limit=4000):
    text = text or ""
    return text[-limit:]


def _failure_diagnostic(exc, cmd, cwd):
    diagnostic = {
        "command": list(cmd or []),
        "cwd": str(cwd),
        "exit_code": getattr(exc, "returncode", None),
        "stdout_tail": _tail(getattr(exc, "stdout", "")),
        "stderr_tail": _tail(getattr(exc, "stderr", "")),
    }
    if diagnostic["exit_code"] is None:
        diagnostic["error"] = str(exc)
    return diagnostic


def _impact_summary(
    context,
    status,
    overall_impact,
    *,
    analysis=None,
    blocked_reasons=None,
    warnings=None,
    source_modified=False,
    failure_diagnostic=None,
):
    analysis = analysis or {}
    return {
        "schema_version": "1.0.0",
        "run_id": getattr(context, "run_id", "unknown"),
        "agent": "analysis_agent",
        "phase": "analysis",
        "status": status,
        "overall_impact": overall_impact,
        "changed_files": analysis.get("changed_files", []),
        "high_risk_files": analysis.get("high_risk_files", []),
        "migration_signals": analysis.get(
            "migration_signals",
            {
                "api_or_boot_upgrade": False,
                "javax_removed": False,
                "boot_2_to_3_gap": False,
                "java_11_to_17_gap": False,
                "javax_present": False,
                "boot_2_to_4_gap": False,
                "boot4_target": False,
                "java_8_to_21_gap": False,
                "java_21_target": False,
                "security_config_touched": False,
                "datasource_config_touched": False,
            },
        ),
        "blocked_reasons": blocked_reasons or [],
        "warnings": warnings or [],
        "failure_diagnostic": failure_diagnostic,
        "source_modified": source_modified,
        "artifact_refs": {"self": "rewrite_impact_summary.json"},
    }


def run_openrewrite_dryrun(context, analysis_facts=None):
    result_data = {
        "status": "SKIPPED",
        "warnings": [],
        "command": [],
        "cwd": None,
        "exit_code": None,
        "patch_path": None,
        "stdout_tail": "",
        "stderr_tail": "",
        "patch_produced": False,
    }
    project_dir = Path(context.legacy_app_path)
    preview_path = context.get_output_path("rewrite_preview.json")
    plan_path = context.get_output_path("rewrite_plugin_plan.json")
    impact_path = context.get_output_path("rewrite_impact_summary.json")

    catalog = load_rewrite_catalog(context)
    write_rewrite_plugin_plan(plan_path, context, catalog)
    preview_maven_args = catalog.get("openrewrite", {}).get("analysis_preview_maven_args", [])
    preview_skip_warning = None
    if "-Denforcer.skip=true" in preview_maven_args:
        preview_skip_warning = (
            "Legacy Maven Enforcer Java range skipped for OpenRewrite preview only; "
            "final sandbox validation must run without preview-only skip."
        )
        result_data["warnings"].append(preview_skip_warning)

    if catalog["status"] != "USED":
        if catalog.get("errors"):
            result_data["warnings"].extend(catalog["errors"])
        if catalog["status"] == "FAILED":
            result_data["status"] = "FAILED"
        _write_json(preview_path, result_data)
        _write_json(impact_path, _impact_summary(context, "SKIPPED", "UNKNOWN"))
        return result_data

    before_hash = _hash_project_sources(project_dir)

    try:
        cmd = build_rewrite_maven_command(catalog["openrewrite"])
        completed = subprocess.run(cmd, cwd=str(project_dir), capture_output=True, text=True, check=True)
        result_data["status"] = "USED"
        result_data["command"] = list(cmd)
        result_data["cwd"] = str(project_dir)
        result_data["exit_code"] = completed.returncode
        result_data["stdout_tail"] = _tail(completed.stdout)
        result_data["stderr_tail"] = _tail(completed.stderr)

        patch_source = _find_dry_run_patch(project_dir)
        if patch_source:
            patch_target = Path(context.get_output_path("rewrite_dry_run.patch"))
            shutil.copyfile(patch_source, patch_target)
            result_data["patch_file"] = "rewrite_dry_run.patch"
            result_data["patch_path"] = str(patch_source)
            result_data["patch_produced"] = True
            impact = analyze_rewrite_patch(
                patch_target.read_text(encoding="utf-8"),
                analysis_facts=analysis_facts,
            )
            _write_json(
                impact_path,
                _impact_summary(
                    context,
                    "PASS",
                    impact["overall_impact"],
                    analysis=impact,
                    warnings=[preview_skip_warning] if preview_skip_warning else [],
                ),
            )
        else:
            result_data["patch_path"] = None
            result_data["patch_produced"] = False
            impact = analyze_rewrite_patch("", analysis_facts=analysis_facts)
            _write_json(
                impact_path,
                _impact_summary(
                    context,
                    "WARNING",
                    impact["overall_impact"],
                    analysis=impact,
                    warnings=[preview_skip_warning] if preview_skip_warning else [],
                ),
            )

    except FileNotFoundError:
        result_data["command"] = list(locals().get("cmd") or [])
        result_data["cwd"] = str(project_dir)
        result_data["status"] = "SKIPPED"
        result_data["warnings"].append("OpenRewrite dry-run skipped: Maven executable not found")
        _write_json(impact_path, _impact_summary(context, "SKIPPED", "UNKNOWN"))
    except Exception as exc:
        diagnostic = _failure_diagnostic(exc, locals().get("cmd"), project_dir)
        result_data["status"] = "FAILED"
        result_data["warnings"].append(f"OpenRewrite dry-run failed: {exc}")
        result_data["failure_diagnostic"] = diagnostic
        result_data["command"] = diagnostic["command"]
        result_data["cwd"] = diagnostic["cwd"]
        result_data["exit_code"] = diagnostic["exit_code"]
        result_data["stdout_tail"] = diagnostic["stdout_tail"]
        result_data["stderr_tail"] = diagnostic["stderr_tail"]
        result_data["patch_path"] = None
        result_data["patch_produced"] = False
        blocked = [f"OpenRewrite dry-run failed with exit code {diagnostic.get('exit_code')}"]
        if diagnostic.get("stdout_tail"):
            blocked.append(f"OpenRewrite stdout tail: {diagnostic['stdout_tail']}")
        if diagnostic.get("stderr_tail"):
            blocked.append(f"OpenRewrite stderr tail: {diagnostic['stderr_tail']}")
        _write_json(
            impact_path,
            _impact_summary(
                context,
                "FAIL",
                "BLOCKED",
                blocked_reasons=blocked,
                warnings=[preview_skip_warning] if preview_skip_warning else [],
                failure_diagnostic=diagnostic,
            ),
        )
    finally:
        after_hash = _hash_project_sources(project_dir)
        if before_hash != after_hash:
            result_data["status"] = "FAILED"
            result_data["warnings"].append(
                "Source safety violation: project sources changed during dry-run"
            )
            _write_json(
                impact_path,
                _impact_summary(
                    context,
                    "FAIL",
                    "BLOCKED",
                    blocked_reasons=[
                        "Source safety violation: project sources changed during dry-run"
                    ],
                    source_modified=True,
                ),
            )

        _write_json(preview_path, result_data)

    return result_data
