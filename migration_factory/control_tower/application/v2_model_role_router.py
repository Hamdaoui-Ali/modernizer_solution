"""Role-based Azure/OpenAI routing for V2 model calls.

This module resolves per-role deployment env refs, applies safe fallback
selection, and optionally fail-closes on structured-output schema checks.
"""

from __future__ import annotations

import json
import os
import urllib.error
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from migration_factory.control_tower.application.v2_model_schemas import validate_model_output
from migration_factory.control_tower.application.v2_settings import ControlTowerSettings
from migration_factory.control_tower.application.redaction import redact_model_summary


class V2ModelRole(str, Enum):
    ASSISTANT = "assistant"
    PROPOSER = "proposer"
    REVIEWER = "reviewer"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class V2RoleModelRequest:
    role: V2ModelRole
    prompt: str
    fallback: str
    output_schema_name: str | None = None
    require_schema: bool = False
    conversation_history: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class V2RoleModelResult:
    content: str
    role: str
    provider: str
    source: str
    model_status: str
    success: bool
    failure_reason: str
    primary_failure_reason: str = ""
    fallback_failure_reason: str = ""
    parser_failure_reason: str = ""
    fallback_used: bool = False
    fallback_attempted: bool = False
    schema_validated: bool = False
    configured_max_input_tokens: int = 0
    configured_max_output_tokens: int = 0
    response_format_used: str = ""
    configured_deployment: str = ""
    actual_deployment: str = ""
    fallback_deployment: str = ""
    primary_http_status: str = ""
    fallback_http_status: str = ""
    timeout_occurred: bool = False
    schema_validation_error: str = ""
    transport: str = ""
    azure_request_id: str = ""
    retry_count: int = 0
    retry_after: str = ""
    primary_raw_content: str = ""
    fallback_raw_content: str = ""


@dataclass(frozen=True)
class V2RoleModelRoute:
    request: V2RoleModelRequest
    primary_env_ref: str
    primary_deployment: str
    fallback_env_ref: str
    fallback_deployment: str
    fallback_enabled: bool


@dataclass(frozen=True)
class V2RoleBudget:
    max_input_tokens: int
    max_output_tokens: int
    reasoning_effort: str | None
    response_format: str | None


class V2ModelRoleRouter:
    """Resolve role-specific deployments and execute safe fallback selection."""

    def __init__(self, settings: ControlTowerSettings | None = None) -> None:
        self._settings = settings or ControlTowerSettings()

    def plan(self, request: V2RoleModelRequest, *, settings: ControlTowerSettings | None = None) -> V2RoleModelRoute:
        active_settings = settings or self._settings
        primary_env_ref = self._role_env_ref(request.role, active_settings)
        fallback_env_ref = active_settings.azure_foundry_fallback_deployment_env or ""
        return V2RoleModelRoute(
            request=request,
            primary_env_ref=primary_env_ref,
            primary_deployment=os.environ.get(primary_env_ref, "").strip(),
            fallback_env_ref=fallback_env_ref,
            fallback_deployment=os.environ.get(fallback_env_ref, "").strip(),
            fallback_enabled=bool(active_settings.azure_foundry_fallback_enabled),
        )

    def route(
        self,
        request: V2RoleModelRequest,
        *,
        invoke: Callable[[str], Any],
        settings: ControlTowerSettings | None = None,
    ) -> V2RoleModelResult:
        route = self.plan(request, settings=settings)
        primary_result, primary_failure, primary_http, primary_timeout = self._try_invoke(
            invoke,
            deployment=route.primary_deployment,
            request=request,
            role=request.role.value,
        )
        if primary_result is not None:
            result = self._coerce_primary_result(primary_result, request)
            primary_raw_content = result.content
            schema_error = ""
            if result.success:
                schema_error = self._schema_error(request, result.content)
            if result.success and not schema_error:
                return V2RoleModelResult(
                    content=result.content,
                    role=result.role,
                    provider=result.provider,
                    source=result.source,
                    model_status=result.model_status,
                    success=True,
                    failure_reason="",
                    primary_failure_reason="",
                    fallback_used=False,
                    schema_validated=True,
                    configured_max_input_tokens=result.configured_max_input_tokens,
                    configured_max_output_tokens=result.configured_max_output_tokens,
                    response_format_used=result.response_format_used,
                    configured_deployment=route.primary_deployment,
                    actual_deployment=route.primary_deployment,
                    fallback_deployment=route.fallback_deployment,
                    primary_http_status=primary_http,
                    timeout_occurred=primary_timeout,
                    schema_validation_error="",
                    transport=result.transport,
                    azure_request_id=result.azure_request_id,
                    retry_count=result.retry_count,
                    retry_after=result.retry_after,
                    primary_raw_content=primary_raw_content,
                )
            primary_failure = schema_error or result.failure_reason or primary_failure or "primary_model_failed"
            if not primary_http and primary_timeout:
                primary_failure = "timeout"
        else:
            result = None

        fallback_http = ""
        fallback_timeout = False
        if route.fallback_enabled and route.fallback_deployment:
            fallback_result, fallback_failure, fallback_http, fallback_timeout = self._try_invoke(
                invoke,
                deployment=route.fallback_deployment,
                request=request,
                role=V2ModelRole.FALLBACK.value,
            )
            if fallback_result is not None:
                result = self._coerce_fallback_result(
                    fallback_result,
                    request,
                    primary_failure_reason=primary_failure,
                )
                fallback_raw_content = result.content
                schema_error = ""
                if result.success:
                    schema_error = self._schema_error(request, result.content)
                if result.success and not schema_error:
                    return V2RoleModelResult(
                        content=result.content,
                        role=result.role,
                        provider=result.provider,
                        source=result.source,
                        model_status=result.model_status,
                        success=True,
                        failure_reason="",
                        primary_failure_reason=primary_failure,
                        fallback_used=True,
                        schema_validated=True,
                        configured_max_input_tokens=result.configured_max_input_tokens,
                        configured_max_output_tokens=result.configured_max_output_tokens,
                        response_format_used=result.response_format_used,
                        configured_deployment=route.fallback_deployment,
                        actual_deployment=route.fallback_deployment,
                        fallback_attempted=True,
                        fallback_deployment=route.fallback_deployment,
                        primary_http_status=primary_http,
                        fallback_http_status=fallback_http,
                        timeout_occurred=primary_timeout or fallback_timeout,
                        schema_validation_error=schema_error,
                        transport=result.transport,
                        azure_request_id=result.azure_request_id,
                        retry_count=result.retry_count,
                        retry_after=result.retry_after,
                        primary_raw_content=primary_raw_content,
                        fallback_raw_content=fallback_raw_content,
                    )
                fallback_failure = result.failure_reason or fallback_failure or "fallback_model_failed"
                if schema_error:
                    fallback_failure = f"{schema_error}: {fallback_failure}"
            else:
                fallback_failure = fallback_failure or "fallback_model_failed"
        else:
            fallback_failure = ""

        return self._deterministic_result(
            request=request,
            primary_failure_reason=primary_failure or "primary_model_unavailable",
            fallback_failure_reason=fallback_failure,
            fallback_attempted=bool(route.fallback_enabled and route.fallback_deployment),
            primary_http_status=primary_http,
            fallback_http_status=fallback_http,
            timeout_occurred=primary_timeout or fallback_timeout,
            schema_validation_error=(primary_failure if "schema_validation_failed" in primary_failure else fallback_failure if "schema_validation_failed" in fallback_failure else ""),
            primary_raw_content=locals().get("primary_raw_content", ""),
            fallback_raw_content=locals().get("fallback_raw_content", ""),
        )

    def resolve_budget(self, *, role: V2ModelRole, responsibility: str = "", output_schema_name: str | None = None) -> V2RoleBudget:
        return self._resolve_budget(role=role, responsibility=responsibility, output_schema_name=output_schema_name)

    def _try_invoke(
        self,
        invoke: Callable[[str], Any],
        *,
        deployment: str,
        request: V2RoleModelRequest,
        role: str,
    ) -> tuple[Any | None, str, str, bool]:
        if not deployment:
            return None, f"missing_{role}_deployment", "", False
        try:
            result = invoke(deployment)
            return result, "", "", False
        except urllib.error.HTTPError as exc:
            code = int(getattr(exc, "code", 0) or 0)
            return None, redact_model_summary(f"http_{code}: {exc}"), str(code), False
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = str(getattr(exc, "reason", exc))
            is_timeout = "timeout" in reason.lower() or "timed out" in reason.lower()
            return None, redact_model_summary(f"{type(exc).__name__}: {exc}"), "", is_timeout
        except Exception as exc:
            return None, redact_model_summary(f"{type(exc).__name__}: {exc}"), "", False

    def _coerce_primary_result(self, result: Any, request: V2RoleModelRequest) -> V2RoleModelResult:
        return V2RoleModelResult(
            content=str(getattr(result, "content", "") or ""),
            role=request.role.value,
            provider=str(getattr(result, "provider", "") or "azure_openai"),
            source=str(getattr(result, "source", "") or "azure_openai"),
            model_status=str(getattr(result, "model_status", "") or "live_ok"),
            success=bool(getattr(result, "success", False)),
            failure_reason=str(getattr(result, "failure_reason", "") or ""),
            parser_failure_reason=str(getattr(result, "parser_failure_reason", "") or ""),
            configured_max_input_tokens=int(getattr(result, "configured_max_input_tokens", 0) or 0),
            configured_max_output_tokens=int(getattr(result, "configured_max_output_tokens", 0) or 0),
            response_format_used=str(getattr(result, "response_format_used", "") or ""),
            primary_http_status=str(getattr(result, "primary_http_status", "") or ""),
            fallback_http_status=str(getattr(result, "fallback_http_status", "") or ""),
            timeout_occurred=bool(getattr(result, "timeout_occurred", False)),
            schema_validation_error=str(getattr(result, "schema_validation_error", "") or ""),
            fallback_failure_reason=str(getattr(result, "fallback_failure_reason", "") or ""),
            transport=str(getattr(result, "transport", "") or ""),
            azure_request_id=str(getattr(result, "azure_request_id", "") or ""),
            retry_count=int(getattr(result, "retry_count", 0) or 0),
            retry_after=str(getattr(result, "retry_after", "") or ""),
        )

    def _coerce_fallback_result(
        self,
        result: Any,
        request: V2RoleModelRequest,
        *,
        primary_failure_reason: str,
        fallback_failure_reason: str = "",
    ) -> V2RoleModelResult:
        coerced = self._coerce_primary_result(result, request)
        return V2RoleModelResult(
            content=coerced.content,
            role=request.role.value,
            provider=coerced.provider,
            source=coerced.source,
            model_status=coerced.model_status,
            success=coerced.success,
            failure_reason=coerced.failure_reason,
            primary_failure_reason=primary_failure_reason,
            fallback_failure_reason=fallback_failure_reason or coerced.fallback_failure_reason,
            fallback_used=True,
            schema_validated=coerced.schema_validated,
            configured_max_input_tokens=coerced.configured_max_input_tokens,
            configured_max_output_tokens=coerced.configured_max_output_tokens,
            response_format_used=coerced.response_format_used,
            primary_http_status=coerced.primary_http_status,
            fallback_http_status=coerced.fallback_http_status,
            timeout_occurred=coerced.timeout_occurred,
            schema_validation_error=coerced.schema_validation_error,
            transport=coerced.transport, azure_request_id=coerced.azure_request_id,
            retry_count=coerced.retry_count, retry_after=coerced.retry_after,
        )

    def _schema_ok(self, request: V2RoleModelRequest, content: str) -> bool:
        return not self._schema_error(request, content)

    def _schema_error(self, request: V2RoleModelRequest, content: str) -> str:
        if not request.require_schema:
            return ""
        if not request.output_schema_name:
            return "schema_validation_failed: missing schema name"
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            return f"json_parse_failed: {exc.msg} at position {exc.pos}"
        try:
            validate_model_output(request.output_schema_name, parsed)
        except Exception as exc:
            return f"schema_validation_failed: {type(exc).__name__}: {exc}"
        return ""

    def _deterministic_result(
        self,
        *,
        request: V2RoleModelRequest,
        primary_failure_reason: str,
        fallback_failure_reason: str,
        fallback_attempted: bool = False,
        primary_http_status: str = "",
        fallback_http_status: str = "",
        timeout_occurred: bool = False,
        schema_validation_error: str = "",
        transport: str = "",
        azure_request_id: str = "",
        retry_count: int = 0,
        retry_after: str = "",
        primary_raw_content: str = "",
        fallback_raw_content: str = "",
    ) -> V2RoleModelResult:
        content = self._deterministic_content(request, primary_failure_reason, fallback_failure_reason)
        schema_validated = self._schema_ok(request, content)
        budget = self._resolve_budget(role=request.role, output_schema_name=request.output_schema_name)
        return V2RoleModelResult(
            content=content,
            role=request.role.value,
            provider="deterministic",
            source="deterministic",
            model_status="fallback",
            success=False,
            failure_reason=fallback_failure_reason or primary_failure_reason or "deterministic_fallback",
            primary_failure_reason=primary_failure_reason,
            fallback_failure_reason=fallback_failure_reason,
            fallback_used=bool(fallback_failure_reason),
            fallback_attempted=fallback_attempted,
            schema_validated=schema_validated,
            configured_max_input_tokens=budget.max_input_tokens,
            configured_max_output_tokens=budget.max_output_tokens,
            response_format_used=budget.response_format or "",
            configured_deployment=self.plan(request).primary_deployment,
            fallback_deployment=self.plan(request).fallback_deployment,
            primary_http_status=primary_http_status,
            fallback_http_status=fallback_http_status,
            timeout_occurred=timeout_occurred,
            schema_validation_error=schema_validation_error,
            transport=transport,
            azure_request_id=azure_request_id,
            retry_count=retry_count,
            retry_after=retry_after,
            primary_raw_content=primary_raw_content,
            fallback_raw_content=fallback_raw_content,
        )

    def _deterministic_content(
        self,
        request: V2RoleModelRequest,
        primary_failure_reason: str,
        fallback_failure_reason: str,
    ) -> str:
        safe_reason = redact_model_summary(
            fallback_failure_reason or primary_failure_reason or "model_unavailable"
        )
        if request.require_schema and request.output_schema_name == "ReviewerCritique":
            return json.dumps(
                {
                    "decision": "revise",
                    "reasoning": "Reviewer model unavailable; fail-closed review requires revision or manual evidence review.",
                    "missing_evidence": ["Reviewer model output unavailable"],
                    "unsafe_assumptions": ["No independent model critique was completed"],
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        if request.require_schema and request.output_schema_name == "AssistantAnswer":
            return json.dumps(
                {"answer": request.fallback, "evidence_refs": []},
                separators=(",", ":"),
                sort_keys=True,
            )
        if request.role == V2ModelRole.REVIEWER:
            return json.dumps(
                {
                    "decision": "revise",
                    "reasoning": "Reviewer model unavailable; fail-closed review requires revision or manual evidence review.",
                    "missing_evidence": ["Reviewer model output unavailable"],
                    "unsafe_assumptions": ["No independent model critique was completed"],
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        return (
            f"{request.fallback}\n\nModel: fallback\nSource: deterministic\nReason: {safe_reason}"
        )

    def _role_env_ref(self, role: V2ModelRole, settings: ControlTowerSettings) -> str:
        if role == V2ModelRole.PROPOSER:
            return settings.azure_foundry_proposer_deployment_env
        if role == V2ModelRole.REVIEWER:
            return settings.azure_foundry_reviewer_deployment_env
        if role == V2ModelRole.FALLBACK:
            return settings.azure_foundry_fallback_deployment_env
        return settings.azure_foundry_assistant_deployment_env

    def _resolve_budget(self, *, role: V2ModelRole, responsibility: str = "", output_schema_name: str | None = None) -> V2RoleBudget:
        role_key = role.value.upper()
        max_input_tokens = self._read_int_env(f"AZURE_OPENAI_{role_key}_MAX_INPUT_TOKENS", 40000)
        max_output_tokens = self._read_int_env(f"AZURE_OPENAI_{role_key}_MAX_OUTPUT_TOKENS", 700)
        reasoning_effort = self._resolve_reasoning_effort(role)
        response_format = self._read_str_env(f"AZURE_OPENAI_{role_key}_RESPONSE_FORMAT")
        if not response_format and output_schema_name and responsibility == "repair_proposal":
            response_format = "json_schema"
        return V2RoleBudget(
            max_input_tokens=min(40000, max(1, max_input_tokens)),
            max_output_tokens=min(20000, max(1, max_output_tokens)),
            reasoning_effort=reasoning_effort,
            response_format=response_format or None,
        )

    def resolve_timeout(self, *, role: V2ModelRole) -> int:
        role_key = role.value.upper()
        role_timeout = self._read_int_env(f"AI_MIGRATION_{role_key}_TIMEOUT_SECONDS", 0)
        if role_timeout > 0:
            return role_timeout
        role_timeout = self._read_int_env(f"AZURE_OPENAI_{role_key}_TIMEOUT_SECONDS", 0)
        if role_timeout > 0:
            return role_timeout
        generic_timeout = self._read_int_env("AZURE_OPENAI_TIMEOUT_SECONDS", 0)
        if generic_timeout > 0:
            return generic_timeout
        return 300

    def _resolve_reasoning_effort(self, role: V2ModelRole) -> str | None:
        role_key = role.value.upper()
        role_env = f"AZURE_OPENAI_{role_key}_REASONING_EFFORT"
        if role_env in os.environ:
            raw = os.environ.get(role_env, "").strip()
            if raw:
                return raw
            return None
        generic = self._read_str_env("AZURE_OPENAI_REASONING_EFFORT")
        if generic:
            return generic
        return None

    @staticmethod
    def _read_int_env(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    @staticmethod
    def _read_str_env(name: str) -> str:
        return os.environ.get(name, "").strip()
