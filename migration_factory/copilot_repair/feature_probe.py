from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from migration_factory.contracts import SCHEMA_VERSION
from migration_factory.copilot_cli import resolve_copilot_cli_executable
from migration_factory.copilot_repair.skill_validator import validate_agent_and_skills


REQUIRED_FLAGS = {
    "--prompt",
    "--agent",
    "--no-ask-user",
}
SAFETY_FLAGS = {
    "--silent",
    "--no-custom-instructions",
    "--no-remote",
    "--disable-builtin-mcps",
    "--available-tools",
    "--deny-tool",
}
OPTIONAL_FLAGS = {"--model"}


def probe_copilot_availability(
    *,
    repo_root: str | Path,
    run_dir: str | Path,
    provider: str = "copilot_cli",
    model: str = "",
    required: bool = False,
    timeout_seconds: int = 15,
    executable: str = "copilot",
    run=subprocess.run,
) -> dict[str, Any]:
    run_path = Path(run_dir)
    output_path = run_path / "preflight" / "copilot_availability.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    probe_cwd = run_path / "preflight" / "copilot_probe"
    probe_cwd.mkdir(parents=True, exist_ok=True)

    payload = _base_payload(provider=provider, model=model)
    if provider != "copilot_cli":
        payload.update({"status": "SKIPPED", "reason": f"provider {provider} is not copilot_cli"})
        return _write(output_path, payload)

    cli_path = resolve_copilot_cli_executable(executable)
    if not cli_path and run is not subprocess.run:
        cli_path = executable
    payload["cli_path"] = cli_path or ""
    if not cli_path:
        payload.update(
            {
                "status": "UNAVAILABLE",
                "reason": "copilot cli executable was not found",
                "agent_status": "SKIPPED",
                "skills_status": "SKIPPED",
                "dry_probe_status": "FAILED",
                "errors": ["Copilot executable path was not resolved"],
            }
        )
        return _write(output_path, payload)
    try:
        help_result = run(
            [cli_path, "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            cwd=probe_cwd,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        payload.update(
            {
                "status": "UNAVAILABLE",
                "reason": f"copilot cli help probe failed: {exc}",
                "agent_status": "SKIPPED",
                "skills_status": "SKIPPED",
                "dry_probe_status": "FAILED",
                "errors": [str(exc)],
            }
        )
        return _write(output_path, payload)

    help_text = "\n".join([help_result.stdout or "", help_result.stderr or ""])
    supported_flags = _extract_supported_flags(help_text)
    missing_required = sorted(REQUIRED_FLAGS - supported_flags)
    # These safety flags materially reduce accidental context/tool exposure. If absent,
    # proposal mode is unavailable, even when Copilot itself is installed.
    missing_safety = sorted(SAFETY_FLAGS - supported_flags)
    payload["supported_flags"] = sorted(supported_flags)
    payload["missing_required_flags"] = [*missing_required, *missing_safety]

    version = ""
    try:
        version_result = run(
            [cli_path, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            cwd=probe_cwd,
        )
        version = (version_result.stdout or version_result.stderr or "").strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        version = ""
    payload["cli_version"] = version

    validation = validate_agent_and_skills(Path(repo_root))
    payload["agent_status"] = validation["agent_status"]
    payload["skills_status"] = validation["skills_status"]
    payload["warnings"] = list(validation["warnings"])
    payload["errors"] = [*list(payload["errors"]), *list(validation["errors"])]

    if help_result.returncode != 0:
        payload.update({"status": "UNAVAILABLE", "reason": "copilot --help exited nonzero"})
    elif payload["missing_required_flags"]:
        payload.update({"status": "UNAVAILABLE", "reason": "required Copilot CLI safety flags missing"})
    elif validation["agent_status"] != "FOUND" or validation["skills_status"] != "FOUND":
        payload.update({"status": "UNAVAILABLE", "reason": "custom repair agent or skills are invalid"})
    else:
        payload.update({"status": "AVAILABLE", "reason": "required Copilot repair proposal capabilities found"})
    payload["dry_probe_status"] = "PASSED" if payload["status"] == "AVAILABLE" else "FAILED"
    if not required and payload["status"] == "UNAVAILABLE":
        payload["warnings"] = [*list(payload["warnings"]), "Copilot repair proposal mode unavailable; continuing because it is optional."]
    return _write(output_path, payload)


def _extract_supported_flags(help_text: str) -> set[str]:
    supported: set[str] = set()
    for flag in [*REQUIRED_FLAGS, *SAFETY_FLAGS, *OPTIONAL_FLAGS]:
        if flag in help_text:
            supported.add(flag)
    return supported


def _base_payload(*, provider: str, model: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "SKIPPED",
        "reason": "",
        "provider": provider,
        "model": model,
        "cli_path": "",
        "cli_version": "",
        "supported_flags": [],
        "missing_required_flags": [],
        "agent_status": "SKIPPED",
        "skills_status": "SKIPPED",
        "dry_probe_status": "SKIPPED",
        "warnings": [],
        "errors": [],
    }


def _write(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload
