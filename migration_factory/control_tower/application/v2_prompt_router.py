"""Event-based prompt router (F03).

Maps backend migration events to prompt templates and strict output schemas.
The router creates typed, bounded model-call request objects only.
It does NOT execute, approve, resolve paths, or choose commands.

Router responsibilities:
1. Accept event_type, ContextPack, and bounded event payload.
2. Select prompt template id and schema name.
3. Select token budget from existing TOKEN_BUDGETS.
4. Return a ModelCallRequest object only.
5. Validate model output with validate_model_output().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.application.v2_model_schemas import (
    ContextPack,
    TOKEN_BUDGETS,
    SCHEMA_REGISTRY,
    validate_model_output,
    SchemaValidationError,
)


# ── Routing table ──────────────────────────────────────────────────

# Maps event_type -> (prompt_template_id, output_schema_name)
# Only failure events that produce actionable migration objects are routed.
# Analysis/planning/final-report events are deferred.
EVENT_ROUTES: dict[str, tuple[str, str]] = {
    "build_failed": ("repair_diagnosis", "RepairProposal"),
    "test_failed": ("repair_diagnosis", "RepairProposal"),
    "transform_failed": ("repair_diagnosis", "RepairProposal"),
    "pom_issue_detected": ("pom_repair", "RepairProposal"),
    "review_requested": ("reviewer", "ReviewerCritique"),
    "repair_proposal_revised": ("revise_repair", "RepairProposal"),
}

# Deferred event types (not yet routed)
DEFERRED_EVENTS: tuple[str, ...] = (
    "analysis_completed",
    "planning_completed",
    "final_report_requested",
)

# Map schema names (PascalCase) to TOKEN_BUDGETS keys (snake_case)
SCHEMA_TO_BUDGET_KEY: dict[str, str] = {
    "RepairProposal": "repair_proposal",
    "ReviewerCritique": "reviewer_critique",
    "PlanProposal": "plan_proposal",
    "ActionRequest": "action_request",
    "AssistantAnswer": "assistant_answer",
}


# ── Data types ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class PromptTemplate:
    """A prompt template with an id and a format string.

    Templates are inline Python constants (not YAML or DB records)
    for the first implementation slice.
    """

    template_id: str
    template: str


@dataclass(frozen=True)
class ModelCallRequest:
    """A resolved model-call request.

    This is the router's output — a typed request object that describes
    what the model should be asked and what schema the output must match.
    It does NOT contain execution fields.
    """

    request_id: str
    event_type: str
    prompt_template_id: str
    output_schema_name: str
    prompt_text: str
    token_budget_input: int
    token_budget_output: int
    context_pack_checksum: str
    created_at: str


@dataclass(frozen=True)
class ModelCallResult:
    """Result of a model call after router validation.

    Contains the validated model output plus routing metadata for audit.
    """

    request_id: str
    output_schema_name: str
    validated_output: dict[str, Any]
    success: bool
    failure_reason: str = ""
    created_at: str = ""


# ── Prompt templates ──────────────────────────────────────────────

PROMPT_TEMPLATES: dict[str, PromptTemplate] = {
    "repair_diagnosis": PromptTemplate(
        template_id="repair_diagnosis",
        template=(
            "You are an AI migration repair assistant. "
            "Analyze the following failure evidence from a Spring Boot migration.\n\n"
            "Event type: {event_type}\n"
            "Stage index: {stage_index}\n"
            "Failure context: {failure_summary}\n"
            "Evidence refs: {evidence_refs}\n"
            "POM summary ref: {pom_summary_ref}\n"
            "Sandbox binding ref: {sandbox_binding_ref}\n\n"
            "Produce a RepairProposal with:\n"
            "- failure_hypothesis: Brief diagnosis of root cause\n"
            "- patch_summary: What change would fix this\n"
            "- affected_paths: Files that need modification\n"
            "- validation_plan: How to verify the fix\n\n"
            "RULES:\n"
            "- Never propose modifying legacy source outside the sandbox.\n"
            "- Never approve or execute commands.\n"
            "- Use only evidence provided above.\n"
            "- Keep the proposal bounded to the immediate failure."
        ),
    ),
    "pom_repair": PromptTemplate(
        template_id="pom_repair",
        template=(
            "You are an AI migration POM repair assistant. "
            "Analyze the following POM-related issue.\n\n"
            "Event type: {event_type}\n"
            "Stage index: {stage_index}\n"
            "Issue: {failure_summary}\n"
            "POM summary ref: {pom_summary_ref}\n\n"
            "Produce a RepairProposal with:\n"
            "- failure_hypothesis: Root cause of the POM issue\n"
            "- patch_summary: Specific POM changes\n"
            "- affected_paths: POM files to modify\n"
            "- validation_plan: Maven command to validate\n\n"
            "RULES:\n"
            "- Only propose changes to POM/build files, not Java source.\n"
            "- Never approve or execute commands."
        ),
    ),
    "reviewer": PromptTemplate(
        template_id="reviewer",
        template=(
            "You are a migration reviewer. "
            "Review the following proposal.\n\n"
            "Event type: {event_type}\n"
            "Stage index: {stage_index}\n"
            "Proposal summary: {failure_summary}\n"
            "Evidence refs: {evidence_refs}\n"
            "Sandbox binding ref: {sandbox_binding_ref}\n"
            "POM summary ref: {pom_summary_ref}\n"
            "Safety policy: {safety_policy}\n"
            "Proposal payload checksum: {proposal_checksum}\n"
            "Context pack checksum: {context_pack_checksum}\n\n"
            "Produce a ReviewerCritique with:\n"
            "- decision: accept, revise, or reject (required)\n"
            "- reasoning: Why this decision (required)\n"
            "- missing_evidence: List of missing evidence (required, can be [])\n"
            "- unsafe_assumptions: Any risks or unsafe assumptions (required, can be [])\n\n"
            "RULES:\n"
            "- Be specific about risks and missing evidence.\n"
            "- Verify the sandbox binding is not legacy source.\n"
            "- Never approve or execute. Accept is only a policy gate.\n"
            "- A revised proposal needs a fresh critique."
        ),
    ),
    "revise_repair": PromptTemplate(
        template_id="revise_repair",
        template=(
            "You are an AI migration repair assistant. "
            "Revise the following repair proposal according to the operator's steering instruction.\n\n"
            "Event type: {event_type}\n"
            "Stage index: {stage_index}\n"
            "Source proposal ID: {source_proposal_id}\n"
            "Failed command ID: {failed_command_id}\n"
            "Original failure summary: {failure_summary}\n"
            "Original hypothesis: {hypothesis}\n"
            "Original patch summary: {patch_summary}\n"
            "Original affected paths: {affected_paths}\n"
            "Evidence refs: {evidence_refs}\n"
            "POM summary ref: {pom_summary_ref}\n"
            "Sandbox binding ref: {sandbox_binding_ref}\n"
            "Context pack checksum: {context_pack_checksum}\n"
            "Allowed scope: {allowed_scope}\n"
            "Safety policy: {safety_policy}\n\n"
            "OPERATOR STEERING INSTRUCTION:\n"
            "{revision_instruction}\n\n"
            "Produce a revised RepairProposal with:\n"
            "- failure_hypothesis: Revised diagnosis of root cause\n"
            "- patch_summary: Revised change description\n"
            "- affected_paths: Files that need modification\n"
            "- validation_plan: How to verify the fix\n\n"
            "RULES:\n"
            "- Incorporate the operator's steering instruction above.\n"
            "- If allowed_scope is pom_only, ONLY propose changes to pom.xml files.\n"
            "- Never propose modifying legacy source outside the sandbox.\n"
            "- Never approve or execute commands.\n"
            "- Use only evidence provided above.\n"
            "- Keep the proposal bounded to the immediate failure."
        ),
    ),
}


# ── Router service ─────────────────────────────────────────────────


class EventPromptRouter:
    """Routes migration events to prompt templates and output schemas.

    The router is stateless and idempotent. It returns ModelCallRequest
    objects that describe what the model should be asked. Backend services
    then execute the model call, validate output, and persist results.
    """

    @staticmethod
    def route(
        event_type: str,
        pack: ContextPack,
        payload: dict[str, Any] | None = None,
    ) -> ModelCallRequest:
        """Route an event to a prompt template and schema.

        Args:
            event_type: The backend event type (e.g. build_failed).
            pack: The enriched ContextPack with evidence refs and metadata.
            payload: Optional additional payload fields for prompt filling.

        Returns:
            A ModelCallRequest describing the model call.

        Raises:
            ValueError: If event_type is unknown or deferred.
        """
        route_entry = EVENT_ROUTES.get(event_type)
        if route_entry is None:
            if event_type in DEFERRED_EVENTS:
                raise ValueError(
                    f"Event {event_type!r} is deferred and not yet routable"
                )
            raise ValueError(
                f"Unknown event type {event_type!r}. "
                f"Known routes: {', '.join(sorted(EVENT_ROUTES))}"
            )

        prompt_template_id, schema_name = route_entry

        # Validate schema exists
        if schema_name not in SCHEMA_REGISTRY:
            raise ValueError(
                f"Schema {schema_name!r} not found in SCHEMA_REGISTRY"
            )

        template_obj = PROMPT_TEMPLATES.get(prompt_template_id)
        if template_obj is None:
            raise ValueError(
                f"Prompt template {prompt_template_id!r} not found"
            )

        # Format the prompt template with context
        prompt_text = EventPromptRouter._format_prompt(
            template=template_obj.template,
            pack=pack,
            payload=payload or {},
        )

        # Get token budgets (map PascalCase schema names to snake_case keys)
        budget_key = SCHEMA_TO_BUDGET_KEY.get(schema_name, schema_name.lower())
        budgets = TOKEN_BUDGETS.get(
            budget_key,
            {"input": 8000, "output": 2000},
        )

        return ModelCallRequest(
            request_id=uuid4().hex,
            event_type=event_type,
            prompt_template_id=prompt_template_id,
            output_schema_name=schema_name,
            prompt_text=prompt_text,
            token_budget_input=budgets["input"],
            token_budget_output=budgets["output"],
            context_pack_checksum=pack.checksum,
            created_at=utc_now_text(),
        )

    @staticmethod
    def validate_model_output(
        schema_name: str,
        output: dict[str, Any],
    ) -> ModelCallResult:
        """Validate model output against the expected schema.

        This is a thin wrapper around validate_model_output() that
        produces a ModelCallResult with routing metadata.

        Args:
            schema_name: The schema name to validate against.
            output: The model output dict.

        Returns:
            A ModelCallResult with validated output or failure info.
        """
        try:
            candidate = dict(output)
            if schema_name == "ReviewerCritique":
                candidate.setdefault("missing_evidence", [])
                candidate.setdefault("unsafe_assumptions", [])
            validated = validate_model_output(schema_name, candidate)
            return ModelCallResult(
                request_id="",  # Set by caller
                output_schema_name=schema_name,
                validated_output=validated,
                success=True,
                created_at=utc_now_text(),
            )
        except SchemaValidationError as exc:
            return ModelCallResult(
                request_id="",
                output_schema_name=schema_name,
                validated_output={},
                success=False,
                failure_reason=str(exc),
                created_at=utc_now_text(),
            )

    @staticmethod
    def list_routes() -> dict[str, dict[str, str]]:
        """List all configured routes (for diagnostics/audit)."""
        return {
            event_type: {
                "prompt_template_id": template_id,
                "output_schema": schema_name,
            }
            for event_type, (template_id, schema_name) in EVENT_ROUTES.items()
        }

    @staticmethod
    def is_routable(event_type: str) -> bool:
        """Check if an event type can be routed."""
        return event_type in EVENT_ROUTES

    @staticmethod
    def _format_prompt(
        template: str,
        pack: ContextPack,
        payload: dict[str, Any],
    ) -> str:
        """Format a prompt template with context pack and payload data."""
        # Use payload fields first (works without F01 metadata), fall back to pack
        fmt_vars: dict[str, str] = {
            "event_type": payload.get("event_type", "unknown"),
            "stage_index": str(payload.get("stage_index", 1)),
            "failure_summary": payload.get("failure_summary", pack.description or "Unknown failure"),
            "evidence_refs": ", ".join(pack.evidence_refs) if pack.evidence_refs else payload.get("evidence_refs", "none"),
            "pom_summary_ref": payload.get("pom_summary_ref", "none"),
            "sandbox_binding_ref": payload.get("sandbox_binding_ref", "none"),
            "safety_policy": payload.get("safety_policy", "No legacy source mutation. Only sandbox changes. Human approval required."),
            "proposal_checksum": payload.get("proposal_checksum", pack.checksum if pack.checksum else "unknown"),
            "context_pack_checksum": payload.get("context_pack_checksum", pack.checksum),
        }
        # Add any additional payload fields (strings, ints, floats, lists)
        for key, value in payload.items():
            if key not in fmt_vars:
                if isinstance(value, str):
                    fmt_vars[key] = value
                elif isinstance(value, (int, float)):
                    fmt_vars[key] = str(value)
                elif isinstance(value, (list, tuple)):
                    fmt_vars[key] = ", ".join(str(v) for v in value)

        return template.format(**fmt_vars)

    @staticmethod
    def request_to_dict(request: ModelCallRequest) -> dict[str, Any]:
        """Convert a ModelCallRequest to a dict for API responses."""
        return {
            "request_id": request.request_id,
            "event_type": request.event_type,
            "prompt_template_id": request.prompt_template_id,
            "output_schema_name": request.output_schema_name,
            "token_budget_input": request.token_budget_input,
            "token_budget_output": request.token_budget_output,
            "context_pack_checksum": request.context_pack_checksum,
            "created_at": request.created_at,
            # NEVER include prompt_text in API responses
            # (it may contain sensitive migration context)
        }

    @staticmethod
    def result_to_dict(result: ModelCallResult) -> dict[str, Any]:
        """Convert a ModelCallResult to a dict for API responses."""
        return {
            "request_id": result.request_id,
            "output_schema_name": result.output_schema_name,
            "validated_output": result.validated_output,
            "success": result.success,
            "failure_reason": result.failure_reason,
            "created_at": result.created_at,
        }
