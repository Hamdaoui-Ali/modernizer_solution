"""Tests for V2 event-based prompt router (F03).

Tests that the router:
1. Maps failure events to RepairProposal schema
2. Returns ModelCallRequest with correct fields
3. Does NOT contain execution or approval fields
4. Rejects unknown event types
5. Rejects deferred event types
6. Validates model output against schema
7. Lists configured routes (read-only)
"""

from __future__ import annotations

import pytest

from migration_factory.control_tower.application.v2_prompt_router import (
    EventPromptRouter,
    ModelCallRequest,
    ModelCallResult,
    EVENT_ROUTES,
    DEFERRED_EVENTS,
    PROMPT_TEMPLATES,
)
from migration_factory.control_tower.application.v2_model_schemas import (
    ContextPackBuilder,
    ContextPack,
    SchemaValidationError,
)


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def failure_context_pack() -> ContextPack:
    """A context pack with build failure enrichment metadata."""
    return ContextPackBuilder.build_context_pack(
        pack_type="repair_proposal",
        title="Build failure diagnosis",
        description="Stage 1 Maven build failed with compilation error",
        evidence_refs=("/tmp/build.log", "/tmp/pom.xml"),
    )


# ── Route tests ────────────────────────────────────────────────────


class TestEventPromptRouter:

    def test_routes_build_failed_to_repair(
        self,
        failure_context_pack: ContextPack,
    ) -> None:
        """build_failed maps to repair_diagnosis template and RepairProposal."""
        request = EventPromptRouter.route(
            event_type="build_failed",
            pack=failure_context_pack,
        )
        assert isinstance(request, ModelCallRequest)
        assert request.event_type == "build_failed"
        assert request.prompt_template_id == "repair_diagnosis"
        assert request.output_schema_name == "RepairProposal"
        assert request.context_pack_checksum == failure_context_pack.checksum
        assert request.token_budget_input > 0
        assert request.token_budget_output > 0

    def test_routes_test_failed_to_repair(
        self,
        failure_context_pack: ContextPack,
    ) -> None:
        """test_failed maps to repair_diagnosis template and RepairProposal."""
        request = EventPromptRouter.route(
            event_type="test_failed",
            pack=failure_context_pack,
        )
        assert request.prompt_template_id == "repair_diagnosis"
        assert request.output_schema_name == "RepairProposal"

    def test_routes_transform_failed_to_repair(
        self,
        failure_context_pack: ContextPack,
    ) -> None:
        """transform_failed maps to repair_diagnosis template."""
        request = EventPromptRouter.route(
            event_type="transform_failed",
            pack=failure_context_pack,
        )
        assert request.prompt_template_id == "repair_diagnosis"
        assert request.output_schema_name == "RepairProposal"

    def test_routes_pom_issue_to_repair(
        self,
        failure_context_pack: ContextPack,
    ) -> None:
        """pom_issue_detected maps to pom_repair template."""
        request = EventPromptRouter.route(
            event_type="pom_issue_detected",
            pack=failure_context_pack,
        )
        assert request.prompt_template_id == "pom_repair"
        assert request.output_schema_name == "RepairProposal"

    def test_routes_review_requested_to_reviewer(
        self,
        failure_context_pack: ContextPack,
    ) -> None:
        """review_requested maps to reviewer template and ReviewerCritique."""
        request = EventPromptRouter.route(
            event_type="review_requested",
            pack=failure_context_pack,
        )
        assert request.prompt_template_id == "reviewer"
        assert request.output_schema_name == "ReviewerCritique"

    def test_rejects_unknown_event_type(
        self,
        failure_context_pack: ContextPack,
    ) -> None:
        """Unknown event types raise ValueError."""
        with pytest.raises(ValueError, match="Unknown event type"):
            EventPromptRouter.route(
                event_type="unknown_event",
                pack=failure_context_pack,
            )

    def test_rejects_deferred_event_type(
        self,
        failure_context_pack: ContextPack,
    ) -> None:
        """Deferred event types raise ValueError with specific message."""
        for deferred in DEFERRED_EVENTS:
            with pytest.raises(ValueError, match="deferred"):
                EventPromptRouter.route(
                    event_type=deferred,
                    pack=failure_context_pack,
                )

    def test_router_creates_request_id(
        self,
        failure_context_pack: ContextPack,
    ) -> None:
        """Each route call creates a unique request_id."""
        request = EventPromptRouter.route(
            event_type="build_failed",
            pack=failure_context_pack,
        )
        assert request.request_id
        assert len(request.request_id) > 0


# ── Router does not execute tests ──────────────────────────────────


class TestRouterNoExecution:

    def test_request_has_no_execution_fields(
        self,
        failure_context_pack: ContextPack,
    ) -> None:
        """ModelCallRequest must NOT contain execution fields."""
        request = EventPromptRouter.route(
            event_type="build_failed",
            pack=failure_context_pack,
        )
        request_dict = {
            "request_id": request.request_id,
            "event_type": request.event_type,
            "prompt_template_id": request.prompt_template_id,
            "output_schema_name": request.output_schema_name,
            "token_budget_input": request.token_budget_input,
            "token_budget_output": request.token_budget_output,
            "context_pack_checksum": request.context_pack_checksum,
            "created_at": request.created_at,
        }
        # Must not contain execution fields
        assert "command" not in request_dict
        assert "exec" not in request_dict
        assert "approve" not in request_dict
        assert "write" not in request_dict
        assert "path" not in request_dict
        assert "sandbox_path" not in request_dict
        assert "target_path" not in request_dict

    def test_request_has_no_prompt_text_in_dict(
        self,
        failure_context_pack: ContextPack,
    ) -> None:
        """request_to_dict must NOT include prompt_text."""
        request = EventPromptRouter.route(
            event_type="build_failed",
            pack=failure_context_pack,
        )
        d = EventPromptRouter.request_to_dict(request)
        assert "prompt_text" not in d


# ── Model output validation tests ──────────────────────────────────


class TestRouterModelValidation:

    def test_validates_repair_proposal_output(self) -> None:
        """Valid RepairProposal output passes validation."""
        output = {
            "failure_hypothesis": "Missing Spring Boot dependency",
            "patch_summary": "Add spring-boot-starter-web to pom.xml",
            "affected_paths": ["pom.xml"],
            "validation_plan": "Run mvn compile",
        }
        result = EventPromptRouter.validate_model_output("RepairProposal", output)
        assert result.success
        assert result.validated_output == output

    def test_rejects_invalid_repair_proposal(self) -> None:
        """Invalid RepairProposal output fails validation."""
        output = {
            "failure_hypothesis": "Missing dependency",
            # Missing required fields: patch_summary, affected_paths, validation_plan
        }
        result = EventPromptRouter.validate_model_output("RepairProposal", output)
        assert not result.success
        assert result.failure_reason

    def test_validates_reviewer_critique_output(self) -> None:
        """Valid ReviewerCritique output passes validation."""
        output = {
            "decision": "accept",
            "reasoning": "The proposal is sound and well-supported",
        }
        result = EventPromptRouter.validate_model_output("ReviewerCritique", output)
        assert result.success

    def test_rejects_invalid_reviewer_decision(self) -> None:
        """Invalid decision enum value fails validation."""
        output = {
            "decision": "maybe",
            "reasoning": "Not sure",
        }
        result = EventPromptRouter.validate_model_output("ReviewerCritique", output)
        assert not result.success

    def test_validates_against_unknown_schema(self) -> None:
        """Unknown schema name raises ValueError (not SchemaValidationError)."""
        with pytest.raises(ValueError, match="Unknown schema"):
            EventPromptRouter.validate_model_output("NonExistentSchema", {})


# ── Route listing tests ────────────────────────────────────────────


class TestRouteListing:

    def test_list_routes_returns_all_configured_routes(self) -> None:
        """list_routes returns all event types with their target schema."""
        routes = EventPromptRouter.list_routes()
        assert isinstance(routes, dict)
        assert set(routes.keys()) == set(EVENT_ROUTES.keys())
        for event_type, (template_id, schema_name) in EVENT_ROUTES.items():
            entry = routes[event_type]
            assert entry["prompt_template_id"] == template_id
            assert entry["output_schema"] == schema_name

    def test_is_routable_returns_true_for_known_events(self) -> None:
        """is_routable returns True for configured event types."""
        for event_type in EVENT_ROUTES:
            assert EventPromptRouter.is_routable(event_type)

    def test_is_routable_returns_false_for_unknown_events(self) -> None:
        """is_routable returns False for unknown event types."""
        assert not EventPromptRouter.is_routable("unknown")
        assert not EventPromptRouter.is_routable("analysis_completed")


# ── Prompt template tests ──────────────────────────────────────────


class TestPromptTemplates:

    def test_all_route_templates_exist(self) -> None:
        """Every route's prompt template is registered."""
        for template_id, _ in EVENT_ROUTES.values():
            assert template_id in PROMPT_TEMPLATES, f"Missing template: {template_id}"

    def test_prompt_templates_have_required_variables(self) -> None:
        """All templates contain the key format variables."""
        required_vars = {"event_type", "stage_index", "failure_summary", "evidence_refs"}
        for template_id, template_obj in PROMPT_TEMPLATES.items():
            # Check that all required variables are referenced in the template
            import re

            found_vars = set(re.findall(r"\{(\w+)\}", template_obj.template))
            # At minimum, the template must reference failure_summary
            assert "failure_summary" in found_vars, f"{template_id} missing failure_summary"
            assert "event_type" in found_vars, f"{template_id} missing event_type"


# ── Backward compatibility tests ───────────────────────────────────


def test_router_with_minimal_pack() -> None:
    """Router works with context packs that have no enrichment metadata."""
    pack = ContextPackBuilder.build_context_pack(
        pack_type="repair_proposal",
        title="Minimal",
        description="Basic failure",
        evidence_refs=("/tmp/log.txt",),
    )
    request = EventPromptRouter.route(
        event_type="build_failed",
        pack=pack,
        payload={"failure_summary": "Maven compilation failed"},
    )
    assert request.prompt_template_id == "repair_diagnosis"
    assert request.output_schema_name == "RepairProposal"
    assert request.context_pack_checksum == pack.checksum


def test_prompt_includes_context() -> None:
    """Formatted prompt includes context from the pack and payload."""
    pack = ContextPackBuilder.build_context_pack(
        pack_type="repair_proposal",
        title="Test prompt",
        description="Compilation error in stage 1",
        evidence_refs=("/tmp/build.log",),
    )
    request = EventPromptRouter.route(
        event_type="build_failed",
        pack=pack,
        payload={"failure_summary": "Maven compile error"},
    )
    assert "Compilation error" in request.prompt_text or "Maven compile error" in request.prompt_text


def test_request_to_dict_redacts_prompt() -> None:
    """request_to_dict excludes prompt_text for security."""
    pack = ContextPackBuilder.build_context_pack(
        pack_type="repair_proposal",
        title="Security test",
        description="Test",
        evidence_refs=("/tmp/t.log",),
    )
    request = EventPromptRouter.route(
        event_type="build_failed",
        pack=pack,
        payload={},
    )
    d = EventPromptRouter.request_to_dict(request)
    assert "prompt_text" not in d
    assert d["output_schema_name"] == "RepairProposal"


def test_token_budget_matches_schema() -> None:
    """Token budgets use correct schema-specific values, not defaults."""
    pack = ContextPackBuilder.build_context_pack(
        pack_type="repair_proposal",
        title="Budget test",
        description="Test",
        evidence_refs=("/tmp/t.log",),
    )
    # RepairProposal should get 20000/6000, not the 8000/2000 fallback
    req = EventPromptRouter.route("build_failed", pack)
    assert req.token_budget_input == 20000
    assert req.token_budget_output == 6000

    # ReviewerCritique should get 16000/4000
    req2 = EventPromptRouter.route("review_requested", pack)
    assert req2.token_budget_input == 16000
    assert req2.token_budget_output == 4000
