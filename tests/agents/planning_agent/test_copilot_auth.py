from migration_factory.agents.planning_agent.assist_config import PlanningAssistConfig
from migration_factory.agents.planning_agent.copilot_assist_client import (
    CopilotPlanningAssistClient,
)
from migration_factory.agents.planning_agent import copilot_auth
from migration_factory.agents.planning_agent.copilot_auth import resolve_copilot_auth
from migration_factory.contracts.planning_assist import PlanningAssistRequest


def test_resolve_auth_github_signed_in_user_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("MF_PLANNING_ASSIST_AUTH_MODE", raising=False)
    monkeypatch.delenv("MF_PLANNING_ASSIST_GITHUB_APP_TOKEN", raising=False)
    monkeypatch.delenv("MF_PLANNING_ASSIST_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(copilot_auth, "_gh_auth_ready", lambda: True)

    auth = resolve_copilot_auth()

    assert auth.ok is True
    assert auth.auth_mode == "github_signed_in_user"
    assert auth.token is None
    assert auth.errors == []


def test_resolve_auth_oauth_github_app_uses_existing_token(monkeypatch) -> None:
    monkeypatch.setenv("MF_PLANNING_ASSIST_AUTH_MODE", "oauth_github_app")
    monkeypatch.setenv("GITHUB_TOKEN", "token-from-env")

    auth = resolve_copilot_auth()

    assert auth.ok is True
    assert auth.auth_mode == "oauth_github_app"
    assert auth.token == "token-from-env"
    assert auth.errors == []


def test_resolve_auth_oauth_github_app_missing_token_is_controlled_failure(monkeypatch) -> None:
    monkeypatch.setenv("MF_PLANNING_ASSIST_AUTH_MODE", "oauth_github_app")
    monkeypatch.delenv("MF_PLANNING_ASSIST_GITHUB_APP_TOKEN", raising=False)
    monkeypatch.delenv("MF_PLANNING_ASSIST_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    auth = resolve_copilot_auth()

    assert auth.ok is False
    assert auth.auth_mode == "oauth_github_app"
    assert auth.errors == ["Missing GitHub app OAuth token for planning assist."]


def test_review_plan_returns_unavailable_on_missing_auth_without_exception(monkeypatch) -> None:
    monkeypatch.setenv("MF_PLANNING_ASSIST_AUTH_MODE", "oauth_github_app")
    monkeypatch.delenv("MF_PLANNING_ASSIST_GITHUB_APP_TOKEN", raising=False)
    monkeypatch.delenv("MF_PLANNING_ASSIST_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    client = CopilotPlanningAssistClient()
    request = PlanningAssistRequest(
        run_id="run-1",
        agent="planning-agent",
        phase="planning",
        model="gpt-test",
        prompt="review",
        context={"migration_units": []},
    )

    result = client.review_plan(request=request, config=PlanningAssistConfig(enabled=True))

    assert result.status == "UNAVAILABLE"
    assert result.error == "Missing GitHub app OAuth token for planning assist."
    assert result.warnings


def test_review_plan_returns_unavailable_on_missing_model_without_exception(monkeypatch) -> None:
    monkeypatch.delenv("MF_PLANNING_ASSIST_AUTH_MODE", raising=False)
    monkeypatch.setattr(copilot_auth, "_gh_auth_ready", lambda: True)

    client = CopilotPlanningAssistClient()
    request = PlanningAssistRequest(
        run_id="run-1",
        agent="planning-agent",
        phase="planning",
        model=None,
        prompt="review",
        context={"migration_units": []},
    )

    result = client.review_plan(
        request=request,
        config=PlanningAssistConfig(enabled=True, model_override=None, default_model=""),
    )

    assert result.status == "UNAVAILABLE"
    assert result.error == "model_unavailable: Planning assist model resolution failed: model is empty or missing."
    assert result.warnings
