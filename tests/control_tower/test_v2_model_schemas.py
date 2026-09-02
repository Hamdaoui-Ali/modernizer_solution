"""Tests for V2 structured output schemas and context packs."""

import json
import pytest

from migration_factory.control_tower.application.v2_model_schemas import (
    SCHEMA_REGISTRY,
    REQUIRED_SCHEMAS,
    TOKEN_BUDGETS,
    PLAN_PROPOSAL_SCHEMA,
    REPAIR_PROPOSAL_SCHEMA,
    REVIEWER_CRITIQUE_SCHEMA,
    ACTION_REQUEST_SCHEMA,
    ASSISTANT_ANSWER_SCHEMA,
    ContextPackBuilder,
)


def test_all_required_schemas_exist() -> None:
    for name in REQUIRED_SCHEMAS:
        assert name in SCHEMA_REGISTRY, f"Missing schema: {name}"


def test_schemas_have_additional_properties_false() -> None:
    for name, schema in SCHEMA_REGISTRY.items():
        assert schema.get("additionalProperties") is False, f"{name} allows extra properties"
        assert "required" in schema, f"{name} missing required fields"
        assert "properties" in schema, f"{name} missing properties"


def test_plan_proposal_schema() -> None:
    s = PLAN_PROPOSAL_SCHEMA
    assert "summary" in s["required"]
    assert "approval_checksum" in s["required"]
    assert "stage_impacts" in s["properties"]
    assert "risks" in s["properties"]


def test_repair_proposal_schema() -> None:
    s = REPAIR_PROPOSAL_SCHEMA
    assert "failure_hypothesis" in s["required"]
    assert "patch_summary" in s["required"]
    assert "affected_paths" in s["required"]


def test_reviewer_critique_schema() -> None:
    s = REVIEWER_CRITIQUE_SCHEMA
    assert s["properties"]["decision"]["enum"] == ["accept", "revise", "reject"]
    assert "reasoning" in s["required"]


def test_action_request_schema() -> None:
    s = ACTION_REQUEST_SCHEMA
    assert "action_type" in s["required"]
    assert "payload_checksum" in s["required"]
    assert s["properties"]["stage_index"]["minimum"] == 1
    assert s["properties"]["stage_index"]["maximum"] == 3


def test_assistant_answer_schema() -> None:
    s = ASSISTANT_ANSWER_SCHEMA
    assert "answer" in s["required"]
    assert "evidence_refs" in s["properties"]
    assert "follow_up_action" in s["properties"]


def test_context_pack_builder() -> None:
    pack = ContextPackBuilder.build_context_pack(
        pack_type="plan_proposal",
        title="Stage 1 plan",
        description="Plan for stage 1 with paths",
        evidence_refs=("/tmp/evidence/log.txt",),
    )
    assert pack.pack_id
    assert pack.pack_type == "plan_proposal"
    assert pack.token_budget_input == 24000
    assert pack.token_budget_output == 6000


def test_context_pack_to_dict_redacts_paths() -> None:
    pack = ContextPackBuilder.build_context_pack(
        pack_type="repair_proposal",
        title="Repair",
        description="Path: /home/user/secret/file.txt",
        evidence_refs=("/tmp/test.txt",),
    )
    d = ContextPackBuilder.pack_to_dict(pack)
    assert "redacted" in d["description"] or "/home/user" not in d["description"]


def test_schema_to_dict() -> None:
    result = ContextPackBuilder.schema_to_dict("PlanProposal")
    assert result is not None
    assert result["schema_name"] == "PlanProposal"
    assert "schema" in result


def test_schema_to_dict_unknown() -> None:
    assert ContextPackBuilder.schema_to_dict("UnknownSchema") is None


def test_token_budgets_defined() -> None:
    assert "plan_proposal" in TOKEN_BUDGETS
    assert "repair_proposal" in TOKEN_BUDGETS
    assert "reviewer_critique" in TOKEN_BUDGETS
    assert "action_request" in TOKEN_BUDGETS
    assert "assistant_answer" in TOKEN_BUDGETS


# ── Schema validation tests ─────────────────────────────────────────

from migration_factory.control_tower.application.v2_model_schemas import (
    SchemaValidator,
    SchemaValidationError,
)


class TestSchemaValidator:

    def test_validate_valid_plan_proposal(self) -> None:
        data = {
            "summary": "Migrate to Java 21",
            "stage_impacts": [
                {"stage_index": 1, "impact": "Update pom.xml"},
            ],
            "risks": ["Breaking changes"],
            "approval_checksum": "sha256:abc123",
        }
        # Should not raise
        SchemaValidator.validate("PlanProposal", data)

    def test_validate_plan_proposal_missing_required(self) -> None:
        data = {"summary": "test"}
        with pytest.raises(SchemaValidationError, match="Missing required"):
            SchemaValidator.validate("PlanProposal", data)

    def test_validate_plan_proposal_extra_field(self) -> None:
        data = {
            "summary": "test",
            "stage_impacts": [],
            "risks": [],
            "approval_checksum": "abc",
            "extra_field": "should be rejected",
        }
        with pytest.raises(SchemaValidationError, match="Unexpected property"):
            SchemaValidator.validate("PlanProposal", data)

    def test_validate_valid_repair_proposal(self) -> None:
        data = {
            "failure_hypothesis": "Null pointer in Service.java",
            "patch_summary": "Add null check",
            "affected_paths": ["src/main/java/Service.java"],
            "validation_plan": "Compile and test",
            "rollback_note": "Revert patch if fails",
        }
        SchemaValidator.validate("RepairProposal", data)

    def test_validate_repair_missing_required(self) -> None:
        data = {"failure_hypothesis": "test"}
        with pytest.raises(SchemaValidationError, match="Missing required"):
            SchemaValidator.validate("RepairProposal", data)

    def test_validate_reviewer_critique_accept(self) -> None:
        data = {"decision": "accept", "reasoning": "Looks good", "missing_evidence": [], "unsafe_assumptions": []}
        SchemaValidator.validate("ReviewerCritique", data)

    def test_validate_reviewer_critique_invalid_decision(self) -> None:
        data = {"decision": "invalid", "reasoning": "test", "missing_evidence": [], "unsafe_assumptions": []}
        with pytest.raises(SchemaValidationError, match="not one of"):
            SchemaValidator.validate("ReviewerCritique", data)

    def test_validate_action_request(self) -> None:
        data = {
            "action_type": "diagnose_failure",
            "reason": "Validate build",
            "stage_index": 2,
            "payload_checksum": "chk:def456",
        }
        SchemaValidator.validate("ActionRequest", data)

    def test_validate_action_request_invalid_stage(self) -> None:
        data = {
            "action_type": "diagnose_failure",
            "reason": "reason",
            "stage_index": 5,
            "payload_checksum": "chk",
        }
        with pytest.raises(SchemaValidationError, match="greater than maximum"):
            SchemaValidator.validate("ActionRequest", data)

    def test_validate_assistant_answer(self) -> None:
        data = {
            "answer": "Stage 1 is running",
            "evidence_refs": ["log.txt"],
            "follow_up_action": {"action_type": "review", "reason": "Check logs"},
        }
        SchemaValidator.validate("AssistantAnswer", data)

    def test_validate_unknown_schema(self) -> None:
        with pytest.raises(ValueError, match="Unknown schema"):
            SchemaValidator.validate("UnknownSchema", {})

    def test_validate_plan_proposal_wrong_type(self) -> None:
        data = {
            "summary": True,  # Should be a string
            "stage_impacts": [],
            "risks": [],
            "approval_checksum": "abc",
        }
        with pytest.raises(SchemaValidationError, match="Expected string"):
            SchemaValidator.validate("PlanProposal", data)

    def test_validate_stage_impacts_item_type(self) -> None:
        data = {
            "summary": "test",
            "stage_impacts": ["not an object"],  # invalid item type
            "risks": [],
            "approval_checksum": "abc",
        }
        with pytest.raises(SchemaValidationError, match="Expected object"):
            SchemaValidator.validate("PlanProposal", data)

    def test_validate_action_request_type_is_strings(self) -> None:
        data = {
            "action_type": "diagnose_failure",
            "reason": "Validate",
            "stage_index": 1,
            "payload_checksum": "chk",
        }
        SchemaValidator.validate("ActionRequest", data)

    def test_all_schemas_pass_with_valid_data(self) -> None:
        """Each schema should accept at least one valid data payload."""
        valid_data = {
            "PlanProposal": {
                "summary": "test",
                "stage_impacts": [{"stage_index": 1, "impact": "test"}],
                "risks": [],
                "approval_checksum": "abc",
            },
            "RepairProposal": {
                "failure_hypothesis": "test",
                "patch_summary": "test",
                "affected_paths": [],
                "validation_plan": "test",
            },
            "ReviewerCritique": {
                "decision": "accept",
                "reasoning": "test",
                "missing_evidence": [],
                "unsafe_assumptions": [],
            },
            "ActionRequest": {
                "action_type": "diagnose_failure",
                "reason": "test",
                "stage_index": 1,
                "payload_checksum": "abc",
            },
            "AssistantAnswer": {
                "answer": "test",
            },
        }
        for name, data in valid_data.items():
            SchemaValidator.validate(name, data)


# ── validate_model_output boundary tests ────────────────────────────

from migration_factory.control_tower.application.v2_model_schemas import (
    validate_model_output,
    validate_against_schema,
)


class TestValidateModelOutput:
    """Tests for validate_model_output boundary function."""

    def test_valid_model_output_passes(self) -> None:
        data = validate_model_output("PlanProposal", {
            "summary": "test",
            "stage_impacts": [{"stage_index": 1, "impact": "test"}],
            "risks": [],
            "approval_checksum": "abc",
        })
        assert data["summary"] == "test"

    def test_invalid_plan_proposal_rejected(self) -> None:
        with pytest.raises(SchemaValidationError, match="Missing required"):
            validate_model_output("PlanProposal", {"summary": "test"})

    def test_invalid_reviewer_critique_rejected(self) -> None:
        with pytest.raises(SchemaValidationError):
            validate_model_output("ReviewerCritique", {
                "decision": "destroy",  # invalid enum
                "reasoning": "none",
            })

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(SchemaValidationError, match="Unexpected property"):
            validate_model_output("AssistantAnswer", {
                "answer": "ok",
                "malicious": "delete all",
            })

    def test_wrong_type_rejected(self) -> None:
        with pytest.raises(SchemaValidationError, match="Expected string"):
            validate_model_output("PlanProposal", {
                "summary": 123,  # not a string
                "stage_impacts": [],
                "risks": [],
                "approval_checksum": "abc",
            })

    def test_returns_validated_data(self) -> None:
        original = {
            "action_type": "diagnose_failure",
            "reason": "test",
            "stage_index": 1,
            "payload_checksum": "chk",
        }
        result = validate_model_output("ActionRequest", original)
        assert result == original  # validated data is returned unchanged

    def test_unknown_schema_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown schema"):
            validate_model_output("BogusSchema", {})


def test_validate_against_schema_convenience() -> None:
    """validate_against_schema delegates to SchemaValidator."""
    validate_against_schema("AssistantAnswer", {"answer": "hello"})
    with pytest.raises(SchemaValidationError):
        validate_against_schema("AssistantAnswer", {"extra": "field"})


# ── Context pack metadata enrichment tests (F01) ────────────────────


def test_context_pack_default_metadata() -> None:
    """Old packs without metadata remain fully backward compatible."""
    pack = ContextPackBuilder.build_context_pack(
        pack_type="plan_proposal",
        title="Legacy pack",
        description="No metadata",
        evidence_refs=("/tmp/evidence.txt",),
    )
    assert pack.agent_name is None
    assert pack.event_type is None
    assert pack.stage_index is None
    assert pack.profile_id is None
    assert pack.command_id is None
    assert pack.failure_type is None
    assert pack.artifact_refs_used == ()
    assert pack.pom_summary_ref is None
    assert pack.sandbox_binding_ref is None
    assert pack.redaction_status is None


def test_context_pack_with_metadata() -> None:
    """Metadata fields are populated when provided."""
    pack = ContextPackBuilder.build_context_pack(
        pack_type="repair_proposal",
        title="Diagnosis for build_failed",
        description="Build failed in stage 1",
        evidence_refs=("/tmp/build.log",),
        agent_name="failure_diagnosis",
        event_type="build_failed",
        stage_index=1,
        profile_id="azure-proposer",
        command_id="cmd-001",
        failure_type="compilation_error",
        artifact_refs_used=("build.log", "pom.xml"),
        pom_summary_ref="pom-summary-001",
        sandbox_binding_ref="sandbox-001",
        redaction_status="redacted_3_refs",
    )
    assert pack.agent_name == "failure_diagnosis"
    assert pack.event_type == "build_failed"
    assert pack.stage_index == 1
    assert pack.profile_id == "azure-proposer"
    assert pack.command_id == "cmd-001"
    assert pack.failure_type == "compilation_error"
    assert pack.artifact_refs_used == ("build.log", "pom.xml")
    assert pack.pom_summary_ref == "pom-summary-001"
    assert pack.sandbox_binding_ref == "sandbox-001"
    assert pack.redaction_status == "redacted_3_refs"


def test_context_pack_to_dict_includes_metadata() -> None:
    """pack_to_dict includes non-empty metadata fields."""
    pack = ContextPackBuilder.build_context_pack(
        pack_type="repair_proposal",
        title="Metadata test",
        description="desc",
        evidence_refs=("/tmp/e.log",),
        agent_name="diagnosis",
        event_type="build_failed",
        stage_index=2,
    )
    d = ContextPackBuilder.pack_to_dict(pack)
    assert d["agent_name"] == "diagnosis"
    assert d["event_type"] == "build_failed"
    assert d["stage_index"] == 2
    # Fields not set should be absent from the dict
    assert "command_id" not in d
    assert "failure_type" not in d


def test_context_pack_to_dict_backward_compatible() -> None:
    """pack_to_dict without metadata omits all metadata keys."""
    pack = ContextPackBuilder.build_context_pack(
        pack_type="assistant_answer",
        title="No metadata",
        description="desc",
        evidence_refs=(),
    )
    d = ContextPackBuilder.pack_to_dict(pack)
    assert "agent_name" not in d
    assert "event_type" not in d
    assert "stage_index" not in d
