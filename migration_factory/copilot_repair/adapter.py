from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from migration_factory.copilot_cli import resolve_copilot_cli_executable
from migration_factory.copilot_repair.evidence_session import (
    create_evidence_session,
    finalize_evidence_session,
)
from migration_factory.copilot_repair.request_builder import COPILOT_RESPONSE_TEMPLATE
from migration_factory.copilot_repair.response_validator import (
    failed_response_payload,
    parse_copilot_stdout,
    validate_copilot_repair_response,
)


DENIED_FLAGS = {"--allow-all", "--allow-all-tools", "--allow-all-paths", "--allow-all-urls", "--yolo"}
EVIDENCE_FILES = (
    "evidence/copilot_repair_request.json",
    "evidence/copilot_repair_response.schema.json",
    "evidence/copilot_repair_response.template.json",
)
EVIDENCE_FILE_PROMPT = """Read only these local files:
- ./evidence/copilot_repair_request.json
- ./evidence/copilot_repair_response.schema.json
- ./evidence/copilot_repair_response.template.json

Return exactly one JSON object validating against the schema and following the template.
No prose, no markdown, no comments.
Do not apply patches."""


def invoke_copilot_repair(
    *,
    repo_root: str | Path,
    run_dir: str | Path,
    run_id: str,
    request_payload: dict[str, Any],
    availability: dict[str, Any],
    model: str = "",
    timeout_seconds: int = 300,
    strict_containment: bool = True,
    executable: str = "copilot",
    run=subprocess.run,
) -> dict[str, Any]:
    failures_dir = Path(run_dir) / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)
    response_path = failures_dir / "copilot_repair_response.json"
    markdown_path = failures_dir / "repair_plan.md"

    if availability.get("status") != "AVAILABLE":
        payload = failed_response_payload(reason="Copilot repair proposal mode unavailable.")
        _write_response(response_path, markdown_path, payload)
        return {"status": "SKIPPED", "artifact_refs": {"copilot_repair_response": str(response_path), "repair_plan": str(markdown_path)}}

    session = create_evidence_session(
        repo_root=repo_root,
        run_dir=run_dir,
        run_id=run_id,
        evidence=request_payload,
    )
    prompt = EVIDENCE_FILE_PROMPT
    if not _read_tool_can_be_safely_enabled(availability):
        command = _build_unavailable_debug_command(availability, model=model, executable=executable, prompt=prompt)
        debug_path = _write_debug_artifact(
            session.session_dir,
            command,
            prompt_mode="evidence_file_read",
            read_tool_enabled=False,
        )
        payload = _read_tool_unavailable_payload(request_payload)
        _write_response(response_path, markdown_path, payload)
        finalize_evidence_session(session.session_dir, strict=strict_containment)
        return {
            "status": "READ_TOOL_UNAVAILABLE",
            "artifact_refs": {
                "copilot_repair_response": str(response_path),
                "repair_plan": str(markdown_path),
                "copilot_evidence_manifest": str(session.manifest_path),
                "copilot_invocation_debug": str(debug_path),
            },
        }
    command = _build_command(availability, model=model, executable=executable, prompt=prompt)
    debug_path = _write_debug_artifact(
        session.session_dir,
        command,
        prompt_mode="evidence_file_read",
        read_tool_enabled=True,
    )
    if any(flag in command for flag in DENIED_FLAGS):
        payload = failed_response_payload(reason="Unsafe Copilot command flag requested.")
        _write_response(response_path, markdown_path, payload)
        return {"status": "FAILED", "artifact_refs": {"copilot_repair_response": str(response_path), "repair_plan": str(markdown_path)}}

    invocation_status = "COMPLETED"
    try:
        completed = run(
            command,
            cwd=str(session.session_dir),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
        parsed, parse_errors = parse_copilot_stdout(completed.stdout or "")
        if parse_errors:
            payload = failed_response_payload(reason="; ".join(parse_errors), stdout=completed.stdout or "", stderr=completed.stderr or "")
            invocation_status = "INVALID_RESPONSE"
        else:
            valid, validation_errors = validate_copilot_repair_response(parsed)
            payload = parsed if valid else failed_response_payload(reason="; ".join(validation_errors), stdout=completed.stdout or "", stderr=completed.stderr or "")
            invocation_status = "COMPLETED" if valid else "INVALID_RESPONSE"
    except (OSError, subprocess.TimeoutExpired) as exc:
        payload = failed_response_payload(reason=f"Copilot invocation failed: {exc}")
        invocation_status = "FAILED"
    finally:
        manifest = finalize_evidence_session(session.session_dir, strict=strict_containment)

    if strict_containment and manifest.get("unexpected_mutations"):
        payload = failed_response_payload(reason="Strict containment detected unexpected evidence session mutation.")
        invocation_status = "FAILED"
    _write_response(response_path, markdown_path, payload)
    return {
        "status": invocation_status,
        "artifact_refs": {
            "copilot_repair_response": str(response_path),
            "repair_plan": str(markdown_path),
            "copilot_evidence_manifest": str(session.manifest_path),
            "copilot_invocation_debug": str(debug_path),
        },
    }


def _build_command(availability: dict[str, Any], *, model: str, executable: str, prompt: str) -> list[str]:
    supported = set(availability.get("supported_flags", []) or [])
    cli_path = str(availability.get("cli_path") or "").strip() or resolve_copilot_cli_executable(executable)
    if not cli_path:
        raise FileNotFoundError("Copilot executable path was not resolved")
    command = [cli_path]
    if "--prompt" in supported:
        command.extend(["--prompt", prompt])
    if "--silent" in supported:
        command.append("--silent")
    if "--no-ask-user" in supported:
        command.append("--no-ask-user")
    if "--no-custom-instructions" in supported:
        command.append("--no-custom-instructions")
    if "--no-remote" in supported:
        command.append("--no-remote")
    if "--disable-builtin-mcps" in supported:
        command.append("--disable-builtin-mcps")
    if model and "--model" in supported:
        command.extend(["--model", model])
    if "--agent" in supported:
        command.extend(["--agent", "ai-migration-repair"])
    if "--available-tools" in supported:
        command.append("--available-tools=read,skill")
    if "--deny-tool" in supported:
        command.append("--deny-tool=write,shell,url,memory")
    return command


def _build_unavailable_debug_command(
    availability: dict[str, Any],
    *,
    model: str,
    executable: str,
    prompt: str,
) -> list[str]:
    try:
        return _build_command(availability, model=model, executable=executable, prompt=prompt)
    except FileNotFoundError:
        return [str(availability.get("cli_path") or executable), "--prompt", prompt]


def _read_tool_can_be_safely_enabled(availability: dict[str, Any]) -> bool:
    supported = set(availability.get("supported_flags", []) or [])
    return "--available-tools" in supported and "--deny-tool" in supported


def _write_debug_artifact(
    session_dir: Path,
    command: list[str],
    *,
    prompt_mode: str,
    read_tool_enabled: bool,
) -> Path:
    prompt = ""
    if "--prompt" in command:
        index = command.index("--prompt")
        if index + 1 < len(command):
            prompt = command[index + 1]
    files = sorted(path.relative_to(session_dir).as_posix() for path in session_dir.rglob("*") if path.is_file())
    payload = {
        "cwd": str(session_dir),
        "files": files,
        "evidence_files_present": [rel for rel in EVIDENCE_FILES if rel in files],
        "command": _redact_command(command),
        "prompt_mode": prompt_mode,
        "prompt_excerpt": prompt[:500],
        "prompt_size_chars": len(prompt),
        "read_tool_enabled": read_tool_enabled,
    }
    path = session_dir / "copilot_invocation_debug.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _redact_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for item in command:
        if skip_next:
            redacted.append("[REDACTED]")
            skip_next = False
            continue
        redacted.append(item)
        if item in {"--token", "--api-key", "--prompt"}:
            skip_next = True
    return redacted


def _write_response(response_path: Path, markdown_path: Path, payload: dict[str, Any]) -> None:
    response_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")


def _read_tool_unavailable_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(COPILOT_RESPONSE_TEMPLATE)
    classification = request_payload.get("failure_classification")
    if isinstance(classification, dict):
        failure_classification = str(classification.get("failure_type") or "UNKNOWN_MIGRATION_FAILURE")
    else:
        failure_classification = "UNKNOWN_MIGRATION_FAILURE"
    payload.update(
        {
            "repair_summary": "Copilot repair proposal skipped because safe evidence read mode is unavailable.",
            "failure_classification": failure_classification,
            "skills_claimed": [],
            "patch_proposals": [],
            "security_review_required": False,
            "confidence": "LOW",
            "refusals": ["COPILOT_READ_TOOL_UNAVAILABLE"],
            "limitations": [
                "Copilot CLI did not expose a safe read-only evidence mode.",
                "No Copilot subprocess was started.",
                "Repair proposals require a manual review or a CLI with --available-tools=read,skill support.",
            ],
        }
    )
    return payload


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Copilot Repair Plan",
        "",
        str(payload.get("repair_summary", "")),
        "",
        f"- Failure classification: {payload.get('failure_classification', '')}",
        f"- Confidence: {payload.get('confidence', '')}",
        f"- Security review required: {str(payload.get('security_review_required', False)).lower()}",
        "",
        "No patches were applied by this adapter.",
    ]
    limitations = payload.get("limitations")
    if isinstance(limitations, list) and limitations:
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {item}" for item in limitations if item)
    return "\n".join(lines) + "\n"
