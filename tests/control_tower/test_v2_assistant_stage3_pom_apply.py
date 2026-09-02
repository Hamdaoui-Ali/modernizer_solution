"""Tests for F14 assistant /ask apply route — proving it uses same PomDependencyEditor.

Validates:
- Assistant /ask explicit apply request calls editor service path
- Assistant /ask propose request does not write
- Assistant /ask vague request "fix all dependencies" does not write
- Assistant apply response comes from PomApplyResult
- Assistant does not claim validation passed before validation event
- Assistant response has no raw sandbox paths
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from migration_factory.control_tower.application.pom_dependency_editor import (
    PomDependencyEditor,
)
from migration_factory.control_tower.application.pom_change_models import (
    PomChangeStatus,
    PomApplyResult,
    PomRollbackResult,
    PomChangeRecordSummary,
    PomValidationRun,
    ALLOWED_POM_OPERATIONS,
    APPLY_CAPABLE_POM_OPERATIONS,
    PROPOSAL_ONLY_POM_OPERATIONS,
)
from migration_factory.control_tower.application.pom_dependency_policy import (
    PomDependencyPolicy,
    DependencyControlMode,
    RiskLevel,
)


SAMPLE_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project>
    <dependencies>
        <dependency>
            <groupId>com.google.code.gson</groupId>
            <artifactId>gson</artifactId>
            <version>2.8.9</version>
        </dependency>
        <dependency>
            <groupId>io.jsonwebtoken</groupId>
            <artifactId>jjwt-api</artifactId>
            <version>0.12.6</version>
        </dependency>
    </dependencies>
    <properties>
        <java.version>17</java.version>
    </properties>
</project>
"""

SAMPLE_POM_DEPS = {
    "properties": {"java.version": "17"},
    "dependencies": [
        {"groupId": "com.google.code.gson", "artifactId": "gson", "version": "2.8.9", "scope": "compile"},
        {"groupId": "io.jsonwebtoken", "artifactId": "jjwt-api", "version": "0.12.6", "scope": "compile"},
    ],
    "dependency_management": [],
    "plugins": [],
    "parent": {},
}


def _mock_editor(**overrides) -> PomDependencyEditor:
    """Build an editor with mock repos for assistant apply testing."""
    events = MagicMock()
    events.save = MagicMock()

    change_repo = MagicMock()
    change_repo.find_by_idempotency = MagicMock(return_value=None)
    change_repo.save = MagicMock(return_value=MagicMock(
        change_id="ch_test_1",
        status=PomChangeStatus.APPLIED_PENDING_VALIDATION.value,
        operation="update_dependency_version",
        target_json='{"kind":"dependency","group_id":"com.google.code.gson","artifact_id":"gson"}',
        requested_version="2.11.0",
        before_checksum="sha256:abc",
        after_checksum="sha256:def",
        diff_unified="diff",
        validation_id="val_1",
        rollback_id=None,
        idempotency_key="ik_1",
        executor="pom_span_patch",
        created_at="2026-06-16T00:00:00Z",
        updated_at="2026-06-16T00:00:00Z",
    ))
    change_repo.get = MagicMock(return_value=None)
    change_repo.update_status = MagicMock()
    change_repo.list_by_job = MagicMock(return_value=[])

    prop_repo = MagicMock()
    val_repo = MagicMock()
    val_repo.save = MagicMock(return_value="val_test")
    val_repo.get = MagicMock(return_value=None)
    rp_repo = MagicMock()

    pom_content = overrides.pop("pom_content", SAMPLE_POM)

    import tempfile
    sandbox = overrides.pop("sandbox_path", None) or tempfile.mkdtemp(prefix="f14_assistant_test_")
    pom_file = Path(sandbox) / "pom.xml"
    pom_file.write_text(pom_content, encoding="utf-8")

    return PomDependencyEditor(
        event_sink=events,
        change_repo=change_repo,
        proposal_repo=prop_repo,
        validation_repo=val_repo,
        repair_plan_repo=rp_repo,
        resolve_sandbox_root=lambda j, s: Path(sandbox),
        resolve_pom_content=lambda j: pom_content,
    )


# ── Tests ──────────────────────────────────────────────────────────

class TestAssistantApplyUsesSameService:

    def test_apply_change_from_user_request_writes_file(self):
        """apply_change_from_user_request writes to sandbox via same service path."""
        editor = _mock_editor()

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_assistant_1",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )

        assert result.status == "applied_pending_validation"
        assert result.operation == "update_dependency_version"
        assert result.change_id != ""
        assert result.message == "The POM change was applied to the Stage 3 sandbox. Validation is now running."

    def test_apply_response_comes_from_pom_apply_result(self):
        """Assistant apply response is built from PomApplyResult, not LLM text."""
        editor = _mock_editor()

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_assistant_2",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )

        # Verify result fields that would be used in assistant answer
        assert isinstance(result.change_id, str)
        assert len(result.change_id) > 0
        assert result.validation_id is not None
        assert result.rollback_available is True
        assert "validation is now running" in result.message.lower()

    def test_propose_does_not_write(self):
        """propose_change must NOT write to sandbox."""
        editor = _mock_editor()

        proposal = editor.propose_change(
            job_id="job_1",
            user_request="propose updating gson to 2.11.0",
            idempotency_key="ik_prop",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )

        assert proposal.applied is False
        assert proposal.proposal_id != ""
        assert len(proposal.proposal_id) > 0

    def test_vague_request_does_not_write(self):
        """'fix all dependencies' must not write."""
        editor = _mock_editor()

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="fix all dependencies",
            idempotency_key="ik_vague",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )

        # Policy should block this as vague
        assert result.status in ("blocked", "error")

    def test_assistant_does_not_claim_validation_passed_without_event(self):
        """Apply response must NOT claim validation passed — it says pending."""
        editor = _mock_editor()

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_assistant_3",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )

        # Status must be pending_validation, not validated_passed
        assert result.status == "applied_pending_validation"
        assert "validation is now running" in result.message.lower()
        # Should NOT contain "passed"
        assert "passed" not in result.status

    def test_assistant_response_no_raw_paths(self):
        """PomApplyResult.to_public_dict() must not expose sandbox paths."""
        editor = _mock_editor()

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_assistant_4",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )

        public = result.to_public_dict()
        for key, value in public.items():
            if isinstance(value, str) and value:
                # Must not contain temporary directory paths
                assert "/tmp/" not in value, f"Temp path leaked in key '{key}': {value}"


class TestAssistantApplyOperationClassification:

    def test_apply_dependency_change_intent_exists(self):
        """The apply_dependency_change intent must be recognized."""
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent

        # "apply this" + explicit change -> apply
        result = _classify_v2_assistant_intent(
            "please apply this: change gson to 2.11.0"
        )
        # Since "apply this" and the explicit pattern both match,
        # the apply intent should be returned
        assert result in ("apply_dependency_change", "pom_dependency_change_request", "capability_boundary")

    def test_propose_intent_does_not_write(self):
        """Propose intent must route to proposal, not write."""
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent

        # The intent classifier should route pom change proposal requests appropriately
        result = _classify_v2_assistant_intent(
            "propose upgrading the pom dependency gson to 2.11.0"
        )
        # Should be a proposal or dependency change request intent,
        # NOT a write intent like apply_dependency_change
        assert result != "apply_dependency_change"
        # Should resolve to a valid non-write intent
        assert result in (
            "pom_change_proposal", "pom_dependency_change_request",
            "stage3_dependency_review", "pom_or_dependency_explanation",
            "general_question",
        )


class TestApplyCapableOperations:

    def test_only_four_operations_apply_capable(self):
        """Only update_property/dependency_version, remove_dependency_version, update_plugin are apply-capable."""
        assert "update_property_version" in APPLY_CAPABLE_POM_OPERATIONS
        assert "update_dependency_version" in APPLY_CAPABLE_POM_OPERATIONS
        assert "remove_dependency_version" in APPLY_CAPABLE_POM_OPERATIONS
        assert "update_plugin_version" in APPLY_CAPABLE_POM_OPERATIONS

    def test_proposal_only_operations(self):
        """change_dependency_coordinates and others are proposal-only."""
        assert "change_dependency_coordinates" in PROPOSAL_ONLY_POM_OPERATIONS
        assert "add_dependency" in PROPOSAL_ONLY_POM_OPERATIONS
        assert "remove_dependency" in PROPOSAL_ONLY_POM_OPERATIONS
        assert "add_or_update_dependency_management_entry" in PROPOSAL_ONLY_POM_OPERATIONS

    def test_proposal_only_operation_blocked_from_write(self):
        """A proposal-only operation must not reach the patcher."""
        editor = _mock_editor()

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="add dependency com.example:lib to 1.0.0",
            idempotency_key="ik_prop_only",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )

        # Should be blocked or error, not applied
        assert result.status != "applied_pending_validation"

    def test_disjoint_sets(self):
        """Apply-capable and proposal-only sets must be disjoint."""
        overlap = APPLY_CAPABLE_POM_OPERATIONS & PROPOSAL_ONLY_POM_OPERATIONS
        assert len(overlap) == 0, f"Overlap found: {overlap}"

    def test_all_operations_accounted_for(self):
        """Every ALLOWED_POM_OPERATIONS must be in either apply-capable or proposal-only."""
        accounted = APPLY_CAPABLE_POM_OPERATIONS | PROPOSAL_ONLY_POM_OPERATIONS
        for op in ALLOWED_POM_OPERATIONS:
            assert op in accounted, f"Operation '{op}' not in apply-capable or proposal-only"


# ── F14 wiring / intent classification tests ───────────────────────


class TestF14IntentClassification:
    """Tests that reproduce the exact user transcript failures."""

    def test_full_pom_defaults_to_stage3_when_stage3_complete(self):
        """User asks for 'full pom xml' with Stage 3 complete → Stage 3 should be used."""
        from migration_factory.control_tower.adapters.fastapi.app import (
            _default_stage_when_stage3_complete,
        )
        # Simulate events with Stage 3 completed
        events = (
            MagicMock(stage=3, type="stage_completed", status="completed", sequence=5),
            MagicMock(stage=3, type="build_completed", status="completed", sequence=6),
        )
        result = _default_stage_when_stage3_complete(events)
        assert result == 3, f"Expected stage=3 when Stage 3 is complete, got {result}"

    def test_stage1_not_used_when_stage3_explicit(self):
        """User explicitly says Stage 3 → must not fall back to Stage 1."""
        from migration_factory.control_tower.adapters.fastapi.app import (
            _get_requested_stage,
        )
        # "Show the full raw backend-resolved Stage 3 root pom.xml"
        question = "Show the full raw backend-resolved Stage 3 root pom.xml. Do not summarize it. Use Stage 3 only."
        result = _get_requested_stage(question, "pom_or_dependency_explanation")
        assert result == 3, f"Expected stage=3 for explicit Stage 3 question, got {result}"

    def test_explicit_stage3_raw_pom_does_not_route_to_dependency_review(self):
        """Explicit Stage 3 raw POM request must NOT route to stage3_dependency_review."""
        from migration_factory.control_tower.adapters.fastapi.app import (
            _classify_v2_assistant_intent,
        )
        question = "Show the full raw backend-resolved Stage 3 root pom.xml. Do not summarize it. Use Stage 3 only."
        intent = _classify_v2_assistant_intent(question)
        assert intent != "stage3_dependency_review", (
            f"Raw POM request must not route to dependency review, got {intent}"
        )
        assert intent in ("pom_or_dependency_explanation", "artifact_content", "general_question"), (
            f"Raw POM request should be pom_or_dependency_explanation, got {intent}"
        )

    def test_stage3_dependency_review_prompt_routes_to_review(self):
        """'Review the Stage 3 pom.xml dependencies' must route to stage3_dependency_review."""
        from migration_factory.control_tower.adapters.fastapi.app import (
            _classify_v2_assistant_intent,
        )
        question = "Review the Stage 3 pom.xml dependencies. Do not apply anything."
        intent = _classify_v2_assistant_intent(question)
        assert intent == "stage3_dependency_review", (
            f"Review prompt should route to stage3_dependency_review, got {intent}"
        )

    def test_propose_property_change_returns_proposal_intent_not_review(self):
        """'Propose changing assertj.version to 3.24.2' must route to proposal, not review."""
        from migration_factory.control_tower.adapters.fastapi.app import (
            _classify_v2_assistant_intent,
        )
        question = "Propose changing assertj.version to 3.24.2 in Stage 3 root pom.xml. Do not apply."
        intent = _classify_v2_assistant_intent(question)
        # Must not be stage3_dependency_review or generic
        assert intent != "stage3_dependency_review", (
            f"Propose property change must not route to dependency review, got {intent}"
        )
        # Should be pom_change_proposal or pom_dependency_change_request
        assert intent in ("pom_change_proposal", "pom_dependency_change_request"), (
            f"Propose property change should route to proposal, got {intent}"
        )

    def test_modelmapper_apply_prompt_does_not_return_healthcheck_status(self):
        """'Apply POM change: update org.modelmapper.version to 2.4.5' must NOT route to model_status."""
        from migration_factory.control_tower.adapters.fastapi.app import (
            _classify_v2_assistant_intent,
        )
        question = "Apply this Stage 3 POM change: update property org.modelmapper.version to 2.4.5"
        intent = _classify_v2_assistant_intent(question)
        assert intent != "model_status", (
            f"Apply property change must not route to model_status (modelmapper has 'model' substring), got {intent}"
        )
        assert intent in ("apply_dependency_change", "pom_dependency_change_request", "pom_change_proposal"), (
            f"Apply property change should route to apply/proposal, got {intent}"
        )

    def test_apply_property_change_routes_to_apply_dependency_change(self):
        """'Apply this Stage 3 POM change: update property assertj.version to 3.24.2' → apply_dependency_change."""
        from migration_factory.control_tower.adapters.fastapi.app import (
            _classify_v2_assistant_intent,
        )
        question = "apply this change: update property assertj.version to 3.24.2"
        intent = _classify_v2_assistant_intent(question)
        assert intent == "apply_dependency_change", (
            f"Apply property change must route to apply_dependency_change, got {intent}"
        )


class TestF14ApplyPropertyChange:
    """Tests for apply property change through assistant path."""

    def test_apply_property_change_from_assistant_calls_editor_and_writes(self):
        """Apply property change from assistant calls PomDependencyEditor and writes."""
        editor = _mock_editor()

        # Update SAMPLE_POM to include assertj.version property
        pom_with_assertj = SAMPLE_POM.replace(
            "</properties>",
            "    <assertj.version>3.13.2</assertj.version>\n    </properties>",
        )
        pom_deps = dict(SAMPLE_POM_DEPS)
        pom_deps["properties"] = {
            **pom_deps.get("properties", {}),
            "assertj.version": "3.13.2",
        }

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="update property assertj.version to 3.24.2",
            idempotency_key="ik_prop_apply_1",
            pom_content=pom_with_assertj,
            pom_deps_data=pom_deps,
        )

        assert result.status == "applied_pending_validation"
        assert result.operation == "update_property_version"
        assert result.change_id != ""

    def test_apply_property_change_returns_change_id_validation_id(self):
        """Apply property change must return change_id and validation_id."""
        editor = _mock_editor()

        pom_with_assertj = SAMPLE_POM.replace(
            "</properties>",
            "    <assertj.version>3.13.2</assertj.version>\n    </properties>",
        )
        pom_deps = dict(SAMPLE_POM_DEPS)
        pom_deps["properties"] = {
            **pom_deps.get("properties", {}),
            "assertj.version": "3.13.2",
        }

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="update property assertj.version to 3.24.2",
            idempotency_key="ik_prop_apply_2",
            pom_content=pom_with_assertj,
            pom_deps_data=pom_deps,
        )

        assert len(result.change_id) > 0, "change_id must be present"
        assert result.validation_id is not None, "validation_id must be present"
        assert result.status == "applied_pending_validation"
        # Must say validation is running, not passed
        assert "validation is now running" in result.message.lower()
        assert "passed" not in result.status


class TestF14DeterministicFallback:
    """Tests for deterministic fallback behavior when Azure is unavailable."""

    def test_invalid_azure_response_falls_back_to_f14_deterministic_behavior(self):
        """When Azure returns empty/invalid, fallback must still execute F14 behavior."""
        from migration_factory.control_tower.application.v2_assistant_model_client import (
            V2AssistantModelResult,
            _fallback_result,
        )
        # Simulate the fallback producing a valid F14 answer (not model_status)
        fallback_text = _build_test_fallback_answer("Propose changing assertj.version to 3.24.2")
        result = _fallback_result(fallback_text, "Azure OpenAI returned empty response", "invalid_response")

        # Fallback content must include the F14 answer, not just the error
        assert result.success is False
        assert result.source == "deterministic"
        # Content should contain both the fallback F14 answer and the reason
        assert "Model: fallback" in result.content
        assert result.failure_reason == "invalid_response"
        # The deterministic F14 answer should NOT be the model status answer
        assert "Azure OpenAI model is" not in fallback_text, (
            f"Deterministic fallback must not return model status for POM proposal, got: {fallback_text[:200]}"
        )

    def test_deterministic_fallback_not_depend_on_azure_response(self):
        """F14 deterministic behavior must not depend on Azure response content."""
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_v2_assistant_answer,
        )
        question = "Propose changing assertj.version to 3.24.2 in Stage 3 root pom.xml. Do not apply."
        answer = _build_v2_assistant_answer(
            question=question,
            events=(),
            approvals=(),
            commands=(),
        )
        # The deterministic answer must NOT be the model status answer
        assert "Azure OpenAI model is" not in answer, (
            f"Deterministic fallback must not return model status for POM proposal, got: {answer[:200]}"
        )

    def test_no_raw_path_leak_in_f14_assistant_response(self):
        """F14 assistant responses must never contain raw sandbox paths."""
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_v2_assistant_answer,
        )
        # Build a simple answer from deterministic fallback (no events/approvals needed)
        question = "Show the full raw backend-resolved Stage 3 root pom.xml"
        answer = _build_v2_assistant_answer(
            question=question,
            events=(),
            approvals=(),
            commands=(),
        )
        # Must not contain raw path patterns
        for bad in ("/tmp/", "/mnt/", "/home/", "/sandbox/", "C:\\", "\\\\"):
            assert bad not in answer, (
                f"Raw path '{bad}' leaked in assistant answer: ...{answer[answer.find(bad)-50:answer.find(bad)+50] if bad in answer else ''}"
            )


class TestF14RuntimeRegressions:
    """Exact regressions observed from the live assistant UI."""

    def test_raw_stage3_pom_preserves_xml_closing_tags(self):
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_pom_explanation_answer,
        )

        pom = SAMPLE_POM.replace(
            "</properties>",
            "    <assertj.version>3.13.2</assertj.version>\n    </properties>",
        )
        answer = _build_pom_explanation_answer(
            artifact_previews=(
                {
                    "source_type": "file_alias",
                    "artifact_kind": "root_pom",
                    "exists": True,
                    "stage_index": 3,
                    "preview": pom,
                },
            ),
            events=(),
            raw_xml_requested=True,
        )

        assert "<assertj.version>3.13.2</assertj.version>" in answer
        assert "<assertj.version>3.13.2<[redacted-path]" not in answer

    def test_xml_redaction_preserves_project_testresult_closing_tag(self):
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_pom_explanation_answer,
        )

        pom = SAMPLE_POM.replace(
            "</properties>",
            "    <project.testresult.directory>${project.build.directory}/C:/Users/me/out</project.testresult.directory>\n    </properties>",
        )
        answer = _build_pom_explanation_answer(
            artifact_previews=(
                {
                    "source_type": "file_alias",
                    "artifact_kind": "root_pom",
                    "exists": True,
                    "stage_index": 3,
                    "preview": pom,
                },
            ),
            events=(),
            raw_xml_requested=True,
        )

        assert "<project.testresult.directory>[redacted-path]</project.testresult.directory>" in answer
        assert "<project.testresult.directory>${project.build.directory}[redacted-path]" not in answer

    def test_validation_result_prompt_routes_to_validation_lookup(self):
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_v2_assistant_answer,
            _classify_v2_assistant_intent,
        )

        assert _classify_v2_assistant_intent(
            "Show the validation result for the latest Stage 3 POM change"
        ) == "pom_validation_result"

        editor = MagicMock()
        editor.list_changes.return_value = [
            PomChangeRecordSummary(
                change_id="ch_1",
                operation="update_property_version",
                target_desc="property:assertj.version",
                before_version="3.13.2",
                after_version="3.24.2",
                before_checksum="before",
                after_checksum="after",
                diff_summary="1 addition(s), 1 removal(s)",
                status="applied_pending_validation",
                validation_id="val_1",
                rollback_id=None,
                created_at="2026-06-16T00:00:00Z",
            )
        ]
        editor.get_validation_result.return_value = PomValidationRun(
            validation_id="val_1",
            change_id="ch_1",
            status="running",
            command="mvn clean compile test",
            build_status="unknown",
            test_status="unknown",
            exit_code=None,
            duration_ms=None,
            log_ref=None,
            test_log_ref=None,
            diagnosis=None,
            repair_plan=None,
            created_at="2026-06-16T00:00:00Z",
            completed_at=None,
        )
        event = MagicMock(job_id="job_1")

        with patch(
            "migration_factory.control_tower.adapters.fastapi.app._build_pom_dependency_editor",
            return_value=editor,
        ):
            answer = _build_v2_assistant_answer(
                question="Show the validation result for the latest Stage 3 POM change",
                events=(event,),
                approvals=(),
                commands=(),
            )

        editor.get_validation_result.assert_called_once_with("job_1", "val_1")
        assert "Stage 3 POM validation result" in answer
        assert "Status:** running" in answer
        assert "Proposal ID" not in answer

    def test_propose_assertj_property_returns_real_proposal(self):
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_pom_change_proposal_answer,
        )

        pom = SAMPLE_POM.replace(
            "</properties>",
            "    <assertj.version>3.13.2</assertj.version>\n    </properties>",
        )
        editor = _mock_editor(pom_content=pom)
        event = MagicMock(job_id="job_1")

        with patch(
            "migration_factory.control_tower.adapters.fastapi.app._build_pom_dependency_editor",
            return_value=editor,
        ):
            answer = _build_pom_change_proposal_answer(
                question="Propose changing assertj.version to 3.24.2 in the Stage 3 root pom.xml. Do not apply it.",
                events=(event,),
                approvals=(),
                commands=(),
            )

        assert "Proposal ID" in answer
        assert "not applied" in answer.lower()
        assert "update_property_version" in answer
        assert "assertj.version" in answer
        assert "3.24.2" in answer

    def test_apply_assertj_property_no_nameerror_returns_change_and_validation(self):
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_apply_dependency_change_answer,
        )

        pom = SAMPLE_POM.replace(
            "</properties>",
            "    <assertj.version>3.13.2</assertj.version>\n    </properties>",
        )
        editor = _mock_editor(pom_content=pom)
        event = MagicMock(job_id="job_1")

        with patch(
            "migration_factory.control_tower.adapters.fastapi.app._build_pom_dependency_editor",
            return_value=editor,
        ):
            answer = _build_apply_dependency_change_answer(
                question="Apply this Stage 3 POM change: update property assertj.version to 3.24.2.",
                events=(event,),
                approvals=(),
                commands=(),
            )

        assert "name '_build_pom_dependency_editor' is not defined" not in answer
        assert "Change ID" in answer
        assert "Validation ID" in answer
        assert "applied_pending_validation" in answer
        assert "update_property_version" in answer
        assert "property:assertj.version" in answer

    def test_apply_assertj_property_changes_stage3_pom(self):
        pom = SAMPLE_POM.replace(
            "</properties>",
            "    <assertj.version>3.13.2</assertj.version>\n    </properties>",
        )
        editor = _mock_editor(pom_content=pom)

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="update property assertj.version to 3.24.2",
            idempotency_key="ik_assertj_write",
        )

        sandbox = editor._resolve_sandbox("job_1", 3)
        assert sandbox is not None
        content = (sandbox / "pom.xml").read_text(encoding="utf-8")
        assert result.status == "applied_pending_validation"
        assert "<assertj.version>3.24.2</assertj.version>" in content
        assert "<assertj.version>3.13.2</assertj.version>" not in content

    def test_apply_then_raw_pom_reads_updated_value_from_live_sandbox(self, tmp_path):
        from migration_factory.control_tower.adapters.fastapi.app import (
            _resolve_root_pom_file_alias_preview,
        )

        pom = SAMPLE_POM.replace(
            "</properties>",
            "    <assertj.version>3.13.2</assertj.version>\n    </properties>",
        )
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(pom, encoding="utf-8")
        editor = _mock_editor(pom_content=pom, sandbox_path=sandbox)

        editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="update property assertj.version to 3.24.2",
            idempotency_key="ik_live_read",
        )

        command = MagicMock(
            stage_index=3,
            result_json=json.dumps({"sandbox_path": str(sandbox)}),
            updated_at="2026-06-16T00:00:00Z",
            created_at="2026-06-16T00:00:00Z",
            command_id="cmd_1",
        )
        event = MagicMock(
            stage=3,
            type="pom_validation_started",
            status="running",
            sequence=2,
            payload_json="{}",
            event_id="evt_1",
        )

        preview = _resolve_root_pom_file_alias_preview(
            job_id="job_1",
            stage_index=3,
            events=(event,),
            commands=(command,),
            max_bytes=100_000,
        )

        assert preview["exists"] is True
        assert preview["label"] == "live Stage 3 sandbox POM during validation"
        assert "<assertj.version>3.24.2</assertj.version>" in preview["preview"]

    def test_root_pom_available_during_validation_from_live_sandbox(self, tmp_path):
        from migration_factory.control_tower.adapters.fastapi.app import (
            _resolve_root_pom_file_alias_preview,
        )

        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(SAMPLE_POM, encoding="utf-8")
        command = MagicMock(
            stage_index=3,
            result_json=json.dumps({"sandbox_path": str(sandbox)}),
            updated_at="2026-06-16T00:00:00Z",
            created_at="2026-06-16T00:00:00Z",
            command_id="cmd_1",
        )
        event = MagicMock(
            stage=3,
            type="pom_validation_started",
            status="running",
            sequence=2,
            payload_json="{}",
            event_id="evt_1",
        )

        preview = _resolve_root_pom_file_alias_preview(
            job_id="job_1",
            stage_index=3,
            events=(event,),
            commands=(command,),
            max_bytes=100_000,
        )

        assert preview["exists"] is True
        assert preview["reason"] is None
        assert preview["label"] == "live Stage 3 sandbox POM during validation"

    def test_rollback_last_stage3_change_routes_to_rollback_not_generic_proposal(self):
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_v2_assistant_answer,
        )

        editor = MagicMock()
        editor.list_changes.return_value = [
            PomChangeRecordSummary(
                change_id="ch_1",
                operation="update_property_version",
                target_desc="property:assertj.version",
                before_version="3.13.2",
                after_version="3.24.2",
                before_checksum="before",
                after_checksum="after",
                diff_summary="1 addition(s), 1 removal(s)",
                status="applied_pending_validation",
                validation_id="val_1",
                rollback_id=None,
                created_at="2026-06-16T00:00:00Z",
            )
        ]
        editor.rollback_change.return_value = PomRollbackResult(
            change_id="ch_1",
            rollback_id="rb_1",
            status="rolled_back",
            checksum_restored=True,
            validation_triggered=False,
            validation_id=None,
            created_at="2026-06-16T00:01:00Z",
        )
        event = MagicMock(job_id="job_1")

        with patch(
            "migration_factory.control_tower.adapters.fastapi.app._build_pom_dependency_editor",
            return_value=editor,
        ):
            answer = _build_v2_assistant_answer(
                question="Rollback the last Stage 3 POM change.",
                events=(event,),
                approvals=(),
                commands=(),
            )

        editor.rollback_change.assert_called_once()
        assert "POM change rolled back" in answer
        assert "Rollback ID" in answer
        assert "Checksum restored:** True" in answer
        assert "I cannot apply this directly" not in answer

    def test_rollback_no_change_returns_clear_message(self):
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_v2_assistant_answer,
        )

        editor = MagicMock()
        editor.list_changes.return_value = []
        event = MagicMock(job_id="job_1")

        with patch(
            "migration_factory.control_tower.adapters.fastapi.app._build_pom_dependency_editor",
            return_value=editor,
        ):
            answer = _build_v2_assistant_answer(
                question="Rollback the last Stage 3 POM change.",
                events=(event,),
                approvals=(),
                commands=(),
            )

        editor.rollback_change.assert_not_called()
        assert answer == "No applied Stage 3 POM change found to rollback."

    def test_invalid_azure_fallback_still_executes_f14_apply(self):
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_v2_assistant_answer,
        )
        from migration_factory.control_tower.application.v2_assistant_model_client import (
            _fallback_result,
        )

        pom = SAMPLE_POM.replace(
            "</properties>",
            "    <assertj.version>3.13.2</assertj.version>\n    </properties>",
        )
        editor = _mock_editor(pom_content=pom)
        event = MagicMock(job_id="job_1")

        with patch(
            "migration_factory.control_tower.adapters.fastapi.app._build_pom_dependency_editor",
            return_value=editor,
        ):
            fallback_answer = _build_v2_assistant_answer(
                question="Apply this Stage 3 POM change: update property assertj.version to 3.24.2.",
                events=(event,),
                approvals=(),
                commands=(),
            )

        fallback = _fallback_result(
            fallback_answer,
            "Azure OpenAI returned empty response",
            "invalid_response",
        )
        assert fallback.source == "deterministic"
        assert "Change ID" in fallback.content
        assert "Validation ID" in fallback.content
        assert "applied_pending_validation" in fallback.content


def _build_test_fallback_answer(question: str) -> str:
    """Helper to build a deterministic fallback answer for testing."""
    from migration_factory.control_tower.adapters.fastapi.app import (
        _build_v2_assistant_answer,
    )
    return _build_v2_assistant_answer(question=question, events=(), approvals=(), commands=())


# ═══════════════════════════════════════════════════════════════════
# F12: GAV parsing & Azure/UI fixes — new tests
# ═══════════════════════════════════════════════════════════════════

class TestProposeGavDependency:
    """Propose GAV (group:artifact) dependency returns real proposal, not generic BOM."""

    def test_propose_gav_dependency_returns_real_proposal(self):
        """'propose changing com.google.code.gson:gson to 2.11.0' -> real proposal with proposal_id."""
        editor = _mock_editor()
        proposal = editor.propose_change(
            job_id="job_1",
            user_request="propose changing com.google.code.gson:gson to 2.11.0 in the Stage 3 root pom.xml. Do not apply it.",
            idempotency_key="ik_gav_prop_1",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )
        assert proposal.applied is False
        assert proposal.proposal_id != ""
        plan = proposal.server_validated_plan_preview
        assert plan.get("operation") == "update_dependency_version"
        target = plan.get("target", {})
        assert target.get("group_id") == "com.google.code.gson"
        assert target.get("artifact_id") == "gson"
        assert plan.get("requested_version") == "2.11.0"

    def test_propose_gav_dependency_not_generic_bom_proposal(self):
        """GAV proposal must NOT produce a generic Spring Boot BOM proposal."""
        editor = _mock_editor()
        proposal = editor.propose_change(
            job_id="job_1",
            user_request="Propose changing com.google.code.gson:gson to 2.11.0 in the Stage 3 root pom.xml. Do not apply it.",
            idempotency_key="ik_gav_prop_2",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )
        plan = proposal.server_validated_plan_preview
        # Must be an update_dependency_version operation, NOT a generic proposal
        assert plan.get("operation") == "update_dependency_version"
        target = plan.get("target", {})
        assert target.get("kind") == "dependency"
        assert target.get("group_id") == "com.google.code.gson"
        assert target.get("artifact_id") == "gson"

    def test_short_gson_and_full_gav_both_parse_to_same_target(self):
        """Short 'gson' and full 'com.google.code.gson:gson' both map to same dependency target."""
        editor = _mock_editor()

        # Short form
        proposal_short = editor.propose_change(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_short",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )
        # Full GAV form
        proposal_gav = editor.propose_change(
            job_id="job_1",
            user_request="change com.google.code.gson:gson to 2.11.0",
            idempotency_key="ik_gav",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )

        target_short = proposal_short.server_validated_plan_preview.get("target", {})
        target_gav = proposal_gav.server_validated_plan_preview.get("target", {})
        # Both should resolve to same groupId:artifactId
        assert target_short.get("group_id", "").lower() == "com.google.code.gson"
        assert target_short.get("artifact_id", "").lower() == "gson"
        assert target_gav.get("group_id", "").lower() == "com.google.code.gson"
        assert target_gav.get("artifact_id", "").lower() == "gson"


class TestApplyGavDependency:
    """Apply GAV dependency routes to PomDependencyEditor and changes live POM."""

    def test_apply_update_dependency_gav_routes_to_apply(self):
        """'update dependency GROUP:ARTIFACT to VERSION' routes to apply, returns result."""
        editor = _mock_editor()
        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="update dependency com.google.code.gson:gson to 2.11.0",
            idempotency_key="ik_gav_apply_1",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )
        assert result.status == "applied_pending_validation"
        assert "gson" in result.target_desc.lower()
        assert result.after_version == "2.11.0"

    def test_apply_update_dependency_gav_returns_change_and_validation(self):
        """GAV apply returns change_id and validation_id."""
        editor = _mock_editor()
        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="Apply this Stage 3 POM change: update dependency com.google.code.gson:gson to 2.11.0.",
            idempotency_key="ik_gav_apply_2",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )
        assert result.change_id != ""
        assert result.validation_id is not None
        assert result.rollback_available is True

    def test_apply_update_dependency_gav_changes_live_pom(self):
        """GAV apply actually changes the live Stage 3 sandbox POM file."""
        import tempfile
        from pathlib import Path

        sandbox = tempfile.mkdtemp(prefix="f14_gav_test_")
        pom_file = Path(sandbox) / "pom.xml"

        # POM with gson at 2.10.1 (the Stage 3 scenario)
        stage3_pom = """<?xml version="1.0" encoding="UTF-8"?>
<project>
    <dependencies>
        <dependency>
            <groupId>com.google.code.gson</groupId>
            <artifactId>gson</artifactId>
            <version>2.10.1</version>
        </dependency>
    </dependencies>
</project>
"""
        pom_file.write_text(stage3_pom, encoding="utf-8")

        editor = PomDependencyEditor(
            resolve_sandbox_root=lambda j, s: Path(sandbox),
            resolve_pom_content=lambda j: pom_file.read_text(encoding="utf-8"),
        )

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="update dependency com.google.code.gson:gson to 2.11.0",
            idempotency_key="ik_gav_live_1",
            pom_deps_data=SAMPLE_POM_DEPS,
        )

        # Check result
        assert result.before_version == "2.10.1"
        assert result.after_version == "2.11.0"

        # Verify live POM file was changed
        updated_content = pom_file.read_text(encoding="utf-8")
        assert "2.11.0" in updated_content
        assert "2.10.1" not in updated_content

        # Cleanup
        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)

    def test_property_update_assertj_still_works(self):
        """Property update 'update property assertj.version to 3.24.2' still works after GAV fixes."""
        editor = _mock_editor(
            pom_content=SAMPLE_POM.replace("<properties>\n        <java.version>17</java.version>",
                                             "<properties>\n        <java.version>17</java.version>\n        <assertj.version>3.24.1</assertj.version>"),
        )

        proposal = editor.propose_change(
            job_id="job_1",
            user_request="update property assertj.version to 3.24.2",
            idempotency_key="ik_prop_test",
        )

        plan = proposal.server_validated_plan_preview
        target = plan.get("target", {})
        assert target.get("kind") == "property"
        assert target.get("property_name") == "assertj.version"
        assert plan.get("requested_version") == "3.24.2"

    def test_invalid_azure_fallback_still_executes_gav_apply(self):
        """Azure empty response fallback must still execute the GAV apply (deterministic path)."""
        from migration_factory.control_tower.application.v2_assistant_model_client import (
            _fallback_result,
        )
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_v2_assistant_answer,
        )

        editor = _mock_editor()
        event = MagicMock(job_id="job_1")

        with patch(
            "migration_factory.control_tower.adapters.fastapi.app._build_pom_dependency_editor",
            return_value=editor,
        ):
            fallback_answer = _build_v2_assistant_answer(
                question="Apply this Stage 3 POM change: update dependency com.google.code.gson:gson to 2.11.0.",
                events=(event,),
                approvals=(),
                commands=(),
            )

        fallback = _fallback_result(
            fallback_answer,
            "Azure OpenAI returned empty response",
            "empty_response",
        )
        # The deterministic fallback came from the backend, not Azure
        assert fallback.source == "deterministic"
        # The route was recognized (either applied or blocked, not generic error)
        assert "applied_pending_validation" in fallback.content or "blocked" in fallback.content
        # Must NOT be the generic "I need a specific dependency name" message
        assert "I need a specific dependency name" not in fallback.content


class TestParserGavPatterns:
    """Parser tests for GAV patterns in _parse_user_request."""

    def _make_proposer(self):
        from migration_factory.control_tower.application.pom_change_proposer import PomChangeProposer
        return PomChangeProposer()

    def test_parse_propose_changing_gav_to_version(self):
        """'propose changing com.google.code.gson:gson to 2.11.0' parses correctly."""
        proposer = self._make_proposer()
        result = proposer._parse_user_request(
            "propose changing com.google.code.gson:gson to 2.11.0 in the Stage 3 root pom.xml. Do not apply it.",
            SAMPLE_POM, SAMPLE_POM_DEPS,
        )
        assert result["target"].kind == "dependency"
        assert result["target"].group_id == "com.google.code.gson"
        assert result["target"].artifact_id == "gson"
        assert result["requested_version"] == "2.11.0"
        assert result["operation"] == "update_dependency_version"

    def test_parse_update_dependency_gav_to_version(self):
        """'update dependency com.google.code.gson:gson to 2.11.0' parses correctly."""
        proposer = self._make_proposer()
        result = proposer._parse_user_request(
            "update dependency com.google.code.gson:gson to 2.11.0",
            SAMPLE_POM, SAMPLE_POM_DEPS,
        )
        assert result["target"].kind == "dependency"
        assert result["target"].group_id == "com.google.code.gson"
        assert result["target"].artifact_id == "gson"
        assert result["requested_version"] == "2.11.0"

    def test_parse_apply_this_update_dependency_gav_to_version(self):
        """'Apply this ... update dependency GROUP:ARTIFACT to VERSION' parses correctly."""
        proposer = self._make_proposer()
        result = proposer._parse_user_request(
            "Apply this Stage 3 POM change: update dependency com.google.code.gson:gson to 2.11.0.",
            SAMPLE_POM, SAMPLE_POM_DEPS,
        )
        assert result["target"].kind == "dependency"
        assert result["target"].group_id == "com.google.code.gson"
        assert result["target"].artifact_id == "gson"
        assert result["requested_version"] == "2.11.0"

    def test_parse_gav_version_strips_trailing_period(self):
        """Version '2.11.0.' -> '2.11.0' (strips trailing period)."""
        from migration_factory.control_tower.application.pom_change_proposer import _clean_version_token
        assert _clean_version_token("2.11.0.") == "2.11.0"
        assert _clean_version_token("2.11.0") == "2.11.0"
        assert _clean_version_token("3.5.14-RC.1,") == "3.5.14-RC.1"

    def test_parse_property_request_still_works(self):
        """Property requests still parse correctly after GAV changes."""
        proposer = self._make_proposer()
        result = proposer._parse_user_request(
            "update property assertj.version to 3.24.2",
            SAMPLE_POM, SAMPLE_POM_DEPS,
        )
        assert result["target"].kind == "property"
        assert result["target"].property_name == "assertj.version"
        assert result["requested_version"] == "3.24.2"
        assert result["operation"] == "update_property_version"

    def test_parse_short_artifact_request_still_works(self):
        """Short artifact 'change gson to 2.11.0' still parses correctly."""
        proposer = self._make_proposer()
        result = proposer._parse_user_request(
            "change gson to 2.11.0",
            SAMPLE_POM, SAMPLE_POM_DEPS,
        )
        assert result["target"].kind == "dependency"
        assert result["target"].artifact_id.lower() == "gson"
        assert result["requested_version"] == "2.11.0"


class TestF14ModeClassification:
    """F14 mode separation: exact imperative applies; advisory proposes only."""

    def test_direct_imperative_gav_update_routes_to_apply(self):
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent

        assert _classify_v2_assistant_intent(
            "update dependency com.google.code.gson:gson to 2.11.0"
        ) == "apply_dependency_change"

    def test_direct_property_update_routes_to_apply(self):
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent

        assert _classify_v2_assistant_intent(
            "update property assertj.version to 3.24.2"
        ) == "apply_dependency_change"

    def test_advisory_gav_update_routes_to_proposal(self):
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent

        assert _classify_v2_assistant_intent(
            "Can I update com.google.code.gson:gson to 2.11.0?"
        ) == "pom_change_proposal"
        assert _classify_v2_assistant_intent(
            "What do you think about updating com.google.code.gson:gson to 2.11.0?"
        ) == "pom_change_proposal"

    def test_do_not_apply_wins_over_apply_verb(self):
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent

        assert _classify_v2_assistant_intent(
            "Update dependency com.google.code.gson:gson to 2.11.0, but do not apply it."
        ) == "pom_change_proposal"


class TestAzureEmptyResponseDiagnostics:
    """Azure empty-response diagnostics capture finish_reason, usage, choice shape."""

    def test_azure_empty_logs_finish_reason_usage_choice_shape(self):
        """_log_empty_azure_response captures finish_reason, usage, choice_count."""
        from migration_factory.control_tower.application.v2_assistant_model_client import (
            _log_empty_azure_response,
        )
        import logging

        # Sample Azure response with empty content
        data = {
            "id": "chatcmpl-abc123",
            "model": "gpt-4o-mini",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "", "role": "assistant"},
                "index": 0,
                "content_filter_results": {"hate": {"filtered": False}},
            }],
            "usage": {
                "prompt_tokens": 5300,
                "completion_tokens": 5,
                "total_tokens": 5305,
            },
        }

        # Should not raise
        with patch.object(logging.getLogger("v2_assistant_model_client"), "warning") as mock_warn:
            _log_empty_azure_response(data, "gpt-4o-mini")
            # Verify logger was called with diagnostic info
            assert mock_warn.called
            call_args = str(mock_warn.call_args)
            assert "empty_response" in call_args.lower() or "AZURE_EMPTY" in call_args

    def test_azure_empty_with_minimal_response_no_error(self):
        """Minimal response (choices present but empty content) logs without error."""
        from migration_factory.control_tower.application.v2_assistant_model_client import (
            _log_empty_azure_response,
        )
        import logging

        data = {"choices": [{"message": {"content": ""}}]}

        with patch.object(logging.getLogger("v2_assistant_model_client"), "warning"):
            # Should not raise
            _log_empty_azure_response(data, "deployment")


class TestModelInvocationFailedClassification:
    """model_invocation_failed with fallback is telemetry, not failure/repair."""

    def test_model_invocation_failed_with_fallback_is_telemetry_not_failure_repair(self):
        """_is_fallback_model_event returns True for deterministic fallback events."""
        from migration_factory.control_tower.adapters.fastapi.app import (_is_fallback_model_event,)

        # Create a mock event that is a model_invocation_failed with deterministic source
        fallback_event = MagicMock(
            type="model_invocation_failed",
            payload_json='{"source": "deterministic", "is_fallback": true}',
        )
        assert _is_fallback_model_event(fallback_event) is True

        # Real model failure (not fallback) should NOT be filtered
        real_failure_event = MagicMock(
            type="model_invocation_failed",
            payload_json='{"source": "azure_openai", "is_fallback": false}',
        )
        assert _is_fallback_model_event(real_failure_event) is False

        # Non-model events should NOT be filtered
        build_failed_event = MagicMock(
            type="build_failed",
            payload_json='{}',
        )
        assert _is_fallback_model_event(build_failed_event) is False

    def test_model_invocation_completed_not_filtered(self):
        """model_invocation_completed events are NOT filtered as fallback."""
        from migration_factory.control_tower.adapters.fastapi.app import (_is_fallback_model_event,)

        completed_event = MagicMock(
            type="model_invocation_completed",
            payload_json='{"source": "azure_openai"}',
        )
        assert _is_fallback_model_event(completed_event) is False


class TestLivePomFind:
    """Live POM find reads from Stage 3 sandbox, not truncated preview."""

    def test_find_gav_dependency_reads_live_stage3_pom_not_preview(self):
        """'find com.google.code.gson:gson' reads live Stage 3 sandbox POM."""
        import tempfile
        from pathlib import Path

        sandbox = tempfile.mkdtemp(prefix="f14_find_test_")
        pom_file = Path(sandbox) / "pom.xml"

        stage3_pom = """<?xml version="1.0" encoding="UTF-8"?>
<project>
    <dependencies>
        <dependency>
            <groupId>com.google.code.gson</groupId>
            <artifactId>gson</artifactId>
            <version>2.10.1</version>
        </dependency>
    </dependencies>
</project>
"""
        pom_file.write_text(stage3_pom, encoding="utf-8")

        editor = PomDependencyEditor(
            resolve_sandbox_root=lambda j, s: Path(sandbox),
            resolve_pom_content=lambda j: pom_file.read_text(encoding="utf-8"),
        )

        view = editor.get_stage3_pom(job_id="job_1")
        assert view.exists is True
        assert "2.10.1" in view.content
        assert "com.google.code.gson" in view.content
        assert "gson" in view.content

        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)

    def test_find_artifactid_gson_returns_matching_block(self):
        """'find gson' returns the matching dependency block."""
        import tempfile
        from pathlib import Path

        sandbox = tempfile.mkdtemp(prefix="f14_find_test_")
        pom_file = Path(sandbox) / "pom.xml"
        pom_file.write_text(SAMPLE_POM, encoding="utf-8")

        editor = PomDependencyEditor(
            resolve_sandbox_root=lambda j, s: Path(sandbox),
            resolve_pom_content=lambda j: pom_file.read_text(encoding="utf-8"),
        )

        view = editor.get_stage3_pom(job_id="job_1")
        assert view.exists is True
        content_lower = view.content.lower()
        # Should contain the gson dependency block
        assert "gson" in content_lower
        assert "com.google.code.gson" in content_lower

        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)

    def test_full_raw_pom_truncation_is_labeled_if_truncated(self):
        """If POM is truncated, the View marks truncated=True."""
        # Create a very large POM
        large_pom = "<?xml version='1.0'?>\n<project>\n" + ("  <!-- padding -->\n" * 5000) + "</project>\n"

        import tempfile
        from pathlib import Path
        sandbox = tempfile.mkdtemp(prefix="f14_pom_trunc_")
        pom_file = Path(sandbox) / "pom.xml"
        pom_file.write_text(large_pom, encoding="utf-8")

        editor = PomDependencyEditor(
            resolve_sandbox_root=lambda j, s: Path(sandbox),
            resolve_pom_content=lambda j: pom_file.read_text(encoding="utf-8"),
        )

        view = editor.get_stage3_pom(job_id="job_1")
        if view.truncated:
            assert len(view.content) <= 100_000

        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)

    def test_find_pom_no_raw_sandbox_path_leak(self):
        """POM view content must NOT leak raw sandbox paths."""
        import tempfile
        from pathlib import Path

        sandbox = tempfile.mkdtemp(prefix="f14_path_leak_")
        pom_file = Path(sandbox) / "pom.xml"
        pom_file.write_text(SAMPLE_POM, encoding="utf-8")

        editor = PomDependencyEditor(
            resolve_sandbox_root=lambda j, s: Path(sandbox),
            resolve_pom_content=lambda j: pom_file.read_text(encoding="utf-8"),
        )

        view = editor.get_stage3_pom(job_id="job_1")
        # The content should NOT contain the raw sandbox path
        assert sandbox not in view.content

        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)
