"""Tests for V2 role-based model routing."""

from __future__ import annotations

from migration_factory.control_tower.application.v2_assistant_model_client import (
    V2AssistantModelClient,
    V2AssistantModelResult,
)
from migration_factory.control_tower.application.v2_model_role_router import (
    V2ModelRole,
    V2ModelRoleRouter,
    V2RoleModelRequest,
)
from migration_factory.control_tower.application.v2_settings import ControlTowerSettings


def test_router_plans_role_specific_deployments(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_PROPOSER_DEPLOYMENT", "proposer-deployment")
    monkeypatch.setenv("AZURE_OPENAI_REVIEWER_DEPLOYMENT", "reviewer-deployment")
    monkeypatch.setenv("AZURE_OPENAI_ASSISTANT_DEPLOYMENT", "assistant-deployment")
    monkeypatch.setenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "fallback-deployment")

    router = V2ModelRoleRouter(ControlTowerSettings(azure_foundry_fallback_enabled=True))
    plan = router.plan(
        V2RoleModelRequest(
            role=V2ModelRole.PROPOSER,
            prompt="draft a proposal",
            fallback="fallback",
        )
    )

    assert plan.primary_env_ref == "AZURE_OPENAI_PROPOSER_DEPLOYMENT"
    assert plan.primary_deployment == "proposer-deployment"
    assert plan.fallback_env_ref == "AZURE_OPENAI_FALLBACK_DEPLOYMENT"
    assert plan.fallback_deployment == "fallback-deployment"
    assert plan.fallback_enabled is True


def test_router_uses_fallback_deployment_when_primary_missing(monkeypatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_PROPOSER_DEPLOYMENT", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "fallback-deployment")

    router = V2ModelRoleRouter(ControlTowerSettings(azure_foundry_fallback_enabled=True))
    calls: list[str] = []

    def invoke(deployment: str) -> V2AssistantModelResult:
        calls.append(deployment)
        return V2AssistantModelResult(
            content="fallback response",
            source="azure_openai",
            model_status="live_ok",
            provider="azure_openai",
            role="proposer",
            success=True,
            redacted_summary="fallback response",
            failure_reason="",
        )

    result = router.route(
        V2RoleModelRequest(role=V2ModelRole.PROPOSER, prompt="draft", fallback="fallback"),
        invoke=invoke,
    )

    assert calls == ["fallback-deployment"]
    assert result.success is True
    assert result.fallback_used is True
    assert result.primary_failure_reason == "missing_proposer_deployment"
    assert result.content == "fallback response"


def test_router_does_not_select_static_fallback_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_PROPOSER_DEPLOYMENT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", raising=False)

    router = V2ModelRoleRouter(ControlTowerSettings(azure_foundry_fallback_enabled=True))
    calls: list[str] = []

    result = router.route(
        V2RoleModelRequest(role=V2ModelRole.PROPOSER, prompt="draft", fallback="fallback"),
        invoke=lambda deployment: calls.append(deployment),
    )

    assert calls == []
    assert result.success is False
    assert result.provider == "deterministic"
    assert result.failure_reason == "missing_proposer_deployment"
    assert result.fallback_used is False


def test_router_uses_user_selected_reviewer_deployment(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_REVIEWER_DEPLOYMENT", "reviewer-selected")
    monkeypatch.setenv("AZURE_OPENAI_PROPOSER_DEPLOYMENT", "proposer-selected")
    monkeypatch.setenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "fallback-selected")

    router = V2ModelRoleRouter(ControlTowerSettings(azure_foundry_fallback_enabled=True))
    calls: list[str] = []

    def invoke(deployment: str) -> V2AssistantModelResult:
        calls.append(deployment)
        return V2AssistantModelResult(
            content='{"decision":"accept","reasoning":"ok","missing_evidence":[],"unsafe_assumptions":[]}',
            source="azure_openai",
            model_status="live_ok",
            provider="azure_openai",
            role="reviewer",
            success=True,
            redacted_summary="ok",
            failure_reason="",
        )

    result = router.route(
        V2RoleModelRequest(
            role=V2ModelRole.REVIEWER,
            prompt="review",
            fallback="fallback",
            output_schema_name="ReviewerCritique",
            require_schema=True,
        ),
        invoke=invoke,
    )

    assert calls == ["reviewer-selected"]
    assert result.success is True
    assert result.fallback_used is False


def test_router_fail_closes_on_schema_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ASSISTANT_DEPLOYMENT", "assistant-deployment")

    router = V2ModelRoleRouter()

    def invoke(_: str) -> V2AssistantModelResult:
        return V2AssistantModelResult(
            content="not json",
            source="azure_openai",
            model_status="live_ok",
            provider="azure_openai",
            role="assistant",
            success=True,
            redacted_summary="not json",
            failure_reason="",
        )

    result = router.route(
        V2RoleModelRequest(
            role=V2ModelRole.ASSISTANT,
            prompt="status?",
            fallback="fallback answer",
            output_schema_name="AssistantAnswer",
            require_schema=True,
        ),
        invoke=invoke,
    )

    assert result.success is False
    assert result.model_status == "fallback"
    assert result.schema_validated is True
    assert result.content.startswith("{")
    assert "fallback answer" in result.content


def test_client_answer_with_role_uses_requested_role_deployment(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/openai/v1")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_PROPOSER_DEPLOYMENT", "proposer-deployment")

    deployments: list[str] = []

    def fake_chat_completion(*, deployment: str, **_: object) -> str:
        deployments.append(deployment)
        return "proposer draft"

    client = V2AssistantModelClient()
    monkeypatch.setattr(client, "_chat_completion", fake_chat_completion)

    result = client.answer_with_role(
        role=V2ModelRole.PROPOSER,
        prompt="Draft a proposal",
        fallback="fallback",
    )

    assert deployments == ["proposer-deployment"]
    assert result.success is True
    assert result.role == "proposer"
    assert result.content == "proposer draft"


def test_answer_delegates_to_assistant_role(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_answer_with_role(*, role, prompt, fallback, conversation_history=None, output_schema_name=None, require_schema=False):
        seen["role"] = role.value
        seen["prompt"] = prompt
        seen["fallback"] = fallback
        return V2AssistantModelResult(
            content="ok",
            source="deterministic",
            model_status="fallback",
            provider="deterministic",
            role=role.value,
            success=False,
            redacted_summary="ok",
            failure_reason="",
        )

    client = V2AssistantModelClient()
    monkeypatch.setattr(client, "answer_with_role", fake_answer_with_role)

    result = client.answer(prompt="status?", fallback="fallback")

    assert seen["role"] == "assistant"
    assert seen["prompt"] == "status?"
    assert seen["fallback"] == "fallback"
    assert result.role == "assistant"
