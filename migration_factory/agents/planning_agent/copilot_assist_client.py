from migration_factory.agents.planning_agent.assist_config import PlanningAssistConfig
from migration_factory.agents.planning_agent.copilot_custom_agent import (
    get_copilot_custom_agent_config,
)
from migration_factory.agents.planning_agent.copilot_auth import resolve_copilot_auth
from migration_factory.agents.planning_agent.copilot_model import resolve_copilot_model
from migration_factory.contracts.planning_assist import (
    PlanningAssistRequest,
    PlanningAssistResult,
)


class CopilotPlanningAssistClient:
    """Provider-neutral planning assist interface. No external SDK calls yet."""

    _FAILURE_REASON_WARNING_PREFIX = "[WARNING] Planning assist failed-open:"
    _ADAPTER_UNAVAILABLE_REASON = (
        "adapter_unavailable: Planning assist provider adapter is not configured."
    )

    def get_custom_agent_config(self):
        """Expose static custom-agent config for wrapper registration/invocation layers."""
        return get_copilot_custom_agent_config()

    def _build_result(self, reason: str, status: str = "ERROR") -> PlanningAssistResult:
        return PlanningAssistResult(
            status=status,
            warnings=[f"{self._FAILURE_REASON_WARNING_PREFIX} {reason}"],
            error=reason,
        )

    def _normalize_failure_reason(self, error: Exception) -> str:
        message = str(error).strip()
        lowered = message.lower()
        if isinstance(error, TimeoutError) or "timeout" in lowered:
            return "Planning assist timeout."
        if "missing auth" in lowered or "auth missing" in lowered:
            return "Planning assist missing authentication."
        if "invalid token" in lowered or "bad credentials" in lowered:
            return "Planning assist invalid token."
        if "entitlement" in lowered:
            return "Planning assist entitlement error."
        if "model unavailable" in lowered or "model not found" in lowered:
            return "Planning assist model unavailable."
        if "invalid output" in lowered:
            return "Planning assist invalid output."
        return "Planning assist runtime error."

    def _perform_provider_review(
        self, request: PlanningAssistRequest, config: PlanningAssistConfig
    ) -> PlanningAssistResult:
        return self._build_result(
            self._ADAPTER_UNAVAILABLE_REASON,
            status="UNAVAILABLE",
        )

    def _validate_output(self, result) -> PlanningAssistResult:
        if not isinstance(result, PlanningAssistResult):
            return self._build_result("Planning assist invalid JSON/non-object payload.")
        if result.status in {"SKIPPED", "UNAVAILABLE", "ERROR"}:
            return result
        if result.status != "USED":
            return self._build_result("Planning assist invalid output.")
        return result

    def review_plan(
        self, request: PlanningAssistRequest, config: PlanningAssistConfig
    ) -> PlanningAssistResult:
        if not config.enabled:
            return PlanningAssistResult(
                status="SKIPPED",
                warnings=["Planning assist disabled by config."],
            )

        model_resolution = resolve_copilot_model(request=request, config=config)
        if not model_resolution.ok:
            reason = "; ".join(model_resolution.errors) or (
                "Planning assist model resolution failed."
            )
            return PlanningAssistResult(
                status="UNAVAILABLE",
                warnings=[
                    f"{self._FAILURE_REASON_WARNING_PREFIX} {reason}",
                    *model_resolution.warnings,
                ],
                error=reason,
                requested_model=model_resolution.requested_model,
                resolved_model=model_resolution.model,
                model_source=model_resolution.source,
                model_verified=model_resolution.model_verified,
            )

        auth = resolve_copilot_auth(config)
        if not auth.ok:
            reason = "; ".join(auth.errors) or "Planning assist missing authentication."
            return PlanningAssistResult(
                status="UNAVAILABLE",
                warnings=[
                    f"{self._FAILURE_REASON_WARNING_PREFIX} {reason}",
                    *auth.warnings,
                ],
                error=reason,
                requested_model=model_resolution.requested_model,
                resolved_model=model_resolution.model,
                model_source=model_resolution.source,
                model_verified=model_resolution.model_verified,
            )

        resolved_request = PlanningAssistRequest(
            run_id=request.run_id,
            agent=request.agent,
            phase=request.phase,
            model=model_resolution.model,
            prompt=request.prompt,
            context=request.context,
            allowed_fields=request.allowed_fields,
            forbidden_fields=request.forbidden_fields,
        )

        try:
            # Provider invocation remains unbound; auth metadata is resolved up front.
            raw_result = self._perform_provider_review(
                request=resolved_request,
                config=config,
            )
            result = self._validate_output(raw_result)
            return PlanningAssistResult(
                status=result.status,
                missing_warnings=result.missing_warnings,
                approval_summary_improvements=result.approval_summary_improvements,
                operator_notes=result.operator_notes,
                risk_explanations=result.risk_explanations,
                confidence=result.confidence,
                warnings=result.warnings,
                error=result.error,
                requested_model=model_resolution.requested_model,
                resolved_model=model_resolution.model,
                model_source=model_resolution.source,
                model_verified=model_resolution.model_verified,
            )
        except Exception as error:
            return self._build_result(self._normalize_failure_reason(error))
