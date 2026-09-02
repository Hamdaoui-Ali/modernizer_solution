from dataclasses import dataclass, field

from migration_factory.agents.planning_agent.assist_config import PlanningAssistConfig
from migration_factory.contracts.planning_assist import PlanningAssistRequest


@dataclass(frozen=True)
class CopilotModelResolutionResult:
    ok: bool
    model: str | None
    source: str | None
    requested_model: str | None = None
    model_verified: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _normalize_model(model: str | None) -> str:
    return (model or "").strip().lower()


def _unavailable(model: str | None) -> CopilotModelResolutionResult:
    requested = _normalize_model(model) or None
    return CopilotModelResolutionResult(
        ok=False,
        model=None,
        source=None,
        requested_model=requested,
        model_verified=False,
        errors=[f"model_unavailable: Planning assist model is not allowed: {requested}."],
    )


def resolve_copilot_model(
    request: PlanningAssistRequest, config: PlanningAssistConfig
) -> CopilotModelResolutionResult:
    allowed_models = {_normalize_model(model) for model in config.allowed_models}

    override = _normalize_model(config.model_override)
    if override:
        if override not in allowed_models:
            return _unavailable(override)
        return CopilotModelResolutionResult(
            ok=True,
            model=override,
            source="env_override",
            requested_model=override,
            model_verified=False,
        )

    phase_overrides = config.phase_model_overrides or {}
    phase_model = _normalize_model(
        phase_overrides.get(request.phase) or phase_overrides.get("planning")
    )
    if phase_model:
        if phase_model not in allowed_models:
            return _unavailable(phase_model)
        return CopilotModelResolutionResult(
            ok=True,
            model=phase_model,
            source="phase_override",
            requested_model=phase_model,
            model_verified=False,
        )

    default_model = _normalize_model(config.default_model)
    if default_model:
        if default_model not in allowed_models:
            return _unavailable(default_model)
        return CopilotModelResolutionResult(
            ok=True,
            model=default_model,
            source="hub_default",
            requested_model=default_model,
            model_verified=False,
        )

    return CopilotModelResolutionResult(
        ok=False,
        model=None,
        source=None,
        requested_model=None,
        model_verified=False,
        errors=["model_unavailable: Planning assist model resolution failed: model is empty or missing."],
    )
