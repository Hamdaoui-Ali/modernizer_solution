"""Central advisory Copilot assist service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from migration_factory.contracts.schema_validation import validate_against_schema
from migration_factory.copilot_assist.context_builder import (
    build_final_report_request,
    build_phase_assist_context,
    load_final_report_context,
)
from migration_factory.copilot_assist.providers.cli_provider import CopilotCliProvider
from migration_factory.copilot_assist.providers.deterministic_provider import (
    DEFAULT_MODEL,
    DeterministicCopilotProvider,
    ProviderResult,
)
from migration_factory.final_report.context_builder import _redact as redact_value


COPILOT_STATE_FIELDS = {
    "copilot_phase_statuses",
    "copilot_artifact_refs",
    "copilot_warnings",
    "copilot_errors",
    "copilot_fallback_used",
}


class CopilotAssistService:
    """Writes Copilot-only artifacts and updates only copilot_* state fields."""

    def __init__(self, state: Mapping[str, Any]) -> None:
        self.state = state
        self.run_dir = Path(str(state.get("run_dir") or ".")).resolve()
        self.model = str(state.get("copilot_model") or DEFAULT_MODEL)
        self.provider_name = str(state.get("copilot_provider") or "deterministic").lower()
        self.timeout_seconds = int(state.get("copilot_timeout_seconds") or 300)
        self.deterministic = DeterministicCopilotProvider()

    def generate_phase_assist(self, state: dict[str, Any], phase: str) -> ProviderResult:
        context = build_phase_assist_context(state, phase)
        provider = self._provider()
        if isinstance(provider, CopilotCliProvider):
            result = provider.phase_assist(
                run_id=str(state.get("run_id") or "unknown"),
                phase=phase,
                agent=f"{phase}_agent",
                run_dir=self.run_dir,
                context=context,
            )
        else:
            result = provider.phase_assist_fallback(
                run_id=str(state.get("run_id") or "unknown"),
                phase=phase,
                agent=f"{phase}_agent",
                context=context,
            )
        payload = self._validated_payload(result.to_dict(), "copilot_assist.schema.json", state)
        json_path = self.run_dir / phase / "copilot_assist.json"
        md_path = self.run_dir / phase / "copilot_assist.md"
        _write_json(json_path, payload)
        _write_text(md_path, _phase_markdown(payload))
        self._record_state(
            state,
            phase_status=(phase, payload.get("status", "generated")),
            artifact_refs={
                f"{phase}_copilot_assist": _rel(json_path, self.run_dir),
                f"{phase}_copilot_assist_md": _rel(md_path, self.run_dir),
            },
            warnings=list(payload.get("warnings") or []),
            fallback_used=bool(payload.get("fallback_used")),
        )
        return result

    def generate_final_report(self, state: dict[str, Any]) -> ProviderResult:
        run_id = str(state.get("run_id") or "unknown")
        context_path = self.run_dir / "final" / "report_context.json"
        request_path = self.run_dir / "final" / "copilot_report_request.json"
        response_path = self.run_dir / "final" / "copilot_report_response.json"
        report_path = self.run_dir / "final" / "copilot_migration_report.md"
        provider = self._provider()

        if not context_path.is_file():
            warning = "missing required final/report_context.json"
            request_payload = build_final_report_request(
                run_id=run_id,
                provider=self._provider_label(provider),
                model=self.model,
                context={},
            )
            result = self.deterministic.final_report_fallback(
                run_id=run_id,
                context={"errors": [warning]},
                output_ref=None,
                warnings=[warning],
            )
            payload = result.to_dict()
            payload["status"] = "failed"
            payload["output_ref"] = None
            payload = self._validated_payload(payload, "copilot_report_response.schema.json", state)
            _write_json(request_path, request_payload)
            _write_json(response_path, payload)
            self._record_state(
                state,
                phase_status=("final", "failed"),
                artifact_refs={
                    "copilot_report_request": _rel(request_path, self.run_dir),
                    "copilot_report_response": _rel(response_path, self.run_dir),
                },
                warnings=[warning],
                errors=[warning],
                fallback_used=True,
            )
            return result

        context = load_final_report_context(self.run_dir)
        request_payload = build_final_report_request(
            run_id=run_id,
            provider=self._provider_label(provider),
            model=self.model,
            context=context,
        )
        request_payload = self._validated_payload(request_payload, "copilot_report_request.schema.json", state)
        _write_json(request_path, request_payload)

        if isinstance(provider, CopilotCliProvider):
            result = provider.final_report(
                run_id=run_id,
                run_dir=self.run_dir,
                context=context,
                output_ref="final/copilot_migration_report.md",
            )
        else:
            result = provider.final_report_fallback(
                run_id=run_id,
                context=context,
                output_ref="final/copilot_migration_report.md",
            )
        response_payload = self._validated_payload(result.to_dict(), "copilot_report_response.schema.json", state)
        _write_json(response_path, response_payload)
        content = response_payload.get("content")
        if content:
            _write_text(report_path, _final_markdown(content))
        refs = {
            "copilot_report_request": _rel(request_path, self.run_dir),
            "copilot_report_response": _rel(response_path, self.run_dir),
        }
        if report_path.is_file():
            refs["copilot_migration_report"] = _rel(report_path, self.run_dir)
        self._record_state(
            state,
            phase_status=("final", response_payload.get("status", "generated")),
            artifact_refs=refs,
            warnings=list(response_payload.get("warnings") or []),
            fallback_used=bool(response_payload.get("fallback_used")),
        )
        return result

    def _provider(self) -> Any:
        if self.provider_name == "cli":
            return CopilotCliProvider(model=self.model, timeout_seconds=self.timeout_seconds)
        return self.deterministic

    def _provider_label(self, provider: Any) -> str:
        return getattr(provider, "provider", "deterministic")

    def _validated_payload(self, payload: dict[str, Any], schema_name: str, state: dict[str, Any]) -> dict[str, Any]:
        clean = redact_value(payload)
        errors = validate_against_schema(clean, schema_name)
        if errors:
            self._record_state(state, errors=[f"{schema_name}: {error}" for error in errors])
        return clean

    def _record_state(
        self,
        state: dict[str, Any],
        *,
        phase_status: tuple[str, Any] | None = None,
        artifact_refs: dict[str, str] | None = None,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
        fallback_used: bool | None = None,
    ) -> None:
        state.setdefault("copilot_phase_statuses", {})
        state.setdefault("copilot_artifact_refs", {})
        state.setdefault("copilot_warnings", [])
        state.setdefault("copilot_errors", [])
        if phase_status:
            state["copilot_phase_statuses"][phase_status[0]] = str(phase_status[1])
        if artifact_refs:
            state["copilot_artifact_refs"].update(artifact_refs)
        if warnings:
            state["copilot_warnings"].extend(str(item) for item in warnings)
        if errors:
            state["copilot_errors"].extend(str(item) for item in errors)
        if fallback_used is not None:
            state["copilot_fallback_used"] = bool(state.get("copilot_fallback_used")) or fallback_used


def generate_phase_assist(state: dict[str, Any], phase: str) -> ProviderResult:
    return CopilotAssistService(state).generate_phase_assist(state, phase)


def generate_final_report(state: dict[str, Any]) -> ProviderResult:
    return CopilotAssistService(state).generate_final_report(state)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact_value(dict(payload)), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(redact_value(text)).rstrip() + "\n", encoding="utf-8")


def _phase_markdown(payload: Mapping[str, Any]) -> str:
    actions = "\n".join(f"- {item}" for item in payload.get("recommended_actions", []) or ["Review deterministic artifacts."])
    return f"# Copilot Assist\n\nStatus: {payload.get('status')}\n\n{actions}\n"


def _final_markdown(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        lines = ["# Copilot Migration Report", ""]
        for key, value in content.items():
            lines.append(f"## {str(key).replace('_', ' ').title()}")
            lines.append("")
            lines.append(json.dumps(redact_value(value), indent=2, sort_keys=True) if isinstance(value, (dict, list)) else str(value))
            lines.append("")
        return "\n".join(lines)
    return str(content)


def _rel(path: Path, run_dir: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return path.name


__all__ = ["COPILOT_STATE_FIELDS", "CopilotAssistService", "generate_final_report", "generate_phase_assist"]
