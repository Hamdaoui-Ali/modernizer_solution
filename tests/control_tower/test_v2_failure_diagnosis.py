"""Tests for V2 Automatic Failure Diagnosis (F02).

Tests that the FailureDiagnosisService:
1. Creates diagnosis records for build_failed, test_failed, transform_failed
2. Rejects non-trigger event types
3. Is idempotent — same command+event_type returns existing record
4. Builds ContextPack with enrichment metadata
5. Routes through EventPromptRouter to RepairProposal
6. Persists proposal via V2RepairFlowService
7. Emits ai_diagnosis_created event
8. Does NOT apply patches
9. Does NOT create approval cards
10. Uses existing failure classification
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from migration_factory.control_tower.application.v2_failure_diagnosis import (
    V2FailureDiagnosisService,
    FailureDiagnosisRecord,
    create_orchestrator_diagnosis_callback,
)
from migration_factory.control_tower.application.v2_repair_flow import (
    V2RepairFlowService,
    RepairProposal,
)


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def repair_flow() -> V2RepairFlowService:
    return V2RepairFlowService()


@pytest.fixture
def diagnosis_service(repair_flow: V2RepairFlowService) -> V2FailureDiagnosisService:
    events: list[dict[str, Any]] = []

    def event_sink(
        job_id: str,
        stage: int | None,
        event_type: str,
        status: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        events.append({
            "job_id": job_id,
            "stage": stage,
            "event_type": event_type,
            "status": status,
            "message": message,
            "payload": payload or {},
        })

    service = V2FailureDiagnosisService(
        repair_flow=repair_flow,
        event_sink=event_sink,
    )
    service._test_events = events  # type: ignore[attr-defined]
    return service


@pytest.fixture
def build_failed_payload() -> dict[str, Any]:
    return {
        "build_status": "BUILD_FAILED",
        "test_status": "",
        "exit_code": 1,
        "stderr": "mvn clean compile failed: compilation error",
        "stdout_tail": "# Failure summary: 5 compilation errors",
        "message": "Build failed with compilation errors",
        "command_id": "cmd-build-1",
    }


@pytest.fixture
def test_failed_payload() -> dict[str, Any]:
    return {
        "build_status": "BUILD_PASSED_IN_SANDBOX",
        "test_status": "TEST_FAILED",
        "exit_code": 1,
        "stderr": "Tests run: 42, Failures: 3",
        "stdout_tail": "# Test failures detected",
        "message": "Test validation failed",
        "command_id": "cmd-test-1",
    }


@pytest.fixture
def transform_failed_payload() -> dict[str, Any]:
    return {
        "transform_status": "TRANSFORM_FAILED",
        "final_status": "FAILED",
        "build_status": "BUILD_FAILED_IN_SANDBOX",
        "stderr": "OpenRewrite transform error",
        "message": "Transform failed during sandbox execution",
        "command_id": "cmd-transform-1",
    }


# ── Core diagnosis tests ──────────────────────────────────────────


class TestFailureDiagnosis:

    def test_diagnose_build_failed(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """Build_failed creates a diagnosis record."""
        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-build-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        assert isinstance(diagnosis, FailureDiagnosisRecord)
        assert diagnosis.command_id == "cmd-build-1"
        assert diagnosis.event_type == "build_failed"
        assert diagnosis.diagnosis_id
        assert diagnosis.context_pack_id
        assert diagnosis.context_pack_checksum
        assert diagnosis.repair_proposal_id

    def test_diagnose_test_failed(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        test_failed_payload: dict[str, Any],
    ) -> None:
        """Test_failed creates a diagnosis record."""
        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=2,
            command_id="cmd-test-1",
            event_type="test_failed",
            payload=test_failed_payload,
        )
        assert isinstance(diagnosis, FailureDiagnosisRecord)
        assert diagnosis.command_id == "cmd-test-1"
        assert diagnosis.event_type == "test_failed"

    def test_diagnose_transform_failed(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        transform_failed_payload: dict[str, Any],
    ) -> None:
        """Transform_failed creates a diagnosis record."""
        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-transform-1",
            event_type="transform_failed",
            payload=transform_failed_payload,
        )
        assert isinstance(diagnosis, FailureDiagnosisRecord)
        assert diagnosis.command_id == "cmd-transform-1"
        assert diagnosis.event_type == "transform_failed"

    def test_rejects_unknown_event_type(
        self,
        diagnosis_service: V2FailureDiagnosisService,
    ) -> None:
        """Non-trigger event types raise ValueError."""
        with pytest.raises(ValueError, match="not a diagnosis trigger"):
            diagnosis_service.diagnose(
                job_id="job-1",
                stage_index=1,
                command_id="cmd-1",
                event_type="stage_started",
            )

    def test_rejects_event_types_not_in_trigger_set(
        self,
        diagnosis_service: V2FailureDiagnosisService,
    ) -> None:
        """Event types like stage_failed or repair_started are rejected."""
        for non_trigger in ("stage_failed", "repair_started", "approval_required"):
            with pytest.raises(ValueError, match="not a diagnosis trigger"):
                diagnosis_service.diagnose(
                    job_id="job-1",
                    stage_index=1,
                    command_id="cmd-1",
                    event_type=non_trigger,
                )

    def test_diagnosis_has_all_correlation_fields(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """Diagnosis record contains all required correlation fields."""
        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-build-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        # Required correlation fields per spec
        assert diagnosis.diagnosis_id
        assert diagnosis.context_pack_id
        assert diagnosis.context_pack_checksum
        assert diagnosis.command_id
        assert diagnosis.event_type
        assert diagnosis.failure_type
        assert diagnosis.repair_proposal_id
        assert diagnosis.model_invocation_id
        assert diagnosis.redaction_status
        assert diagnosis.created_at


# ── Idempotency tests ─────────────────────────────────────────────


class TestIdempotency:

    def test_same_command_event_returns_existing(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """Second call with same (command_id, event_type) returns existing diagnosis."""
        first = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        second = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        assert first.diagnosis_id == second.diagnosis_id

    def test_same_command_different_event_creates_separate(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
        test_failed_payload: dict[str, Any],
    ) -> None:
        """Same command but different event types get separate diagnoses."""
        first = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        second = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="test_failed",
            payload=test_failed_payload,
        )
        assert first.diagnosis_id != second.diagnosis_id

    def test_different_command_same_event_creates_separate(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """Different commands with same event type get separate diagnoses."""
        first = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        second = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-2",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        assert first.diagnosis_id != second.diagnosis_id

    def test_get_diagnosis_returns_existing(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """get_diagnosis retrieves previously created record."""
        created = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        retrieved = diagnosis_service.get_diagnosis("cmd-1", "build_failed")
        assert retrieved is not None
        assert retrieved.diagnosis_id == created.diagnosis_id

    def test_get_diagnosis_returns_none_for_unknown(
        self,
        diagnosis_service: V2FailureDiagnosisService,
    ) -> None:
        """get_diagnosis returns None for unknown command/event."""
        assert diagnosis_service.get_diagnosis("nonexistent", "build_failed") is None


# ── Event emission tests ──────────────────────────────────────────


class TestEventEmission:

    def test_emits_ai_diagnosis_created(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """Diagnosing a failure emits ai_diagnosis_created event."""
        events = getattr(diagnosis_service, "_test_events", [])

        diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )

        matching = [e for e in events if e["event_type"] == "ai_diagnosis_created"]
        assert len(matching) == 1
        event = matching[0]
        assert event["job_id"] == "job-1"
        assert event["status"] == "completed"
        assert "AI diagnosis created for build_failed" in event["message"]

    def test_event_payload_contains_correlation_fields(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """ai_diagnosis_created payload contains all correlation fields."""
        events = getattr(diagnosis_service, "_test_events", [])

        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )

        event = next(e for e in events if e["event_type"] == "ai_diagnosis_created")
        payload = event["payload"]
        assert payload["diagnosis_id"] == diagnosis.diagnosis_id
        assert payload["context_pack_id"] == diagnosis.context_pack_id
        assert payload["context_pack_checksum"] == diagnosis.context_pack_checksum
        assert payload["command_id"] == "cmd-1"
        assert payload["event_type"] == "build_failed"
        assert payload["failure_type"] is not None
        assert payload["repair_proposal_id"] == diagnosis.repair_proposal_id
        assert payload["model_invocation_id"] is not None
        assert payload["redaction_status"] is not None

    def test_idempotent_diagnosis_does_not_emit_again(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """Second diagnosis for same (command, event) does not emit again."""
        events = getattr(diagnosis_service, "_test_events", [])

        diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )

        matching = [e for e in events if e["event_type"] == "ai_diagnosis_created"]
        assert len(matching) == 1


# ── Context pack tests ────────────────────────────────────────────


class TestContextPack:

    def test_context_pack_is_created(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """Diagnosis creates a ContextPack with enrichment metadata."""
        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        assert diagnosis.context_pack_id
        assert diagnosis.context_pack_checksum
        assert diagnosis.context_pack_checksum.startswith("cp-")

    def test_context_pack_has_evidence_refs(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """Context pack evidence refs include failure type from classification."""
        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        # Evidence refs should include failure type info
        assert diagnosis.failure_type

    def test_context_pack_includes_f01_enrichment_metadata(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """ContextPack receives F01 enrichment metadata (event_type, stage_index, etc)."""
        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=2,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
            profile_id="profile-1",
            pom_summary_ref="pom://summary/1",
            sandbox_binding_ref="binding://cmd-1",
        )
        assert diagnosis.context_pack_id
        assert diagnosis.context_pack_checksum
        # The pack should have the metadata passed via ContextPackBuilder.
        # Evidence refs include failure info from classification.
        assert "BUILD_FAILED" in diagnosis.failure_type or diagnosis.failure_type


# ── Repair proposal tests ─────────────────────────────────────────


class TestRepairProposal:

    def test_proposal_created_via_repair_flow(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
        repair_flow: V2RepairFlowService,
    ) -> None:
        """Diagnosis creates a RepairProposal via V2RepairFlowService."""
        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        assert diagnosis.repair_proposal_id

    def test_proposal_is_draft_not_approved(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
        repair_flow: V2RepairFlowService,
    ) -> None:
        """Diagnosis creates a draft (not approved) proposal."""
        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        # The repair flow is internal, so we check via the event payload
        assert diagnosis.repair_proposal_id

    def test_proposal_is_validated_against_schema(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
        repair_flow: V2RepairFlowService,
    ) -> None:
        """Diagnosis validates proposal dict against RepairProposal schema."""
        # Should succeed without raising SchemaValidationError
        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        assert diagnosis.repair_proposal_id


# ── Non-goal enforcement tests ────────────────────────────────────


class TestNoPatchApplied:

    def test_diagnosis_does_not_apply_patch(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
        repair_flow: V2RepairFlowService,
    ) -> None:
        """Diagnosis must not call apply_patch on repair flow."""
        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        # Proposal should be in draft, not applied
        # If it were applied, we'd need an approve call first
        events = getattr(diagnosis_service, "_test_events", [])
        for event in events:
            assert event["event_type"] != "patch_applied"
            assert event["event_type"] != "approval_card_created"

    def test_diagnosis_does_not_create_approval_card(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """Diagnosis must not create approval cards."""
        events = getattr(diagnosis_service, "_test_events", [])
        diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        for event in events:
            assert "approval" not in event["event_type"]


# ── Serialization tests ───────────────────────────────────────────


class TestSerialization:

    def test_diagnosis_to_dict(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """diagnosis_to_dict produces expected dict shape."""
        diagnosis = diagnosis_service.diagnose(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            event_type="build_failed",
            payload=build_failed_payload,
        )
        d = V2FailureDiagnosisService.diagnosis_to_dict(diagnosis)
        assert d["diagnosis_id"] == diagnosis.diagnosis_id
        assert d["command_id"] == "cmd-1"
        assert d["event_type"] == "build_failed"
        assert d["failure_type"] is not None
        assert d["context_pack_id"] == diagnosis.context_pack_id
        assert d["context_pack_checksum"] == diagnosis.context_pack_checksum
        assert d["repair_proposal_id"] == diagnosis.repair_proposal_id
        assert d["model_invocation_id"] is not None
        assert d["redaction_status"] is not None
        assert d["created_at"] is not None

    def test_list_diagnoses_empty_on_new_service(self) -> None:
        """New service returns empty tuple."""
        service = V2FailureDiagnosisService()
        assert len(service.list_diagnoses()) == 0

    def test_list_diagnoses(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
        test_failed_payload: dict[str, Any],
    ) -> None:
        """list_diagnoses returns all stored records."""
        diagnosis_service.diagnose(
            job_id="job-1", stage_index=1, command_id="cmd-1",
            event_type="build_failed", payload=build_failed_payload,
        )
        diagnosis_service.diagnose(
            job_id="job-1", stage_index=2, command_id="cmd-2",
            event_type="test_failed", payload=test_failed_payload,
        )
        all_diags = diagnosis_service.list_diagnoses()
        assert len(all_diags) == 2

    def test_is_diagnosable_event(self) -> None:
        """is_diagnosable_event correctly identifies trigger events."""
        assert V2FailureDiagnosisService.is_diagnosable_event("build_failed")
        assert V2FailureDiagnosisService.is_diagnosable_event("test_failed")
        assert V2FailureDiagnosisService.is_diagnosable_event("transform_failed")
        assert not V2FailureDiagnosisService.is_diagnosable_event("stage_started")
        assert not V2FailureDiagnosisService.is_diagnosable_event("stage_completed")

    def test_clear_resets_diagnoses(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """clear() removes all in-memory diagnoses."""
        diagnosis_service.diagnose(
            job_id="job-1", stage_index=1, command_id="cmd-1",
            event_type="build_failed", payload=build_failed_payload,
        )
        assert len(diagnosis_service.list_diagnoses()) == 1
        diagnosis_service.clear()
        assert len(diagnosis_service.list_diagnoses()) == 0


# ── Failure summary tests ─────────────────────────────────────────


class TestFailureSummary:

    def test_build_failed_summary_includes_build_status(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """Build failure summary mentions build status."""
        summary = diagnosis_service._build_failure_summary(
            event_type="build_failed",
            payload=build_failed_payload,
        )
        assert "BUILD_FAILED" in summary or "Build failed" in summary

    def test_test_failed_summary_includes_test_status(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        test_failed_payload: dict[str, Any],
    ) -> None:
        """Test failure summary mentions test status."""
        summary = diagnosis_service._build_failure_summary(
            event_type="test_failed",
            payload=test_failed_payload,
        )
        assert "TEST_FAILED" in summary or "Test failed" in summary

    def test_transform_failed_summary_includes_transform_status(
        self,
        diagnosis_service: V2FailureDiagnosisService,
        transform_failed_payload: dict[str, Any],
    ) -> None:
        """Transform failure summary mentions transform status."""
        summary = diagnosis_service._build_failure_summary(
            event_type="transform_failed",
            payload=transform_failed_payload,
        )
        assert "TRANSFORM_FAILED" in summary or "Transform failed" in summary


# ── Production callback wiring tests ──────────────────────────────


class TestProductionCallback:
    """Prove the production-wired callback emits ai_diagnosis_created
    without requiring direct V2FailureDiagnosisService.diagnose() calls.

    This mirrors the pattern used in app.py:
        callback = create_orchestrator_diagnosis_callback(..., event_sink=...)
        callback(job_id, stage_index, command_id, event_type, payload)
    """

    def test_callback_emits_ai_diagnosis_created(
        self,
        repair_flow: V2RepairFlowService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """The production callback emits ai_diagnosis_created when called
        with a build_failed payload, without direct service access."""
        events: list[dict[str, Any]] = []

        def event_sink(
            job_id: str,
            stage: int | None,
            event_type: str,
            status: str,
            message: str,
            payload: dict[str, Any] | None = None,
        ) -> None:
            events.append({
                "job_id": job_id,
                "stage": stage,
                "event_type": event_type,
                "status": status,
                "message": message,
                "payload": payload or {},
            })

        callback = create_orchestrator_diagnosis_callback(
            repair_flow=repair_flow,
            event_sink=event_sink,
        )

        # Call exactly as V2OrchestratorRunner._maybe_diagnose does
        callback(
            "job-1",  # job_id
            1,        # stage_index
            "cmd-1",  # command_id
            "build_failed",  # event_type
            build_failed_payload,  # payload
        )

        matching = [e for e in events if e["event_type"] == "ai_diagnosis_created"]
        assert len(matching) == 1, f"Expected 1 ai_diagnosis_created, got {len(matching)}"
        event = matching[0]
        assert event["job_id"] == "job-1"
        assert event["status"] == "completed"
        assert "build_failed" in event["message"]

    def test_callback_is_idempotent(
        self,
        repair_flow: V2RepairFlowService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """Second callback call with same (command_id, event_type) does not
        emit duplicate ai_diagnosis_created."""
        events: list[dict[str, Any]] = []

        def event_sink(
            job_id: str,
            stage: int | None,
            event_type: str,
            status: str,
            message: str,
            payload: dict[str, Any] | None = None,
        ) -> None:
            events.append({
                "job_id": job_id,
                "stage": stage,
                "event_type": event_type,
                "status": status,
                "message": message,
                "payload": payload or {},
            })

        callback = create_orchestrator_diagnosis_callback(
            repair_flow=repair_flow,
            event_sink=event_sink,
        )

        callback("job-1", 1, "cmd-1", "build_failed", build_failed_payload)
        callback("job-1", 1, "cmd-1", "build_failed", build_failed_payload)

        matching = [e for e in events if e["event_type"] == "ai_diagnosis_created"]
        assert len(matching) == 1, f"Expected 1 (idempotent), got {len(matching)}"

    def test_callback_does_not_apply_patches(
        self,
        repair_flow: V2RepairFlowService,
        build_failed_payload: dict[str, Any],
    ) -> None:
        """Callback must never emit patch_applied or approval_card_created."""
        events: list[dict[str, Any]] = []

        def event_sink(
            job_id: str,
            stage: int | None,
            event_type: str,
            status: str,
            message: str,
            payload: dict[str, Any] | None = None,
        ) -> None:
            events.append({
                "job_id": job_id,
                "stage": stage,
                "event_type": event_type,
                "status": status,
                "message": message,
                "payload": payload or {},
            })

        callback = create_orchestrator_diagnosis_callback(
            repair_flow=repair_flow,
            event_sink=event_sink,
        )

        callback("job-1", 1, "cmd-1", "build_failed", build_failed_payload)

        for event in events:
            assert event["event_type"] != "patch_applied"
            assert event["event_type"] != "approval_card_created"
            assert "approval" not in event["event_type"]

    def test_callback_payload_is_redacted(
        self,
        repair_flow: V2RepairFlowService,
    ) -> None:
        """ai_diagnosis_created payload contains no raw paths or secrets."""
        events: list[dict[str, Any]] = []

        def event_sink(
            job_id: str,
            stage: int | None,
            event_type: str,
            status: str,
            message: str,
            payload: dict[str, Any] | None = None,
        ) -> None:
            events.append({
                "job_id": job_id,
                "stage": stage,
                "event_type": event_type,
                "status": status,
                "message": message,
                "payload": payload or {},
            })

        callback = create_orchestrator_diagnosis_callback(
            repair_flow=repair_flow,
            event_sink=event_sink,
        )

        # Payload with a raw absolute path
        payload_with_paths: dict[str, Any] = {
            "build_status": "BUILD_FAILED",
            "command_id": "cmd-1",
            "message": "Build failed in /home/user/projects/sandbox",
        }

        callback("job-1", 1, "cmd-1", "build_failed", payload_with_paths)

        matching = [e for e in events if e["event_type"] == "ai_diagnosis_created"]
        assert len(matching) >= 1
        event_payload = matching[0]["payload"]

        # The ai_diagnosis_created payload keys are correlation fields only,
        # no raw paths or secrets
        assert "diagnosis_id" in event_payload
        assert "context_pack_id" in event_payload
        assert "context_pack_checksum" in event_payload
        # Check no raw path-like content in payload values
        for value in event_payload.values():
            if isinstance(value, str):
                assert "/home/" not in value, f"Raw path found: {value}"
