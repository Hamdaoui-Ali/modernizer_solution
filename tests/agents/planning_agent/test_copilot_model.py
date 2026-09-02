from migration_factory.agents.planning_agent.assist_config import PlanningAssistConfig
from migration_factory.agents.planning_agent.copilot_model import resolve_copilot_model
from migration_factory.contracts.planning_assist import PlanningAssistRequest


def _request(model: str | None) -> PlanningAssistRequest:
    return PlanningAssistRequest(
        run_id="r1",
        agent="planning_agent",
        phase="planning",
        model=model,
        prompt="review",
        context={"migration_units": []},
    )


def test_resolve_copilot_model_prefers_env_override() -> None:
    result = resolve_copilot_model(
        request=_request("default-model"),
        config=PlanningAssistConfig(
            enabled=True,
            model_override="override-model",
            allowed_models=("default-model", "override-model"),
        ),
    )

    assert result.ok is True
    assert result.model == "override-model"
    assert result.source == "env_override"
    assert result.requested_model == "override-model"
    assert result.model_verified is False
    assert result.errors == []


def test_resolve_copilot_model_prefers_phase_override() -> None:
    result = resolve_copilot_model(
        request=_request(None),
        config=PlanningAssistConfig(
            enabled=True,
            model_override=None,
            default_model="default-model",
            allowed_models=("default-model", "gpt-5-mini"),
            phase_model_overrides={"planning": "gpt-5-mini"},
        ),
    )

    assert result.ok is True
    assert result.model == "gpt-5-mini"
    assert result.source == "phase_override"
    assert result.errors == []


def test_resolve_copilot_model_falls_back_to_hub_default_gpt_5_mini() -> None:
    result = resolve_copilot_model(
        request=_request("ignored-request-model"),
        config=PlanningAssistConfig(
            enabled=True,
            model_override=None,
            default_model="gpt-5-mini",
            allowed_models=("gpt-5-mini",),
        ),
    )

    assert result.ok is True
    assert result.model == "gpt-5-mini"
    assert result.source == "hub_default"
    assert result.errors == []


def test_resolve_copilot_model_rejects_invalid_env_override() -> None:
    result = resolve_copilot_model(
        request=_request(None),
        config=PlanningAssistConfig(
            enabled=True,
            model_override="unknown-model",
            allowed_models=("gpt-5-mini",),
        ),
    )

    assert result.ok is False
    assert result.model is None
    assert result.requested_model == "unknown-model"
    assert result.model_verified is False
    assert result.errors == [
        "model_unavailable: Planning assist model is not allowed: unknown-model."
    ]


def test_resolve_copilot_model_empty_missing_returns_controlled_failure() -> None:
    result = resolve_copilot_model(
        request=_request(None),
        config=PlanningAssistConfig(enabled=True, model_override="   ", default_model=""),
    )

    assert result.ok is False
    assert result.model is None
    assert result.source is None
    assert result.errors == [
        "model_unavailable: Planning assist model resolution failed: model is empty or missing."
    ]
