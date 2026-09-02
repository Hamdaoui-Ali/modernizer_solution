"""Contained GitHub Copilot CLI provider with deterministic fallback."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from migration_factory.copilot_assist.providers.deterministic_provider import (
    DEFAULT_MODEL,
    DeterministicCopilotProvider,
    ProviderResult,
)


PROVIDER_NAME = "cli"
DEFAULT_TIMEOUT_SECONDS = 300
LARGE_PROMPT_BYTES = 8000
ALLOWED_FLAGS = {
    "-p",
    "--prompt",
    "--silent",
    "-s",
    "--no-ask-user",
    "--model",
    "--no-color",
}
FLAGS_WITH_VALUES = {"-p", "--prompt", "--model"}
FORBIDDEN_FLAGS = {
    "--allow-all",
    "--allow-all-tools",
    "--allow-all-paths",
    "--allow-all-urls",
    "--yolo",
}
NO_REMOTE_DECISION = (
    "not used: GitHub Copilot CLI support for --no-remote is not verified here, "
    "and report/assist generation requires the configured Copilot model."
)
_SECRET_KEY_PARTS = ("token", "secret", "password", "credential", "authorization", "api_key", "apikey")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?im)^(\s*authorization\s*:\s*).+$"),
    re.compile(r"(?i)\b[A-Za-z_]*(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|API_KEY)[A-Za-z_]*\s*=\s*[^\s]+"),
)


@dataclass(frozen=True)
class CliInvocation:
    args: list[str]
    input_mode: str
    prompt_bytes: int
    cwd: Path


class UnsafeCopilotCliConfig(ValueError):
    """Raised when the CLI invocation would violate containment rules."""


class CopilotCliProvider:
    """Calls Copilot CLI from a neutral cwd and falls back deterministically."""

    provider = PROVIDER_NAME

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        deterministic_provider: DeterministicCopilotProvider | None = None,
        executable_path: str | None = None,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self.timeout_seconds = int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
        self._deterministic = deterministic_provider or DeterministicCopilotProvider(model=DEFAULT_MODEL)
        self._executable_path = executable_path

    def phase_assist(
        self,
        *,
        run_id: str,
        phase: str,
        agent: str,
        run_dir: str | Path,
        context: Mapping[str, Any] | None = None,
        prompt: str | None = None,
    ) -> ProviderResult:
        prompt_text = prompt or _phase_prompt(run_id, phase, agent, context or {})
        fallback = lambda reason: self._deterministic.phase_assist_fallback(
            run_id=run_id,
            phase=phase,
            agent=agent,
            context={**dict(context or {}), "copilot_cli_fallback_reason": reason},
            trigger="cli_fallback",
        )
        result = self._invoke_with_fallback(run_dir=run_dir, prompt=prompt_text, fallback=fallback)
        if result is None:
            return fallback("empty_cli_result")
        payload = {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "phase": phase,
            "agent": agent,
            "trigger": "cli",
            "validation_snapshot": _redact(dict(context or {})),
            "root_cause_summary": _redact(result),
            "evidence": [],
            "recommended_actions": [_redact(result)],
            "blocked_actions": [
                "Do not modify official migration statuses, blockers, warnings, errors, verdicts, or approval state.",
            ],
            "confidence": "medium",
            "created_at": _utc_now(),
        }
        return ProviderResult(
            status="generated",
            provider=self.provider,
            model=self.model,
            advisory_only=True,
            fallback_used=False,
            payload=payload,
        )

    def final_report(
        self,
        *,
        run_id: str,
        run_dir: str | Path,
        context: Mapping[str, Any] | None = None,
        prompt: str | None = None,
        output_ref: str | None = "final/copilot_migration_report.md",
        warnings: list[str] | None = None,
    ) -> ProviderResult:
        prompt_text = prompt or _final_report_prompt(run_id, context or {})
        fallback = lambda reason: self._deterministic.final_report_fallback(
            run_id=run_id,
            context={**dict(context or {}), "copilot_cli_fallback_reason": reason},
            output_ref=output_ref,
            warnings=[*(warnings or []), f"Copilot CLI fallback used: {_redact(reason)}"],
        )
        result = self._invoke_with_fallback(run_dir=run_dir, prompt=prompt_text, fallback=fallback)
        if result is None:
            return fallback("empty_cli_result")
        payload = {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "output_ref": output_ref,
            "validation": {
                "valid": True,
                "source": "copilot_cli",
                "uses_provided_context_only": True,
            },
            "warnings": list(warnings or []),
            "content": _redact(result),
        }
        return ProviderResult(
            status="generated",
            provider=self.provider,
            model=self.model,
            advisory_only=True,
            fallback_used=False,
            payload=payload,
        )

    def _invoke_with_fallback(
        self,
        *,
        run_dir: str | Path,
        prompt: str,
        fallback: Any,
    ) -> str | None:
        try:
            invocation = self._build_invocation(run_dir=run_dir, prompt=prompt)
            started = time.monotonic()
            completed = subprocess.run(
                invocation.args,
                input=prompt if invocation.input_mode == "stdin" else None,
                capture_output=True,
                text=True,
                cwd=str(invocation.cwd),
                timeout=self.timeout_seconds,
                check=False,
            )
            elapsed = round(time.monotonic() - started, 3)
            self._write_invocation_log(invocation, completed.returncode, elapsed, completed.stderr)
            if completed.returncode != 0:
                fallback(f"nonzero_exit:{completed.returncode}")
                return None
            output = _redact(completed.stdout or "").strip()
            if not output:
                fallback("empty_stdout")
                return None
            return output
        except subprocess.TimeoutExpired:
            self._write_failure_log(run_dir, "timeout")
            fallback("timeout")
            return None
        except (OSError, UnsafeCopilotCliConfig, ValueError) as exc:
            self._write_failure_log(run_dir, _safe_exception_hint(exc))
            fallback(_safe_exception_hint(exc))
            return None

    def _build_invocation(self, *, run_dir: str | Path, prompt: str) -> CliInvocation:
        run_path = Path(run_dir).resolve()
        cwd = _neutral_cwd(run_path)
        executable = self._executable_path or _find_copilot_command()
        if not executable:
            raise FileNotFoundError("Copilot executable path was not resolved for live call")
        prompt_bytes = len(prompt.encode("utf-8"))
        args = [executable, "-s", "--no-ask-user", "--model", self.model, "--no-color"]
        input_mode = "stdin"
        if prompt_bytes <= LARGE_PROMPT_BYTES:
            args.extend(["--prompt", prompt])
            input_mode = "argv"
        _validate_cli_args(args)
        return CliInvocation(args=args, input_mode=input_mode, prompt_bytes=prompt_bytes, cwd=cwd)

    def _write_invocation_log(
        self,
        invocation: CliInvocation,
        return_code: int,
        elapsed_seconds: float,
        stderr: str,
    ) -> None:
        payload = {
            "provider": self.provider,
            "model": self.model,
            "executable_basename": _basename(invocation.args[0]),
            "args": _diagnostic_args(invocation.args),
            "cwd": _neutral_cwd_label(invocation.cwd),
            "input_mode": invocation.input_mode,
            "prompt_bytes": invocation.prompt_bytes,
            "prompt_chars_logged": False,
            "return_code": return_code,
            "elapsed_seconds": elapsed_seconds,
            "stderr_tail": _redacted_tail(stderr),
            "no_remote_decision": NO_REMOTE_DECISION,
        }
        _write_json(invocation.cwd / "copilot_cli_invocation.json", payload)

    def _write_failure_log(self, run_dir: str | Path, reason: str) -> None:
        cwd = _neutral_cwd(Path(run_dir).resolve())
        payload = {
            "provider": self.provider,
            "model": self.model,
            "executable_basename": _basename(self._executable_path or ""),
            "args": [],
            "cwd": _neutral_cwd_label(cwd),
            "input_mode": "not_started",
            "prompt_bytes": 0,
            "prompt_chars_logged": False,
            "return_code": None,
            "failure_reason": _redact(reason),
            "no_remote_decision": NO_REMOTE_DECISION,
        }
        _write_json(cwd / "copilot_cli_invocation.json", payload)


def _validate_cli_args(args: list[str]) -> None:
    if not args:
        raise UnsafeCopilotCliConfig("empty Copilot CLI command")
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in FORBIDDEN_FLAGS:
            raise UnsafeCopilotCliConfig(f"forbidden Copilot CLI flag: {arg}")
        if arg.startswith("-") and arg not in ALLOWED_FLAGS:
            raise UnsafeCopilotCliConfig(f"unsupported Copilot CLI flag: {arg}")
        if arg in FLAGS_WITH_VALUES:
            if index + 1 >= len(args):
                raise UnsafeCopilotCliConfig(f"missing value for Copilot CLI flag: {arg}")
            value = args[index + 1]
            if value in FORBIDDEN_FLAGS or (value.startswith("-") and arg not in {"-p", "--prompt"}):
                raise UnsafeCopilotCliConfig(f"unsafe value for Copilot CLI flag: {arg}")
            index += 2
            continue
        index += 1


def _neutral_cwd(run_dir: Path) -> Path:
    cwd = (run_dir / "logs" / "copilot").resolve()
    cwd.mkdir(parents=True, exist_ok=True)
    allowed = {(run_dir / "logs" / "copilot").resolve(), (run_dir / "final").resolve()}
    if cwd not in allowed:
        raise UnsafeCopilotCliConfig("Copilot CLI cwd is not an allowed neutral directory")
    forbidden = {
        Path.cwd().resolve(),
        run_dir.resolve(),
        (run_dir / "workspaces" / "sandbox").resolve(),
    }
    for name in ("legacy", "legacy_app", "modernized", "modernized_app"):
        forbidden.add((run_dir / name).resolve())
    if cwd in forbidden:
        raise UnsafeCopilotCliConfig("Copilot CLI cwd matches a protected project directory")
    return cwd


def _find_copilot_command() -> str | None:
    preferred = ("copilot.cmd", "copilot") if os.name == "nt" else ("copilot",)
    for name in preferred:
        found = shutil.which(name)
        if found:
            return found
    return None


def _phase_prompt(run_id: str, phase: str, agent: str, context: Mapping[str, Any]) -> str:
    return json.dumps(
        {
            "task": "Provide advisory Copilot phase assist only.",
            "run_id": run_id,
            "phase": phase,
            "agent": agent,
            "guardrails": _guardrails(),
            "context": _redact(dict(context)),
        },
        sort_keys=True,
    )


def _final_report_prompt(run_id: str, context: Mapping[str, Any]) -> str:
    return json.dumps(
        {
            "task": "Generate an advisory Copilot migration report from the provided deterministic context only.",
            "run_id": run_id,
            "guardrails": _guardrails(),
            "context": _redact(dict(context)),
        },
        sort_keys=True,
    )


def _guardrails() -> dict[str, bool]:
    return {
        "advisory_only": True,
        "can_modify_source": False,
        "can_modify_plan": False,
        "can_modify_blockers": False,
        "can_override_status": False,
        "can_approve": False,
        "can_deploy": False,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_redact(dict(payload)), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _diagnostic_args(args: list[str]) -> list[str]:
    if not args:
        return []
    safe = [_basename(args[0])]
    index = 1
    while index < len(args):
        arg = args[index]
        safe.append(arg)
        if arg in FLAGS_WITH_VALUES and index + 1 < len(args):
            safe.append("[PROMPT_REDACTED]" if arg in {"-p", "--prompt"} else _redact(args[index + 1]))
            index += 2
            continue
        index += 1
    return safe


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
        return _redact_user_home_path(redacted)
    return value


def _redacted_tail(text: str, *, max_chars: int = 2000) -> str:
    return str(_redact((text or "")[-max_chars:]))


def _redact_user_home_path(text: str) -> str:
    home = str(Path.home())
    if home and home not in {".", "/"}:
        text = text.replace(home, "%USERPROFILE%")
        text = text.replace(home.replace("\\", "/"), "%USERPROFILE%")
    return text


def _safe_exception_hint(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    if isinstance(exc, FileNotFoundError):
        message = "Copilot executable path was not resolved for live call"
    return f"{type(exc).__name__}: {_redact(message)}"


def _neutral_cwd_label(cwd: Path) -> str:
    parts = cwd.parts[-3:]
    return "/".join(parts).replace("\\", "/")


def _basename(path: str) -> str:
    return re.split(r"[\\/]", str(path))[-1] if path else ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "ALLOWED_FLAGS",
    "FORBIDDEN_FLAGS",
    "NO_REMOTE_DECISION",
    "CopilotCliProvider",
    "UnsafeCopilotCliConfig",
    "_validate_cli_args",
]
