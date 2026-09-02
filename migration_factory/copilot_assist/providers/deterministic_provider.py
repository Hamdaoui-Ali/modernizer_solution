"""Deterministic advisory fallback provider for Copilot assist artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


PROVIDER_NAME = "deterministic"
DEFAULT_MODEL = "local-template"
SUPPORTED_PHASES = (
    "analysis",
    "planning",
    "assessment",
    "transformation",
    "build",
    "quality",
    "security",
    "final",
)


@dataclass(frozen=True)
class ProviderResult:
    """Provider-owned data result; callers decide whether and where to persist it."""

    status: str
    provider: str
    model: str
    advisory_only: bool
    fallback_used: bool
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.payload,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "advisory_only": self.advisory_only,
            "fallback_used": self.fallback_used,
        }


class DeterministicCopilotProvider:
    """Returns deterministic advisory fallback content without external calls or writes."""

    provider = PROVIDER_NAME

    def __init__(self, *, model: str = DEFAULT_MODEL) -> None:
        self.model = model

    def phase_assist_fallback(
        self,
        *,
        run_id: str,
        phase: str,
        agent: str,
        context: Mapping[str, Any] | None = None,
        trigger: str = "deterministic_fallback",
    ) -> ProviderResult:
        if phase not in SUPPORTED_PHASES:
            raise ValueError(f"unsupported Copilot assist phase: {phase}")

        snapshot = dict(context or {})
        warnings = _string_list(snapshot.get("warnings"))
        blockers = _string_list(snapshot.get("blockers"))
        errors = _string_list(snapshot.get("errors"))
        evidence = _evidence(snapshot, warnings, blockers, errors)
        recommended_actions = _recommended_actions(phase, warnings, blockers, errors)
        blocked_actions = [
            "Do not modify official migration statuses, blockers, warnings, errors, verdicts, or approval state.",
            "Do not write source, plan, executable, approval, or graph artifacts from the provider.",
        ]

        payload = {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "phase": phase,
            "agent": agent,
            "trigger": trigger,
            "validation_snapshot": snapshot,
            "root_cause_summary": _root_cause_summary(phase, warnings, blockers, errors),
            "evidence": evidence,
            "recommended_actions": recommended_actions,
            "blocked_actions": blocked_actions,
            "confidence": _confidence(warnings, blockers, errors),
            "created_at": _utc_now(),
        }
        return ProviderResult(
            status="fallback",
            provider=self.provider,
            model=self.model,
            advisory_only=True,
            fallback_used=True,
            payload=payload,
        )

    def final_report_fallback(
        self,
        *,
        run_id: str,
        context: Mapping[str, Any] | None = None,
        output_ref: str | None = None,
        warnings: list[str] | None = None,
    ) -> ProviderResult:
        snapshot = dict(context or {})
        supplied_warnings = list(warnings or [])
        content = _final_report_content(run_id, snapshot)
        payload = {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "output_ref": output_ref,
            "validation": {
                "valid": True,
                "source": "deterministic_provider",
                "uses_provided_context_only": True,
            },
            "warnings": supplied_warnings,
            "content": content,
        }
        return ProviderResult(
            status="generated_with_fallback",
            provider=self.provider,
            model=self.model,
            advisory_only=True,
            fallback_used=True,
            payload=payload,
        )


def _root_cause_summary(phase: str, warnings: list[str], blockers: list[str], errors: list[str]) -> str:
    if errors:
        return f"{phase} has recorded errors requiring deterministic review."
    if blockers:
        return f"{phase} has recorded blockers requiring deterministic review."
    if warnings:
        return f"{phase} has recorded warnings requiring operator review."
    return f"{phase} has no recorded blockers or errors in the provided context."


def _evidence(
    context: Mapping[str, Any],
    warnings: list[str],
    blockers: list[str],
    errors: list[str],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for name, values in (("errors", errors), ("blockers", blockers), ("warnings", warnings)):
        evidence.append({"source": name, "count": len(values), "items": values})
    artifact_refs = context.get("artifact_refs")
    if isinstance(artifact_refs, Mapping):
        evidence.append({"source": "artifact_refs", "count": len(artifact_refs), "items": dict(artifact_refs)})
    return evidence


def _recommended_actions(
    phase: str,
    warnings: list[str],
    blockers: list[str],
    errors: list[str],
) -> list[str]:
    if errors or blockers:
        return [
            f"Review {phase} deterministic failure artifacts.",
            "Resolve recorded blockers or errors before relying on advisory guidance.",
        ]
    if warnings:
        return [
            f"Review {phase} warnings against deterministic artifacts.",
            "Keep advisory recommendations separate from official migration verdicts.",
        ]
    return [
        f"Continue {phase} review using deterministic migration artifacts.",
        "Treat this result as advisory context only.",
    ]


def _confidence(warnings: list[str], blockers: list[str], errors: list[str]) -> str:
    if errors or blockers:
        return "medium"
    if warnings:
        return "medium"
    return "high"


def _final_report_content(run_id: str, context: Mapping[str, Any]) -> dict[str, Any]:
    statuses = context.get("statuses") if isinstance(context.get("statuses"), Mapping) else {}
    return {
        "summary": f"Deterministic fallback advisory report for run {run_id}.",
        "source_of_truth": "Deterministic migration artifacts remain authoritative.",
        "statuses": dict(statuses),
        "review_notes": _string_list(context.get("warnings")) or ["No warnings supplied in provider context."],
        "final_verdict": context.get("final_verdict") or statuses.get("final") or "not_captured",
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in (None, ""):
        return []
    return [str(value)]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
