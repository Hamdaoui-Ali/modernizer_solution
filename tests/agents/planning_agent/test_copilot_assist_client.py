from migration_factory.agents.planning_agent.assist_config import PlanningAssistConfig
from migration_factory.agents.planning_agent.copilot_assist_client import (
    CopilotPlanningAssistClient,
)
from migration_factory.agents.planning_agent import copilot_auth
from migration_factory.contracts.planning_assist import PlanningAssistRequest


def _request() -> PlanningAssistRequest:
    return PlanningAssistRequest(
        run_id="r1",
        agent="planning-agent",
        phase="planning",
        model="gpt-test",
        prompt="review plan",
        context={"migration_units": []},
    )


def test_review_plan_returns_unavailable_when_adapter_is_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("MF_PLANNING_ASSIST_AUTH_MODE", raising=False)
    monkeypatch.setattr(copilot_auth, "_gh_auth_ready", lambda: True)
    client = CopilotPlanningAssistClient()

    result = client.review_plan(
        request=_request(),
        config=PlanningAssistConfig(
            enabled=True,
            allowed_models=("gpt-test",),
            phase_model_overrides={"planning": "gpt-test"},
        ),
    )

    assert result.status == "UNAVAILABLE"
    assert (
        result.error
        == "adapter_unavailable: Planning assist provider adapter is not configured."
    )
    assert result.warnings
    assert result.requested_model == "gpt-test"
    assert result.resolved_model == "gpt-test"
    assert result.model_source == "phase_override"
    assert result.model_verified is False


def test_review_plan_skips_when_disabled() -> None:
    client = CopilotPlanningAssistClient()

    result = client.review_plan(
        request=_request(),
        config=PlanningAssistConfig(
            enabled=False,
            allowed_models=("gpt-test",),
            phase_model_overrides={"planning": "gpt-test"},
        ),
    )

    assert result.status == "SKIPPED"
    assert result.error is None


def test_review_plan_rejects_invalid_env_override_fail_open(monkeypatch) -> None:
    monkeypatch.setattr(copilot_auth, "_gh_auth_ready", lambda: True)
    client = CopilotPlanningAssistClient()

    result = client.review_plan(
        request=_request(),
        config=PlanningAssistConfig(
            enabled=True,
            model_override="unknown-model",
            allowed_models=("gpt-test",),
        ),
    )

    assert result.status == "UNAVAILABLE"
    assert result.error == "model_unavailable: Planning assist model is not allowed: unknown-model."
    assert result.requested_model == "unknown-model"
    assert result.resolved_model is None
    assert result.model_verified is False


def test_review_plan_normalizes_provider_exception_to_error(monkeypatch) -> None:
    monkeypatch.delenv("MF_PLANNING_ASSIST_AUTH_MODE", raising=False)
    monkeypatch.setattr(copilot_auth, "_gh_auth_ready", lambda: True)

    class RaisingClient(CopilotPlanningAssistClient):
        def _perform_provider_review(self, request, config):
            raise TimeoutError("request timeout")

    client = RaisingClient()
    result = client.review_plan(
        request=_request(),
        config=PlanningAssistConfig(
            enabled=True,
            allowed_models=("gpt-test",),
            phase_model_overrides={"planning": "gpt-test"},
        ),
    )

    assert result.status == "ERROR"
    assert result.error == "Planning assist timeout."
    assert result.warnings


def test_review_plan_rejects_non_object_payload_as_controlled_error(monkeypatch) -> None:
    monkeypatch.delenv("MF_PLANNING_ASSIST_AUTH_MODE", raising=False)
    monkeypatch.setattr(copilot_auth, "_gh_auth_ready", lambda: True)

    class InvalidPayloadClient(CopilotPlanningAssistClient):
        def _perform_provider_review(self, request, config):
            return "not-json-object"

    client = InvalidPayloadClient()
    result = client.review_plan(
        request=_request(),
        config=PlanningAssistConfig(
            enabled=True,
            allowed_models=("gpt-test",),
            phase_model_overrides={"planning": "gpt-test"},
        ),
    )

    assert result.status == "ERROR"
    assert result.error == "Planning assist invalid JSON/non-object payload."
